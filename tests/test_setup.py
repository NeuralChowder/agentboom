"""Tests for `agentboom setup` and `agentboom init --generate-env`.

Covers the pure helpers (env parsing/filling, settings building) and the
file-writing behaviour: token generation, idempotency (tokens are never
regenerated), non-clobbering of an existing working setup, and that no
secret value ever leaks into the returned/JSON payload.
"""
import argparse
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from agentboom.commands import init as init_cmd
from agentboom.commands import setup as setup_cmd
from agentboom.commands.setup import (
    ENV_LLM_URL,
    ENV_LLM_MODEL,
    QWEN_SETTINGS,
    build_settings_dict,
    fill_env_text,
    generate_env,
    parse_env,
)

from helpers import AgentTestCase

_HEX32 = re.compile(r"^[0-9a-f]{64}$")

# A minimal rendered .env.example used for the pure fill_env_text tests.
_EXAMPLE_ENV = (
    "QWEN_SERVER_TOKEN=\n"
    "PLATFORM_ADMIN_PASSWORD=\n"
    "PLATFORM_TOKEN=\n"
    "LLM_BASE_URL=\n"
    "LLM_API_KEY=\n"
    "LLM_MODEL=\n"
    "PORT_AGENT=4170\n"
    "PORT_PLATFORM=8000\n"
)

# A minimal settings.example.json structure for build_settings_dict tests.
_EXAMPLE_SETTINGS = {
    "$comment": "copy me",
    "tools": {"approvalMode": "yolo"},
    "permissions": {"allow": ["*"]},
    "modelProviders": {"openai": [{
        "id": "generic", "name": "generic",
        "baseUrl": "http://example:1/v1",
        "envKey": "OLD_KEY",
        "generationConfig": {"contextWindowSize": 128000},
    }]},
    "model": {"name": "generic"},
}


class ParseEnvTests(unittest.TestCase):
    def test_parses_set_keys(self):
        self.assertEqual(parse_env("A=1\nB=x\n"), {"A": "1", "B": "x"})

    def test_ignores_comments_and_empty(self):
        self.assertEqual(parse_env("# c\n\nA=1\n"), {"A": "1"})

    def test_ignores_unset_and_bad_keys(self):
        self.assertEqual(parse_env("A=\nlower=1\nNOPE"), {})


