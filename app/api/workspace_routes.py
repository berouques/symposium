"""Workspace browser routes (spec NG_10 §7, NG_06 §3.8/3.10)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from .. import db
from ..audit import audit
from ..auth import get_current_user, require_admin
from ..workspace import manager as ws
from .deps import get_symposium_or_404

router = APIRouter(prefix="/api/symposia", tags=["workspace"])


@router.get("/{sid}/workspace")
async def workspace_tree(sid: int, user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    # auto-wake for the owner on visit (NG_06 3.8)
    if user["id"] == _owner(sid):
        state = ws.state_of(sid)
        if state in ("none", "stopped"):
            asyncio.ensure_future(ws.ensure(sid))
            info = ws.state_info(sid)
            info["state"] = "starting"
            info["tree"] = ws.tree(sid)["tree"]
            return info
    return ws.tree(sid)


def _owner(sid: int) -> int | None:
    return db.query_value("SELECT owner_id FROM symposia WHERE id=?", (sid,))


@router.get("/{sid}/workspace/file")
def workspace_file(sid: int, path: str = Query(...), user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    try:
        return ws.read_file(sid, path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    except PermissionError:
        raise HTTPException(403, "Path escapes workspace")


@router.get("/{sid}/workspace/download")
def workspace_download(sid: int, path: str = Query(...), user: dict = Depends(get_current_user)):
    get_symposium_or_404(sid, user)
    try:
        target = ws.safe_path(sid, path)
    except PermissionError:
        raise HTTPException(403, "Path escapes workspace")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    ws.touch(sid)
    return FileResponse(target, filename=target.name)


@router.post("/{sid}/workspace/wake")
async def workspace_wake(sid: int, user: dict = Depends(get_current_user)):
    """Wake up a sleeping/absent workspace (owner or admin)."""
    sym = get_symposium_or_404(sid, user)
    if user["id"] != sym["owner_id"] and not user.get("is_admin"):
        raise HTTPException(403, "Only the owner or an admin can wake this workspace")
    state = await ws.ensure(sid)
    info = ws.state_info(sid)
    audit(user["id"], "workspace_wake", "symposium", sid, {"state": state})
    if state == "error":
        raise HTTPException(
            409,
            {
                "state": state, "reason": info["reason"],
                "message": ws._mode_error(info),
            },
        )
    return {"state": state, "reason": info["reason"]}


@router.post("/{sid}/workspace/stop")
async def workspace_stop(sid: int, user: dict = Depends(require_admin)):
    get_symposium_or_404(sid, user)
    await ws.stop(sid, reason="admin")
    audit(user["id"], "workspace_stop", "symposium", sid)
    return {"state": "stopped", "reason": "admin"}


@router.post("/{sid}/workspace/restart")
async def workspace_restart(sid: int, user: dict = Depends(require_admin)):
    get_symposium_or_404(sid, user)
    state = await ws.restart(sid)
    audit(user["id"], "workspace_restart", "symposium", sid)
    return {"state": state, "reason": ws.state_info(sid)["reason"]}
