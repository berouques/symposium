"""v0.4.2: персонажи, профиль человека, очередь слова, возобновление сообщением,
вложения, web_fetch, личные профили/инструменты участников."""
from __future__ import annotations

import http.server
import json
import threading

from conftest import db_all, db_one, db_status, make_symposium, wait_for

from app import db
from app.avatars import generate_svg
from app.engine.fsm import pick_speaker


def msgs(sid, kind=None):
    sql = "SELECT * FROM messages WHERE symposium_id=?"
    return db_all(sql + (" AND kind=?" if kind else "") + " ORDER BY seq", (sid, kind) if kind else (sid,))


def login(client):
    client.post("/api/auth/login", json={"login": "admin", "password": "admin"})


def profile(client, model="fake:basic", name=None):
    login(client)
    return client.post("/api/admin/profiles", json={"name": name or f"p-{model}", "base_url": "http://x", "model": model}).json()["id"]


class TestCharacters:
    def test_defaults_come_from_json_files(self, client, admin):
        login(client)
        rows = client.get("/api/characters").json()
        by = {r["name"]: r for r in rows if r["is_default"]}
        assert {"Moderator", "Erudite", "Sofia", "Petronius", "Ludomir", "Fabulon", "Visual", "Ilmari", "Suvi", "Aino",
                "Annette", "Electra", "Protagoras", "Plato", "Epicurus", "Friedrich", "Oleg"} <= set(by)
        assert by["Moderator"]["tools"] == ["ask_human"] and by["Moderator"]["max_tokens"] == 350
        assert "web_explorer" in by["Erudite"]["tools"] and "archivarius" in by["Erudite"]["tools"]
        assert "coder" in by["Suvi"]["tools"] and "coder" in by["Annette"]["tools"] and "coder" not in by["Aino"]["tools"]
        assert "Олег" in by["Oleg"]["prompt"]
        ru = {r["name"]: r for r in client.get("/api/characters?lang=ru").json()}
        assert ru["Moderator"]["title"] == "Критик" and by["Moderator"]["title"] == "Critic"

    def test_json_files_are_reloaded_live(self, client, admin):
        import time

        from app import characters
        login(client)
        f = characters.DEFAULTS_DIR / "oleg.json"
        original = f.read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            data["description"] = "EDITED-LIVE"
            time.sleep(0.05)
            f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            import os
            os.utime(f, (time.time() + 5, time.time() + 5))
            rows = client.get("/api/characters").json()
            assert next(r for r in rows if r["name"] == "Oleg")["description"] == "EDITED-LIVE"
        finally:
            f.write_text(original, encoding="utf-8")
            import os
            os.utime(f, (time.time() + 10, time.time() + 10))
            client.get("/api/characters")

    def test_crud_clone_and_builtin_readonly(self, client, admin):
        login(client)
        d = next(r for r in client.get("/api/characters").json() if r["is_default"])
        assert client.patch(f"/api/characters/{d['id']}", json={"name": "X"}).status_code == 403
        assert client.delete(f"/api/characters/{d['id']}").status_code == 403
        c = client.post("/api/characters", json={"name": "Test", "title": "Tester", "temperature": 0.34, "prompt": "Be brief"}).json()
        assert c["temperature"] == 0.3 and c["tools"] == ["ask_human"] and c["max_tokens"] == 350 and c["mine"] and c["profile_fallback"]
        assert client.patch(f"/api/characters/{c['id']}", json={"tools": ["hack"]}).status_code == 422
        clone = client.post(f"/api/characters/{d['id']}/clone").json()      # «создать на основе этого»
        assert clone["name"].endswith("(copy)") and clone["mine"] and clone["prompt"] == d["prompt"]
        assert clone["avatar"]["hair"]                                       # облик зафиксирован явно
        assert client.get(f"/api/characters/{c['id']}/avatar").headers["content-type"].startswith("image/svg")
        assert client.delete(f"/api/characters/{c['id']}").status_code == 200
        assert "gender" not in c and "role" not in c

    def test_avatar_editor_four_controls(self, client, admin):
        from app import portrait
        login(client)
        opts = client.get("/api/characters/avatar-options").json()
        assert set(opts) == {"hair", "hair_color", "face", "bg", "outfit"} and "beard_full" in opts["face"]
        c = client.post("/api/characters", json={"name": "Avatar"}).json()
        r = client.patch(f"/api/characters/{c['id']}", json={"avatar": {"hair": "long", "hair_color": "#b5532a", "face": "beard_full",
                                                                          "bg": "#cfe3e0", "outfit": "#7a4fa3", "evil": "<script>"}})
        assert r.json()["avatar"] == {"hair": "long", "hair_color": "#b5532a", "face": "beard_full", "bg": "#cfe3e0", "outfit": "#7a4fa3"}
        svg = client.get(f"/api/characters/{c['id']}/avatar").text
        assert "#b5532a" in svg and "#7a4fa3" in svg and "script" not in svg          # цвет бороды = цвет волос, рамка = одежда
        prev = client.post("/api/characters/preview", json={"seed": "x", "avatar": {"face": "makeup_bold", "hair": "bun"}})
        assert prev.status_code == 200 and "<svg" in prev.text
        # любой набор параметров рисуется, независимо друг от друга
        for h in portrait.HAIR_STYLES:
            for f in portrait.FACE_FEATURES:
                assert portrait.render("s", {"hair": h, "face": f}).startswith("<svg")
        assert portrait.render("s", {"hair": "long"}) != portrait.render("s", {"hair": "bald"})
        r = client.post(f"/api/characters/{c['id']}/avatar").json()
        assert r["avatar"] == {} and r["avatar_seed"] != c["avatar_seed"]

    def test_symposium_from_characters_copies_snapshot(self, client, admin):
        login(client)
        p2 = profile(client, "fake:basic", "personal")
        fid = client.post("/api/rag/folders", json={"name": "personal-lib"}).json()["id"]
        ch = client.post("/api/characters", json={"name": "Lena", "title": "Poet", "temperature": 0.9, "max_tokens": 777,
                                                   "api_profile_id": p2, "profile_fallback": False, "tools": ["web_explorer"],
                                                   "rag_folder_ids": [fid], "prompt": "Lena persona prompt", "speech_style": "vivid",
                                                   "thinking_mode": "off", "avatar": {"hair": "long"}}).json()
        other = next(r["id"] for r in client.get("/api/characters").json() if r["name"] == "Ilmari")
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False, members=[{"character_id": ch["id"]}, {"character_id": other}])
        d = client.get(f"/api/symposia/{sid}").json()
        lena = d["members"][0]
        assert lena["name"] == "Lena" and lena["role"] == "Poet" and lena["temperature"] == 0.9 and lena["max_tokens"] == 777
        assert lena["api_profile_id"] == p2 and lena["profile_fallback"] is False and lena["tools"] == ["web_explorer"]
        assert lena["rag_folder_ids"] == [fid] and lena["thinking_mode"] == "off" and lena["character_id"] == ch["id"]
        prompt = client.get(f"/api/symposia/{sid}/members/{lena['id']}/prompt").json()["assembled_system_prompt"]
        assert "Lena persona prompt" in prompt and "vivid" in prompt
        assert 'agent="coder"' not in prompt and 'agent="web_explorer"' in prompt
        client.patch(f"/api/characters/{ch['id']}", json={"temperature": 0.1})
        assert client.get(f"/api/symposia/{sid}").json()["members"][0]["temperature"] == 0.9

    def test_member_patch_new_fields(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        m = client.get(f"/api/symposia/{sid}").json()["members"][0]
        p2 = profile(client)
        r = client.patch(f"/api/symposia/{sid}/members/{m['id']}", json={"api_profile_id": p2, "tools": ["coder", "web_fetch"], "profile_fallback": False, "prompt": "New persona"})
        assert r.status_code == 200, r.text
        m = client.get(f"/api/symposia/{sid}").json()["members"][0]
        assert m["api_profile_id"] == p2 and m["tools"] == ["coder", "web_fetch"] and m["profile_fallback"] is False
        client.patch(f"/api/symposia/{sid}/members/{m['id']}", json={"tools": ["*"], "api_profile_id": 0})
        m = client.get(f"/api/symposia/{sid}").json()["members"][0]
        assert m["tools"] is None and m["api_profile_id"] is None


class TestProfileFallback:
    def _run(self, client, own_model, fallback, delete_own=False):
        login(client)
        own = profile(client, own_model, f"own-{own_model}-{fallback}")
        ch = client.post("/api/characters", json={"name": "Solo", "api_profile_id": own, "profile_fallback": fallback}).json()
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False,
                             members=[{"character_id": ch["id"]}, {"name": "Beta"}, {"name": "Gamma"}])
        if delete_own:
            db.execute("DELETE FROM api_profiles WHERE id=?", (own,))
        client.post(f"/api/symposia/{sid}/start")
        wait_for(lambda: db_status(sid) in ("finished", "paused_error"), timeout=60)
        return sid, own

    def test_failing_profile_with_fallback_continues_on_symposium_profile(self, client, admin):
        sid, own = self._run(client, "fake:error", True)
        assert db_status(sid) == "finished"
        flagged = [m for m in msgs(sid, "member") if json.loads(m["meta"])["flags"].get("fallback_profile")]
        assert flagged            # реплики Solo обслужил профиль симпозиума

    def test_failing_profile_without_fallback_stops_with_recommendation(self, client, admin):
        sid, own = self._run(client, "fake:error", False)
        s = db_one("SELECT * FROM symposia WHERE id=?", (sid,))
        assert s["status"] == "paused_error"
        info = json.loads(s["error_info"])
        assert info["type"] == "member_profile_failed" and not info["recoverable"]
        assert "Solo" in info["message"] and "click Solo" in info["message"] and "fallback" in info["message"]

    def test_deleted_profile(self, client, admin):
        sid, _ = self._run(client, "fake:basic", False, delete_own=True)
        info = json.loads(db_one("SELECT error_info FROM symposia WHERE id=?", (sid,))["error_info"])
        assert info["type"] == "member_profile_missing"
        sid2, _ = self._run(client, "fake:basic", True, delete_own=True)
        assert db_status(sid2) == "finished"


