"""LLM client: AsyncOpenAI (OpenRouter-compatible) + fake mode + retries.

Every call is rate-limited through ProfileGate and recorded in token_usage.
Transient provider errors (429 / 5xx / timeouts) are retried with backoff
per spec NG_00 §17.3; persistent failures raise LLMPersistentError which the
engine turns into an error pause.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from .. import db
from ..auth import utcnow
from ..logging_setup import get_logger
from . import fake
from . import reasoning
from . import thinking as thinking_mod
from . import limits as limits_mod
from .rate import QuotaExhausted, rate_limiter

log = get_logger("llm")

MAX_ATTEMPTS = 4          # значения по умолчанию; реально — из политики профиля
BACKOFF_BASE = 5.0

_clients: dict[int, AsyncOpenAI] = {}

# profiles whose provider rejected the extra_body payload with 4xx — stop
# sending it instead of failing every call (in-memory, resets on restart)
_extra_rejected: set[int] = set()
# profiles whose provider rejected the THINKING fields specifically — the
# profile extra_body stays, thinking is no longer mapped onto the request
_thinking_rejected: set[int] = set()


class LLMPersistentError(Exception):
    def __init__(self, message: str, kind: str = "llm_error"):
        super().__init__(message)
        self.kind = kind


class StreamResult:
    def __init__(self) -> None:
        self.content = ""
        self.thinking = ""
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_thinking = 0     # из usage провайдера; иначе оценка по длине рассуждений
        self.model = ""
        # "stop" | "length" | None (not reported by provider)
        self.finish_reason: str | None = None
        # chars of leaked reasoning removed from the content stream
        self.stripped_reasoning = 0


def _profile_extra_dict(profile: dict) -> dict:
    """Profile-level extra fields (e.g. Zhipu thinking off) as a plain dict.
    Empty when unset/invalid/rejected."""
    if profile["id"] in _extra_rejected:
        return {}
    raw = profile.get("extra_body")
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("profile %s: extra_body is not valid JSON — ignored", profile["id"])
        return {}
    return dict(data) if isinstance(data, dict) and data else {}


def _merged_extra(profile: dict, mode: str | None, depth: str | None) -> dict:
    """extra_body for one call: profile fields + the thinking layer on top.

    The thinking layer OVERRIDES profile-level 'thinking'-ish keys, so a
    symposium that switched thinking on beats the profile preset that
    switched it off. When the provider rejected the thinking fields
    earlier, only the profile fields remain."""
    extra = _profile_extra_dict(profile)
    if mode and mode != "default" and profile["id"] not in _thinking_rejected:
        fields, _, _ = thinking_mod.request_params(profile, mode, depth, 0)
        extra.update(fields)
    return extra


def _request_max_tokens(profile: dict, mode: str | None, depth: str | None, reply_max_tokens: int) -> int:
    """Request limit: the reply budget plus the thinking allowance when the
    provider caps thinking+reply together (see llm/thinking.py)."""
    _, request_max, _ = thinking_mod.request_params(profile, mode or "default", depth, reply_max_tokens)
    return request_max


def _get_client(profile: dict) -> AsyncOpenAI:
    pid = profile["id"]
    client = _clients.get(pid)
    if client is None:
        client = AsyncOpenAI(
            base_url=profile["base_url"].rstrip("/"),
            api_key=profile["api_key"] or "none",
            timeout=180.0,
            max_retries=0,  # we handle retries ourselves
        )
        _clients[pid] = client
    return client


def invalidate_client(profile_id: int) -> None:
    _clients.pop(profile_id, None)


def is_fake(profile: dict) -> bool:
    return profile["model"].startswith("fake:")


async def chat(
    profile: dict,
    *,
    system: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 1024,
    call_type: str = "turn",
    symposium_id: int | None = None,
    member_id: int | None = None,
    round_id: int | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_thinking_delta: Callable[[str], None] | None = None,
    thinking_mode: str | None = None,
    thinking_depth: str | None = None,
) -> StreamResult:
    """One rate-limited, usage-recorded, (possibly streamed) completion.

    thinking_mode/thinking_depth: resolved per-member reasoning control
    ('default'/'off'/'on' + depth); mapped onto provider fields on top of the
    profile's own extra_body (see llm/thinking.py)."""
    gate = rate_limiter.gate_for(profile)
    retry = gate.policy["retry"]
    attempts, base, cap = retry["attempts"], retry["base_s"], retry["max_s"]
    started = time.monotonic()
    last_error: Exception | None = None
    est = (len(system) + sum(len(m["content"]) for m in messages)) // 4 + max_tokens

    async def _sleep_backoff(attempt: int, e: Exception) -> None:
        wait = min(cap, base * (2 ** (attempt - 1)))
        hinted = _retry_after_seconds(e)
        if hinted is not None and gate.policy["respect_retry_after"]:
            gate.note_retry_after(hinted)
            wait = min(max(wait, hinted), max(cap, hinted))
        log.warning("transient LLM error (attempt %d/%d): %s; waiting %.0fs",
                    attempt, attempts, _brief_error(e), wait)
        await asyncio.sleep(wait)

    for attempt in range(1, attempts + 1):
        try:
            async with gate.slot(est) as entry:
                result = await _chat_once(
                    profile, system, messages, temperature, max_tokens,
                    on_delta, on_thinking_delta, thinking_mode, thinking_depth,
                )
                gate.record_tokens(entry, result.tokens_in + result.tokens_out)
            _record_usage(profile, result, call_type, symposium_id, member_id, round_id, started)
            return result
        except QuotaExhausted as e:
            raise LLMPersistentError(str(e), "quota_exhausted") from e
        except fake.FakeLLMError as e:
            # simulated failures are never retried (they test error handling)
            raise LLMPersistentError(str(e), "llm_error") from e
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            last_error = e
            if attempt < attempts:
                await _sleep_backoff(attempt, e)
        except APIStatusError as e:
            if _looks_like_extra_body_error(e):
                # retry ladder: (1) drop the thinking layer only, (2) drop the
                # whole extra_body, (3) give up as a persistent error
                if profile["id"] not in _thinking_rejected:
                    _thinking_rejected.add(profile["id"])
                    log.warning("profile %s: provider rejected the thinking fields (%s); "
                                "retrying with the profile extra_body only", profile["id"], _brief_error(e))
                    continue
                if profile["id"] not in _extra_rejected:
                    _extra_rejected.add(profile["id"])
                    log.warning("profile %s: provider rejected extra_body (%s); retrying without it",
                                profile["id"], _brief_error(e))
                    continue
                raise LLMPersistentError(f"provider error {e.status_code}: {e.message}", "llm_http") from e
            if e.status_code and e.status_code >= 500:
                last_error = e
                if attempt < attempts:
                    await _sleep_backoff(attempt, e)
            elif e.status_code == 402:
                raise LLMPersistentError(
                    f"provider error 402 (payment required / negative balance): {_brief_error(e)}",
                    "payment_required") from e
            else:
                raise LLMPersistentError(f"provider error {e.status_code}: {e.message}", "llm_http") from e

    raise LLMPersistentError(
        f"LLM call failed after {attempts} attempts: {last_error}", "llm_unavailable"
    )


