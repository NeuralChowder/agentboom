"""Tests for scaffolding commands (add skill/miniapp), discovery, selfcheck."""
import argparse
import json

from agentboom.commands import add as add_cmd
from agentboom.commands import listcmd
from agentboom.commands import selfcheck as selfcheck_cmd

from helpers import AgentTestCase


def _add_args(name, agent_dir, description=""):
    return argparse.Namespace(name=name, description=description, dir=str(agent_dir))


class AddSkillTests(AgentTestCase):
    def test_skill_scaffold(self):
        result = add_cmd.run_skill(_add_args("my-skill", self.agent_dir, "does things"))
        self.assertTrue(result["ok"])
        skill_md = self.agent_dir / ".qwen-docker/skills/my-skill/SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        self.assertIn("name: my-skill", text)
        self.assertIn("does things", text)

    def test_refuses_duplicate(self):
        add_cmd.run_skill(_add_args("dup", self.agent_dir))
        with self.assertRaises(add_cmd.AddError):
            add_cmd.run_skill(_add_args("dup", self.agent_dir))

    def test_rejects_bad_name(self):
        with self.assertRaises(add_cmd.AddError):
            add_cmd.run_skill(_add_args("Bad Name!", self.agent_dir))


class AddMiniappTests(AgentTestCase):
    def test_miniapp_scaffold(self):
        result = add_cmd.run_miniapp(_add_args("my-app", self.agent_dir, "test app"))
        self.assertTrue(result["ok"])
        app_dir = self.agent_dir / "platform/miniapps/my-app"
        main_py = (app_dir / "main.py").read_text(encoding="utf-8")
        self.assertIn("def get_router", main_py)
        manifest = json.loads((app_dir / ".miniapp.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "my-app")

    def test_scaffolded_miniapp_passes_validate(self):
        from agentboom.commands import validate as validate_cmd
        add_cmd.run_miniapp(_add_args("checked-app", self.agent_dir, "x"))
        result = validate_cmd.run(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertTrue(result["ok"], result["checks"])


class DiscoveryTests(AgentTestCase):
    def test_list_finds_agents(self):
        result = listcmd.run_list(argparse.Namespace(dir=str(self.tmp)))
        self.assertEqual(len(result["agents"]), 1)
        self.assertEqual(result["agents"][0]["name"], self.name)

    def test_skills_listed_with_managed_flag(self):
        result = listcmd.run_skills(argparse.Namespace(dir=str(self.agent_dir)))
        names = {s["name"] for s in result["skills"]}
        self.assertIn("web-search", names)
        self.assertIn("skill-creator", names)
        self.assertTrue(all(s["managed"] for s in result["skills"]))

    def test_miniapps_listed(self):
        result = listcmd.run_miniapps(argparse.Namespace(dir=str(self.agent_dir)))
        self.assertEqual(result["miniapps"][0]["name"], "hello")


class SelfcheckTest(AgentTestCase):
    def setUp(self):
        pass  # selfcheck manages its own temp dir

    def tearDown(self):
        pass

    def test_selfcheck_passes(self):
        result = selfcheck_cmd.run(argparse.Namespace())
        self.assertTrue(result["ok"], result["steps"])


if __name__ == "__main__":
    import unittest
    unittest.main()
