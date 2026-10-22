"""Оркестратор симпозиума: цикл «снимок состояния → fsm.decide → исполнение».

Сам не принимает решений о порядке — их принимает `fsm.decide`; сам не
ходит в БД напрямую — для этого `Store`; ход, голосование и секретарь —
отдельные раннеры. Здесь остаётся жизненный цикл: цикл событий, паузы и
ошибки, раунды, завершение, интервенции человека.
"""
from __future__ import annotations

import asyncio
import json

from .. import db
from ..auth import utcnow
from ..events import bus
from ..logging_setup import get_logger
from . import fsm
from .convergence import asked_members, round_claims, should_propose_end
from .secretary import Secretary
from .state import TurnState
from .store import Store, msg_public  # noqa: F401  (msg_public — публичный реэкспорт)
from .turn import TurnRunner
from .vote import VoteRunner

log = get_logger("engine")


class EngineStop(Exception):
    """Штатная остановка шага (пауза по ошибке/бюджету)."""


class SymposiumEngine:
    def __init__(self, symposium_id: int):
        self.sid = symposium_id
        self.store = Store(symposium_id)
        self.turns = TurnRunner(self)
        self.voting = VoteRunner(self)
        self.secretary = Secretary(self)
        self._wake = asyncio.Event()
        self._stopped = False

    # -------------------------------------------------------------- управление
    def wake(self) -> None:
        self._wake.set()

    def is_active(self) -> bool:
        row = self.store.load()
        return row is not None and row["status"] == "active" and not self._stopped

    async def run(self) -> None:
        log.info("автомат запущен для симпозиума %d", self.sid)
        try:
            while not self._stopped:
                row = self.store.load()
                if row is None:
                    return
                if row["status"] == "paused_error" and self._try_auto_recover(row):
                    continue
                if row["status"] != "active":
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    self._wake.clear()
                    continue
                try:
                    await self._step()
                except EngineStop:
                    pass
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("автомат %d упал: %s", self.sid, e)
            await self.error_pause("engine_crash", str(e))
        finally:
            log.info("автомат остановлен для симпозиума %d", self.sid)

    # ---------------------------------------------------------------------- шаг
    async def _step(self) -> None:
        store = self.store
        row = store.load()
        if row is None:
            return
        pending = TurnState.load(row["pending_turn"])
        if pending is not None and store.member(pending.member_id) is None:
            store.patch(pending_turn=None)
            pending = None
        if pending is None and not row["voting_state"]:
            self.flush_interventions()

        rnd = store.round(row["current_round"]) if row["current_round"] >= 1 else None
        profile = store.profile()
        snap = fsm.Snapshot(
            active=row["status"] == "active",
            has_profile=profile is not None,
            has_opening=db.query_one(
                "SELECT 1 FROM messages WHERE symposium_id=? AND kind='secretary'", (self.sid,)) is not None,
            voting=bool(row["voting_state"]),
            round_exists=rnd is not None,
            pending_member_id=pending.member_id if pending else None,
            next_speaker_id=self._next_speaker(rnd) if (rnd and pending is None) else None,
        )
        action = fsm.decide(snap)
        k = fsm.Kind
        if action.kind == k.PAUSE_NO_PROFILE:
            await self.error_pause(
                "no_profile", "No LLM API profile is configured for this symposium. "
                "Create one in the admin panel or select another profile.")
        elif action.kind == k.OPEN:
            await self.secretary.opening(profile)
        elif action.kind == k.VOTE:
            await self.voting.step()
        elif action.kind == k.ENSURE_ROUND:
            self._ensure_round()
        elif action.kind == k.RESUME_TURN:
            await self.turns.run(pending.member_id, pending.round_id)
        elif action.kind == k.START_TURN:
            ts = TurnState(member_id=action.member_id, round_id=rnd["id"])
            store.patch(pending_turn=ts.dump())
            await self.turns.run(action.member_id, rnd["id"])
        elif action.kind == k.CLOSE_ROUND:
            await self._close_round(rnd, profile)

    # ------------------------------------------------------------------ раунды
    def _ensure_round(self) -> dict:
        store = self.store
        number = store.load()["current_round"]
        if number < 1:
            store.patch(current_round=1)
            number = 1
        rnd = store.round(number)
        if rnd is None:
            rid = db.execute("INSERT INTO rounds(symposium_id, number, status, started_at) VALUES(?,?,?,?)",
                             (self.sid, number, "open", utcnow()))
            rnd = db.query_one("SELECT * FROM rounds WHERE id=?", (rid,))
            bus.publish(self.sid, {"type": "round_start", "round": number})
        return rnd

    def round_id_for_log(self) -> int | None:
        """Раунд для служебных записей (голоса, системные заметки)."""
        row = self.store.load()
        rnd = self.store.round(row["current_round"]) if row else None
        if rnd is None and row and row["current_round"] > 1:
            rnd = self.store.round(row["current_round"] - 1)
        return rnd["id"] if rnd else None

    def _spoken(self, round_id: int) -> set[int]:
        rows = db.query_all(
            "SELECT member_id FROM messages WHERE symposium_id=? AND round_id=? AND kind IN ('member','skip')",
            (self.sid, round_id))
        return {r["member_id"] for r in rows if r["member_id"]}

    def _next_speaker(self, rnd: dict) -> int | None:
        store = self.store
        row = store.load()
        members = store.speaking_members()
        priority = json.loads(row["next_round_priority"]) if row["next_round_priority"] else []
        spoken = self._spoken(rnd["id"])
        msgs = store.messages()
        last_own = {}
        for m in msgs:
            if m["kind"] in ("member", "skip") and m["member_id"]:
                last_own[m["member_id"]] = m["seq"]
        asked = asked_members(msgs, members, last_own)
        return fsm.pick_speaker([m["id"] for m in members], spoken, priority, asked, last_own)

    async def _close_round(self, rnd: dict, profile: dict) -> None:
        store = self.store
        db.execute("UPDATE rounds SET status='done', ended_at=? WHERE id=?", (utcnow(), rnd["id"]))
        row = store.load()
        keep_priority = row["cooldown_round"] >= rnd["number"]
        store.patch(current_round=rnd["number"] + 1,
                    **({} if keep_priority else {"next_round_priority": "[]"}))
        # сначала чек-лист пересчитан ходами; дайджест — детерминированный
        await self.secretary.close_round(rnd, profile)
        bus.publish(self.sid, {"type": "round_end", "round": rnd["number"]})
        log.info("symposium %d: раунд %d завершён", self.sid, rnd["number"])
        if store.load()["voting_state"]:
            return
        max_rounds = store.max_rounds()
        proposal = json.loads(row["end_proposal"]) if row.get("end_proposal") else None
        if proposal:
            store.patch(end_proposal=None)
            if max_rounds and rnd["number"] >= max_rounds:
                pass                                   # итоговое голосование ниже поглотит предложение
            elif self.voting.request("member", proposal["member_id"], f"{proposal['name']} proposes to conclude"):
                return
        if max_rounds and rnd["number"] >= max_rounds:
            self.voting.request("round_limit", None, f"round limit ({max_rounds}) reached")
            return
        msgs = store.messages()
        done_rounds = [r for r in store.rounds() if r["status"] == "done"]
        reason = should_propose_end(
            current_round=rnd["number"] + 1, cooldown_round=row["cooldown_round"],
            items=store.checklist(), open_issues=len(store.issues(only_open=True)),
            states=store.speaking_states(), n_members=len(store.speaking_members()),
            recent_round_claims=[round_claims(msgs, r["id"]) for r in done_rounds],
            stall_rounds=store.stall_rounds())
        if reason == "checklist":
            self.voting.request("checklist", None, "the completion checklist is closed")
        elif reason == "stall":
            self.voting.request("stall", None, "no new claims in recent rounds")

    # ------------------------------------------------------------- голосование
    def request_vote(self, kind: str, initiator_id: int | None = None, reason: str = "") -> bool:
        return self.voting.request(kind, initiator_id, reason)

    # --------------------------------------------------------------- завершение
    async def finish(self, end_reason: str, text: str) -> None:
        """Штатное завершение: заметка, статус, итоговый отчёт."""
        store = self.store
        store.add_message("system", f"[SYMPOSIUM ENDED: {text}]", round_id=self.round_id_for_log())
        store.patch(status="finished", end_reason=end_reason, voting_state=None, pending_turn=None)
        bus.publish(self.sid, {"type": "status", "status": "finished", "end_reason": end_reason})
        log.info("symposium %d завершён: %s", self.sid, text)
        await self.secretary.final_report(store.profile(), ending=text)

    # ------------------------------------------------------------- интервенции
    def flush_interventions(self) -> None:
        store = self.store
        row = store.load()
        queue = json.loads(row["pending_interventions"]) if row["pending_interventions"] else []
        if not queue:
            return
        rnd = store.round(row["current_round"])
        for text in queue:
            store.add_message("human", text, round_id=rnd["id"] if rnd else None)
        store.patch(pending_interventions="[]")

    _flush_interventions = flush_interventions  # имя из API v0.3

    def queue_intervention(self, text: str) -> None:
        row = self.store.load()
        queue = json.loads(row["pending_interventions"]) if row["pending_interventions"] else []
        queue.append(text)
        self.store.patch(pending_interventions=json.dumps(queue, ensure_ascii=False))

    # -------------------------------------------------------------- пауза/ошибки
    async def error_pause(self, kind: str, message: str, member_id: int | None = None, stage: str = "") -> None:
        """Пауза с ошибкой. Поздняя ошибка не «воскрешает» завершённый симпозиум."""
        store = self.store
        row = store.load()
        if row is None or row["status"] == "finished":
            return
        from datetime import datetime, timedelta, timezone
        attempts = row["recovery_attempts"] or 0
        delays = self._delays()
        delay = delays[min(attempts, len(delays) - 1)]
        info = {"type": kind, "message": message, "stage": stage, "member_id": member_id,
                "round": row["current_round"], "at": utcnow(),
                "recoverable": kind in self.RECOVERABLE,
                "retry_at": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()}
        store.patch(status="paused_error", error_info=json.dumps(info, ensure_ascii=False),
                    pause_reason=f"error:{kind}")
        rid = self.round_id_for_log()
        if rid:
            store.add_message("system", f"[ERROR: {kind}]", round_id=rid)
        bus.publish(self.sid, {"type": "error", "error": info, "status": "paused_error"})
        log.error("symposium %d на паузе из-за ошибки (%s): %s", self.sid, kind, message)

    # ----------------------------------------------------- самовосстановление
    # некритичные сбои: симпозиум сам возвращается к работе после паузы с
    # нарастающей задержкой; при недоступности основного профиля — переходит на
    # запасной (если задан). Критичные (нет профиля, исчерпана квота, 402,
    # 4xx от провайдера, токен-бюджет) требуют решения человека.
    RECOVERABLE = {"truncated", "empty_reply", "invalid_vote", "turn_loop", "llm_unavailable",
                   "llm_error", "engine_crash"}
    RECOVERY_DELAYS = (15, 45, 120, 300, 600)

    def _delays(self) -> tuple[float, ...]:
        raw = str(db.get_setting("auto_recovery_delays", "") or "")
        try:
            vals = tuple(float(x) for x in raw.split(",") if x.strip())
        except ValueError:
            vals = ()
        return vals or self.RECOVERY_DELAYS

    def _try_auto_recover(self, row: dict) -> bool:
        if str(db.get_setting("auto_recovery", "1")) not in ("1", "true", "True"):
            return False
        try:
            info = json.loads(row["error_info"]) if row["error_info"] else {}
        except ValueError:
            return False
        if not info.get("recoverable"):
            return False
        limit = int(db.get_setting("auto_recovery_max", 3) or 0)
        attempts = row["recovery_attempts"] or 0
        if attempts >= limit:
            return False
        from datetime import datetime, timezone
        due = info.get("retry_at")
        if due and datetime.now(timezone.utc) < datetime.fromisoformat(due):
            return False
        store = self.store
        note = f"[AUTO-RECOVERY {attempts + 1}/{limit} after {info.get('type')}]"
        patch: dict = {"status": "active", "error_info": None, "pause_reason": None,
                       "recovery_attempts": attempts + 1}
        fb = row["fallback_profile_id"]
        if fb and info.get("type") in ("llm_unavailable", "llm_error") and row["api_profile_id"] != fb:
            if db.query_one("SELECT 1 FROM api_profiles WHERE id=?", (fb,)):
                patch["api_profile_id"] = fb
                note += " — switched to the fallback LLM profile"
        store.patch(**patch)
        store.add_message("system", note, round_id=self.round_id_for_log())
        bus.publish(self.sid, {"type": "state", "status": "active", "recovery": attempts + 1})
        log.warning("symposium %d: автовосстановление %d/%d (%s)", self.sid, attempts + 1, limit, info.get("type"))
        return True

    async def check_budget(self) -> bool:
        """Токен-бюджет симпозиума; False — симпозиум поставлен на паузу."""
        row = self.store.load()
        limit = row["token_limit"] if row else None
        if not limit:
            return True
        used = db.query_one(
            "SELECT COALESCE(SUM(tokens_in),0) AS ti, COALESCE(SUM(tokens_out),0) AS to_ "
            "FROM token_usage WHERE symposium_id=?", (self.sid,))
        total = (used["ti"] or 0) + (used["to_"] or 0)
        if total >= int(limit):
            await self.error_pause(
                "token_limit", f"The token budget of this symposium ({limit}) is exhausted ({total} used). "
                "Raise the limit in symposium settings or stop here.", stage="budget")
            return False
        return True
