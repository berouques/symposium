"""Characters — presets of participants.

A character carries everything a participant needs: portrait, name, title,
description, persona prompt, manner of speech, own LLM profile (+ fallback
flag), temperature / reply budget / reasoning, permitted tools, private RAG
folders. A symposium gets a SNAPSHOT of the character: later edits never
change past symposia.

Built-in characters live in prompts/characters/*.json — edit the files, the
change is picked up live (file modification times are checked on access).
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from . import db
from .auth import utcnow
from .avatars import make_seed, member_color
from .logging_setup import get_logger

log = get_logger("characters")
ALL_TOOLS = ("web_explorer", "web_fetch", "archivarius", "coder", "ask_human")
DEFAULT_TOOLS = ["ask_human"]
DEFAULT_MAX_TOKENS = 350
DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "characters"
_sig: dict[str, float] = {}


def _jl(raw) -> list | None:
    if raw in (None, ""):
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, list) else None
    except ValueError:
        return None


def _jd(raw) -> dict:
    try:
        v = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def public(row: dict, user: dict | None = None, lang: str = "en") -> dict:
    loc = _jd(row.get("i18n")).get(lang) or {}
    return {
        "id": row["id"], "key": row["key"], "is_default": bool(row["is_default"]),
        "mine": bool(user and row["owner_id"] == user["id"]), "name": row["name"],
        "title": loc.get("title") or row["title"], "description": loc.get("description") or row["description"],
        "prompt": row["personal_prompt"], "speech_style": row["speech_style"],
        "avatar_seed": row["avatar_seed"], "avatar_style": row["avatar_style"], "avatar_color": row["avatar_color"],
        "avatar": _jd(row.get("avatar_params")),
        "api_profile_id": row["api_profile_id"], "profile_fallback": bool(row["profile_fallback"]),
        "temperature": row["temperature"], "max_tokens": row["max_tokens"],
        "thinking_mode": row["thinking_mode"], "thinking_depth": row["thinking_depth"],
        "tools": _jl(row["tools"]) if row["tools"] is not None else None,
        "rag_folder_ids": _jl(row["rag_folder_ids"]) or [], "subagent_limit": row["subagent_limit"],
    }


def new_seed(name: str) -> str:
    return make_seed(name + secrets.token_hex(4), "", "character")


def _files() -> dict[str, Path]:
    return {f.stem: f for f in sorted(DEFAULTS_DIR.glob("*.json"))} if DEFAULTS_DIR.is_dir() else {}


def sync_defaults(force: bool = False) -> None:
    """prompts/characters/*.json → таблица characters (источник истины — файлы).
    Дёшево: пока ни один файл не менялся, ничего не делается."""
    files = _files()
    sig = {k: f.stat().st_mtime for k, f in files.items()}
    if not force and sig == _sig:
        return
    now = utcnow()
    seen = set()
    for stem, f in files.items():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            log.error("персонаж %s: некорректный JSON (%s) — пропущен", f.name, e)
            continue
        key = d.get("key") or stem
        seen.add(key)
        seed = d.get("avatar_seed") or make_seed(d["name"], d.get("title", ""), key)
        vals = (d["name"], d.get("title", ""), d.get("description", ""), d.get("prompt", ""), d.get("speech_style", ""),
                d.get("temperature", 0.7), d.get("max_tokens", DEFAULT_MAX_TOKENS), d.get("thinking_mode"), d.get("thinking_depth"),
                json.dumps(d["tools"]) if d.get("tools") is not None else json.dumps(DEFAULT_TOOLS),
                json.dumps(d.get("avatar") or {}), json.dumps(d.get("i18n") or {}, ensure_ascii=False))
        row = db.query_one("SELECT id FROM characters WHERE key=?", (key,))
        if row:
            db.execute("UPDATE characters SET name=?, title=?, description=?, personal_prompt=?, speech_style=?, temperature=?, "
                       "max_tokens=?, thinking_mode=?, thinking_depth=?, tools=?, avatar_params=?, i18n=?, is_default=1, "
                       "updated_at=? WHERE id=?", vals + (now, row["id"]))
        else:
            db.execute("INSERT INTO characters(owner_id, key, is_default, name, title, description, personal_prompt, speech_style, "
                       "temperature, max_tokens, thinking_mode, thinking_depth, tools, avatar_params, i18n, avatar_seed, "
                       "avatar_style, avatar_color, created_at, updated_at) VALUES(NULL,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,'bust',?,?,?)",
                       (key,) + vals + (seed, member_color(seed), now, now))
            log.info("персонаж по умолчанию: %s", d["name"])
    for row in db.query_all("SELECT id, key FROM characters WHERE is_default=1"):
        if row["key"] not in seen:
            db.execute("DELETE FROM characters WHERE id=?", (row["id"],))
    _sig.clear()
    _sig.update(sig)


def seed_defaults(now: str | None = None) -> None:
    sync_defaults(force=True)
