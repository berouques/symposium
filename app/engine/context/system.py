from __future__ import annotations

from ...seeds import active_system_prompt
import re

from .common import fmt_members, human_line


def _form_block(symposium: dict) -> str:
    form = (symposium.get("form") or "free").strip() or "free"
    text = active_system_prompt(f"form_{form}")
    return text or active_system_prompt("form_free")


def _filter_tools(text: str, allowed: list[str], all_tools: tuple[str, ...]) -> str:
    """Убрать из инструкции строки о запрещённых участнику инструментах."""
    drop = [t for t in all_tools if t not in allowed]
    if not drop:
        return text
    out = []
    for line in text.splitlines():
        if any(re.search(rf'agent="{t}"', line) or line.lstrip().startswith(f"- {t}:") for t in drop):
            continue
        out.append(line)
    return "\n".join(out)


def build_member_system_prompt(member: dict, symposium: dict, members: list[dict],
                               subagent_limit: int, human: dict | None = None) -> str:
    """Системный промпт обычного хода: личность+роль → контекст симпозиума →
    участники → человек → протокол хода → правила → форма дискуссии → субагенты."""
    from ...characters import ALL_TOOLS
    import json as _json
    turn_protocol = active_system_prompt("turn_protocol").replace("{max_tokens}", str(member["max_tokens"]))
    allowed = list(ALL_TOOLS)
    if member.get("tools"):
        try:
            allowed = [t for t in _json.loads(member["tools"]) if t in ALL_TOOLS]
        except ValueError:
            pass
    subagents = ""
    if allowed:
        subagents = _filter_tools(
            active_system_prompt("subagents_instruction").replace("{subagent_limit}", str(subagent_limit)),
            allowed, ALL_TOOLS)
        if len(allowed) < len(ALL_TOOLS):
            subagents += f"\nYour permitted subagents: {', '.join(allowed)}. Other tools are not available to you."
    blocks = [
        f"You are {member['name']}. {member['description'] or 'A symposium participant.'}",
        member["role_prompt_snapshot"] or "",
        (f"Your manner of speech: {member['speech_style']}" if member.get("speech_style") else ""),
        ("Symposium context:\n"
         f"Symposium topic: {symposium['topic']}\n"
         f"Completion criterion: {symposium['completion_criterion'] or 'not specified'}\n"
         f"Report format: {symposium['report_format'] or 'not specified'}"),
        fmt_members(members, member["name"]),
        human_line(symposium, human),
        turn_protocol,
        active_system_prompt("symposium_rules"),
        _form_block(symposium),
        subagents,
    ]
    if member.get("user_prompt"):
        blocks.append(f"Additional instructions for you personally:\n{member['user_prompt']}")
    return "\n\n".join(b for b in blocks if b.strip())


def build_vote_system_prompt(member: dict, symposium: dict, members: list[dict], reason: str) -> str:
    """Промпт подтверждения завершения заменяет протокол хода."""
    vote = (active_system_prompt("vote_prompt")
            .replace("{reason}", reason)
            .replace("{completion_criterion}", symposium["completion_criterion"] or "not specified"))
    blocks = [
        f"You are {member['name']}. {member['description'] or 'A symposium participant.'}",
        member["role_prompt_snapshot"] or "",
        fmt_members(members, member["name"]),
        vote,
    ]
    return "\n\n".join(b for b in blocks if b.strip())
