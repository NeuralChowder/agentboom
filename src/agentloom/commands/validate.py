"""`agentloom validate` — structural health checks for an agent project."""
import json
from pathlib import Path

from agentloom.checks import (
    compose_required_vars,
    env_file_vars,
    parse_frontmatter,
    referenced_scripts,
    validate_cron,
)
from agentloom.registry import load_registry, sha256_file

REQUIRED_FILES = [
    "Dockerfile",
    "docker-compose.yml",
    "entrypoint.sh",
    ".env.example",
    ".gitignore",
    ".qwen-docker/AGENTS.md",
    ".qwen-docker/output-language.md",
    ".qwen-docker/settings.example.json",
    "platform/api_gateway.py",
    "platform/requirements.txt",
    "platform/Dockerfile",
    "platform/sdk/__init__.py",
    "platform/migrations/run.py",
]

APP_DIRS = ("platform/miniapps", "platform/public-apps")


def _check(checks: list, level: str, check_id: str, message: str, path: str = "") -> None:
    checks.append({"id": check_id, "level": level, "message": message, "path": path})


def run(args) -> dict:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    checks = []

    registry = load_registry(agent_dir)
    if registry is None:
        _check(
            checks, "error", "agentloom.registry-missing",
            f"No .agentloom.json found — {agent_dir} does not look like an "
            "agentloom-managed agent (created with `agentloom init`).",
            ".agentloom.json",
        )

    # ── Required files ────────────────────────────────────────────
    for rel in REQUIRED_FILES:
        if not (agent_dir / rel).is_file():
            _check(checks, "error", "structure.missing-file", f"Missing required file: {rel}", rel)

    # ── entrypoint.sh must not reference missing scripts ─────────
    entrypoint = agent_dir / "entrypoint.sh"
    if entrypoint.is_file():
        for ref in referenced_scripts(entrypoint.read_text(encoding="utf-8")):
            if not (agent_dir / ref).is_file():
                _check(
                    checks, "error", "entrypoint.dangling-script",
                    f"entrypoint.sh references '{ref}' which does not exist.",
                    "entrypoint.sh",
                )

    # ── compose variables must be covered by .env.example ─────────
    compose = agent_dir / "docker-compose.yml"
    env_example = agent_dir / ".env.example"
    if compose.is_file() and env_example.is_file():
        required = compose_required_vars(compose.read_text(encoding="utf-8"))
        provided = env_file_vars(env_example.read_text(encoding="utf-8"))
        for var in sorted(required - provided):
            _check(
                checks, "error", "env.var-not-documented",
                f"docker-compose.yml requires ${{{var}}} (no default) but "
                ".env.example never defines it.",
                ".env.example",
            )

    # ── Mini-app manifests ────────────────────────────────────────
    for app_root in APP_DIRS:
        base = agent_dir / app_root
        if not base.is_dir():
            continue
        for app_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            rel = app_dir.relative_to(agent_dir).as_posix()
            main_py = app_dir / "main.py"
            manifest_path = app_dir / ".miniapp.json"
            if not main_py.is_file():
                _check(checks, "warn", "miniapp.no-main",
                       f"{rel} has no main.py — it will not be loaded.", rel)
                continue
            if "def get_router" not in main_py.read_text(encoding="utf-8"):
                _check(checks, "error", "miniapp.no-get-router",
                       f"{rel}/main.py does not define get_router() — "
                       "the gateway cannot mount it.", rel)
            if not manifest_path.is_file():
                _check(checks, "warn", "miniapp.no-manifest",
                       f"{rel} has no .miniapp.json manifest (name/description/jobs).", rel)
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                _check(checks, "error", "miniapp.bad-manifest",
                       f"{rel}/.miniapp.json is not valid JSON: {exc}", rel)
                continue
            for job in manifest.get("jobs", []):
                job_name = job.get("name", "<unnamed>")
                cron_expr = job.get("cron")
                if cron_expr:
                    ok, msg = validate_cron(cron_expr)
                    if not ok:
                        _check(checks, "error", "miniapp.bad-cron",
                               f"{rel} job '{job_name}': invalid cron '{cron_expr}' — {msg}", rel)
                if job.get("type", "http") == "http" and not job.get("target"):
                    _check(checks, "error", "miniapp.job-no-target",
                           f"{rel} job '{job_name}': http jobs need a 'target'.", rel)
                if job.get("type") == "agent" and not job.get("prompt"):
                    _check(checks, "error", "miniapp.job-no-prompt",
                           f"{rel} job '{job_name}': agent jobs need a 'prompt'.", rel)

    # ── Skills frontmatter ────────────────────────────────────────
    skills_dir = agent_dir / ".qwen-docker" / "skills"
    if skills_dir.is_dir():
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            rel = skill_dir.relative_to(agent_dir).as_posix()
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                _check(checks, "warn", "skill.no-skill-md",
                       f"{rel} has no SKILL.md — it will not be discovered.", rel)
                continue
            fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            if fm is None:
                _check(checks, "error", "skill.no-frontmatter",
                       f"{rel}/SKILL.md has no frontmatter block.", rel)
                continue
            for field in ("name", "description"):
                if not fm.get(field):
                    _check(checks, "error", f"skill.missing-{field}",
                           f"{rel}/SKILL.md frontmatter is missing '{field}'.", rel)
            desc = fm.get("description", "")
            if len(desc) > 1024:
                _check(checks, "warn", "skill.long-description",
                       f"{rel}/SKILL.md description is {len(desc)} chars — keep it concise.", rel)

    # ── Managed-file drift (informational) ────────────────────────
    if registry:
        for rel, recorded in sorted(registry.get("managed", {}).items()):
            path = agent_dir / rel
            if not path.is_file():
                _check(checks, "warn", "base.file-missing",
                       f"Managed file {rel} was deleted. `agentloom upgrade --apply` restores it.", rel)
            elif sha256_file(path) != recorded:
                _check(checks, "info", "base.locally-modified",
                       f"Managed file {rel} was modified locally (drift from base).", rel)

    errors = [c for c in checks if c["level"] == "error"]
    return {
        "ok": not errors,
        "agent_dir": str(agent_dir),
        "errors": len(errors),
        "warnings": sum(1 for c in checks if c["level"] == "warn"),
        "checks": checks,
    }
