"""Сходимость обсуждения: когда пора предложить завершение (чистые функции).

Вместо «модель сама вспомнит про [END_SYMPOSIUM]» движок смотрит на
структурные блоки <state>: чек-лист критерия, готовность участников,
наличие новых утверждений. Слово «голосование» участникам не раскрывается —
поле readiness нейтрально.
"""
from __future__ import annotations

import json
import math


def meta_state(msg: dict) -> dict | None:
    """Распарсенный <state> реплики (из messages.meta)."""
    raw = msg.get("meta")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    st = data.get("state") if isinstance(data, dict) else None
    return st if isinstance(st, dict) else None


def latest_states(messages: list[dict]) -> dict[int, dict]:
    """member_id → state его последней содержательной реплики."""
    out: dict[int, dict] = {}
    for m in messages:
        if m["kind"] == "member" and m["member_id"]:
            st = meta_state(m)
            if st is not None:
                out[m["member_id"]] = st
    return out


def checklist_consensus(items: list[dict], states: dict[int, dict], n_members: int) -> dict[str, str]:
    """ref → 'done'|'open'. Пункт закрыт, если о нём высказалась хотя бы
    половина состава и не менее 2/3 высказавшихся говорят «done»."""
    need = max(1, math.ceil(n_members / 2))
    result: dict[str, str] = {}
    for item in items:
        votes = [s.get("checklist", {}).get(item["ref"]) for s in states.values()]
        votes = [v for v in votes if v in ("done", "open")]
        done = sum(1 for v in votes if v == "done")
        result[item["ref"]] = "done" if len(votes) >= need and done * 3 >= len(votes) * 2 else "open"
    return result


def round_claims(messages: list[dict], round_id: int) -> int:
    """Сколько новых утверждений заявлено в раунде."""
    total = 0
    for m in messages:
        if m["round_id"] == round_id and m["kind"] == "member":
            st = meta_state(m)
            if st:
                total += len(st.get("claims") or [])
    return total


def should_propose_end(
    *, current_round: int, cooldown_round: int, items: list[dict], open_issues: int,
    states: dict[int, dict], n_members: int, recent_round_claims: list[int], stall_rounds: int,
) -> str | None:
    """'checklist' | 'stall' | None. recent_round_claims — по завершённым раундам."""
    if current_round < 2 or current_round <= cooldown_round or open_issues:
        return None
    if items and all(i["status"] == "done" for i in items):
        spoken = [s for s in states.values()]
        if len(spoken) >= n_members and all(s.get("readiness") == "settled" for s in spoken):
            return "checklist"
    if stall_rounds and len(recent_round_claims) >= stall_rounds \
            and all(c == 0 for c in recent_round_claims[-stall_rounds:]):
        return "stall"
    return None


def asked_members(messages: list[dict], members: list[dict], since_seq_by_member: dict[int, int]) -> list[int]:
    """Участники, которых по имени спросили («@Имя: …») после их последней реплики."""
    by_name = {m["name"].lower(): m["id"] for m in members}
    asked: list[int] = []
    for m in messages:
        if m["kind"] != "member":
            continue
        st = meta_state(m)
        for q in (st or {}).get("questions", []) or []:
            if q.startswith("@") and ":" in q:
                name = q[1:q.index(":")].strip().lower()
                mid = by_name.get(name)
                if mid and mid != m["member_id"] and m["seq"] > since_seq_by_member.get(mid, 0) \
                        and mid not in asked:
                    asked.append(mid)
    return asked
