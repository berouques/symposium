"""Хранилище дефолтных промптов на диске (v0.3).

Все встроенные промпты — системные и промпты дефолтных ролей — лежат
в папке `prompts/` в текстовом виде (markdown) и ПЕРЕЧИТЫВАЮТСЯ С ДИСКА
при изменении (mtime-кэш). Файл — источник дефолта; БД хранит только
правки администратора (версии), которые перекрывают файл до «сброса».

Структура:
    prompts/system/<ключ>.md           — системные промпты (протокол хода,
                                         правила, голосование, секретарь…)
    prompts/roles/<слаг>.md            — дефолтные роли; front-matter:
        ---
        name: ART историк
        description: История стилей…
        ---
        <тело промпта на английском>

Плейсхолдеры в фигурных скобках ({max_tokens}, {subagent_limit},
{initiator_name}, {completion_criterion}, {report_format}) подставляются
в момент сборки промпта движком.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import get_config
from .logging_setup import get_logger

log = get_logger("prompts")

# --- кэш: (путь, mtime) -> текст; при изменении файла перечитываем --------
_cache: dict[Path, tuple[float, str]] = {}


def _read_cached(path: Path) -> str:
    """Читает файл с кэшированием по mtime: пока файл не тронут — берём из
    памяти; после сохранения (в т.ч. редактором, vim-стиль rename) — перечёт."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _cache.pop(path, None)
        return ""
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    text = path.read_text(encoding="utf-8").strip()
    _cache[path] = (mtime, text)
    log.info("промпт перечитан с диска: %s", path.name)
    return text


def prompts_dir() -> Path:
    """Каталог промптов: SYMPOSIUM_PROMPTS_DIR или <корень>/prompts."""
    custom = get_config().prompts_dir
    root = custom if custom else Path(__file__).resolve().parent.parent / "prompts"
    return Path(root)


def system_keys() -> list[str]:
    """Ключи системных промптов, найденные в prompts/system/*.md."""
    d = prompts_dir() / "system"
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


def read_system_prompt(key: str) -> str:
    """Текст системного промпта из файла (пусто, если файла нет)."""
    return _read_cached(prompts_dir() / "system" / f"{key}.md")


# ---------------------------------------------------------------- роли

@dataclass
class RoleSpec:
    """Роль, прочитанная из prompts/roles/<слаг>.md."""
    slug: str
    name: str          # отображаемое имя (RU по списку пользователя)
    description: str   # одна строка для списков (RU)
    prompt: str        # тело промпта (EN, служебный язык)


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_role(slug: str, text: str) -> RoleSpec:
    """Разбирает markdown-файл роли: front-matter (name/description) + тело."""
    name, description = slug, ""
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        meta = m.group(1)
        body = text[m.end():].strip()
        for line in meta.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "name":
                name = v
            elif k == "description":
                description = v
    return RoleSpec(slug=slug, name=name, description=description, prompt=body)


def role_specs() -> list[RoleSpec]:
    """Все роли из папки (сортировка по имени — как в списках UI)."""
    d = prompts_dir() / "roles"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        text = _read_cached(p)
        if text:
            out.append(_parse_role(p.stem, text))
    out.sort(key=lambda r: r.name)
    return out


def role_spec_by_name(name: str) -> RoleSpec | None:
    for spec in role_specs():
        if spec.name == name:
            return spec
    return None


# --------------------------------------------------- активный текст промпта

def active_system_prompt(key: str) -> str:
    """Эффективный текст системного промпта.

    Приоритет: активная правка администратора в БД → файл на диске.
    Пустая правка приравнивается к её отсутствию (движок никогда не
    теряет протокол хода из-за битой строки).
    """
    row = db.query_one(
        "SELECT content FROM system_prompts WHERE key=? AND is_active=1 "
        "ORDER BY version DESC LIMIT 1",
        (key,),
    )
    if row is not None and (row["content"] or "").strip():
        return row["content"]
    return read_system_prompt(key)


def default_system_prompt(key: str) -> str:
    return read_system_prompt(key)


def active_role_prompt(role_name: str) -> str | None:
    """Дефолтный текст роли из файла (для «сброса к дефолту»)."""
    spec = role_spec_by_name(role_name)
    return spec.prompt if spec else None
