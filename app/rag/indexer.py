"""Sequential RAG indexing queue worker.

Extraction: txt/md (direct), pdf (pypdf), docx (python-docx).
Chunking: character windows with overlap (admin-tunable). Embedding:
local Ollama nomic-embed-text (or the offline test provider).
One file at a time — parallel indexing is forbidden by spec (NG_20 §8.4).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .. import db
from ..logging_setup import get_logger
from . import embeddings, library

log = get_logger("rag.indexer")

_wake: asyncio.Event | None = None
_task: asyncio.Task | None = None
_cancelled: set[int] = set()


def request_cancel(file_id: int) -> None:
    _cancelled.add(file_id)


def _wake_event() -> asyncio.Event:
    global _wake
    if _wake is None:
        _wake = asyncio.Event()
    return _wake


def kick() -> None:
    _wake_event().set()


def start_worker() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_worker(), name="rag-indexer")


async def stop_worker() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None


async def _worker() -> None:
    log.info("RAG indexer worker started")
    while True:
        row = db.query_one(
            "SELECT * FROM rag_files WHERE status='queued' ORDER BY created_at LIMIT 1"
        )
        if row is None:
            _wake_event().clear()
            await _wake_event().wait()
            continue
        if row["id"] in _cancelled:
            _cancelled.discard(row["id"])
            db.execute("UPDATE rag_files SET status='not_indexed' WHERE id=?", (row["id"],))
            continue
        await _index_file(row)
        await asyncio.sleep(0.05)


async def _index_file(row: dict) -> None:
    fid = row["id"]
    db.execute("UPDATE rag_files SET status='indexing', error=NULL WHERE id=?", (fid,))
    try:
        text = extract_text(Path(row["stored_path"]))
        if not text.strip():
            raise ValueError("no extractable text")
        chunks = chunk_text(
            text,
            int(db.get_setting("rag_chunk_chars", "1200")),
            int(db.get_setting("rag_chunk_overlap", "200")),
        )
        db.execute("DELETE FROM rag_chunks WHERE file_id=?", (fid,))
        dims = 0
        for ordinal, chunk in enumerate(chunks):
            if fid in _cancelled:
                db.execute(
                    "UPDATE rag_files SET status='not_indexed', chunks=0 WHERE id=?", (fid,)
                )
                db.execute("DELETE FROM rag_chunks WHERE file_id=?", (fid,))
                _cancelled.discard(fid)
                return
            vec = await embeddings.embed(chunk)
            dims = len(vec)
            db.execute(
                "INSERT INTO rag_chunks(file_id, ordinal, content, vector) VALUES(?,?,?,?)",
                (fid, ordinal, chunk, embeddings.vector_to_blob(vec)),
            )
        db.execute(
            "UPDATE rag_files SET status='done', chunks=?, dims=?, indexed_at=datetime('now') WHERE id=?",
            (len(chunks), dims, fid),
        )
        log.info("indexed file %s (%d chunks)", row["filename"], len(chunks))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.error("indexing failed for %s: %s", row["filename"], e)
        db.execute(
            "UPDATE rag_files SET status='error', error=? WHERE id=?", (str(e)[:500], fid)
        )


# ------------------------------------------------------------- extraction

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".txt", ".md", ".markdown"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages)
    if ext == ".docx":
        import docx
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValueError(f"unsupported extension for indexing: {ext}")


def chunk_text(text: str, chunk_chars: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if chunk_chars <= 0:
        return [text]
    step = max(1, chunk_chars - overlap)
    chunks = []
    start = 0
    while start < len(text):
        piece = text[start:start + chunk_chars]
        if piece.strip():
            chunks.append(piece)
        if start + chunk_chars >= len(text):
            break
        start += step
    return chunks
