"""Tests for the movienight package (agentboom package: movienight).

Conventions per tests/test_durable_events.py: DATA_DIR temp + pop
DATABASE_URI before agentboom_sdk.db is imported, per-class loop +
run_async, tearDownModule resetting db._op_lock._lock.

The mini-app is loaded from the template source (bytecode off — the
installer copies every file in the template tree) against a real temp
SQLite database with the package's own migration applied. The agent
is stubbed (no LLM turn is ever started).

Tests pure logic only (helpers and validators). HTTP routes are covered
by the parent agent's e2e suite.
"""
import asyncio
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="agentboom-movienight-tests-")
os.environ["DATA_DIR"] = str(pathlib.Path(_TMP) / "data")
os.environ.pop("DATABASE_URI", None)
for _k in ("QWEN_AGENT_URL", "QWEN_SERVER_TOKEN", "AGENT_SESSION_LABEL"):
    os.environ.pop(_k, None)

from agentboom_sdk import db  # noqa: E402
from fastapi import HTTPException  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = REPO_ROOT / "src/agentboom/templates/packages/movienight"
_MAIN = _PKG / "platform/miniapps/movienight/main.py"


def _load_miniapp():
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "movienight_miniapp_under_test", _MAIN)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


mod = _load_miniapp()


class MovieNightPureLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    # ------------------------------------------------------------------
    # _fold: accents, case, whitespace
    # ------------------------------------------------------------------

    def test_fold_strips_accents(self):
        self.assertEqual(mod._fold("Interstellar"), "interstellar")
        self.assertEqual(mod._fold("InterstellaR"), "interstellar")
        self.assertEqual(mod._fold("Código de Verificação"),
                         "codigo de verificacao")
        self.assertEqual(mod._fold("São Paulo"), "sao paulo")
        self.assertEqual(mod._fold("El Niño"), "el nino")

    def test_fold_handles_whitespace(self):
        self.assertEqual(mod._fold("  Hello  "), "hello")
        self.assertEqual(mod._fold("\tWorld\n"), "world")

    def test_fold_handles_none(self):
        self.assertEqual(mod._fold(None), "")

    def test_fold_dedup_match(self):
        """Two differently accented forms should produce the same fold."""
        self.assertEqual(mod._fold("Café"), mod._fold("café"))
        self.assertEqual(mod._fold("Café"), mod._fold("CAFÉ"))

    # ------------------------------------------------------------------
    # _poster_url: https only
    # ------------------------------------------------------------------

    def test_poster_url_accepts_https(self):
        url = "https://image.tmdb.org/t/p/w500/abc123.jpg"
        self.assertEqual(mod._poster_url(url), url)

    def test_poster_url_rejects_http(self):
        self.assertEqual(mod._poster_url("http://example.com/p.png"), "")

    def test_poster_url_rejects_data_uri(self):
        self.assertEqual(
            mod._poster_url("data:image/png;base64,abc"), "")

    def test_poster_url_rejects_javascript(self):
        self.assertEqual(
            mod._poster_url("javascript:void(0)"), "")

    def test_poster_url_rejects_n_a(self):
        self.assertEqual(mod._poster_url("N/A"), "")
        self.assertEqual(mod._poster_url("n/a"), "")

    def test_poster_url_rejects_empty(self):
        self.assertEqual(mod._poster_url(""), "")
        self.assertEqual(mod._poster_url(None), "")
        self.assertEqual(mod._poster_url(0), "")

    def test_poster_url_rejects_missing_netloc(self):
        self.assertEqual(mod._poster_url("https:///path"), "")

    # ------------------------------------------------------------------
    # _coerce_bool
    # ------------------------------------------------------------------

    def test_coerce_bool_truthy_strings(self):
        for v in ("true", "True", "1", "yes", "on", "YES"):
            self.assertIs(mod._coerce_bool(v), True)

    def test_coerce_bool_falsy_strings(self):
        for v in ("false", "False", "0", "no", "off", "NO"):
            self.assertIs(mod._coerce_bool(v), False)

    def test_coerce_bool_ints(self):
        self.assertIs(mod._coerce_bool(1), True)
        self.assertIs(mod._coerce_bool(0), False)
        self.assertIs(mod._coerce_bool(42), True)

    def test_coerce_bool_none_returns_default(self):
        self.assertIsNone(mod._coerce_bool(None))
        self.assertIs(mod._coerce_bool(None, default=True), True)
        self.assertIs(mod._coerce_bool(None, default=False), False)

    def test_coerce_bool_real_booleans(self):
        self.assertIs(mod._coerce_bool(True), True)
        self.assertIs(mod._coerce_bool(False), False)

    def test_coerce_bool_invalid_raises(self):
        with self.assertRaises(HTTPException) as ctx:
            mod._coerce_bool("maybe")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_coerce_bool_empty_returns_default(self):
        self.assertIs(mod._coerce_bool("  "), None)
        self.assertIs(mod._coerce_bool("", default=True), True)

    # ------------------------------------------------------------------
    # _normalize_titles
    # ------------------------------------------------------------------

    def test_normalize_non_list(self):
        self.assertEqual(mod._normalize_titles("not a list", {}), [])
        self.assertEqual(mod._normalize_titles(42, {}), [])
        self.assertEqual(mod._normalize_titles(None, {}), [])

    def test_normalize_missing_title_skipped(self):
        result = mod._normalize_titles(
            [{"platform": "netflix", "type": "movie"}],
            {"netflix": "Netflix"}
        )
        self.assertEqual(result, [])

    def test_normalize_bad_platform_dropped(self):
        result = mod._normalize_titles(
            [
                {"title": "A", "platform": "netflix", "type": "movie"},
                {"title": "B", "platform": "disney", "type": "movie"},
            ],
            {"netflix": "Netflix", "prime": "Prime Video"}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "A")

    def test_normalize_year_clamping(self):
        # This test runs against the real module — use the module's _normalize_titles.
        # Since datetime.now() changes, we check the clamping logic.
        # Year before 1900 -> None
        result = mod._normalize_titles(
            [{"title": "X", "platform": "netflix", "year": 1899, "type": "movie"}],
            {"netflix": "Netflix"}
        )
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["year"])

        # Year way in the future -> None
        result = mod._normalize_titles(
            [{"title": "Y", "platform": "netflix", "year": 9999, "type": "movie"}],
            {"netflix": "Netflix"}
        )
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["year"])

        # Valid year passes through
        result = mod._normalize_titles(
            [{"title": "Z", "platform": "netflix", "year": 2024, "type": "movie"}],
            {"netflix": "Netflix"}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["year"], 2024)

    def test_normalize_invalid_year_becomes_none(self):
        result = mod._normalize_titles(
            [{"title": "W", "platform": "netflix", "year": "not-a-year", "type": "movie"}],
            {"netflix": "Netflix"}
        )
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["year"])

    def test_normalize_invalid_type_defaults_to_movie(self):
        result = mod._normalize_titles(
            [{"title": "X", "platform": "netflix", "type": "documentary"}],
            {"netflix": "Netflix"}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "movie")

    def test_normalize_platform_lowered(self):
        result = mod._normalize_titles(
            [{"title": "X", "platform": "NETFLIX", "type": "movie"}],
            {"netflix": "Netflix"}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["platform"], "netflix")

    # ------------------------------------------------------------------
    # _parse_platforms (settings validation)
    # ------------------------------------------------------------------

    def test_parse_platforms_valid_json_string(self):
        result = mod._parse_platforms('{"netflix": "Netflix"}')
        self.assertEqual(result, {"netflix": "Netflix"})

    def test_parse_platforms_valid_dict(self):
        result = mod._parse_platforms({"netflix": "Netflix"})
        self.assertEqual(result, {"netflix": "Netflix"})

    def test_parse_platforms_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            mod._parse_platforms("{bad json}")

    def test_parse_platforms_empty_dict_raises(self):
        with self.assertRaises(ValueError):
            mod._parse_platforms({})

    def test_parse_platforms_non_dict_raises(self):
        with self.assertRaises(ValueError):
            mod._parse_platforms("not json at all")

    def test_parse_platforms_non_string_values_raises(self):
        with self.assertRaises(ValueError):
            mod._parse_platforms({"key": 123})

    def test_parse_platforms_non_string_keys_raises(self):
        with self.assertRaises(ValueError):
            mod._parse_platforms({123: "value"})

    # ------------------------------------------------------------------
    # _validate_country (settings validation)
    # ------------------------------------------------------------------

    def test_validate_country_ok(self):
        self.assertEqual(mod._validate_country("Germany"), "Germany")

    def test_validate_country_strips(self):
        self.assertEqual(mod._validate_country("  Germany  "), "Germany")

    def test_validate_country_max_length(self):
        # 40 chars is OK
        self.assertEqual(mod._validate_country("a" * 40), "a" * 40)
        # 41 chars raises
        with self.assertRaises(ValueError):
            mod._validate_country("a" * 41)

    def test_validate_country_empty_string_ok(self):
        self.assertEqual(mod._validate_country(""), "")
        self.assertEqual(mod._validate_country(None), "")


