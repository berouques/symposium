"""archivarius: RAG search subagent.

Searches only the folders attached to the symposium. Distinguishes the
states 'no attached sources' and 'no results' (spec NG_00 §12.7).
Sources are cited as folder / file / fragment.
"""
from __future__ import annotations

from ..logging_setup import get_logger
from ..rag import embeddings, library
from ..seeds import active_system_prompt
from .base import AgentContext, AgentResult, digest_via_llm, raw_log_path

log = get_logger("subagents.archivarius")


async def run(ctx: AgentContext, params: dict) -> AgentResult:
    query = (params.get("query") or "").strip()
    if not query:
        return AgentResult(ok=False, error="archivarius: missing 'query' parameter")
    sid = ctx.symposium["id"]
    # папки симпозиума + ЛИЧНЫЕ папки этого участника (другим они недоступны)
    own = []
    try:
        import json as _json
        own = [int(x) for x in _json.loads(ctx.member.get("rag_folder_ids") or "[]")]
    except (ValueError, TypeError):
        own = []
    # "no sources" is decided before any embedding work (spec NG_00 §12.7)
    if not library.attached_folder_ids(sid) and not own:
        return AgentResult(
            ok=True,
            digest="archivarius: no RAG folders are attached to this symposium. "
                   "There are no available sources to search.",
            raw_path=str(raw_log_path(ctx, "archivarius")),
        )
    try:
        qvec = await embeddings.embed(query)
    except embeddings.EmbeddingError as e:
        return AgentResult(ok=False, error=f"archivarius failed: {e}")

    found = library.search_with_vector(sid, qvec, top_k=6, extra_folder_ids=own)
    raw_path = raw_log_path(ctx, "archivarius")

    if found["status"] == "no_sources":
        return AgentResult(
            ok=True,
            digest="archivarius: no RAG folders are attached to this symposium. "
                   "There are no available sources to search.",
            raw_path=str(raw_path),
        )
    if found["status"] == "no_results":
        raw_path.write_text(f"Query: {query}\nNo matching fragments.", encoding="utf-8")
        return AgentResult(
            ok=True,
            digest="archivarius: no results in the attached RAG folders for this query.",
            raw_path=str(raw_path),
        )

    raw = format_raw(query, found["hits"])
    raw_path.write_text(raw, encoding="utf-8")

    digest = raw[:1600]
    tin = tout = 0
    try:
        instruction = active_system_prompt("subagent_archivarius")
        digest, tin, tout = await digest_via_llm(ctx, instruction, raw)
    except Exception as e:  # noqa: BLE001
        log.warning("archivarius digest LLM failed, using raw excerpts: %s", e)
    return AgentResult(ok=True, digest=digest, raw_path=str(raw_path), tokens_in=tin, tokens_out=tout)


def format_raw(query: str, hits: list[dict]) -> str:
    lines = [f"RAG search results for: {query}"]
    for h in hits:
        lines.append(
            f"- [{h['folder']} / {h['file']} / {h['fragment']}] (score {h['score']}):\n"
            f"  {h['content'][:600]}"
        )
    return "\n".join(lines)
