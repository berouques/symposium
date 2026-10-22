"""v0.4.1: мышление модели/участника, участники вкл/выкл, самовосстановление,
запасное открытие секретаря, правка темы/критерия на ходу, полный дамп, расход."""
from __future__ import annotations

import io
import json
import zipfile

from conftest import db_all, db_one, db_status, make_symposium, wait_for

from app import db
from app.llm import thinking as th


def msgs(sid, kind=None):
    sql = "SELECT * FROM messages WHERE symposium_id=?"
    return db_all(sql + (" AND kind=?" if kind else "") + " ORDER BY seq", (sid, kind) if kind else (sid,))


class TestThinkingCeiling:
    P = {"model": "glm-5.3-flash", "base_url": "https://api.z.ai/api/paas/v4"}

    def test_model_off_is_a_ceiling(self):
        p = {**self.P, "thinking_mode": "off"}
        assert th.resolve({"thinking_mode": "on", "thinking_depth": "high"}, {}, p) == ("off", None)

    def test_model_depth_is_a_ceiling(self):
        p = {**self.P, "thinking_mode": "on", "thinking_depth": "medium"}
        assert th.resolve({"thinking_mode": "on", "thinking_depth": "high"}, {}, p) == ("on", "medium")
        assert th.resolve({"thinking_mode": "on", "thinking_depth": "low"}, {}, p) == ("on", "low")
        assert th.resolve({"thinking_mode": "on"}, {}, p) == ("on", "medium")

    def test_member_can_turn_off_under_enabled_model(self):
        p = {**self.P, "thinking_mode": "on", "thinking_depth": "high"}
        assert th.resolve({"thinking_mode": "off"}, {}, p) == ("off", None)

    def test_default_inherits_profile_and_symposium(self):
        p = {**self.P, "thinking_mode": "on", "thinking_depth": "low"}
        assert th.resolve({}, {}, p) == ("on", "low")
        assert th.resolve({}, {"thinking_mode": "off"}, {**self.P}) == ("off", None)
        assert th.resolve({}, {}, None) == ("default", None)

    def test_reasoning_model_default_gets_headroom(self):
        """Корень сбоя e2e: GLM-5.3 думает сам, а лимит запроса равнялся лимиту реплики."""
        fields, request_max, note = th.request_params(self.P, "default", None, 900)
        assert fields == {} and request_max > 900 and note
        plain = {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"}
        assert th.request_params(plain, "default", None, 900)[1] == 900
        assert th.reasons_by_default({"model": "z-ai/glm-5.3-flash", "base_url": "https://openrouter.ai/api/v1"})

    def test_admin_api_profile_thinking(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        pid = client.post("/api/admin/profiles", json={
            "name": "t", "base_url": "http://x", "model": "fake:basic",
            "thinking_mode": "on", "thinking_depth": "medium"}).json()["id"]
        p = next(x for x in client.get("/api/admin/profiles").json() if x["id"] == pid)
        assert p["thinking_mode"] == "on" and p["thinking_depth"] == "medium"
        assert client.patch(f"/api/admin/profiles/{pid}", json={"thinking_mode": "bad"}).status_code == 422
        client.patch(f"/api/admin/profiles/{pid}", json={"thinking_mode": "off", "thinking_depth": ""})
        p = next(x for x in client.get("/api/admin/profiles").json() if x["id"] == pid)
        assert p["thinking_mode"] == "off" and p["thinking_depth"] is None
        b = client.get("/api/bootstrap").json()
        assert any(x["id"] == pid and x["thinking_mode"] == "off" for x in b["profiles"])
        client.delete(f"/api/admin/profiles/{pid}")


class TestSecretaryRobust:
    def test_empty_opening_falls_back_and_symposium_proceeds(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:emptyopen", completion_criterion="Критерий X")
        wait_for(lambda: db_status(sid) in ("finished", "paused_error"), timeout=60)
        assert db_status(sid) == "finished"
        opening = msgs(sid, "secretary")[0]
        assert opening["content"].strip() and json.loads(opening["meta"]).get("fallback")
        items = db_all("SELECT * FROM checklist_items WHERE symposium_id=?", (sid,))
        assert [i["text"] for i in items] == ["Критерий X"]


class TestAutoRecovery:
    def test_transient_error_heals_itself(self, client, admin):
        db.set_setting("auto_recovery", "1")
        db.set_setting("auto_recovery_delays", "1")
        try:
            sid = make_symposium(client, "admin", "admin", "fake:flaky")
            wait_for(lambda: db_status(sid) == "finished", timeout=90)
        finally:
            db.set_setting("auto_recovery", "0")
        notes = [m["content"] for m in msgs(sid, "system") if "AUTO-RECOVERY" in m["content"]]
        assert notes and "llm_error" in notes[0]
        s = db_one("SELECT * FROM symposia WHERE id=?", (sid,))
        assert s["error_info"] is None and s["recovery_attempts"] == 0     # серия сброшена успехом

    def test_gives_up_after_max_attempts(self, client, admin):
        db.set_setting("auto_recovery", "1")
        db.set_setting("auto_recovery_delays", "0.3")
        db.set_setting("auto_recovery_max", "2")
        try:
            sid = make_symposium(client, "admin", "admin", "fake:error")
            wait_for(lambda: db_one("SELECT recovery_attempts AS a FROM symposia WHERE id=?", (sid,))["a"] >= 2
                     and db_status(sid) == "paused_error", timeout=60)
        finally:
            db.set_setting("auto_recovery", "0")
            db.set_setting("auto_recovery_max", "3")
        assert len([m for m in msgs(sid, "system") if "AUTO-RECOVERY" in m["content"]]) == 2

    def test_fallback_profile_used_when_primary_fails(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        good = client.post("/api/admin/profiles", json={"name": "good", "base_url": "http://x", "model": "fake:basic"}).json()["id"]
        db.set_setting("auto_recovery", "1")
        db.set_setting("auto_recovery_delays", "0.3")
        try:
            sid = make_symposium(client, "admin", "admin", "fake:error", fallback_profile_id=good) \
                if False else make_symposium(client, "admin", "admin", "fake:error")
            client.patch(f"/api/symposia/{sid}", json={"fallback_profile_id": good})
            wait_for(lambda: db_status(sid) == "finished", timeout=90)
        finally:
            db.set_setting("auto_recovery", "0")
        s = db_one("SELECT * FROM symposia WHERE id=?", (sid,))
        assert s["api_profile_id"] == good
        assert any("fallback" in m["content"] for m in msgs(sid, "system"))
        client.delete(f"/api/admin/profiles/{good}")


class TestMembersAndEditing:
    def test_disabled_member_does_not_speak_or_vote(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        d = client.get(f"/api/symposia/{sid}").json()
        gamma = next(m for m in d["members"] if m["name"] == "Gamma")
        assert client.patch(f"/api/symposia/{sid}/members/{gamma['id']}", json={"enabled": False}).status_code == 200
        client.post(f"/api/symposia/{sid}/start")
        wait_for(lambda: db_status(sid) in ("finished", "paused_error"), timeout=60)
        assert db_status(sid) == "finished"
        assert not [m for m in msgs(sid) if m["member_id"] == gamma["id"]]
        assert len(msgs(sid, "vote")) == 2
        assert client.get(f"/api/symposia/{sid}").json()["members"][2]["enabled"] is False

    def test_cannot_disable_everyone(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        mem = client.get(f"/api/symposia/{sid}").json()["members"]
        for m in mem[:-1]:
            assert client.patch(f"/api/symposia/{sid}/members/{m['id']}", json={"enabled": False}).status_code == 200
        r = client.patch(f"/api/symposia/{sid}/members/{mem[-1]['id']}", json={"enabled": False})
        assert r.status_code == 422

    def test_member_patch_thinking_and_temperature(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        m = client.get(f"/api/symposia/{sid}").json()["members"][0]
        r = client.patch(f"/api/symposia/{sid}/members/{m['id']}", json={"temperature": 0.9, "thinking_mode": "on", "thinking_depth": "low"})
        assert r.status_code == 200, r.text
        m = client.get(f"/api/symposia/{sid}").json()["members"][0]
        assert m["temperature"] == 0.9 and m["thinking_mode"] == "on" and m["thinking_depth"] == "low"

    def test_topic_and_criterion_editable_with_notice_and_checklist_regen(self, client, admin):
        sid = run_done(client)
        r = client.patch(f"/api/symposia/{sid}", json={"topic": "Новая тема", "completion_criterion": "Новый критерий"})
        assert r.status_code == 200 and r.json()["topic_full"] == "Новая тема"
        notes = [m["content"] for m in msgs(sid, "system") if "CHANGED BY THE HUMAN" in m["content"]]
        assert notes and "Новая тема" in notes[-1] and "Новый критерий" in notes[-1]
        r = client.post(f"/api/symposia/{sid}/checklist/regenerate")
        assert r.status_code == 200 and r.json()["items"]
        assert db_all("SELECT * FROM checklist_items WHERE symposium_id=?", (sid,))
        assert client.patch(f"/api/symposia/{sid}", json={"topic": "  "}).status_code == 422

    def test_switch_profile_mid_symposium(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        other = client.post("/api/admin/profiles", json={"name": "other", "base_url": "http://x", "model": "fake:basic"}).json()["id"]
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        r = client.patch(f"/api/symposia/{sid}", json={"api_profile_id": other})
        assert r.json()["api_profile"]["id"] == other
        client.delete(f"/api/admin/profiles/{other}")


def run_done(client):
    sid = make_symposium(client, "admin", "admin", "fake:basic")
    wait_for(lambda: db_status(sid) == "finished", timeout=60)
    wait_for(lambda: msgs(sid, "report"), timeout=10)
    return sid


class TestUsageAndDump:
    def test_usage_by_member_and_round(self, client, admin):
        sid = run_done(client)
        u = client.get(f"/api/symposia/{sid}/usage").json()
        assert len(u["by_member"]) >= 3 and all(m["calls"] >= 1 for m in u["by_member"])
        assert u["by_round"] and u["by_round"][0]["round"] == 1
        assert u["tokens_in"] + u["tokens_out"] > 0

    def test_global_token_stats(self, client, admin):
        run_done(client)
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        g = client.get("/api/stats/tokens").json()
        assert g["total"]["calls"] > 0 and g["by_symposium"] and g["by_day"]

    def test_full_dump_contains_technical_details(self, client, admin):
        sid = run_done(client)
        r = client.post(f"/api/symposia/{sid}/export", json={"format": "dump"})
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        assert {"README.txt", "messages.json", "members.json", "assembled_prompts.json", "token_usage.json",
                "evidence.json", "profile.json", "settings_snapshot.json", "transcript_full.md"} <= names
        messages = json.loads(z.read("messages.json"))
        assert any(isinstance(m["meta"], dict) and "state" in m["meta"] for m in messages)   # разобранный <state>
        prompts = json.loads(z.read("assembled_prompts.json"))
        assert "<state>" in next(iter(prompts.values()))["turn_system_prompt"]
        prof = json.loads(z.read("profile.json"))
        assert "api_key" not in prof and "limits_effective" in prof
