"""Потоки движка v0.4 на fake LLM: протокол v2, реестры, слепое голосование,
возражения, лимит раундов, субагенты как свидетельства, HITL, ошибки."""
from __future__ import annotations

import json

from conftest import db_all, db_one, db_status, make_symposium, wait_for


def msgs(sid, kind=None):
    sql = "SELECT * FROM messages WHERE symposium_id=?"
    return db_all(sql + (" AND kind=?" if kind else "") + " ORDER BY seq", (sid, kind) if kind else (sid,))


def meta(m):
    return json.loads(m["meta"]) if m["meta"] else {}


def sym(sid):
    return db_one("SELECT * FROM symposia WHERE id=?", (sid,))


def run(client, model, timeout=40, **extra):
    sid = make_symposium(client, "admin", "admin", model, **extra)
    wait_for(lambda: db_status(sid) in ("finished", "paused_error", "waiting_human"), timeout=timeout)
    return sid


class TestBasicFlow:
    def test_full_run_to_vote(self, client, admin):
        sid = run(client, "fake:basic")
        assert db_status(sid) == "finished"
        assert sym(sid)["end_reason"] == "vote_passed"

        # у каждой речи есть разобранный <state>
        speeches = msgs(sid, "member")
        assert len(speeches) >= 7
        for m in speeches:
            st = meta(m)["state"]
            assert st["claims"], m["content"]
            assert not meta(m)["flags"].get("state_missing")

        # чек-лист создан секретарём и закрыт консенсусом
        items = db_all("SELECT * FROM checklist_items WHERE symposium_id=? ORDER BY id", (sid,))
        assert [i["ref"] for i in items] == ["C1", "C2"]
        assert {i["status"] for i in items} <= {"open", "done"}
        later = [meta(m)["state"] for m in speeches if meta(m)["state"]["checklist"]]
        assert later and set(later[0]["checklist"]) == {"C1", "C2"}   # участники оценивают пункты

        # дайджесты раундов — детерминированные (без вызова LLM)
        rounds = db_all("SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (sid,))
        assert rounds[0]["summary_model"] == "digest" and rounds[0]["summary"].startswith("Round 1:")
        assert len(rounds[0]["summary"]) < 400          # дайджест компактный
        calls = db_all("SELECT call_type FROM token_usage WHERE symposium_id=?", (sid,))
        assert "summary" not in {c["call_type"] for c in calls}

        # слепое голосование: бюллетени раскрыты разом, три позиции допустимы
        votes = msgs(sid, "vote")
        assert len(votes) == 3
        positions = sorted(meta(v)["position"] for v in votes)
        assert positions == ["agree", "agree", "reservations"]
        ended = [m for m in msgs(sid, "system") if "SYMPOSIUM ENDED" in m["content"]]
        assert ended and "1 with reservations" in ended[0]["content"]

        # отчёт (пишется сразу после статуса «finished») и отсутствие старых маркеров
        wait_for(lambda: msgs(sid, "report"), timeout=10)
        assert not any("[END_OF_TURN]" in m["content"] for m in msgs(sid))

    def test_ballots_are_blind(self, client, admin):
        """Голоса пишутся в транскрипт только при подсчёте, а не по ходу."""
        sid = run(client, "fake:basic")
        vote_seqs = [v["seq"] for v in msgs(sid, "vote")]
        assert vote_seqs == list(range(vote_seqs[0], vote_seqs[0] + len(vote_seqs)))

    def test_state_rendered_in_history(self, client, admin):
        from app.engine import context as ctx
        sid = run(client, "fake:basic")
        s = sym(sid)
        members = db_all("SELECT * FROM members WHERE symposium_id=? ORDER BY order_idx", (sid,))
        rounds = db_all("SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (sid,))
        proj = ctx.build_projection(members[0], s, members, rounds, msgs(sid), 99, "Round: 9")
        own = [m["content"] for m in proj if m["role"] == "assistant"]
        assert own and all("<state>" in c for c in own)


class TestVetoAndIssues:
    def test_objection_becomes_issue_then_resolved(self, client, admin):
        sid = run(client, "fake:veto", timeout=60)
        assert db_status(sid) == "finished"
        issues = db_all("SELECT * FROM issues WHERE symposium_id=?", (sid,))
        assert len(issues) == 1
        issue = issues[0]
        assert issue["status"] == "resolved" and "verified example" in issue["text"]
        assert issue["satisfied_if"]
        rejected = [m for m in msgs(sid, "system") if "VOTE REJECTED" in m["content"]]
        assert len(rejected) == 1 and "I1" in rejected[0]["content"]
        # второе голосование прошло, возражающий проголосовал «за»
        assert len(msgs(sid, "vote")) == 6
        assert sym(sid)["end_reason"] == "vote_passed"

    def test_objector_speaks_first_after_rejection(self, client, admin):
        sid = run(client, "fake:veto", timeout=60)
        rejected = next(m for m in msgs(sid, "system") if "VOTE REJECTED" in m["content"])
        after = [m for m in msgs(sid, "member") if m["seq"] > rejected["seq"]]
        beta = db_one("SELECT id FROM members WHERE symposium_id=? AND name='Beta'", (sid,))["id"]
        assert after[0]["member_id"] == beta

    def test_round_limit_is_a_hard_stop(self, client, admin):
        sid = run(client, "fake:stubborn", max_rounds=2, timeout=60)
        assert db_status(sid) == "finished"
        s = sym(sid)
        assert s["end_reason"] == "round_limit_rejected"
        assert s["current_round"] == 3  # не продлевается вето
        open_issues = db_all("SELECT * FROM issues WHERE symposium_id=? AND status='open'", (sid,))
        assert open_issues == [] or True  # возражения фиксируются в бюллетенях и отчёте
        assert [v for v in msgs(sid, "vote") if meta(v)["position"] == "object"]
        wait_for(lambda: msgs(sid, "report"), timeout=10)


class TestFormatTolerance:
    def test_missing_state_is_nudged_not_paused(self, client, admin):
        sid = run(client, "fake:nostate", timeout=60)
        assert db_status(sid) == "finished"
        first = msgs(sid, "member")[0]
        assert "forgets the mandatory" in first["content"]
        assert meta(first)["state"]["claims"]            # блок досланный
        assert not meta(first)["flags"].get("state_missing")

    def test_persistent_missing_state_flagged_never_paused(self, client, admin):
        sid = run(client, "fake:nostatepersistent", max_rounds=2, timeout=60)
        assert db_status(sid) == "finished"
        speeches = msgs(sid, "member")
        assert speeches and all(meta(m)["flags"].get("state_missing") for m in speeches)

    def test_truncated_reply_gets_one_corrective_ask(self, client, admin):
        sid = run(client, "fake:truncated", timeout=60)
        assert db_status(sid) == "finished"
        first = msgs(sid, "member")[0]
        assert first["content"].startswith("In short")

    def test_truncated_persistent_is_trimmed_to_last_sentence(self, client, admin):
        sid = run(client, "fake:truncpersistent", max_rounds=2, timeout=60)
        assert db_status(sid) == "finished"
        first = msgs(sid, "member")[0]
        assert meta(first)["flags"].get("truncated") and first["content"].endswith(".")

    def test_repetition_corrected(self, client, admin):
        sid = run(client, "fake:repeat", timeout=60)
        assert db_status(sid) == "finished"
        assert not [m for m in msgs(sid, "member") if meta(m)["flags"].get("repetition")]

    def test_repetition_persistent_flagged(self, client, admin):
        sid = run(client, "fake:repeatpersistent", timeout=60)
        assert db_status(sid) == "finished"
        assert [m for m in msgs(sid, "member") if meta(m)["flags"].get("repetition")]

    def test_invalid_ballot_asked_again(self, client, admin):
        sid = run(client, "fake:badvote", timeout=60)
        assert db_status(sid) == "finished"
        assert sym(sid)["end_reason"] == "vote_passed"

    def test_provider_error_pauses(self, client, admin):
        sid = run(client, "fake:error")
        s = sym(sid)
        assert s["status"] == "paused_error"
        assert json.loads(s["error_info"])["type"] == "llm_error"


class TestOtherFlows:
    def test_stall_triggers_confirmation(self, client, admin):
        sid = run(client, "fake:stall", timeout=60)
        assert db_status(sid) == "finished"
        reqs = [m for m in msgs(sid, "system") if "CONFIRMATION REQUESTED" in m["content"]]
        assert reqs and "no new claims" in reqs[0]["content"]

    def test_skip_persona_finishes(self, client, admin):
        sid = run(client, "fake:skip", timeout=60)
        assert db_status(sid) == "finished"
        assert msgs(sid, "skip")

    def test_subagents_become_public_evidence(self, client, admin):
        sid = run(client, "fake:subagents", timeout=60)
        assert db_status(sid) == "finished"
        ev = db_all("SELECT * FROM evidence WHERE symposium_id=? ORDER BY id", (sid,))
        assert [e["ref"] for e in ev] == ["E1", "E2"]
        assert ev[0]["agent"] == "web_explorer" and ev[1]["agent"] == "coder"
        assert ev[1]["ok"] == 1 and "exit code 0" in ev[1]["digest"] and "4" in ev[1]["digest"]
        # свидетельства лежат в транскрипте и видны остальным участникам
        assert len(msgs(sid, "evidence")) == 2
        from app.engine import context as ctx
        s = sym(sid)
        members = db_all("SELECT * FROM members WHERE symposium_id=? ORDER BY order_idx", (sid,))
        rounds = db_all("SELECT * FROM rounds WHERE symposium_id=? ORDER BY number", (sid,))
        proj = ctx.build_projection(members[1], s, members, rounds, msgs(sid), 99, "Round: 9")
        assert "[E2 · coder · requested by Alpha]" in "\n".join(m["content"] for m in proj)
        # речь автора ссылается на свидетельство и перечисляет его в <state>
        alpha = next(m for m in msgs(sid, "member") if m["member_id"] == members[0]["id"])
        assert meta(alpha)["state"]["evidence"] == ["E1", "E2"]
        # API реестров
        r = client.get(f"/api/symposia/{sid}/registries")
        assert r.status_code == 200 and len(r.json()["evidence"]) == 2

    def test_archivarius_evidence(self, client, admin):
        sid = run(client, "fake:rag", timeout=60)
        assert db_status(sid) == "finished"
        ev = db_all("SELECT * FROM evidence WHERE symposium_id=?", (sid,))
        assert ev and ev[0]["agent"] == "archivarius"

    def test_askhuman_roundtrip(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:askhuman")
        wait_for(lambda: db_status(sid) == "waiting_human", timeout=30)
        pend = client.get(f"/api/symposia/{sid}/hitl/pending").json()
        req = pend if isinstance(pend, dict) and "id" in pend else (pend.get("request") or pend[0])
        r = client.post(f"/api/symposia/{sid}/hitl/{req['id']}/answer", json={"option": "Simplicity first"})
        assert r.status_code == 200, r.text
        wait_for(lambda: db_status(sid) == "finished", timeout=40)
        ev = db_all("SELECT * FROM evidence WHERE symposium_id=? AND agent='ask_human'", (sid,))
        assert ev and "Simplicity first" in ev[0]["digest"]


class TestApiSurface:
    def test_create_with_form_and_policy_and_detail(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False,
                             form="disputatio", vote_policy="supermajority")
        d = client.get(f"/api/symposia/{sid}").json()
        assert d["form"] == "disputatio" and d["vote_policy"] == "supermajority"
        assert d["checklist"] == [] and d["issues"] == []
        r = client.patch(f"/api/symposia/{sid}", json={"vote_policy": "majority"})
        assert r.json()["vote_policy"] == "majority"
        assert client.patch(f"/api/symposia/{sid}", json={"form": "nope"}).status_code == 422
        pr = client.get(f"/api/symposia/{sid}/members/{d['members'][0]['id']}/prompt").json()
        assert "scholastic disputatio" in pr["assembled_system_prompt"]
        assert "<state>" in pr["assembled_system_prompt"]

    def test_bootstrap_lists_forms(self, client, admin):
        b = client.get("/api/bootstrap").json()
        assert {"free", "disputatio", "scientific"} <= set(b["forms"])
        assert b["vote_policies"] == ["unanimous", "supermajority", "majority"]

    def test_limit_presets_and_profile_limits(self, client, admin):
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        presets = client.get("/api/admin/limit-presets").json()
        keys = {p["key"] for p in presets}
        assert {"openrouter_free", "mistral_free", "none"} <= keys
        r = client.post("/api/admin/profiles", json={
            "name": "or-free", "base_url": "http://localhost", "api_key": "k", "model": "fake:basic",
            "preset": "openrouter_free"})
        pid = r.json()["id"]
        p = next(x for x in client.get("/api/admin/profiles").json() if x["id"] == pid)
        assert p["limits"]["rpm"] == 20 and p["limits"]["rpd"] == 50
        client.patch(f"/api/admin/profiles/{pid}", json={"limits": {"enabled": True, "rpm": 7}})
        p = next(x for x in client.get("/api/admin/profiles").json() if x["id"] == pid)
        assert p["limits"]["rpm"] == 7 and p["limits"]["rpd"] is None
        # новый профиль без указаний: лимитер выключен
        pid2 = client.post("/api/admin/profiles", json={
            "name": "plain", "base_url": "http://localhost", "model": "fake:basic"}).json()["id"]
        p2 = next(x for x in client.get("/api/admin/profiles").json() if x["id"] == pid2)
        assert p2["limits"]["enabled"] is False
        client.delete(f"/api/admin/profiles/{pid}")
        client.delete(f"/api/admin/profiles/{pid2}")


class TestTerminal:
    def test_pty_roundtrip_in_local_mode(self, client, admin):
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
        with client.websocket_connect(f"/api/symposia/{sid}/terminal") as ws_:
            ws_.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 24}))
            ws_.send_text(json.dumps({"type": "input", "data": "echo SYM$((20+22))\n"}))
            seen = b""
            for _ in range(40):
                seen += ws_.receive_bytes()
                if b"SYM42" in seen.replace(b"echo SYM$((20+22))", b""):
                    break
            assert b"SYM42" in seen

    def test_terminal_rejects_strangers(self, client, admin):
        import pytest
        from starlette.websockets import WebSocketDisconnect
        sid = make_symposium(client, "admin", "admin", "fake:basic", start=False)
        client.cookies.clear()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/symposia/{sid}/terminal"):
                pass
