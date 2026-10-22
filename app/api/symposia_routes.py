"""Symposium routes: CRUD, lifecycle, transcript, HITL, export, SSE."""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import db, tokens
from ..audit import audit
from ..auth import get_current_user, public_user, utcnow
from ..avatars import avatar_options_from_seed, generate_svg, make_seed, member_color
from ..engine.manager import manager
from ..events import bus
from ..llm.client import LLMPersistentError
from ..llm.rate import rate_limiter
from ..rag import library
from ..workspace import manager as ws
from .deps import get_symposium_or_404, member_of, owned_symposium
from . import export as export_mod

router = APIRouter(prefix="/api/symposia", tags=["symposia"])


# ------------------------------------------------------------------- models

class MemberIn(BaseModel):
    name: str = ""
    description: str = ""
    character_id: int | None = None    # создать участника из персонажа (снимок)
    user_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 2000
    thinking_mode: str | None = None   # None = follow the symposium
    thinking_depth: str | None = None
    enabled: bool = True


class SymposiumIn(BaseModel):
    title: str
    topic: str
    completion_criterion: str = ""
    report_format: str = ""
    # язык общения: произвольная строка (метаданные симпозиума),
    # движок передаёт её участникам через рантайм-мету
    language: str = "ru"
    token_limit: int | None = None
    api_profile_id: int | None = None
    max_rounds: int | None = None      # None = глобальный дефолт, 0 = без лимита
    thinking_mode: str = "default"
    thinking_depth: str | None = None
    form: str = "free"                 # free | disputatio | scientific (prompts/system/form_*.md)
    vote_policy: str = "unanimous"     # unanimous | supermajority | majority
    members: list[MemberIn]
    rag_folder_ids: list[int] = []
    start_now: bool = False


class MessageIn(BaseModel):
    text: str = ""
    attachments: list[dict] = []   # [{name, path, size}] — файлы, уже загруженные в workspace/uploads


class MemberPatch(BaseModel):
    user_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    thinking_mode: str | None = None
    thinking_depth: str | None = None
    enabled: bool | None = None
    api_profile_id: int | None = None     # личный LLM-профиль участника; 0 — как у симпозиума
    tools: list[str] | None = None        # разрешённые инструменты; ["*"] — все
    rag_folder_ids: list[int] | None = None
    subagent_limit: int | None = None     # 0 — как в настройках
    speech_style: str | None = None
    profile_fallback: bool | None = None
    prompt: str | None = None             # промпт персонажа (персоны) в этом симпозиуме


class SymposiumPatch(BaseModel):
    title: str | None = None
    topic: str | None = None
    fallback_profile_id: int | None = None
    completion_criterion: str | None = None
    report_format: str | None = None
    api_profile_id: int | None = None
    compression_override: int | None = None
    token_limit: int | None = None
    strict_markers: bool | None = None
    max_rounds: int | None = None
    thinking_mode: str | None = None
    thinking_depth: str | None = None
    form: str | None = None
    vote_policy: str | None = None


def _valid_form(form: str | None, policy: str | None) -> None:
    from ..prompt_store import system_keys
    if form is not None and f"form_{form}" not in system_keys():
        raise HTTPException(422, f"unknown discussion form '{form}'")
    if policy is not None and policy not in ("unanimous", "supermajority", "majority"):
        raise HTTPException(422, "vote_policy must be one of: unanimous, supermajority, majority")


def _valid_thinking(mode: str | None, depth: str | None) -> None:
    """'' is the explicit 'clear this override' value for PATCH bodies."""
    if mode is not None and mode not in ("", "default", "off", "on"):
        raise HTTPException(422, "thinking_mode must be one of: default, off, on")
    if depth is not None and depth not in ("", "low", "medium", "high"):
        raise HTTPException(422, "thinking_depth must be one of: low, medium, high")


# ------------------------------------------------------------------ helpers

def _sym_public(sym: dict, user: dict) -> dict:
    counts = db.query_one(
        "SELECT COUNT(*) AS c FROM members WHERE symposium_id=?", (sym["id"],)
    )
    usage = tokens.session_totals(sym["id"])
    return {
        "id": sym["id"], "title": sym["title"], "topic": sym["topic"],
        "status": sym["status"], "current_round": sym["current_round"],
        "members": counts["c"], "language": sym["language"],
        "tokens_in": usage["tokens_in"], "tokens_out": usage["tokens_out"],
        "last_activity": sym["last_activity"], "created_at": sym["created_at"],
        "owner_id": sym["owner_id"],
        "owner_login": db.query_value("SELECT login FROM users WHERE id=?", (sym["owner_id"],)),
        "mine": sym["owner_id"] == user["id"],
        "error_info": json.loads(sym["error_info"]) if sym["error_info"] else None,
        "pause_reason": sym["pause_reason"],
    }


def _hitl_public_row(req: dict) -> dict:
    try:
        options = json.loads(req["options"] or "[]")
    except ValueError:
        options = []
    return {
        "id": req["id"], "member_id": req["member_id"], "round_id": req["round_id"],
        "question": req["question"], "options": options, "context": req["context"],
        "status": req["status"], "created_at": req["created_at"],
    }


def _issue_public(i: dict) -> dict:
    return {"ref": i["ref"], "owner_member_id": i["owner_member_id"], "text": i["text"],
            "satisfied_if": i["satisfied_if"], "status": i["status"],
            "opened_round": i["opened_round"], "resolved_round": i["resolved_round"]}


