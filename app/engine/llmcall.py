"""LLM calls on behalf of a participant: personal profile → fallback logic.

A participant may have its own LLM profile. If it cannot serve the call
(API error, quota, deleted profile):
  * fallback ON  → the call is retried with the symposium's profile
  * fallback OFF → the symposium STOPS with an explicit recommendation to
                   open the participant's settings (kind member_profile_*,
                   not auto-recoverable: a human decision is needed).
"""
from __future__ import annotations

from .. import db
from ..llm import client as llm_client
from ..llm import thinking as thinking_mod
from ..llm.client import LLMPersistentError

PROFILE_FAILURES = {"llm_unavailable", "llm_error", "llm_http", "payment_required", "quota_exhausted"}


async def member_chat(store, member: dict, row: dict, *, thinking_override: tuple | None = None, **kw):
    """→ (result, profile_used, used_fallback)."""
    sym_profile = store.profile()
    own_id = member.get("api_profile_id")
    fallback = bool(member.get("profile_fallback", 1))
    own = db.query_one("SELECT * FROM api_profiles WHERE id=?", (own_id,)) if own_id else None
    name = member["name"]
    if own_id and own is None:
        if not (fallback and sym_profile):
            raise LLMPersistentError(
                f"The LLM profile assigned to {name} no longer exists and fallback to the symposium profile is off. "
                f"Open the symposium → click {name} → choose another LLM profile (or enable the fallback), then press Continue.",
                "member_profile_missing")
        candidates = [(sym_profile, True)]
    elif own is not None:
        candidates = [(own, False)]
        if fallback and sym_profile and sym_profile["id"] != own["id"]:
            candidates.append((sym_profile, True))
    else:
        if sym_profile is None:
            raise LLMPersistentError("No LLM profile configured for this symposium", "no_profile")
        candidates = [(sym_profile, False)]
    last: LLMPersistentError | None = None
    for prof, is_fb in candidates:
        mode, depth = thinking_override or thinking_mod.resolve(member, row, prof)
        try:
            result = await llm_client.chat(prof, thinking_mode=mode, thinking_depth=depth, **kw)
            return result, prof, is_fb
        except LLMPersistentError as e:
            last = e
            if e.kind not in PROFILE_FAILURES:
                raise
    assert last is not None
    if own is not None and not fallback:
        raise LLMPersistentError(
            f"The LLM profile '{own['name']}' assigned to {name} is unavailable ({last.kind}: {last}). "
            f"Fallback to the symposium profile is off for this participant. Open the symposium → click {name} → "
            "choose another LLM profile (or enable the fallback), then press Continue.", "member_profile_failed")
    raise last
