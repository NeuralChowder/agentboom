"""The per-agent registry (.agentloom.json) and managed-file bookkeeping.

A generated agent records which of its files are *managed* (owned by the
agentloom base, upgradable with `agentloom upgrade`) together with the
sha256 of each file as shipped. Upgrade is then a three-way decision:

  installed hash == recorded hash  -> clean, safe to overwrite with new base
  installed hash != recorded hash  -> locally modified, skip unless --force

This is deliberately pragmatic: hashes detect drift, but nothing stops a
user from editing managed files — they just take ownership of the diff.
"""
import fnmatch
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REGISTRY_NAME = ".agentloom.json"
TEMPLATE_META_NAME = ".agentloom-template.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_registry(agent_dir: Path) -> Optional[dict]:
    reg_path = agent_dir / REGISTRY_NAME
    if not reg_path.is_file():
        return None
    try:
        return json.loads(reg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_registry(agent_dir: Path, registry: dict) -> None:
    reg_path = agent_dir / REGISTRY_NAME
    reg_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_template_meta(template_dir: Path) -> dict:
    meta_path = template_dir / TEMPLATE_META_NAME
    if not meta_path.is_file():
        raise FileNotFoundError(
            f"Template meta missing: {meta_path} (expected {TEMPLATE_META_NAME})"
        )
    return json.loads(meta_path.read_text(encoding="utf-8"))


def matches_any(rel_path: str, patterns) -> bool:
    """fnmatch against agent-relative POSIX paths.

    Note: fnmatch's '*' also matches '/', so 'platform/sdk/*' covers nested
    files. Patterns are written accordingly.
    """
    return any(fnmatch.fnmatch(rel_path, pat) for pat in patterns)
