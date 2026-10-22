"""Секретарь: открытие (заметка + чек-лист), дайджест раунда, итоговый отчёт.

Дайджест раунда по умолчанию собирается БЕЗ вызова LLM — из блоков <state>
(`context.round_digest`); режим `summary_mode=llm` возвращает LLM-саммари
v0.3 (дороже, но «человечнее»). На бесплатных тарифах с лимитом запросов
сэкономленные вызовы — это дополнительные ходы участников.
"""
from __future__ import annotations

import asyncio
import re

from .. import db
from ..auth import utcnow
from ..events import bus
from ..llm import client as llm_client
from ..llm.client import LLMPersistentError
from ..logging_setup import get_logger
from ..seeds import active_system_prompt
from . import context as ctx

log = get_logger("engine.secretary")

_CHECKLIST_RE = re.compile(r"<checklist>(.*?)</checklist\s*>", re.DOTALL | re.IGNORECASE)
_ITEM_RE = re.compile(r"^\s*[-*•]?\s*C?\d+\s*[:.)-]\s*(.+?)\s*$", re.MULTILINE)


def parse_opening(text: str) -> tuple[str, list[str]]:
    """Ответ секретаря → (публичная заметка, пункты чек-листа)."""
    m = _CHECKLIST_RE.search(text or "")
    items: list[str] = []
    note = (text or "").strip()
    if m:
        items = [i.strip() for i in _ITEM_RE.findall(m.group(1)) if i.strip()][:8]
        note = (text[:m.start()] + text[m.end():]).strip()
    return note, items


class Secretary:
    def __init__(self, eng):
        self.eng = eng
        self.store = eng.store

    async def _call(self, profile: dict, system: str, messages: list[dict], max_tokens: int,
                    call_type: str, round_id: int | None = None):
        # секретарю мышление не нужно: «off» (у моделей, не умеющих его выключать,
        # клиент сам добавляет запас к лимиту запроса — рассуждения не съедят ответ)
        return await llm_client.chat(
            profile, system=system, messages=messages, temperature=0.3, max_tokens=max_tokens,
            call_type=call_type, symposium_id=self.store.sid, round_id=round_id,
            thinking_mode="off", thinking_depth=None)

    # ----------------------------------------------------------- открытие
    async def opening(self, profile: dict) -> None:
        """Открытие: заметка + чек-лист. Пустой ответ модели (бюджет съело
        мышление) → повтор с удвоенным бюджетом → детерминированное открытие
        без LLM: симпозиум никогда не остаётся без открытия и чек-листа."""
        store = self.store
        row = store.load()
        note, items, result = "", [], None
        for attempt in (1, 2):
            try:
                result = await self._call(
                    profile, active_system_prompt("secretary_open"),
                    ctx.build_secretary_open_messages(row, store.members()), 1200 * attempt, "secretary")
            except LLMPersistentError as e:
                await self.eng.error_pause(e.kind, str(e))
                return
            note, items = parse_opening(result.content)
            if note.strip():
                break
            log.warning("symposium %d: секретарь вернул пустое открытие (попытка %d)", store.sid, attempt)
        meta: dict = {}
        if not note.strip():
            note = self._fallback_note(row, store.speaking_members())
            meta["fallback"] = True
        if not items and (row["completion_criterion"] or "").strip():
            items = [row["completion_criterion"].strip()]
        meta["checklist"] = items
        store.add_message("secretary", note, tokens_in=result.tokens_in if result else 0,
                          tokens_out=result.tokens_out if result else 0,
                          model=result.model if result else None, meta=meta)
        store.set_checklist(items)

    @staticmethod
    def _fallback_note(row: dict, members: list[dict]) -> str:
        ru = (row["language"] or "").strip().lower() in ("ru", "русский", "russian", "рус")
        who = "; ".join(f"{m['name']} — {m['role_name']}" for m in members)
        if ru:
            return (f"Симпозиум открыт. Тема: {row['topic']}\nКритерий завершения: "
                    f"{row['completion_criterion'] or 'не задан'}\nФормат отчёта: {row['report_format'] or 'свободный'}\n"
                    f"Участники: {who}")
        return (f"Symposium opened. Topic: {row['topic']}\nCompletion criterion: "
                f"{row['completion_criterion'] or 'not specified'}\nReport format: "
                f"{row['report_format'] or 'free form'}\nParticipants: {who}")

    async def regenerate_checklist(self, profile: dict) -> list[str]:
        """Пересоставить чек-лист по текущему критерию (после правки критерия)."""
        store = self.store
        row = store.load()
        criterion = (row["completion_criterion"] or "").strip()
        items: list[str] = []
        if criterion:
            try:
                result = await self._call(
                    profile, active_system_prompt("secretary_open"),
                    ctx.build_secretary_open_messages(row, store.members()), 1200, "secretary")
                _, items = parse_opening(result.content)
            except Exception as e:  # noqa: BLE001
                log.warning("пересоставление чек-листа не удалось: %s", e)
            if not items:
                items = [criterion]
        store.set_checklist(items)
        return items

    # --------------------------------------------------------- дайджест раунда
    async def close_round(self, rnd: dict, profile: dict) -> None:
        store = self.store
        names = {m["id"]: m["name"] for m in store.members()}
        msgs = [m for m in store.messages() if m["round_id"] == rnd["id"]]
        digest = ctx.round_digest(rnd["number"], msgs, names, store.checklist(), store.issues())
        db.execute("UPDATE rounds SET summary=?, summary_model='digest', summarized_at=? WHERE id=?",
                   (digest, utcnow(), rnd["id"]))
        bus.publish(store.sid, {"type": "summary", "round": rnd["number"], "summary": digest})
        if store.summary_mode() == "llm":
            asyncio.create_task(self._llm_summary(rnd, profile, msgs, names))

    async def _llm_summary(self, rnd: dict, profile: dict, msgs: list[dict], names: dict) -> None:
        store = self.store
        try:
            result = await self._call(
                profile, active_system_prompt("secretary_summarize"),
                ctx.build_secretary_summarize_messages(
                    store.load(), ctx.round_transcript_text(msgs, names)), 700, "summary", rnd["id"])
        except Exception as e:  # noqa: BLE001 — дайджест уже есть, LLM-версия опциональна
            log.warning("LLM-саммари раунда %s не получено: %s", rnd["number"], e)
            return
        db.execute("UPDATE rounds SET summary=?, summary_model=?, summary_tokens_in=?, "
                   "summary_tokens_out=?, summarized_at=? WHERE id=?",
                   (result.content.strip(), result.model, result.tokens_in, result.tokens_out,
                    utcnow(), rnd["id"]))
        bus.publish(store.sid, {"type": "summary", "round": rnd["number"], "summary": result.content.strip()})

    # --------------------------------------------------------------- отчёт
    async def final_report(self, profile: dict | None, ending: str = "stopped by the human") -> None:
        store = self.store
        row = store.load()
        profile = profile or store.profile()
        if row is None or profile is None:
            return
        system = active_system_prompt("secretary_report").replace("{report_format}", row["report_format"] or "free form")
        try:
            result = await self._call(
                profile, system,
                ctx.build_secretary_report_messages(
                    row, store.rounds(), store.messages(), store.members(), store.checklist(),
                    store.issues(), store.evidence(), ending), 3000, "report")
        except Exception as e:  # noqa: BLE001
            log.error("итоговый отчёт не получен: %s", e)
            store.add_message("system", f"[ERROR: report_failed] {e}")
            return
        store.add_message("report", result.content.strip(), tokens_in=result.tokens_in,
                          tokens_out=result.tokens_out, model=result.model)
