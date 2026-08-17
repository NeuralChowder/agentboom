"""`agentboom upgrade` — sync an agent's managed base files with this agentboom.

Managed files are re-rendered from the template using the variables stored
in .agentboom.json. Files the agent modified locally are skipped (reported)
unless --force is given. Files the base added since init are reported as new
and written on --apply. Files removed from the base are reported as stale
and never deleted automatically.
"""
from pathlib import Path

from agentboom import __version__
from agentboom.registry import (
    load_registry,
    load_template_meta,
    matches_any,
    save_registry,
    sha256_file,
    sha256_text,
)
from agentboom.render import render_file

from . import skills_base_root, template_dir

SKILLS_PREFIX = ".qwen-docker/skills/"
# The template stores the agent home undotted (packaging globs skip
# dot-dirs); generated agents use the dot-directory.
QWEN_HOME_SRC = "qwen-home"
QWEN_HOME_DEST = ".qwen-docker"


class UpgradeError(RuntimeError):
    pass


def _to_agent_rel(template_rel: str) -> str:
    parts = template_rel.split("/", 1)
    if parts[0] == QWEN_HOME_SRC:
        return QWEN_HOME_DEST + ("/" + parts[1] if len(parts) > 1 else "")
    return template_rel


def _base_managed_rels(template: str) -> set:
    """Every managed rel path the current base would ship, at this version."""
    source = template_dir(template)
    meta = load_template_meta(source)
    patterns = meta.get("managed", [])
    rels = set()
    for src in source.rglob("*"):
        if not src.is_file() or src.name == ".agentboom-template.json":
            continue
        rel = _to_agent_rel(src.relative_to(source).as_posix())
        if matches_any(rel, patterns):
            rels.add(rel)
    for src in skills_base_root().rglob("*"):
        if src.is_file():
            rels.add(SKILLS_PREFIX + src.relative_to(skills_base_root()).as_posix())
    return rels


def _resolve_source(template: str, rel: str) -> Path:
    if rel.startswith(SKILLS_PREFIX):
        return skills_base_root() / rel[len(SKILLS_PREFIX):]
    parts = rel.split("/", 1)
    if parts[0] == QWEN_HOME_DEST:
        return template_dir(template) / QWEN_HOME_SRC / (parts[1] if len(parts) > 1 else "")
    return template_dir(template) / rel


def run(args) -> dict:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    registry = load_registry(agent_dir)
    if registry is None:
        raise UpgradeError(
            f"No .agentboom.json in {agent_dir} — not an agentboom-managed agent."
        )

    template = registry.get("template", "platform")
    variables = registry.get("vars", {})
    managed = dict(registry.get("managed", {}))

    report = {
        "up_to_date": [],
        "upgraded": [],
        "new": [],
        "restored": [],
        "locally_modified": [],
        "stale": [],
    }

    base_rels = _base_managed_rels(template)

    for rel in sorted(set(managed) | base_rels):
        src = _resolve_source(template, rel)
        installed = agent_dir / rel

        if not src.is_file():
            # No longer shipped by the base.
            if rel in managed:
                report["stale"].append(rel)
            continue

        rendered_sha = _rendered_sha(src, variables, rel)

        if rel not in managed:
            report["new"].append(rel)
            if args.apply:
                installed.parent.mkdir(parents=True, exist_ok=True)
                render_file(src, installed, variables)
                managed[rel] = sha256_file(installed)
            continue

        recorded = managed[rel]
        if not installed.is_file():
            report["restored"].append(rel)
            if args.apply:
                installed.parent.mkdir(parents=True, exist_ok=True)
                render_file(src, installed, variables)
                managed[rel] = sha256_file(installed)
            continue

        current = sha256_file(installed)
        if current != recorded:
            # The agent edited a managed file.
            if args.apply and args.force:
                render_file(src, installed, variables)
                managed[rel] = sha256_file(installed)
                report["upgraded"].append(rel)
            else:
                report["locally_modified"].append(rel)
            continue

        if rendered_sha != recorded:
            report["upgraded"].append(rel)
            if args.apply:
                render_file(src, installed, variables)
                managed[rel] = sha256_file(installed)
        else:
            report["up_to_date"].append(rel)

    if args.apply:
        registry["managed"] = managed
        registry["base_version"] = __version__
        save_registry(agent_dir, registry)

    changed = (
        report["upgraded"] or report["new"] or report["restored"]
        or report["stale"] or report["locally_modified"]
    )
    return {
        "ok": True,
        "agent_dir": str(agent_dir),
        "mode": "apply" if args.apply else "check",
        "base_version_from": registry.get("base_version"),
        "base_version_to": __version__,
        "changed": bool(changed),
        **report,
    }


def _rendered_sha(src: Path, variables: dict, rel: str) -> str:
    from agentboom.render import is_probably_binary, render_text

    data = src.read_bytes()
    if is_probably_binary(data):
        from agentboom.registry import sha256_file as _f

        return _f(src)
    return sha256_text(render_text(data.decode("utf-8"), variables, source=rel))
