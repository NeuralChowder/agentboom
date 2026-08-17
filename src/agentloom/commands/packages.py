"""`agentloom add package` / `agentloom packages` — optional agent add-ons.

A package is a bundle of files placed into an existing agent (mini-apps,
skills, docs, migrations) plus requirement lines, .env.example lines, and
post-install instructions. Packages never overwrite existing files.
"""
import json
from pathlib import Path

from agentloom.registry import load_registry, save_registry, now_iso
from agentloom.render import render_text

from . import templates_root

PACKAGE_META_NAME = ".agentloom-package.json"
QWEN_HOME_SRC = "qwen-home"
QWEN_HOME_DEST = ".qwen-docker"


class PackageError(RuntimeError):
    pass


def packages_root() -> Path:
    return templates_root() / "packages"


def available_packages() -> list:
    root = packages_root()
    if not root.is_dir():
        return []
    out = []
    for pkg_dir in sorted(root.iterdir()):
        meta_path = pkg_dir / PACKAGE_META_NAME
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({
            "name": meta.get("name", pkg_dir.name),
            "description": meta.get("description", ""),
        })
    return out


def _agent_dir(args) -> Path:
    agent_dir = Path(args.dir or ".").expanduser().resolve()
    if load_registry(agent_dir) is None:
        raise PackageError(
            f"{agent_dir} has no .agentloom.json — run from inside an "
            "agentloom agent or pass --dir."
        )
    return agent_dir


def run_add_package(args) -> dict:
    name = args.name.strip().lower()
    pkg_dir = packages_root() / name
    meta_path = pkg_dir / PACKAGE_META_NAME
    if not meta_path.is_file():
        known = ", ".join(p["name"] for p in available_packages()) or "(none)"
        raise PackageError(f"Unknown package '{name}'. Available: {known}")

    agent_dir = _agent_dir(args)
    registry = load_registry(agent_dir)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    variables = dict(registry.get("vars", {}))

    created, skipped = [], []

    # 1. Files (rendered with the agent's stored init variables).
    for src in sorted(pkg_dir.rglob("*")):
        if not src.is_file() or src.name == PACKAGE_META_NAME:
            continue
        rel = src.relative_to(pkg_dir).as_posix()
        parts = rel.split("/", 1)
        if parts[0] == QWEN_HOME_SRC:
            rel = QWEN_HOME_DEST + ("/" + parts[1] if len(parts) > 1 else "")
        dst = agent_dir / rel
        if dst.exists():
            skipped.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_bytes()
        try:
            dst.write_text(
                render_text(text.decode("utf-8"), variables, source=rel),
                encoding="utf-8",
            )
        except UnicodeDecodeError:
            dst.write_bytes(text)
        created.append(rel)
        if rel.endswith(".sh"):
            dst.chmod(0o755)

    # 2. Requirements lines (idempotent by distribution name prefix).
    added_reqs = []
    reqs_path = agent_dir / "platform" / "requirements.txt"
    for line in meta.get("requirements", []):
        pkg_name = line.split()[0].split("@")[0].split("=")[0].split(">")[0].split("<")[0].strip()
        if reqs_path.is_file() and pkg_name and pkg_name in reqs_path.read_text(encoding="utf-8"):
            continue
        if reqs_path.is_file():
            with reqs_path.open("a", encoding="utf-8") as fh:
                fh.write(line.rstrip() + "\n")
            added_reqs.append(line)

    # 3. .env.example lines (idempotent by KEY name).
    added_env = []
    env_path = agent_dir / ".env.example"
    if env_path.is_file():
        existing = env_path.read_text(encoding="utf-8")
        for line in meta.get("env_example", []):
            key = line.split("=", 1)[0].strip()
            if key and any(
                l.strip().startswith(key + "=") or l.strip().startswith("# " + key + "=")
                for l in existing.splitlines()
            ):
                continue
            rendered = render_text(line, variables, source=f"env:{key}")
            with env_path.open("a", encoding="utf-8") as fh:
                fh.write(rendered.rstrip() + "\n")
            added_env.append(rendered)
            existing += "\n" + rendered

    # 4. Record in registry.
    registry.setdefault("packages", {})[name] = {
        "installed_at": now_iso(),
        "files": created,
    }
    save_registry(agent_dir, registry)

    return {
        "ok": True,
        "package": name,
        "agent_dir": str(agent_dir),
        "created": created,
        "skipped_existing": skipped,
        "requirements_added": added_reqs,
        "env_example_added": added_env,
        "post_install": meta.get("post_install", []),
    }


def run_packages(args) -> dict:
    agent_dir = Path(args.dir or ".").expanduser().resolve() if args.dir else None
    installed = {}
    if agent_dir:
        registry = load_registry(agent_dir)
        if registry:
            installed = registry.get("packages", {})
    return {
        "ok": True,
        "available": available_packages(),
        "installed": installed,
        "agent_dir": str(agent_dir) if agent_dir else None,
    }
