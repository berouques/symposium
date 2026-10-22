"""Reasoning ("thinking") control, provider-agnostic (v0.2.3).

The symposium stores a user-facing pair:
  thinking_mode  : 'default' (follow the profile, touch nothing) | 'off' | 'on'
  thinking_depth : None (model decides) | 'low' | 'medium' | 'high'

A member may override the symposium-level pair. The resolved spec maps, per
provider family, to concrete request fields:

  zhipu (bigmodel.cn, glm-4.5..5.2)  off -> {"thinking": {"type": "disabled"}}
                                      on  -> {"thinking": {"type": "enabled"}};
                                            glm-5.2+ additionally takes
                                            reasoning_effort low/high/max
  zhipu glm-5.3*                     thinking is ALWAYS on and cannot be
                                      disabled (400); 'off' is best-effort
                                      reasoning_effort "low", depth maps
                                      low/high/max directly
  openai (gpt-5 / o-series)          reasoning_effort low|medium|high;
                                      'off' is best-effort (reasoning models
                                      cannot fully switch it off)
  qwen (dashscope-style)             enable_thinking true/false (+ best-effort
                                      thinking_budget)
  anthropic                          {"thinking": {"type": "enabled",
                                      "budget_tokens": N}}; off -> no field
  generic                            no provider fields (best effort only)

BUDGET SEMANTICS (the point of the feature): the member's reply limit
(max_tokens of the PUBLIC speech) never pays for the thinking. Where the
provider caps thinking+reply together in one max_tokens (zhipu, openai,
qwen — reasoning tokens are output tokens), the client INFLATES the request
limit by DEPTH_ALLOWANCE[depth]; the turn protocol still tells the model the
public budget. Anthropic gets the allowance as an explicit budget_tokens.
Everything degrades gracefully: a provider that rejects the fields triggers
the client's drop-thinking-then-drop-extra retry ladder.
"""
from __future__ import annotations

# extra tokens requested on top of the reply budget, per depth level
DEPTH_ALLOWANCE: dict[str | None, int] = {
    None: 1024,   # model decides: moderate reserve
    "low": 512,
    "medium": 1536,
    "high": 3072,
}

VALID_MODES = ("default", "off", "on")
VALID_DEPTHS = ("low", "medium", "high")

# fields that belong to the thinking layer; used by the retry ladder to
# strip them selectively when a provider rejects them
THINKING_KEYS = ("thinking", "reasoning_effort", "enable_thinking", "thinking_budget")


_ORDER = {"low": 1, "medium": 2, "high": 3}


