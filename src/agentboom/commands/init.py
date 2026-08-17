"""`agentboom init` — scaffold a new agent project from the base template."""
import re
from pathlib import Path

from agentboom import __version__
from agentboom.registry import (
    TEMPLATE_META_NAME,
    load_template_meta,
    matches_any,
    now_iso,
    save_registry,
    sha256_file,
)
from agentboom.render import render_file
from agentboom import fleet as fleet_reg

from . import DEFAULT_TEMPLATE, skills_base_root, template_dir

NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

DEFAULT_DESCRIPTION = "a general-purpose autonomous agent"

# The agent home lives in a dot-directory in generated agents, but the
# template stores it undotted: packaging globs skip dot-directories.
QWEN_HOME_SRC = "qwen-home"
QWEN_HOME_DEST = ".qwen-docker"


class InitError(RuntimeError):
    pass


def _kebab_to_title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-") if part)


def run(args) -> dict:
    target = Path(args.dir).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()

    name = (args.name or target.name).strip().lower()
    if not NAME_RE.match(name):
        raise InitError(
            f"Invalid agent name '{name}' — use kebab-case: lowercase letters, "
            "digits and hyphens, starting with a letter (e.g. my-agent)."
        )

    if target.exists() and any(target.iterdir()) and not args.force:
        raise InitError(
            f"Target directory {target} exists and is not empty. "
            "Use an empty directory, or pass --force to fill it without "
            "overwriting existing files."
        )
    if target.exists() and not target.is_dir():
        raise InitError(f"Target {target} exists and is not a directory.")

    variables = {
        "AGENT_NAME": name,
        "AGENT_TITLE": _kebab_to_title(name),
        "AGENT_DESCRIPTION": (args.description or DEFAULT_DESCRIPTION).strip(),
        "PORT_AGENT": str(args.port_agent),
        "PORT_PLATFORM": str(args.port_platform),
        "BASE_VERSION": __version__,
    }

    source = template_dir(DEFAULT_TEMPLATE)
    meta = load_template_meta(source)
    managed_patterns = meta.get("managed", [])

    target.mkdir(parents=True, exist_ok=True)

    created, skipped = [], []

    def _place(src: Path, rel: str) -> None:
        dst = target / rel
        if dst.exists():
            skipped.append(rel)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        render_file(src, dst, variables)
        created.append(rel)

    # 1. The platform template tree. The agent home is stored undotted in
    # the template ('qwen-home') because packaging globs skip dot-dirs,
    # and lands as '.qwen-docker' in generated agents.
    for src in sorted(source.rglob("*")):
        if not src.is_file() or src.name == TEMPLATE_META_NAME:
            continue
        rel = src.relative_to(source).as_posix()
        parts = rel.split("/", 1)
        if parts[0] == QWEN_HOME_SRC:
            rel = QWEN_HOME_DEST + ("/" + parts[1] if len(parts) > 1 else "")
        _place(src, rel)

    # 2. Base skills land in the agent home's skills/ directory.
    skills_dest_prefix = ".qwen-docker/skills"
    for src in sorted(skills_base_root().rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(skills_base_root()).as_posix()
        _place(src, f"{skills_dest_prefix}/{rel}")

    # 3. Executables: everything listed in the meta, plus any shell script
    # under a scripts/ directory (skill scripts must be runnable).
    for rel in meta.get("executable", []):
        path = target / rel
        if path.is_file():
            path.chmod(0o755)
    for rel in created:
        if rel.endswith(".sh") and "/scripts/" in rel:
            (target / rel).chmod(0o755)

    # 4. Registry: record every managed file as shipped (content hash).
    managed = {}
    for rel in created:
        is_managed = matches_any(rel, managed_patterns) or rel.startswith(
            skills_dest_prefix + "/"
        )
        if is_managed:
            managed[rel] = sha256_file(target / rel)

    registry = {
        "tool": "agentboom",
        "schema": 1,
        "template": DEFAULT_TEMPLATE,
        "base_version": __version__,
        "created_at": now_iso(),
        "name": name,
        "vars": variables,
        "managed": managed,
    }
    save_registry(target, registry)
    fleet_reg.register_best_effort(target)

    return {
        "ok": True,
        "path": str(target),
        "name": name,
        "template": DEFAULT_TEMPLATE,
        "base_version": __version__,
        "created": sorted(created),
        "skipped_existing": sorted(skipped),
        "managed_count": len(managed),
        "next_steps": [
            f"cd {target}",
            "cp .env.example .env   # fill in the required secrets",
            "docker compose up --build -d",
            "docker compose logs -f qwen-agent",
            "# Read .qwen-docker/AGENTS.md — it is the agent's operating manual.",
        ],
    }
