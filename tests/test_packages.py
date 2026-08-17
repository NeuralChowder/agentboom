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
        self.assertEqual(names, {"telegram", "rich-link", "vault"})


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


if __name__ == "__main__":
    import unittest
    unittest.main()
