"""Registry tests: package sources, discovery, dependency enforcement."""
import argparse
import json
import os
import pathlib
import shutil
import tempfile
import unittest

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="agentboom-registry-tests-"))
os.environ["AGENTBOOM_HOME"] = str(_TMP / "home")

from agentboom import registries as registries_mod  # noqa: E402
from agentboom.commands import packages as packages_cmd  # noqa: E402
from agentboom.commands.packages import PackageError  # noqa: E402

from helpers import AgentTestCase  # noqa: E402


def _make_pkg(root: pathlib.Path, name: str, meta_extra: dict = None) -> None:
    pkg = root / name
    pkg.mkdir(parents=True)
    meta = {"name": name, "description": f"test package {name}"}
    if meta_extra:
        meta.update(meta_extra)
    (pkg / ".agentboom-package.json").write_text(json.dumps(meta))
    (pkg / "README.md").write_text(f"# {name}\n")


class RegistryConfigTests(unittest.TestCase):
    def setUp(self):
        registries_mod.registries_path().unlink(missing_ok=True)

    def test_builtin_always_listed_first(self):
        regs = registries_mod.list_registries()
        self.assertEqual(regs[0]["name"], "builtin")

    def test_add_remove_roundtrip(self):
        src = _TMP / "src-repo"
        _make_pkg(src / "packages", "one")
        entry = registries_mod.add_registry("extra", str(src))
        self.assertEqual(entry["subdir"], "packages")
        names = [r["name"] for r in registries_mod.list_registries()]
        self.assertIn("extra", names)
        self.assertTrue(registries_mod.remove_registry("extra"))
        self.assertFalse(registries_mod.remove_registry("extra"))

    def test_builtin_name_is_reserved(self):
        with self.assertRaises(registries_mod.RegistryError):
            registries_mod.add_registry("builtin", "/tmp")

    def test_discover_merges_builtin_and_path_registry(self):
        src = _TMP / "src-merge"
        _make_pkg(src / "packages", "zz-external", {"kind": "connector",
                                                     "icon": "🔌"})
        registries_mod.add_registry("merge", str(src))
        found = {p["name"]: p for p in registries_mod.discover_packages()}
        self.assertIn("vault", found)          # builtin
        self.assertIn("zz-external", found)    # extra registry
        self.assertEqual(found["zz-external"]["source"], "merge")
        self.assertEqual(found["zz-external"]["kind"], "connector")
        registries_mod.remove_registry("merge")

    def test_builtin_wins_name_collisions(self):
        src = _TMP / "src-collide"
        _make_pkg(src / "packages", "vault", {"description": "impostor"})
        registries_mod.add_registry("collide", str(src))
        found = {p["name"]: p for p in registries_mod.discover_packages()}
        self.assertEqual(found["vault"]["source"], "builtin")
        registries_mod.remove_registry("collide")


class DependencyTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        registries_mod.registries_path().unlink(missing_ok=True)
        self.src = pathlib.Path(tempfile.mkdtemp(dir=_TMP))
        _make_pkg(self.src / "packages", "needs-vault", {"requires": ["vault"]})
        _make_pkg(self.src / "packages", "needs-two",
                  {"requires": ["vault", "telegram"]})
        registries_mod.add_registry("deptest", str(self.src))

    def tearDown(self):
        registries_mod.remove_registry("deptest")
        super().tearDown()

    def _add(self, name):
        return packages_cmd.run_add_package(argparse.Namespace(
            name=name, dir=str(self.agent_dir), refresh=False))

    def test_missing_dependency_is_refused_with_instructions(self):
        with self.assertRaises(PackageError) as ctx:
            self._add("needs-vault")
        self.assertIn("requires: vault", str(ctx.exception))
        self.assertIn("agentboom add package vault", str(ctx.exception))

    def test_only_still_missing_dependencies_are_listed(self):
        self._add("vault")  # one of the two is now satisfied
        with self.assertRaises(PackageError) as ctx:
            self._add("needs-two")
        self.assertIn("requires: telegram", str(ctx.exception))

    def test_installs_once_dependencies_are_met(self):
        self._add("vault")
        result = self._add("needs-vault")
        self.assertTrue(result["ok"])
        record = json.loads(
            (self.agent_dir / ".agentboom.json").read_text())["packages"]["needs-vault"]
        self.assertEqual(record["requires"], ["vault"])
        self.assertEqual(record["source"], "deptest")


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
