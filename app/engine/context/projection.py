from __future__ import annotations

import json
import re

from ..convergence import meta_state
from .common import names_by_id, one_line

_ERROR_MSG_RE = re.compile(r"^\[ERROR:")


def _vote_line(msg: dict, names: dict[int, str]) -> str:
    who = names.get(msg["member_id"], "?")
    try:
        meta = json.loads(msg["meta"]) if msg.get("meta") else {}
    except ValueError:
        meta = {}
    pos = meta.get("position", "?")
    extra = f" — {one_line(msg['content'], 160)}" if msg.get("content") else ""
    return f"{who}: [confirmation ballot: {pos}]{extra}"


def _evidence_line(msg: dict, names: dict[int, str]) -> str:
    try:
        meta = json.loads(msg["meta"]) if msg.get("meta") else {}
    except ValueError:
        meta = {}
    who = names.get(msg["member_id"], "?")
    head = f"[{meta.get('ref', 'E?')} · {meta.get('agent', 'subagent')} · requested by {who}]"
    query = f" {one_line(meta.get('query', ''), 160)}\n" if meta.get("query") else "\n"
    return f"{head}{query}{msg['content']}"


def _own_assistant_text(msg: dict) -> str:
    """Своя реплика с каноническим <state>: модель видит образец формата."""
    content = msg["content"] or ""
    try:
        meta = json.loads(msg["meta"]) if msg.get("meta") else {}
    except ValueError:
        meta = {}
    raw = (meta.get("state_rendered") or "")
    return f"{content}\n{raw}".strip() if raw else content


def _round_lines(round_messages: list[dict], names: dict[int, str], self_id: int | None) -> list[dict]:
    out: list[dict] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "content": "\n".join(pending)})
            pending.clear()

    for msg in round_messages:
        kind, mid = msg["kind"], msg["member_id"]
        content = msg["content"] or ""
        if kind == "member":
            if mid == self_id:
                flush()
                out.append({"role": "assistant", "content": _own_assistant_text(msg)})
            else:
                pending.append(f"{names.get(mid, '?')}: {content}")
        elif kind == "skip":
            pending.append(f"{names.get(mid, '?')}: [passed the floor]")
        elif kind == "human":
            pending.append(f"[Human] {content}")
        elif kind == "vote":
            pending.append(_vote_line(msg, names))
        elif kind == "evidence":
            pending.append(_evidence_line(msg, names))
        elif kind == "system":
            if _ERROR_MSG_RE.match(content.strip()):
                continue
            pending.append(content)
        elif kind == "secretary":
            pending.append(f"Secretary: {content}")
        elif kind == "report":
            pending.append(f"Secretary final report: {content}")
    flush()
    return out


def build_projection(member: dict, symposium: dict, members: list[dict], rounds: list[dict],
                     all_messages: list[dict], compression_n: int, runtime_meta: str) -> list[dict]:
    """Открытие секретаря → блоки раундов (последние N дословно, старые — дайджест)."""
    names = names_by_id(members)
    current_round = symposium["current_round"]
    out: list[dict] = []
    opening = next((m for m in all_messages if m["kind"] == "secretary"), None)
    if opening:
        out.append({"role": "user", "content": f"Secretary's opening note:\n{opening['content']}"})

    by_round: dict[int | None, list[dict]] = {}
    for msg in all_messages:
        if msg["kind"] != "secretary":
            by_round.setdefault(msg["round_id"], []).append(msg)

    for r in rounds:
        if r["number"] > current_round:
            continue
        ready = r["status"] == "done" and r["summary"]
        if ready and r["number"] <= current_round - compression_n:
            out.append({"role": "user", "content": f"[Round {r['number']}]\nSecretary digest of the round:\n{r['summary']}"})
            continue
        block = _round_lines(by_round.get(r["id"], []), names, member["id"])
        if not block:
            continue
        if block[0]["role"] == "user":
            block[0]["content"] = f"[Round {r['number']}]\n" + block[0]["content"]
        else:
            block.insert(0, {"role": "user", "content": f"[Round {r['number']}]"})
        out.extend(block)

    merged: list[dict] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n\n" + m["content"]}
        else:
            merged.append(dict(m))
    if merged and merged[-1]["role"] == "user":
        merged[-1]["content"] += "\n\n" + runtime_meta
    else:
        merged.append({"role": "user", "content": runtime_meta})
    return merged


def round_transcript_text(round_messages: list[dict], names: dict[int, str]) -> str:
    """Плоский текст раунда (для LLM-саммари и отчёта)."""
    lines = []
    for msg in round_messages:
        kind, mid = msg["kind"], msg["member_id"]
        content = msg["content"] or ""
        if kind == "member":
            lines.append(f"{names.get(mid, '?')}: {content}")
        elif kind == "skip":
            lines.append(f"{names.get(mid, '?')}: [passed]")
        elif kind == "human":
            lines.append(f"[Human] {content}")
        elif kind == "vote":
            lines.append(_vote_line(msg, names))
        elif kind == "evidence":
            lines.append(_evidence_line(msg, names))
        elif kind == "system":
            lines.append(content)
    return "\n".join(lines)


def round_digest(number: int, round_messages: list[dict], names: dict[int, str],
                 checklist: list[dict], issues: list[dict]) -> str:
    """Компактный детерминированный дайджест раунда из блоков <state> (без LLM):
    по одной короткой строке на участника — главный тезис; ссылки на
    свидетельства, открытые вопросы и состояние реестров — одной строкой."""
    lines = [f"Round {number}:"]
    questions: list[str] = []
    evidence: list[str] = []
    for msg in round_messages:
        who = names.get(msg["member_id"], "?")
        if msg["kind"] == "skip":
            lines.append(f"- {who}: —")
        elif msg["kind"] == "member":
            st = meta_state(msg) or {}
            claims = st.get("claims") or []
            lines.append(f"- {who}: {one_line(claims[0] if claims else msg['content'], 110)}")
            questions += [q for q in (st.get("questions") or [])][:1]
        elif msg["kind"] == "evidence":
            try:
                meta = json.loads(msg["meta"]) if msg.get("meta") else {}
            except ValueError:
                meta = {}
            evidence.append(f"{meta.get('ref', 'E?')}/{meta.get('agent', '?')}")
        elif msg["kind"] == "system" and not _ERROR_MSG_RE.match((msg["content"] or "").strip()):
            lines.append(f"- {one_line(msg['content'], 100)}")
    tail = []
    if evidence:
        tail.append("evidence " + ", ".join(evidence))
    if questions:
        tail.append("open: " + " | ".join(one_line(q, 70) for q in questions[:2]))
    if checklist:
        tail.append(f"checklist {sum(1 for c in checklist if c['status'] == 'done')}/{len(checklist)}")
    n_open = sum(1 for i in issues if i["status"] == "open")
    if n_open:
        tail.append(f"objections open {n_open}")
    if tail:
        lines.append("· " + " · ".join(tail))
    return "\n".join(lines)
