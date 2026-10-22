"""Subagent framework.

Subagents are isolated executors invoked by participants via the <call> blocks
protocol. Participants receive only a digest; raw outputs are persisted in
the workspace (subagent_logs/) and never enter the member context
(spec NG_00 §6.4). Every call is recorded in subagent_calls for audit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from ..auth import utcnow
from ..llm import client as llm_client
from ..llm.client import LLMPersistentError
from ..logging_setup import get_logger
from ..workspace import manager as ws

log = get_logger("subagents")

AGENTS = ("web_explorer", "web_fetch", "archivarius", "coder", "ask_human")


@dataclass
class AgentContext:
    symposium: dict
    member: dict
    round_id: int | None
    profile: dict | None  # active LLM API profile of the symposium
    language: str = "ru"


@dataclass
class AgentResult:
    ok: bool
    digest: str = ""
    error: str = ""
    raw_path: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    hitl: dict | None = None  # only for ask_human
    extra: dict = field(default_factory=dict)


async def digest_via_llm(ctx: AgentContext, instruction: str, payload: str) -> tuple[str, int, int]:
    """Сжатие сырых данных в короткий дайджест на языке общения.

    Язык приходит ЗДЕСЬ (user-роль), а не в системный промпт — так же,
    как у участников и секретаря (v0.3)."""
    if not ctx.profile:
        raise LLMPersistentError("no LLM profile configured", "no_profile")
    from ..engine.context import language_name
    system = instruction.replace("{user_language}", ctx.language)
    lang_line = (f"Language of your output: {language_name(ctx.language)}. "
                 f"Write the digest in {language_name(ctx.language)}.")
    result = await llm_client.chat(
        ctx.profile,
        system=system,
        messages=[{"role": "user", "content": lang_line + "\n\n" + payload[:20000]}],
        temperature=0.2,
        max_tokens=int(db.get_setting("subagent_digest_tokens", 900) or 900),
        call_type="subagent",
        symposium_id=ctx.symposium["id"],
        member_id=ctx.member["id"],
        round_id=ctx.round_id,
    )
    text = result.content.strip()
    return text.strip() or "(empty digest)", result.tokens_in, result.tokens_out


def raw_log_path(ctx: AgentContext, agent: str) -> Path:
    root = ws.ws_root(ctx.symposium["id"]) / "subagent_logs"
    root.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().replace(":", "").replace("-", "")
    return root / f"{stamp}_{agent}_{ctx.member['id']}.log"


def record_call(ctx: AgentContext, agent: str, params: dict, result: AgentResult) -> None:
    db.execute(
        "INSERT INTO subagent_calls(symposium_id, round_id, member_id, agent, params, status, "
        "result, error, raw_path, tokens_in, tokens_out, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ctx.symposium["id"], ctx.round_id, ctx.member["id"], agent,
            json.dumps(params, ensure_ascii=False)[:4000],
            "ok" if result.ok else "error",
            (result.digest or result.error)[:4000],
            result.error[:1000] if not result.ok else None,
            result.raw_path,
            result.tokens_in, result.tokens_out, utcnow(),
        ),
    )


async def run(agent: str, ctx: AgentContext, params: dict) -> AgentResult:
    from . import web_explorer, web_fetch, archivarius, coder
    if agent == "web_explorer":
        result = await web_explorer.run(ctx, params)
    elif agent == "web_fetch":
        result = await web_fetch.run(ctx, params)
    elif agent == "archivarius":
        result = await archivarius.run(ctx, params)
    elif agent == "coder":
        result = await coder.run(ctx, params)
    else:
        result = AgentResult(ok=False, error=f"unknown agent {agent}")
    record_call(ctx, agent, params, result)
    return result
