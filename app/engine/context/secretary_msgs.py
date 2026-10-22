from __future__ import annotations

from .common import human_line, language_name, names_by_id
from .projection import round_transcript_text


def _language_line(symposium: dict) -> str:
    lang = language_name(symposium["language"])
    hl = human_line(symposium)
    return f"Language of your output: {lang}. Write in {lang} only." + (f"\n{hl}" if hl else "")


def build_secretary_open_messages(symposium: dict, members: list[dict]) -> list[dict]:
    lines = [
        f"Symposium topic: {symposium['topic']}",
        f"Completion criterion: {symposium['completion_criterion'] or 'not specified'}",
        f"Report format: {symposium['report_format'] or 'not specified'}",
        _language_line(symposium), "Participants:",
    ]
    for m in members:
        lines.append(f"- {m['name']}: {m['description']} (role: {m['role_name']})")
    return [{"role": "user", "content": "\n".join(lines)}]


def build_secretary_summarize_messages(symposium: dict, round_text: str) -> list[dict]:
    return [{"role": "user", "content": _language_line(symposium)
             + "\n\nTranscript of the round to summarize:\n\n" + round_text}]


def build_secretary_report_messages(symposium: dict, rounds: list[dict], all_messages: list[dict],
                                    members: list[dict], checklist: list[dict], issues: list[dict],
                                    evidence: list[dict], ending: str) -> list[dict]:
    names = names_by_id(members)
    parts = []
    opening = next((m for m in all_messages if m["kind"] == "secretary"), None)
    if opening:
        parts.append("Opening note:\n" + opening["content"])
    for r in rounds:
        if r["summary"]:
            parts.append(f"[Round {r['number']}] digest:\n{r['summary']}")
        else:
            msgs = [m for m in all_messages if m["round_id"] == r["id"]]
            parts.append(f"[Round {r['number']}]\n" + round_transcript_text(msgs, names))
    if checklist:
        parts.append("Checklist:\n" + "\n".join(f"{c['ref']} [{c['status']}] {c['text']}" for c in checklist))
    if issues:
        parts.append("Issues:\n" + "\n".join(
            f"{i['ref']} [{i['status']}] by {names.get(i['owner_member_id'], '?')}: {i['text']}" for i in issues))
    ballots = [m for m in all_messages if m["kind"] == "vote"]
    if ballots:
        parts.append("Ballots:\n" + "\n".join(
            f"{names.get(b['member_id'], '?')}: {b['content']}" for b in ballots))
    if evidence:
        parts.append("Evidence:\n" + "\n".join(f"{e['ref']} ({e['agent']}): {e['digest'][:300]}" for e in evidence))
    parts.append(f"Ending: {ending}")
    parts.append(_language_line(symposium))
    return [{"role": "user", "content": "\n\n".join(parts)}]
