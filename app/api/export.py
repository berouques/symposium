"""Экспорт транскриптов симпозиума (Markdown / JSON), спецификация NG_00 §20.

Фильтры (v0.3):
  discussion           — только общение: реплики, молчания, человек,
                         голоса, секретарь (открытие/отчёт)
  discussion_thinking  — + блоки размышлений (thinking)
  full                 — полный дамп: + системные события, мета, токены
"""
from __future__ import annotations

import json

from .. import db

FILTERS = ("discussion", "discussion_thinking", "full")


def _members_map(symposium_id: int) -> dict[int, dict]:
    return {m["id"]: m for m in db.query_all(
        "SELECT * FROM members WHERE symposium_id=? ORDER BY order_idx", (symposium_id,)
    )}


def _selected_messages(symposium_id: int, options: dict) -> list[dict]:
    """Сообщения по фильтру; message_ids (legacy) дополнительно сужает выбор."""
    msgs = db.query_all(
        "SELECT * FROM messages WHERE symposium_id=? ORDER BY seq", (symposium_id,)
    )
    selected = options.get("message_ids")
    if selected:
        ids = set(selected)
        msgs = [m for m in msgs if m["id"] in ids]
    # старые опции (обратная совместимость v0.2)
    include_system = options.get("include_system_events", True)
    include_thinking = options.get("include_thinking", False)

    content_filter = options.get("filter", "discussion")
    out = []
    for m in msgs:
        if content_filter == "discussion" or content_filter == "discussion_thinking":
            if m["kind"] == "system":
                if include_system and content_filter != "discussion":
                    # legacy-режим: системные события были явно запрошены
                    pass
                else:
                    continue
        elif content_filter == "full":
            pass  # полный дамп: всё
        if m["kind"] in ("member", "skip") and not include_thinking and content_filter == "discussion":
            m = dict(m)
            m["thinking"] = None
        out.append(m)
    return out


def export_markdown(symposium_id: int, options: dict) -> str:
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (symposium_id,))
    members = _members_map(symposium_id)
    rounds = db.query_all(
        "SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (symposium_id,)
    )
    msgs = _selected_messages(symposium_id, options)
    content_filter = options.get("filter", "discussion")
    show_meta = content_filter == "full"
    with_thinking = content_filter in ("discussion_thinking", "full")

    lines = [
        f"# {sym['title']}",
        "",
        f"- **Topic**: {sym['topic']}",
        f"- **Completion criterion**: {sym['completion_criterion'] or '-'}",
        f"- **Report format**: {sym['report_format'] or '-'}",
        f"- **Status**: {sym['status']}",
        f"- **Participants**: {', '.join(m['name'] for m in members.values())}",
        "",
    ]
    opening = next((m for m in msgs if m["kind"] == "secretary"), None)
    if opening:
        lines += ["## Opening note", "", opening["content"], ""]

    rounds_by_id = {r["id"]: r for r in rounds}
    current_round = None
    for m in msgs:
        if m["kind"] == "secretary":
            continue
        rnd = rounds_by_id.get(m["round_id"]) if m["round_id"] else None
        rnum = rnd["number"] if rnd else 0
        if rnum != current_round:
            current_round = rnum
            lines += [f"## Round {rnum}", ""]
        name = members[m["member_id"]]["name"] if m["member_id"] in members else "Secretary"
        if m["kind"] == "member":
            lines += [f"**{name}**:", "", m["content"], ""]
            if with_thinking and m.get("thinking"):
                lines += ["<details><summary>thinking</summary>", "", m["thinking"], "", "</details>", ""]
            if show_meta and m.get("meta"):
                lines += [f"*meta: {m['meta']}*", ""]
        elif m["kind"] == "skip":
            lines += [f"**{name}**: [SKIP_TURN]", ""]
        elif m["kind"] == "human":
            lines += [f"**[Human]**:", "", m["content"], ""]
        elif m["kind"] == "vote":
            agree = (m["content"] or "").strip().startswith("[AGREE_END]")
            lines += [f"**{name}** (vote): {m['content']}", ""]
            if with_thinking and m.get("thinking"):
                lines += ["<details><summary>thinking</summary>", "", m["thinking"], "", "</details>", ""]
        elif m["kind"] == "system":
            lines += [f"*{m['content']}*", ""]
        elif m["kind"] == "report":
            lines += ["## Final report", "", m["content"], ""]
    return "\n".join(lines)


