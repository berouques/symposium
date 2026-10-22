"""Протокол v2: ответ участника, бюллетень, вызовы субагентов, повторы."""
from app.engine.protocol import parse_ballot, parse_calls, parse_reply
from app.engine.protocol import repetition
from app.engine.protocol.calls import has_calls

NAMES = ["Alice", "Bob", "Carol"]

GOOD = """Я согласен с Alice, но с оговоркой.
<state>
action: speak
reply_to: alice, Bob
stance: Alice=agree; Bob=disagree
claims: первая мысль | вторая мысль
questions: @Bob: что опровергнет это? | общий вопрос
evidence: E1, e3
checklist: c1=done; C2=open
issues: I1=resolved
readiness: settled
</state>"""


class TestEnvelope:
    def test_full_state(self):
        r = parse_reply(GOOD, NAMES)
        assert r.kind == "speech" and not r.state_missing
        assert r.speech.startswith("Я согласен")
        st = r.state
        assert st.reply_to == ["Alice", "Bob"]
        assert st.stance == {"Alice": "agree", "Bob": "disagree"}
        assert st.claims == ["первая мысль", "вторая мысль"]
        assert st.questions[0].startswith("@Bob:")
        assert st.evidence == ["E1", "E3"]
        assert st.checklist == {"C1": "done", "C2": "open"}
        assert st.issues == {"I1": "resolved"}
        assert st.readiness == "settled"

    def test_missing_state(self):
        r = parse_reply("Просто речь без блока.", NAMES)
        assert r.kind == "speech" and r.state_missing

    def test_legacy_end_of_turn_is_ignored(self):
        r = parse_reply("Речь. [END_OF_TURN]", NAMES)
        assert r.speech == "Речь." and r.state_missing

    def test_unclosed_state_is_parsed(self):
        r = parse_reply("Речь.\n<state>\naction: speak\nclaims: a | b\n", NAMES)
        assert r.state_unclosed and r.state.claims == ["a", "b"]

    def test_skip(self):
        assert parse_reply("<state>\naction: skip\n</state>", NAMES).kind == "skip"
        assert parse_reply("[SKIP_TURN]", NAMES).kind == "skip"

    def test_skip_with_speech_is_speech(self):
        r = parse_reply("Всё-таки скажу.\n<state>\naction: skip\n</state>", NAMES)
        assert r.kind == "speech" and r.state.action == "speak"

    def test_propose_end(self):
        r = parse_reply("Критерий выполнен.\n<state>\naction: propose_end\n</state>", NAMES)
        assert r.propose_end and r.kind == "speech"
        assert parse_reply("<state>\naction: propose_end\n</state>", NAMES).kind == "end"

    def test_empty_is_invalid(self):
        assert parse_reply("", NAMES).kind == "invalid"
        assert parse_reply("<state>\nreadiness: developing\n</state>", NAMES).kind == "invalid"

    def test_calls_have_priority(self):
        r = parse_reply('Проверю.\n<call agent="web_explorer">кошки</call>', NAMES)
        assert r.kind == "calls" and r.calls[0].agent == "web_explorer"
        assert r.pre_call_text == "Проверю."

    def test_render_roundtrip(self):
        st = parse_reply(GOOD, NAMES).state
        again = parse_reply("x\n" + st.render(), NAMES).state
        assert again.claims == st.claims and again.checklist == st.checklist
        assert again.readiness == "settled"

    def test_readiness_not_settled(self):
        r = parse_reply("a\n<state>\nreadiness: not settled yet\n</state>", NAMES)
        assert r.state.readiness == "developing"


class TestCalls:
    def test_raw_body_for_coder_no_json_escaping(self):
        code = "print(1)\n>>> doctest line\n"
        (c,) = parse_calls(f'<call agent="coder" language="python" filename="a.py">\n{code}</call>')
        assert c.error is None and c.params["language"] == "python"
        assert ">>> doctest line" in c.params["code"]

    def test_several_calls_in_order(self):
        calls = parse_calls('<call agent="web_explorer">a</call> x <call agent="archivarius">b</call>')
        assert [c.agent for c in calls] == ["web_explorer", "archivarius"]
        assert calls[0].params == {"query": "a"}

    def test_json_body(self):
        (c,) = parse_calls('<call agent="ask_human">{"question": "Q?", "options": ["a", "b"]}</call>')
        assert c.params["question"] == "Q?" and c.params["options"] == ["a", "b"]

    def test_bad_json_and_unknown_agent(self):
        assert parse_calls('<call agent="coder">{oops}</call>')[0].error
        assert parse_calls('<call agent="hacker">x</call>')[0].error

    def test_shell_mode_attrs(self):
        (c,) = parse_calls('<call agent="coder" mode="shell" timeout="120">pip install numpy</call>')
        assert c.params["mode"] == "shell" and c.params["timeout"] == "120"
        assert c.params["code"] == "pip install numpy"

    def test_has_calls(self):
        assert has_calls('<call agent="coder">x</call>') and not has_calls("нет")


class TestBallot:
    def test_agree(self):
        b = parse_ballot("<vote>agree</vote><reason>ok</reason>")
        assert b.ok and b.position == "agree" and b.reason == "ok"

    def test_reservations(self):
        b = parse_ballot("<vote>reservations</vote><reason>r</reason><unresolved>edge cases</unresolved>")
        assert b.position == "reservations" and b.unresolved == "edge cases"

    def test_agree_with_reservations_phrase(self):
        assert parse_ballot("<vote>agree with reservations</vote>").position == "reservations"

    def test_object_needs_substance(self):
        assert not parse_ballot("<vote>object</vote>").ok
        b = parse_ballot("<vote>object</vote><unresolved>нет примера</unresolved><satisfied_if>пример</satisfied_if>")
        assert b.ok and b.position == "object" and b.satisfied_if == "пример"

    def test_word_veto_in_explanation_is_not_a_vote(self):
        """Баг v0.3: слово «veto» где угодно считалось голосом."""
        b = parse_ballot("<vote>agree</vote><reason>I have no reason to veto this.</reason>")
        assert b.position == "agree"
        b = parse_ballot("I have no reason to veto. [AGREE_END]")
        assert b.position == "agree"

    def test_disagree_is_object(self):
        assert parse_ballot("<vote>disagree</vote><reason>no</reason>").position == "object"

    def test_garbage_is_invalid(self):
        b = parse_ballot("Hmm, hard to say.")
        assert not b.ok

    def test_plain_first_word(self):
        assert parse_ballot("Agree. The criterion is met.").position == "agree"

    def test_legacy_brackets(self):
        assert parse_ballot("[VETO] example missing").position == "object"


class TestRepetition:
    def test_identical_is_repeat(self):
        own = [{"content": "The same long argument about institutions and incentives.", "round_id": 1}]
        r, rid = repetition.find("The same long argument about institutions and incentives.", own)
        assert r > repetition.REPETITION_THRESHOLD and rid == 1

    def test_different_is_not(self):
        own = [{"content": "Первый довод про институты.", "round_id": 1}]
        r, _ = repetition.find("Совершенно иное соображение о стоимости проверки данных.", own)
        assert r < repetition.REPETITION_THRESHOLD

    def test_trim(self):
        t = "Первое предложение достаточно длинное для принятия. Второе оборвано на полусл"
        assert repetition.trim_to_last_sentence(t).endswith("принятия.")
        assert repetition.trim_to_last_sentence("нет точки вообще") == ""
