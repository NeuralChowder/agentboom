"""Tests for adopt, fleet registry, and the boomkeeper console."""
import argparse
import os
import pathlib
import shutil
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="agentboom-fleet-tests-")
os.environ["AGENTBOOM_HOME"] = str(pathlib.Path(_TMP) / "home")

from agentboom import fleet as fleet_reg  # noqa: E402
from agentboom.commands import adopt as adopt_cmd  # noqa: E402
from agentboom.commands import console as console_cmd  # noqa: E402
from agentboom.commands import fleetcmd  # noqa: E402
from agentboom.commands import init as init_cmd  # noqa: E402


def _init(where: pathlib.Path, name: str) -> dict:
    return init_cmd.run(argparse.Namespace(
        dir=str(where), name=name, description="fleet test agent",
        port_agent=4170, port_platform=8000, force=False,
    ))


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=_TMP))

    def test_adopt_matches_untouched_managed_files(self):
        init_result = _init(self.dir / "a1", "a1")
        original = set(init_result["created"]) & set(
            (self.dir / "a1" / ".agentboom.json").exists() and
            __import__("json").loads(
                (self.dir / "a1" / ".agentboom.json").read_text()
            )["managed"]
        )
        (self.dir / "a1" / ".agentboom.json").unlink()
        result = adopt_cmd.run(argparse.Namespace(
            dir=str(self.dir / "a1"), name="a1", description=None,
            template="platform", port_agent=4170, port_platform=8000))
        self.assertTrue(result["ok"])
        self.assertEqual(set(result["managed_matched"]), original)

    def test_diverged_file_becomes_agent_owned(self):
        _init(self.dir / "a2", "a2")
        gw = self.dir / "a2" / "platform/api_gateway.py"
        gw.write_text(gw.read_text() + "\n# local divergence\n")
        (self.dir / "a2" / ".agentboom.json").unlink()
        result = adopt_cmd.run(argparse.Namespace(
            dir=str(self.dir / "a2"), name="a2", description=None,
            template="platform", port_agent=4170, port_platform=8000))
        self.assertNotIn("platform/api_gateway.py", result["managed_matched"])
        self.assertIn("platform/api_gateway.py", result["owned_or_diverged"])

    def test_refuses_when_registry_exists(self):
        _init(self.dir / "a3", "a3")
        with self.assertRaises(adopt_cmd.AdoptError):
            adopt_cmd.run(argparse.Namespace(
                dir=str(self.dir / "a3"), name="a3", description=None,
                template="platform", port_agent=4170, port_platform=8000))


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=_TMP))

    def test_init_auto_registers(self):
        _init(self.dir / "b1", "b1")
        fleet = fleet_reg.load_fleet()
        paths = [e["path"] for e in fleet["agents"]]
        self.assertIn(str((self.dir / "b1").resolve()), paths)

    def test_status_reports_health_and_drift(self):
        _init(self.dir / "b2", "b2")
        gw = self.dir / "b2" / "platform/api_gateway.py"
        gw.write_text(gw.read_text() + "\n# drift\n")
        status = fleetcmd.run_status(argparse.Namespace())
        row = [r for r in status["agents"] if r["name"] == "b2"][0]
        self.assertTrue(row["ok"])
        self.assertIn("platform/api_gateway.py", row["drift_modified"])
        self.assertEqual(row["validate_errors"], 0)  # drift is informational

    def test_remove_and_unknown(self):
        _init(self.dir / "b3", "b3")
        self.assertTrue(fleetcmd.run_remove(argparse.Namespace(name="b3"))["ok"])
        with self.assertRaises(fleetcmd.FleetError):
            fleetcmd.run_remove(argparse.Namespace(name="b3"))

    def test_add_requires_registry(self):
        plain = self.dir / "plain"
        plain.mkdir()
        with self.assertRaises(fleetcmd.FleetError):
            fleetcmd.run_add(argparse.Namespace(dir=str(plain)))


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=_TMP))

    def test_dry_run_materializes_workspace(self):
        _init(self.dir / "c1", "c1")
        result = console_cmd.run(argparse.Namespace(dry_run=True, qwen_args=[]))
        ws = pathlib.Path(result["workspace"])
        self.assertTrue((ws / "AGENTS.md").is_file())
        self.assertTrue((ws / "skills/fleet-ops/SKILL.md").is_file())
        snap = (ws / "fleet-snapshot.md").read_text()
        self.assertIn("c1", snap)
        self.assertIn("agentboom", snap)

    def test_workspace_refresh_keeps_user_files(self):
        _init(self.dir / "c2", "c2")
        console_cmd.run(argparse.Namespace(dry_run=True, qwen_args=[]))
        ws = console_cmd.console_dir()
        (ws / "notes.md").write_text("mine")
        console_cmd.run(argparse.Namespace(dry_run=True, qwen_args=[]))
        self.assertEqual((ws / "notes.md").read_text(), "mine")


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class CodeCommandTests(unittest.TestCase):
    def setUp(self):
        self.agent_dir = pathlib.Path(tempfile.mkdtemp(dir=_TMP)) / "coded"
        _init(self.agent_dir, "coded")

    def test_code_miniapp_dry_run_scaffolds_and_prepares_mission(self):
        from agentboom.commands import code as code_cmd
        result = code_cmd.run_miniapp(argparse.Namespace(
            name="coded-app", prompt="track my plants watering",
            description="", dir=str(self.agent_dir), dry_run=True))
        self.assertTrue(result["ok"])
        self.assertTrue(result["scaffolded"])
        self.assertIn("platform/miniapps/coded-app", result["mission"])
        self.assertIn("agentboom_sdk", result["mission"])
        self.assertTrue(
            (self.agent_dir / "platform/miniapps/coded-app/main.py").is_file())

    def test_code_refuses_non_agent_dir(self):
        from agentboom.commands import code as code_cmd
        plain = pathlib.Path(tempfile.mkdtemp(dir=_TMP))
        with self.assertRaises(code_cmd.CodeError):
            code_cmd.run_miniapp(argparse.Namespace(
                name="x", prompt="", description="", dir=str(plain), dry_run=True))
