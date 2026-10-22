"""Протокол v2: форматы ответа участника, бюллетеня голосования и вызовов
субагентов. Только чистые функции — без БД и LLM, поэтому тестируются в
изоляции (tests/test_protocol.py).

    envelope.py — ответ участника: речь + блок <state> (+ <call> блоки)
    ballot.py   — бюллетень голосования: <vote>/<reason>/<unresolved>
    calls.py    — вызовы субагентов <call agent="…">…</call>
    nudges.py   — тексты корректирующих просьб
    repetition.py — детектор повторов собственных реплик
"""
from .ballot import Ballot, parse_ballot  # noqa: F401
from .calls import Call, parse_calls  # noqa: F401
from .envelope import Reply, State, parse_reply  # noqa: F401
