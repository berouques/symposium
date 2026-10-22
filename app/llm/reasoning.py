"""Leaked-reasoning cleanup for real LLM providers.

Some models (notably Zhipu GLM in interleaved-thinking mode, and models that
ignore the reasoning-content channel) occasionally emit their private
chain-of-thought INSIDE the public content stream. In a symposium this shows
up as a sudden switch to English meta-planning text like:

    «I'm Plato in this symposium. Round 1, my turn first. ...
     My character instructions: ... So my response should be brief ...
     Let me think about what Plato would say.»

Consequences without cleanup: the reply blows past its token budget (the
thinking eats the tokens), the terminal marker [END_OF_TURN] never appears,
and the engine pauses the symposium with truncated/turn.

This module strips:
1. explicit reasoning tags: <think>...</think>, <thinking>...</thinking>
   (open tag without a closing one = reasoning ran to the end of the stream);
2. paragraphs that look like leaked meta-planning, detected via a scored
   pattern model (strong patterns alone mark a paragraph; weak patterns need
   reinforcement).

The stripper is deliberately conservative: it never touches paragraphs
without meta signals, so legitimate dialogue about "my turn" or "the
completion criterion" survives when it does not cluster with other signals.
"""
from __future__ import annotations

import re

# --- tagged blocks (case-insensitive, DOTALL) --------------------------------
_TAG_BLOCK_RE = re.compile(r"<(?:think|thinking|reasoning)>(.*?)</(?:think|thinking|reasoning)>", re.IGNORECASE | re.DOTALL)
_TAG_OPEN_RE = re.compile(r"<(?:think|thinking|reasoning)>", re.IGNORECASE)

# --- strong patterns: a single hit marks the paragraph as leaked reasoning ---
_STRONG = [
    re.compile(r"\bI\s*(?:'m|am)\b[^\n.!?]{0,120}\bin (?:this|the) symposium\b", re.IGNORECASE),
    re.compile(r"\bmy character instructions\b", re.IGNORECASE),
    re.compile(r"\bthe symposium format requires\b", re.IGNORECASE),
    re.compile(r"\bmy response should be\b", re.IGNORECASE),
    re.compile(r"\blet me think about\b", re.IGNORECASE),
    re.compile(r"\bas an? (?:AI|language model|artificial intelligence)\b", re.IGNORECASE),
    re.compile(r"\bI need to contribute to\b", re.IGNORECASE),
    re.compile(r"\bEND_OF_TURN\b[^\n.!?]{0,80}\bmarker\b", re.IGNORECASE),
    re.compile(r"\bmarker protocol\b", re.IGNORECASE),
    re.compile(r"\bI (?:will|'ll) now (?:write|compose|draft|respond|reply)\b", re.IGNORECASE),
    re.compile(r"\bwhat would \w+ (?:say|do)\b[^\n.!?]{0,40}\?", re.IGNORECASE),
]

# --- weak patterns: need a cluster (>=2 distinct hits in one paragraph) ------
_WEAK = [
    re.compile(r"\blet me\b", re.IGNORECASE),
    re.compile(r"\bmy turn\b", re.IGNORECASE),
    re.compile(r"\bHmm\b[.,]", re.IGNORECASE),
    re.compile(r"\bthe completion criterion\b", re.IGNORECASE),
    re.compile(r"\bI will\b", re.IGNORECASE),
    re.compile(r"\bI should\b", re.IGNORECASE),
    re.compile(r"\bI need to\b", re.IGNORECASE),
    re.compile(r"\bWait[,.]?\s", re.IGNORECASE),
    re.compile(r"\bso my\b", re.IGNORECASE),
    re.compile(r"\bround[- ]robin\b", re.IGNORECASE),
    re.compile(r"\bmy next (?:reply|response|turn)\b", re.IGNORECASE),
    # v0.3: типичные интерливер-зачины внутри одного абзаца
    re.compile(r"\bmy (?:character )?instructions\b", re.IGNORECASE),
    re.compile(r"\bmy response\b", re.IGNORECASE),
    re.compile(r"\bthe (?:symposium|turn) (?:format|protocol|rules)\b", re.IGNORECASE),
]

_PARA_SPLIT_RE = re.compile(r"\n{2,}")
_LEAD_JUNK_RE = re.compile(r"[«\"'`\s—–-]+$")

_MAX_REASONABLE_STRIP_RATIO = 0.85  # if more than this would be stripped, be extra careful (log it)


def _strip_tagged(text: str) -> tuple[str, str]:
    """Remove <think>-style blocks; returns (clean, removed)."""
    removed: list[str] = []

    def _cut(m: re.Match) -> str:
        removed.append(m.group(0))
        return ""

    clean = _TAG_BLOCK_RE.sub(_cut, text)
    # an unclosed tag: everything after it is reasoning (stream was cut)
    m = _TAG_OPEN_RE.search(clean)
    if m:
        removed.append(clean[m.start():])
        clean = clean[:m.start()]
    # removing a block can leave double spaces — collapse them
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean, "".join(removed)


def _paragraph_score(para: str) -> int:
    score = 0
    for rx in _STRONG:
        if rx.search(para):
            score += 2
            break  # one strong hit is enough
    weak_hits = sum(1 for rx in _WEAK if rx.search(para))
    score += weak_hits
    return score


def _is_suspect(para: str) -> bool:
    has_strong = any(rx.search(para) for rx in _STRONG)
    if has_strong:
        return True
    weak_hits = sum(1 for rx in _WEAK if rx.search(para))
    return weak_hits >= 2