def _min_depth(a: str | None, b: str | None) -> str | None:
    """Меньший из двух уровней; None («модель решает») уступает заданному."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _ORDER[a] <= _ORDER[b] else b


def resolve(member: dict, symposium: dict, profile: dict | None = None) -> tuple[str, str | None]:
    """Итоговый режим мышления: участник → симпозиум → профиль модели.

    Настройка МОДЕЛИ (профиля) — потолок: если мышление модели выключено,
    участник включить его не может; если задан уровень модели, уровень
    участника не может быть выше. Режим «default» участника/симпозиума
    наследует настройку профиля."""
    profile = profile or {}
    p_mode = profile.get("thinking_mode") or "default"
    p_depth = profile.get("thinking_depth")
    if p_mode not in VALID_MODES:
        p_mode = "default"
    if p_depth not in VALID_DEPTHS:
        p_depth = None

    mode = member.get("thinking_mode") or symposium.get("thinking_mode") or "default"
    if mode not in VALID_MODES:
        mode = "default"
    if mode == "default":
        mode = p_mode
    depth = member.get("thinking_depth")
    if depth is None:
        depth = symposium.get("thinking_depth")
    if depth not in VALID_DEPTHS:
        depth = None

    if p_mode == "off":
        return "off", None                      # потолок: модель без мышления
    if mode == "on" and p_mode == "on":
        depth = _min_depth(depth, p_depth)      # потолок по уровню
    elif mode == "on" and depth is None and p_depth:
        depth = p_depth
    if mode != "on":
        depth = None
    return mode, depth


def ceiling_note(profile: dict | None) -> str:
    """Подпись для UI: что разрешает модель."""
    profile = profile or {}
    m = profile.get("thinking_mode") or "default"
    d = profile.get("thinking_depth")
    if m == "off":
        return "Мышление модели выключено — участники включить его не могут."
    if m == "on":
        return f"Мышление модели включено{f', уровень не выше {d}' if d else ''}."
    return "Мышление модели не ограничено профилем (решает провайдер)."


def reasons_by_default(profile: dict) -> bool:
    """Модель думает по умолчанию (скрытые рассуждения едят max_tokens)."""
    model = (profile.get("model") or "").lower()
    kind = provider_kind(profile)
    if "/" in model:                    # openrouter: vendor/model
        model = model.split("/", 1)[1]
    if kind == "zhipu":
        return model.startswith(("glm-4.5", "glm-4.6", "glm-4.7", "glm-5"))
    if kind == "openai":
        return model.startswith(("o1", "o3", "o4", "gpt-5"))
    if kind == "qwen":
        return model.startswith(("qwen3", "qwq", "deepseek-r"))
    return any(k in model for k in ("-thinking", "reasoner", "r1"))


def provider_kind(profile: dict) -> str:
    """Best-effort provider family from the profile's model/base_url."""
    model = (profile.get("model") or "").lower().strip()
    base = (profile.get("base_url") or "").lower().strip()
    # openrouter routes by vendor prefix: "z-ai/glm-4.6", "qwen/qwen3..."
    if "openrouter" in base and "/" in model:
        vendor = model.split("/", 1)[0]
        if vendor in ("z-ai", "zhipu", "glm"):
            return "zhipu"
        if vendor in ("anthropic",):
            return "anthropic"
        if vendor in ("qwen", "deepseek", "alibaba"):
            return "qwen"
        return "generic"
    if "bigmodel.cn" in base or "zhipu" in base or model.startswith("glm"):
        return "zhipu"
    if "anthropic" in base or model.startswith("claude"):
        return "anthropic"
    if "dashscope" in base or model.startswith(("qwen", "deepseek")):
        return "qwen"
    if "api.openai.com" in base or model.startswith(("gpt-", "o1", "o3", "o4", "o3-", "chatgpt")):
        return "openai"
    return "generic"


def _glm_effort(model: str, depth: str | None) -> str | None:
    """reasoning_effort for GLM-5.2+/5.3*. None = do not send the field.

    GLM-5.2 accepts none/minimal (skip thinking), low/medium (-> high),
    xhigh (-> max); effective distinct levels: high, max.
    GLM-5.3/5.3-flash accept only low/high/max (default max).
    We send only unambiguous values: low -> 'low' (5.3) / omitted (5.2),
    medium -> 'high', high -> 'max'.
    """
    is53 = model.lower().startswith("glm-5.3")
    if depth is None:
        return None
    if depth == "high":
        return "max"
    if depth == "medium":
        return "high"
    if depth == "low":
        return "low" if is53 else None  # 5.2 folds 'low' into 'high' anyway
    return None


