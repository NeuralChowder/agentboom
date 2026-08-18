"""Tests for optional packages (`agentboom add package`)."""
import argparse
import json

from agentboom.commands import packages as packages_cmd

from helpers import AgentTestCase


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


if __name__ == "__main__":
    import unittest
    unittest.main()
