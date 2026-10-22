"""Лимитер вызовов LLM по профилям (политика — см. limits.py).

Все вызовы системы (участники, голосование, субагенты, секретарь) идут
через `gate.slot()`. Слот УДЕРЖИВАЕТСЯ на всё время вызова — так
`max_concurrent` соблюдается по-настоящему (в v0.3 замок создавался, но
нигде не захватывался).

Ожидаемые лимиты (rps/rpm/rph/tpm/пауза/параллелизм) — ждём. Исчерпанные
суточные и месячные — по настройке on_exhausted: пауза симпозиума
(QuotaExhausted) или ожидание сброса.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from .. import db
from . import limits as limits_mod


class QuotaExhausted(Exception):
    """Суточный/месячный лимит исчерпан (on_exhausted = pause)."""

    def __init__(self, kind: str, used: int, limit: int, resets: str):
        super().__init__(f"{kind} quota exhausted ({used}/{limit}); resets {resets}")
        self.kind, self.used, self.limit, self.resets = kind, used, limit, resets


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class ProfileGate:
    def __init__(self, profile: dict):
        self.profile = profile
        self.policy = limits_mod.policy_of(profile)
        n = self.policy["max_concurrent"] if self.policy["enabled"] else None
        self._sem = asyncio.Semaphore(n) if n else None
        self._sec: deque[float] = deque()
        self._min: deque[float] = deque()
        self._hour: deque[float] = deque()
        self._tokens: deque[list] = deque()      # [ts, tokens]
        self._last_start = 0.0
        self._cooldown_until = 0.0
        self._in_flight = 0
        self._lock = asyncio.Lock()

    @property
    def is_fake(self) -> bool:
        return self.profile["model"].startswith("fake:")

    # ----------------------------------------------------------------- учёт
    def _prune(self, now: float) -> None:
        for dq, span in ((self._sec, 1.0), (self._min, 60.0), (self._hour, 3600.0)):
            while dq and now - dq[0] > span:
                dq.popleft()
        while self._tokens and now - self._tokens[0][0] > 60.0:
            self._tokens.popleft()

    def _day_requests(self) -> int:
        row = db.query_one("SELECT requests FROM profile_usage WHERE profile_id=? AND day=?",
                           (self.profile["id"], _utc_day()))
        return row["requests"] if row else 0

    def _bump_day(self) -> None:
        db.execute(
            "INSERT INTO profile_usage(profile_id, day, requests) VALUES(?,?,1) "
            "ON CONFLICT(profile_id, day) DO UPDATE SET requests=requests+1",
            (self.profile["id"], _utc_day()))

    def _tokens_since(self, prefix: str) -> int:
        v = db.query_value(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM token_usage "
            "WHERE profile_id=? AND created_at >= ?", (self.profile["id"], prefix))
        return int(v or 0)

    def note_retry_after(self, seconds: float) -> None:
        """Провайдер ответил 429 с Retry-After: общий «карантин» профиля."""
        if self.policy["respect_retry_after"] and seconds > 0:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    # ---------------------------------------------------------------- слот
    @asynccontextmanager
    async def slot(self, est_tokens: int = 0):
        """Дождаться разрешения и удерживать слот на время вызова."""
        if self.is_fake:
            yield None
            return
        if self._sem is not None:
            await self._sem.acquire()
        entry = None
        try:
            entry = await self._admit(est_tokens)
            self._in_flight += 1
            try:
                yield entry
            finally:
                self._in_flight -= 1
        finally:
            if self._sem is not None:
                self._sem.release()

    def record_tokens(self, entry: list | None, tokens: int) -> None:
        """Заменить оценку фактическим расходом токенов."""
        if entry is not None:
            entry[1] = max(0, int(tokens))

    async def _admit(self, est_tokens: int) -> list | None:
        p = self.policy
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)
                waits: list[float] = []
                if now < self._cooldown_until:
                    waits.append(self._cooldown_until - now)
                if p["enabled"]:
                    waits += self._window_waits(now, est_tokens)
                    try:
                        self._check_quota()
                    except _WaitHour:
                        waits.append(3600.0)
                if not waits:
                    break
                await asyncio.sleep(min(max(waits), 30.0) + 0.02)
            now = time.monotonic()
            self._sec.append(now)
            self._min.append(now)
            self._hour.append(now)
            self._last_start = now
            self._bump_day()
            entry = [now, est_tokens]
            self._tokens.append(entry)
            return entry

    def _window_waits(self, now: float, est: int) -> list[float]:
        p = self.policy
        waits: list[float] = []
        if p["min_gap_ms"] and now - self._last_start < p["min_gap_ms"] / 1000:
            waits.append(p["min_gap_ms"] / 1000 - (now - self._last_start))
        for key, dq, span in (("rps", self._sec, 1.0), ("rpm", self._min, 60.0), ("rph", self._hour, 3600.0)):
            if p[key] and len(dq) >= p[key]:
                waits.append(span - (now - dq[0]))
        if p["tpm"] and self._tokens:
            used = sum(t[1] for t in self._tokens)
            if used + est > p["tpm"]:
                waits.append(60.0 - (now - self._tokens[0][0]))
        return [w for w in waits if w > 0]

    def _check_quota(self) -> None:
        p = self.policy
        checks = []
        if p["rpd"]:
            checks.append(("requests/day", self._day_requests(), p["rpd"], "at 00:00 UTC"))
        if p["tpd"]:
            checks.append(("tokens/day", self._tokens_since(_utc_day()), p["tpd"], "at 00:00 UTC"))
        if p["tokens_per_month"]:
            checks.append(("tokens/month", self._tokens_since(_utc_month()), p["tokens_per_month"],
                           "on the 1st of next month, 00:00 UTC"))
        for kind, used, limit, resets in checks:
            if used >= limit:
                if p["on_exhausted"] == "pause":
                    raise QuotaExhausted(kind, used, limit, resets)
                raise _WaitHour()

    # ----------------------------------------------------------- наблюдение
    def usage(self) -> dict:
        now = time.monotonic()
        self._prune(now)
        p = self.policy
        return {
            "profile_id": self.profile["id"], "enabled": p["enabled"], "policy": p,
            "in_flight": self._in_flight,
            "rps": {"used": len(self._sec), "limit": p["rps"]},
            "rpm": {"used": len(self._min), "limit": p["rpm"]},
            "rph": {"used": len(self._hour), "limit": p["rph"]},
            "rpd": {"used": self._day_requests(), "limit": p["rpd"]},
            "tpm": {"used": sum(t[1] for t in self._tokens), "limit": p["tpm"]},
            "tpd": {"used": self._tokens_since(_utc_day()), "limit": p["tpd"]},
            "tokens_per_month": {"used": self._tokens_since(_utc_month()), "limit": p["tokens_per_month"]},
            "cooldown_s": max(0, round(self._cooldown_until - now, 1)),
            # совместимость с v0.3 (индикаторы UI)
            "rpm_used": len(self._min), "rpm_limit": p["rpm"],
            "rpd_used": self._day_requests(), "rpd_limit": p["rpd"],
        }


class _WaitHour(Exception):
    """Внутренний сигнал: исчерпан суточный лимит при on_exhausted=wait."""


class RateLimiter:
    """Реестр шлюзов по профилям. Шлюз пересоздаётся только при смене limits."""

    def __init__(self) -> None:
        self._gates: dict[int, tuple[str, ProfileGate]] = {}

    def gate_for(self, profile: dict) -> ProfileGate:
        key = str(profile.get("limits") or "")
        hit = self._gates.get(profile["id"])
        if hit is None or hit[0] != key:
            hit = (key, ProfileGate(profile))
            self._gates[profile["id"]] = hit
        return hit[1]

    def usage(self, profile: dict) -> dict:
        return self.gate_for(profile).usage()

    def invalidate(self, profile_id: int) -> None:
        self._gates.pop(profile_id, None)


rate_limiter = RateLimiter()
