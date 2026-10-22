"""Admin panel routes (users, roles, prompts, profiles, settings, audit)."""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import db, tokens
from ..audit import audit
from ..auth import get_current_user, hash_password, public_user, require_admin, utcnow
from ..llm import client as llm_client
from ..llm import limits as limits_mod
from ..llm.rate import rate_limiter
from ..seeds import (
    default_role_prompt, default_system_prompt,
)
from .. import prompt_store

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ------------------------------------------------------------------ users

@router.get("/users")
def users(_: dict = Depends(require_admin)):
    rows = db.query_all("SELECT * FROM users ORDER BY id")
    out = []
    for u in rows:
        pu = public_user(u)
        pu["created_at"] = u["created_at"]
        out.append(pu)
    return out


class UserIn(dict):
    pass


@router.post("/users")
def create_user(body: dict, admin: dict = Depends(require_admin)):
    login = (body.get("login") or "").strip()
    password = body.get("password") or ""
    if not login or len(password) < 4:
        raise HTTPException(422, "Login and a password of at least 4 characters are required")
    if db.query_one("SELECT 1 FROM users WHERE login=?", (login,)):
        raise HTTPException(422, f"Login '{login}' is already taken")
    now = utcnow()
    uid = db.execute(
        "INSERT INTO users(login, password_hash, name, language, gender, is_admin, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            login, hash_password(password), body.get("name") or login,
            body.get("language") if body.get("language") in ("ru", "en") else "ru",
            body.get("gender") or "", 1 if body.get("is_admin") else 0, now, now,
        ),
    )
    audit(admin["id"], "create_user", "user", uid, {"login": login})
    return {"id": uid}


@router.patch("/users/{uid}")
def update_user(uid: int, body: dict, admin: dict = Depends(require_admin)):
    user = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
    if user is None:
        raise HTTPException(404, "User not found")
    updates = {}
    if "name" in body:
        updates["name"] = str(body["name"])[:200]
    if "is_admin" in body:
        updates["is_admin"] = 1 if body["is_admin"] else 0
    if "language" in body and body["language"] in ("ru", "en"):
        updates["language"] = body["language"]
    if "gender" in body:
        updates["gender"] = str(body["gender"])[:50]
    if updates:
        updates["updated_at"] = utcnow()
        sets = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE users SET {sets} WHERE id=?", list(updates.values()) + [uid])
        audit(admin["id"], "update_user", "user", uid, {"fields": list(updates.keys())})
    return public_user(db.query_one("SELECT * FROM users WHERE id=?", (uid,)))


@router.delete("/users/{uid}")
def delete_user(uid: int, admin: dict = Depends(require_admin)):
    if uid == admin["id"]:
        raise HTTPException(422, "You cannot delete your own account")
    user = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
    if user is None:
        raise HTTPException(404, "User not found")
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    audit(admin["id"], "delete_user", "user", uid, {"login": user["login"]})
    return {"ok": True}


# -------------------------------------------------------- system prompts

@router.get("/prompts")
def prompts(_: dict = Depends(require_admin)):
    """Список системных промптов: ключи из папки prompts/system (+ ключи,
    существующие только в БД — правки админа удалённых ключей)."""
    keys = set(prompt_store.system_keys())
    for row in db.query_all("SELECT DISTINCT key FROM system_prompts"):
        keys.add(row["key"])
    out = []
    for key in sorted(keys):
        content = prompt_store.active_system_prompt(key)
        file_text = prompt_store.read_system_prompt(key)
        override = db.query_one(
            "SELECT * FROM system_prompts WHERE key=? AND is_active=1 "
            "ORDER BY version DESC LIMIT 1",
            (key,),
        )
        out.append({
            "key": key,
            "active_version": override["version"] if override else 0,
            "content": content,
            "note": override["note"] if override else "",
            # источник: файл (дефолт, перечитывается с диска) или правка админа
            "source": "override" if override else "file",
            "is_default": content == file_text,
            "default_content": file_text,
            "updated_at": override["created_at"] if override else None,
        })
    return out


@router.get("/prompts/{key}/versions")
def prompt_versions(key: str, _: dict = Depends(require_admin)):
    return db.query_all(
        "SELECT p.key, p.version, p.is_active, p.note, p.created_at, p.content, "
        "u.login AS updated_by FROM system_prompts p LEFT JOIN users u ON u.id=p.updated_by "
        "WHERE p.key=? ORDER BY p.version DESC",
        (key,),
    )