def _detail(sid: int) -> dict:
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    members = db.query_all(
        "SELECT * FROM members WHERE symposium_id=? ORDER BY order_idx", (sid,)
    )
    member_tokens = tokens.member_totals(sid)
    usage = tokens.session_totals(sid)
    hitl = db.query_one(
        "SELECT * FROM hitl_requests WHERE symposium_id=? AND status='pending' "
        "ORDER BY id DESC LIMIT 1", (sid,)
    )
    folders = library.attached_folders(sid)
    # эффективный профиль: явный у симпозиума, иначе активный владельца —
    # чтобы настройки показывали РЕАЛЬНО работающий профиль, а не пустоту
    effective_pid = sym["api_profile_id"]
    inherited = False
    if effective_pid is None:
        owner = db.query_one("SELECT active_profile_id FROM users WHERE id=?", (sym["owner_id"],))
        effective_pid = owner["active_profile_id"] if owner else None
        inherited = True
    profile = db.query_one(
        "SELECT id, name, model FROM api_profiles WHERE id=?", (effective_pid,)
    ) if effective_pid else None
    return {
        "id": sym["id"], "title": sym["title"], "topic": sym["topic"],
        "completion_criterion": sym["completion_criterion"],
        "report_format": sym["report_format"], "language": sym["language"],
        "status": sym["status"], "current_round": sym["current_round"],
        "token_limit": sym["token_limit"], "strict_markers": sym["strict_markers"],
        "compression_override": sym["compression_override"],
        "thinking_mode": sym["thinking_mode"], "thinking_depth": sym["thinking_depth"],
        "max_rounds": sym["max_rounds"],
        "form": sym["form"], "vote_policy": sym["vote_policy"], "end_reason": sym["end_reason"],
        "topic_full": sym["topic"], "fallback_profile_id": sym["fallback_profile_id"],
        "recovery_attempts": sym["recovery_attempts"],
        "thinking_mode": sym["thinking_mode"], "thinking_depth": sym["thinking_depth"],
        "summary_mode": db.get_setting("summary_mode", "digest"),
        "checklist": [{"ref": c["ref"], "text": c["text"], "status": c["status"]}
                      for c in db.query_all("SELECT * FROM checklist_items WHERE symposium_id=? ORDER BY id", (sid,))],
        "issues": [_issue_public(i) for i in db.query_all("SELECT * FROM issues WHERE symposium_id=? ORDER BY id", (sid,))],
        "evidence_count": db.query_value("SELECT COUNT(*) FROM evidence WHERE symposium_id=?", (sid,)),
        "api_profile": profile,
        "api_profile_inherited": inherited,
        "ws_state": ws.state_of(sid),
        "ws_reason": ws.state_info(sid)["reason"],
        "ws_mode": ws.state_info(sid)["mode"],
        "error_info": json.loads(sym["error_info"]) if sym["error_info"] else None,
        "pause_reason": sym["pause_reason"], "revived_count": sym["revived_count"],
        "created_at": sym["created_at"], "last_activity": sym["last_activity"],
        "usage": usage,
        "pending_hitl": _hitl_public_row(hitl) if hitl else None,
        "rag_folders": [{"id": f["id"], "name": f["name"]} for f in folders],
        "members": [
            {
                "id": m["id"], "name": m["name"], "description": m["description"],
                "role": m["role_name"], "role_id": m["role_id"],
                "user_prompt": m["user_prompt"], "temperature": m["temperature"],
                "max_tokens": m["max_tokens"], "order": m["order_idx"],
                "thinking_mode": m["thinking_mode"], "thinking_depth": m["thinking_depth"],
                "enabled": bool(m["enabled"]), "avatar_color": m["avatar_color"],
                "character_id": m["character_id"], "api_profile_id": m["api_profile_id"], "profile_fallback": bool(m["profile_fallback"]),
                "tools": json.loads(m["tools"]) if m["tools"] else None,
                "rag_folder_ids": json.loads(m["rag_folder_ids"]) if m["rag_folder_ids"] else [],
                "tokens": member_tokens.get(m["id"], {"tokens_in": 0, "tokens_out": 0}),
            }
            for m in members
        ],
    }


# -------------------------------------------------------------------- list

@router.get("")
def list_symposia(user: dict = Depends(get_current_user)):
    if user["is_admin"]:
        rows = db.query_all(
            "SELECT * FROM symposia ORDER BY last_activity DESC"
        )
    else:
        rows = db.query_all(
            "SELECT * FROM symposia WHERE owner_id=? ORDER BY last_activity DESC",
            (user["id"],),
        )
    return [_sym_public(s, user) for s in rows]