def request_params(
    profile: dict, mode: str, depth: str | None, reply_max_tokens: int
) -> tuple[dict, int, str | None]:
    """Map the resolved spec onto a provider.

    Returns (extra_fields, request_max_tokens, note) where `note` is a short
    human-readable caveat surfaced in the UI (e.g. "GLM-5.3 cannot disable
    thinking; using the lowest effort").

    ВАЖНО (v0.3, баг «350 токенов + мышление off»): если провайдер НЕ МОЖЕТ
    выключить мышление (GLM-5.3 всегда думает; openai reasoning-модели
    тоже), а режим off — мы всё равно добавляем минимальную надбавку
    (LOW_ALLOWANCE) к лимиту ЗАПРОСА. Иначе интерливированное мышление
    съедает бюджет реплики: видимый текст начинается с середины слова и
    обрезается — truncated/turn. Лимит реплики в ПРОМПТЕ не меняется.
    """
    allowance = DEPTH_ALLOWANCE[depth]
    request_max = reply_max_tokens
    fields: dict = {}
    note: str | None = None
    kind = provider_kind(profile)
    model = (profile.get("model") or "").lower()

    if mode == "default":
        # провайдерский режим по умолчанию: ничего не посылаем, но модель,
        # думающая сама, должна получить запас сверх лимита РЕПЛИКИ — иначе
        # скрытые рассуждения съедают бюджет и видимый текст обрывается
        if reasons_by_default(profile):
            return {}, reply_max_tokens + DEPTH_ALLOWANCE[None], \
                "модель думает по умолчанию — к лимиту запроса добавлен запас на мышление"
        return {}, reply_max_tokens, None

    if kind == "zhipu":
        always_on = model.startswith("glm-5.3")
        if mode == "off":
            if always_on:
                # 5.3 rejects thinking.type=disabled with a 400 (and the
                # profile preset may carry exactly that); best effort:
                # explicitly enabled + the lowest reasoning effort.
                # Надбавка обязательна: модель будет думать независимо
                # от наших пожеланий (см. docstring функции).
                fields = {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
                request_max = reply_max_tokens + DEPTH_ALLOWANCE["low"]
                note = "glm-5.3: мышление всегда включено — минимальный уровень (low) + надбавка к лимиту запроса"
            else:
                fields = {"thinking": {"type": "disabled"}}
        else:
            fields = {"thinking": {"type": "enabled"}}
            effort = _glm_effort(model, depth)
            if effort:
                fields["reasoning_effort"] = effort
            if not model.startswith(("glm-5.2", "glm-5.3", "glm-5.4")):
                # older GLMs do not know reasoning_effort — the retry ladder
                # would drop it; leave only the on/off switch
                fields.pop("reasoning_effort", None)
            request_max = reply_max_tokens + allowance  # thinking counts toward max_tokens
        return fields, request_max, note

    if kind == "openai":
        if mode == "off":
            # reasoning models cannot fully switch thinking off — мышление
            # всё равно появится, дадим ему минимальный бюджет сверх реплики
            fields = {"reasoning_effort": "low"}
            request_max = reply_max_tokens + DEPTH_ALLOWANCE["low"]
            note = "openai: reasoning-модели не выключают мышление — low + надбавка к лимиту запроса"
        else:
            effort = {"low": "low", "medium": "medium", "high": "high"}.get(depth)  # type: ignore[arg-type]
            if effort:
                fields = {"reasoning_effort": effort}
            request_max = reply_max_tokens + allowance
        return fields, request_max, note

    if kind == "qwen":
        if mode == "off":
            fields = {"enable_thinking": False}
        else:
            fields = {"enable_thinking": True, "thinking_budget": allowance}
            request_max = reply_max_tokens + allowance
        return fields, request_max, note

    if kind == "anthropic":
        if mode == "on":
            # explicit, separate thinking budget; max_tokens must exceed it
            fields = {"thinking": {"type": "enabled", "budget_tokens": allowance}}
            request_max = reply_max_tokens + allowance
        return fields, request_max, note

    # generic: no provider fields; still inflate the limit so a thinking
    # model has headroom beyond the public reply budget
    if mode == "on":
        request_max = reply_max_tokens + allowance
        note = "провайдер не опознан: мышление не управляется параметрами, лимит запроса расширен"
    elif mode == "off":
        # неизвестный провайдер может думать принудительно — страховочная
        # надбавка спасает реплику от обрезки по вине мышления
        request_max = reply_max_tokens + DEPTH_ALLOWANCE["low"]
        note = "провайдер не опознан: мышление не управляется — дана страховочная надбавка к лимиту"
    return fields, request_max, note


def thinking_note_for_ui(profile: dict, mode: str, depth: str | None, reply_max_tokens: int) -> str:
    """One-line effective-math summary for the settings modal."""
    _, request_max, note = request_params(profile, mode, depth, reply_max_tokens)
    parts = []
    if mode == "default":
        parts.append("как в профиле (extra_body провайдера решает)")
    else:
        parts.append(f"реплика ≤ {reply_max_tokens}")
        if request_max > reply_max_tokens:
            parts.append(f"+ мышление ≤ {request_max - reply_max_tokens} → запрос ≤ {request_max}")
        else:
            parts.append("мышление отдельным бюджетом провайдера")
    if note:
        parts.append(note)
    return "; ".join(parts)