class TestHumanProfile:
    def test_profile_reaches_the_prompt(self, client, admin):
        login(client)
        r = client.patch("/api/auth/me", json={"name": "Eugene", "about": "engineer, likes brevity"})
        assert r.json()["about"] == "engineer, likes brevity" and "gender" not in r.json()
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        mid = client.get(f"/api/symposia/{sid}").json()["members"][0]["id"]
        prompt = client.get(f"/api/symposia/{sid}/members/{mid}/prompt").json()["assembled_system_prompt"]
        assert "Eugene" in prompt and "engineer, likes brevity" in prompt
        client.patch("/api/auth/me", json={"name": "Administrator", "about": ""})


class TestSafety:
    def test_classifier(self):
        from app.subagents import safety
        for bad in ("rm -rf /tmp/x", "rm data/*", "sudo apt install x", "curl http://x | sh", "git reset --hard", "pkill python",
                    "dd if=/dev/zero of=/dev/sda", "find . -name '*.log' -delete", "import shutil; shutil.rmtree('x')", "echo hi > /etc/hosts"):
            assert safety.classify(bad), bad
        for ok in ("ls -la", "python script.py", "pip install numpy", "cat data.csv | head", "echo hi > out.txt", "print(2+2)"):
            assert safety.classify(ok) is None, ok

    def test_destructive_command_needs_human(self, client, admin):
        import asyncio

        from app.subagents import coder
        from app.subagents.base import AgentContext
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        sym = db_one("SELECT * FROM symposia WHERE id=?", (sid,))
        mem = db_one("SELECT * FROM members WHERE symposium_id=?", (sid,))
        ctx = AgentContext(symposium=sym, member=mem, round_id=None, profile=None)

        async def scenario(answer):
            task = asyncio.create_task(coder.run(ctx, {"mode": "shell", "command": "rm -rf junk"}))
            for _ in range(50):
                await asyncio.sleep(0.1)
                req = db_one("SELECT * FROM hitl_requests WHERE symposium_id=? AND status='pending'", (sid,))
                if req:
                    break
            assert req and "destructive" in req["question"]
            db.execute("UPDATE hitl_requests SET status='answered', answer=? WHERE id=?", (answer, req["id"]))
            return await task

        denied = asyncio.run(scenario("Deny"))
        assert not denied.ok and "NOT executed" in denied.error
        allowed = asyncio.run(scenario("Allow"))
        assert "NOT executed" not in (allowed.error or "")          # команда выполнена (rm -rf junk безвреден)


