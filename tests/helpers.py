"""Shared test helpers: init a throwaway agent in a temp dir."""
import argparse
import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Isolate the fleet registry before anything imports it — init registers
# agents best-effort, and tests must never touch the user's real fleet.
os.environ.setdefault("AGENTBOOM_HOME", tempfile.mkdtemp(prefix="agentboom-home-"))

from agentboom.commands import init as init_cmd  # noqa: E402


class AgentTestCase(unittest.TestCase):
    """Base class providing a freshly initialized agent in self.agent_dir."""

    name = "test-agent"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agentboom-test-"))
        self.agent_dir = self.tmp / self.name
        self.init_result = init_cmd.run(argparse.Namespace(
            dir=str(self.agent_dir),
            name=self.name,
            description="unit test agent",
            port_agent=4170,
            port_platform=8000,
            force=False,
        ))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