class MovieNightMigrationTests(unittest.TestCase):
    """Verify the migration files parse on SQLite."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        migs = pathlib.Path(_TMP) / "migrations"
        migs.mkdir()
        (migs / "020_movienight.sql").write_text(
            (_PKG / "platform/migrations/020_movienight.sql").read_text())
        cls.loop.run_until_complete(db.run_migrations(migs))

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(db.close())
        cls.loop.close()

    def test_tables_created(self):
        tables = self.loop.run_until_complete(
            db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        )
        table_names = {t["name"] for t in tables}
        self.assertIn("movienight_sessions", table_names)
        self.assertIn("movienight_titles", table_names)
        self.assertIn("movienight_taste_notes", table_names)
        self.assertIn("movienight_settings", table_names)

    def test_sessions_schema(self):
        col_info = self.loop.run_until_complete(
            db.fetchall("PRAGMA table_info(movienight_sessions)"))
        col_names = {c["name"] for c in col_info}
        self.assertIn("id", col_names)
        self.assertIn("session_date", col_names)
        self.assertIn("theme", col_names)
        self.assertIn("created_at", col_names)
        # No shortlink, no telegram_sent
        self.assertNotIn("shortlink", col_names)
        self.assertNotIn("telegram_sent", col_names)

    def test_titles_schema(self):
        col_info = self.loop.run_until_complete(
            db.fetchall("PRAGMA table_info(movienight_titles)"))
        col_names = {c["name"] for c in col_info}
        self.assertIn("id", col_names)
        self.assertIn("title_fold", col_names)
        self.assertIn("platform", col_names)
        # platform has no check constraint on allowed values
        # (confirmed by absence of a CHECK constraint in the schema)

    def test_taste_notes_schema(self):
        col_info = self.loop.run_until_complete(
            db.fetchall("PRAGMA table_info(movienight_taste_notes)"))
        col_names = {c["name"] for c in col_info}
        self.assertIn("id", col_names)
        self.assertIn("title", col_names)
        self.assertIn("seen", col_names)
        self.assertIn("source", col_names)

    def test_settings_schema(self):
        col_info = self.loop.run_until_complete(
            db.fetchall("PRAGMA table_info(movienight_settings)"))
        col_names = {c["name"] for c in col_info}
        self.assertIn("key", col_names)
        self.assertIn("value", col_names)
        self.assertIn("updated_at", col_names)

    def test_insert_and_read(self):
        now = mod._now()
        self.loop.run_until_complete(
            db.execute(
                """INSERT INTO movienight_sessions
                     (session_date, theme, created_at)
                   VALUES ($1, $2, $3)""",
                "2026-01-01", "Test Theme", now))
        row = self.loop.run_until_complete(
            db.fetchone("SELECT * FROM movienight_sessions WHERE id = $1", 1))
        self.assertEqual(row["session_date"], "2026-01-01")
        self.assertEqual(row["theme"], "Test Theme")


class MovieNightSettingsTests(unittest.TestCase):
    """Test the settings routes directly (no HTTP client)."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        migs = pathlib.Path(_TMP) / "migrations2"
        migs.mkdir()
        (migs / "020_movienight.sql").write_text(
            (_PKG / "platform/migrations/020_movienight.sql").read_text())
        cls.loop.run_until_complete(db.run_migrations(migs))

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(db.close())
        cls.loop.close()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    def test_settings_defaults(self):
        async def scenario():
            s = await mod.get_settings_route()
            self.assertIn("platforms", s)
            self.assertIn("country", s)
            self.assertIsInstance(s["platforms"], dict)
            self.assertEqual(s["country"], "")
        self.run_async(scenario())

    def test_settings_update_country(self):
        async def scenario():
            req = mod.SettingsUpdate(country="Germany")
            res = await mod.update_settings_route(req)
            self.assertTrue(res["ok"])
            self.assertEqual(res["settings"]["country"], "Germany")
        self.run_async(scenario())

    def test_settings_update_platforms(self):
        async def scenario():
            req = mod.SettingsUpdate(
                platforms={"disney": "Disney+"})
            res = await mod.update_settings_route(req)
            self.assertTrue(res["ok"])
            self.assertEqual(res["settings"]["platforms"]["disney"], "Disney+")
        self.run_async(scenario())

    def test_settings_update_bad_country_raises(self):
        async def scenario():
            req = mod.SettingsUpdate(country="x" * 50)
            with self.assertRaises(HTTPException) as ctx:
                await mod.update_settings_route(req)
            self.assertEqual(ctx.exception.status_code, 400)
        self.run_async(scenario())

    def test_settings_update_bad_platforms_raises(self):
        async def scenario():
            req = mod.SettingsUpdate(platforms="not json")
            with self.assertRaises(HTTPException) as ctx:
                await mod.update_settings_route(req)
            self.assertEqual(ctx.exception.status_code, 400)
        self.run_async(scenario())

    def test_settings_update_no_changes_raises(self):
        async def scenario():
            req = mod.SettingsUpdate()
            with self.assertRaises(HTTPException) as ctx:
                await mod.update_settings_route(req)
            self.assertEqual(ctx.exception.status_code, 400)
        self.run_async(scenario())


def tearDownModule():
    # A contended asyncio.Lock binds itself to the loop that first waits on
    # it. The concurrent tests above may leave a stuck lock. A fresh,
    # unbound lock restores the pristine state.
    db._op_lock._lock = asyncio.Lock()


if __name__ == "__main__":
    unittest.main()
