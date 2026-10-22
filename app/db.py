"""SQLite storage layer.

Single shared connection (WAL mode) guarded by an RLock. All operations are
short (sub-millisecond at PoC scale), so synchronous access from the asyncio
event loop is acceptable and keeps the code simple and debuggable.

Schema is versioned via `schema_version`; migrations are sequential.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .logging_setup import get_logger

log = get_logger("db")

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()

MIGRATIONS: list[str] = [
    # ---- v1: initial schema -------------------------------------------------
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT 'ru',
        gender TEXT NOT NULL DEFAULT '',
        theme TEXT NOT NULL DEFAULT 'light',
        is_admin INTEGER NOT NULL DEFAULT 0,
        show_thinking INTEGER NOT NULL DEFAULT 0,
        transcript_prefs TEXT NOT NULL DEFAULT '{}',
        active_profile_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE TABLE api_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        api_key TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL,
        max_concurrent INTEGER NOT NULL DEFAULT 1,
        requests_per_minute INTEGER,
        requests_per_hour INTEGER,
        requests_per_day INTEGER,
        min_pause_ms INTEGER NOT NULL DEFAULT 500,
        supports_thinking INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE role_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT 'en',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE system_prompts (
        key TEXT NOT NULL,
        version INTEGER NOT NULL,
        content TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0,
        updated_by INTEGER,
        created_at TEXT NOT NULL,
        PRIMARY KEY (key, version)
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE symposia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL REFERENCES users(id),
        title TEXT NOT NULL,
        topic TEXT NOT NULL,
        completion_criterion TEXT NOT NULL DEFAULT '',
        report_format TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT 'ru',
        status TEXT NOT NULL DEFAULT 'paused_manual',
        current_round INTEGER NOT NULL DEFAULT 0,
        api_profile_id INTEGER REFERENCES api_profiles(id),
        compression_override INTEGER,
        token_limit INTEGER,
        strict_markers INTEGER,
        pending_turn TEXT,
        pending_interventions TEXT NOT NULL DEFAULT '[]',
        next_round_priority TEXT NOT NULL DEFAULT '[]',
        error_info TEXT,
        pause_reason TEXT,
        ws_state TEXT NOT NULL DEFAULT 'none',
        revived_count INTEGER NOT NULL DEFAULT 0,
        voting_state TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_activity TEXT NOT NULL
    );
    CREATE TABLE members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        order_idx INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        role_id INTEGER,
        role_name TEXT NOT NULL DEFAULT '',
        role_prompt_snapshot TEXT NOT NULL DEFAULT '',
        user_prompt TEXT NOT NULL DEFAULT '',
        temperature REAL NOT NULL DEFAULT 0.7,
        max_tokens INTEGER NOT NULL DEFAULT 2000,
        avatar_seed TEXT NOT NULL,
        avatar_style TEXT NOT NULL DEFAULT 'bust',
        avatar_options TEXT NOT NULL DEFAULT '{}',
        avatar_color TEXT NOT NULL DEFAULT '#7a8c5e',
        avatar_path TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        number INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        summary TEXT,
        summary_error TEXT,
        summary_model TEXT,
        summary_tokens_in INTEGER NOT NULL DEFAULT 0,
        summary_tokens_out INTEGER NOT NULL DEFAULT 0,
        started_at TEXT,
        ended_at TEXT,
        summarized_at TEXT,
        UNIQUE (symposium_id, number)
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        round_id INTEGER,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        member_id INTEGER,
        content TEXT NOT NULL,
        thinking TEXT,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        model TEXT,
        meta TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_messages_symposium ON messages(symposium_id, seq);
    CREATE TABLE subagent_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        round_id INTEGER,
        member_id INTEGER,
        agent TEXT NOT NULL,
        params TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'ok',
        result TEXT,
        error TEXT,
        raw_path TEXT,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE hitl_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        member_id INTEGER NOT NULL,
        round_id INTEGER,
        question TEXT NOT NULL,
        options TEXT NOT NULL DEFAULT '[]',
        context TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        answer TEXT,
        answered_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE rag_folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_by INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE TABLE rag_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        folder_id INTEGER NOT NULL REFERENCES rag_folders(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        size INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'queued',
        error TEXT,
        chunks INTEGER NOT NULL DEFAULT 0,
        dims INTEGER,
        created_by INTEGER,
        created_at TEXT NOT NULL,
        indexed_at TEXT
    );
    CREATE TABLE rag_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER NOT NULL REFERENCES rag_files(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        content TEXT NOT NULL,
        vector BLOB
    );
    CREATE INDEX idx_chunks_file ON rag_chunks(file_id);
    CREATE TABLE rag_attachments (
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        folder_id INTEGER NOT NULL REFERENCES rag_folders(id) ON DELETE CASCADE,
        attached_at TEXT NOT NULL,
        PRIMARY KEY (symposium_id, folder_id)
    );
    CREATE TABLE token_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER,
        member_id INTEGER,
        round_id INTEGER,
        call_type TEXT NOT NULL,
        profile_id INTEGER,
        model TEXT,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_usage_symposium ON token_usage(symposium_id);
    CREATE TABLE profile_usage (
        profile_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        requests INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (profile_id, day)
    );
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        object_type TEXT NOT NULL DEFAULT '',
        object_id TEXT NOT NULL DEFAULT '',
        details TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_audit_created ON audit_log(created_at);
    """,
    # ---- v2: NG_05/06/07 rework ----------------------------------------------
    # role metadata (default/hidden flags + UI description), role prompt
    # versioning, prompt change notes, workspace stop reason.
    """
    ALTER TABLE role_templates ADD COLUMN description TEXT NOT NULL DEFAULT '';
    ALTER TABLE role_templates ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE role_templates ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE system_prompts ADD COLUMN note TEXT;
    ALTER TABLE symposia ADD COLUMN ws_reason TEXT;
    CREATE TABLE role_prompt_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role_id INTEGER NOT NULL REFERENCES role_templates(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        content TEXT NOT NULL,
        note TEXT,
        updated_by INTEGER,
        created_at TEXT NOT NULL,
        UNIQUE (role_id, version)
    );
    """,
    # ---- v3: v0.2.2 real-model robustness ------------------------------------
    # provider-specific request payload (e.g. Zhipu {"thinking":{"type":
    # "disabled"}}) merged into every chat completion of the profile.
    """
    ALTER TABLE api_profiles ADD COLUMN extra_body TEXT;
    """,
    # ---- v4: v0.2.3 reasoning control + round limit --------------------------
    # thinking_mode: 'default' (profile decides) | 'off' | 'on'
    # thinking_depth: NULL (model decides) | 'low' | 'medium' | 'high'
    # members may override the symposium-level values (NULL = follow it).
    # max_rounds: NULL (use the global default) | 0 (unlimited) | N — reaching
    # the limit moves the symposium to an automatic final vote.
    """
    ALTER TABLE symposia ADD COLUMN thinking_mode TEXT NOT NULL DEFAULT 'default';
    ALTER TABLE symposia ADD COLUMN thinking_depth TEXT;
    ALTER TABLE symposia ADD COLUMN max_rounds INTEGER;
    ALTER TABLE members ADD COLUMN thinking_mode TEXT;
    ALTER TABLE members ADD COLUMN thinking_depth TEXT;
    """,
    # ---- v5: v0.4 protocol v2 -------------------------------------------------
    # limits: JSON-политика лимитера профиля (см. app/llm/limits.py); старые
    #   колонки переносятся и дальше не используются.
    # form / vote_policy / cooldown_round / end_reason: форма дискуссии,
    #   правило голосования, «остывание» после отклонённого голосования,
    #   причина завершения.
    # evidence / checklist_items / issues: реестры симпозиума.
    """
    ALTER TABLE api_profiles ADD COLUMN limits TEXT;
    UPDATE api_profiles SET limits = json_object(
        'enabled', 1, 'max_concurrent', max_concurrent, 'min_gap_ms', min_pause_ms,
        'rpm', requests_per_minute, 'rph', requests_per_hour, 'rpd', requests_per_day
    );
    ALTER TABLE symposia ADD COLUMN form TEXT NOT NULL DEFAULT 'free';
    ALTER TABLE symposia ADD COLUMN vote_policy TEXT NOT NULL DEFAULT 'unanimous';
    ALTER TABLE symposia ADD COLUMN cooldown_round INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE symposia ADD COLUMN end_reason TEXT;
    CREATE TABLE evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        ref TEXT NOT NULL,
        member_id INTEGER,
        round_id INTEGER,
        agent TEXT NOT NULL,
        query TEXT NOT NULL DEFAULT '',
        digest TEXT NOT NULL DEFAULT '',
        ok INTEGER NOT NULL DEFAULT 1,
        raw_path TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (symposium_id, ref)
    );
    CREATE TABLE checklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        ref TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        updated_round INTEGER NOT NULL DEFAULT 0,
        UNIQUE (symposium_id, ref)
    );
    CREATE TABLE issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symposium_id INTEGER NOT NULL REFERENCES symposia(id) ON DELETE CASCADE,
        ref TEXT NOT NULL,
        owner_member_id INTEGER,
        text TEXT NOT NULL,
        satisfied_if TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        opened_round INTEGER NOT NULL DEFAULT 0,
        resolved_round INTEGER,
        UNIQUE (symposium_id, ref)
    );
    DELETE FROM system_prompts WHERE key IN (
        'turn_protocol', 'symposium_rules', 'vote_prompt', 'subagents_instruction',
        'secretary_open', 'secretary_summarize', 'secretary_report'
    );
    """,
    # ---- v6: мышление на уровне модели, участники вкл/выкл, самовосстановление ----
    # thinking_mode/depth профиля — «потолок» для участников (llm/thinking.py);
    # members.enabled — участник выключен (не говорит и не голосует);
    # recovery_attempts — подряд идущие автовосстановления после некритичных ошибок;
    # fallback_profile_id — запасной LLM-профиль на случай недоступности основного.
    """
    ALTER TABLE api_profiles ADD COLUMN thinking_mode TEXT NOT NULL DEFAULT 'default';
    ALTER TABLE api_profiles ADD COLUMN thinking_depth TEXT;
    ALTER TABLE members ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE symposia ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE symposia ADD COLUMN fallback_profile_id INTEGER;
    """,
    # ---- v7: профиль человека, персонажи, личные настройки участников ----
    # users.about — строка о человеке (имя и пол уже есть) для обращения моделей;
    # symposia.end_proposal — предложение завершить откладывается до конца раунда,
    #   чтобы выступили ВСЕ участники;
    # characters — пресеты участников; members получают снимок персонажа плюс
    #   личный профиль LLM, разрешённые инструменты, личные RAG-папки;
    # messages.tokens_thinking — токены мышления отдельно от текста.
    """
    ALTER TABLE users ADD COLUMN about TEXT NOT NULL DEFAULT '';
    ALTER TABLE symposia ADD COLUMN end_proposal TEXT;
    ALTER TABLE messages ADD COLUMN tokens_thinking INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE members ADD COLUMN character_id INTEGER;
    ALTER TABLE members ADD COLUMN api_profile_id INTEGER;
    ALTER TABLE members ADD COLUMN tools TEXT;
    ALTER TABLE members ADD COLUMN rag_folder_ids TEXT;
    ALTER TABLE members ADD COLUMN gender TEXT NOT NULL DEFAULT '';
    ALTER TABLE members ADD COLUMN speech_style TEXT NOT NULL DEFAULT '';
    ALTER TABLE members ADD COLUMN subagent_limit INTEGER;
    CREATE TABLE characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        key TEXT,
        is_default INTEGER NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        role_id INTEGER,
        personal_prompt TEXT NOT NULL DEFAULT '',
        speech_style TEXT NOT NULL DEFAULT '',
        gender TEXT NOT NULL DEFAULT '',
        avatar_seed TEXT NOT NULL,
        avatar_style TEXT NOT NULL DEFAULT 'bust',
        avatar_color TEXT NOT NULL DEFAULT '#888888',
        api_profile_id INTEGER,
        temperature REAL NOT NULL DEFAULT 0.7,
        max_tokens INTEGER NOT NULL DEFAULT 2000,
        thinking_mode TEXT,
        thinking_depth TEXT,
        tools TEXT,
        rag_folder_ids TEXT,
        subagent_limit INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_characters_key ON characters(key) WHERE key IS NOT NULL;
    """,
    # ---- v8: роли убраны из продукта (персонаж несёт собственный промпт), профиль-фоллбэк,
    # редактируемый облик, локализованные подписи встроенных персонажей ----
    """
    ALTER TABLE characters ADD COLUMN title TEXT NOT NULL DEFAULT '';
    ALTER TABLE characters ADD COLUMN profile_fallback INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE characters ADD COLUMN avatar_params TEXT;
    ALTER TABLE characters ADD COLUMN i18n TEXT;
    ALTER TABLE members ADD COLUMN profile_fallback INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE members ADD COLUMN avatar_params TEXT;
    DELETE FROM characters WHERE is_default=1;
    """,
]