@router.put("/prompts/{key}")
def update_prompt(key: str, body: dict, admin: dict = Depends(require_admin)):
    """Правка админа: создаёт активную строку в БД, перекрывающую файл."""
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(422, "Prompt content cannot be empty")
    note = (body.get("note") or "").strip()
    latest = db.query_value("SELECT COALESCE(MAX(version),0) FROM system_prompts WHERE key=?", (key,))
    db.execute("UPDATE system_prompts SET is_active=0 WHERE key=?", (key,))
    db.execute(
        "INSERT INTO system_prompts(key, version, content, is_active, note, updated_by, created_at) "
        "VALUES(?,?,?,1,?,?,?)",
        (key, latest + 1, content, note, admin["id"], utcnow()),
    )
    audit(admin["id"], "update_system_prompt", "system_prompt", key,
          {"new_version": latest + 1})
    return {"key": key, "version": latest + 1}


@router.post("/prompts/{key}/rollback/{version}")
def rollback_prompt(key: str, version: int, admin: dict = Depends(require_admin)):
    row = db.query_one(
        "SELECT * FROM system_prompts WHERE key=? AND version=?", (key, version)
    )
    if row is None:
        raise HTTPException(404, "Version not found")
    latest = db.query_value("SELECT COALESCE(MAX(version),0) FROM system_prompts WHERE key=?", (key,))
    db.execute("UPDATE system_prompts SET is_active=0 WHERE key=?", (key,))
    db.execute(
        "INSERT INTO system_prompts(key, version, content, is_active, note, updated_by, created_at) "
        "VALUES(?,?,?,1,?,?,?)",
        (key, latest + 1, row["content"], f"rollback to v{version}", admin["id"], utcnow()),
    )
    audit(admin["id"], "rollback_system_prompt", "system_prompt", key,
          {"rolled_back_to": version, "new_version": latest + 1})
    return {"ok": True, "version": latest + 1}


@router.post("/prompts/{key}/reset")
def reset_prompt(key: str, admin: dict = Depends(require_admin)):
    """Сброс к дефолту: БД-правки удаляются, действует файл prompts/system.

    Дефолт теперь живёт в файле и перечитывается с диска — «сброс»
    возвращает промпт под управление файла."""
    file_text = prompt_store.read_system_prompt(key)
    if not file_text:
        raise HTTPException(404, f"No factory default file for prompt '{key}'")
    db.execute("DELETE FROM system_prompts WHERE key=?", (key,))
    audit(admin["id"], "reset_system_prompt", "system_prompt", key,
          {"action": "cleared overrides; file is the source"})
    return {"ok": True, "version": 0, "content": file_text, "source": "file"}


# -------------------------------------------------------------- settings

@router.get("/settings")
def get_settings(_: dict = Depends(require_admin)):
    raw = db.all_settings()
    ints = {
        "compression_threshold", "workspace_idle_timeout_sec", "max_file_size_bytes",
        "max_rag_folder_bytes", "subagent_calls_per_turn", "backup_retention_hours",
        "rag_chunk_chars", "rag_chunk_overlap", "embedding_dims", "workspace_mem_limit_mb",
        "web_search_max_results", "web_search_timeout_sec", "stall_rounds", "vote_max_tokens",
        "subagent_digest_tokens", "sandbox_max_timeout_sec", "default_max_rounds",
    }
    bools = {"backup_on_delete", "strict_state", "web_search_enabled", "workspace_allow_local"}
    lists = {"web_search_engines"}
    out = {}
    for k, v in raw.items():
        if k in ints:
            out[k] = int(v)
        elif k in bools:
            out[k] = v in ("1", "true", "True")
        elif k in lists or k == "available_languages":
            try:
                out[k] = json.loads(v)
            except ValueError:
                out[k] = []
        else:
            out[k] = v
    return out


