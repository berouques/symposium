"""Authentication: password hashing, sessions, FastAPI dependencies."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request

from . import db

SESSION_COOKIE = "symposium_session"
SESSION_DAYS = 30


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, hexdigest = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return hmac.compare_digest(digest.hex(), hexdigest)


# --------------------------------------------------------------- sessions

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?,?,?,?)",
        (token, user_id, utcnow(), expires),
    )
    return token


def delete_session(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token=?", (token,))


def user_for_token(token: str) -> dict | None:
    if not token:
        return None
    row = db.query_one(
        "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
        (token,),
    )
    if row is None:
        return None
    if row["expires_at"] < utcnow():
        delete_session(token)
        return None
    return row


# ------------------------------------------------------------ dependencies

def get_current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    user = user_for_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "login": u["login"],
        "name": u["name"],
        "language": u["language"],
        "about": u.get("about", ""),
        "theme": u["theme"],
        "is_admin": bool(u["is_admin"]),
        "show_thinking": bool(u["show_thinking"]),
        "transcript_prefs": u["transcript_prefs"],
        "active_profile_id": u["active_profile_id"],
    }
