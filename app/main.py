"""FastAPI application assembly."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import (
    admin_routes, auth_routes, character_routes, rag_routes, stats_routes, symposia_routes, terminal,
    workspace_routes,
)
from .auth import hash_password, utcnow
from .config import get_config
from .engine.manager import manager
from .llm import limits as limits_mod
from .logging_setup import get_logger, setup_logging
from .rag import indexer
from .seeds import seed_defaults
from .workspace import manager as ws_manager

log = get_logger("main")

VERSION = "0.5.0"


def bootstrap() -> None:
    """Init DB, first admin, seeds. Idempotent; used by app + CLI."""
    cfg = get_config()
    cfg.ensure_dirs()
    cfg.load_or_create_secret()
    db.init_db(cfg.db_path)
    if db.query_value("SELECT COUNT(*) FROM users") == 0:
        login = cfg.admin_login
        password = cfg.admin_password
        now = utcnow()
        db.execute(
            "INSERT INTO users(login, password_hash, name, is_admin, language, theme, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (login, hash_password(password), cfg.admin_name or "Administrator", 1, "ru", "light", now, now),
        )
        log.info("first admin '%s' created", login)
    admin_id = db.query_value("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1")
    seed_defaults(admin_user_id=admin_id)
    _bootstrap_profile_from_env()


def _bootstrap_profile_from_env() -> None:
    """First-boot LLM provider profile from SYMPOSIUM_BOOT_* env/.env vars
    (the 'how do I set the API key' path without the admin UI)."""
    cfg = get_config()
    if not cfg.boot_profile_ready:
        return
    if db.query_value("SELECT COUNT(*) FROM api_profiles") > 0:
        return
    now = utcnow()
    # Zhipu GLM: disable interleaved thinking by default (v0.2.2) — without
    # this the chain-of-thought leaks into the reply and eats the token budget
    extra = ''
    if "bigmodel.cn" in cfg.boot_profile_base_url.lower():
        extra = '{"thinking": {"type": "disabled"}}'
    pid = db.execute(
        "INSERT INTO api_profiles(name, base_url, api_key, model, max_concurrent, "
        "min_pause_ms, limits, extra_body, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (cfg.boot_profile_name, cfg.boot_profile_base_url, cfg.boot_profile_api_key,
         cfg.boot_profile_model, 1, 0,
         json.dumps(limits_mod.preset(cfg.boot_limits_preset) or limits_mod.normalize(None)),
         extra, now, now),
    )
    log.info("created first LLM profile '%s' (model %s) from SYMPOSIUM_BOOT_* settings",
             cfg.boot_profile_name, cfg.boot_profile_model)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    manager.resume_active()
    indexer.start_worker()
    await ws_manager.start_monitor()
    log.info("symposium backend ready (data dir: %s)", get_config().data_dir)
    yield
    await manager.shutdown()
    await indexer.stop_worker()
    await ws_manager.stop_monitor()
    db.close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Symposium",
        version=VERSION,
        description="LLM round-table symposium system (PoC)",
        lifespan=lifespan,
    )

    app.include_router(auth_routes.router)
    app.include_router(symposia_routes.router)
    app.include_router(workspace_routes.router)
    app.include_router(rag_routes.router)
    app.include_router(terminal.router)
    app.include_router(character_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(stats_routes.router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": VERSION}

    @app.get("/api/avatar/preview")
    def avatar_preview(name: str = "", description: str = "", role: str = ""):
        from .avatars import generate_svg, make_seed
        from fastapi.responses import Response
        seed = make_seed(name or "?", description or "", role or "")
        svg = generate_svg(seed)
        return Response(content=svg, media_type="image/svg+xml")

    @app.get("/api/bootstrap")
    def bootstrap_info():
        # роли сортируются по имени во всех списках (требование v0.3);
        # дефолтные промпты пересинхронизируются с папкой prompts/
        folders = db.query_all("SELECT id, name FROM rag_folders ORDER BY name")
        from .prompt_store import system_keys
        forms = [k[len("form_"):] for k in system_keys() if k.startswith("form_")]
        profiles = [{"id": p["id"], "name": p["name"], "model": p["model"],
                     "thinking_mode": p["thinking_mode"], "thinking_depth": p["thinking_depth"]}
                    for p in db.query_all("SELECT * FROM api_profiles ORDER BY id")]
        return {"rag_folders": folders, "forms": forms, "profiles": profiles,
                "vote_policies": ["unanimous", "supermajority", "majority"],
                "version": VERSION,
                "author": {"name": "Le Berouque",
                            "url": "https://github.com/berouques/symposium"}}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    frontend = get_config().frontend_dir()
    if frontend is not None:
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="frontend")
    return app


app = create_app()
