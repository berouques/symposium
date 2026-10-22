"""Первичный посев и ремонт: дефолтные роли, системные промпты, настройки.

Начиная с v0.3 ДЕФОЛТНЫЕ промпты живут в папке `prompts/` (markdown,
перечитываются с диска — см. app/prompt_store.py). Роли и настройки
синхронизируются в БД при старте (и при обращении к спискам), а
системные промпты вообще не создаются в БД строкой-дефолтом:
БД хранит ТОЛЬКО правки администратора, файл — источник дефолта.

Апгрейд существующих БД: если активная строка совпадает с одним из
исторических фабричных текстов — она «не
редактировалась админом» и удаляется, после чего действует текст
из файла. Правки админа никогда не затираются.
"""
from __future__ import annotations

from . import db
from . import prompt_store
from .auth import utcnow
from .config import get_config
from .logging_setup import get_logger

log = get_logger("seeds")


def log_repair(message: str) -> None:
    get_logger("seeds").warning("repair: %s", message)


# ------------------------------------------------------------------ настройки

DEFAULT_SETTINGS: dict[str, str] = {
    "installation_name": "Academy Symposium",
    "compression_threshold": "10",
    "workspace_idle_timeout_sec": "3600",
    "workspace_mode": "local",
    "workspace_allow_local": "1",
    "workspace_docker_image": "python:3.12-slim",
    "workspace_docker_network": "bridge",
    "workspace_cpu_limit": "1.0",
    "workspace_mem_limit_mb": "4096",
    "max_file_size_bytes": str(50 * 1024 * 1024),
    "max_rag_folder_bytes": "0",
    "subagent_calls_per_turn": "3",
    "avatar_default_style": "bust",
    "default_theme": "garden",
    # языки интерфейса: en / ru / uk / la (схоластическая латынь)
    "default_language": "ru",
    "available_languages": '["en", "ru", "uk", "la"]',
    "service_language": "en",
    "backup_on_delete": "0",
    "backup_retention_hours": "24",
    # ---- протокол v2 (v0.4) ----
    # требовать блок <state>: нет блока → одна просьба, затем реплика принимается с флагом
    "strict_state": "1",
    # дайджест раунда: digest (детерминированный, без LLM) | llm (секретарь)
    "summary_mode": "digest",
    # раундов подряд без новых утверждений → автоматический запрос подтверждения (0 = выкл.)
    "stall_rounds": "2",
    "vote_max_tokens": "500",
    "web_fetch_max_bytes": "26214400",
    "web_fetch_allow_private": "0",
    "confirm_destructive": "1",
    # самовосстановление после некритичных ошибок (повторов, обрыва, недоступности LLM)
    "auto_recovery": "1",
    "auto_recovery_max": "3",
    "subagent_digest_tokens": "900",
    "sandbox_max_timeout_sec": "300",
    # лимит раундов (0 = ∞); по достижении — ОКОНЧАТЕЛЬНОЕ голосование
    "default_max_rounds": "12",
    "rag_chunk_chars": "1200",
    "rag_chunk_overlap": "200",
    "embedding_provider": "ollama",
    "embedding_model": "nomic-embed-text",
    "embedding_dims": "768",
    "ollama_url": "",
    # ---- веб-поиск ----
    "web_search_enabled": "1",
    "web_search_engines": '["google", "bing", "yandex"]',
    "web_search_searxng_url": "",
    "web_search_google_cx": "",
    "web_search_google_api_key": "",
    "web_search_max_results": "8",
    "web_search_timeout_sec": "10",
}

# значения настроек, которые должны обновиться при апгрейде v0.3
# (старое значение → новое; правки админа со старым значением тоже
# обновляются — это смена формата, а не вкуса)
SETTINGS_UPGRADES: dict[str, dict[str, str]] = {
    "available_languages": {'["ru", "en"]': '["en", "ru", "uk", "la"]'},
    "default_theme": {"light": "garden", "dark": "starlight"},
}


# -------------------------------------------------------------------- роли