@router.post("")
async def create_symposium(body: SymposiumIn, user: dict = Depends(get_current_user)):
    if not body.title.strip() or not body.topic.strip():
        raise HTTPException(422, "Title and topic are required")
    if not body.members:
        raise HTTPException(422, "At least one participant is required")
    language = (body.language or "").strip()[:80]
    if not language:
        raise HTTPException(422, "language: укажите язык общения (например, Русский)")
    names = set()
    for m in body.members:
        nm = m.name.strip()
        if not nm and m.character_id:
            ch = db.query_one("SELECT name FROM characters WHERE id=?", (m.character_id,))
            nm = ch["name"] if ch else ""
        if not nm:
            raise HTTPException(422, "Every participant needs a name")
        if nm in names:
            raise HTTPException(422, f"Duplicate participant name: {nm}")
        names.add(nm)
        if not (0.0 <= m.temperature <= 1.0):
            raise HTTPException(422, f"Temperature of {m.name} must be within 0.0-1.0")
        if m.max_tokens < 32:
            raise HTTPException(422, f"Max reply tokens of {m.name} must be >= 32")
        _valid_thinking(m.thinking_mode, m.thinking_depth)
    _valid_thinking(body.thinking_mode, body.thinking_depth)
    _valid_form(body.form, body.vote_policy)
    if body.max_rounds is not None and body.max_rounds < 0:
        raise HTTPException(422, "max_rounds must be >= 0 (0 = unlimited)")

    now = utcnow()
    status = "paused_manual"
    if body.start_now:
        status = "active"
    effective_max_rounds = body.max_rounds
    if effective_max_rounds is None:
        effective_max_rounds = int(db.get_setting("default_max_rounds", 12) or 0)
    sid = db.execute(
        "INSERT INTO symposia(owner_id, title, topic, completion_criterion, report_format, "
        "language, status, api_profile_id, token_limit, thinking_mode, thinking_depth, "
        "max_rounds, form, vote_policy, created_at, updated_at, last_activity) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            user["id"], body.title.strip(), body.topic.strip(),
            body.completion_criterion.strip(), body.report_format.strip(),
            language, status, body.api_profile_id, body.token_limit,
            body.thinking_mode, body.thinking_depth,
            effective_max_rounds, body.form, body.vote_policy,
            now, now, now,
        ),
    )
    default_style = db.get_setting("avatar_default_style", "bust")
    for idx, m in enumerate(body.members):
        ch = db.query_one("SELECT * FROM characters WHERE id=?", (m.character_id,)) if m.character_id else None
        if m.character_id and ch is None:
            db.execute("DELETE FROM symposia WHERE id=?", (sid,))
            raise HTTPException(422, f"Unknown character id {m.character_id}")
        title = (ch["title"] if ch else "") or ""
        persona = (ch["personal_prompt"] if ch else "") or ""
        name = (m.name or (ch["name"] if ch else "")).strip()
        if not name:
            db.execute("DELETE FROM symposia WHERE id=?", (sid,))
            raise HTTPException(422, "Participant name is required")
        description = (m.description or (ch["description"] if ch else "")).strip()
        user_prompt = (m.user_prompt or "").strip()
        seed = ch["avatar_seed"] if ch else make_seed(name, description, "")
        style = ch["avatar_style"] if ch else default_style
        color = ch["avatar_color"] if ch else member_color(seed)
        options = avatar_options_from_seed(seed)
        avatar_rel = f"avatars/{sid}_{idx + 1}.svg"
        db.execute(
            "INSERT INTO members(symposium_id, order_idx, name, description, role_id, role_name, "
            "role_prompt_snapshot, user_prompt, temperature, max_tokens, thinking_mode, "
            "thinking_depth, avatar_seed, avatar_style, avatar_options, avatar_color, "
            "avatar_path, created_at, enabled, character_id, api_profile_id, tools, rag_folder_ids, "
            "speech_style, subagent_limit, profile_fallback, avatar_params) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, idx, name, description, None, title,
                persona, user_prompt, round(ch["temperature"] if ch else m.temperature, 1),
                ch["max_tokens"] if ch else m.max_tokens,
                (ch["thinking_mode"] if ch else None) or m.thinking_mode,
                (ch["thinking_depth"] if ch else None) or m.thinking_depth,
                seed, style, options, color, avatar_rel, now, 1 if m.enabled else 0,
                ch["id"] if ch else None, ch["api_profile_id"] if ch else None, ch["tools"] if ch else None,
                ch["rag_folder_ids"] if ch else None, (ch["speech_style"] if ch else "") or "",
                ch["subagent_limit"] if ch else None, ch["profile_fallback"] if ch else 1,
                (ch["avatar_params"] if ch else None),
            ),
        )
        try:
            ws.save_file(sid, avatar_rel, generate_svg(seed, style, name, json.loads((ch["avatar_params"] if ch else None) or "{}")))
        except Exception:  # noqa: BLE001  (avatar failure never blocks creation)
            pass
    for fid in body.rag_folder_ids:
        folder = db.query_one("SELECT * FROM rag_folders WHERE id=?", (fid,))
        if folder:
            library.attach(sid, fid)
            # per spec NG_00 §12.6: attach is fixed as a transcript event
            seq = (db.query_value(
                "SELECT COALESCE(MAX(seq),0) FROM messages WHERE symposium_id=?", (sid,)
            ) or 0) + 1
            db.execute(
                "INSERT INTO messages(symposium_id, round_id, seq, kind, content, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (sid, None, seq, "system", f"[RAG_FOLDER_ATTACHED: {folder['name']}]", now),
            )

    if body.start_now and body.api_profile_id is None:
        db.execute("UPDATE symposia SET status='paused_manual' WHERE id=?", (sid,))
    if status == "active":
        manager.ensure(sid)
    audit(user["id"], "create", "symposium", sid, {"title": body.title})
    return {"id": sid, "started": status == "active"}


