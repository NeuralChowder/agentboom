"""`agentloom adopt` — bring an existing agent under base management.

Non-destructive by construction: a file is only marked managed when the
file on disk is byte-identical to what the template would render with the
stored variables. Everything else stays agent-owned. After adopting,
`agentloom upgrade` behaves exactly as it does for an initialized agent.
"""
import argparse
import re
from pathlib import Path

from agentloom import __version__
from agentloom.registry import (
    TEMPLATE_META_NAME,
    load_registry,
    load_template_meta,
    matches_any,
    now_iso,
    save_registry,
    sha256_text,
)
from agentloom.render import TemplateError, render_text

from . import DEFAULT_TEMPLATE, skills_base_root, template_dir
from .init import DEFAULT_DESCRIPTION, QWEN_HOME_DEST, QWEN_HOME_SRC, NAME_RE


class AdoptError(RuntimeError):
    pass


def run(args) -> dict:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    if not agent_dir.is_dir():
        raise AdoptError(f"Not a directory: {agent_dir}")
    if load_registry(agent_dir) is not None:
        raise AdoptError(
            f"{agent_dir} already has a .agentloom.json — "
            "use `agentloom upgrade` to sync it."
        )

    name = (args.name or agent_dir.name).strip().lower()
    if not NAME_RE.match(name):
        raise AdoptError(
            f"Invalid agent name '{name}' — kebab-case required."
        )

    variables = {
        "AGENT_NAME": name,
        "AGENT_TITLE": " ".join(p.capitalize() for p in name.split("-") if p),
        "AGENT_DESCRIPTION": (args.description or DEFAULT_DESCRIPTION).strip(),
        "PORT_AGENT": str(args.port_agent),
        "PORT_PLATFORM": str(args.port_platform),
        "BASE_VERSION": __version__,
    }

    source = template_dir(args.template)
    meta = load_template_meta(source)
    managed_patterns = meta.get("managed", [])

    managed, skipped = {}, []

    def _consider(rel: str, rendered_text: str) -> None:
        installed = agent_dir / rel
        if not installed.is_file():
            skipped.append(rel)
            return
        try:
            current = installed.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append(rel)
            return
        if sha256_text(current) == sha256_text(rendered_text):
            managed[rel] = sha256_text(rendered_text)
        else:
            skipped.append(rel)

    # Template tree (with the qwen-home -> .qwen-docker mapping).
    for src in sorted(source.rglob("*")):
        if not src.is_file() or src.name == TEMPLATE_META_NAME:
            continue
        rel = src.relative_to(source).as_posix()
        parts = rel.split("/", 1)
        if parts[0] == QWEN_HOME_SRC:
            rel = QWEN_HOME_DEST + ("/" + parts[1] if len(parts) > 1 else "")
        is_managed_slot = matches_any(rel, managed_patterns)
        if not is_managed_slot:
            continue
        try:
            rendered = render_text(src.read_text(encoding="utf-8"), variables,
                                   source=rel)
        except (TemplateError, UnicodeDecodeError):
            skipped.append(rel)
            continue
        _consider(rel, rendered)

    # Base skills.
    skills_prefix = f"{QWEN_HOME_DEST}/skills"
    for src in sorted(skills_base_root().rglob("*")):
        if not src.is_file():
            continue
        rel = skills_prefix + "/" + src.relative_to(skills_base_root()).as_posix()
        try:
            rendered = render_text(src.read_text(encoding="utf-8"), variables,
                                   source=rel)
        except (TemplateError, UnicodeDecodeError):
            skipped.append(rel)
            continue
        _consider(rel, rendered)

    registry = {
        "tool": "agentloom",
        "schema": 1,
        "template": args.template,
        "base_version": __version__,
        "created_at": now_iso(),
        "name": name,
        "vars": variables,
        "managed": managed,
        "adopted": True,
    }
    save_registry(agent_dir, registry)

    return {
        "ok": True,
        "agent_dir": str(agent_dir),
        "name": name,
        "template": args.template,
        "base_version": __version__,
        "managed_matched": sorted(managed),
        "owned_or_diverged": sorted(skipped),
        "next": [
            "agentloom validate   # structural health",
            "agentloom upgrade    # check-only sync of the managed subset",
        ],
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("dir", nargs="?", default=".",
                        help="agent directory to adopt (default: cwd)")
    parser.add_argument("--name", help="agent name in kebab-case (default: directory name)")
    parser.add_argument("--description", help="one-line agent description")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE,
                        help=f"template the agent matches (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--port-agent", type=int, default=4170)
    parser.add_argument("--port-platform", type=int, default=8000)
