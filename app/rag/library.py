"""RAG library: virtual folders, files, attachments, search."""
from __future__ import annotations

import math
from pathlib import Path

from .. import db
from ..auth import utcnow
from ..config import get_config
from ..logging_setup import get_logger
from . import embeddings

log = get_logger("rag.library")

INDEXABLE_EXT = {".txt", ".md", ".pdf", ".docx"}


def folder_list() -> list[dict]:
    rows = db.query_all(
        "SELECT f.*, "
        "(SELECT COUNT(*) FROM rag_files rf WHERE rf.folder_id=f.id) AS file_count, "
        "(SELECT COALESCE(SUM(rf.size),0) FROM rag_files rf WHERE rf.folder_id=f.id) AS total_size "
        "FROM rag_folders f ORDER BY f.name"
    )
    return rows


def folder_create(name: str, description: str, user_id: int) -> dict:
    fid = db.execute(
        "INSERT INTO rag_folders(name, description, created_by, created_at) VALUES(?,?,?,?)",
        (name.strip(), description.strip(), user_id, utcnow()),
    )
    return db.query_one("SELECT * FROM rag_folders WHERE id=?", (fid,))


def folder_rename(fid: int, name: str, description: str) -> None:
    db.execute("UPDATE rag_folders SET name=?, description=? WHERE id=?", (name.strip(), description.strip(), fid))


def folder_delete(fid: int) -> None:
    # cascade removes files, chunks, attachments
    db.execute("DELETE FROM rag_folders WHERE id=?", (fid,))


def file_list(folder_id: int | None = None) -> list[dict]:
    if folder_id:
        return db.query_all("SELECT * FROM rag_files WHERE folder_id=? ORDER BY created_at DESC", (folder_id,))
    return db.query_all("SELECT * FROM rag_files ORDER BY created_at DESC")


def file_register(folder_id: int, filename: str, stored_path: str, size: int, user_id: int) -> dict:
    ext = Path(filename).suffix.lower()
    status = "queued" if ext in INDEXABLE_EXT else "not_indexed"
    fid = db.execute(
        "INSERT INTO rag_files(folder_id, filename, stored_path, size, status, created_by, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (folder_id, filename, stored_path, size, status, user_id, utcnow()),
    )
    return db.query_one("SELECT * FROM rag_files WHERE id=?", (fid,))


def file_delete(fid: int) -> None:
    row = db.query_one("SELECT stored_path FROM rag_files WHERE id=?", (fid,))
    if row and row["stored_path"]:
        try:
            Path(row["stored_path"]).unlink(missing_ok=True)
        except OSError:
            log.warning("could not delete file %s", row["stored_path"])
    db.execute("DELETE FROM rag_files WHERE id=?", (fid,))


def file_cancel_indexing(fid: int) -> None:
    """Cancel a queued/failed file: keep it in the library as not_indexed."""
    db.execute("UPDATE rag_files SET status='not_indexed', error=NULL WHERE id=? AND status IN ('queued','error')", (fid,))


def file_reindex(fid: int) -> None:
    db.execute("UPDATE rag_files SET status='queued', error=NULL WHERE id=?", (fid,))


def attach(symposium_id: int, folder_id: int) -> None:
    if db.query_one("SELECT 1 FROM rag_attachments WHERE symposium_id=? AND folder_id=?", (symposium_id, folder_id)):
        return
    db.execute(
        "INSERT INTO rag_attachments(symposium_id, folder_id, attached_at) VALUES(?,?,?)",
        (symposium_id, folder_id, utcnow()),
    )


def detach(symposium_id: int, folder_id: int) -> None:
    db.execute("DELETE FROM rag_attachments WHERE symposium_id=? AND folder_id=?", (symposium_id, folder_id))


def attached_folders(symposium_id: int) -> list[dict]:
    return db.query_all(
        "SELECT f.* FROM rag_attachments a JOIN rag_folders f ON f.id=a.folder_id "
        "WHERE a.symposium_id=? ORDER BY f.name",
        (symposium_id,),
    )


def attached_folder_ids(symposium_id: int) -> list[int]:
    return [r["folder_id"] for r in db.query_all(
        "SELECT folder_id FROM rag_attachments WHERE symposium_id=?", (symposium_id,)
    )]


# ------------------------------------------------------------------- search

def search_with_vector(symposium_id: int, query_vector: list[float], top_k: int = 6,
                       extra_folder_ids: list[int] | None = None) -> dict:
    folder_ids = list(dict.fromkeys(attached_folder_ids(symposium_id) + list(extra_folder_ids or [])))
    if not folder_ids:
        return {"status": "no_sources", "hits": []}
    placeholders = ",".join("?" for _ in folder_ids)
    rows = db.query_all(
        f"SELECT c.id, c.file_id, c.ordinal, c.content, c.vector, rf.filename, rf.folder_id "
        f"FROM rag_chunks c JOIN rag_files rf ON rf.id=c.file_id "
        f"WHERE rf.status='done' AND rf.folder_id IN ({placeholders})",
        folder_ids,
    )
    if not rows:
        return {"status": "no_results", "hits": []}
    scored = []
    for r in rows:
        if not r["vector"]:
            continue
        vec = embeddings.blob_to_vector(r["vector"])
        if len(vec) != len(query_vector):
            continue
        score = embeddings.cosine(query_vector, vec)
        if score > 0.05 and not math.isnan(score):
            scored.append((score, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    hits = []
    for score, r in scored[:top_k]:
        folder = db.query_one("SELECT name FROM rag_folders WHERE id=?", (r["folder_id"],))
        hits.append({
            "score": round(score, 4),
            "folder": folder["name"] if folder else "?",
            "file": r["filename"],
            "fragment": f"chunk #{r['ordinal'] + 1}",
            "content": r["content"],
        })
    if not hits:
        return {"status": "no_results", "hits": []}
    return {"status": "ok", "hits": hits}