@router.get("/{sid}")
async def get_symposium(sid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    detail = _detail(sid)
    # workspace auto-start on entering the symposium screen (spec NG_00 §14.5)
    if detail["ws_state"] in ("none", "stopped") and detail["status"] != "finished":
        asyncio.ensure_future(ws.ensure(sid))
    return detail


@router.patch("/{sid}")
def patch_symposium(sid: int, body: SymposiumPatch, user: dict = Depends(get_current_user)):
    owned_symposium(sid, user)
    updates = {}
    if body.title is not None:
        updates["title"] = body.title.strip()
    before = db.query_one("SELECT topic, completion_criterion FROM symposia WHERE id=?", (sid,))
    if body.topic is not None:
        if not body.topic.strip():
            raise HTTPException(422, "topic must not be empty")
        updates["topic"] = body.topic.strip()
    if body.completion_criterion is not None:
        updates["completion_criterion"] = body.completion_criterion.strip()
    if body.report_format is not None:
        updates["report_format"] = body.report_format.strip()
    if body.fallback_profile_id is not None:
        if body.fallback_profile_id and db.query_one("SELECT 1 FROM api_profiles WHERE id=?", (body.fallback_profile_id,)) is None:
            raise HTTPException(422, "No such API profile")
        updates["fallback_profile_id"] = body.fallback_profile_id or None
    if body.api_profile_id is not None:
        if body.api_profile_id == 0:
            # 0 = сбросить явный выбор → наследовать активный профиль владельца
            updates["api_profile_id"] = None
        else:
            if db.query_one("SELECT 1 FROM api_profiles WHERE id=?", (body.api_profile_id,)) is None:
                raise HTTPException(422, "No such API profile")
            updates["api_profile_id"] = body.api_profile_id
    if body.compression_override is not None:
        updates["compression_override"] = body.compression_override
    if body.token_limit is not None:
        updates["token_limit"] = body.token_limit
    if body.strict_markers is not None:
        updates["strict_markers"] = 1 if body.strict_markers else 0
    if body.max_rounds is not None:
        if body.max_rounds < 0:
            raise HTTPException(422, "max_rounds must be >= 0 (0 = unlimited)")
        updates["max_rounds"] = body.max_rounds
    _valid_thinking(body.thinking_mode, body.thinking_depth)
    _valid_form(body.form, body.vote_policy)
    if body.form is not None:
        updates["form"] = body.form
    if body.vote_policy is not None:
        updates["vote_policy"] = body.vote_policy
    if body.thinking_mode is not None:
        updates["thinking_mode"] = body.thinking_mode or "default"
    if body.thinking_depth is not None:
        updates["thinking_depth"] = body.thinking_depth or None
    if updates:
        updates["updated_at"] = utcnow()
        sets = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE symposia SET {sets} WHERE id=?", list(updates.values()) + [sid])
        audit(user["id"], "update", "symposium", sid, {"fields": list(body.model_dump(exclude_none=True).keys())})
        # участники читают тему/критерий из БД на каждом ходу; пометка в транскрипте
        # сообщает им, ЧТО именно изменилось и что с этого момента важно учитывать
        changed = []
        if "topic" in updates and updates["topic"] != before["topic"]:
            changed.append(f"topic → {updates['topic']}")
        if "completion_criterion" in updates and updates["completion_criterion"] != before["completion_criterion"]:
            changed.append(f"completion criterion → {updates['completion_criterion']}")
        if changed:
            from ..engine.store import Store
            Store(sid).add_message("system", "[CHANGED BY THE HUMAN: " + "; ".join(changed) + "]",
                                   round_id=(db.query_one("SELECT id FROM rounds WHERE symposium_id=? "
                                                          "ORDER BY number DESC LIMIT 1", (sid,)) or {}).get("id"))
        manager.wake(sid)
    return _detail(sid)


@router.post("/{sid}/checklist/regenerate")
async def regenerate_checklist(sid: int, user: dict = Depends(get_current_user)):
    """Пересоставить чек-лист по текущему критерию (после его правки)."""
    owned_symposium(sid, user)
    from ..engine.symposium import SymposiumEngine
    eng = manager.engines.get(sid) or SymposiumEngine(sid)
    profile = eng.store.profile()
    if profile is None:
        raise HTTPException(422, "No LLM profile configured")
    items = await eng.secretary.regenerate_checklist(profile)
    eng.store.add_message("system", "[CHECKLIST REGENERATED: " + "; ".join(f"C{i + 1}" for i in range(len(items))) + "]")
    eng.store.broadcast_registries()
    return {"items": items}


@router.delete("/{sid}")
async def delete_symposium(sid: int, user: dict = Depends(get_current_user)):
    sym = owned_symposium(sid, user)
    backup_path = None
    if db.get_setting("backup_on_delete", False):
        try:
            backup_path = _backup_workspace(sid, sym["title"])
        except Exception:  # noqa: BLE001
            backup_path = None
    asyncio.ensure_future(_delete_async(sid))
    audit(user["id"], "delete", "symposium", sid, {"title": sym["title"], "backup": backup_path})
    return {"ok": True, "backup": backup_path}


async def _delete_async(sid: int) -> None:
    await manager.stop(sid)
    await ws.remove(sid)


def _backup_workspace(sid: int, title: str) -> str:
    root = ws.ws_root(sid)
    if not root.exists():
        return ""
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", title)[:40] or "symposium"
    path = ws.ws_root(sid).parent.parent / "backup" / f"{sid}_{safe}_{utcnow().replace(':', '')}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in root.rglob("*"):
            if f.is_file():
                zf.write(f, str(f.relative_to(root)))
    return str(path)


# -------------------------------------------------------------- lifecycle

@router.post("/{sid}/start")
async def start(sid: int, user: dict = Depends(get_current_user)):
    sym = owned_symposium(sid, user)
    if sym["status"] == "finished":
        raise HTTPException(409, "The symposium is finished: write a message to the participants to continue it")
    if sym["status"] not in ("paused_manual", "paused_error"):
        raise HTTPException(409, f"Cannot start from status {sym['status']}")
    profile = _resolve_profile(sym)
    if profile is None:
        raise HTTPException(422, "No active LLM API profile: ask the admin to create one")
    db.execute(
        "UPDATE symposia SET status='active', error_info=NULL, pause_reason=NULL, "
        "updated_at=?, last_activity=? WHERE id=?", (utcnow(), utcnow(), sid)
    )
    manager.ensure(sid)
    audit(user["id"], "start", "symposium", sid)
    return {"status": "active"}


@router.post("/{sid}/pause")
async def pause(sid: int, user: dict = Depends(get_current_user)):
    sym = owned_symposium(sid, user)
    if sym["status"] != "active":
        raise HTTPException(409, f"Cannot pause from status {sym['status']}")
    db.execute(
        "UPDATE symposia SET status='paused_manual', pause_reason='manual', updated_at=? WHERE id=?",
        (utcnow(), sid),
    )
    # if no turn is in flight, flush pending interventions immediately
    if not sym["pending_turn"]:
        eng = manager.engines.get(sid)
        if eng:
            eng._flush_interventions()
    audit(user["id"], "pause", "symposium", sid)
    bus.publish(sid, {"type": "state", "status": "paused_manual"})
    return {"status": "paused_manual"}


@router.post("/{sid}/resume")
async def resume(sid: int, user: dict = Depends(get_current_user)):
    sym = owned_symposium(sid, user)
    if sym["status"] not in ("paused_manual", "paused_error"):
        raise HTTPException(409, f"Cannot resume from status {sym['status']}")
    if sym["status"] == "paused_error" and _resolve_profile(sym) is None:
        raise HTTPException(422, "Cannot resume: no LLM API profile configured")
    db.execute(
        "UPDATE symposia SET status='active', error_info=NULL, pause_reason=NULL, updated_at=?, "
        "last_activity=? WHERE id=?", (utcnow(), utcnow(), sid)
    )
    manager.ensure(sid)
    manager.wake(sid)
    audit(user["id"], "resume", "symposium", sid, {"from": sym["status"]})
    return {"status": "active"}


@router.post("/{sid}/stop")
async def force_stop(sid: int, user: dict = Depends(get_current_user)):
    """User-side forced stop: the symposium ends with a marked final report."""
    sym = owned_symposium(sid, user)
    if sym["status"] == "finished":
        raise HTTPException(409, "Already finished")
    profile = _resolve_profile(sym)
    rnd = db.query_one(
        "SELECT * FROM rounds WHERE symposium_id=? AND number=?", (sid, sym["current_round"])
    )
    rnd_id = rnd["id"] if rnd else None
    seq = (db.query_value("SELECT COALESCE(MAX(seq),0) FROM messages WHERE symposium_id=?", (sid,)) or 0) + 1
    db.execute(
        "INSERT INTO messages(symposium_id, round_id, seq, kind, content, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (sid, rnd_id, seq, "system", "[SYMPOSIUM STOPPED BY USER]", utcnow()),
    )
    db.execute(
        "UPDATE symposia SET status='finished', pending_turn=NULL, voting_state=NULL, "
        "pause_reason=NULL, error_info=NULL, end_reason='stopped_by_user', updated_at=? WHERE id=?", (utcnow(), sid)
    )
    bus.publish(sid, {"type": "state", "status": "finished"})
    if profile:
        asyncio.ensure_future(_final_report_for_stopped(sid))
    audit(user["id"], "force_stop", "symposium", sid)
    return {"status": "finished"}


async def _final_report_for_stopped(sid: int) -> None:
    from ..engine.symposium import SymposiumEngine
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    if sym is None:
        return
    profile = _resolve_profile(sym)
    if profile is None:
        return
    engine = SymposiumEngine(sid)
    try:
        await engine.secretary.final_report(profile)
    except (LLMPersistentError, Exception) as e:  # noqa: BLE001
        db.execute(
            "UPDATE symposia SET error_info=? WHERE id=?",
            (json.dumps({"type": "report_error", "message": str(e)[:400]}), sid),
        )


def _resolve_profile(sym: dict) -> dict | None:
    pid = sym["api_profile_id"]
    if pid is None:
        owner = db.query_one("SELECT active_profile_id FROM users WHERE id=?", (sym["owner_id"],))
        pid = owner["active_profile_id"] if owner else None
    if pid is None:
        return None
    return db.query_one("SELECT * FROM api_profiles WHERE id=?", (pid,))


# ------------------------------------------------------------- interventions

@router.post("/{sid}/message")
async def post_message(sid: int, body: MessageIn, user: dict = Depends(get_current_user)):
    sym = owned_symposium(sid, user)
    text = _compose_with_attachments(sid, (body.text or "").strip(), body.attachments)
    if not text:
        raise HTTPException(422, "Message text or an attachment is required")

    if sym["status"] in ("paused_error", "waiting_human"):
        raise HTTPException(
            409,
            "The symposium is waiting: "
            + ("answer the participant's question first" if sym["status"] == "waiting_human"
               else "resolve the error and resume the symposium first"),
        )

    if sym["status"] == "finished":
        return _revive(sym, text, user)

    # active or paused_manual: queue the intervention
    eng = manager.engines.get(sid)
    if sym["status"] == "active" and sym["pending_turn"]:
        if eng:
            eng.queue_intervention(text)
        else:
            manager.ensure(sid)
            manager.engines[sid].queue_intervention(text)
    else:
        rnd = db.query_one(
            "SELECT * FROM rounds WHERE symposium_id=? AND number=?", (sid, sym["current_round"])
        )
        seq = (db.query_value("SELECT COALESCE(MAX(seq),0) FROM messages WHERE symposium_id=?", (sid,)) or 0) + 1
        db.execute(
            "INSERT INTO messages(symposium_id, round_id, seq, kind, content, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (sid, rnd["id"] if rnd else None, seq, "human", text, utcnow()),
        )
        db.execute("UPDATE symposia SET last_activity=? WHERE id=?", (utcnow(), sid))
    return {"ok": True, "status": sym["status"]}


_TEXT_PREVIEW = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".py", ".js", ".log", ".sql", ".html"}


