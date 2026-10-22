"""Unit tests: marker parsing, avatars, chunking, embeddings math."""
from __future__ import annotations

from app.avatars import fallback_svg, generate_svg, make_seed, member_color
from app.rag.indexer import chunk_text
from app.rag.embeddings import blob_to_vector, cosine, vector_to_blob


class TestAvatars:
    def test_deterministic(self):
        seed = make_seed("Plato", "loves forms", "Philosopher")
        assert generate_svg(seed, "bust") == generate_svg(seed, "bust")

    def test_different_seeds_differ(self):
        s1 = make_seed("Plato", "a", "Philosopher")
        s2 = make_seed("Aristotle", "a", "Philosopher")
        assert generate_svg(s1) != generate_svg(s2)

    def test_seed_depends_on_all_inputs(self):
        assert make_seed("X", "a", "r1") != make_seed("X", "b", "r1")
        assert make_seed("X", "a", "r1") != make_seed("X", "a", "r2")

    def test_styles(self):
        seed = make_seed("Diogenes", "in a barrel", "Skeptic")
        for style in ("bust", "geometric", "botanical"):
            svg = generate_svg(seed, style)
            assert svg.startswith("<svg") and svg.endswith("</svg>")

    def test_fallback_initials(self):
        svg = fallback_svg(make_seed("A", "", ""), "Ada Lovelace")
        assert "AL" in svg and svg.startswith("<svg")

    def test_color_format(self):
        color = member_color(make_seed("a", "b", "c"))
        assert color.startswith("#") and len(color) == 7


class TestChunking:
    def test_short_text_single_chunk(self):
        assert chunk_text("hello", 100, 20) == ["hello"]

    def test_overlap(self):
        text = "word " * 100  # 500 chars
        chunks = chunk_text(text, 100, 20)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_empty(self):
        assert chunk_text("   ", 100, 20) == []


