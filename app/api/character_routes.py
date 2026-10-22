"""Characters API: list (built-in + own), create, edit, clone, avatar editor."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import characters as chars
from .. import db, portrait
from ..audit import audit
from ..auth import utcnow
from ..avatars import generate_svg, member_color
from .deps import get_current_user

router = APIRouter(prefix="/api/characters", tags=["characters"])
FIELDS = ("name", "title", "description", "speech_style", "api_profile_id", "temperature", "max_tokens",
          "thinking_mode", "thinking_depth", "subagent_limit")


def _check(body: dict) -> dict:
    out: dict = {k: body[k] for k in FIELDS if k in body}
    if "prompt" in body:
        out["personal_prompt"] = str(body["prompt"] or "")
    if "name" in out:
        out["name"] = str(out["name"]).strip()
        if not out["name"]:
            raise HTTPException(422, "name is required")
    if out.get("api_profile_id") in (0, ""):
        out["api_profile_id"] = None
    if out.get("api_profile_id") and not db.query_one("SELECT 1 FROM api_profiles WHERE id=?", (out["api_profile_id"],)):
        raise HTTPException(422, "No such API profile")
    if "profile_fallback" in body:
        out["profile_fallback"] = 1 if body["profile_fallback"] else 0
    if "temperature" in out:
        out["temperature"] = max(0.0, min(1.0, round(float(out["temperature"]), 1)))
    if "max_tokens" in out:
        out["max_tokens"] = max(64, int(out["max_tokens"]))
    if out.get("thinking_mode") not in (None, "", "off", "on", "default"):
        raise HTTPException(422, "thinking_mode must be off, on or empty")
    if out.get("thinking_depth") not in (None, "", "low", "medium", "high"):
        raise HTTPException(422, "thinking_depth must be low, medium, high or empty")
    for k in ("thinking_mode", "thinking_depth"):
        if k in out and out[k] in ("", "default"):
            out[k] = None
    if out.get("subagent_limit") in (0, ""):
        out["subagent_limit"] = None
    if "tools" in body:
        t = body["tools"] if body["tools"] is not None else list(chars.DEFAULT_TOOLS)
        bad = [x for x in t if x not in chars.ALL_TOOLS]
        if bad:
            raise HTTPException(422, f"unknown tools: {bad}")
        out["tools"] = json.dumps(t)
    if "rag_folder_ids" in body:
        ids = [int(x) for x in (body["rag_folder_ids"] or [])]
        for i in ids:
            if not db.query_one("SELECT 1 FROM rag_folders WHERE id=?", (i,)):
                raise HTTPException(422, f"No such RAG folder {i}")
        out["rag_folder_ids"] = json.dumps(ids)
    if "avatar" in body:
        out["avatar_params"] = json.dumps(portrait.sanitize(body["avatar"]))
    return out


def _get(cid: int) -> dict:
    row = db.query_one("SELECT * FROM characters WHERE id=?", (cid,))
    if row is None:
        raise HTTPException(404, "Character not found")
    return row


def _editable(row: dict, user: dict) -> None:
    if row["is_default"]:
        raise HTTPException(403, "Built-in characters live in prompts/characters/*.json — clone it to customize")
    if row["owner_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Not your character")


@router.get("/avatar-options")
def avatar_options(_: dict = Depends(get_current_user)):
    return portrait.OPTIONS


@router.get("")
def list_characters(lang: str = "en", user: dict = Depends(get_current_user)):
    chars.sync_defaults()
    rows = db.query_all("SELECT * FROM characters WHERE is_default=1 OR owner_id=? ORDER BY is_default DESC, name", (user["id"],))
    return [chars.public(r, user, lang) for r in rows]


@router.post("")
def create(body: dict, user: dict = Depends(get_current_user)):
    if not (body.get("name") or "").strip():
        raise HTTPException(422, "name is required")
    f = _check(body)
    seed = chars.new_seed(f["name"])
    now = utcnow()
    cid = db.execute(
        "INSERT INTO characters(owner_id, is_default, name, title, description, personal_prompt, speech_style, avatar_seed, "
        "avatar_style, avatar_color, avatar_params, api_profile_id, profile_fallback, temperature, max_tokens, thinking_mode, "
        "thinking_depth, tools, rag_folder_ids, subagent_limit, created_at, updated_at) VALUES(?,0,?,?,?,?,?,?,'bust',?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user["id"], f["name"], f.get("title", ""), f.get("description", ""), f.get("personal_prompt", ""), f.get("speech_style", ""),
         seed, member_color(seed), f.get("avatar_params", "{}"), f.get("api_profile_id"), f.get("profile_fallback", 1),
         f.get("temperature", 0.7), f.get("max_tokens", chars.DEFAULT_MAX_TOKENS), f.get("thinking_mode"), f.get("thinking_depth"),
         f.get("tools", json.dumps(chars.DEFAULT_TOOLS)), f.get("rag_folder_ids"), f.get("subagent_limit"), now, now))
    audit(user["id"], "create", "character", cid, {"name": f["name"]})
    return chars.public(_get(cid), user)


@router.patch("/{cid}")
def patch(cid: int, body: dict, user: dict = Depends(get_current_user)):
    row = _get(cid)
    _editable(row, user)
    f = _check(body)
    if f:
        f["updated_at"] = utcnow()
        db.execute(f"UPDATE characters SET {', '.join(k + '=?' for k in f)} WHERE id=?", list(f.values()) + [cid])
    return chars.public(_get(cid), user)


@router.post("/{cid}/clone")
def clone(cid: int, user: dict = Depends(get_current_user)):
    """Create a new character based on this one (built-in or own)."""
    src = _get(cid)
    seed = src["avatar_seed"] if src["is_default"] else chars.new_seed(src["name"])
    params = json.dumps(portrait.resolve(src["avatar_seed"], json.loads(src["avatar_params"] or "{}")))
    now = utcnow()
    new = db.execute(
        "INSERT INTO characters(owner_id, is_default, name, title, description, personal_prompt, speech_style, avatar_seed, "
        "avatar_style, avatar_color, avatar_params, api_profile_id, profile_fallback, temperature, max_tokens, thinking_mode, "
        "thinking_depth, tools, rag_folder_ids, subagent_limit, created_at, updated_at, i18n) "
        "VALUES(?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user["id"], src["name"] + " (copy)", src["title"], src["description"], src["personal_prompt"], src["speech_style"],
         seed, src["avatar_style"], src["avatar_color"], params, src["api_profile_id"], src["profile_fallback"], src["temperature"],
         src["max_tokens"], src["thinking_mode"], src["thinking_depth"], src["tools"], src["rag_folder_ids"],
         src["subagent_limit"], now, now, src["i18n"]))
    return chars.public(_get(new), user)


router.add_api_route("/{cid}/duplicate", clone, methods=["POST"])   # имя из 0.4.2


@router.delete("/{cid}")
def delete(cid: int, user: dict = Depends(get_current_user)):
    row = _get(cid)
    _editable(row, user)
    db.execute("DELETE FROM characters WHERE id=?", (cid,))
    audit(user["id"], "delete", "character", cid, {"name": row["name"]})
    return {"ok": True}


@router.post("/{cid}/avatar")
def reroll_avatar(cid: int, user: dict = Depends(get_current_user)):
    """New random look: fresh seed, appearance controls reset to seed defaults."""
    row = _get(cid)
    _editable(row, user)
    seed = chars.new_seed(row["name"])
    db.execute("UPDATE characters SET avatar_seed=?, avatar_params='{}', avatar_color=?, updated_at=? WHERE id=?",
               (seed, member_color(seed), utcnow(), cid))
    return chars.public(_get(cid), user)


@router.get("/{cid}/avatar")
def avatar(cid: int, user: dict = Depends(get_current_user)):
    row = _get(cid)
    svg = generate_svg(row["avatar_seed"], row["avatar_style"], row["name"], json.loads(row["avatar_params"] or "{}"))
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})


@router.post("/preview")
def preview(body: dict, _: dict = Depends(get_current_user)):
    """SVG for the avatar editor without saving: {seed, avatar:{...}}."""
    seed = str(body.get("seed") or "preview")
    return Response(generate_svg(seed, "bust", "", body.get("avatar") or {}), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})
