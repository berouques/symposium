"""Доступ движка к БД одного симпозиума (всё SQL — здесь).

Раньше раннеры дёргали «приватные» методы оркестратора; теперь у них общий
явный объект `Store`. Он же публикует события SSE при записи сообщений.
"""
from __future__ import annotations

import json

from .. import db
from ..auth import utcnow
from ..events import bus
from .convergence import latest_states


def msg_public(msg: dict) -> dict:
    """Публичная проекция сообщения (в SSE и API)."""
    return {
        "id": msg["id"], "round_id": msg["round_id"], "seq": msg["seq"],
        "kind": msg["kind"], "member_id": msg["member_id"], "content": msg["content"],
        "thinking": msg["thinking"], "tokens_in": msg["tokens_in"],
        "tokens_out": msg["tokens_out"], "tokens_thinking": msg.get("tokens_thinking", 0), "model": msg["model"], "meta": msg["meta"],
        "created_at": msg["created_at"],
    }


class Store:
    def __init__(self, symposium_id: int):
        self.sid = symposium_id

    # ------------------------------------------------------------ симпозиум
    def load(self) -> dict | None:
        return db.query_one("SELECT * FROM symposia WHERE id=?", (self.sid,))

    def patch(self, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE symposia SET {sets}, updated_at=? WHERE id=?",
                   list(fields.values()) + [utcnow(), self.sid])

    def members(self) -> list[dict]:
        return db.query_all("SELECT * FROM members WHERE symposium_id=? ORDER BY order_idx", (self.sid,))

    def speaking_members(self) -> list[dict]:
        """Включённые участники: говорят, голосуют, считаются в консенсусе."""
        return [m for m in self.members() if m.get("enabled", 1)]

    def speaking_states(self) -> dict[int, dict]:
        """Последние <state> только включённых участников."""
        ids = {m["id"] for m in self.speaking_members()}
        return {k: v for k, v in latest_states(self.messages()).items() if k in ids}

    def member(self, member_id: int) -> dict | None:
        return db.query_one("SELECT * FROM members WHERE id=?", (member_id,))

    def messages(self) -> list[dict]:
        return db.query_all("SELECT * FROM messages WHERE symposium_id=? ORDER BY seq", (self.sid,))

    def rounds(self) -> list[dict]:
        return db.query_all("SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (self.sid,))

    def round(self, number: int) -> dict | None:
        return db.query_one("SELECT * FROM rounds WHERE symposium_id=? AND number=?", (self.sid, number))

    def profile(self) -> dict | None:
        """Эффективный профиль: явный у симпозиума → активный у владельца."""
        row = self.load()
        pid = row["api_profile_id"] if row else None
        if pid is None and row:
            owner = db.query_one("SELECT active_profile_id FROM users WHERE id=?", (row["owner_id"],))
            pid = owner["active_profile_id"] if owner else None
        if pid is None:
            return None
        return db.query_one("SELECT * FROM api_profiles WHERE id=?", (pid,))

    def profile_for(self, member: dict | None) -> dict | None:
        """Личный LLM-профиль участника, иначе профиль симпозиума."""
        if member and member.get("api_profile_id"):
            p = db.query_one("SELECT * FROM api_profiles WHERE id=?", (member["api_profile_id"],))
            if p:
                return p
        return self.profile()

    def human(self) -> dict:
        row = self.load()
        u = db.query_one("SELECT name, about FROM users WHERE id=?", (row["owner_id"],)) if row else None
        return u or {"name": "", "about": ""}

    # ------------------------------------------------------------ настройки
    def strict(self) -> bool:
        """Требовать <state>: локальный флаг симпозиума → глобальная настройка."""
        row = self.load()
        if row and row["strict_markers"] is not None:
            return bool(row["strict_markers"])
        return str(db.get_setting("strict_state", "1")) in ("1", "true", "True")

    def compression_n(self) -> int:
        row = self.load()
        if row and row["compression_override"] is not None:
            return int(row["compression_override"])
        return int(db.get_setting("compression_threshold", 10))

    def subagent_limit(self, member: dict | None = None) -> int:
        if member and member.get("subagent_limit"):
            return int(member["subagent_limit"])
        return int(db.get_setting("subagent_calls_per_turn", 3))

    def max_rounds(self) -> int:
        row = self.load()
        raw = row.get("max_rounds") if row else None
        if raw is None:
            raw = int(db.get_setting("default_max_rounds", 12) or 0)
        return int(raw or 0)  # 0 = без лимита

    def setting(self, key: str, default=None):
        return db.get_setting(key, default)

    def summary_mode(self) -> str:
        return str(db.get_setting("summary_mode", "digest"))

    def stall_rounds(self) -> int:
        return int(db.get_setting("stall_rounds", 2) or 0)

    # ------------------------------------------------------------ сообщения
    def next_seq(self) -> int:
        return (db.query_value("SELECT COALESCE(MAX(seq), 0) FROM messages WHERE symposium_id=?",
                               (self.sid,)) or 0) + 1

    def add_message(
        self, kind: str, content: str, *, member_id: int | None = None,
        round_id: int | None = None, thinking: str | None = None,
        tokens_in: int = 0, tokens_out: int = 0, model: str | None = None,
        meta: dict | None = None, tokens_thinking: int = 0,
    ) -> dict:
        msg_id = db.execute(
            "INSERT INTO messages(symposium_id, round_id, seq, kind, member_id, content, "
            "thinking, tokens_in, tokens_out, model, meta, created_at, tokens_thinking) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.sid, round_id, self.next_seq(), kind, member_id, content, thinking,
             tokens_in, tokens_out, model,
             json.dumps(meta, ensure_ascii=False) if meta else None, utcnow(), tokens_thinking),
        )
        db.execute("UPDATE symposia SET last_activity=? WHERE id=?", (utcnow(), self.sid))
        if kind in ("member", "skip", "vote", "secretary", "report"):
            # успешный шаг — серия автовосстановлений закончилась
            db.execute("UPDATE symposia SET recovery_attempts=0 WHERE id=? AND recovery_attempts<>0", (self.sid,))
        msg = db.query_one("SELECT * FROM messages WHERE id=?", (msg_id,))
        bus.publish(self.sid, {"type": "message", "message": msg_public(msg)})
        return msg

    # ------------------------------------------------------------ свидетельства
    def add_evidence(self, *, member_id: int | None, round_id: int | None, agent: str,
                     query: str, digest: str, ok: bool, raw_path: str | None = None) -> dict:
        n = (db.query_value("SELECT COUNT(*) FROM evidence WHERE symposium_id=?", (self.sid,)) or 0) + 1
        ref = f"E{n}"
        db.execute(
            "INSERT INTO evidence(symposium_id, ref, member_id, round_id, agent, query, digest, ok, "
            "raw_path, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (self.sid, ref, member_id, round_id, agent, query[:1000], digest, int(ok), raw_path, utcnow()),
        )
        row = db.query_one("SELECT * FROM evidence WHERE symposium_id=? AND ref=?", (self.sid, ref))
        bus.publish(self.sid, {"type": "evidence", "evidence": row})
        return row

    def evidence(self) -> list[dict]:
        return db.query_all("SELECT * FROM evidence WHERE symposium_id=? ORDER BY id", (self.sid,))

    # ------------------------------------------------------------ чек-лист
    def set_checklist(self, items: list[str]) -> None:
        db.execute("DELETE FROM checklist_items WHERE symposium_id=?", (self.sid,))
        for i, text in enumerate(items, start=1):
            db.execute("INSERT INTO checklist_items(symposium_id, ref, text) VALUES(?,?,?)",
                       (self.sid, f"C{i}", text))
        bus.publish(self.sid, {"type": "checklist", "items": self.checklist()})

    def checklist(self) -> list[dict]:
        return db.query_all("SELECT * FROM checklist_items WHERE symposium_id=? ORDER BY id", (self.sid,))

    def set_checklist_status(self, ref: str, status: str, round_no: int) -> bool:
        cur = db.query_one("SELECT status FROM checklist_items WHERE symposium_id=? AND ref=?", (self.sid, ref))
        if cur is None or cur["status"] == status:
            return False
        db.execute("UPDATE checklist_items SET status=?, updated_round=? WHERE symposium_id=? AND ref=?",
                   (status, round_no, self.sid, ref))
        return True

    # ------------------------------------------------------------ возражения
    def open_issue(self, owner_id: int | None, text: str, satisfied_if: str, round_no: int) -> dict:
        n = (db.query_value("SELECT COUNT(*) FROM issues WHERE symposium_id=?", (self.sid,)) or 0) + 1
        ref = f"I{n}"
        db.execute(
            "INSERT INTO issues(symposium_id, ref, owner_member_id, text, satisfied_if, opened_round) "
            "VALUES(?,?,?,?,?,?)", (self.sid, ref, owner_id, text[:600], satisfied_if[:400], round_no))
        return db.query_one("SELECT * FROM issues WHERE symposium_id=? AND ref=?", (self.sid, ref))

    def issues(self, only_open: bool = False) -> list[dict]:
        sql = "SELECT * FROM issues WHERE symposium_id=?"
        if only_open:
            sql += " AND status='open'"
        return db.query_all(sql + " ORDER BY id", (self.sid,))

    def resolve_issue(self, ref: str, round_no: int) -> bool:
        cur = db.query_one("SELECT status FROM issues WHERE symposium_id=? AND ref=?", (self.sid, ref))
        if cur is None or cur["status"] == "resolved":
            return False
        db.execute("UPDATE issues SET status='resolved', resolved_round=? WHERE symposium_id=? AND ref=?",
                   (round_no, self.sid, ref))
        return True

    def broadcast_registries(self) -> None:
        bus.publish(self.sid, {"type": "registries", "checklist": self.checklist(),
                               "issues": self.issues()})

    # ------------------------------------------------------------ прочее
    def rag_folder_names(self) -> list[str]:
        rows = db.query_all(
            "SELECT f.name FROM rag_attachments a JOIN rag_folders f ON f.id=a.folder_id "
            "WHERE a.symposium_id=? ORDER BY f.name", (self.sid,))
        return [r["name"] for r in rows]

    def create_hitl(self, member_id: int, round_id: int | None, question: str,
                    options: list, context: str) -> int:
        return db.execute(
            "INSERT INTO hitl_requests(symposium_id, member_id, round_id, question, options, "
            "context, created_at) VALUES(?,?,?,?,?,?,?)",
            (self.sid, member_id, round_id, question, json.dumps(options, ensure_ascii=False),
             context, utcnow()))

    def hitl(self, req_id: int) -> dict | None:
        return db.query_one("SELECT * FROM hitl_requests WHERE id=?", (req_id,))
