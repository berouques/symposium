"""Явный конечный автомат симпозиума (чистые функции, без БД и LLM).

Состояние фактически хранится в БД (status, pending_turn, voting_state, …);
здесь — ТОЛЬКО решение «что делать дальше» по снимку этого состояния. Так
весь порядок шагов виден в одном месте и тестируется без запуска движка.

Фазы (порядок = приоритет):
    PAUSE_NO_PROFILE  нет LLM-профиля
    OPEN              секретарь ещё не открыл симпозиум
    VOTE              идёт голосование (абсолютный приоритет)
    ENSURE_ROUND      текущий раунд не создан
    RESUME_TURN       ход прерван (рестарт, ошибка, HITL) — продолжить
    START_TURN        выбран следующий оратор
    CLOSE_ROUND       все высказались
    IDLE              симпозиум не активен
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    IDLE = "idle"
    PAUSE_NO_PROFILE = "pause_no_profile"
    OPEN = "open"
    VOTE = "vote"
    ENSURE_ROUND = "ensure_round"
    RESUME_TURN = "resume_turn"
    START_TURN = "start_turn"
    CLOSE_ROUND = "close_round"


@dataclass(frozen=True)
class Action:
    kind: Kind
    member_id: int | None = None


@dataclass
class Snapshot:
    active: bool
    has_profile: bool
    has_opening: bool
    voting: bool
    round_exists: bool
    pending_member_id: int | None
    next_speaker_id: int | None


def decide(s: Snapshot) -> Action:
    if not s.active:
        return Action(Kind.IDLE)
    if not s.has_profile:
        return Action(Kind.PAUSE_NO_PROFILE)
    if not s.has_opening:
        return Action(Kind.OPEN)
    if s.voting:
        return Action(Kind.VOTE)
    if not s.round_exists:
        return Action(Kind.ENSURE_ROUND)
    if s.pending_member_id is not None:
        return Action(Kind.RESUME_TURN, s.pending_member_id)
    if s.next_speaker_id is not None:
        return Action(Kind.START_TURN, s.next_speaker_id)
    return Action(Kind.CLOSE_ROUND)


def pick_speaker(member_ids: list[int], spoken: set[int], priority: list[int],
                 asked: list[int], last_spoken: dict[int, int] | None = None) -> int | None:
    """Оратор раунда: сначала авторы возражений, затем те, кого спросили по
    имени («@Имя: …»), затем тот, кто ДОЛЬШЕ ВСЕХ не говорил (справедливая
    очередь: если раунды прерывались, слово не застревает у первых), при
    равенстве — порядок создания. Каждый говорит один раз за раунд."""
    for group in (priority, asked):
        for mid in group:
            if mid in member_ids and mid not in spoken:
                return mid
    last = last_spoken or {}
    pending = [m for m in member_ids if m not in spoken]
    if not pending:
        return None
    return min(pending, key=lambda m: (last.get(m, 0), member_ids.index(m)))