def _retry_after_seconds(e: Exception) -> float | None:
    """Пауза из ответа провайдера: Retry-After (сек/дата) или X-RateLimit-Reset
    (эпоха в мс/с — так отвечает OpenRouter). None — заголовков нет."""
    headers = getattr(getattr(e, "response", None), "headers", None)
    if not headers:
        return None
    ra = headers.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                return max(0.0, (parsedate_to_datetime(ra) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    reset = headers.get("x-ratelimit-reset")
    if reset:
        try:
            val = float(reset)
            now = time.time()
            if val > 1e11:      # миллисекунды эпохи
                val /= 1000.0
            if val > 1e9:       # секунды эпохи
                return max(0.0, val - now)
            return max(0.0, val)
        except ValueError:
            pass
    return None


async def _chat_once(
    profile: dict,
    system: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    on_delta: Callable[[str], None] | None,
    on_thinking_delta: Callable[[str], None] | None,
    thinking_mode: str | None = None,
    thinking_depth: str | None = None,
) -> StreamResult:
    result = StreamResult()

    if is_fake(profile):
        content = fake.fake_completion(system, messages, profile["model"])
        for i in range(0, len(content), 24):
            piece = content[i:i + 24]
            result.content += piece
            if on_delta:
                on_delta(piece)
            await asyncio.sleep(0.004)
        result.model = profile["model"]
        result.finish_reason = fake.fake_finish_reason(profile["model"], system, messages)
        result.tokens_in = max(1, (len(system) + sum(len(m["content"]) for m in messages)) // 4)
        result.tokens_out = max(1, len(content) // 4)
        return result

    client = _get_client(profile)
    extra = _merged_extra(profile, thinking_mode, thinking_depth)
    request_max = _request_max_tokens(profile, thinking_mode, thinking_depth, max_tokens)
    if thinking_mode and thinking_mode != "default":
        _, _, note = thinking_mod.request_params(
            profile, thinking_mode, thinking_depth, max_tokens
        )
        if note:
            log.info("profile %s: thinking %s/%s — %s", profile["id"], thinking_mode,
                     thinking_depth, note)
    stream = await client.chat.completions.create(
        model=profile["model"],
        messages=[{"role": "system", "content": system}, *messages],
        temperature=temperature,
        max_tokens=request_max,
        stream=True,
        stream_options={"include_usage": True},
        **({"extra_body": extra} if extra else {}),
    )
    async for event in stream:
        if event.usage:
            result.tokens_in = event.usage.prompt_tokens or 0
            result.tokens_out = event.usage.completion_tokens or 0
            det = getattr(event.usage, "completion_tokens_details", None)
            rt = getattr(det, "reasoning_tokens", None) if det is not None else None
            if rt:
                result.tokens_thinking = int(rt)
        if not event.choices:
            continue
        choice = event.choices[0]
        if choice.finish_reason:
            result.finish_reason = choice.finish_reason
        delta = choice.delta
        if delta is None:
            continue
        piece = delta.content or ""
        if piece:
            result.content += piece
            if on_delta:
                on_delta(piece)
        reasoning_delta = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
        if not reasoning_delta:
            # the typed SDK may keep provider extras (reasoning_content) in
            # model_extra rather than as an attribute
            extra = getattr(delta, "model_extra", None)
            if isinstance(extra, dict):
                reasoning_delta = extra.get("reasoning_content") or extra.get("reasoning")
        if reasoning_delta:
            result.thinking += reasoning_delta
            if on_thinking_delta:
                on_thinking_delta(reasoning_delta)
    result.model = profile["model"]
    # -- leak guard: some providers emit chain-of-thought inside the content
    # stream (interleaved thinking). Strip it so the marker protocol and the
    # token budget apply to the PUBLIC reply only. The removed text is kept
    # as `thinking` (visible in the UI thinking pane, exported on request).
    clean, leaked = reasoning.strip_reasoning(result.content)
    if leaked:
        result.stripped_reasoning = len(leaked)
        if result.thinking:
            result.thinking = result.thinking + "\n" + leaked
        else:
            result.thinking = leaked
        log.warning(
            "profile %s: stripped %d chars of leaked reasoning from the reply",
            profile["id"], result.stripped_reasoning,
        )
        result.content = clean
    if not result.tokens_out:
        result.tokens_out = max(1, (len(result.content) + len(result.thinking)) // 4)
    if not result.tokens_thinking and result.thinking:
        result.tokens_thinking = min(result.tokens_out, max(1, len(result.thinking) // 3))
    return result


def _looks_like_extra_body_error(e: Exception) -> bool:
    """4xx that points at unknown/invalid request FIELDS (not auth/rate)."""
    status = getattr(e, "status_code", None)
    if status is None or status not in (400, 422):
        return False
    text = _brief_error(e).lower()
    return any(k in text for k in (
        "unknown parameter", "unknown field", "unexpected", "not supported",
        "invalid parameter", "extra fields", "thinking", "reasoning",
        "not allowed", "unrecognized",
    ))


def _brief_error(e: Exception) -> str:
    body = getattr(e, "message", None) or str(e)
    return str(body)[:400]


def _record_usage(
    profile: dict, result: StreamResult, call_type: str,
    symposium_id: int | None, member_id: int | None, round_id: int | None,
    started: float,
) -> None:
    latency = int((time.monotonic() - started) * 1000)
    db.execute(
        "INSERT INTO token_usage(symposium_id, member_id, round_id, call_type, profile_id, "
        "model, tokens_in, tokens_out, latency_ms, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            symposium_id, member_id, round_id, call_type, profile["id"],
            result.model, result.tokens_in, result.tokens_out, latency, utcnow(),
        ),
    )


async def test_connection(profile: dict) -> dict:
    """Admin 'test connection' probe: one tiny completion.

    Deliberately fast-fail: a dedicated client with a short timeout and NO
    retry loop, so the admin gets a verdict in seconds even for a wrong
    base_url or key (the regular chat() path can take minutes retrying).
    Still passes through the rate gate to honour the no-parallel-requests
    contract of the provider.
    """
    started = time.monotonic()
    if is_fake(profile):
        return {
            "ok": True, "detail": "fake profile (offline test mode)",
            "latency_ms": 5, "model": profile["model"], "reply": "ok",
        }
    probe_client = AsyncOpenAI(
        base_url=profile["base_url"].rstrip("/"),
        api_key=profile["api_key"] or "none",
        timeout=25.0,
        max_retries=0,
    )
    gate = rate_limiter.gate_for(profile)
    try:
        async with gate.slot(16):
            probe_extra = _profile_extra_dict(profile)
            stream = await probe_client.chat.completions.create(
                model=profile["model"],
                messages=[
                    {"role": "system", "content": "You are a connection probe. Reply with the single word: ok"},
                    {"role": "user", "content": "ping"},
                ],
                temperature=0.0,
                max_tokens=8,
                stream=True,
                **({"extra_body": probe_extra} if probe_extra else {}),
            )
            content = ""
            async for event in stream:
                if not event.choices:
                    continue
                piece = event.choices[0].delta.content if event.choices[0].delta else None
                if piece:
                    content += piece
        latency = int((time.monotonic() - started) * 1000)
        content, _ = reasoning.strip_reasoning(content)
        return {
            "ok": True,
            "detail": f"provider answered in {latency} ms",
            "latency_ms": latency,
            "model": profile["model"],
            "reply": content.strip()[:120],
        }
    except QuotaExhausted as e:
        return {"ok": False, "detail": f"local quota: {e}", "latency_ms": 0,
                "model": profile["model"], "reply": ""}
    except Exception as e:  # noqa: BLE001
        latency = int((time.monotonic() - started) * 1000)
        return {"ok": False, "detail": _probe_error_text(e), "latency_ms": latency,
                "model": profile["model"], "reply": ""}
    finally:
        try:
            await probe_client.close()
        except Exception:  # noqa: BLE001
            pass


def _probe_error_text(e: Exception) -> str:
    status = getattr(e, "status_code", None)
    if status == 401:
        return "401 Unauthorized: check the API key"
    if status == 404:
        return "404 Not Found: check base URL and model name"
    if status == 429:
        return "429 Too Many Requests: rate limit reached, profile works but is throttled"
    body = getattr(e, "message", None) or str(e)
    return f"{type(e).__name__}: {body}"[:300]
