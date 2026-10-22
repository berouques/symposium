"""Shared API dependencies and helpers."""
from __future__ import annotations

from fastapi import HTTPException, Request

from .. import db
from ..auth import get_current_user


def get_symposium_or_404(sid: int, user: dict) -> dict:
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    if sym is None:
        raise HTTPException(404, "Symposium not found")
    if sym["owner_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Not your symposium")
    return sym


def owned_symposium(sid: int, user: dict) -> dict:
    sym = db.query_one("SELECT * FROM symposia WHERE id=?", (sid,))
    if sym is None:
        raise HTTPException(404, "Symposium not found")
    if sym["owner_id"] != user["id"]:
        raise HTTPException(403, "Owner access required")
    return sym


def member_of(symposium_id: int, member_id: int) -> dict:
    m = db.query_one(
        "SELECT * FROM members WHERE id=? AND symposium_id=?", (member_id, symposium_id)
    )
    if m is None:
        raise HTTPException(404, "Member not found")
    return m


def require_body(fields: dict) -> None:
    missing = [k for k, v in fields.items() if v in (None, "", [])]
    if missing:
        raise HTTPException(422, f"Missing required fields: {', '.join(missing)}")
