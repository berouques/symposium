"""Детектор повторов собственных реплик и обрезка до целого предложения."""
from __future__ import annotations

import difflib
import re

REPETITION_THRESHOLD = 0.72


def _norm(text: str) -> str:
    return re.sub(r"[^\w\u0400-\u04ff]+", " ", (text or "").lower()).strip()


def ratio(new_text: str, previous: str) -> float:
    """Похожесть (0..1; 1 — дословный повтор), независимо от языка."""
    a, b = _norm(new_text), _norm(previous)
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b)
    if sm.quick_ratio() < REPETITION_THRESHOLD * 0.9:
        return 0.0
    return sm.ratio()


def find(new_text: str, own_messages: list[dict]) -> tuple[float, int | None]:
    """Лучшая (похожесть, round_id) против трёх последних реплик автора."""
    best, best_round = 0.0, None
    for m in own_messages[-3:]:
        r = ratio(new_text, m["content"] or "")
        if r > best:
            best, best_round = r, m["round_id"]
    return best, best_round


def trim_to_last_sentence(text: str) -> str:
    """Мягкая обрезка переполненной реплики; пусто, если целого предложения нет."""
    matches = list(re.finditer(r"[.!?…»\"')\]]+", text))
    if not matches:
        return ""
    trimmed = text[:matches[-1].end()].strip()
    return trimmed if len(trimmed) >= 40 else ""
