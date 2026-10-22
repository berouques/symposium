"""Бюллетень голосования (протокол v2).

    <vote>agree | reservations | object</vote>
    <reason>одна строка</reason>
    <unresolved>что остаётся нерешённым (для reservations/object)</unresolved>
    <satisfied_if>что меня устроит (для object)</satisfied_if>

Голосование СЛЕПОЕ: голосующий не видит чужих бюллетеней. Парсер терпим, но
позицию ищет только в теге <vote> либо в самом начале ответа — слово «veto»
в середине пояснения голосом не считается (баг v0.3 с регулярным выражением,
ловившим слово где угодно).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TAG = lambda name: re.compile(rf"<{name}>(.*?)</{name}\s*>", re.DOTALL | re.IGNORECASE)  # noqa: E731
_VOTE, _REASON, _UNRES, _SAT = _TAG("vote"), _TAG("reason"), _TAG("unresolved"), _TAG("satisfied_if")

_AGREE = {"agree", "yes", "approve", "approved", "accept", "support", "for", "aye",
          "согласен", "согласна", "да", "за"}
_OBJECT = {"object", "objection", "veto", "disagree", "no", "against", "reject", "nay",
           "возражаю", "против", "нет"}


@dataclass
class Ballot:
    ok: bool = False
    position: str = ""            # agree | reservations | object
    reason: str = ""
    unresolved: str = ""
    satisfied_if: str = ""
    problem: str | None = None

    def public(self) -> dict:
        return {"position": self.position, "reason": self.reason,
                "unresolved": self.unresolved, "satisfied_if": self.satisfied_if}


def _position(word: str) -> str:
    low = (word or "").strip().lower()
    if "reserv" in low or "оговор" in low or "conditional" in low:
        return "reservations"
    first = re.split(r"[^\wа-яё]+", low.strip("[]<>"), maxsplit=1)[0] if low else ""
    if first in _OBJECT:
        return "object"
    if first in _AGREE:
        return "agree"
    return ""


def _grab(rx: re.Pattern, text: str) -> str:
    m = rx.search(text)
    return " ".join(m.group(1).split()) if m else ""


def parse_ballot(raw: str) -> Ballot:
    text = (raw or "").strip()
    # старые маркеры принимаем ТОЛЬКО в точной форме с квадратными скобками
    legacy_agree = re.search(r"\[AGREE_END\]", text)
    legacy_veto = re.search(r"\[VETO\]", text)

    pos = ""
    m = _VOTE.search(text)
    if m:
        pos = _position(m.group(1))
    elif legacy_veto and (not legacy_agree or legacy_veto.start() < legacy_agree.start()):
        pos = "object"
    elif legacy_agree:
        pos = "agree"
    else:
        head = re.sub(r"<[^>]+>", " ", text).strip()[:40]
        pos = _position(head)

    if not pos:
        return Ballot(problem="no recognizable <vote> position")

    reason = _grab(_REASON, text)
    unresolved = _grab(_UNRES, text)
    satisfied = _grab(_SAT, text)
    if legacy_veto and not reason:
        reason = re.sub(r"\[[A-Z_]+\]", "", text[legacy_veto.end():]).strip()
    if not reason and not m:
        # без тегов: пояснение — остаток текста после слова-позиции
        reason = re.sub(r"<[^>]+>|\[[A-Z_]+\]", " ", text).strip()[:300]

    ballot = Ballot(ok=True, position=pos, reason=reason, unresolved=unresolved,
                    satisfied_if=satisfied)
    if pos == "object" and not (reason or unresolved):
        ballot.ok = False
        ballot.problem = "an objection must say what remains unresolved"
    if pos in ("object", "reservations") and not unresolved:
        ballot.unresolved = reason
    return ballot