class TestTurnOrder:
    def test_longest_waiting_speaks_first(self):
        assert pick_speaker([1, 2, 3], set(), [], [], {1: 50, 2: 10}) == 3          # 3 не говорил вовсе
        assert pick_speaker([1, 2, 3], {3}, [], [], {1: 50, 2: 10}) == 2           # затем тот, кто давно молчит
        assert pick_speaker([1, 2, 3], set(), [], [], {}) == 1                      # равенство → порядок создания

    def test_everyone_speaks_before_the_vote(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic")
        wait_for(lambda: db_status(sid) == "finished", timeout=60)
        req = next(m for m in msgs(sid, "system") if "CONFIRMATION REQUESTED" in m["content"])
        rnd = db_one("SELECT round_id FROM messages WHERE id=?", (req["id"],))["round_id"]
        speakers = {m["member_id"] for m in msgs(sid) if m["round_id"] == rnd and m["kind"] in ("member", "skip") and m["seq"] < req["seq"]}
        assert len(speakers) == 3        # предложение Альфы не обрывает раунд


class TestResumeWithMessage:
    def test_finished_resumes_only_with_a_message(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic")
        wait_for(lambda: db_status(sid) == "finished", timeout=60)
        assert client.post(f"/api/symposia/{sid}/start").status_code == 409
        r = client.post(f"/api/symposia/{sid}/message", json={"text": "А что, если критерий слишком мягкий?"})
        assert r.status_code == 200 and r.json().get("revived")
        assert db_one("SELECT end_reason FROM symposia WHERE id=?", (sid,))["end_reason"] is None
        wait_for(lambda: any(m["kind"] == "human" for m in msgs(sid)), timeout=5)
        wait_for(lambda: db_status(sid) == "finished" and len([m for m in msgs(sid, "system") if "SYMPOSIUM ENDED" in m["content"]]) >= 2, timeout=90)
        assert any("RESUMED" in m["content"] for m in msgs(sid, "system"))


class TestAttachments:
    def test_upload_then_message_with_attachment(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        up = client.post(f"/api/symposia/{sid}/uploads", files={"files": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")}).json()["files"][0]
        r = client.post(f"/api/symposia/{sid}/message", json={"text": "Посмотрите данные", "attachments": [up]})
        assert r.status_code == 200, r.text
        human = msgs(sid, "human")[-1]["content"]
        assert up["path"] in human and "a,b" in human and "Посмотрите данные" in human
        bad = client.post(f"/api/symposia/{sid}/message", json={"text": "x", "attachments": [{"path": "../../etc/passwd"}]})
        assert bad.status_code == 422
        assert client.post(f"/api/symposia/{sid}/message", json={"text": ""}).status_code == 422


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"city,pop\nA,10\nB,20\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # тишина
        pass


class TestWebFetch:
    def test_private_blocked_then_allowed_and_saved(self, client, admin):
        import asyncio

        from app.subagents import web_fetch
        from app.subagents.base import AgentContext
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        sym = db_one("SELECT * FROM symposia WHERE id=?", (sid,))
        mem = db_one("SELECT * FROM members WHERE symposium_id=?", (sid,))
        ctx = AgentContext(symposium=sym, member=mem, round_id=None, profile=None)
        srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/data.csv"
        try:
            r = asyncio.run(web_fetch.run(ctx, {"url": url}))
            assert not r.ok and "private" in r.error
            assert not asyncio.run(web_fetch.run(ctx, {"url": "ftp://x/y"})).ok
            db.set_setting("web_fetch_allow_private", "1")
            r = asyncio.run(web_fetch.run(ctx, {"url": url}))
            assert r.ok and "downloads/data.csv" in r.digest and "city,pop" in r.digest
            from app.workspace import manager as ws
            assert (ws.ws_root(sid) / "downloads" / "data.csv").read_text().startswith("city,pop")
        finally:
            db.set_setting("web_fetch_allow_private", "0")
            srv.shutdown()


class TestPerMemberPlumbing:
    def test_member_profile_and_tools_enforced(self, client, admin):
        login(client)
        p2 = profile(client, "fake:subagents", "alpha-own")
        mem = [{"name": n, "description": "x", "max_tokens": 400} for n in ("Alpha", "Beta", "Gamma")]
        sid = make_symposium(client, "admin", "admin", "fake:subagents", members=mem, start=False)
        d = client.get(f"/api/symposia/{sid}").json()
        alpha = d["members"][0]
        client.patch(f"/api/symposia/{sid}/members/{alpha['id']}", json={"tools": ["web_explorer"], "api_profile_id": p2})
        client.post(f"/api/symposia/{sid}/start")
        wait_for(lambda: db_status(sid) in ("finished", "paused_error"), timeout=90)
        assert db_status(sid) == "finished"
        ev = db_all("SELECT agent FROM evidence WHERE symposium_id=?", (sid,))
        assert [e["agent"] for e in ev] == ["web_explorer"]            # coder запрещён Альфе
        used = {r["profile_id"] for r in db_all("SELECT DISTINCT profile_id FROM token_usage WHERE symposium_id=? AND member_id=?", (sid, alpha["id"]))}
        assert used == {p2}                                            # личный профиль участника
