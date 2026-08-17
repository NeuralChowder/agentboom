"""Shared helpers for agentloom commands."""
from pathlib import Path
from typing import List

from agentloom.registry import TEMPLATE_META_NAME, load_template_meta

DEFAULT_TEMPLATE = "platform"


def templates_root() -> Path:
    return Path(__file__).resolve().parent.parent / "templates"


def template_dir(name: str) -> Path:
    path = templates_root() / name
    if not path.is_dir():
        raise FileNotFoundError(f"Unknown template '{name}' (looked in {path})")
    return path


def list_templates() -> List[str]:
    return sorted(
        p.name for p in templates_root().iterdir()
        if p.is_dir() and (p / TEMPLATE_META_NAME).is_file()
    )


def skills_base_root() -> Path:
    return templates_root() / "skills-base"


def template_meta(name: str) -> dict:
    return load_template_meta(template_dir(name))