@router.patch("/settings")
def patch_settings(body: dict, admin: dict = Depends(require_admin)):
    allowed = set(db.all_settings().keys())
    changed = {}
    for k, v in body.items():
        if k not in allowed:
            raise HTTPException(422, f"Unknown setting: {k}")
        db.set_setting(k, v if isinstance(v, str) else json.dumps(v) if isinstance(v, list) else str(int(bool(v))) if isinstance(v, bool) else str(v))
        changed[k] = v
    audit(admin["id"], "update_settings", "settings", "", {"fields": list(changed.keys())})
    return {"ok": True, "updated": changed}


# ------------------------------------------------------------ web search

@router.get("/web-search")
def web_search_settings(_: dict = Depends(require_admin)):
    from ..subagents import search as web_search
    return web_search.search_status()


@router.post("/web-search/test")
async def web_search_test(body: dict, admin: dict = Depends(require_admin)):
    """Live probe of the search backend: runs a real query and returns what
    each configured engine produced (for the admin panel test button)."""
    from ..subagents import search as web_search
    query = (body.get("query") or "openai news").strip()
    status = web_search.search_status()
    try:
        results = await web_search.search(query)
    except RuntimeError as e:
        return {"ok": False, "query": query, "error": str(e), "engines": status["engines"], "results": []}
    per_engine: dict[str, int] = {}
    for r in results:
        per_engine[r.get("engine", "?")] = per_engine.get(r.get("engine", "?"), 0) + 1
    return {
        "ok": bool(results),
        "query": query,
        "engines": status["engines"],
        "per_engine": per_engine,
        "results": [
            {"title": r["title"], "url": r["url"], "engine": r.get("engine", "?")}
            for r in results[:6]
        ],
        "error": "" if results else "no results from any enabled engine",
    }


# ---------------------------------------------------------- API profiles

@router.get("/profiles")
def profiles(_: dict = Depends(require_admin)):
    rows = db.query_all("SELECT * FROM api_profiles ORDER BY id")
    out = []
    for p in rows:
        usage = rate_limiter.usage(p)
        totals = db.query_one(
            "SELECT COALESCE(SUM(tokens_in+tokens_out),0) AS tok, COUNT(*) AS calls "
            "FROM token_usage WHERE profile_id=?", (p["id"],)
        )
        out.append({
            "id": p["id"], "name": p["name"], "base_url": p["base_url"],
            "api_key_masked": ("*" * 8 + p["api_key"][-4:]) if len(p["api_key"]) > 4 else "",
            "model": p["model"], "limits": limits_mod.policy_of(p),
            "thinking_mode": p["thinking_mode"], "thinking_depth": p["thinking_depth"],
            "supports_thinking": bool(p["supports_thinking"]),
            "extra_body": p["extra_body"] or "",
            "created_at": p["created_at"],
            "limits_usage": usage,
            "total_tokens": totals["tok"], "total_calls": totals["calls"],
        })
    return out


@router.get("/limit-presets")
def limit_presets(_: dict = Depends(require_admin)):
    """Пресеты лимитов провайдеров (OpenRouter, Mistral, …) — отправная точка."""
    return [{**p, "policy": limits_mod.normalize(p["policy"])} for p in limits_mod.PRESETS]


def _limits_from_body(body: dict) -> str | None:
    """limits | preset | устаревшие плоские поля → JSON политики (None — не менять)."""
    if body.get("preset"):
        pol = limits_mod.preset(str(body["preset"]))
        if pol is None:
            raise HTTPException(422, f"unknown limits preset '{body['preset']}'")
        return json.dumps(pol)
    if isinstance(body.get("limits"), dict):
        return json.dumps(limits_mod.normalize(body["limits"]))
    legacy = {k: body[k] for k in ("max_concurrent", "min_pause_ms", "requests_per_minute",
                                   "requests_per_hour", "requests_per_day") if k in body}
    if legacy:
        return json.dumps(limits_mod.normalize({
            "enabled": True, "max_concurrent": legacy.get("max_concurrent"),
            "min_gap_ms": legacy.get("min_pause_ms"), "rpm": legacy.get("requests_per_minute"),
            "rph": legacy.get("requests_per_hour"), "rpd": legacy.get("requests_per_day")}))
    return None


def _thinking_mode(v) -> str:
    v = v or "default"
    if v not in ("default", "off", "on"):
        raise HTTPException(422, "thinking_mode must be one of: default, off, on")
    return v


