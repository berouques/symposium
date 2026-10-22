"""Подтверждение завершения: слепое голосование с градациями.

Отличия от v0.3: бюллетени НЕ видны друг другу до подсчёта (нет «стадности»),
три позиции (agree / reservations / object), возражение обязано назвать
нерешённое и что его снимет — оно превращается в запись реестра issues и
висит в брифинге, пока владелец не снимет его. Правило (unanimous /
supermajority / majority) настраивается на симпозиум. Голосование по лимиту
раундов — окончательное: симпозиум завершается при любом исходе.
"""
from __future__ import annotations

from ..events import bus
from .llmcall import member_chat
from ..llm.client import LLMPersistentError
from ..logging_setup import get_logger
from . import context as ctx
from .protocol import Ballot, nudges, parse_ballot
from .state import VoteState

log = get_logger("engine.vote")

POLICIES = ("unanimous", "supermajority", "majority")


def passes(policy: str, ballots: list[dict]) -> bool:
    """Исход по правилу. reservations засчитываются как «за»."""
    n = len(ballots)
    ok = sum(1 for b in ballots if b["position"] != "object")
    if policy == "majority":
        return ok * 2 > n
    if policy == "supermajority":
        return ok * 3 >= n * 2
    return ok == n


class VoteRunner:
    def __init__(self, eng):
        self.eng = eng
        self.store = eng.store

    # ------------------------------------------------------------- запуск
    def request(self, kind: str, initiator_id: int | None, reason: str) -> bool:
        """Начать голосование. Предложение участника отклоняется, пока висят
        открытые возражения или идёт «остывание» после отказа."""
        store = self.store
        row = store.load()
        if row is None or row["voting_state"]:
            return False
        if kind == "member" and (store.issues(only_open=True) or row["current_round"] <= row["cooldown_round"]):
            log.info("symposium %d: предложение завершить отклонено (возражения/остывание)", store.sid)
            return False
        vs = VoteState(kind=kind, initiator_id=initiator_id, reason=reason,
                       remaining=[m["id"] for m in store.speaking_members()])
        store.patch(voting_state=vs.dump())
        store.add_message("system", f"[CONFIRMATION REQUESTED: {reason}]", round_id=self.eng.round_id_for_log())
        log.info("symposium %d: голосование (%s): %s", store.sid, kind, reason)
        return True

    # --------------------------------------------------------------- шаг
    async def step(self) -> None:
        store = self.store
        vs = VoteState.load(store.load()["voting_state"])
        if vs is None:
            return
        if not vs.remaining:
            await self._tally(vs)
            return
        mid = vs.remaining[0]
        member = store.member(mid)
        if member is None:
            vs.remaining.pop(0)
            store.patch(voting_state=vs.dump())
            return
        if vs.kind == "member" and mid == vs.initiator_id:
            ballot = Ballot(ok=True, position="agree", reason="(proposer)")
        else:
            try:
                ballot = await self._ask(vs, member)
            except LLMPersistentError as e:
                await self.eng.error_pause(e.kind, str(e), member_id=mid)
                return
            if ballot is None:
                return
        vs.ballots[str(mid)] = ballot.public()
        vs.remaining.pop(0)
        vs.pending = None
        store.patch(voting_state=vs.dump())
        bus.publish(store.sid, {"type": "vote_progress", "done": len(vs.ballots),
                                "total": len(vs.ballots) + len(vs.remaining)})

    async def _ask(self, vs: VoteState, member: dict) -> Ballot | None:
        store = self.store
        row = store.load()
        members = store.members()
        speaking = store.speaking_members()
        if store.profile_for(member) is None:
            await self.eng.error_pause("no_profile", "no LLM profile configured", member_id=member["id"])
            return None
        history = list((vs.pending or {}).get("history", []))
        for attempt in range(2):
            if not await self.eng.check_budget() or not self.eng.is_active():
                return None
            meta = ctx.build_runtime_meta(row, row["current_round"], store.rag_folder_names())
            proj = ctx.build_projection(member, row, members, store.rounds(), store.messages(),
                                        store.compression_n(), meta)
            proj[-1] = {"role": "user", "content": proj[-1]["content"] + "\n\n" + self._info()}
            result, _p, _fb = await member_chat(
                store, member, row, thinking_override=("off", None),
                system=ctx.build_vote_system_prompt(member, row, speaking, vs.reason),
                messages=proj + history, temperature=0.2,
                max_tokens=int(store.setting("vote_max_tokens", 500)), call_type="vote",
                symposium_id=store.sid, member_id=member["id"], round_id=self.eng.round_id_for_log())
            ballot = parse_ballot(result.content)
            if ballot.ok:
                return ballot
            if attempt == 0:
                history += [{"role": "assistant", "content": result.content or "(empty)"},
                            {"role": "user", "content": nudges.vote_format()}]
                vs.pending = {"history": history}
                store.patch(voting_state=vs.dump())
        await self.eng.error_pause("invalid_vote", f"{member['name']} did not cast a valid ballot",
                                   member_id=member["id"])
        return None

    def _info(self) -> str:
        store = self.store
        lines = ["Status for your ballot:"]
        items = store.checklist()
        if items:
            lines.append("Checklist: " + "; ".join(f"{c['ref']} [{c['status']}] {c['text']}" for c in items))
        opens = store.issues(only_open=True)
        if opens:
            lines.append("Open objections: " + "; ".join(f"{i['ref']}: {i['text']}" for i in opens))
        if len(lines) == 1:
            lines.append("No checklist items and no open objections are recorded.")
        return "\n".join(lines)

    # ------------------------------------------------------------ подсчёт
    async def _tally(self, vs: VoteState) -> None:
        store = self.store
        row = store.load()
        names = {m["id"]: m["name"] for m in store.members()}
        rid = self.eng.round_id_for_log()
        ballots = []
        for key, b in vs.ballots.items():
            mid = int(key)
            ballots.append({**b, "member_id": mid})
            store.add_message("vote", b["reason"] or "", member_id=mid, round_id=rid, meta=b)
        policy = row["vote_policy"] or "unanimous"
        ok = passes(policy, ballots)
        objectors = [b for b in ballots if b["position"] == "object"]
        reservations = [b for b in ballots if b["position"] == "reservations"]
        tally = f"{len(ballots) - len(objectors)}/{len(ballots)}"
        bus.publish(store.sid, {"type": "vote_result", "passed": ok, "tally": tally, "policy": policy})

        if ok:
            note = f"{policy} vote passed ({tally})" + (f", {len(reservations)} with reservations" if reservations else "")
            await self.eng.finish("vote_passed", note)
            return
        if vs.kind == "round_limit":
            await self.eng.finish(
                "round_limit_rejected",
                f"round limit reached; the vote did not pass ({tally}) — closed with open objections")
            return
        refs = []
        for b in objectors:
            issue = store.open_issue(b["member_id"], b["unresolved"] or b["reason"], b["satisfied_if"],
                                     row["current_round"])
            refs.append(f"{issue['ref']} ({names.get(b['member_id'], '?')})")
        store.add_message(
            "system", f"[VOTE REJECTED: {tally}. Open objections: {', '.join(refs) or 'none'}]", round_id=rid)
        store.patch(voting_state=None, cooldown_round=row["current_round"],
                    next_round_priority=__import__("json").dumps([b["member_id"] for b in objectors]))
        store.broadcast_registries()
