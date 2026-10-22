"""CLI: serve, create-admin, reset-password, info, status, demo.

Usage (uv):
  uv run python -m app.cli serve [--host 0.0.0.0] [--port 23606] [--ui-port 23607]
  uv run python -m app.cli create-admin <login> <password> [--name NAME]
  uv run python -m app.cli reset-password <login> <new_password>
  uv run python -m app.cli info
  uv run python -m app.cli status
  uv run python -m app.cli demo [--model fake:basic] [--no-serve]

Ports: backend API on SYMPOSIUM_PORT (default 23606), frontend UI listener on
SYMPOSIUM_UI_PORT (default 23607, disable with `off`). Both are settable in
the .env file or the process environment.
"""
from __future__ import annotations

import argparse
import sys
import threading

from . import db
from .auth import hash_password, utcnow
from .config import get_config, set_config
from .logging_setup import setup_logging


def _init(data_dir: str | None = None) -> None:
    if data_dir:
        cfg = get_config()
        cfg.data_dir = __import__("pathlib").Path(data_dir)
    cfg = get_config()
    cfg.ensure_dirs()
    setup_logging(cfg.log_dir, cfg.log_level)
    from .main import bootstrap
    bootstrap()


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .logging_setup import get_logger

    log = get_logger("cli")
    cfg = get_config()
    host = args.host or cfg.host
    port = int(args.port or cfg.port)
    ui_port = args.ui_port if args.ui_port is not None else cfg.ui_port
    use_ui = ui_port is not None and int(ui_port) != port

    if use_ui:
        def _ui_thread() -> None:
            from .ui_server import create_ui_app
            app = create_ui_app(host, port)
            server = uvicorn.Server(uvicorn.Config(
                app, host=host, port=int(ui_port),
                log_level="warning", access_log=False,
            ))
            try:
                server.run()
            except Exception as e:  # noqa: BLE001 — thread must never kill the process
                log.error("UI listener failed: %s", e)

        t = threading.Thread(target=_ui_thread, name="symposium-ui", daemon=True)
        t.start()
        print(f" * Frontend UI : http://{host}:{int(ui_port)}")
        print(f" * Backend API : http://{host}:{port} (UI also served here)")
    else:
        print(f" * Backend API + UI: http://{host}:{port} (standalone UI disabled)")
    print(f" * Data dir    : {cfg.data_dir.resolve()}")

    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host=host, port=port, log_level=cfg.log_level.lower(),
    ))
    server.run()


def cmd_serve(args: argparse.Namespace) -> None:
    _serve(args)


def cmd_create_admin(args: argparse.Namespace) -> None:
    _init(args.data_dir)
    if db.query_one("SELECT 1 FROM users WHERE login=?", (args.login,)):
        print(f"login '{args.login}' already exists")
        sys.exit(1)
    now = utcnow()
    uid = db.execute(
        "INSERT INTO users(login, password_hash, name, is_admin, language, theme, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (args.login, hash_password(args.password), args.name or args.login,
         1, "ru", "light", now, now),
    )
    print(f"user #{uid} '{args.login}' created (admin)")


def cmd_reset_password(args: argparse.Namespace) -> None:
    _init(args.data_dir)
    user = db.query_one("SELECT * FROM users WHERE login=?", (args.login,))
    if user is None:
        print(f"login '{args.login}' not found")
        sys.exit(1)
    if len(args.password) < 4:
        print("password must be at least 4 characters")
        sys.exit(1)
    db.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
        (hash_password(args.password), utcnow(), user["id"]),
    )
    print(f"password updated for '{args.login}'")


def cmd_info(args: argparse.Namespace) -> None:
    _init(args.data_dir)
    cfg = get_config()
    counts = {
        "users": db.query_value("SELECT COUNT(*) FROM users"),
        "symposia": db.query_value("SELECT COUNT(*) FROM symposia"),
        "api_profiles": db.query_value("SELECT COUNT(*) FROM api_profiles"),
        "rag_files": db.query_value("SELECT COUNT(*) FROM rag_files"),
        "rag_chunks": db.query_value("SELECT COUNT(*) FROM rag_chunks"),
    }
    print("Symposium PoC")
    print(f"  data dir : {cfg.data_dir.resolve()}")
    print(f"  database : {cfg.db_path.resolve()}")
    print(f"  ollama   : {cfg.ollama_url}")
    print(f"  api port : {cfg.port} (ui: {cfg.ui_port if cfg.ui_enabled else 'embedded'})")
    for k, v in counts.items():
        print(f"  {k:10}: {v}")


