"""Исполнение одного хода участника (протокол v2).

Ход = цикл «собрать контекст → вызвать модель → разобрать → (вызвать
субагентов | потребовать <state> | принять)». Все корректирующие просьбы
идут через один механизм (`_nudge`), а итоги хода применяются отдельной
функцией (`_apply_effects`): чек-лист, возражения, запрос завершения.

Принцип v0.4: формат НЕ останавливает симпозиум. Нет <state> → одна
корректирующая просьба, затем реплика принимается с пометкой
meta.flags.state_missing. Пауза — только при ошибке LLM или пустом ответе.
"""
from __future__ import annotations

import json

from .. import db
from ..events import bus
from ..llm.client import LLMPersistentError
from ..logging_setup import get_logger
from ..subagents import base as agents
from . import context as ctx
from .convergence import checklist_consensus
from .llmcall import member_chat
from .protocol import nudges, parse_reply, repetition
from .state import TurnState

log = get_logger("engine.turn")

MAX_TURN_ITERATIONS = 15


class TurnRunner:
    def __init__(self, eng):
        self.eng = eng
        self.store = eng.store

    # ------------------------------------------------------------------ вход
    async def run(self, member_id: int, round_id: int) -> None:
        store = self.store
        row = store.load()
        member = store.member(member_id)
        if row is None or member is None:
            return
        ts = TurnState.load(row["pending_turn"])
        if ts is None or ts.member_id != member_id or ts.round_id != round_id:
            ts = TurnState(member_id=member_id, round_id=round_id)
        store.patch(pending_turn=ts.dump())
        bus.publish(store.sid, {"type": "turn_start", "member_id": member_id, "round_id": round_id})

        try:
            outcome = await self._loop(ts)
        except LLMPersistentError as e:
            await self.eng.error_pause(e.kind, str(e), member_id=member_id)
            return
        if outcome == "done":
            store.patch(pending_turn=None)
            bus.publish(store.sid, {"type": "turn_end", "member_id": member_id})

    # ------------------------------------------------------------- основной цикл
    async def _loop(self, ts: TurnState) -> str:
        store = self.store
        while True:
            if not self.eng.is_active():
                return "interrupted"
            ts.iterations += 1
            if ts.iterations > MAX_TURN_ITERATIONS:
                await self.eng.error_pause("turn_loop", "turn exceeded the iteration limit", member_id=ts.member_id)
                return "error"
            if ts.hitl_pending:
                state = self._resume_hitl(ts)
                if state == "waiting":
                    return "waiting"
            if not await self.eng.check_budget():
                return "interrupted"

            row = store.load()
            member = store.member(ts.member_id)
            members = store.members()                  # все (имена для истории)
            names = [m["name"] for m in members]

            system = ctx.build_member_system_prompt(member, row, store.speaking_members(), store.subagent_limit(member), store.human())
            messages = self._messages(row, member, members, ts)
            reply_budget = int(member["max_tokens"] * ts.boost)

            def on_delta(piece: str, _mid=member["id"]) -> None:
                bus.publish(store.sid, {"type": "delta", "member_id": _mid, "text": piece})

            def on_think(piece: str, _mid=member["id"]) -> None:
                bus.publish(store.sid, {"type": "thinking_delta", "member_id": _mid, "text": piece})

            result, used_profile, fell_back = await member_chat(
                store, member, row, system=system, messages=messages, temperature=member["temperature"],
                max_tokens=reply_budget, call_type="turn", symposium_id=store.sid,
                member_id=member["id"], round_id=ts.round_id, on_delta=on_delta, on_thinking_delta=on_think)
            if not self.eng.is_active():
                store.patch(pending_turn=ts.dump())
                return "interrupted"

            raw = result.content
            reply = parse_reply(raw, names)
            flags: dict = {}
            if fell_back:
                flags["fallback_profile"] = used_profile["name"]

            # ---------- вызовы субагентов
            if reply.kind == "calls":
                outcome = await self._handle_calls(ts, member, row, used_profile, raw, reply)
                store.patch(pending_turn=ts.dump())
                if outcome == "waiting":
                    return "waiting"
                continue

            # ---------- обрезка по лимиту: просьба → увеличенный бюджет → мягкое принятие
            truncated = result.finish_reason == "length"
            if truncated and (reply.state_missing or reply.state_unclosed):
                if store.strict() and ts.nudge_count("truncation") < 1:
                    self._nudge(ts, raw, nudges.truncated(member["max_tokens"]), "truncation")
                    continue
                if ts.boost < 2.0 and ts.nudge_count("boost") < 1:
                    ts.bump("boost")
                    ts.boost = 2.0
                    store.patch(pending_turn=ts.dump())
                    log.info("symposium %d: ответ %s обрезан, повтор с бюджетом x2", store.sid, member["name"])
                    continue
                trimmed = repetition.trim_to_last_sentence(reply.speech)
                if not trimmed and reply.speech.strip():
                    trimmed = reply.speech.strip().rstrip(",;:—- ") + " …"
                if not trimmed:
                    await self.eng.error_pause(
                        "truncated", f"reply cut off by the {reply_budget}-token limit and nothing usable was produced",
                        member_id=ts.member_id)
                    return "error"
                reply.speech = trimmed
                flags["truncated"] = True

            # ---------- формат: пустой ответ (часто — мышление съело весь бюджет)
            if reply.kind == "invalid" and not ts.speech_cache:
                if ts.boost < 2.0 and ts.nudge_count("boost") < 1:
                    ts.bump("boost")
                    ts.boost = 2.0
                    store.patch(pending_turn=ts.dump())
                    continue
                if ts.nudge_count("empty") < 1:
                    self._nudge(ts, raw or "(empty)", nudges.empty_reply(), "empty")
                    continue
                await self.eng.error_pause("empty_reply", "the model returned an empty reply",
                                           member_id=ts.member_id)
                return "error"

            # ---------- досылка <state> после просьбы
            if ts.speech_cache:
                if not reply.state_missing and len(reply.speech) < 25:
                    reply.speech = ts.speech_cache
                    reply.kind = "speech" if reply.state.action != "skip" else "skip"
                elif reply.state_missing and not reply.speech:
                    reply.speech, reply.kind = ts.speech_cache, "speech"
                ts.speech_cache = ""

            # ---------- формат: нет <state>
            if reply.kind in ("speech", "end") and (reply.state_missing or reply.state_unclosed):
                if store.strict() and ts.nudge_count("state") < 1:
                    ts.speech_cache = reply.speech
                    self._nudge(ts, raw, nudges.state_unclosed() if reply.state_unclosed
                                else nudges.state_missing(), "state")
                    continue
                flags["state_missing"] = True

            # ---------- повтор собственной реплики
            if reply.kind == "speech" and row["current_round"] > 1:
                own = [m for m in store.messages() if m["kind"] == "member" and m["member_id"] == member["id"]]
                ratio, rnd = repetition.find(reply.speech, own)
                if ratio >= repetition.REPETITION_THRESHOLD:
                    if ts.nudge_count("repetition") < 1:
                        others = self._others_since(own, members)
                        ts.speech_cache = ""
                        self._nudge(ts, raw, nudges.repetition(self._round_no(rnd), ratio, others,
                                                               member["max_tokens"]), "repetition")
                        continue
                    flags["repetition"] = round(ratio, 2)

            self._record(ts, member, row, reply, result, flags)
            return "done"

    # ------------------------------------------------------------------ запись
    def _record(self, ts: TurnState, member: dict, row: dict, reply, result, flags: dict) -> None:
        store = self.store
        st = reply.state
        if reply.kind == "skip":
            store.add_message("skip", "", member_id=member["id"], round_id=ts.round_id,
                              tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                              model=result.model, meta={"state": st.public(), "flags": flags},
                              tokens_thinking=result.tokens_thinking)
            return
        meta = {"state": st.public(), "state_rendered": st.render(), "flags": flags}
        msg = store.add_message(
            "member", reply.speech, member_id=member["id"], round_id=ts.round_id,
            thinking=result.thinking or None, tokens_in=result.tokens_in,
            tokens_out=result.tokens_out, model=result.model, meta=meta,
            tokens_thinking=result.tokens_thinking)
        self._apply_effects(msg, member, row, reply)

    def _apply_effects(self, msg: dict, member: dict, row: dict, reply) -> None:
        """Итоги хода: возражения, чек-лист, запрос на завершение."""
        store = self.store
        st = reply.state
        round_no = row["current_round"]
        changed = False
        for ref, status in st.issues.items():
            issue = next((i for i in store.issues() if i["ref"] == ref), None)
            if status == "resolved" and issue and issue["owner_member_id"] == member["id"]:
                changed |= store.resolve_issue(ref, round_no)
        items = store.checklist()
        if items:
            consensus = checklist_consensus(items, store.speaking_states(), len(store.speaking_members()))
            for ref, status in consensus.items():
                changed |= store.set_checklist_status(ref, status, round_no)
        if changed:
            store.broadcast_registries()
        if reply.propose_end:
            # предложение откладывается до конца раунда: выступают ВСЕ участники
            store.patch(end_proposal=json.dumps({"member_id": member["id"], "name": member["name"],
                                                 "round": round_no}, ensure_ascii=False))

    # ------------------------------------------------------------------ контекст
    def _messages(self, row: dict, member: dict, members: list[dict], ts: TurnState) -> list[dict]:
        store = self.store
        all_messages = store.messages()
        rounds = store.rounds()
        meta = ctx.build_runtime_meta(row, row["current_round"], store.rag_folder_names())
        proj = ctx.build_projection(member, row, members, rounds, all_messages,
                                    store.compression_n(), meta)  # members — все: имена старых реплик
        briefing = ctx.build_turn_briefing(
            member, members, all_messages, current_round=row["current_round"],
            checklist=store.checklist(), issues=store.issues(), max_rounds=store.max_rounds())
        if briefing:
            proj[-1] = {"role": "user", "content": proj[-1]["content"] + "\n\n" + briefing}
        return proj + list(ts.history)

    def _nudge(self, ts: TurnState, assistant_raw: str, text: str, key: str) -> None:
        ts.bump(key)
        ts.history.append({"role": "assistant", "content": assistant_raw})
        ts.history.append({"role": "user", "content": text})
        self.store.patch(pending_turn=ts.dump())

    def _others_since(self, own: list[dict], members: list[dict]) -> list[str]:
        names = {m["id"]: m["name"] for m in members}
        last = own[-1]["seq"] if own else 0
        out = []
        for m in self.store.messages():
            if m["seq"] > last and m["kind"] == "member" and names.get(m["member_id"]):
                out.append(names[m["member_id"]])
        return out

    def _round_no(self, round_id: int | None) -> int | str:
        r = db.query_one("SELECT number FROM rounds WHERE id=?", (round_id,)) if round_id else None
        return r["number"] if r else "?"

    # ------------------------------------------------------------- субагенты
    async def _handle_calls(self, ts: TurnState, member: dict, row: dict, profile,
                            raw: str, reply) -> str:
        store = self.store
        limit = store.subagent_limit(member)
        allowed = self._allowed_tools(member)
        ts.history.append({"role": "assistant", "content": raw})
        lines: list[str] = []
        errors: list[str] = []
        executed = 0
        for call in reply.calls:
            if call.error:
                errors.append(call.error)
                lines.append(f"[call {call.agent} not executed: {call.error}]")
                continue
            if call.agent not in allowed:
                errors.append(f"agent {call.agent} is not permitted for you")
                lines.append(f"[call {call.agent} not executed: this tool is not permitted for you; "
                             f"allowed: {', '.join(allowed) or 'none'}]")
                continue
            if ts.calls_used >= limit:
                lines.append(f"[call {call.agent} not executed: limit of {limit} calls per turn reached]")
                continue
            if call.agent == "ask_human":
                ts.calls_used += 1
                req = store.create_hitl(
                    member["id"], ts.round_id, str(call.params.get("question") or "").strip() or "(no question)",
                    call.params.get("options") or [], str(call.params.get("context") or ""))
                ts.hitl_pending = req
                store.patch(status="waiting_human", pending_turn=ts.dump())
                bus.publish(store.sid, {"type": "hitl_request", "request_id": req, "member_id": member["id"]})
                return "waiting"
            ts.calls_used += 1
            executed += 1
            bus.publish(store.sid, {"type": "subagent_start", "member_id": member["id"], "agent": call.agent})
            actx = agents.AgentContext(symposium=row, member=member, round_id=ts.round_id,
                                       profile=store.profile_for(member) or store.profile(), language=row["language"])
            result = await agents.run(call.agent, actx, call.params)
            lines.append(self._evidence_from(member, ts, call, result))
        if executed == 0 and errors and len(errors) == len(reply.calls):
            ts.history.append({"role": "user", "content": nudges.call_errors(errors)})
        elif ts.calls_used >= limit and executed == 0:
            ts.history.append({"role": "user", "content": nudges.call_limit(limit)})
        else:
            ts.history.append({"role": "user", "content": nudges.evidence_results(lines)})
        return "continue"

    @staticmethod
    def _allowed_tools(member: dict) -> list[str]:
        from ..characters import ALL_TOOLS
        raw = member.get("tools")
        if not raw:
            return list(ALL_TOOLS)
        try:
            return [t for t in json.loads(raw) if t in ALL_TOOLS]
        except ValueError:
            return list(ALL_TOOLS)

    def _evidence_from(self, member: dict, ts: TurnState, call, result) -> str:
        p = call.params
        label = (p.get("query") or p.get("question") or p.get("command") or p.get("filename")
                 or (p.get("code") or "")[:80] or call.agent)
        digest = result.digest if result.ok else f"ERROR: {result.error}"
        ev = self.store.add_evidence(member_id=member["id"], round_id=ts.round_id, agent=call.agent,
                                     query=str(label), digest=digest, ok=result.ok, raw_path=result.raw_path)
        self.store.add_message("evidence", digest, member_id=member["id"], round_id=ts.round_id,
                               tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                               meta={"ref": ev["ref"], "agent": call.agent, "query": str(label)[:300],
                                     "ok": result.ok})
        return f"[{ev['ref']}] {call.agent}: {digest}"

    def _resume_hitl(self, ts: TurnState) -> str:
        store = self.store
        req = store.hitl(ts.hitl_pending)
        if req is None or req["status"] == "pending":
            store.patch(status="waiting_human", pending_turn=ts.dump())
            return "waiting"
        member = store.member(ts.member_id)
        if req["status"] == "answered":
            answer = req["answer"] or ""
            ev = store.add_evidence(member_id=member["id"], round_id=ts.round_id, agent="ask_human",
                                    query=req["question"], digest=f"Human answered: {answer}", ok=True)
            store.add_message("evidence", f"Human answered: {answer}", member_id=member["id"],
                              round_id=ts.round_id,
                              meta={"ref": ev["ref"], "agent": "ask_human", "query": req["question"][:300], "ok": True})
            ts.history.append({"role": "user", "content": nudges.evidence_results(
                [f"[{ev['ref']}] ask_human: Human answered: {answer}"])})
        else:
            ts.history.append({"role": "user", "content": "The human did not answer (question cancelled). "
                               "Proceed without it; write your reply and the <state> block."})
        ts.hitl_pending = None
        store.patch(pending_turn=ts.dump())
        return "resumed"
