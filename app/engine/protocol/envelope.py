"""Ответ участника (протокол v2): свободная речь + итоговый блок <state>.

    <речь участника, свободный текст>
    <state>
    action: speak              # speak | skip | propose_end
    reply_to: Alice, Bob       # кому отвечаю (с раунда 2 обязательно)
    stance: Alice=agree; Bob=disagree     # agree | disagree | refine
    claims: новая мысль 1 | новая мысль 2 # 0–3 новых утверждения
    questions: @Bob: вопрос | общий вопрос
    evidence: E1, E3           # на какие свидетельства опираюсь
    checklist: C1=done; C2=open
    issues: I1=resolved        # по возражениям (своим/чужим)
    readiness: developing      # developing | settled
    </state>

Движок НЕ требует терминального маркера: блок <state> и есть конец ответа.
Парсер терпим: ключи без учёта регистра, маркеры списков «-», «*» игнорируются,
незакрытый <state> (обрезка по лимиту) разбирается по возможности, старые
маркеры [END_OF_TURN]/[SKIP_TURN]/[END_SYMPOSIUM] понимаются как синонимы.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .calls import Call, has_calls, parse_calls, strip_calls

STATE_CLOSED_RE = re.compile(r"<state>(.*?)</state\s*>", re.DOTALL | re.IGNORECASE)
STATE_OPEN_RE = re.compile(r"<state>(.*)$", re.DOTALL | re.IGNORECASE)
_LEGACY_RE = re.compile(r"\[(?:END_OF_TURN|SKIP_TURN|END_SYMPOSIUM)\]", re.IGNORECASE)

_EMPTY = {"", "none", "-", "—", "n/a", "na", "нет", "пусто", "null", "[]"}
_EVID_RE = re.compile(r"\bE\d+\b", re.IGNORECASE)
_STANCES = {"agree": "agree", "disagree": "disagree", "refine": "refine",
            "согласен": "agree", "возражаю": "disagree", "уточняю": "refine"}


@dataclass
class State:
    action: str = "speak"                 # speak | skip | propose_end
    reply_to: list[str] = field(default_factory=list)
    stance: dict[str, str] = field(default_factory=dict)
    claims: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    checklist: dict[str, str] = field(default_factory=dict)   # C1 -> done|open
    issues: dict[str, str] = field(default_factory=dict)      # I1 -> resolved|open
    readiness: str = "developing"         # developing | settled
    raw: str = ""

    def public(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d

    def render(self) -> str:
        """Канонический вид блока (для истории: модель видит свой формат)."""
        def lst(v, sep=" | "):
            return sep.join(v)
        lines = [f"action: {self.action}"]
        if self.reply_to:
            lines.append("reply_to: " + ", ".join(self.reply_to))
        if self.stance:
            lines.append("stance: " + "; ".join(f"{k}={v}" for k, v in self.stance.items()))
        if self.claims:
            lines.append("claims: " + lst(self.claims))
        if self.questions:
            lines.append("questions: " + lst(self.questions))
        if self.evidence:
            lines.append("evidence: " + ", ".join(self.evidence))
        if self.checklist:
            lines.append("checklist: " + "; ".join(f"{k}={v}" for k, v in self.checklist.items()))
        if self.issues:
            lines.append("issues: " + "; ".join(f"{k}={v}" for k, v in self.issues.items()))
        lines.append(f"readiness: {self.readiness}")
        return "<state>\n" + "\n".join(lines) + "\n</state>"


@dataclass
class Reply:
    kind: str = "speech"          # speech | skip | end | calls | invalid
    speech: str = ""
    state: State = field(default_factory=State)
    calls: list[Call] = field(default_factory=list)
    state_missing: bool = False
    state_unclosed: bool = False
    propose_end: bool = False
    problem: str | None = None
    pre_call_text: str = ""


# ------------------------------------------------------------------ helpers

def _items(value: str) -> list[str]:
    value = (value or "").strip()
    if value.lower() in _EMPTY:
        return []
    sep = "|" if "|" in value else (";" if ";" in value else ",")
    out = []
    for part in value.split(sep):
        part = part.strip().strip("-*• ").strip()
        if part.lower() not in _EMPTY:
            out.append(part)
    return out


def _pairs(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in _items(value.replace(",", ";") if ";" not in value and "|" not in value else value):
        m = re.match(r"^(.+?)\s*[=:]\s*(\S+)\s*$", item)
        if m:
            out[m.group(1).strip()] = m.group(2).strip().lower()
    return out


def _match_name(name: str, known: list[str]) -> str:
    """Имя из блока → точное имя участника (без регистра, по префиксу)."""
    low = name.strip().lower().lstrip("@")
    for k in known:
        if k.lower() == low:
            return k
    for k in known:
        if k.lower().startswith(low) or low.startswith(k.lower()):
            return k
    return name.strip().lstrip("@")


def parse_state_block(body: str, known: list[str] | None = None) -> State:
    known = known or []
    st = State(raw=body.strip())
    for raw_line in body.splitlines():
        line = raw_line.strip().lstrip("-*•").strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key == "action":
            v = value.lower().split()[0] if value.split() else "speak"
            v = v.strip(".,;")
            if v.startswith("skip") or v.startswith("pass"):
                st.action = "skip"
            elif v.startswith(("propose", "end")):
                st.action = "propose_end"
            else:
                st.action = "speak"
        elif key in ("reply_to", "replies_to", "replyto"):
            st.reply_to = [_match_name(n, known) for n in _items(value)]
        elif key == "stance":
            for who, how in _pairs(value).items():
                st.stance[_match_name(who, known)] = _STANCES.get(how, how)
        elif key in ("claims", "new_claims"):
            st.claims = _items(value)[:5]
        elif key in ("questions", "open_questions"):
            st.questions = _items(value)[:5]
        elif key in ("evidence", "evidence_used"):
            st.evidence = [e.upper() for e in _EVID_RE.findall(value)]
        elif key == "checklist":
            st.checklist = {k.upper(): ("done" if v.startswith("done") else "open")
                            for k, v in _pairs(value).items()}
        elif key == "issues":
            st.issues = {k.upper(): ("resolved" if v.startswith(("resolv", "closed", "done", "withdr")) else "open")
                         for k, v in _pairs(value).items()}
        elif key == "readiness":
            low = value.lower()
            st.readiness = "settled" if any(w in low for w in ("settled", "complete", "done", "ready")) \
                and "not" not in low else "developing"
    return st


# -------------------------------------------------------------------- main

def parse_reply(raw: str, known_members: list[str] | None = None) -> Reply:
    """Разбор сырого ответа модели. Никогда не бросает исключений."""
    known = known_members or []
    text = (raw or "").strip()

    # --- блоки вызовов субагентов: ход ещё не закончен
    if has_calls(text):
        first = text.lower().find("<call")
        return Reply(kind="calls", calls=parse_calls(text),
                     pre_call_text=text[:first].strip())

    legacy_skip = "[skip_turn]" in text.lower()
    legacy_end = "[end_symposium]" in text.lower()
    text = _LEGACY_RE.sub("", text).strip()

    state_text: str | None = None
    unclosed = False
    m = STATE_CLOSED_RE.search(text)
    if m:
        state_text = m.group(1)
        speech = (text[:m.start()] + " " + text[m.end():]).strip()
    else:
        m = STATE_OPEN_RE.search(text)
        if m:
            state_text, unclosed = m.group(1), True
            speech = text[:m.start()].strip()
        else:
            speech = text
    # если блоков несколько — берём последний закрытый
    for extra in STATE_CLOSED_RE.finditer(text):
        state_text = extra.group(1)

    state = parse_state_block(state_text, known) if state_text is not None else State()
    if legacy_skip and state_text is None:
        state.action = "skip"
    if legacy_end and state_text is None:
        state.action = "propose_end"
    speech = re.sub(r"</?state>", "", speech, flags=re.IGNORECASE).strip()

    reply = Reply(speech=speech, state=state, state_missing=state_text is None,
                  state_unclosed=unclosed)
    if state.action == "skip" and not speech:
        reply.kind = "skip"
        return reply
    if state.action == "skip" and speech:
        state.action = "speak"      # противоречие: речь есть — это речь
    reply.propose_end = state.action == "propose_end"
    if not speech:
        if reply.propose_end:
            reply.kind = "end"
        else:
            reply.kind = "invalid"
            reply.problem = "empty reply"
        return reply
    reply.kind = "speech"
    return reply
