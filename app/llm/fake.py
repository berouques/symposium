"""Детерминированная поддельная LLM для тестов и офлайн-демо (протокол v2).

Включается профилем, у которого модель начинается с «fake:». Поведение
выбирается суффиксом:

  fake:basic        участники говорят по протоколу v2; в раунде 3 первый
                    предлагает завершение, все соглашаются
  fake:veto         Beta возражает против первого предложения завершить, затем
                    снимает возражение и соглашается
  fake:stubborn     Beta возражает всегда (проверка лимита раундов)
  fake:askhuman     Alpha в раунде 2 вызывает ask_human
  fake:subagents    Alpha в раунде 1 вызывает web_explorer и coder в ОДНОМ сообщении
  fake:rag          Alpha в раунде 1 вызывает archivarius
  fake:skip         участники иногда пропускают ход
  fake:error        в раунде 2 — ошибка провайдера (пауза)
  fake:nostate      первая реплика без <state> (проверка досылки блока)
  fake:nostatepersistent  <state> не присылается никогда (реплика принимается с флагом)
  fake:truncated    первая реплика обрезана по лимиту до <state>
  fake:truncpersistent  обрезана всегда (мягко принимается усечённой)
  fake:repeat       со 2-го раунда повторяет себя (проверка просьбы), затем исправляется
  fake:repeatpersistent  повторяет и после просьбы (принимается с флагом)
  fake:badvote      первый бюллетень невалиден (проверка повторного запроса)
  fake:stall        новых утверждений нет (проверка автозапуска по застою)
  fake:emptyopen    секретарь возвращает пустое открытие (проверка запасного открытия)
  fake:flaky        один сбой провайдера в раунде 2, после автовосстановления всё работает

Вызовы fake обходят лимитер и сеть не трогают.
"""
from __future__ import annotations

import re


class FakeLLMError(Exception):
    """Simulated provider failure."""


_SENTENCES = [
    "The mechanism relies on feedback between the participants, so each added constraint changes the equilibrium.",
    "A counterexample from historical practice shows the claim only holds under stable institutions.",
    "Measured against the stated criterion, the weakest link is the assumption about data quality.",
    "If we separate the descriptive question from the normative one, most of the disagreement dissolves.",
    "Two competing definitions are in play here, and the argument silently switches between them.",
    "The cost of verification grows faster than the benefit, which favours a staged approach.",
    "An independent source would settle this; absent that, the claim should be marked as an assumption.",
    "The strongest objection concerns scope: the result is local and should not be generalised.",
]


def _member_name(system: str) -> str:
    m = re.search(r"You are ([^.\n]+)\.", system)
    return m.group(1).strip() if m else "Member"


def _round_number(messages: list[dict]) -> int:
    for msg in reversed(messages):
        m = re.search(r"Round: (\d+)", msg.get("content", "") or "")
        if m:
            return int(m.group(1))
    return 1


def _assistant_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "assistant")


def _all_text(messages: list[dict]) -> str:
    return "\n".join(m.get("content", "") or "" for m in messages)


def _others(system: str, me: str) -> list[str]:
    names = re.findall(r"^- ([^:\n]+):", system.split("Participants in this symposium:")[-1].split("You are")[0],
                       re.MULTILINE) if "Participants in this symposium:" in system else []
    return [n.strip() for n in names if n.strip() != me]


def fake_finish_reason(model: str, system: str = "", messages: list[dict] | None = None) -> str:
    persona = model.split(":", 1)[1] if ":" in model else "basic"
    if persona == "truncpersistent":
        return "length"
    if persona == "truncated" and messages is not None:
        if "LENGTH LIMIT" not in _all_text(messages) and _round_number(messages) == 1:
            return "length"
    return "stop"


def _state(name: str, rnd: int, system: str, messages: list[dict], *, settled: bool,
           propose: bool = False, claims: bool = True, evidence: str = "", action: str = "speak") -> str:
    text = _all_text(messages)
    others = _others(system, name)
    lines = [f"action: {'propose_end' if propose else action}"]
    if rnd > 1 and others:
        lines.append(f"reply_to: {others[0]}")
        lines.append(f"stance: {others[0]}=refine")
    if claims:
        lines.append(f"claims: {name} claim for round {rnd} | a second point of {name}")
    if rnd > 1 and others:
        lines.append(f"questions: @{others[0]}: what would falsify this?")
    if evidence:
        lines.append(f"evidence: {evidence}")
    refs = sorted(set(re.findall(r"\b(C\d+) \[", text)))
    if refs and rnd >= 2:
        lines.append("checklist: " + "; ".join(f"{r}={'done' if settled else 'open'}" for r in refs))
    issues = sorted(set(re.findall(r"• (I\d+) by", text)))
    if issues and name.startswith("Beta"):
        lines.append("issues: " + "; ".join(f"{i}=resolved" for i in issues))
    lines.append(f"readiness: {'settled' if settled else 'developing'}")
    return "<state>\n" + "\n".join(lines) + "\n</state>"


def _speech(name: str, rnd: int, idx: int) -> str:
    return f"[{name}, round {rnd}] " + _SENTENCES[(rnd * 3 + idx) % len(_SENTENCES)] + \
        f" {_SENTENCES[(rnd * 5 + idx + 3) % len(_SENTENCES)]}"


