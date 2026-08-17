"""Lifecycle tests: init -> validate -> upgrade (drift, restore, force)."""
import argparse
import json

from agentloom.commands import upgrade as upgrade_cmd
from agentloom.commands import validate as validate_cmd
from agentloom.registry import REGISTRY_NAME, load_registry, sha256_file

from helpers import AgentTestCase


def _upgrade_args(agent_dir, apply=False, force=False):
    return argparse.Namespace(dir=str(agent_dir), apply=apply, force=force)


def _validate_args(agent_dir):
    return argparse.Namespace(dir=str(agent_dir))


class InitTests(AgentTestCase):
    def test_registry_written_and_complete(self):
        registry = load_registry(self.agent_dir)
        self.assertIsNotNone(registry)
        self.assertEqual(registry["name"], self.name)
        self.assertEqual(registry["template"], "platform")
        self.assertIn("platform/sdk/agent.py", registry["managed"])
        self.assertIn(".qwen-docker/skills/web-search/SKILL.md", registry["managed"])

    def test_no_unrendered_placeholders(self):
        leftovers = []
        for path in self.agent_dir.rglob("*"):
            if path.is_file() and path.name not in (".agentloom.json",):
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                if "{{" in text:
                    leftovers.append(str(path))
        self.assertEqual(leftovers, [])

    def test_executables_are_executable(self):
        import os
        for rel in ("entrypoint.sh",
                    "platform/scripts/prune_agent_transcripts.py",
                    ".qwen-docker/skills/skill-creator/scripts/validate-skill.sh"):
            path = self.agent_dir / rel
            self.assertTrue(os.access(path, os.X_OK), f"{rel} should be executable")

    def test_validate_passes_on_fresh_agent(self):
        result = validate_cmd.run(_validate_args(self.agent_dir))
        self.assertTrue(result["ok"], result["checks"])

    def test_upgrade_check_clean_on_fresh_agent(self):
        result = upgrade_cmd.run(_upgrade_args(self.agent_dir))
        self.assertFalse(result["changed"])

    def test_init_refuses_non_empty_dir(self):
        from agentloom.commands.init import InitError, run
        with self.assertRaises(InitError):
            run(argparse.Namespace(
                dir=str(self.agent_dir), name="other", description="",
                port_agent=4170, port_platform=8000, force=False))


class UpgradeTests(AgentTestCase):
    def test_locally_modified_is_reported_and_skipped(self):
        llm = self.agent_dir / "platform/sdk/llm.py"
        llm.write_text(llm.read_text() + "\n# local hack\n", encoding="utf-8")

        check = upgrade_cmd.run(_upgrade_args(self.agent_dir))
        self.assertIn("platform/sdk/llm.py", check["locally_modified"])

        apply_result = upgrade_cmd.run(_upgrade_args(self.agent_dir, apply=True))
        self.assertIn("platform/sdk/llm.py", apply_result["locally_modified"])
        self.assertIn("# local hack", llm.read_text(encoding="utf-8"))

    def test_force_overwrites_local_modification(self):
        llm = self.agent_dir / "platform/sdk/llm.py"
        llm.write_text(llm.read_text() + "\n# local hack\n", encoding="utf-8")
        upgrade_cmd.run(_upgrade_args(self.agent_dir, apply=True, force=True))
        self.assertNotIn("# local hack", llm.read_text(encoding="utf-8"))
        registry = load_registry(self.agent_dir)
        self.assertEqual(registry["managed"]["platform/sdk/llm.py"], sha256_file(llm))

    def test_deleted_managed_file_is_restored(self):
        cron = self.agent_dir / "platform/sdk/cron.py"
        cron.unlink()
        upgrade_cmd.run(_upgrade_args(self.agent_dir, apply=True))
        self.assertTrue(cron.is_file())

    def test_apply_updates_registry_hash(self):
        registry_before = load_registry(self.agent_dir)
        cron = self.agent_dir / "platform/sdk/cron.py"
        cron.unlink()
        upgrade_cmd.run(_upgrade_args(self.agent_dir, apply=True))
        registry_after = load_registry(self.agent_dir)
        self.assertEqual(
            registry_after["managed"]["platform/sdk/cron.py"],
            registry_before["managed"]["platform/sdk/cron.py"],
        )


class ValidateFailureTests(AgentTestCase):
    def test_bad_cron_detected(self):
        manifest = self.agent_dir / "platform/miniapps/hello/.miniapp.json"
        data = json.loads(manifest.read_text())
        data["jobs"][1]["cron"] = "99 99 * * *"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        result = validate_cmd.run(_validate_args(self.agent_dir))
        self.assertFalse(result["ok"])
        self.assertTrue(any(c["id"] == "miniapp.bad-cron" for c in result["checks"]))

    def test_missing_required_file_detected(self):
        (self.agent_dir / "entrypoint.sh").unlink()
        result = validate_cmd.run(_validate_args(self.agent_dir))
        self.assertFalse(result["ok"])
        self.assertTrue(any(c["id"] == "structure.missing-file" for c in result["checks"]))

    def test_drift_reported_as_info(self):
        llm = self.agent_dir / "platform/sdk/llm.py"
        llm.write_text(llm.read_text() + "\n# drift\n", encoding="utf-8")
        result = validate_cmd.run(_validate_args(self.agent_dir))
        self.assertTrue(result["ok"])  # drift is informational
        self.assertTrue(any(c["id"] == "base.locally-modified" for c in result["checks"]))


if __name__ == "__main__":
    import unittest
    unittest.main()
