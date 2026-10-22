"""RAG embeddings: local Ollama (nomic-embed-text) with a deterministic
offline fallback provider used for tests.

Per the deployment brief, the primary provider is local Ollama with
nomic-embed-text. If Ollama is unreachable, indexing reports an explicit
error (spec NG_50 §18.4: fail loudly, do not degrade silently). The
`fake` provider is admin-selectable for offline demos and tests.
"""
from __future__ import annotations

import hashlib
import struct
import urllib.parse

import httpx

from .. import db
from ..logging_setup import get_logger

log = get_logger("rag.embeddings")


class EmbeddingError(Exception):
    pass


def _provider() -> str:
    return db.get_setting("embedding_provider", "ollama")


def _model() -> str:
    return db.get_setting("embedding_model", "nomic-embed-text")


def _dims() -> int:
    return int(db.get_setting("embedding_dims", "768"))


def _ollama_url() -> str:
    from ..config import get_config
    # настройка из админ-панели перекрывает .env
    return (db.get_setting("ollama_url", "") or "").strip() or get_config().ollama_url


async def embed(text: str) -> list[float]:
    """Embed one text chunk; raises EmbeddingError on failure."""
    provider = _provider()
    if provider == "fake":
        return _fake_embed(text)
    return await _ollama_embed(text)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    return [await embed(t) for t in texts]  # sequential: local CPU, small batches


def _fake_embed(text: str) -> list[float]:
    dims = min(64, _dims())  # tiny deterministic vectors for fast tests
    vec = []
    for i in range(dims):
        h = hashlib.blake2b(f"{i}:{text}".encode(), digest_size=8).digest()
        val = struct.unpack(">q", h)[0] / 2**62
        vec.append(val)
    return vec


async def _ollama_embed(text: str) -> list[float]:
    url = _ollama_url().rstrip("/")
    payloads = [
        ("legacy", {"model": _model(), "prompt": text}),
        ("modern", {"model": _model(), "input": [text]}),
    ]
    last_error = ""
    for variant, payload in payloads:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{url}/api/embeddings" if variant == "legacy" else f"{url}/api/embed", json=payload)
                if resp.status_code != 200:
                    last_error = f"ollama HTTP {resp.status_code}"
                    continue
                data = resp.json()
                if "embedding" in data:
                    return [float(x) for x in data["embedding"]]
                if "embeddings" in data and data["embeddings"]:
                    return [float(x) for x in data["embeddings"][0]]
                last_error = "unexpected ollama response shape"
        except Exception as e:  # noqa: BLE001
            last_error = f"{type(e).__name__}: {e}"
    raise EmbeddingError(f"Ollama embedding failed ({last_error}); check that Ollama is running and model '{_model()}' is pulled")


async def provider_status() -> dict:
    provider = _provider()
    if provider == "fake":
        return {"provider": provider, "ok": True, "detail": "offline test provider"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_ollama_url().rstrip('/')}/api/tags")
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                has = any(_model() in m for m in models)
                return {
                    "provider": "ollama",
                    "ok": True,
                    "detail": "Ollama reachable",
                    "model": _model(),
                    "model_available": has,
                    "models": models[:20],
                }
            return {"provider": "ollama", "ok": False, "detail": f"HTTP {resp.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"provider": "ollama", "ok": False, "detail": f"unreachable: {e}"}


# ------------------------------------------------------------- vector math

def vector_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def blob_to_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)