def init_db(db_path: Path) -> None:
    global _conn
    with _lock:
        if _conn is not None:
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=10000")
        version = _conn.execute("PRAGMA user_version").fetchone()[0]
        for idx, script in enumerate(MIGRATIONS, start=1):
            if idx > version:
                log.info("applying schema migration v%d", idx)
                _conn.executescript(script)
                _conn.execute(f"PRAGMA user_version={idx}")
        _conn.commit()


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.commit()
            _conn.close()
            _conn = None


def conn() -> sqlite3.Connection:
    assert _conn is not None, "init_db() must be called first"
    return _conn


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run a mutation, commit, return lastrowid."""
    with _lock:
        cur = _conn.execute(sql, tuple(params))
        _conn.commit()
        return cur.lastrowid


def execute_many(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    with _lock:
        _conn.executemany(sql, [tuple(r) for r in rows])
        _conn.commit()


def query_all(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        rows = _conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    with _lock:
        row = _conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None


def query_value(sql: str, params: Iterable[Any] = ()) -> Any:
    with _lock:
        row = _conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None


# ------------------------------------------------------------------ helpers

def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return default
    value = row["value"]
    if isinstance(default, bool):
        return value in ("1", "true", "True")
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    return value


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value if isinstance(value, str) else json.dumps(value)),
    )


def all_settings() -> dict:
    return {r["key"]: r["value"] for r in query_all("SELECT key, value FROM settings")}