class TestVectorMath:
    def test_roundtrip(self):
        vec = [0.1, 0.2, 0.3]
        assert blob_to_vector(vector_to_blob(vec)) == pytest_approx(vec)

    def test_cosine_identical(self):
        assert abs(cosine([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-6

    def test_cosine_orthogonal(self):
        assert abs(cosine([1, 0], [0, 1])) < 1e-6


def pytest_approx(vec):
    import pytest
    return pytest.approx(vec)


class TestSearchParsers:
    """Engine HTML/JSON parsers (no network)."""

    def test_google_basic_html(self):
        from app.subagents.search import parse_google_html
        page = (
            '<a href="/url?q=https://example.com/a&amp;sa=U">Result A</a>'
            '<a href="/url?q=https://example.com/b&amp;sa=U">Result B</a>'
        )
        res = parse_google_html(page)
        assert len(res) == 2
        assert res[0]["title"] == "Result A"
        assert res[0]["url"] == "https://example.com/a"

    def test_google_filters_internal(self):
        from app.subagents.search import parse_google_html
        page = (
            '<a href="/url?q=https://support.google.com/x&amp;sa=U">Help</a>'
            '<a href="/url?q=https://example.com/keep&amp;sa=U">Keep</a>'
        )
        res = parse_google_html(page)
        assert len(res) == 1 and "keep" in res[0]["url"]

    def test_bing_algo_blocks(self):
        from app.subagents.search import parse_bing_html
        page = (
            '<li class="b_algo"><h2><a href="https://example.com/one">One</a></h2>'
            '<p class="b_lineclamp4">Snippet one</p></li>'
            '<li class="b_algo"><h2><a href="https://example.com/two">Two</a></h2></li>'
        )
        res = parse_bing_html(page)
        assert len(res) == 2
        assert res[0]["title"] == "One" and res[0]["snippet"] == "Snippet one"
        assert res[1]["snippet"] == ""

    def test_yandex_serp_items(self):
        from app.subagents.search import parse_yandex_html
        page = (
            '<li class="serp-item"><a class="organic__url" href="https://example.com/ya">'
            '<h2 class="OrganicTitle-LinkText">Ya Result</h2></a>snippet text here</li>'
            '<li class="serp-item"><a class="organic__url" href="https://google.com/x">'
            '<h2>Bad</h2></a></li>'
        )
        res = parse_yandex_html(page)
        assert len(res) == 1
        assert res[0]["title"] == "Ya Result"
        assert res[0]["url"] == "https://example.com/ya"

    def test_dedup_by_url(self):
        from app.subagents.search import _dedup
        items = [
            {"title": "A", "url": "https://x.com/a", "snippet": ""},
            {"title": "A2", "url": "https://x.com/a", "snippet": ""},
            {"title": "B", "url": "https://x.com/b/", "snippet": ""},
            {"title": "B2", "url": "https://x.com/b", "snippet": ""},
            {"title": "C", "url": "notaurl", "snippet": ""},
        ]
        res = _dedup(items)
        assert len(res) == 2

    def test_format_raw_includes_engine(self):
        from app.subagents.search import format_raw
        out = format_raw([{"title": "T", "url": "https://a", "snippet": "s", "engine": "google"}])
        assert "google" in out and "https://a" in out


class TestConfigEnvFile:
    def test_parse_env_file_quotes_and_comments(self, tmp_path):
        from app.config import _parse_env_file
        f = tmp_path / ".env"
        f.write_text(
            "# comment\n"
            "PLAIN=value1\n"
            'QUOTED="value 2"  # inline comment\n'
            "EMPTY=\n"
            "NOEQUALS\n",
            encoding="utf-8",
        )
        vals = _parse_env_file(f)
        assert vals["PLAIN"] == "value1"
        assert vals["QUOTED"] == "value 2"
        assert "EMPTY" in vals
        assert "NOEQUALS" not in vals

    def test_default_ports(self, monkeypatch):
        import importlib
        import os
        from app import config as config_mod
        for var in ("SYMPOSIUM_PORT", "SYMPOSIUM_UI_PORT", "SYMPOSIUM_HOST"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(config_mod, "_env_file_cache", {})
        importlib.reload(config_mod)
        cfg = config_mod.Config()
        assert cfg.port == 23606
        assert cfg.ui_port == 23607
        assert cfg.ui_enabled
        assert cfg.boot_profile_ready is False
        importlib.reload(config_mod)


class TestMemberDefaults:
    def test_member_default_max_tokens_2000(self):
        from app.api.symposia_routes import MemberIn
        m = MemberIn(name="X", role_id=1)
        assert m.max_tokens == 2000
        assert m.temperature == 0.7


class TestSeedRepair:
    """Починка сломанных (пустых) промптов и фолбэк к файлу (v0.3)."""

    def test_active_system_prompt_falls_back_on_empty_row(self):
        from app import db
        from app.seeds import active_system_prompt, seed_defaults
        from app import prompt_store
        # пустая активная правка админа → действует файл
        db.execute("DELETE FROM system_prompts WHERE key='turn_protocol'")
        db.execute(
            "INSERT INTO system_prompts(key, version, content, is_active, note, created_at) "
            "VALUES('turn_protocol', 1, '', 1, 'broken', '2020-01-01')"
        )
        try:
            assert active_system_prompt("turn_protocol") == prompt_store.read_system_prompt("turn_protocol")
        finally:
            db.execute("DELETE FROM system_prompts WHERE key='turn_protocol'")
            seed_defaults()


    def test_file_change_is_hot_reloaded(self):
        """Промпт перечитывается с диска при изменении файла."""
        import time
        from app import prompt_store
        path = prompt_store.prompts_dir() / "system" / "secretary_summarize.md"
        original = path.read_text()
        try:
            # mtime должен измениться — гарантируем паузой
            time.sleep(0.02)
            path.write_text("Test-only marker line. " + original, encoding="utf-8")
            text = prompt_store.read_system_prompt("secretary_summarize")
            assert text.startswith("Test-only marker line.")
        finally:
            time.sleep(0.02)
            path.write_text(original, encoding="utf-8")
            assert prompt_store.read_system_prompt("secretary_summarize") == original.strip()

    def test_admin_override_beats_file(self):
        """Правка админа перекрывает файл до сброса."""
        from app import db
        from app.seeds import active_system_prompt
        db.execute(
            "INSERT INTO system_prompts(key, version, content, is_active, note, created_at) "
            "VALUES('secretary_open', 1, 'ADMIN OVERRIDE TEXT', 1, 'test', '2020-01-01')"
        )
        try:
            assert active_system_prompt("secretary_open") == "ADMIN OVERRIDE TEXT"
        finally:
            db.execute("DELETE FROM system_prompts WHERE key='secretary_open'")

    def test_seed_defaults_no_changes_on_healthy_db(self):
        from app import db
        from app.seeds import seed_defaults
        # здоровая база: посев не СОЗДАЁТ новых правок (существующие не трогает)
        before = db.query_value("SELECT COUNT(*) FROM system_prompts")
        seed_defaults()
        after = db.query_value("SELECT COUNT(*) FROM system_prompts")
        assert before == after


class TestDockerWorkspace:
    def test_docker_start_uses_absolute_bind_path(self, monkeypatch):
        """The -v bind mount must be an absolute host path (docker rejects
        relative paths as invalid volume names)."""
        import asyncio

        from app.workspace import manager as ws

        captured = {}

        class FakeProc:
            def __init__(self):
                self.returncode = 0
            async def communicate(self):
                return (b"container-id", b"")

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeProc()

        async def fake_stop(sid):
            return None

        monkeypatch.setattr(ws, "_docker_stop", fake_stop)
        monkeypatch.setattr(ws, "_docker_running", lambda sid: True)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        ok = asyncio.run(ws._docker_start(42))
        assert ok
        cmd = captured["cmd"]
        i = cmd.index("-v")
        mount = cmd[i + 1]
        src, _, dst = mount.partition(":")
        assert src.startswith("/"), f"bind source must be absolute, got {src!r}"
        assert src == str(ws.ws_root(42).resolve())
        assert dst == "/workspace"