class FillEnvTextTests(unittest.TestCase):
    def _run(self, existing, llm):
        return fill_env_text(_EXAMPLE_ENV, existing, llm)

    def test_fills_secrets_and_llm_when_empty(self):
        new, filled = self._run({}, {"base_url": "u", "api_key": "k", "model": "m"})
        parsed = parse_env(new)
        self.assertEqual(set(filled),
                         {"QWEN_SERVER_TOKEN", "PLATFORM_ADMIN_PASSWORD",
                          "PLATFORM_TOKEN", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"})
        self.assertEqual(parsed["LLM_BASE_URL"], "u")
        self.assertEqual(parsed["LLM_MODEL"], "m")
        self.assertEqual(parsed["PORT_AGENT"], "4170")  # untouched
        self.assertTrue(_HEX32.match(parsed["QWEN_SERVER_TOKEN"]))

    def test_secrets_are_valid_lengths(self):
        new, _ = self._run({}, {})
        parsed = parse_env(new)
        self.assertTrue(_HEX32.match(parsed["QWEN_SERVER_TOKEN"]))
        self.assertTrue(_HEX32.match(parsed["PLATFORM_TOKEN"]))
        self.assertTrue(parsed["PLATFORM_ADMIN_PASSWORD"])
        self.assertNotEqual(parsed["QWEN_SERVER_TOKEN"], parsed["PLATFORM_TOKEN"])

    def test_carries_over_existing_secret_without_regenerating(self):
        token = "a" * 64
        existing = {"QWEN_SERVER_TOKEN": token}
        new, filled = self._run(existing, {})
        parsed = parse_env(new)
        self.assertEqual(parsed["QWEN_SERVER_TOKEN"], token)
        self.assertNotIn("QWEN_SERVER_TOKEN", filled)  # not a *new* change

    def test_llm_fresh_answer_wins(self):
        existing = {"LLM_MODEL": "old"}
        new, filled = self._run(existing, {"model": "new"})
        self.assertEqual(parse_env(new)["LLM_MODEL"], "new")
        self.assertIn("LLM_MODEL", filled)

    def test_llm_preserved_when_not_provided(self):
        existing = {"LLM_MODEL": "old", "LLM_BASE_URL": "http://keep:1/v1"}
        new, filled = self._run(existing, {})
        parsed = parse_env(new)
        self.assertEqual(parsed["LLM_MODEL"], "old")
        self.assertEqual(parsed["LLM_BASE_URL"], "http://keep:1/v1")
        self.assertNotIn("LLM_MODEL", filled)
        self.assertNotIn("LLM_BASE_URL", filled)

    def test_ports_and_unknown_keys_untouched(self):
        new, filled = self._run({}, {})
        self.assertNotIn("PORT_AGENT", filled)
        self.assertIn("PORT_AGENT=4170", new)


class BuildSettingsTests(unittest.TestCase):
    def test_wires_provider_model_and_env_key(self):
        s = build_settings_dict(
            _EXAMPLE_SETTINGS,
            {"base_url": "http://u:4000/v1", "api_key": "real", "model": "mytag"},
        )
        prov = s["modelProviders"]["openai"][0]
        self.assertEqual(prov["id"], "mytag")
        self.assertEqual(prov["baseUrl"], "http://u:4000/v1")
        self.assertEqual(prov["envKey"], "AGENT_LLM_API_KEY")
        self.assertEqual(s["model"]["name"], "mytag")
        self.assertEqual(s["env"], {"AGENT_LLM_API_KEY": "real"})
        self.assertNotIn("$comment", s)
        # structure preserved from the example
        self.assertEqual(s["tools"], {"approvalMode": "yolo"})
        self.assertEqual(prov["generationConfig"]["contextWindowSize"], 128000)

    def test_placeholder_when_nothing_known(self):
        s = build_settings_dict(_EXAMPLE_SETTINGS, {})
        self.assertEqual(s["model"]["name"], "generic")
        self.assertEqual(s["model"]["baseUrl"], "http://YOUR_LLM_SERVER:4000/v1")
        self.assertEqual(s["env"], {"AGENT_LLM_API_KEY": "not-needed"})

    def test_preserves_existing_when_caller_gives_none(self):
        s = build_settings_dict(
            _EXAMPLE_SETTINGS, {},
            existing_model={"name": "keepm", "baseUrl": "http://keep:4000/v1"},
            existing_env={"AGENT_LLM_API_KEY": "keepkey"},
        )
        self.assertEqual(s["model"]["name"], "keepm")
        self.assertEqual(s["model"]["baseUrl"], "http://keep:4000/v1")
        self.assertEqual(s["env"], {"AGENT_LLM_API_KEY": "keepkey"})


class SetupIOTests(AgentTestCase):
    def test_generate_env_creates_files(self):
        result = generate_env(self.agent_dir, {"base_url": "u", "model": "m",
                                               "api_key": "k"})
        env = self.agent_dir / ".env"
        settings = self.agent_dir / QWEN_SETTINGS
        self.assertTrue(env.is_file())
        self.assertTrue(settings.is_file())
        parsed = parse_env(env.read_text())
        self.assertTrue(_HEX32.match(parsed["QWEN_SERVER_TOKEN"]))
        self.assertEqual(parsed["LLM_MODEL"], "m")
        self.assertTrue(result["settings_written"])
        self.assertEqual(result["llm"]["model"], "m")  # read back, not echoed

    def test_env_and_settings_are_not_world_readable(self):
        generate_env(self.agent_dir, {})
        for rel in (".env", QWEN_SETTINGS):
            mode = os.stat(self.agent_dir / rel).st_mode & 0o077
            self.assertEqual(mode, 0, f"{rel} must not be group/other readable")

    def test_generate_env_is_idempotent_for_tokens(self):
        generate_env(self.agent_dir, {"model": "m"})
        first = parse_env((self.agent_dir / ".env").read_text())["QWEN_SERVER_TOKEN"]
        generate_env(self.agent_dir, {})  # re-run, no new values
        second = parse_env((self.agent_dir / ".env").read_text())["QWEN_SERVER_TOKEN"]
        self.assertEqual(first, second)

    def _run_setup(self, **overrides):
        defaults = dict(dir=str(self.agent_dir), non_interactive=True, yes=False,
                        llm_url=None, llm_key=None, llm_model=None, timezone=None)
        defaults.update(overrides)
        return setup_cmd.run(argparse.Namespace(**defaults))

    def test_setup_non_interactive_from_env(self):
        with mock.patch.dict(os.environ,
                             {ENV_LLM_URL: "http://u:4000/v1", ENV_LLM_MODEL: "fast"}):
            result = self._run_setup()
        self.assertTrue(result["ok"])
        settings = json.loads((self.agent_dir / QWEN_SETTINGS).read_text())
        self.assertEqual(settings["model"]["name"], "fast")
        self.assertEqual(settings["model"]["baseUrl"], "http://u:4000/v1")

    def test_setup_updates_model_but_keeps_token(self):
        with mock.patch.dict(os.environ,
                             {ENV_LLM_URL: "http://u:4000/v1", ENV_LLM_MODEL: "one"}):
            self._run_setup()
        token = parse_env((self.agent_dir / ".env").read_text())["QWEN_SERVER_TOKEN"]
        with mock.patch.dict(os.environ, {ENV_LLM_MODEL: "two"}):
            result = self._run_setup()
        self.assertEqual(result["llm"]["model"], "two")
        env_after = parse_env((self.agent_dir / ".env").read_text())
        self.assertEqual(env_after["QWEN_SERVER_TOKEN"], token)  # preserved
        self.assertEqual(env_after["LLM_MODEL"], "two")  # updated
        self.assertIn("LLM_MODEL", result["env_keys_set"])
        self.assertNotIn("QWEN_SERVER_TOKEN", result["env_keys_set"])

    def test_setup_refuses_non_agent_dir(self):
        tmp = Path(self.tmp / "not-an-agent")
        tmp.mkdir()
        with self.assertRaises(setup_cmd.SetupError):
            self._run_setup(dir=str(tmp))

    def test_result_contains_no_secret_values(self):
        with mock.patch.dict(os.environ,
                             {ENV_LLM_URL: "http://u:4000/v1",
                              ENV_LLM_MODEL: "m",
                              "AGENT_LLM_API_KEY": "super-secret-api-key"}):
            result = self._run_setup()
        dump = json.dumps(result)
        token = parse_env((self.agent_dir / ".env").read_text())["QWEN_SERVER_TOKEN"]
        self.assertNotIn(token, dump)
        self.assertNotIn("super-secret-api-key", dump)
        # The API key IS legitimately written to settings.json (the agent
        # reads it at runtime) — that file is gitignored, not the payload.
        settings = json.loads((self.agent_dir / QWEN_SETTINGS).read_text())
        self.assertEqual(settings["env"]["AGENT_LLM_API_KEY"], "super-secret-api-key")


class InitGenerateEnvTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = Path(tempfile.mkdtemp(prefix="agentboom-initgen-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_init_generate_env_creates_env_and_settings(self):
        target = self._tmp / "gen"
        result = init_cmd.run(argparse.Namespace(
            dir=str(target), name="gen", description="",
            port_agent=4170, port_platform=8000, force=False,
            generate_env=True,
            llm_url="http://u:4000/v1", llm_key="not-needed", llm_model="m",
        ))
        self.assertTrue(result["env_generated"])
        self.assertTrue(result["settings_generated"])
        parsed = parse_env((target / ".env").read_text())
        self.assertTrue(_HEX32.match(parsed["QWEN_SERVER_TOKEN"]))
        self.assertEqual(parsed["LLM_MODEL"], "m")
        settings = json.loads((target / ".qwen-docker" / "settings.json").read_text())
        self.assertEqual(settings["model"]["name"], "m")
        # the manual "cp .env.example" step is gone from next_steps
        self.assertFalse(any(".env.example" in s for s in result["next_steps"]))

    def test_init_without_generate_env_leaves_example_only(self):
        target = self._tmp / "plain"
        result = init_cmd.run(argparse.Namespace(
            dir=str(target), name="plain", description="",
            port_agent=4170, port_platform=8000, force=False,
            generate_env=False,
        ))
        self.assertFalse(result["env_generated"])
        self.assertFalse((target / ".env").is_file())
        self.assertTrue((target / ".env.example").is_file())
        self.assertTrue(any(".env.example" in s for s in result["next_steps"]))


if __name__ == "__main__":
    unittest.main()
