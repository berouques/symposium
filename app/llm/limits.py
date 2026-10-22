"""Политика лимитов LLM-профиля и пресеты провайдеров.

Политика — JSON в api_profiles.limits. ВСЁ выключено по умолчанию: система
ничего не навязывает, пользователь включает нужные лимиты сам (или
применяет пресет и правит его). Поля (null/0 = не ограничивать):

  enabled           общий выключатель политики
  max_concurrent    одновременных вызовов
  min_gap_ms        минимальная пауза между стартами вызовов
  rps / rpm / rph   запросов в секунду / минуту / час (скользящие окна)
  rpd               запросов в сутки (по UTC)
  tpm               токенов в минуту (вход+выход, оценка до вызова, факт после)
  tpd               токенов в сутки (UTC)
  tokens_per_month  токенов в календарный месяц (UTC)
  on_exhausted      "pause" — суточный/месячный лимит исчерпан → симпозиум
                    встаёт на паузу с понятным сообщением; "wait" — ждать
                    сброса (проверка раз в час)
  respect_retry_after  уважать Retry-After / X-RateLimit-Reset при 429
  retry             {attempts, base_s, max_s} — повторы при 429/5xx/сети
"""
from __future__ import annotations

import json

DEFAULT_POLICY: dict = {
    "enabled": False,
    "max_concurrent": None, "min_gap_ms": 0,
    "rps": None, "rpm": None, "rph": None, "rpd": None,
    "tpm": None, "tpd": None, "tokens_per_month": None,
    "on_exhausted": "pause", "respect_retry_after": True,
    "retry": {"attempts": 4, "base_s": 5.0, "max_s": 120.0},
}

_INT_FIELDS = ("max_concurrent", "min_gap_ms", "rps", "rpm", "rph", "rpd", "tpm", "tpd",
               "tokens_per_month")


def normalize(raw: dict | str | None) -> dict:
    """Любой ввод → полная валидная политика (мусор отбрасывается)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    pol = json.loads(json.dumps(DEFAULT_POLICY))
    pol["enabled"] = bool(raw.get("enabled", pol["enabled"]))
    for f in _INT_FIELDS:
        v = raw.get(f)
        try:
            v = int(v) if v not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            v = None
        if v is not None and v < 0:
            v = None
        pol[f] = v if f != "min_gap_ms" else (v or 0)
    if raw.get("on_exhausted") in ("pause", "wait"):
        pol["on_exhausted"] = raw["on_exhausted"]
    pol["respect_retry_after"] = bool(raw.get("respect_retry_after", True))
    retry = raw.get("retry") if isinstance(raw.get("retry"), dict) else {}
    try:
        pol["retry"] = {
            "attempts": max(1, min(10, int(retry.get("attempts", 4)))),
            "base_s": max(0.0, float(retry.get("base_s", 5.0))),
            "max_s": max(1.0, float(retry.get("max_s", 120.0))),
        }
    except (TypeError, ValueError):
        pass
    return pol


def policy_of(profile: dict) -> dict:
    return normalize(profile.get("limits"))


# ---------------------------------------------------------------- пресеты
# Цифры взяты из документации провайдеров (см. поле source); это отправная
# точка, а не истина: лимиты меняются — правьте под свой аккаунт.
PRESETS: list[dict] = [
    {
        "key": "none", "title": "Без лимитов",
        "note": "Лимитер выключен: все вызовы идут сразу.",
        "source": "", "policy": {"enabled": False},
    },
    {
        "key": "openrouter_free", "title": "OpenRouter — бесплатные модели (без пополнения)",
        "note": "20 запросов в минуту, 50 в сутки. Одновременные вызовы ограничены до 1 "
                "консервативно (по опыту автора; в документации не заявлено).",
        "source": "https://openrouter.ai/docs/api-reference/limits",
        "policy": {"enabled": True, "max_concurrent": 1, "min_gap_ms": 1000, "rpm": 20, "rpd": 50,
                   "on_exhausted": "pause"},
    },
    {
        "key": "openrouter_free_credits", "title": "OpenRouter — бесплатные модели (баланс ≥ 10 кредитов)",
        "note": "20 запросов в минуту, 1000 в сутки.",
        "source": "https://openrouter.ai/docs/api-reference/limits",
        "policy": {"enabled": True, "max_concurrent": 1, "min_gap_ms": 1000, "rpm": 20, "rpd": 1000,
                   "on_exhausted": "pause"},
    },
    {
        "key": "mistral_free", "title": "Mistral — бесплатный тариф (Experiment)",
        "note": "1 запрос в секунду, 500 000 токенов в минуту, 1 млрд токенов в месяц "
                "(лимиты на уровне организации).",
        "source": "https://help.mistral.ai/en/articles/225174-what-are-the-limits-of-the-free-tier",
        "policy": {"enabled": True, "max_concurrent": 1, "rps": 1, "tpm": 500000,
                   "tokens_per_month": 1_000_000_000, "on_exhausted": "pause"},
    },
    {
        "key": "serial_polite", "title": "Последовательно, с паузой 0,5 с",
        "note": "Без внешних лимитов: один вызов за раз и короткая пауза (щадящий режим).",
        "source": "", "policy": {"enabled": True, "max_concurrent": 1, "min_gap_ms": 500},
    },
]


def preset(key: str) -> dict | None:
    for p in PRESETS:
        if p["key"] == key:
            return normalize(p["policy"])
    return None