def export_json(symposium_id: int, options: dict) -> dict:
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (symposium_id,))
    members = _members_map(symposium_id)
    rounds = db.query_all(
        "SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (symposium_id,)
    )
    msgs = _selected_messages(symposium_id, options)
    attachments = db.query_all(
        "SELECT f.id, f.name FROM rag_attachments a JOIN rag_folders f ON f.id=a.folder_id "
        "WHERE a.symposium_id=?", (symposium_id,)
    )
    content_filter = options.get("filter", "discussion")
    with_thinking = content_filter in ("discussion_thinking", "full")
    show_full_meta = content_filter == "full"
    return {
        "export_version": 2,
        "filter": content_filter,
        "symposium": {
            "id": sym["id"], "title": sym["title"], "topic": sym["topic"],
            "completion_criterion": sym["completion_criterion"],
            "report_format": sym["report_format"], "language": sym["language"],
            "status": sym["status"], "current_round": sym["current_round"],
            "created_at": sym["created_at"],
        },
        "members": [
            {
                "id": m["id"], "name": m["name"], "description": m["description"],
                "role": m["role_name"], "temperature": m["temperature"],
                "max_tokens": m["max_tokens"],
                "avatar": {
                    "seed": m["avatar_seed"], "style": m["avatar_style"],
                    "options": m["avatar_options"], "color": m["avatar_color"],
                    "path": m["avatar_path"],
                },
            }
            for m in members.values()
        ],
        "rag_folders": [{"id": a["id"], "name": a["name"]} for a in attachments],
        "rounds": [
            {
                "number": r["number"], "status": r["status"], "summary": r["summary"],
                "summary_error": r["summary_error"],
                "started_at": r["started_at"], "ended_at": r["ended_at"],
            }
            for r in rounds
        ],
        "messages": [
            {
                "id": m["id"], "round_id": m["round_id"], "seq": m["seq"],
                "kind": m["kind"], "member_id": m["member_id"],
                "content": m["content"],
                "thinking": m["thinking"] if with_thinking else None,
                "tokens_in": m["tokens_in"] if show_full_meta else None,
                "tokens_out": m["tokens_out"] if show_full_meta else None,
                "model": m["model"] if show_full_meta else None,
                "meta": json.loads(m["meta"]) if (show_full_meta and m["meta"]) else None,
                "created_at": m["created_at"],
            }
            for m in msgs
        ],
    }


# ------------------------------------------------------------- полный дамп

_SECRET_HINTS = ("key", "token", "password", "secret")


def export_dump(symposium_id: int) -> bytes:
    """«Полный дамп» — ZIP с техническими подробностями «под капотом»: все
    таблицы симпозиума (включая мышление, мета-блоки <state>, флаги, токены,
    вызовы субагентов), собранные системные промпты каждого участника,
    параметры профиля и лимитера, снимок настроек (без секретов), сырые
    журналы субагентов и полный Markdown-транскрипт."""
    import io
    import zipfile
    from datetime import datetime, timezone
    from pathlib import Path

    from .. import db as _db
    from ..engine import context as ctx
    from ..engine.store import Store
    from ..llm import limits as limits_mod
    from ..workspace import manager as ws

    store = Store(symposium_id)
    sym = _db.query_one("SELECT * FROM symposia WHERE id=?", (symposium_id,)) or {}
    members = store.members()
    profile = store.profile()

    def rows(sql: str) -> list[dict]:
        return _db.query_all(sql, (symposium_id,))

    messages = rows("SELECT * FROM messages WHERE symposium_id=? ORDER BY seq")
    for m in messages:                      # meta хранится JSON-строкой: разворачиваем
        try:
            m["meta"] = json.loads(m["meta"]) if m["meta"] else None
        except ValueError:
            pass
    prompts = {}
    for m in members:
        prompts[m["name"]] = {
            "turn_system_prompt": ctx.build_member_system_prompt(m, sym, [x for x in members if x["enabled"]], 3),
            "vote_system_prompt": ctx.build_vote_system_prompt(m, sym, [x for x in members if x["enabled"]], "<reason>"),
        }
    settings = {r["key"]: r["value"] for r in _db.query_all("SELECT key, value FROM settings")
                if not any(h in r["key"].lower() for h in _SECRET_HINTS)}
    prof = None
    if profile:
        prof = {k: v for k, v in profile.items() if k != "api_key"}
        prof["api_key_present"] = bool(profile.get("api_key"))
        prof["limits_effective"] = limits_mod.policy_of(profile)
    files = {
        "symposium.json": sym,
        "members.json": members,
        "assembled_prompts.json": prompts,
        "rounds.json": rows("SELECT * FROM rounds WHERE symposium_id=? ORDER BY number"),
        "messages.json": messages,
        "evidence.json": rows("SELECT * FROM evidence WHERE symposium_id=? ORDER BY id"),
        "checklist.json": rows("SELECT * FROM checklist_items WHERE symposium_id=? ORDER BY id"),
        "issues.json": rows("SELECT * FROM issues WHERE symposium_id=? ORDER BY id"),
        "token_usage.json": rows("SELECT * FROM token_usage WHERE symposium_id=? ORDER BY id"),
        "subagent_calls.json": rows("SELECT * FROM subagent_calls WHERE symposium_id=? ORDER BY id"),
        "hitl_requests.json": rows("SELECT * FROM hitl_requests WHERE symposium_id=? ORDER BY id"),
        "profile.json": prof,
        "settings_snapshot.json": settings,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt",
                   f"Symposium full dump\nsymposium #{symposium_id}: {sym.get('title')}\n"
                   f"generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                   "*.json — таблицы симпозиума как есть (messages.meta содержит разобранный <state>,\n"
                   "флаги нарушений протокола; messages.thinking — рассуждения модели);\n"
                   "assembled_prompts.json — системные промпты участников в том виде, в каком их видит модель;\n"
                   "profile.json — профиль LLM без ключа, с действующей политикой лимитов;\n"
                   "raw/ — сырые журналы субагентов; transcript_full.md — полный транскрипт.\n")
        for name, data in files.items():
            z.writestr(name, json.dumps(data, ensure_ascii=False, indent=1, default=str))
        z.writestr("transcript_full.md", export_markdown(symposium_id, {"filter": "full"}))
        try:
            logs = Path(ws.ws_root(symposium_id)) / "subagent_logs"
            if logs.is_dir():
                for f in sorted(logs.iterdir())[:500]:
                    if f.is_file() and f.stat().st_size < 2_000_000:
                        z.write(f, f"raw/{f.name}")
        except Exception:  # noqa: BLE001 — журналы необязательны
            pass
    return buf.getvalue()
