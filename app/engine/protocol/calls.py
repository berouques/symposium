"""Вызовы субагентов: блоки <call agent="имя" атрибуты…>тело</call>.

Тело — либо JSON-объект (параметры), либо «сырой» текст: он становится
главным параметром агента (coder → code, web_explorer/archivarius → query,
ask_human → question). Так код не нужно экранировать внутри JSON-строки.
Допустимо несколько блоков в одном сообщении — они исполняются по порядку.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

AGENTS = ("web_explorer", "web_fetch", "archivarius", "coder", "ask_human")

# главный параметр агента, в который попадает «сырое» тело блока
MAIN_PARAM = {
    "web_explorer": "query",
    "web_fetch": "url",
    "archivarius": "query",
    "coder": "code",
    "ask_human": "question",
}

CALL_RE = re.compile(r"<call\b([^>]*)>(.*?)</call\s*>", re.DOTALL | re.IGNORECASE)
_ATTR_RE = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


@dataclass
class Call:
    agent: str
    params: dict = field(default_factory=dict)
    error: str | None = None


def has_calls(text: str) -> bool:
    return CALL_RE.search(text or "") is not None


def parse_calls(text: str) -> list[Call]:
    """Все блоки <call> в порядке появления."""
    out: list[Call] = []
    for m in CALL_RE.finditer(text or ""):
        attrs = {k: (a or b) for k, a, b in _ATTR_RE.findall(m.group(1))}
        agent = (attrs.pop("agent", "") or "").strip()
        body = m.group(2).strip("\n")
        if agent not in AGENTS:
            out.append(Call(agent or "?", {}, f"unknown subagent '{agent}' (allowed: {', '.join(AGENTS)})"))
            continue
        params: dict = dict(attrs)
        stripped = body.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    params.update(data)
                else:
                    raise ValueError("JSON body must be an object")
            except ValueError as e:
                out.append(Call(agent, {}, f"invalid JSON body: {e}"))
                continue
        elif stripped:
            params[MAIN_PARAM[agent]] = body if agent == "coder" else stripped
        out.append(Call(agent, params, None))
    return out


def strip_calls(text: str) -> str:
    return CALL_RE.sub("", text or "").strip()
