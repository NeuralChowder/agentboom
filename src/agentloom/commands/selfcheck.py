"""`agentloom selfcheck` — end-to-end QA of the templates.

Inits a throwaway agent in a temp dir, validates it, runs an upgrade check
(must be clean on a fresh agent), scaffolds a skill and a mini-app, and
validates again. Used by the test suite and safe for agents/CI to run.
"""
import argparse
import shutil
import tempfile
from pathlib import Path

from . import add as add_cmd
from . import init as init_cmd
from . import upgrade as upgrade_cmd
from . import validate as validate_cmd


def run(args) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="agentloom-selfcheck-"))
    agent_dir = tmp / "selfcheck-agent"
    steps = []
    ok = True

    def step(name, fn, expect_ok=True):
        nonlocal ok
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — report any failure
            steps.append({"step": name, "ok": False, "error": str(exc)})
            ok = False
            return None
        passed = bool(result.get("ok", False)) == expect_ok
        steps.append({"step": name, "ok": passed, "detail": _summary(name, result)})
        if not passed:
            ok = False
        return result

    try:
        step("init", lambda: init_cmd.run(argparse.Namespace(
            dir=str(agent_dir), name="selfcheck-agent",
            description="selfcheck", port_agent=4170, port_platform=8000,
            force=False,
        )))
        step("validate", lambda: validate_cmd.run(argparse.Namespace(dir=str(agent_dir))))
        step("upgrade-check", lambda: upgrade_cmd.run(argparse.Namespace(
            dir=str(agent_dir), apply=False, force=False,
        )), expect_ok=True)
        upgrade_result = step("upgrade-check-clean", lambda: upgrade_cmd.run(argparse.Namespace(
            dir=str(agent_dir), apply=False, force=False,
        )))
        if upgrade_result and upgrade_result.get("changed"):
            steps.append({"step": "upgrade-check-clean", "ok": False,
                          "detail": "fresh agent already drifts from base"})
            ok = False
        step("add-skill", lambda: add_cmd.run_skill(argparse.Namespace(
            name="selfcheck-skill", description="selfcheck skill", dir=str(agent_dir),
        )))
        step("add-miniapp", lambda: add_cmd.run_miniapp(argparse.Namespace(
            name="selfcheck-app", description="selfcheck app", dir=str(agent_dir),
        )))
        step("validate-after-adds", lambda: validate_cmd.run(argparse.Namespace(dir=str(agent_dir))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"ok": ok, "agent_dir": str(agent_dir), "steps": steps}


def _summary(name, result: dict) -> str:
    if name.startswith("validate"):
        return f"errors={result.get('errors')} warnings={result.get('warnings')}"
    if name.startswith("upgrade"):
        return f"changed={result.get('changed')}"
    if name == "init":
        return f"created={len(result.get('created', []))} managed={result.get('managed_count')}"
    return "ok" if result.get("ok") else "failed"
