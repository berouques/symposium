"""web_explorer: internet search subagent (multi-engine backend).

Engines and configuration are managed by the admin (Google / Bing / Yandex /
SearXNG; DuckDuckGo optional but off by default — it is unavailable in some
regions). Raw results are saved to the workspace; the member receives an LLM
digest in the user's language. Search failures return an error result which
the member must acknowledge in its turn (spec NG_30 §8.6, NG_05 §5).
"""
from __future__ import annotations

from ..logging_setup import get_logger
from ..seeds import active_system_prompt
from . import search as web_search
from .base import AgentContext, AgentResult, digest_via_llm, raw_log_path

log = get_logger("subagents.web")


async def run(ctx: AgentContext, params: dict) -> AgentResult:
    query = (params.get("query") or "").strip()
    if not query:
        return AgentResult(ok=False, error="web_explorer: missing 'query' parameter")
    try:
        results = await web_search.search(query)
    except RuntimeError as e:
        return AgentResult(ok=False, error=f"web search unavailable: {e}")
    except Exception as e:  # noqa: BLE001
        log.warning("web search failed: %s", e)
        return AgentResult(ok=False, error=f"web_explorer failed: web search unavailable ({e})")

    raw = web_search.format_raw(results)
    path = raw_log_path(ctx, "web_explorer")
    path.write_text(raw, encoding="utf-8")

    if not results:
        return AgentResult(
            ok=True,
            digest="Web search returned no results for this query. State this explicitly "
            "and proceed without web facts.",
            raw_path=str(path),
        )

    digest = raw[:1500]
    tin = tout = 0
    try:
        instruction = active_system_prompt("subagent_web_explorer")
        digest, tin, tout = await digest_via_llm(ctx, instruction, raw)
    except Exception as e:  # noqa: BLE001
        log.warning("web digest LLM failed, using raw snippets: %s", e)
    return AgentResult(ok=True, digest=digest, raw_path=str(path), tokens_in=tin, tokens_out=tout)