def cmd_status(args: argparse.Namespace) -> None:
    """Короткая сводка «что живо»: сервер, Ollama, SearXNG, docker. Ничего не меняет."""
    import json
    import shutil
    import subprocess

    import httpx

    _init(args.data_dir)
    cfg = get_config()
    rows: list[tuple[str, str, str]] = []

    def probe(name: str, fn) -> None:
        try:
            rows.append((name, "ok", str(fn())))
        except Exception as e:  # noqa: BLE001
            rows.append((name, "DOWN", f"{type(e).__name__}: {str(e)[:90]}"))

    def server():
        r = httpx.get(f"http://127.0.0.1:{cfg.port}/api/health", timeout=3)
        return f"{r.json().get('version')} on :{cfg.port}"

    def ollama():
        url = (db.get_setting("ollama_url", "") or cfg.ollama_url).rstrip("/")
        model = db.get_setting("embedding_model", cfg.embedding_model)
        names = [m["name"] for m in httpx.get(url + "/api/tags", timeout=3).json().get("models", [])]
        have = any(n == model or n.startswith(model + ":") for n in names)
        return f"{url}; model {model}: {'present' if have else 'NOT pulled'}"

    def searxng():
        url = (db.get_setting("web_search_searxng_url", "") or "").strip().rstrip("/")
        if not url:
            return "not configured (web_search_searxng_url is empty)"
        r = httpx.get(url + "/search", params={"q": "test", "format": "json"}, timeout=8)
        r.raise_for_status()
        return f"{url}; JSON API answered, {len(r.json().get('results', []))} results"

    def docker():
        if not shutil.which("docker"):
            raise RuntimeError("docker not found in PATH")
        out = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                             text=True, timeout=8)
        if out.returncode:
            raise RuntimeError(out.stderr.strip()[:90] or "docker daemon not reachable")
        return f"server {out.stdout.strip()}; sandbox image {db.get_setting('workspace_docker_image')}"

    probe("server", server)
    probe("ollama", ollama)
    probe("searxng", searxng)
    probe("docker", docker)
    for name, state, detail in rows:
        print(f"  {name:8} {state:5} {detail}")
    print("  llm profiles:")
    from .llm import limits as limits_mod
    for p in db.query_all("SELECT * FROM api_profiles ORDER BY id"):
        pol = limits_mod.policy_of(p)
        lim = "no limits" if not pol["enabled"] else ", ".join(
            f"{k}={pol[k]}" for k in ("max_concurrent", "min_gap_ms", "rps", "rpm", "rph", "rpd", "tpm",
                                       "tpd", "tokens_per_month") if pol[k])
        print(f"    #{p['id']} {p['name']} [{p['model']}] — {lim}")


def cmd_demo(args: argparse.Namespace) -> None:
    _init(args.data_dir)
    model = args.model
    # fake profile for offline demo
    pid = db.query_one("SELECT id FROM api_profiles WHERE model=?", (model,))
    if pid is None:
        pid = db.execute(
            "INSERT INTO api_profiles(name, base_url, api_key, model, max_concurrent, "
            "min_pause_ms, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("Offline demo (fake LLM)", "http://localhost", "fake-key", model, 1, 0,
             utcnow(), utcnow()),
        )
        print(f"created demo API profile #{pid} with model {model}")
    else:
        pid = pid["id"]

    if not db.query_one("SELECT 1 FROM users WHERE login=?", ("demo",)):
        now = utcnow()
        db.execute(
            "INSERT INTO users(login, password_hash, name, is_admin, language, theme, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("demo", hash_password("demo123"), "Demo User", 0, "ru", "light", now, now),
        )
        db.execute("UPDATE users SET active_profile_id=? WHERE login=?", (pid, "demo"))
        print("created user 'demo' (password: demo123)")

    print("demo user ready — create a symposium from the built-in characters in the UI")

    if args.no_serve:
        print("demo data ready; run `uv run python -m app.cli serve` and log in as demo/demo123")
    else:
        print("starting server; log in as demo/demo123 (admin from env or 'admin')")
        _serve(args)


def main() -> None:
    parser = argparse.ArgumentParser(prog="symposium", description="Symposium PoC CLI")
    parser.add_argument("--data-dir", default=None, help="override data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--ui-port", type=int, default=None, help="standalone UI port (default from SYMPOSIUM_UI_PORT)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("create-admin")
    p.add_argument("login")
    p.add_argument("password")
    p.add_argument("--name", default=None)
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("reset-password")
    p.add_argument("login")
    p.add_argument("password")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("info")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("demo")
    p.add_argument("--model", default="fake:basic")
    p.add_argument("--no-serve", action="store_true")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--ui-port", type=int, default=None)
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
