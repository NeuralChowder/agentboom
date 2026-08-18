"""Tests for optional packages (`agentboom add package`)."""
import argparse
import json
import pathlib
import tempfile
import unittest

from agentboom.commands import packages as packages_cmd

from helpers import AgentTestCase

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="agentboom-pkg-tests-"))


def _pkg_args(name, agent_dir):
    return argparse.Namespace(name=name, dir=str(agent_dir))


class PackageDiscoveryTests(AgentTestCase):
    def test_available_packages_listed(self):
        result = packages_cmd.run_packages(argparse.Namespace(dir=None))
        names = {p["name"] for p in result["available"]}
        # The catalog grows; assert the shipped set is present and every
        # entry carries the listing fields the website generator relies on.
        self.assertTrue({"telegram", "rich-link", "vault"} <= names, names)
        self.assertIn("weather", names)
        for pkg in result["available"]:
            self.assertIn("kind", pkg)
            self.assertIn("source", pkg)
            self.assertEqual(pkg["source"], "builtin")


class RichLinkPackageTests(AgentTestCase):
    def test_install_creates_miniapp_and_skill(self):
        result = packages_cmd.run_add_package(_pkg_args("rich-link", self.agent_dir))
        self.assertTrue(result["ok"])
        self.assertTrue((self.agent_dir / "platform/miniapps/shortlinks/main.py").is_file())
        self.assertTrue(
            (self.agent_dir / ".qwen-docker/skills/rich-link/SKILL.md").is_file()
        )
        env = (self.agent_dir / ".env.example").read_text(encoding="utf-8")
        self.assertIn("LOCAL_BASE_URL=", env)
        self.assertIn("8000", env)  # rendered {{PORT_PLATFORM}}

    def test_installed_agent_still_validates(self):
        from agentboom.commands import validate as validate_cmd
        packages_cmd.run_add_package(_pkg_args("rich-link", self.agent_dir))
        result = validate_cmd.run(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertTrue(result["ok"], result["checks"])

    def test_registry_records_package(self):
        packages_cmd.run_add_package(_pkg_args("rich-link", self.agent_dir))
        registry = json.loads((self.agent_dir / ".agentboom.json").read_text(encoding="utf-8"))
        self.assertIn("rich-link", registry["packages"])


class VaultPackageTests(AgentTestCase):
    def test_install_adds_migration_requirement_and_env(self):
        result = packages_cmd.run_add_package(_pkg_args("vault", self.agent_dir))
        self.assertTrue(result["ok"])
        self.assertTrue(
            (self.agent_dir / "platform/migrations/002_vault.sql").is_file()
        )
        reqs = (self.agent_dir / "platform/requirements.txt").read_text(encoding="utf-8")
        self.assertIn("cryptography", reqs)
        env = (self.agent_dir / ".env.example").read_text(encoding="utf-8")
        self.assertIn("VAULT_KEY=", env)

    def test_install_is_idempotent(self):
        packages_cmd.run_add_package(_pkg_args("vault", self.agent_dir))
        packages_cmd.run_add_package(_pkg_args("vault", self.agent_dir))
        reqs = (self.agent_dir / "platform/requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(reqs.count("cryptography"), 1)
        env = (self.agent_dir / ".env.example").read_text(encoding="utf-8")
        self.assertEqual(env.count("VAULT_KEY="), 1)


class TelegramPackageTests(AgentTestCase):
    def test_install_renders_agent_name_into_env(self):
        packages_cmd.run_add_package(_pkg_args("telegram", self.agent_dir))
        env = (self.agent_dir / ".env.example").read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_BOT_TOKEN=", env)
        self.assertIn(f"CHANNEL_NAME={self.name}-telegram", env)
        self.assertTrue(
            (self.agent_dir / "docs/telegram-channel.md").is_file()
        )

    def test_unknown_package_raises(self):
        with self.assertRaises(packages_cmd.PackageError):
            packages_cmd.run_add_package(_pkg_args("does-not-exist", self.agent_dir))


class ConnectorPackageTests(AgentTestCase):
    """Connector packages ship importable clients under platform/connectors/."""

    def test_weather_installs_connector_and_miniapp(self):
        result = packages_cmd.run_add_package(_pkg_args("weather", self.agent_dir))
        self.assertTrue(result["ok"])
        self.assertTrue(
            (self.agent_dir / "platform/connectors/__init__.py").is_file())
        self.assertTrue(
            (self.agent_dir / "platform/connectors/weather/__init__.py").is_file())
        self.assertTrue(
            (self.agent_dir / "platform/miniapps/weather/main.py").is_file())
        registry = json.loads((self.agent_dir / ".agentboom.json").read_text())
        self.assertEqual(registry["packages"]["weather"]["kind"], "connector")

    def test_two_connectors_share_the_connectors_root(self):
        packages_cmd.run_add_package(_pkg_args("weather", self.agent_dir))
        packages_cmd.run_add_package(_pkg_args("ntfy", self.agent_dir))
        self.assertTrue(
            (self.agent_dir / "platform/connectors/ntfy/__init__.py").is_file())
        self.assertTrue(
            (self.agent_dir / "platform/connectors/weather/__init__.py").is_file())

    def test_rss_feeds_ships_migration_and_valid_cron_job(self):
        packages_cmd.run_add_package(_pkg_args("rss-feeds", self.agent_dir))
        self.assertTrue(
            (self.agent_dir / "platform/migrations/003_feeds.sql").is_file())
        reqs = (self.agent_dir / "platform/requirements.txt").read_text()
        self.assertIn("feedparser", reqs)
        # validate parses manifest jobs: the poll job's cron must pass.
        from agentboom.commands import validate as validate_cmd
        result = validate_cmd.run(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertTrue(result["ok"], result["checks"])

    def test_installed_connectors_agent_still_validates(self):
        from agentboom.commands import validate as validate_cmd
        for name in ("weather", "ntfy", "github-watch"):
            packages_cmd.run_add_package(_pkg_args(name, self.agent_dir))
        result = validate_cmd.run(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertTrue(result["ok"], result["checks"])


class EmailStackTests(AgentTestCase):
    """The email trio: vault <- email <- email-actions / email-search."""

    def test_email_refuses_without_vault(self):
        with self.assertRaises(packages_cmd.PackageError) as ctx:
            packages_cmd.run_add_package(_pkg_args("email", self.agent_dir))
        self.assertIn("requires: vault", str(ctx.exception))

    def test_email_actions_refuses_without_email(self):
        packages_cmd.run_add_package(_pkg_args("vault", self.agent_dir))
        with self.assertRaises(packages_cmd.PackageError) as ctx:
            packages_cmd.run_add_package(
                _pkg_args("email-actions", self.agent_dir))
        self.assertIn("requires: email", str(ctx.exception))

    def test_full_chain_installs_and_validates(self):
        from agentboom.commands import validate as validate_cmd
        for name in ("vault", "email", "email-actions", "email-search"):
            packages_cmd.run_add_package(_pkg_args(name, self.agent_dir))
        base = self.agent_dir / "platform"
        self.assertTrue((base / "connectors/email/__init__.py").is_file())
        self.assertTrue((base / "migrations/005_email.sql").is_file())
        self.assertTrue((base / "migrations/006_email_actions.sql").is_file())
        self.assertTrue((base / "miniapps/email-sync/main.py").is_file())
        self.assertTrue((base / "miniapps/email-actions/main.py").is_file())
        self.assertTrue((base / "miniapps/email-search/main.py").is_file())
        self.assertTrue(
            (self.agent_dir / ".qwen-docker/skills/email-manager/SKILL.md").is_file())
        result = validate_cmd.run(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertTrue(result["ok"], result["checks"])


class UseCaseEnginePackagesTests(AgentTestCase):
    """finance / documents / digests: engines whose use cases are defined
    through their APIs — install + validate must be green standalone."""

    def test_standalone_engines_install_and_validate(self):
        from agentboom.commands import validate as validate_cmd
        for name in ("finance", "documents", "digests", "knowledge", "storage"):
            packages_cmd.run_add_package(_pkg_args(name, self.agent_dir))
        base = self.agent_dir / "platform"
        self.assertTrue((base / "migrations/008_finance.sql").is_file())
        self.assertTrue((base / "migrations/009_documents.sql").is_file())
        self.assertTrue((base / "migrations/010_digests.sql").is_file())
        self.assertTrue((base / "miniapps/finance/main.py").is_file())
        self.assertTrue((base / "miniapps/documents/main.py").is_file())
        self.assertTrue((base / "miniapps/digests/main.py").is_file())
        result = validate_cmd.run(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertTrue(result["ok"], result["checks"])

    def test_calendar_requires_vault_and_installs_with_it(self):
        from agentboom.commands import validate as validate_cmd
        with self.assertRaises(packages_cmd.PackageError) as ctx:
            packages_cmd.run_add_package(_pkg_args("calendar", self.agent_dir))
        self.assertIn("requires: vault", str(ctx.exception))
        packages_cmd.run_add_package(_pkg_args("vault", self.agent_dir))
        result = packages_cmd.run_add_package(_pkg_args("calendar", self.agent_dir))
        self.assertTrue(result["ok"])
        base = self.agent_dir / "platform"
        self.assertTrue((base / "connectors/caldav/__init__.py").is_file())
        self.assertTrue((base / "migrations/011_calendar.sql").is_file())
        self.assertTrue((base / "miniapps/calendar/main.py").is_file())
        result = validate_cmd.run(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertTrue(result["ok"], result["checks"])

    def test_every_migration_ships_a_postgres_variant(self):
        """Dual-database doctrine: each package migration may be applied on
        SQLite (default, zero setup) or PostgreSQL (.pg.sql variant)."""
        from agentboom import registries as registries_mod
        root = registries_mod.packages_root()
        for pkg in root.iterdir():
            migrations = pkg / "platform" / "migrations"
            if not migrations.is_dir():
                continue
            for base in migrations.glob("[0-9]*.sql"):
                if base.name.endswith(".pg.sql"):
                    continue
                self.assertTrue(
                    (migrations / (base.stem + ".pg.sql")).is_file(),
                    f"{pkg.name}: {base.name} has no .pg.sql variant")


class MigrationDialectTests(unittest.TestCase):
    """The runner picks the .pg.sql variant only on PostgreSQL agents."""

    def _dir_with_pair(self) -> pathlib.Path:
        d = pathlib.Path(tempfile.mkdtemp(dir=_TMP))
        (d / "001_thing.sql").write_text("-- sqlite/base\n")
        (d / "001_thing.pg.sql").write_text("-- postgres variant\n")
        (d / "002_plain.sql").write_text("-- shared\n")
        return d

    def test_sqlite_backend_uses_base_files(self):
        from agentboom_sdk import db
        self.assertFalse(db.is_postgres())  # tests run SQLite-only
        selected = db._select_migration_files(self._dir_with_pair())
        self.assertEqual(sorted(selected), ["001_thing.sql", "002_plain.sql"])
        self.assertFalse(selected["001_thing.sql"].name.endswith(".pg.sql"))

    def test_postgres_backend_prefers_pg_variant(self):
        from agentboom_sdk import db
        original = db._use_postgres
        db._use_postgres = lambda: True
        try:
            selected = db._select_migration_files(self._dir_with_pair())
        finally:
            db._use_postgres = original
        self.assertEqual(sorted(selected), ["001_thing.sql", "002_plain.sql"])
        self.assertTrue(selected["001_thing.sql"].name.endswith(".pg.sql"))
        self.assertEqual(selected["002_plain.sql"].name, "002_plain.sql")


if __name__ == "__main__":
    import unittest
    unittest.main()
