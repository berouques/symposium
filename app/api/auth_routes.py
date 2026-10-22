"""Auth and user-profile routes."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import db
from ..audit import audit
from ..auth import (
    SESSION_COOKIE, create_session, delete_session, get_current_user,
    hash_password, public_user, utcnow, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: dict, response: Response):
    login_name = (body.get("login") or "").strip()
    password = body.get("password") or ""
    user = db.query_one("SELECT * FROM users WHERE login=?", (login_name,))
    if user is None or not verify_password(password, user["password_hash"]):
        # deliberately vague (spec NG_10 §3.3)
        raise HTTPException(401, "Invalid login or password")
    token = create_session(user["id"])
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=30 * 24 * 3600
    )
    audit(user["id"], "login", "user", user["id"])
    return {"user": public_user(user)}


@router.post("/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(token)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return public_user(user)


# языки интерфейса (v0.3): английский, русский, украинский, схоластическая латынь
UI_LANGUAGES = ("en", "ru", "uk", "la")
# темы оформления (v0.3): от светлого сада до звёздной ночи
UI_THEMES = ("garden", "starlight", "scholastic", "marble", "obsidian",
              "amber", "laurel", "lagoon")


@router.patch("/me")
def update_me(body: dict, user: dict = Depends(get_current_user)):
    updates = {}
    if "name" in body:
        updates["name"] = str(body["name"])[:200]
    if "language" in body:
        if body["language"] not in UI_LANGUAGES:
            raise HTTPException(422, "language must be one of: " + ", ".join(UI_LANGUAGES))
        updates["language"] = body["language"]
    if "about" in body:
        updates["about"] = str(body["about"] or "")[:500]
    if "theme" in body:
        # старые значения (light/dark) мигрируем на новые темы
        theme = body["theme"]
        if theme in ("light", ""):
            theme = "garden"
        elif theme == "dark":
            theme = "starlight"
        if theme not in UI_THEMES:
            raise HTTPException(422, "theme must be one of: " + ", ".join(UI_THEMES))
        updates["theme"] = theme
    if "show_thinking" in body:
        updates["show_thinking"] = 1 if body["show_thinking"] else 0
    if "transcript_prefs" in body:
        updates["transcript_prefs"] = json.dumps(body["transcript_prefs"])
    if "active_profile_id" in body:
        pid = body["active_profile_id"]
        if pid is not None:
            if db.query_one("SELECT 1 FROM api_profiles WHERE id=?", (pid,)) is None:
                raise HTTPException(422, "No such API profile")
        updates["active_profile_id"] = pid
    if updates:
        updates["updated_at"] = utcnow()
        sets = ", ".join(f"{k}=?" for k in updates)
        db.execute(
            f"UPDATE users SET {sets} WHERE id=?",
            list(updates.values()) + [user["id"]],
        )
    fresh = db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))
    audit(user["id"], "update_profile", "user", user["id"], {"fields": list(body.keys())})
    return public_user(fresh)


@router.post("/password")
def change_password(body: dict, user: dict = Depends(get_current_user)):
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    if len(new) < 4:
        raise HTTPException(422, "New password must be at least 4 characters")
    if not verify_password(old, user["password_hash"]):
        raise HTTPException(422, "Current password is incorrect")
    db.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
        (hash_password(new), utcnow(), user["id"]),
    )
    audit(user["id"], "change_password", "user", user["id"])
    return {"ok": True}
