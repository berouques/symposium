"""Тексты корректирующих просьб (user-роль внутри хода). Чистые функции."""
from __future__ import annotations

STATE_TEMPLATE = (
    "<state>\naction: speak\nreply_to: <names>\nstance: <Name>=agree|disagree|refine\n"
    "claims: <new claim> | <new claim>\nquestions: <@Name: question> | <question>\n"
    "evidence: <E-ids or empty>\nchecklist: <C-id>=done|open\nissues: <I-id>=resolved|open\n"
    "readiness: developing|settled\n</state>"
)


def state_missing() -> str:
    return (
        "PROTOCOL: your reply has no <state> block. Send ONLY the <state> block for the "
        "reply you just wrote (do not repeat the speech), in this format:\n" + STATE_TEMPLATE
    )


def state_unclosed() -> str:
    return "PROTOCOL: your <state> block was not closed with </state>. Send ONLY the complete <state> block."


def truncated(max_tokens: int) -> str:
    return (
        f"LENGTH LIMIT: your reply was cut off by the {max_tokens}-token limit before the "
        "<state> block. Do NOT repeat the speech. Finish now: sum up your key point in 1-3 "
        "sentences and end with the complete <state> block."
    )


def empty_reply() -> str:
    return "PROTOCOL: your reply was empty. Write your speech, then the <state> block (or action: skip)."


def repetition(round_no, ratio: float, others: list[str], max_tokens: int) -> str:
    head = (
        f"REPETITION: your reply repeats your own statement from round {round_no} "
        f"(similarity {ratio:.0%}). Write a DIFFERENT reply now:"
    )
    if others:
        head += f"\n1. React to what {', '.join(others[-3:])} said since your last turn."
    else:
        head += "\n1. Develop your position: a new argument, example, or revision."
    return head + f"\n2. Add something new of your own.\nEnd with the <state> block. Stay within {max_tokens} tokens."


def call_errors(errors: list[str]) -> str:
    return (
        "SUBAGENT PROTOCOL ERROR: " + "; ".join(errors) +
        ". Fix the <call> syntax, or write your reply without the call."
    )


def call_limit(limit: int) -> str:
    return (f"SUBAGENT LIMIT REACHED: at most {limit} calls per turn. Write your reply now, "
            "end with the <state> block.")


def evidence_results(lines: list[str]) -> str:
    return (
        "EVIDENCE RESULTS (now public to all participants):\n" + "\n\n".join(lines) +
        "\n\nNow write your reply. Cite evidence as [E#] and list the ids in the "
        "'evidence:' field of your <state> block. If a call failed, say so and proceed."
    )


def vote_format() -> str:
    return (
        "VOTE FORMAT: your previous reply was not a valid ballot. Answer again with EXACTLY:\n"
        "<vote>agree | reservations | object</vote>\n<reason>one line</reason>\n"
        "<unresolved>what remains (for reservations/object)</unresolved>\n"
        "<satisfied_if>what would satisfy you (for object)</satisfied_if>\nNo other text."
    )
