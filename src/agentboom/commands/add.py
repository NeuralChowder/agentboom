"""`agentboom add skill|miniapp` — scaffold new capabilities inside an agent."""
import json
import re
from pathlib import Path

from agentboom.registry import load_registry

NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

SKILL_MD = """---
name: {name}
description: {description}
---

# {title}

## When to use

Describe the trigger: what user request or situation should invoke this skill.

## Procedure

1. First deterministic step (prefer scripts over model guesswork).
2. Second step.
3. How to present the result.

## Notes

- Constraints, safety rules, and edge cases.
"""

MINIAPP_MAIN = '''"""{name} mini-app."""
import logging

from fastapi import APIRouter

log = logging.getLogger("miniapps.{safe}")

router = APIRouter()


@router.get("/health")
async def health():
    return {{"status": "ok", "app": "{name}"}}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
'''


class AddError(RuntimeError):
    pass


def _validate_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise AddError(
            f"Invalid name '{name}' — kebab-case required (lowercase, digits, hyphens)."
        )
    return name


def _agent_dir(args) -> Path:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    if not (agent_dir / ".agentboom.json").is_file() and not (
        agent_dir / ".qwen-docker"
    ).is_dir():
        raise AddError(
            f"{agent_dir} does not look like an agent project "
            "(no .agentboom.json or .qwen-docker). Run from inside an agent "
            "or pass --dir."
        )
    return agent_dir


def run_skill(args) -> dict:
    name = _validate_name(args.name)
    agent_dir = _agent_dir(args)
    skill_dir = agent_dir / ".qwen-docker" / "skills" / name
    if skill_dir.exists():
        raise AddError(f"Skill already exists: {skill_dir}")

    description = (args.description or f"TODO: one-line description of {name}").strip()
    title = " ".join(p.capitalize() for p in name.split("-"))

    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        SKILL_MD.format(name=name, description=description, title=title),
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / ".gitkeep").touch()
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / ".gitkeep").touch()

    return {
        "ok": True,
        "kind": "skill",
        "name": name,
        "path": str(skill_dir),
        "created": [
            f"{skill_dir / 'SKILL.md'}",
            f"{skill_dir / 'references'}",
            f"{skill_dir / 'scripts'}",
        ],
        "next": "Edit SKILL.md — the description is what the model sees when choosing skills.",
    }


def run_miniapp(args) -> dict:
    name = _validate_name(args.name)
    agent_dir = _agent_dir(args)
    app_dir = agent_dir / "platform" / "miniapps" / name
    if app_dir.exists():
        raise AddError(f"Mini-app already exists: {app_dir}")

    description = (args.description or f"TODO: one-line description of {name}").strip()
    safe = name.replace("-", "_")

    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text(
        MINIAPP_MAIN.format(name=name, safe=safe), encoding="utf-8"
    )
    manifest = {
        "name": name,
        "description": description,
        "version": "0.1.0",
        "status": "active",
        "jobs": [],
        "subscribes": [],
        "ui": None,
    }
    (app_dir / ".miniapp.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "ok": True,
        "kind": "miniapp",
        "name": name,
        "path": str(app_dir),
        "created": [str(app_dir / "main.py"), str(app_dir / ".miniapp.json")],
        "next": (
            "The gateway hot-loads it within ~2s. Verify: "
            "curl -s localhost:<platform-port>/api/catalog | python3 -m json.tool"
        ),
    }
