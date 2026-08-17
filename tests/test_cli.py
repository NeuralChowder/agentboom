"""CLI dispatch tests: every path goes through cli.main().

These exist because command modules can be perfectly correct while the
dispatch layer is broken (a missing import once made every error path
crash with a NameError). Exercise main() itself: routing, --json at
every nesting level, and clean error exit codes.
"""
import contextlib
import io
import json
import tempfile
from pathlib import Path

from agentboom.cli import build_parser, main

from helpers import AgentTestCase


def _run(argv):
    """Run cli.main(argv), capturing stdout/stderr. Returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class DispatchTests(AgentTestCase):
    def test_validate_dispatch_ok(self):
        rc, out, _ = _run(["validate", str(self.agent_dir)])
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out)

    def test_error_path_is_clean_message_and_exit_1(self):
        with tempfile.TemporaryDirectory() as plain:
            rc, out, err = _run(["upgrade", plain])
        self.assertEqual(rc, 1)
        self.assertIn("agentboom:", err)
        self.assertIn(".agentboom.json", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")

    def test_error_path_json(self):
        with tempfile.TemporaryDirectory() as plain:
            rc, out, _ = _run(["upgrade", plain, "--json"])
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn(".agentboom.json", payload["error"])

    def test_code_miniapp_dry_run_via_dispatch(self):
        rc, out, err = _run(["code", "miniapp", "cli-demo", "do things",
                             "--dir", str(self.agent_dir), "--dry-run", "--json"])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["scaffolded"])
        self.assertIn("cli-demo", payload["mission"])
        self.assertTrue((self.agent_dir / "platform/miniapps/cli-demo/main.py").is_file())

    def test_code_skill_dry_run_via_dispatch(self):
        rc, out, err = _run(["code", "skill", "cli-skill", "",
                             "--dir", str(self.agent_dir), "--dry-run", "--json"])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "skill")

    def test_json_flag_works_after_nested_subcommand(self):
        # Regression: --json used to be rejected after `add skill <name>`
        # because nested subparsers did not inherit the common flags.
        rc, out, err = _run(["add", "skill", "nested-json-skill",
                             "--dir", str(self.agent_dir), "--json"])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["name"], "nested-json-skill")

    def test_unknown_package_is_clean_error(self):
        rc, out, err = _run(["add", "package", "does-not-exist",
                             "--dir", str(self.agent_dir)])
        self.assertEqual(rc, 1)
        self.assertIn("Unknown package", err)
        self.assertNotIn("Traceback", err)

    def test_fleet_status_json_via_dispatch(self):
        rc, out, err = _run(["fleet", "status", "--json"])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertIsInstance(payload["agents"], list)


class ParserTests(AgentTestCase):
    def test_no_args_is_usage_error(self):
        rc, out, _ = _run([])
        self.assertEqual(rc, 2)
        self.assertIn("examples:", out)

    def test_version_flag(self):
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(io.StringIO()):
                build_parser().parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)
