"""Лимитер: политика, пресеты, реальное удержание слота, окна, квоты, Retry-After."""
import asyncio
import json
import time

import pytest

from app import db
from app.auth import utcnow
from app.llm import limits
from app.llm.client import _retry_after_seconds
from app.llm.rate import ProfileGate, QuotaExhausted, RateLimiter

_pid = [90000]


def profile(policy: dict | None = None, model: str = "real-model") -> dict:
    _pid[0] += 1
    return {"id": _pid[0], "model": model, "limits": json.dumps(policy) if policy is not None else None}


class TestPolicy:
    def test_off_by_default(self):
        pol = limits.policy_of({"limits": None})
        assert pol["enabled"] is False and pol["rpm"] is None and pol["max_concurrent"] is None

    def test_normalize_garbage(self):
        pol = limits.normalize({"enabled": 1, "rpm": "abc", "rpd": "-5", "tpm": "100", "on_exhausted": "x"})
        assert pol["rpm"] is None and pol["rpd"] is None and pol["tpm"] == 100
        assert pol["on_exhausted"] == "pause"
        assert limits.normalize("not json")["enabled"] is False

    def test_presets_match_provider_docs(self):
        pr = limits.preset("openrouter_free")
        assert pr["rpm"] == 20 and pr["rpd"] == 50 and pr["enabled"]
        assert limits.preset("openrouter_free_credits")["rpd"] == 1000
        m = limits.preset("mistral_free")
        assert m["rps"] == 1 and m["tpm"] == 500000 and m["tokens_per_month"] == 1_000_000_000
        assert limits.preset("none")["enabled"] is False
        assert limits.preset("nope") is None
        assert all(p["key"] for p in limits.PRESETS)


class TestGate:
    async def test_disabled_policy_never_blocks(self):
        g = ProfileGate(profile({"enabled": False, "rpm": 1}))
        t = time.monotonic()
        for _ in range(5):
            async with g.slot():
                pass
        assert time.monotonic() - t < 0.5

    async def test_fake_bypasses(self):
        g = ProfileGate(profile({"enabled": True, "max_concurrent": 1, "rpm": 1}, model="fake:basic"))
        async with g.slot():
            async with g.slot():
                pass

    async def test_max_concurrent_is_really_enforced(self):
        g = ProfileGate(profile({"enabled": True, "max_concurrent": 1}))
        running, peak = 0, 0

        async def worker():
            nonlocal running, peak
            async with g.slot():
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.03)
                running -= 1

        await asyncio.gather(*(worker() for _ in range(5)))
        assert peak == 1

    async def test_parallel_when_allowed(self):
        g = ProfileGate(profile({"enabled": True, "max_concurrent": 3}))
        running, peak = 0, 0

        async def worker():
            nonlocal running, peak
            async with g.slot():
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.05)
                running -= 1

        await asyncio.gather(*(worker() for _ in range(6)))
        assert peak == 3

    async def test_min_gap(self):
        g = ProfileGate(profile({"enabled": True, "min_gap_ms": 150}))
        t = time.monotonic()
        for _ in range(3):
            async with g.slot():
                pass
        assert time.monotonic() - t >= 0.29

    async def test_rps_window(self):
        g = ProfileGate(profile({"enabled": True, "rps": 2}))
        t = time.monotonic()
        for _ in range(3):
            async with g.slot():
                pass
        assert time.monotonic() - t >= 0.95  # третий запрос ждёт окно в 1 с

    async def test_daily_quota_pauses(self):
        g = ProfileGate(profile({"enabled": True, "rpd": 2, "on_exhausted": "pause"}))
        for _ in range(2):
            async with g.slot():
                pass
        with pytest.raises(QuotaExhausted) as e:
            async with g.slot():
                pass
        assert "requests/day" in str(e.value) and e.value.limit == 2
        assert g.usage()["rpd"] == {"used": 2, "limit": 2}

    async def test_token_quota_from_usage_table(self):
        p = profile({"enabled": True, "tpd": 1000, "on_exhausted": "pause"})
        db.execute(
            "INSERT INTO token_usage(symposium_id, call_type, profile_id, model, tokens_in, tokens_out, "
            "latency_ms, created_at) VALUES(NULL,'turn',?,?,?,?,0,?)", (p["id"], "m", 700, 400, utcnow()))
        g = ProfileGate(p)
        with pytest.raises(QuotaExhausted):
            async with g.slot():
                pass

    async def test_tpm_waits_for_window(self):
        g = ProfileGate(profile({"enabled": True, "tpm": 1000}))
        async with g.slot(900) as e:
            g.record_tokens(e, 900)
        t = time.monotonic()
        async with asyncio.timeout(3):
            # второй вызов на 900 токенов не влезает в минутное окно → должен ждать;
            # проверяем только, что слот НЕ выдаётся мгновенно
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.4):
                    async with g.slot(900):
                        pass
        assert time.monotonic() - t >= 0.35

    async def test_retry_after_cooldown_applies_even_without_policy(self):
        g = ProfileGate(profile(None))
        g.policy["respect_retry_after"] = True
        g.note_retry_after(0.3)
        t = time.monotonic()
        async with g.slot():
            pass
        assert time.monotonic() - t >= 0.25

    def test_registry_recreates_only_on_limits_change(self):
        reg = RateLimiter()
        p = profile({"enabled": True, "rpm": 5})
        g1 = reg.gate_for(p)
        assert reg.gate_for(dict(p, name="renamed")) is g1
        assert reg.gate_for(dict(p, limits=json.dumps({"enabled": True, "rpm": 6}))) is not g1


class TestRetryAfter:
    class _E(Exception):
        def __init__(self, headers):
            self.response = type("R", (), {"headers": headers})()

    def test_seconds(self):
        assert _retry_after_seconds(self._E({"retry-after": "7"})) == 7.0

    def test_openrouter_reset_epoch_ms(self):
        reset = str(int((time.time() + 12) * 1000))
        v = _retry_after_seconds(self._E({"x-ratelimit-reset": reset}))
        assert 10 <= v <= 13

    def test_none(self):
        assert _retry_after_seconds(self._E({})) is None
        assert _retry_after_seconds(ValueError("x")) is None
