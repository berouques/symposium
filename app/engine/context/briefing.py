from __future__ import annotations

from ..convergence import meta_state
from .common import names_by_id, one_line


def build_turn_briefing(
    member: dict, members: list[dict], all_messages: list[dict], *, current_round: int,
    checklist: list[dict], issues: list[dict], max_rounds: int = 0,
) -> str | None:
    """Брифинг хода: дельта с прошлого хода, вопросы лично вам, чек-лист,
    открытые возражения, предупреждение о лимите раундов. Про процедуру
    голосования здесь нет ни слова — только нейтральное «readiness»."""
    names = names_by_id(members)
    own = [m for m in all_messages if m["kind"] == "member" and m["member_id"] == member["id"]]
    last_own_seq = own[-1]["seq"] if own else 0
    others_after = [
        m for m in all_messages
        if m["seq"] > last_own_seq and (
            (m["kind"] == "member" and m["member_id"] != member["id"]) or m["kind"] in ("human", "evidence"))
    ]
    if not own and not others_after and not checklist:
        return None

    lines = [f"Turn briefing (round {current_round}):"]
    if own:
        lines.append("- You have ALREADY spoken earlier; your statements are in the transcript. "
                     "Repeating them (verbatim or in essence) violates the protocol.")
        if own[-1].get("content"):
            lines.append(f"- Your last statement began with: «{one_line(own[-1]['content'], 110)}» — do not restate it.")
    if others_after:
        lines.append("Since your last turn the following happened (react to THIS):")
        for m in others_after[-6:]:
            if m["kind"] == "human":
                lines.append(f"  • [Human]: {one_line(m['content'])}")
            elif m["kind"] == "evidence":
                meta = m.get("meta") or ""
                lines.append(f"  • evidence: {one_line(m['content'], 100)}")
            else:
                lines.append(f"  • {names.get(m['member_id'], '?')}: {one_line(m['content'])}")
        if len(others_after) > 6:
            lines.append(f"  (…and {len(others_after) - 6} earlier items)")
        lines.append("- Your task: ADVANCE the discussion. Engage at least one statement above "
                     "(agree, object, refine, or ask its author), then add something new of your own.")
    elif own:
        lines.append("- No one has spoken since your last turn. Develop your position: deepen it, "
                     "add a new example or argument, or revise it. Do not restate yourself.")

    # вопросы лично вам, оставшиеся без ответа
    mine = []
    for m in all_messages:
        if m["kind"] != "member" or m["member_id"] == member["id"] or m["seq"] <= last_own_seq:
            continue
        for q in (meta_state(m) or {}).get("questions", []) or []:
            if q.lower().startswith("@" + member["name"].lower() + ":"):
                mine.append(f"{names.get(m['member_id'], '?')} asks you: {one_line(q.split(':', 1)[1], 160)}")
    if mine:
        lines.append("Questions addressed to YOU (answer them):")
        lines.extend("  • " + q for q in mine)

    if checklist:
        lines.append("Completion checklist (moderator's view): " +
                     "; ".join(f"{c['ref']} [{c['status']}] {one_line(c['text'], 90)}" for c in checklist))
    open_issues = [i for i in issues if i["status"] == "open"]
    if open_issues:
        lines.append("OPEN OBJECTIONS — the discussion cannot close while they stand:")
        for i in open_issues:
            owner = names.get(i["owner_member_id"], "?")
            sat = f" (would be satisfied if: {one_line(i['satisfied_if'], 120)})" if i["satisfied_if"] else ""
            lines.append(f"  • {i['ref']} by {owner}: {one_line(i['text'], 160)}{sat}")
        lines.append("  Address them directly. Their owner marks them 'resolved' in the issues field once satisfied.")
    elif checklist and all(c["status"] == "done" for c in checklist):
        lines.append("- All checklist items look done and no objection is open. If you agree and have "
                     "nothing to add, set readiness: settled.")
    if max_rounds and current_round >= max_rounds - 1 and max_rounds > 1:
        lines.append(f"- Round limit ({max_rounds}) is close: the symposium will be closed when round "
                     f"{max_rounds} completes. Wrap up your position.")
    return "\n".join(lines)