def _role_is_factory(name: str, prompt: str) -> bool:
    """Промпт роли — фабричный (админ не правил)?"""
        # совпадение с текущим файлом — тоже фабричное состояние
    spec = prompt_store.role_spec_by_name(name)
    return spec is not None and prompt == spec.prompt


def _sync_roles(now: str) -> None:
    """Роли из prompts/roles/*.md → БД.

    - новой роли нет в БД → вставляем (is_default=1);
    - роль есть и её промпт фабричный → обновляем текст/описание из файла
      (история версий при этом пополняется);
    - роль правил админ → не трогаем;
    - старая дефолтная роль, которой нет в новом списке → теряет
      is_default и скрывается (существующие участники хранят снапшот
      промпта, поэтому FK не страдает).
    """
    specs = prompt_store.role_specs()
    file_names = {s.name for s in specs}
    for spec in specs:
        row = db.query_one(
            "SELECT id, prompt, description, is_default FROM role_templates WHERE name=?",
            (spec.name,),
        )
        if row is None:
            rid = db.execute(
                "INSERT INTO role_templates(name, prompt, description, language, "
                "is_default, is_hidden, created_at, updated_at) VALUES(?,?,?,?,'1',0,?,?)",
                (spec.name, spec.prompt, spec.description, "en", now, now),
            )
            _add_role_version(rid, spec.prompt, "initial default (from file)", None, now)
            continue
        # роль существует: обновляем, только если текущий текст фабричный
        fresh_prompt = _role_is_factory(spec.name, row["prompt"] or "")
        if fresh_prompt and (row["prompt"] or "").strip() != spec.prompt:
            db.execute(
                "UPDATE role_templates SET prompt=?, updated_at=? WHERE id=?",
                (spec.prompt, now, row["id"]),
            )
            _add_role_version(row["id"], spec.prompt, "factory text updated (file)", None, now)
            log_repair(f"роль «{spec.name}»: фабричный промпт обновлён из файла")
        elif not (row["prompt"] or "").strip():
            db.execute(
                "UPDATE role_templates SET prompt=?, updated_at=? WHERE id=?",
                (spec.prompt, now, row["id"]),
            )
            _add_role_version(row["id"], spec.prompt, "repaired: empty prompt", None, now)
            log_repair(f"роль «{spec.name}»: пустой промпт восстановлен из файла")
        # описание обновляем только у нетронутых строк (без версий админа)
        if not _role_has_admin_versions(row["id"]) and (row["description"] or "") != spec.description:
            db.execute(
                "UPDATE role_templates SET description=?, updated_at=? WHERE id=?",
                (spec.description, now, row["id"]),
            )
        if not row["is_default"]:
            # роль из файла обязана быть дефолтной (например, легаси «Философ»)
            db.execute("UPDATE role_templates SET is_default=1 WHERE id=?", (row["id"],))

    # старые дефолтные роли, не вошедшие в новый список: снять дефолт и скрыть
    for row in db.query_all("SELECT id, name, is_default, is_hidden FROM role_templates"):
        if row["is_default"] and row["name"] not in file_names:
            db.execute(
                "UPDATE role_templates SET is_default=0, is_hidden=1, updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            log_repair(f"роль «{row['name']}» выведена из дефолтных (заменена списком v0.3)")
        elif not row["is_default"] and row["name"] not in file_names and not row["is_hidden"]:
            # легаси-роли v1 (Philosopher, Skeptic, …) — прячем из пользовательских списков
            db.execute("UPDATE role_templates SET is_hidden=1, updated_at=? WHERE id=?", (now, row["id"]))


def _role_has_admin_versions(rid: int) -> bool:
    """Есть ли у роли версии, сохранённые из админки (не фабричные)?"""
    rows = db.query_all(
        "SELECT note FROM role_prompt_versions WHERE role_id=?", (rid,)
    )
    factory_notes = {"initial default", "initial default (from file)",
                     "factory text updated (file)", "repaired: empty prompt"}
    return any((r["note"] or "") not in factory_notes for r in rows)


def _add_role_version(rid: int, content: str, note: str, admin_user_id: int | None, now: str) -> None:
    latest = db.query_value(
        "SELECT COALESCE(MAX(version),0) FROM role_prompt_versions WHERE role_id=?", (rid,)
    )
    db.execute(
        "INSERT INTO role_prompt_versions(role_id, version, content, note, updated_by, "
        "created_at) VALUES(?,?,?,?,?,?)",
        (rid, latest + 1, content, note, admin_user_id, now),
    )


# ---------------------------------------------------------- системные промпты

def _sync_system_prompts() -> None:
    """БД хранит только правки админа; фабричные строки удаляем.

    Строка «фабричная», если её текст совпадает с историческим фабричным
    текстом (v0.2.x) или с текущим текстом файла. Удаляем ВСЕ строки
    ключа: история правок не нужна, дефолт теперь читается с диска.
    """
    for key in db.query_all("SELECT DISTINCT key FROM system_prompts"):
        key = key["key"]
        active = db.query_one(
            "SELECT content FROM system_prompts WHERE key=? AND is_active=1 "
            "ORDER BY version DESC LIMIT 1",
            (key,),
        )
        if active is None:
            continue
        content = (active["content"] or "").strip()
        if not content:
            # пустая активная версия — чиним удалением (файл возьмёт на себя)
            db.execute("DELETE FROM system_prompts WHERE key=?", (key,))
            log_repair(f"системный промпт «{key}» был пуст — переключён на файл")
            continue
        legacy: list[str] = []
        file_text = prompt_store.read_system_prompt(key).strip()
        if content in legacy or content == file_text:
            db.execute("DELETE FROM system_prompts WHERE key=?", (key,))
            log_repair(f"системный промпт «{key}»: фабричная строка удалена, "
                       "источник — файл prompts/")


# ------------------------------------------------------------------- точка входа

def seed_defaults(admin_user_id: int | None = None) -> None:
    """Идемпотентный посев/ремонт. Вызывается на каждом старте."""
    now = utcnow()
    from . import characters
    characters.seed_defaults(now)
    # v0.4.2: docker-песочница в настройках убрана; код выполняется локально в workspace
    if db.query_one("SELECT 1 FROM settings WHERE key='sandbox_local_default'") is None:
        db.set_setting("workspace_mode", "local")
        db.set_setting("workspace_allow_local", "1")
        db.set_setting("sandbox_local_default", "1")
    _sync_system_prompts()
    cfg = get_config()
    env_defaults = {
        "embedding_model": cfg.embedding_model, "embedding_dims": cfg.embedding_dims,
        "web_search_searxng_url": cfg.searxng_url, "workspace_docker_image": cfg.sandbox_image,
        "workspace_docker_network": cfg.sandbox_network,
    }
    if cfg.web_search_engines.strip():
        env_defaults["web_search_engines"] = cfg.web_search_engines
    elif cfg.searxng_url.strip():
        env_defaults["web_search_engines"] = '["searxng", "google", "bing", "yandex"]'
    for key, value in DEFAULT_SETTINGS.items():
        if db.query_one("SELECT 1 FROM settings WHERE key=?", (key,)) is None:
            db.set_setting(key, env_defaults.get(key) or value)
    for key, mapping in SETTINGS_UPGRADES.items():
        current = db.query_value("SELECT value FROM settings WHERE key=?", (key,))
        if current in mapping:
            db.set_setting(key, mapping[current])
            log_repair(f"настройка «{key}»: {current} → {mapping[current]} (апгрейд v0.3)")


def active_system_prompt(key: str) -> str:
    """Активный текст: правка админа из БД → файл (см. prompt_store)."""
    return prompt_store.active_system_prompt(key)


def default_system_prompt(key: str) -> str:
    return prompt_store.read_system_prompt(key)


def default_role_prompt(role: dict) -> str | None:
    """Дефолтный текст роли из файла (для «сброса к дефолту»)."""
    return prompt_store.active_role_prompt(role["name"])
