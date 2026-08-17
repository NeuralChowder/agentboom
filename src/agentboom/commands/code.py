"""`agentboom code` — let a Qwen Code agent build content inside an agent.

`agentboom add miniapp` only scaffolds; this command hands the scaffold to
the agent itself: it drops you into an interactive qwen session, inside
the target agent's repo, with a mission prompt that already knows the
mini-app contract. Smooth by design: scaffold -> agent builds -> you
iterate in the same session.
"""
import os
import shutil
import subprocess
from pathlib import Path

from . import add as add_cmd


class CodeError(RuntimeError):
    pass


def _agent_dir(args) -> Path:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    if not (agent_dir / ".agentboom.json").is_file() and not (
        agent_dir / ".qwen-docker"
    ).is_dir():
        raise CodeError(
            f"{agent_dir} is not an agentboom agent — run this inside an "
            "agent repo (or `agentboom init` one first)."
        )
    return agent_dir


def run_miniapp(args) -> dict:
    agent_dir = _agent_dir(args)
    name = args.name

    # Scaffold if missing (add miniapp refuses duplicates; that's fine here).
    try:
        add_cmd.run_miniapp(
            type("A", (), {"name": name, "description": args.description or "",
                           "dir": str(agent_dir)})()
        )
        scaffolded = True
    except add_cmd.AddError:
        scaffolded = False

    mission = (
        f"You are working inside the agentboom agent repo at {agent_dir}.\n"
        f"Mission: implement the mini-app '{name}' in platform/miniapps/{name}/ "
        f"(the scaffold {'was just created' if scaffolded else 'already exists'}).\n"
        f"User intent: {args.prompt or '(none given — inspect the scaffold and propose a purpose)'}\n\n"
        "Contract (read platform/README.md and the miniapp-dev skill for details):\n"
        "- main.py exports get_router(); endpoints live under /api/" + name + "/ once hot-reloaded.\n"
        "- .miniapp.json declares name/description; scheduled work goes in manifest `jobs`.\n"
        "- Import shared machinery only from agentboom_sdk; deterministic first.\n"
        "- Verify before finishing: python -m py_compile on every file you touch, "
        "and check the gateway catalog loads the app if the platform is running.\n"
        "Implement it now, then summarize what you built and how to use it."
    )

    qwen = shutil.which("qwen")
    if not qwen:
        raise CodeError(
            "qwen (Qwen Code) not found on PATH. Install it with "
            "`npm install -g @qwen-code/qwen-code`, then re-run."
        )

    result = {
        "ok": True,
        "kind": "miniapp",
        "name": name,
        "agent_dir": str(agent_dir),
        "scaffolded": scaffolded,
        "launched": False,
    }
    if getattr(args, "dry_run", False):
        result["mission"] = mission
        result["note"] = "dry-run: mission prompt prepared, qwen not launched"
        return result

    os.chdir(agent_dir)
    os.execvp(qwen, [qwen, "--prompt-interactive", mission])
    return result  # unreachable; exec replaces the process


def run_skill(args) -> dict:
    agent_dir = _agent_dir(args)
    name = args.name
    try:
        add_cmd.run_skill(
            type("A", (), {"name": name, "description": args.description or "",
                           "dir": str(agent_dir)})()
        )
        scaffolded = True
    except add_cmd.AddError:
        scaffolded = False

    mission = (
        f"You are working inside the agentboom agent repo at {agent_dir}.\n"
        f"Mission: write the skill '{name}' at .qwen-docker/skills/{name}/ "
        f"(scaffold {'just created' if scaffolded else 'already exists'}).\n"
        f"User intent: {args.prompt or '(none given — propose a purpose)'}\n\n"
        "Contract (read the skill-creator skill): SKILL.md frontmatter with name + "
        "description (the description is WHEN to use it), body = When to use / "
        "Procedure / Notes; deterministic scripts over model probing. "
        "Validate with the skill-creator validator before finishing."
    )

    qwen = shutil.which("qwen")
    if not qwen:
        raise CodeError(
            "qwen (Qwen Code) not found on PATH. Install it with "
            "`npm install -g @qwen-code/qwen-code`, then re-run."
        )

    result = {"ok": True, "kind": "skill", "name": name,
              "agent_dir": str(agent_dir), "scaffolded": scaffolded,
              "launched": False}
    if getattr(args, "dry_run", False):
        result["mission"] = mission
        result["note"] = "dry-run: mission prompt prepared, qwen not launched"
        return result

    os.chdir(agent_dir)
    os.execvp(qwen, [qwen, "--prompt-interactive", mission])
    return result
