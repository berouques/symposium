"""Token usage statistics endpoints (top-bar dashboard button)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import tokens
from ..auth import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/tokens")
def token_stats(user: dict = Depends(get_current_user)):
    """Total token consumption, breakdown by LLM profile, by symposium,
    by call type and by day. Users see their own data, admins see all."""
    return tokens.global_stats(user)
