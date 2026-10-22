from __future__ import annotations

from datetime import datetime, timezone

_LANGUAGE_NAMES = {
    "ru": "Russian", "en": "English", "uk": "Ukrainian", "ua": "Ukrainian",
    "la": "Latin (scholastic)", "lat": "Latin (scholastic)", "de": "German",
    "fr": "French", "es": "Spanish", "it": "Italian", "pl": "Polish",
    "be": "Belarusian", "kk": "Kazakh", "zh": "Chinese", "ja": "Japanese",
}


def language_name(raw: str) -> str:
    """Код языка → название; произвольная строка проходит как есть."""
    code = (raw or "").strip().lower()
    return _LANGUAGE_NAMES.get(code, (raw or "").strip() or "Russian")


RUNTIME_META_TEMPLATE = (
    "Current date/time: {iso_datetime} ({weekday}, UTC)\n"
    "Round: {round_number}\n"
    "Attached RAG folders: {folder_list}\n"
    "Discussion language: {language}\n"
    "Write your speech ONLY in {language}, whatever the language of these "
    "instructions or of other participants. The <state> block keys stay in English."
)


def build_runtime_meta(symposium: dict, round_number: int, folders: list[str]) -> str:
    now = datetime.now(timezone.utc)
    return RUNTIME_META_TEMPLATE.format(
        iso_datetime=now.isoformat(timespec="minutes"), weekday=now.strftime("%A"),
        round_number=round_number, folder_list=", ".join(folders) if folders else "none",
        language=language_name(symposium["language"]),
    )


def names_by_id(members: list[dict]) -> dict[int, str]:
    return {m["id"]: m["name"] for m in members}


def fmt_members(members: list[dict], self_name: str) -> str:
    lines = ["Participants in this symposium:"]
    for m in members:
        lines.append(f"- {m['name']}: {m['description']}")
    lines.append(f"You are {self_name}. Speak only as yourself, in the first person.")
    return "\n".join(lines)


def one_line(text: str, limit: int = 140) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def human_line(symposium: dict, human: dict | None = None) -> str:
    """Who the human moderator is: preferred name and a line about themselves,
    so models address them properly. Empty string — no data."""
    from ... import db
    if human is None:
        human = db.query_one("SELECT name, about FROM users WHERE id=?", (symposium.get("owner_id"),)) or {}
    name = (human.get("name") or "").strip()
    about = (human.get("about") or "").strip()
    if not (name or about):
        return ""
    text = "The human moderator of this symposium" + (f" is called {name}." if name else ".")
    if about:
        text += f" About them: {about}"
    text += " Address them by name where natural; they may interject as [Human]."
    return text
