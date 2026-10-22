"""API tests: auth, admin panel, RAG, export, workspace."""
from __future__ import annotations

import json
import urllib.parse
import time

from conftest import db_all, db_one, db_status, make_symposium, wait_for


class TestAuth:
    def test_wrong_password_is_vague(self, client):
        r = client.post("/api/auth/login", json={"login": "admin", "password": "nope"})
        assert r.status_code == 401
        assert "Invalid login or password" in r.text

    def test_no_registration_via_api(self, client):
        # there is no /api/auth/register route at all
        r = client.post("/api/auth/register", json={})
        assert r.status_code in (404, 405)

    def test_me_and_profile_update(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        r = client.get("/api/auth/me")
        assert r.status_code == 200 and r.json()["is_admin"] is True
        r = client.patch("/api/auth/me", json={"theme": "dark", "language": "en"})
        # v0.3: старые темы маппятся на новые (dark → starlight)
        assert r.json()["theme"] == "starlight"
        r = client.patch("/api/auth/me", json={"theme": "scholastic", "language": "uk"})
        assert r.status_code == 200 and r.json()["theme"] == "scholastic"
        r = client.patch("/api/auth/me", json={"theme": "light", "language": "ru"})
        assert r.status_code == 200

    def test_password_change(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        r = client.post("/api/auth/password", json={"old_password": "wrong", "new_password": "x" * 8})
        assert r.status_code == 422
        r = client.post("/api/auth/password", json={"old_password": "admin", "new_password": "admin2x"})
        assert r.status_code == 200
        r = client.post("/api/auth/login", json={"login": "admin", "password": "admin2x"})
        assert r.status_code == 200
        # change back for other tests
        client.post("/api/auth/password", json={"old_password": "admin2x", "new_password": "admin"})

    def test_non_admin_cannot_admin(self, client, alice):
        client.post("/api/auth/login", json={"login": "alice", "password": "alice123"})
        r = client.get("/api/admin/users")
        assert r.status_code == 403
        r = client.get("/api/admin/settings")
        assert r.status_code == 403


class TestAdminPanel:
    def test_users_crud(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        r = client.post("/api/admin/users", json={
            "login": "tmpuser", "password": "secret1", "name": "Tmp",
        })
        uid = r.json()["id"]
        r = client.patch(f"/api/admin/users/{uid}", json={"is_admin": True})
        assert r.json()["is_admin"] is True
        r = client.delete(f"/api/admin/users/{uid}")
        assert r.status_code == 200



    def test_prompt_versioning_and_rollback(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        prompts = client.get("/api/admin/prompts").json()
        target = next(p for p in prompts if p["key"] == "turn_protocol")
        original = target["content"]
        r = client.put("/api/admin/prompts/turn_protocol", json={"content": original + "\n- Be terse."})
        assert r.json()["version"] >= 1
        versions = client.get("/api/admin/prompts/turn_protocol/versions").json()
        assert len(versions) >= 1
        active = [v for v in versions if v["is_active"]]
        assert len(active) == 1 and "Be terse" in active[0]["content"]
        # вторая правка поверх → откат к первой правке
        client.put("/api/admin/prompts/turn_protocol", json={"content": original + "\n- Be even terser."})
        client.post("/api/admin/prompts/turn_protocol/rollback/1")
        fresh = next(p for p in client.get("/api/admin/prompts").json() if p["key"] == "turn_protocol")
        assert "Be terse" in fresh["content"] and "even terser" not in fresh["content"]
        # v0.3: чистим за собой — сброс возвращает управление файлу
        client.post("/api/admin/prompts/turn_protocol/reset")
        fresh = next(p for p in client.get("/api/admin/prompts").json() if p["key"] == "turn_protocol")
        assert fresh["is_default"] is True and fresh["content"] == original

    def test_settings_patch(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        r = client.patch("/api/admin/settings", json={"subagent_calls_per_turn": 4})
        assert r.status_code == 200
        settings = client.get("/api/admin/settings").json()
        assert settings["subagent_calls_per_turn"] == 4
        client.patch("/api/admin/settings", json={"subagent_calls_per_turn": 3})

    def test_profile_crud_and_fake_test(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        r = client.post("/api/admin/profiles", json={
            "name": "test-profile", "base_url": "http://localhost",
            "api_key": "k", "model": "fake:basic",
            "requests_per_minute": 60, "requests_per_day": 1000,
        })
        pid = r.json()["id"]
        r = client.post(f"/api/admin/profiles/{pid}/test")
        assert r.json()["ok"] is True
        profiles = client.get("/api/admin/profiles").json()
        p = next(x for x in profiles if x["id"] == pid)
        assert p["limits"]["rpm"] == 60 and p["limits"]["rpd"] == 1000 and p["limits"]["enabled"]
        client.delete(f"/api/admin/profiles/{pid}")

    def test_audit_log_and_export(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        rows = client.get("/api/admin/audit?limit=10").json()
        assert rows["total"] > 0
        r = client.get("/api/admin/audit/export")
        assert r.status_code == 200 and "login" in r.text


class TestRAG:
    def test_folder_and_file_flow(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        # use the fake embedding provider for an offline test
        client.patch("/api/admin/settings", json={"embedding_provider": "fake"})
        try:
            r = client.post("/api/rag/folders", json={"name": "Test Docs", "description": "d"})
            fid = r.json()["id"]
            files = {"files": ("note.txt", b"project requirements state the garden needs three zones", "text/plain")}
            r = client.post(f"/api/rag/folders/{fid}/files", files=files)
            assert r.status_code == 200
            file_row = r.json()["files"][0]
            wait_for(lambda: db_one(
                "SELECT status FROM rag_files WHERE id=?", (file_row["id"],)
            )["status"] == "done", timeout=15)
            row = db_one("SELECT * FROM rag_files WHERE id=?", (file_row["id"],))
            assert row["chunks"] >= 1
            # preview
            r = client.get(f"/api/rag/files/{file_row['id']}/preview")
            assert r.status_code == 200 and r.json()["chunks"]

            # archivarius via a symposium with the folder attached
            sid = make_symposium(
                client, "admin", "admin", "fake:rag", start=True,
                rag_folder_ids=[fid],
            )
            wait_for(lambda: db_one(
                "SELECT COUNT(*) AS c FROM subagent_calls WHERE symposium_id=? AND agent='archivarius'",
                (sid,)
            )["c"] >= 1, timeout=40)
            wait_for(lambda: db_status(sid) == "finished", timeout=40)
            call = db_one(
                "SELECT * FROM subagent_calls WHERE symposium_id=? AND agent='archivarius'",
                (sid,)
            )
            # exact-text query returns a hit with source citation
            assert call["status"] == "ok"
            # attach/detach events recorded
            events = db_all(
                "SELECT * FROM messages WHERE symposium_id=? AND kind='system'", (sid,)
            )
            assert any("[RAG_FOLDER_ATTACHED: Test Docs]" in m["content"] for m in events)

            # delete the file -> chunks gone
            client.delete(f"/api/rag/files/{file_row['id']}")
            assert db_one("SELECT COUNT(*) AS c FROM rag_chunks", ())["c"] == 0
            client.delete(f"/api/rag/folders/{fid}")
        finally:
            client.patch("/api/admin/settings", json={"embedding_provider": "ollama"})


class TestExport:
    def test_markdown_and_json(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic")
        wait_for(lambda: db_status(sid) == "finished", timeout=40)
        r = client.post(f"/api/symposia/{sid}/export", json={"format": "md", "download": True})
        assert r.status_code == 200 and "# Test" in r.text
        assert "Round 1" in r.text
        # v0.3: имя файла {TITLE}_{id}.md (UTF-8 → percent-encoded в filename*)
        cd = r.headers["content-disposition"]
        assert 'filename*=UTF-8''' in cd
        assert "Test_fake:basic_" in urllib.parse.unquote(cd)
        assert '.md"' in cd
        r = client.post(f"/api/symposia/{sid}/export", json={"format": "json"})
        data = r.json()
        assert data["export_version"] == 2
        assert data["members"] and data["messages"]
        assert data["members"][0]["avatar"]["seed"]
        # selected messages only (legacy option)
        some_id = data["messages"][0]["id"]
        r = client.post(f"/api/symposia/{sid}/export",
                        json={"format": "md", "message_ids": [some_id]})
        assert "Round 1" not in r.text or r.text.count("**") <= 4

    def test_filters(self, client, admin):
        """v0.3: фильтры общение / общение+thinking / полный дамп."""
        sid = make_symposium(client, "admin", "admin", "fake:basic")
        wait_for(lambda: db_status(sid) == "finished", timeout=40)
        disc = client.post(f"/api/symposia/{sid}/export", json={"format": "md", "filter": "discussion"}).text
        full = client.post(f"/api/symposia/{sid}/export", json={"format": "md", "filter": "full"}).text
        # системные события только в полном дампе
        assert "[ERROR:" not in disc and "[SYMPOSIUM ENDED" not in disc
        assert "[SYMPOSIUM ENDED" in full
        # thinking не в «общении»
        assert "<details>" not in disc
        # режим просмотра — inline; кириллица в имени не роняет ответ
        r = client.post(f"/api/symposia/{sid}/export", json={"format": "md", "download": False})
        assert "inline" in r.headers["content-disposition"]
        client.patch(f"/api/symposia/{sid}", json={"title": "Кириллическое название"})
        r = client.post(f"/api/symposia/{sid}/export", json={"format": "md"})
        assert r.status_code == 200
        assert "filename*=UTF-8" in r.headers["content-disposition"]
        client.patch(f"/api/symposia/{sid}", json={"title": f"Test {sid}"})
        # неизвестный фильтр — 422
        r = client.post(f"/api/symposia/{sid}/export", json={"format": "md", "filter": "bogus"})
        assert r.status_code == 422


class TestWorkspace:
    def test_tree_read_download_upload(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        # upload a chat file
        files = {"files": ("notes.md", b"# notes\nhello garden", "text/markdown")}
        r = client.post(f"/api/symposia/{sid}/uploads", files=files)
        assert r.status_code == 200
        rel = r.json()["files"][0]["path"]
        # workspace tree
        r = client.get(f"/api/symposia/{sid}/workspace")
        tree = r.json()["tree"]
        names = _walk_names(tree)
        assert "uploads" in names and "avatars" in names
        # read the file back
        r = client.get(f"/api/symposia/{sid}/workspace/file", params={"path": rel})
        assert r.status_code == 200 and "hello garden" in r.json()["content"]
        # download
        r = client.get(f"/api/symposia/{sid}/workspace/download", params={"path": rel})
        assert r.status_code == 200 and b"hello garden" in r.content
        # path escape blocked
        r = client.get(f"/api/symposia/{sid}/workspace/file", params={"path": "../../app/db.py"})
        assert r.status_code == 403


def _walk_names(nodes):
    out = []
    for n in nodes:
        out.append(n["name"])
        out.extend(_walk_names(n.get("children", [])))
    return out


class TestIsolation:
    def test_avatar_endpoint_deterministic(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        member = db_one(
            "SELECT * FROM members WHERE symposium_id=? ORDER BY order_idx", (sid,)
        )
        r1 = client.get(f"/api/symposia/{sid}/members/{member['id']}/avatar")
        r2 = client.get(f"/api/symposia/{sid}/members/{member['id']}/avatar")
        assert r1.status_code == 200
        assert r1.content == r2.content
        assert b"<svg" in r1.content


class TestTokenStats:
    def test_admin_scope_and_breakdown(self, client, admin):
        # create a symposium and generate some usage
        sid = make_symposium(client, "admin", "admin", "fake:basic")
        wait_for(lambda: db_status(sid) == "finished", timeout=30)
        r = client.get("/api/stats/tokens")
        assert r.status_code == 200
        data = r.json()
        assert data["scope"] == "all"
        assert data["total"]["tokens"] > 0
        assert data["total"]["calls"] > 0
        assert any(s["id"] == sid for s in data["by_symposium"])
        assert data["by_profile"] and data["by_profile"][0]["tokens"] > 0
        assert data["by_type"]  # turn / summary / opening / report

    def test_user_scope_only_own(self, client, alice):
        from app import db as _db
        # the session cookie may belong to admin from an earlier test -> re-login
        client.post("/api/auth/login", json={"login": "alice", "password": "alice123"})
        alice_ids = {
            r["id"] for r in _db.query_all("SELECT id FROM symposia WHERE owner_id=?", (alice["id"],))
        }
        r = client.get("/api/stats/tokens")
        assert r.status_code == 200
        data = r.json()
        assert data["scope"] == "own"
        # only alice-owned symposia ever appear in her breakdown
        assert {s["id"] for s in data["by_symposium"]} <= alice_ids
        # restore the admin session for the following tests
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})



class TestPromptResetAndNote:
    def test_reset_and_note(self, client, admin):
        prompts = client.get("/api/admin/prompts").json()
        target = next(p for p in prompts if p["key"] == "turn_protocol")
        assert target["is_default"] is True
        assert target["default_content"]

        r = client.put("/api/admin/prompts/turn_protocol", json={
            "content": "Broken protocol.", "note": "v2 experiment",
        })
        assert r.status_code == 200
        prompts = client.get("/api/admin/prompts").json()
        target = next(p for p in prompts if p["key"] == "turn_protocol")
        assert target["is_default"] is False and target["note"] == "v2 experiment"

        r = client.post("/api/admin/prompts/turn_protocol/reset")
        assert r.status_code == 200
        prompts = client.get("/api/admin/prompts").json()
        target = next(p for p in prompts if p["key"] == "turn_protocol")
        assert target["is_default"] is True

        versions = client.get("/api/admin/prompts/turn_protocol/versions").json()
        # v0.3: сброс удаляет правки — файл снова источник; версий нет
        assert versions == []


class TestWebSearchAdmin:
    def test_status_and_disabled_test(self, client, admin):
        from app import db as _db
        _db.set_setting("web_search_enabled", "0")
        r = client.get("/api/admin/web-search")
        assert r.status_code == 200
        assert r.json()["engines"] == []  # disabled -> no active engines
        r = client.post("/api/admin/web-search/test", json={"query": "x"})
        assert r.status_code == 200
        assert r.json()["ok"] is False
        _db.set_setting("web_search_enabled", "1")

    def test_search_mocked(self, client, admin, monkeypatch):
        from app.subagents import search as web_search
        from app import db as _db
        _db.set_setting("web_search_enabled", "1")

        async def fake_search(q):
            return [{"title": "T1", "url": "https://a", "snippet": "s", "engine": "google"},
                    {"title": "T2", "url": "https://b", "snippet": "", "engine": "bing"}]
        monkeypatch.setattr(web_search, "search", fake_search)
        # admin route imports the module dynamically -> patch module attribute works
        r = client.post("/api/admin/web-search/test", json={"query": "q"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["per_engine"] == {"google": 1, "bing": 1}
        assert len(data["results"]) == 2


class TestWorkspaceLifecycleV02:
    def test_wake_and_reason(self, client, admin, alice):
        sid = make_symposium(client, "alice", "alice123", "fake:basic", start=False)
        # admin (not the owner) may wake and stop; owner GET auto-wakes otherwise
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        r = client.post(f"/api/symposia/{sid}/workspace/wake")
        assert r.status_code == 200
        assert r.json()["state"] == "running"
        client.post("/api/auth/login", json={"login": "alice", "password": "alice123"})
        detail = client.get(f"/api/symposia/{sid}").json()
        assert detail["ws_state"] == "running"
        assert detail["ws_mode"] == "local"
        # admin stop records the reason
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        r = client.post(f"/api/symposia/{sid}/workspace/stop")
        assert r.status_code == 200
        tree = client.get(f"/api/symposia/{sid}/workspace").json()
        assert tree["state"] == "stopped"
        assert tree["reason"] == "admin"
        detail = client.get(f"/api/symposia/{sid}").json()
        assert detail["ws_reason"] == "admin"

    def test_wake_error_message_when_docker_forbidden(self, client, admin):
        from app import db as _db
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        _db.set_setting("workspace_mode", "docker")
        _db.set_setting("workspace_allow_local", "0")
        from app.workspace import manager as ws
        monkey_state = {"docker": False}
        async def fake_docker_available():
            return monkey_state["docker"]
        orig = ws.docker_available
        ws.docker_available = fake_docker_available
        try:
            r = client.post(f"/api/symposia/{sid}/workspace/wake")
            assert r.status_code == 409
            detail = r.json()["detail"]
            assert "Docker" in str(detail)
        finally:
            ws.docker_available = orig
            _db.set_setting("workspace_mode", "local")
            _db.set_setting("workspace_allow_local", "1")