def fake_completion(system: str, messages: list[dict], model: str) -> str:
    persona = model.split(":", 1)[1] if ":" in model else "basic"
    name = _member_name(system)
    is_member_turn = "Turn protocol:" in system
    text = _all_text(messages)

    # ---------------- бюллетень
    if not is_member_turn and "CONFIRMATION IS NOW REQUESTED" in system:
        if persona == "badvote" and "VOTE FORMAT" not in text:
            return "I think it is fine, honestly."
        rejected_before = "[VOTE REJECTED" in text
        if persona == "stubborn" and name.startswith("Beta"):
            return ("<vote>object</vote><reason>The verified example is still missing.</reason>"
                    "<unresolved>no verified example</unresolved>"
                    "<satisfied_if>a working example is produced</satisfied_if>")
        if persona == "veto" and name.startswith("Beta") and not rejected_before:
            return ("<vote>object</vote><reason>The criterion asks for a verified example.</reason>"
                    "<unresolved>no verified example yet</unresolved>"
                    "<satisfied_if>one example is shown and checked</satisfied_if>")
        if name.startswith("Gamma") and persona in ("basic", "veto"):
            return "<vote>reservations</vote><reason>Fine with ending.</reason><unresolved>edge cases remain untested</unresolved>"
        return "<vote>agree</vote><reason>The criterion is met.</reason>"

    # ---------------- секретарь
    if "Open the symposium" in system:
        if persona == "emptyopen":
            return ""
        topic = "the topic"
        m = re.search(r"Symposium topic: (.+)", text)
        if m:
            topic = m.group(1).strip()
        return (f"Opening note. Topic: {topic}. Criterion and report format recorded.\n"
                "<checklist>\nC1: the main question is answered with arguments\n"
                "C2: objections are addressed\n</checklist>")
    if "Summarize the round" in system:
        return "Round summary (secretary): key points stated; open questions remain."
    if "final report" in system:
        return ("FINAL REPORT (secretary).\nConclusions: the topic was discussed and closed by vote.\n"
                "Dissent and open issues: see ballots.")

    # ---------------- дайджест субагента
    if not is_member_turn and "digest" in system.lower():
        return "Digest: relevant facts extracted, numbers preserved, sources kept."

    # ---------------- ход участника
    rnd = _round_number(messages)
    idx = {"Alpha": 0, "Beta": 1, "Gamma": 2}.get(name.split()[0], 3)
    first_in_symposium = _assistant_turns(messages) == 0

    if persona == "error" and rnd == 2:
        raise FakeLLMError("simulated provider failure (fake:error)")
    if persona == "flaky" and rnd == 2 and "[AUTO-RECOVERY" not in text:
        raise FakeLLMError("simulated transient failure (fake:flaky)")

    last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")

    if persona == "nostate" and rnd == 1 and first_in_symposium:
        return f"[{name}] A reasoned contribution that forgets the mandatory state block entirely."
    if persona == "nostate" and "Send ONLY the <state>" in last_user:
        return _state(name, rnd, system, messages, settled=False)
    if persona == "nostatepersistent":
        return _speech(name, rnd, idx)
    if persona == "truncated" and rnd == 1 and first_in_symposium and "LENGTH LIMIT" not in text:
        return ("A contribution that goes on and on and gets cut off by the token limit long before "
                "the state block could appear, and the cut lands right her")
    if persona == "truncated" and "LENGTH LIMIT" in last_user:
        return "In short: the position stands. " + _state(name, rnd, system, messages, settled=False)
    if persona == "truncpersistent" and rnd == 1:
        return ("A contribution that goes on and on and gets cut off by the token limit long before "
                "the state block could appear. The cut lands right her")

    if persona in ("repeat", "repeatpersistent") and rnd >= 2:
        own = [m for m in messages if m.get("role") == "assistant"]
        if own and ("REPETITION:" not in last_user or persona == "repeatpersistent"):
            return own[-1]["content"]
        if own:
            return (f"Responding to new points instead of repeating myself ({name}, round {rnd}): "
                    + _SENTENCES[(rnd * 7 + idx) % len(_SENTENCES)] + "\n" + _state(name, rnd, system, messages, settled=False))

    asked = "EVIDENCE RESULTS" in text
    if persona == "askhuman" and rnd == 2 and name.startswith("Alpha") and not asked:
        return ('<call agent="ask_human">{"question": "Should we prioritize simplicity or completeness?", '
                '"options": ["Simplicity first", "Completeness first"], "context": "round 3 depends on it"}</call>')
    if persona == "subagents" and rnd == 1 and name.startswith("Alpha") and not asked:
        return ('Let me check facts first.\n'
                '<call agent="web_explorer">best practices for structured LLM debates</call>\n'
                '<call agent="coder" language="python" filename="check.py">\nprint(2 + 2)\n</call>')
    if persona == "rag" and rnd == 1 and name.startswith("Alpha") and not asked:
        return '<call agent="archivarius">project requirements</call>'

    evidence = ""
    if asked:
        evidence = ", ".join(sorted(set(re.findall(r"\[(E\d+)\]", last_user)), key=lambda e: int(e[1:])))

    if persona == "skip" and (_assistant_turns(messages) % 3) == 2:
        return "<state>\naction: skip\n</state>"

    speech = _speech(name, rnd, idx) + (f" Evidence {evidence.split(',')[0]} supports this." if evidence else "")
    if persona == "stall" and rnd >= 2:
        return speech + "\n" + _state(name, rnd, system, messages, settled=False, claims=False)

    settled = rnd >= 3 and persona != "stubborn"
    propose = rnd >= 3 and name.startswith("Alpha") and persona in ("basic", "veto", "askhuman", "subagents",
                                                                      "rag", "skip", "badvote", "emptyopen", "flaky")
    return speech + "\n" + _state(name, rnd, system, messages, settled=settled, propose=propose, evidence=evidence)