def _compose_with_attachments(sid: int, text: str, attachments: list[dict]) -> str:
    """Текст + список вложений. Вложения лежат в workspace/uploads; небольшие
    текстовые файлы показываются участникам фрагментом, остальное они читают
    сами кодером. Пути проверяются: только внутри uploads/ этого симпозиума."""
    if not attachments:
        return text
    lines = []
    for a in attachments[:10]:
        rel = str(a.get("path") or "")
        root = ws.ws_root(sid)
        path = (root / rel).resolve()
        if not rel.startswith("uploads/") or root.resolve() not in path.parents or not path.is_file():
            raise HTTPException(422, f"Attachment not found: {rel}")
        size = path.stat().st_size
        line = f"- `{rel}` ({a.get('name') or path.name}, {size} bytes)"
        if path.suffix.lower() in _TEXT_PREVIEW and size <= 200_000:
            head = path.read_text(encoding="utf-8", errors="replace")[:1500]
            line += f"\n  Beginning of the file:\n```\n{head}\n```"
        lines.append(line)
    block = ("[Attached by the human — files in the shared workspace; read and analyze them with the coder "
             "subagent (python/shell)]:\n" + "\n".join(lines))
    return (text + "\n\n" if text else "") + block


def _revive(sym: dict, text: str, user: dict) -> dict:
    sid = sym["id"]
    new_round = sym["current_round"] + 1
    rid = db.execute(
        "INSERT INTO rounds(symposium_id, number, status, started_at) VALUES(?,?,?,?)",
        (sid, new_round, "open", utcnow()),
    )
    seq = (db.query_value("SELECT COALESCE(MAX(seq),0) FROM messages WHERE symposium_id=?", (sid,)) or 0) + 1
    db.execute(
        "INSERT INTO messages(symposium_id, round_id, seq, kind, content, created_at) "
        "VALUES(?,?,?,?,?,?)", (sid, rid, seq, "system", "[RESUMED: the human wrote to the participants]", utcnow())
    )
    db.execute(
        "INSERT INTO messages(symposium_id, round_id, seq, kind, content, created_at) "
        "VALUES(?,?,?,?,?,?)", (sid, rid, seq + 1, "human", text, utcnow())
    )
    db.execute(
        "UPDATE symposia SET status='active', current_round=?, revived_count=revived_count+1, "
        "next_round_priority='[]', error_info=NULL, pause_reason=NULL, end_reason=NULL, "
        "voting_state=NULL, end_proposal=NULL, recovery_attempts=0, updated_at=?, "
        "last_activity=? WHERE id=?",
        (new_round, utcnow(), utcnow(), sid),
    )
    # лимит раундов не должен тут же закрыть возобновлённый симпозиум: даём ещё два раунда
    lim = sym.get("max_rounds")
    if lim and new_round + 1 >= lim:
        db.execute("UPDATE symposia SET max_rounds=? WHERE id=?", (new_round + 2, sid))
    manager.ensure(sid)
    manager.wake(sid)
    audit(user["id"], "revive", "symposium", sid)
    bus.publish(sid, {"type": "state", "status": "active", "revived": True})
    return {"ok": True, "status": "active", "revived": True}


