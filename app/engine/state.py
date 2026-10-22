"""Типизированные состояния возобновления (хранятся JSON в БД).

Раньше `pending_turn` был безымянным мешком счётчиков; теперь это dataclass
с явными полями. Рестарт сервера безопасен: состояние читается из БД.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


def _load(cls, raw):
    if not raw:
        return None
    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class TurnState:
    """Незавершённый ход участника."""
    member_id: int
    round_id: int
    phase: str = "speak"
    history: list = field(default_factory=list)   # диалог корректировок внутри хода
    calls_used: int = 0
    iterations: int = 0
    hitl_pending: int | None = None
    nudges: dict = field(default_factory=dict)    # state/truncation/repetition/empty/vote
    speech_cache: str = ""                        # речь, ожидающая <state>-досылки
    boost: float = 1.0                            # множитель лимита ответа после обрезки/пустого ответа

    def dump(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def load(cls, raw):
        return _load(cls, raw)

    def nudge_count(self, key: str) -> int:
        return int(self.nudges.get(key, 0))

    def bump(self, key: str) -> None:
        self.nudges[key] = self.nudge_count(key) + 1


@dataclass
class VoteState:
    """Идущее голосование (слепое: бюллетени копятся здесь и раскрываются
    только при подсчёте)."""
    kind: str = "member"                  # member | checklist | stall | round_limit
    initiator_id: int | None = None
    reason: str = ""
    remaining: list = field(default_factory=list)   # member_id, ещё не голосовавшие
    ballots: dict = field(default_factory=dict)     # str(member_id) -> Ballot.public()
    pending: dict | None = None                     # TurnState текущего голоса (dict)

    def dump(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def load(cls, raw):
        return _load(cls, raw)
