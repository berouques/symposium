"""Test fixtures: isolated data dir, running app, fake LLM profiles."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

DATA_ROOT = Path(tempfile.mkdtemp(prefix="symposium-tests-"))


@pytest.fixture(scope="session", autouse=True)
def _data_dir():
    os.environ["SYMPOSIUM_DATA_DIR"] = str(DATA_ROOT)
    # deterministic offline test environment
    os.environ.pop("SYMPOSIUM_BOOT_API_KEY", None)
    os.environ.pop("SYMPOSIUM_BOOT_BASE_URL", None)
    yield
    shutil.rmtree(DATA_ROOT, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _offline_settings():
    """Deterministic offline tests: local sandbox, no real web search.

    Initializes the DB directly (settings are insert-if-missing, so the later
    app bootstrap will not overwrite these values).
    """
    from app import db
    from app.config import get_config
    cfg = get_config()
    cfg.ensure_dirs()
    db.init_db(cfg.db_path)
    db.set_setting("workspace_mode", "local")   # host-subprocess sandbox in CI
    db.set_setting("workspace_allow_local", "1")
    db.set_setting("web_search_enabled", "0")   # engines are unit-tested with mocks
    db.set_setting("auto_recovery", "0")        # тесты сами включают самовосстановление
    yield
    db.close_db()


@pytest.fixture(scope="session")
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin(client):
    """First admin created at bootstrap (admin/admin by default)."""
    r = client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    return r.json()["user"]


@pytest.fixture(scope="session")
def alice(client, admin):
    """Regular user created by the admin."""
    r = client.post(
        "/api/admin/users",
        json={"login": "alice", "password": "alice123", "name": "Alice"},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/auth/login", json={"login": "alice", "password": "alice123"})
    assert r.status_code == 200, r.text
    return r.json()["user"]


@pytest.fixture()
def fake_profile(client, admin):
    """A fake LLM API profile (offline)."""
    def _make(model: str):
        r = client.post(
            "/api/admin/profiles",
            json={
                "name": f"fake {model}",
                "base_url": "http://localhost",
                "api_key": "fake",
                "model": model,
                "max_concurrent": 1,
                "min_pause_ms": 0,
            },
        )
        assert r.status_code == 200, r.text
        return r.json()["id"]
    return _make


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.1):
    """Poll until predicate() is truthy (engine runs in the app's loop)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError("condition not reached in time")


def db_status(sid: int) -> str:
    from app import db
    return db.query_value("SELECT status FROM symposia WHERE id=?", (sid,))


def db_one(sql: str, params=()):
    from app import db
    return db.query_one(sql, params)


def db_all(sql: str, params=()):
    from app import db
    return db.query_all(sql, params)


def make_symposium(client, user_login: str, password: str, model: str,
                   members: list[dict] | None = None, start: bool = True, **extra):
    client.post("/api/auth/login", json={"login": user_login, "password": password})
    from app import db as _db
    from app.auth import utcnow
    pid = _db.execute(
        "INSERT INTO api_profiles(name, base_url, api_key, model, max_concurrent, "
        "min_pause_ms, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (f"profile-{model}-{time.time_ns()}", "http://localhost", "k", model, 1, 0,
         utcnow(), utcnow()),
    )
    if members is None:
        members = [
            {"name": "Alpha", "description": "first speaker", "temperature": 0.5, "max_tokens": 300},
            {"name": "Beta", "description": "second speaker", "temperature": 0.5, "max_tokens": 300},
            {"name": "Gamma", "description": "third speaker", "temperature": 0.5, "max_tokens": 300},
        ]
    body = {
        "title": f"Test {model}", "topic": "Test topic for the symposium",
        "completion_criterion": "All participants agree the test is complete",
        "report_format": "text with sections", "language": "en",
        "api_profile_id": pid, "members": members, "start_now": start,
    }
    body.update(extra)
    r = client.post("/api/symposia", json=body)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    return sid
