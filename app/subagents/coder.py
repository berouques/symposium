"""coder: sandboxed code execution subagent.

Writes code into the workspace artifacts, executes it in the sandbox
(local subprocess with rlimits/timeout, or the docker workspace), stores
the full log in subagent_logs, and returns a digest (exit code + key
output + artifact path).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..logging_setup import get_logger
from ..workspace import manager as ws
from . import safety
from .base import AgentContext, AgentResult, raw_log_path

log = get_logger("subagents.coder")

# mode="shell": произвольная команда в песочнице (установка пакетов, git, make…).
# timeout (сек) ограничен настройкой sandbox_max_timeout_sec (по умолчанию 300).
ALLOWED_LANGUAGES = {
    "python": ["python3", "-I", "-u"],
    "bash": ["bash"],
    "sh": ["sh"],
}

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _timeout(params: dict) -> int:
    from .. import db
    cap = int(db.get_setting("sandbox_max_timeout_sec", 300) or 300)
    try:
        want = int(params.get("timeout") or ws.SANDBOX_TIMEOUT_SEC)
    except (TypeError, ValueError):
        want = ws.SANDBOX_TIMEOUT_SEC
    return max(1, min(want, cap))


async def run_shell(ctx: AgentContext, params: dict) -> AgentResult:
    command = (params.get("command") or params.get("code") or "").strip()
    if not command:
        return AgentResult(ok=False, error="coder: shell mode needs a command")
    sid = ctx.symposium["id"]
    why = safety.classify(command)
    if why and not await safety.confirm_with_human(ctx, command, why):
        return AgentResult(ok=False, error=f"coder: the human did not allow this command ({why}); it was NOT executed")
    if ws.state_of(sid) != "running":
        if await ws.ensure(sid) != "running":
            return AgentResult(ok=False, error="coder: workspace is not running and could not be started")
    timeout = _timeout(params)
    result = await ws.run_sandbox(sid, ".", ["sh", "-c", command], timeout=timeout)
    raw = f"Command: {command}\nExit code: {result['exit_code']}\nTimed out: {result.get('timed_out')}\n" \
          f"Duration: {result['duration_ms']} ms\n--- stdout ---\n{result['stdout']}\n--- stderr ---\n{result['stderr']}\n"
    raw_path = raw_log_path(ctx, "coder")
    raw_path.write_text(raw, encoding="utf-8")
    status_line = f"exit code {result['exit_code']}"
    if result.get("timed_out"):
        status_line = f"timed out after {timeout}s (killed)"
    digest = (f"coder (shell): `{command[:200]}` — {status_line}, {result['duration_ms']} ms.\n"
              f"stdout:\n{result['stdout'].strip()[:1500]}\nstderr:\n{result['stderr'].strip()[:700]}\n"
              f"Full log: {raw_path}")
    return AgentResult(ok=result["exit_code"] == 0, digest=digest, raw_path=str(raw_path),
                       extra={"exit_code": result["exit_code"]})


async def run(ctx: AgentContext, params: dict) -> AgentResult:
    if str(params.get("mode") or "").lower() == "shell":
        return await run_shell(ctx, params)
    language = (params.get("language") or "python").lower()
    code = params.get("code") or ""
    filename = params.get("filename") or ""
    why = safety.classify(code)
    if why and not await safety.confirm_with_human(ctx, code, why):
        return AgentResult(ok=False, error=f"coder: the human did not allow this code ({why}); it was NOT executed")
    if language not in ALLOWED_LANGUAGES:
        return AgentResult(
            ok=False,
            error=f"coder: unsupported language '{language}' (allowed: python, bash, sh)",
        )
    if not code.strip():
        return AgentResult(ok=False, error="coder: missing 'code' parameter")

    sid = ctx.symposium["id"]
    state = ws.state_of(sid)
    if state != "running":
        state = await ws.ensure(sid)
    if state != "running":
        return AgentResult(ok=False, error="coder: workspace is not running and could not be started")

    if not filename:
        filename = "script.py" if language == "python" else "script.sh"
    filename = Path(filename).name  # no path components
    if not _SAFE_FILENAME.match(filename):
        filename = f"script_{abs(hash(filename)) % 10000}" + (".py" if language == "python" else ".sh")

    rel_dir = f"artifacts/member_{ctx.member['id']}"
    rel_path = f"{rel_dir}/{filename}"
    try:
        ws.save_file(sid, rel_path, code)
    except PermissionError as e:
        return AgentResult(ok=False, error=f"coder: {e}")

    command = ALLOWED_LANGUAGES[language] + [filename]
    result = await ws.run_sandbox(sid, rel_dir, command, timeout=_timeout(params))

    raw = format_raw(rel_path, result)
    raw_path = raw_log_path(ctx, "coder")
    raw_path.write_text(raw, encoding="utf-8")

    status_line = f"exit code {result['exit_code']}"
    if result.get("timed_out"):
        status_line = f"timed out after {_timeout(params)}s (killed)"
    stdout = result["stdout"].strip()
    digest = (
        f"coder: executed {rel_path} in the sandbox — {status_line}, "
        f"{result['duration_ms']} ms.\n"
        f"stdout:\n{stdout[:1200]}\n"
        f"stderr:\n{result['stderr'].strip()[:600]}\n"
        f"Full log: {raw_path}"
    )
    return AgentResult(ok=result["exit_code"] == 0, digest=digest, raw_path=str(raw_path),
                       extra={"exit_code": result["exit_code"]})


def format_raw(rel_path: str, result: dict) -> str:
    return (
        f"Executed: {rel_path}\n"
        f"Exit code: {result['exit_code']}\n"
        f"Timed out: {result.get('timed_out')}\n"
        f"Duration: {result['duration_ms']} ms\n"
        f"--- stdout ---\n{result['stdout']}\n"
        f"--- stderr ---\n{result['stderr']}\n"
    )
