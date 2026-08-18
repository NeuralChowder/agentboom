"""Tests for `agentboom self-update` (version compare, asset pick, installer)."""
import argparse
import unittest
from unittest import mock

from agentboom import __version__
from agentboom.commands import selfupdate as su


class VersionTupleTests(unittest.TestCase):
    def test_ordering(self):
        self.assertGreater(su._version_tuple("0.8.0"), su._version_tuple("0.7.0"))
        self.assertGreater(su._version_tuple("1.0.0"), su._version_tuple("0.9.9"))
        self.assertGreater(su._version_tuple("0.7.1"), su._version_tuple("0.7.0"))
        self.assertEqual(su._version_tuple("v0.7.0"), su._version_tuple("0.7.0"))

    def test_pads_short_versions(self):
        self.assertEqual(su._version_tuple("0.7"), (0, 7, 0))
        self.assertEqual(su._version_tuple("1"), (1, 0, 0))


class WheelAssetTests(unittest.TestCase):
    def test_picks_matching_wheel(self):
        release = {"tag_name": "v0.8.0", "assets": [
            {"name": "agentboom_sdk-0.8.0-py3-none-any.whl",
             "browser_download_url": "https://x/sdk.whl"},
            {"name": "agentboom-0.8.0-py3-none-any.whl",
             "browser_download_url": "https://x/cli.whl"},
        ]}
        self.assertEqual(su._wheel_asset(release), "https://x/cli.whl")

    def test_none_when_missing(self):
        self.assertIsNone(su._wheel_asset({"tag_name": "v1", "assets": []}))


class InstallerCommandTests(unittest.TestCase):
    def test_pip_user_when_not_venv_not_root(self):
        with mock.patch.object(su, "_installed_via_pipx", return_value=False), \
             mock.patch.object(su, "_in_venv", return_value=False), \
             mock.patch.object(su, "_is_root", return_value=False):
            cmd = su.installer_command("https://x/cli.whl")
        self.assertIn("-m", cmd)
        self.assertIn("pip", cmd)
        self.assertIn("--user", cmd)
        self.assertIn("agentboom @ https://x/cli.whl", cmd)

    def test_pipx_when_pipx_install(self):
        with mock.patch.object(su, "_installed_via_pipx", return_value=True):
            cmd = su.installer_command("https://x/cli.whl")
        self.assertEqual(cmd[0], "pipx")
        self.assertIn("--force", cmd)


class RunTests(unittest.TestCase):
    def _release(self, tag):
        return {"tag_name": tag, "assets": [
            {"name": f"agentboom-{tag.lstrip('v')}-py3-none-any.whl",
             "browser_download_url": f"https://x/agentboom-{tag.lstrip('v')}.whl"}]}

    def test_up_to_date(self):
        with mock.patch.object(su, "_latest_release",
                               return_value=self._release("v" + __version__)):
            result = su.run(argparse.Namespace(apply=False))
        self.assertTrue(result["ok"])
        self.assertFalse(result["update_available"])

    def test_update_available_dry_run_does_not_install(self):
        with mock.patch.object(su, "_latest_release",
                               return_value=self._release("v99.0.0")), \
             mock.patch.object(su, "subprocess") as sp:
            result = su.run(argparse.Namespace(apply=False))
        self.assertTrue(result["update_available"])
        self.assertIn("command", result)
        sp.run.assert_not_called()  # dry-run never invokes an installer

    def test_apply_runs_installer(self):
        fake = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(su, "_latest_release",
                               return_value=self._release("v99.0.0")), \
             mock.patch.object(su, "subprocess") as sp:
            sp.run.return_value = fake
            result = su.run(argparse.Namespace(apply=True))
        sp.run.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertTrue(result["applied"])


if __name__ == "__main__":
    unittest.main()
