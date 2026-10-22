"""Application configuration: boot-level settings from environment / .env.

Values are resolved with the following precedence:
1. real process environment variables (always win),
2. the `.env` file next to the working directory (or SYMPOSIUM_ENV path),
3. built-in defaults.

Default ports (user requirement):
- SYMPOSIUM_PORT    = 23606  backend API (also serves the SPA directly),
- SYMPOSIUM_UI_PORT = 23607  standalone frontend listener with a reverse
                            proxy to the backend (set `off`/`0` to disable).
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, '#' comments, optional quotes."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split(" #")[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


_env_file_cache: dict[str, str] | None = None


def _load_env_file() -> dict[str, str]:
    global _env_file_cache
    if _env_file_cache is None:
        candidates: list[Path] = []
        env_path = os.environ.get("SYMPOSIUM_ENV")
        if env_path:
            candidates.append(Path(env_path))
        candidates.append(Path.cwd() / ".env")
        merged: dict[str, str] = {}
        for cand in candidates:
            if cand.is_file():
                merged.update(_parse_env_file(cand))
        _env_file_cache = merged
    return _env_file_cache


def _env(name: str, default: str) -> str:
    if name in os.environ and os.environ[name] != "":
        return os.environ[name]
    return _load_env_file().get(name, default)


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_env("SYMPOSIUM_DATA_DIR", "./data")))
    host: str = field(default_factory=lambda: _env("SYMPOSIUM_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("SYMPOSIUM_PORT", "23606")))
    # Standalone UI listener; "off"/"0"/empty disables the split mode.
    ui_port: int = field(default_factory=lambda: _parse_ui_port(_env("SYMPOSIUM_UI_PORT", "23607")))
    log_level: str = field(default_factory=lambda: _env("SYMPOSIUM_LOG_LEVEL", "INFO"))
    admin_login: str = field(default_factory=lambda: _env("SYMPOSIUM_ADMIN_LOGIN", "admin"))
    admin_password: str = field(default_factory=lambda: _env("SYMPOSIUM_ADMIN_PASSWORD", "admin"))
    admin_name: str = field(default_factory=lambda: _env("SYMPOSIUM_ADMIN_NAME", "Administrator"))
    secret: str = field(default_factory=lambda: _env("SYMPOSIUM_SECRET", ""))
    ollama_url: str = field(default_factory=lambda: _env("SYMPOSIUM_OLLAMA_URL", "http://localhost:11434"))
    # Эмбеддинги RAG (Ollama). Это ДЕФОЛТЫ для настроек БД: после первого старта
    # значения правятся в админ-панели, .env их больше не перезаписывает.
    embedding_model: str = field(default_factory=lambda: _env("SYMPOSIUM_EMBEDDING_MODEL", "nomic-embed-text"))
    embedding_dims: str = field(default_factory=lambda: _env("SYMPOSIUM_EMBEDDING_DIMS", "768"))
    # Веб-поиск через SearXNG (JSON API). Пусто — движок searxng неактивен.
    searxng_url: str = field(default_factory=lambda: _env("SYMPOSIUM_SEARXNG_URL", ""))
    # Список движков поиска JSON-массивом; searxng добавляется, если задан URL.
    web_search_engines: str = field(default_factory=lambda: _env("SYMPOSIUM_WEB_SEARCH_ENGINES", ""))
    # Песочница кода (docker): образ и сеть контейнера.
    sandbox_image: str = field(default_factory=lambda: _env("SYMPOSIUM_SANDBOX_IMAGE", "python:3.12-slim"))
    sandbox_network: str = field(default_factory=lambda: _env("SYMPOSIUM_SANDBOX_NETWORK", "bridge"))
    # Пресет лимитов для профиля из SYMPOSIUM_BOOT_* (см. app/llm/limits.py): пусто — без лимитов.
    boot_limits_preset: str = field(default_factory=lambda: _env("SYMPOSIUM_BOOT_LIMITS_PRESET", ""))
    # First-boot LLM profile created automatically (so the API key can be set
    # from .env instead of the admin UI). All fields must be non-empty.
    boot_profile_name: str = field(default_factory=lambda: _env("SYMPOSIUM_BOOT_PROFILE_NAME", "OpenRouter"))
    boot_profile_base_url: str = field(default_factory=lambda: _env("SYMPOSIUM_BOOT_BASE_URL", ""))
    boot_profile_api_key: str = field(default_factory=lambda: _env("SYMPOSIUM_BOOT_API_KEY", ""))
    boot_profile_model: str = field(default_factory=lambda: _env("SYMPOSIUM_BOOT_MODEL", ""))
    # Каталог дефолтных промптов (markdown). Пусто → <корень проекта>/prompts.
    # Файлы перечитываются с диска при изменении (см. app/prompt_store.py).
    prompts_dir: str = field(default_factory=lambda: _env("SYMPOSIUM_PROMPTS_DIR", ""))

    def frontend_dir(self) -> Path | None:
        """Собранный фронтенд: frontend/dist (после ./sym build); иначе заглушка
        frontend/placeholder; None — раздавать нечего."""
        root = Path(__file__).resolve().parent.parent / "frontend"
        for cand in (root / "dist", root / "placeholder"):
            if (cand / "index.html").is_file():
                return cand
        return None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def rag_files_dir(self) -> Path:
        return self.data_dir / "rag" / "files"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backup"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def ui_enabled(self) -> bool:
        return self.ui_port is not None and self.ui_port != self.port

    @property
    def boot_profile_ready(self) -> bool:
        return bool(
            self.boot_profile_base_url.strip()
            and self.boot_profile_api_key.strip()
            and self.boot_profile_model.strip()
        )

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.log_dir, self.workspaces_dir, self.rag_files_dir, self.backup_dir):
            p.mkdir(parents=True, exist_ok=True)

    def load_or_create_secret(self) -> str:
        if self.secret:
            return self.secret
        if self.secret_path.exists():
            return self.secret_path.read_text().strip()
        key = secrets.token_urlsafe(48)
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        self.secret_path.write_text(key)
        try:
            os.chmod(self.secret_path, 0o600)
        except OSError:
            pass
        return key


def _parse_ui_port(raw: str) -> int | None:
    if raw.strip().lower() in ("off", "no", "none", "disabled", "0", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        return 23607


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
