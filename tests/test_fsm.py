"""Автомат и сходимость — чистые функции, без БД и LLM."""
from app.engine import convergence as cv
from app.engine.fsm import Action, Kind, Snapshot, decide, pick_speaker


def snap(**kw):
    base = dict(active=True, has_profile=True, has_opening=True, voting=False, round_exists=True,
                pending_member_id=None, next_speaker_id=1)
    base.update(kw)
    return Snapshot(**base)


class TestDecide:
    def test_priority_order(self):
        assert decide(snap(active=False)).kind == Kind.IDLE
        assert decide(snap(has_profile=False)).kind == Kind.PAUSE_NO_PROFILE
        assert decide(snap(has_opening=False)).kind == Kind.OPEN
        assert decide(snap(voting=True, pending_member_id=2)).kind == Kind.VOTE
        assert decide(snap(round_exists=False)).kind == Kind.ENSURE_ROUND
        assert decide(snap(pending_member_id=2)) == Action(Kind.RESUME_TURN, 2)
        assert decide(snap()) == Action(Kind.START_TURN, 1)
        assert decide(snap(next_speaker_id=None)).kind == Kind.CLOSE_ROUND


class TestPickSpeaker:
    def test_creation_order(self):
        assert pick_speaker([1, 2, 3], set(), [], []) == 1
        assert pick_speaker([1, 2, 3], {1}, [], []) == 2
        assert pick_speaker([1, 2, 3], {1, 2, 3}, [], []) is None

    def test_priority_then_asked(self):
        assert pick_speaker([1, 2, 3], set(), [3], [2]) == 3
        assert pick_speaker([1, 2, 3], {3}, [3], [2]) == 2
        assert pick_speaker([1, 2, 3], {1}, [], [3]) == 3


def msg(mid, seq, state, rid=1, kind="member"):
    import json
    return {"kind": kind, "member_id": mid, "seq": seq, "round_id": rid, "content": "x",
            "meta": json.dumps({"state": state})}


class TestConvergence:
    ITEMS = [{"ref": "C1", "status": "open"}, {"ref": "C2", "status": "open"}]

    def test_checklist_consensus(self):
        states = {1: {"checklist": {"C1": "done", "C2": "open"}},
                  2: {"checklist": {"C1": "done", "C2": "done"}},
                  3: {"checklist": {"C1": "done"}}}
        res = cv.checklist_consensus(self.ITEMS, states, 3)
        assert res == {"C1": "done", "C2": "open"}  # C2: 1 done из 2 высказавшихся < 2/3

    def test_checklist_needs_half_of_the_members(self):
        res = cv.checklist_consensus(self.ITEMS, {1: {"checklist": {"C1": "done"}}}, 4)
        assert res["C1"] == "open"

    def test_latest_states(self):
        ms = [msg(1, 1, {"readiness": "developing"}), msg(1, 2, {"readiness": "settled"})]
        assert cv.latest_states(ms)[1]["readiness"] == "settled"

    def test_should_propose_end_checklist(self):
        done = [{"ref": "C1", "status": "done"}]
        states = {1: {"readiness": "settled"}, 2: {"readiness": "settled"}}
        kw = dict(current_round=3, cooldown_round=0, items=done, open_issues=0, states=states,
                  n_members=2, recent_round_claims=[2, 1], stall_rounds=2)
        assert cv.should_propose_end(**kw) == "checklist"
        assert cv.should_propose_end(**{**kw, "open_issues": 1}) is None
        assert cv.should_propose_end(**{**kw, "cooldown_round": 3}) is None
        states[2]["readiness"] = "developing"
        assert cv.should_propose_end(**kw) is None

    def test_should_propose_end_stall(self):
        kw = dict(current_round=4, cooldown_round=0, items=[], open_issues=0, states={}, n_members=3,
                  recent_round_claims=[3, 0, 0], stall_rounds=2)
        assert cv.should_propose_end(**kw) == "stall"
        assert cv.should_propose_end(**{**kw, "recent_round_claims": [3, 0, 1]}) is None
        assert cv.should_propose_end(**{**kw, "stall_rounds": 0}) is None

    def test_asked_members(self):
        members = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        ms = [msg(1, 5, {"questions": ["@Bob: почему?", "общий"]})]
        assert cv.asked_members(ms, members, {}) == [2]
        assert cv.asked_members(ms, members, {2: 9}) == []  # Bob уже говорил после вопроса