def _strip_meta_paragraphs(text: str) -> tuple[str, str]:
    """Drop paragraphs that read like leaked planning notes.

    Middle paragraphs are removed only on a STRONG signal; the head/tail runs
    are removed already on suspicion (that is where leaks actually appear).

    Одиночный абзац (v0.3): раньше сильное совпадение срезало всё до КОНЦА
    текста — вместе с настоящей речью, шедшей после размышлений в том же
    абзаце. Теперь режем до конца ПРЕДЛОЖЕНИЯ с последним сильным
    совпадением: речь после него сохраняется, начало чинит salvage_start.
    """
    paras = _PARA_SPLIT_RE.split(text)
    if len(paras) <= 1:
        # один абзац: режем по предложениям, как абзацы выше —
        # ведущая серия мета-предложений удаляется (strong ИЛИ слабый
        # сигнал), середина — только по strong, хвостовая серия — снова
        # по подозрению. Так сохраняется речь, идущая после размышлений.
        chunks = [m for m in re.finditer(r"[^.!?…]+[.!?…]+[\s\n]*|[^.!?…]+$", text)]
        if len(chunks) <= 1:
            # одно предложение на всё: сильный сигнал — весь текст утечка
            if any(rx.search(text) for rx in _STRONG):
                return "", text.strip()
            weak_hits = sum(1 for rx in _WEAK if rx.search(text))
            if weak_hits >= 3:
                return "", text.strip()
            return text, ""
        flags = []
        for m in chunks:
            sent = m.group(0)
            strong = any(rx.search(sent) for rx in _STRONG)
            weak = sum(1 for rx in _WEAK if rx.search(sent))
            flags.append((strong, weak))
        keep = [True] * len(chunks)
        # головная серия
        i = 0
        while i < len(chunks) and (flags[i][0] or flags[i][1] >= 1):
            keep[i] = False
            i += 1
        # хвостовая серия
        j = len(chunks) - 1
        while j > i and (flags[j][0] or flags[j][1] >= 1):
            keep[j] = False
            j -= 1
        # середина: только сильный сигнал
        for k in range(i, j + 1):
            if flags[k][0]:
                keep[k] = False
        kept = "".join(m.group(0) for m, k in zip(chunks, keep) if k).strip()
        dropped = "".join(m.group(0) for m, k in zip(chunks, keep) if not k).strip()
        return kept, dropped

    keep_flags = [not _is_suspect(p) for p in paras]
    # strong-only rule for middle paragraphs: a suspect middle paragraph stays
    # unless it has a strong signal
    for i, p in enumerate(paras):
        if not keep_flags[i] and 0 < i < len(paras) - 1:
            if not any(rx.search(p) for rx in _STRONG):
                keep_flags[i] = True
    # trim leading/trailing runs of removed paragraphs — already implied by
    # flags; middle suspect paragraphs with strong hits stay removed.
    kept = [p for p, k in zip(paras, keep_flags) if k]
    dropped = [p for p, k in zip(paras, keep_flags) if not k]
    return "\n\n".join(kept).strip(), "\n\n".join(dropped).strip()


def strip_reasoning(content: str) -> tuple[str, str]:
    """Remove leaked reasoning from an LLM reply. Returns (clean, removed).

    Safe: content without meta signals is returned unchanged.
    """
    if not content:
        return content, ""
    text, removed_tags = _strip_tagged(content)
    text, removed_meta = _strip_meta_paragraphs(text)
    removed = "\n".join(x for x in (removed_tags.strip(), removed_meta) if x)
    clean = salvage_start(text).strip()
    return clean, removed.strip()


# ---------------------------------------------------------------- v0.3 fix
# После вырезки утечки мышления реплика может НАЧИНАТЬСЯ с обрывка:
# «…ской идеи» / «- важный момент: …». Причины: (а) stripper срезал
# ведущие абзацы-размышления и текст пошёл с середины предложения;
# (б) модель-интерливер сама начала публичный поток с середины слова.
# Ответы вида «…срезанное слово» выглядят сломанными — чиним: срезаем
# ведущее многоточие/тире/обрывок до первого начала предложения.

_LEAD_JUNK_PREFIX_RE = re.compile(r"^[\s…·—–\-]+")


def salvage_start(text: str) -> str:
    """Починить НАЧАЛО реплики после вырезки размышлений.

    Правила (консервативные — чистый текст не трогаем):
      1. срезать ведущий мусор: многоточия, точки, тире, дефисы;
      2. если текст начинается со строчной буквы — искать первый
         заглавный старт предложения и срезать всё до него (обрывок);
      3. если начала предложения нет — оставить текст как есть.
    """
    if not text:
        return text
    cleaned = _LEAD_JUNK_PREFIX_RE.sub("", text)
    if not cleaned:
        return cleaned
    first = cleaned[0]
    if first.isupper() or first in "«\"'([АЯЁA-Z]":
        return cleaned  # начинается как нормальное предложение
    if not first.isalpha():
        return cleaned  # цифра/символ — не обрывок слова
    # строчная буква в начале: возможно, обрывок. Ищем старт предложения.
    for m in re.finditer(r"(?:[.!?…]\s+|\n)+(?=[^\s])", cleaned):
        candidate = cleaned[m.end():]
        if candidate and (candidate[0].isupper() or candidate[0] in "«\"'(["):
            # обрывок достаточно короткий? длинный «строчный» текст —
            # возможно, намеренный стиль; режем только явный огрызок
            if m.start() <= 160:
                return candidate
            return cleaned
    return cleaned


def looks_like_reasoning(text: str) -> bool:
    """Public helper (tests, E2E): does the text contain leaked thinking?"""
    if not text:
        return False
    if _TAG_OPEN_RE.search(text):
        return True
    _, removed = strip_reasoning(text)
    return bool(removed.strip())