# --------------------------------------------------------------- transcript

@router.get("/{sid}/transcript")
def transcript(sid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    msgs = db.query_all(
        "SELECT * FROM messages WHERE symposium_id=? ORDER BY seq", (sid,)
    )
    rounds = db.query_all(
        "SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (sid,)
    )
    return {
        "rounds": [
            {
                "id": r["id"], "number": r["number"], "status": r["status"],
                "summary": r["summary"], "summary_error": r["summary_error"],
                "started_at": r["started_at"], "ended_at": r["ended_at"],
            }
            for r in rounds
        ],
        "messages": [_msg(m) for m in msgs],
    }


def _msg(m: dict) -> dict:
    return {
        "id": m["id"], "round_id": m["round_id"], "seq": m["seq"], "kind": m["kind"],
        "member_id": m["member_id"], "content": m["content"], "thinking": m["thinking"],
        "tokens_in": m["tokens_in"], "tokens_out": m["tokens_out"], "model": m["model"],
        "meta": json.loads(m["meta"]) if m["meta"] else None,
        "created_at": m["created_at"],
    }


@router.get("/{sid}/registries")
def registries(sid: int, user: dict = Depends(get_current_user)):
    """Реестры симпозиума: чек-лист критерия, возражения, свидетельства."""
    get_symposium_or_404(sid, user)
    return {
        "checklist": [{"ref": c["ref"], "text": c["text"], "status": c["status"]}
                      for c in db.query_all("SELECT * FROM checklist_items WHERE symposium_id=? ORDER BY id", (sid,))],
        "issues": [_issue_public(i) for i in db.query_all("SELECT * FROM issues WHERE symposium_id=? ORDER BY id", (sid,))],
        "evidence": [{"ref": e["ref"], "member_id": e["member_id"], "round_id": e["round_id"],
                      "agent": e["agent"], "query": e["query"], "digest": e["digest"], "ok": bool(e["ok"]),
                      "created_at": e["created_at"]}
                     for e in db.query_all("SELECT * FROM evidence WHERE symposium_id=? ORDER BY id", (sid,))],
    }


@router.get("/{sid}/protocol")
def protocol(sid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    rounds = db.query_all(
        "SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (sid,)
    )
    report = db.query_one(
        "SELECT * FROM messages WHERE symposium_id=? AND kind='report' ORDER BY id DESC LIMIT 1",
        (sid,),
    )
    opening = db.query_one(
        "SELECT * FROM messages WHERE symposium_id=? AND kind='secretary' ORDER BY id LIMIT 1",
        (sid,),
    )
    return {
        "opening": _msg(opening) if opening else None,
        "rounds": [
            {
                "number": r["number"], "status": r["status"], "summary": r["summary"],
                "summary_error": r["summary_error"], "ended_at": r["ended_at"],
                "tokens_in": r["summary_tokens_in"], "tokens_out": r["summary_tokens_out"],
            }
            for r in rounds
        ],
        "final_report": _msg(report) if report else None,
    }


# ----------------------------------------------------------------- members

@router.patch("/{sid}/members/{mid}")
async def patch_member(sid: int, mid: int, body: MemberPatch, user: dict = Depends(get_current_user)):
    """Edit member parameters of a created symposium (v0.2.2).

    Editable: role (re-snapshotted), temperature, max_tokens, user prompt.
    Name and description are immutable (they are the member's identity in the
    transcript and the round-robin order). The engine reads the member row
    on every turn, so changes apply from the next reply on; a paused
    symposium is woken so a resume picks them up immediately.
    """
    owned_symposium(sid, user)
    member = member_of(sid, mid)
    sym = db.query_one("SELECT status FROM symposia WHERE id=?", (sid,))
    if sym and sym["status"] == "finished":
        raise HTTPException(422, "The symposium is finished")
    updates: dict = {}
    if body.prompt is not None:
        updates["role_prompt_snapshot"] = body.prompt
    if body.temperature is not None:
        if not (0.0 <= body.temperature <= 1.0):
            raise HTTPException(422, "Temperature must be within 0.0-1.0")
        updates["temperature"] = round(body.temperature, 1)
    if body.max_tokens is not None:
        if body.max_tokens < 32:
            raise HTTPException(422, "Max reply tokens must be >= 32")
        updates["max_tokens"] = body.max_tokens
    if body.user_prompt is not None:
        updates["user_prompt"] = body.user_prompt
    _valid_thinking(body.thinking_mode, body.thinking_depth)
    if body.thinking_mode is not None:
        updates["thinking_mode"] = body.thinking_mode or None
    if body.thinking_depth is not None:
        updates["thinking_depth"] = body.thinking_depth or None
    if body.api_profile_id is not None:
        if body.api_profile_id and not db.query_one("SELECT 1 FROM api_profiles WHERE id=?", (body.api_profile_id,)):
            raise HTTPException(422, "No such API profile")
        updates["api_profile_id"] = body.api_profile_id or None
    if body.tools is not None:
        from ..characters import ALL_TOOLS
        if body.tools == ["*"]:
            updates["tools"] = None
        else:
            bad = [t for t in body.tools if t not in ALL_TOOLS]
            if bad:
                raise HTTPException(422, f"unknown tools: {bad}")
            updates["tools"] = json.dumps(body.tools)
    if body.rag_folder_ids is not None:
        for fid in body.rag_folder_ids:
            if not db.query_one("SELECT 1 FROM rag_folders WHERE id=?", (fid,)):
                raise HTTPException(422, f"No such RAG folder {fid}")
        updates["rag_folder_ids"] = json.dumps(body.rag_folder_ids)
    if body.subagent_limit is not None:
        updates["subagent_limit"] = body.subagent_limit or None
    if body.speech_style is not None:
        updates["speech_style"] = body.speech_style
    if body.profile_fallback is not None:
        updates["profile_fallback"] = 1 if body.profile_fallback else 0
    if body.enabled is not None:
        if not body.enabled:
            others = db.query_value(
                "SELECT COUNT(*) FROM members WHERE symposium_id=? AND enabled=1 AND id<>?", (sid, mid))
            if not others:
                raise HTTPException(422, "At least one participant must stay enabled")
        updates["enabled"] = 1 if body.enabled else 0
    if not updates:
        raise HTTPException(422, "Nothing to update")
    sets = ", ".join(f"{k}=?" for k in updates)
    db.execute(f"UPDATE members SET {sets} WHERE id=?", list(updates.values()) + [mid])
    audit(user["id"], "update_member", "member", mid,
          {"symposium": sid, "member": member["name"], "fields": list(updates.keys())})
    manager.wake(sid)
    return {"ok": True, "updated": list(updates.keys())}


@router.get("/{sid}/members/{mid}/avatar")
def member_avatar(sid: int, mid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    m = member_of(sid, mid)
    svg = None
    try:
        svg = ws.read_file(sid, m["avatar_path"])["content"] if m["avatar_path"] else None
    except (FileNotFoundError, PermissionError, Exception):  # noqa: BLE001
        svg = None
    if not svg:
        # regenerate deterministically from the seed (spec NG_11 §6.4)
        svg = generate_svg(m["avatar_seed"], m["avatar_style"], m["name"], json.loads(m["avatar_params"] or "{}"))
        try:
            ws.save_file(sid, m["avatar_path"], svg)
        except Exception:  # noqa: BLE001
            pass
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/{sid}/members/{mid}/prompt")
def member_prompt(sid: int, mid: int, user: dict = Depends(get_current_user)):
    """Full prompt view: identity blocks + editable user block (view mode)."""
    get_symposium_or_404(sid, user)
    m = member_of(sid, mid)
    from ..engine import context as ctx_mod
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    members = db.query_all("SELECT * FROM members WHERE symposium_id=? ORDER BY order_idx", (sid,))
    system = ctx_mod.build_member_system_prompt(m, sym, members, db.get_setting("subagent_calls_per_turn", 3))
    return {
        "member": {"id": m["id"], "name": m["name"], "description": m["description"],
                   "role": m["role_name"], "role_prompt": m["role_prompt_snapshot"]},
        "user_prompt": m["user_prompt"],
        "assembled_system_prompt": system,
    }


# ------------------------------------------------------------------- HITL

@router.get("/{sid}/hitl/pending")
def hitl_pending(sid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    req = db.query_one(
        "SELECT * FROM hitl_requests WHERE symposium_id=? AND status='pending' "
        "ORDER BY id DESC LIMIT 1", (sid,)
    )
    return {"request": _hitl_public_row(req) if req else None}


@router.post("/{sid}/hitl/{req_id}/answer")
async def hitl_answer(sid: int, req_id: int, body: dict, user: dict = Depends(get_current_user)):
    owned_symposium(sid, user)
    req = db.query_one("SELECT * FROM hitl_requests WHERE id=? AND symposium_id=?", (req_id, sid))
    if req is None or req["status"] != "pending":
        raise HTTPException(404, "No pending question with this id")
    option = str(body.get("option") or "").strip()
    free = str(body.get("text") or "").strip()
    parts = [p for p in (option, free) if p]
    if not parts:
        raise HTTPException(422, "Select an option or type an answer")
    answer = " | ".join(parts)
    db.execute(
        "UPDATE hitl_requests SET status='answered', answer=?, answered_at=? WHERE id=?",
        (answer, utcnow(), req_id),
    )
    db.execute(
        "UPDATE symposia SET status='active', updated_at=?, last_activity=? WHERE id=?",
        (utcnow(), utcnow(), sid),
    )
    manager.ensure(sid)
    manager.wake(sid)
    bus.publish(sid, {"type": "hitl_answered", "request_id": req_id})
    audit(user["id"], "hitl_answer", "hitl_request", req_id, {"symposium": sid})
    return {"ok": True}


@router.post("/{sid}/hitl/{req_id}/cancel")
async def hitl_cancel(sid: int, req_id: int, user: dict = Depends(get_current_user)):
    owned_symposium(sid, user)
    req = db.query_one("SELECT * FROM hitl_requests WHERE id=? AND symposium_id=?", (req_id, sid))
    if req is None or req["status"] != "pending":
        raise HTTPException(404, "No pending question with this id")
    db.execute(
        "UPDATE hitl_requests SET status='cancelled', answered_at=? WHERE id=?",
        (utcnow(), req_id),
    )
    db.execute("UPDATE symposia SET status='active', updated_at=? WHERE id=?", (utcnow(), sid))
    manager.ensure(sid)
    manager.wake(sid)
    bus.publish(sid, {"type": "hitl_cancelled", "request_id": req_id})
    return {"ok": True}


# ------------------------------------------------------------- RAG folders

@router.post("/{sid}/rag-folders")
async def set_rag_folder(sid: int, body: dict, user: dict = Depends(get_current_user)):
    owned_symposium(sid, user)
    fid = body.get("folder_id")
    action = body.get("action")
    folder = db.query_one("SELECT * FROM rag_folders WHERE id=?", (fid,))
    if folder is None or action not in ("attach", "detach"):
        raise HTTPException(422, "folder_id and action (attach|detach) are required")
    if action == "attach":
        library.attach(sid, fid)
        event = f"[RAG_FOLDER_ATTACHED: {folder['name']}]"
    else:
        library.detach(sid, fid)
        event = f"[RAG_FOLDER_DETACHED: {folder['name']}]"
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    rnd = db.query_one(
        "SELECT * FROM rounds WHERE symposium_id=? AND number=?", (sid, sym["current_round"])
    )
    seq = (db.query_value("SELECT COALESCE(MAX(seq),0) FROM messages WHERE symposium_id=?", (sid,)) or 0) + 1
    db.execute(
        "INSERT INTO messages(symposium_id, round_id, seq, kind, content, created_at) VALUES(?,?,?,?,?,?)",
        (sid, rnd["id"] if rnd else None, seq, "system", event, utcnow()),
    )
    db.execute("UPDATE symposia SET last_activity=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), sid))
    audit(user["id"], f"rag_{action}", "symposium", sid, {"folder": folder["name"]})
    bus.publish(sid, {"type": "rag_folders_changed", "event": event})
    return {"ok": True, "attached": library.attached_folder_ids(sid)}


# --------------------------------------------------------------- uploads

@router.post("/{sid}/uploads")
async def upload_chat_file(sid: int, request: Request, user: dict = Depends(get_current_user)):
    owned_symposium(sid, user)
    max_size = int(db.get_setting("max_file_size_bytes", 50 * 1024 * 1024))
    form = await request.form()
    files = form.getlist("files") or ([form["file"]] if "file" in form else [])
    if not files:
        raise HTTPException(422, "No files")
    saved = []
    for f in files:
        data = await f.read()
        if len(data) > max_size:
            raise HTTPException(422, f"File {f.filename} exceeds the {max_size // (1024*1024)} MB limit")
        name = Path(f.filename or "upload.bin").name
        rel = f"uploads/{utcnow().replace(':', '').replace('-', '')}_{name}"
        ws.ensure_dirs(sid)
        ws.save_file(sid, rel, "")  # ensure parent dirs
        (ws.ws_root(sid) / rel).write_bytes(data)
        saved.append({"name": name, "path": rel, "size": len(data)})
    db.execute("UPDATE symposia SET last_activity=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), sid))
    audit(user["id"], "chat_upload", "symposium", sid, {"files": [s["name"] for s in saved]})
    return {"files": saved}


# ------------------------------------------------------------------ usage

@router.get("/{sid}/usage")
def usage(sid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    detail = tokens.session_totals(sid)
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    profile = _resolve_profile(sym)
    detail["token_limit"] = sym["token_limit"]
    members = {m["id"]: m for m in db.query_all("SELECT id, name, enabled FROM members WHERE symposium_id=?", (sid,))}
    mt = tokens.member_totals(sid)
    detail["by_member"] = [
        {"member_id": mid, "name": members[mid]["name"], "tokens_in": t.get("tokens_in", 0),
         "tokens_out": t.get("tokens_out", 0), "calls": t.get("calls", 0)}
        for mid, t in mt.items() if mid in members]
    detail["by_round"] = db.query_all(
        "SELECT r.number AS round, COALESCE(SUM(u.tokens_in),0) AS tokens_in, COALESCE(SUM(u.tokens_out),0) AS tokens_out, "
        "COUNT(u.id) AS calls FROM rounds r LEFT JOIN token_usage u ON u.round_id=r.id "
        "WHERE r.symposium_id=? GROUP BY r.id ORDER BY r.number", (sid,))
    if profile:
        detail["profile_limits"] = rate_limiter.usage(profile)
    return detail


# ------------------------------------------------------------------ export

def export_filename(sym: dict, ext: str) -> str:
    """Имя файла по требованию v0.3: {НАЗВАНИЕ СИМПОЗИУМА}_{id}.{ext}."""
    raw = f"{sym['title']}_{sym['id']}".strip().replace(" ", "_")
    safe = re.sub(r"[/\\\x00-\x1f]+", "", raw) or f"symposium_{sym['id']}"
    return f"{safe}.{ext}"


def _content_disposition(kind: str, fname: str) -> str:
    """Собрать заголовок Content-Disposition, не падая на кириллице.

    HTTP-заголовки обязаны быть latin-1: ascii-часть filename= получает
    безопасный вариант (symposium_{id}.{ext}), а оригинальное имя уезжает
    в filename*=UTF-8''<percent-encoded> (RFC 6266) — браузеры сохранят
    файл под оригинальным именем с кириллицей.
    """
    quoted = urllib.parse.quote(fname)
    try:
        fname.encode("latin-1")
        ascii_name = fname
    except UnicodeEncodeError:
        m = re.search(r"_(\d+)(\.[a-z]+)$", fname)
        ascii_name = f"symposium_{m.group(1) if m else 'export'}{m.group(2) if m else '.txt'}"
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'


@router.post("/{sid}/export")
def export_post(sid: int, body: dict, user: dict = Depends(get_current_user)):
    """Экспорт: format=md|json, filter=discussion|discussion_thinking|full,
    download=true → вложение с именем {TITLE}_{id}.{ext}, иначе inline
    (режим «просмотр»)."""
    sym = owned_symposium(sid, user)
    fmt = body.get("format", "md")
    if fmt == "dump":
        return Response(
            content=export_mod.export_dump(sid), media_type="application/zip",
            headers={"Content-Disposition": _content_disposition("attachment", export_filename(sym, "dump.zip"))})
    content_filter = body.get("filter", "discussion")
    if content_filter not in export_mod.FILTERS:
        raise HTTPException(422, f"filter must be one of: {', '.join(export_mod.FILTERS)}")
    options = {
        "filter": content_filter,
        # legacy-опции v0.2 для совместимости старых клиентов
        "message_ids": body.get("message_ids"),
        "include_thinking": bool(body.get("include_thinking", content_filter != "discussion")),
        "include_system_events": bool(body.get("include_system_events", content_filter == "full")),
    }
    if fmt == "json":
        data = export_mod.export_json(sid, options)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        media = "application/json"
        ext = "json"
    else:
        content = export_mod.export_markdown(sid, options)
        media = "text/markdown"
        ext = "md"
    fname = export_filename(sym, ext)
    disposition = "attachment" if body.get("download", True) else "inline"
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": _content_disposition(disposition, fname)},
    )


# --------------------------------------------------------------------- SSE

@router.get("/{sid}/events")
async def sse_events(sid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    queue = bus.subscribe(sid)

    async def gen():
        try:
            sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
            hello = {
                "type": "hello",
                "status": sym["status"] if sym else "gone",
                "current_round": sym["current_round"] if sym else 0,
            }
            yield f"data: {json.dumps(hello)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(sid, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")