def _thinking_depth(v) -> str | None:
    if v in (None, ""):
        return None
    if v not in ("low", "medium", "high"):
        raise HTTPException(422, "thinking_depth must be one of: low, medium, high")
    return v


@router.post("/profiles")
def create_profile(body: dict, admin: dict = Depends(require_admin)):
    name = (body.get("name") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    model = (body.get("model") or "").strip()
    if not name or not base_url or not model:
        raise HTTPException(422, "name, base_url and model are required")
    extra_body = _validate_extra_body(body.get("extra_body"))
    limits = _limits_from_body(body) or json.dumps(limits_mod.normalize(None))
    now = utcnow()
    pid = db.execute(
        "INSERT INTO api_profiles(name, base_url, api_key, model, max_concurrent, min_pause_ms, "
        "limits, supports_thinking, extra_body, thinking_mode, thinking_depth, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, base_url, body.get("api_key") or "", model, 1, 0, limits,
         1 if body.get("supports_thinking") else 0, extra_body,
         _thinking_mode(body.get("thinking_mode")), _thinking_depth(body.get("thinking_depth")), now, now),
    )
    audit(admin["id"], "create_profile", "api_profile", pid, {"name": name, "model": model})
    return {"id": pid}


def _validate_extra_body(value) -> str:
    """Profile extra_body: must be a JSON object string (or empty)."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    import json as _json
    try:
        data = _json.loads(text)
    except ValueError:
        raise HTTPException(422, "extra_body must be valid JSON, e.g. {\"thinking\": {\"type\": \"disabled\"}}")
    if not isinstance(data, dict):
        raise HTTPException(422, "extra_body must be a JSON object")
    return text


@router.patch("/profiles/{pid}")
def update_profile(pid: int, body: dict, admin: dict = Depends(require_admin)):
    p = db.query_one("SELECT * FROM api_profiles WHERE id=?", (pid,))
    if p is None:
        raise HTTPException(404, "Profile not found")
    updates = {}
    for field in ("name", "base_url", "model"):
        if field in body and (body[field] or "").strip():
            updates[field] = body[field].strip()
    if "api_key" in body and body["api_key"]:
        updates["api_key"] = body["api_key"]
    new_limits = _limits_from_body(body)
    if new_limits is not None:
        updates["limits"] = new_limits
    if "thinking_mode" in body:
        updates["thinking_mode"] = _thinking_mode(body["thinking_mode"])
    if "thinking_depth" in body:
        updates["thinking_depth"] = _thinking_depth(body["thinking_depth"])
    if "supports_thinking" in body:
        updates["supports_thinking"] = 1 if body["supports_thinking"] else 0
    if "extra_body" in body:
        updates["extra_body"] = _validate_extra_body(body["extra_body"])
    if updates:
        updates["updated_at"] = utcnow()
        sets = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE api_profiles SET {sets} WHERE id=?", list(updates.values()) + [pid])
        rate_limiter.invalidate(pid)
        llm_client.invalidate_client(pid)
        audit(admin["id"], "update_profile", "api_profile", pid, {"fields": list(updates.keys())})
    return {"ok": True}


@router.delete("/profiles/{pid}")
def delete_profile(pid: int, admin: dict = Depends(require_admin)):
    p = db.query_one("SELECT * FROM api_profiles WHERE id=?", (pid,))
    if p is None:
        raise HTTPException(404, "Profile not found")
    db.execute("UPDATE symposia SET api_profile_id=NULL WHERE api_profile_id=?", (pid,))
    db.execute("UPDATE users SET active_profile_id=NULL WHERE active_profile_id=?", (pid,))
    db.execute("DELETE FROM api_profiles WHERE id=?", (pid,))
    rate_limiter.invalidate(pid)
    llm_client.invalidate_client(pid)
    audit(admin["id"], "delete_profile", "api_profile", pid, {"name": p["name"]})
    return {"ok": True}


@router.post("/profiles/{pid}/test")
async def test_profile(pid: int, admin: dict = Depends(require_admin)):
    p = db.query_one("SELECT * FROM api_profiles WHERE id=?", (pid,))
    if p is None:
        raise HTTPException(404, "Profile not found")
    result = await llm_client.test_connection(p)
    audit(admin["id"], "test_profile", "api_profile", pid, {"ok": result["ok"]})
    return result


# --------------------------------------------------------------- symposia

@router.get("/symposia")
def admin_list_symposia(_: dict = Depends(require_admin)):
    rows = db.query_all(
        "SELECT s.*, u.login AS owner_login FROM symposia s LEFT JOIN users u ON u.id=s.owner_id "
        "ORDER BY s.last_activity DESC"
    )
    return [
        {
            "id": r["id"], "title": r["title"], "owner": r["owner_login"],
            "status": r["status"], "current_round": r["current_round"],
            "last_activity": r["last_activity"], "ws_state": r["ws_state"],
            "ws_reason": r["ws_reason"],
            "tokens": db.query_value(
                "SELECT COALESCE(SUM(tokens_in+tokens_out),0) FROM token_usage WHERE symposium_id=?",
                (r["id"],),
            ),
        }
        for r in rows
    ]


@router.get("/workspaces")
def workspaces_list(_: dict = Depends(require_admin)):
    return db.query_all(
        "SELECT id, title, status, ws_state, ws_reason FROM symposia "
        "WHERE ws_state<>'none' ORDER BY id"
    )


@router.post("/symposia/{sid}/force-stop")
def admin_force_stop(sid: int, admin: dict = Depends(require_admin)):
    from .symposia_routes import _resolve_profile  # local import to avoid cycle
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    if sym is None:
        raise HTTPException(404, "Symposium not found")
    db.execute(
        "UPDATE symposia SET status='paused_manual', pause_reason='admin_force_stop', "
        "pending_turn=NULL, voting_state=NULL, updated_at=? WHERE id=?",
        (utcnow(), sid),
    )
    audit(admin["id"], "force_stop", "symposium", sid)
    return {"ok": True}


# ------------------------------------------------------------ round compare

@router.get("/rounds/{rid}/compare")
def round_compare(rid: int, _: dict = Depends(require_admin)):
    rnd = db.query_one("SELECT * FROM rounds WHERE id=?", (rid,))
    if rnd is None:
        raise HTTPException(404, "Round not found")
    msgs = db.query_all(
        "SELECT * FROM messages WHERE round_id=? ORDER BY seq", (rid,)
    )
    names = {m["id"]: m["name"] for m in db.query_all(
        "SELECT id, name FROM members WHERE symposium_id=?", (rnd["symposium_id"],)
    )}
    original = "\n".join(
        f"{names.get(m['member_id'], 'Secretary')}: {m['content'] or '[SKIP_TURN]'}"
        if m["kind"] in ("member", "skip")
        else (f"[Human] {m['content']}" if m["kind"] == "human" else m["content"])
        for m in msgs
    )
    return {
        "round": {"number": rnd["number"], "status": rnd["status"]},
        "original": original,
        "summary": rnd["summary"],
        "summary_error": rnd["summary_error"],
    }


# ------------------------------------------------------------------ audit

@router.get("/audit")
def audit_log(
    user_id: int | None = None, action: str | None = None,
    symposium_id: str | None = None, limit: int = 100, offset: int = 0,
    _: dict = Depends(require_admin),
):
    where, params = [], []
    if user_id:
        where.append("user_id=?")
        params.append(user_id)
    if action:
        where.append("action LIKE ?")
        params.append(f"%{action}%")
    if symposium_id:
        where.append("(object_type='symposium' AND object_id=?)")
        params.append(symposium_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.query_all(
        f"SELECT a.*, u.login FROM audit_log a LEFT JOIN users u ON u.id=a.user_id "
        f"{clause} ORDER BY a.id DESC LIMIT ? OFFSET ?",
        params + [min(limit, 500), offset],
    )
    total = db.query_value(f"SELECT COUNT(*) FROM audit_log a {clause}", params)
    return {"total": total, "items": rows}


@router.get("/audit/export")
def audit_export(_: dict = Depends(require_admin)):
    rows = db.query_all(
        "SELECT a.id, u.login, a.action, a.object_type, a.object_id, a.details, a.created_at "
        "FROM audit_log a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id"
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "user", "action", "object_type", "object_id", "details", "created_at"])
    for r in rows:
        writer.writerow([r["id"], r["login"], r["action"], r["object_type"],
                         r["object_id"], r["details"], r["created_at"]])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
