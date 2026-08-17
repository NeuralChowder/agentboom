"""`agentboom list|skills|miniapps` — discovery commands (JSON-friendly)."""
import json
from pathlib import Path

from agentboom.checks import parse_frontmatter
from agentboom.registry import load_registry


def run_list(args) -> dict:
    """Discover agentboom agents one level below a parent directory."""
    parent = Path(args.dir or ".").expanduser().resolve()
    if not parent.is_dir():
        return {"ok": False, "error": f"Not a directory: {parent}", "agents": []}
    agents = []
    for child in sorted(parent.iterdir()):
        if not child.is_dir():
            continue
        registry = load_registry(child)
        if registry is None:
            continue
        agents.append({
            "name": registry.get("name", child.name),
            "path": str(child),
            "template": registry.get("template"),
            "base_version": registry.get("base_version"),
            "created_at": registry.get("created_at"),
        })
    return {"ok": True, "parent": str(parent), "agents": agents}


def run_skills(args) -> dict:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    skills_dir = agent_dir / ".qwen-docker" / "skills"
    registry = load_registry(agent_dir)
    managed = set((registry or {}).get("managed", {}))
    skills = []
    if skills_dir.is_dir():
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            fm = parse_frontmatter(skill_md.read_text(encoding="utf-8")) if skill_md.is_file() else None
            rel_prefix = skill_dir.relative_to(agent_dir).as_posix() + "/"
            skills.append({
                "name": (fm or {}).get("name", skill_dir.name),
                "path": str(skill_dir),
                "description": (fm or {}).get("description", ""),
                "managed": any(m.startswith(rel_prefix) for m in managed),
                "files": sum(1 for _ in skill_dir.rglob("*") if _.is_file()),
            })
    return {"ok": True, "agent_dir": str(agent_dir), "skills": skills}


def run_miniapps(args) -> dict:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    apps = []
    for root_name in ("platform/miniapps", "platform/public-apps"):
        root = agent_dir / root_name
        if not root.is_dir():
            continue
        for app_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            manifest_path = app_dir / ".miniapp.json"
            manifest = {}
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    manifest = {"_error": "invalid .miniapp.json"}
            apps.append({
                "name": manifest.get("name", app_dir.name),
                "path": str(app_dir),
                "public": root_name.endswith("public-apps"),
                "description": manifest.get("description", ""),
                "version": manifest.get("version", ""),
                "status": manifest.get("status", ""),
                "jobs": len(manifest.get("jobs", [])),
                "has_main": (app_dir / "main.py").is_file(),
            })
    return {"ok": True, "agent_dir": str(agent_dir), "miniapps": apps}
