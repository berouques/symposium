"""RAG library routes (shared library, all authenticated users)."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db
from ..audit import audit
from ..auth import get_current_user, require_admin, utcnow
from ..config import get_config
from ..logging_setup import get_logger
from ..rag import embeddings, indexer, library

router = APIRouter(prefix="/api/rag", tags=["rag"])
log = get_logger("api.rag")


@router.get("/status")
async def rag_status(user: dict = Depends(get_current_user)):
    prov = await embeddings.provider_status()
    queue = db.query_all(
        "SELECT id, filename, status, error FROM rag_files WHERE status IN ('queued','indexing') "
        "ORDER BY created_at"
    )
    return {"provider": prov, "queue": queue}


@router.get("/folders")
def folders(user: dict = Depends(get_current_user)):
    return library.folder_list()


@router.post("/folders")
def create_folder(body: dict, user: dict = Depends(get_current_user)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Folder name is required")
    if db.query_one("SELECT 1 FROM rag_folders WHERE name=?", (name,)):
        raise HTTPException(422, f"A folder named '{name}' already exists")
    folder = library.folder_create(name, body.get("description") or "", user["id"])
    audit(user["id"], "rag_folder_create", "rag_folder", folder["id"], {"name": name})
    return folder


@router.patch("/folders/{fid}")
def rename_folder(fid: int, body: dict, user: dict = Depends(get_current_user)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Folder name is required")
    dup = db.query_one("SELECT 1 FROM rag_folders WHERE name=? AND id<>?", (name, fid))
    if dup:
        raise HTTPException(422, f"A folder named '{name}' already exists")
    library.folder_rename(fid, name, body.get("description") or "")
    audit(user["id"], "rag_folder_rename", "rag_folder", fid, {"name": name})
    return {"ok": True}


@router.delete("/folders/{fid}")
def delete_folder(fid: int, user: dict = Depends(get_current_user)):
    folder = db.query_one("SELECT * FROM rag_folders WHERE id=?", (fid,))
    if folder is None:
        raise HTTPException(404, "Folder not found")
    library.folder_delete(fid)
    audit(user["id"], "rag_folder_delete", "rag_folder", fid, {"name": folder["name"]})
    return {"ok": True}


@router.get("/folders/{fid}/files")
def folder_files(fid: int, user: dict = Depends(get_current_user)):
    if db.query_one("SELECT 1 FROM rag_folders WHERE id=?", (fid,)) is None:
        raise HTTPException(404, "Folder not found")
    return library.file_list(fid)


@router.post("/folders/{fid}/files")
async def upload_files(fid: int, request: Request, user: dict = Depends(get_current_user)):
    folder = db.query_one("SELECT * FROM rag_folders WHERE id=?", (fid,))
    if folder is None:
        raise HTTPException(404, "Folder not found")
    max_size = int(db.get_setting("max_file_size_bytes", 50 * 1024 * 1024))
    form = await request.form()
    files = form.getlist("files") or ([form["file"]] if "file" in form else [])
    if not files:
        raise HTTPException(422, "No files uploaded")
    saved = []
    for f in files:
        data = await f.read()
        if len(data) > max_size:
            raise HTTPException(
                422, f"File {f.filename} exceeds the {max_size // (1024*1024)} MB limit"
            )
        name = Path(f.filename or "file.bin").name
        stored = get_config().rag_files_dir / f"{uuid.uuid4().hex}_{name}"
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(data)
        row = library.file_register(fid, name, str(stored), len(data), user["id"])
        saved.append(row)
    indexer.kick()
    audit(user["id"], "rag_file_upload", "rag_folder", fid, {"files": [s["filename"] for s in saved]})
    return {"files": saved}


@router.delete("/files/{fid}")
def delete_file(fid: int, user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT * FROM rag_files WHERE id=?", (fid,))
    if row is None:
        raise HTTPException(404, "File not found")
    if row["status"] in ("queued", "indexing"):
        indexer.request_cancel(fid)
    library.file_delete(fid)
    audit(user["id"], "rag_file_delete", "rag_file", fid, {"file": row["filename"]})
    return {"ok": True}


@router.post("/files/{fid}/cancel")
def cancel_file(fid: int, body: dict, user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT * FROM rag_files WHERE id=?", (fid,))
    if row is None:
        raise HTTPException(404, "File not found")
    remove = bool(body.get("remove", False))
    if row["status"] in ("queued", "indexing"):
        indexer.request_cancel(fid)
    if remove:
        library.file_delete(fid)
        audit(user["id"], "rag_file_delete", "rag_file", fid, {"file": row["filename"], "reason": "cancelled"})
    else:
        library.file_cancel_indexing(fid)
        audit(user["id"], "rag_index_cancel", "rag_file", fid, {"file": row["filename"]})
    return {"ok": True, "removed": remove}


@router.post("/files/{fid}/reindex")
def reindex_file(fid: int, user: dict = Depends(require_admin)):
    row = db.query_one("SELECT * FROM rag_files WHERE id=?", (fid,))
    if row is None:
        raise HTTPException(404, "File not found")
    library.file_reindex(fid)
    indexer.kick()
    audit(user["id"], "rag_reindex", "rag_file", fid, {"file": row["filename"]})
    return {"ok": True}


@router.get("/files/{fid}/preview")
def file_preview(fid: int, user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT * FROM rag_files WHERE id=?", (fid,))
    if row is None:
        raise HTTPException(404, "File not found")
    chunks = db.query_all(
        "SELECT ordinal, content FROM rag_chunks WHERE file_id=? ORDER BY ordinal LIMIT 20",
        (fid,),
    )
    return {"file": row, "chunks": chunks}


# ------------------------------------------------------------------ admin

@router.post("/reindex-all")
def reindex_all(user: dict = Depends(require_admin)):
    db.execute("UPDATE rag_files SET status='queued', error=NULL WHERE status IN ('done','error','not_indexed')")
    indexer.kick()
    audit(user["id"], "rag_reindex_all", "rag_library", "")
    return {"ok": True}


@router.post("/clear")
def clear_library(user: dict = Depends(require_admin)):
    folders = db.query_all("SELECT id, name FROM rag_folders")
    for f in folders:
        library.folder_delete(f["id"])
    audit(user["id"], "rag_clear", "rag_library", "", {"folders": [f["name"] for f in folders]})
    return {"ok": True}
