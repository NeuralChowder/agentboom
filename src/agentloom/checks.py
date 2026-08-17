"""Lightweight structural checks used by `agentloom validate` (stdlib only)."""
import re
from typing import Dict, List, Optional, Set, Tuple

# ── SKILL.md frontmatter ──────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> Optional[Dict[str, str]]:
    """Parse a minimal YAML-ish frontmatter block (flat key: value pairs only).

    Enough for agentskills.io-style SKILL.md headers without a YAML dep.
    Returns None when no frontmatter block is present.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    data: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            data[key] = value
    return data


# ── cron expressions (syntax validation only) ─────────────────────

_CRON_FIELDS = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 7),  # 7 = Sunday (Unix cron accepts both)
]


def validate_cron(expr: str) -> Tuple[bool, str]:
    """Check 5-field cron syntax. Returns (ok, message)."""
    if not expr or not expr.strip():
        return False, "empty cron expression"
    parts = expr.strip().split()
    if len(parts) != 5:
        return False, f"expected 5 fields, got {len(parts)}"
    for part, (name, lo, hi) in zip(parts, _CRON_FIELDS):
        ok, msg = _validate_cron_field(part, lo, hi)
        if not ok:
            return False, f"{name} field '{part}': {msg}"
    return True, "ok"


def _validate_cron_field(field: str, lo: int, hi: int) -> Tuple[bool, str]:
    for chunk in field.split(","):
        if not chunk:
            return False, "empty list item"
        base, _, step_str = chunk.partition("/")
        if step_str:
            if not step_str.isdigit() or int(step_str) < 1:
                return False, f"invalid step '{step_str}'"
        if base == "*":
            continue
        if "-" in base:
            start_str, _, end_str = base.partition("-")
            if not (start_str.isdigit() and end_str.isdigit()):
                return False, f"invalid range '{base}'"
            start, end = int(start_str), int(end_str)
            if start > end:
                return False, f"range start > end in '{base}'"
            if start < lo or end > hi:
                return False, f"range '{base}' out of bounds [{lo}-{hi}]"
        else:
            if not base.isdigit():
                return False, f"invalid value '{base}'"
            if not (lo <= int(base) <= hi):
                return False, f"value {base} out of bounds [{lo}-{hi}]"
    return True, "ok"


# ── compose / .env variable coverage ──────────────────────────────

_COMPOSE_VAR_RE = re.compile(
    r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)(?P<default>[:?-][^}]*)?\s*\}"
)
_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def compose_required_vars(compose_text: str) -> Set[str]:
    """Vars referenced as ${VAR} without a default — they must be provided."""
    required = set()
    for match in _COMPOSE_VAR_RE.finditer(compose_text):
        if not match.group("default"):
            required.add(match.group(1))
    return required


def env_file_vars(env_text: str) -> Set[str]:
    """Variable names assigned in a .env / .env.example file."""
    names = set()
    for line in env_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


# ── script references inside shell files ──────────────────────────

_SCRIPT_REF_RE = re.compile(r"(?:^|[\s\"'])([A-Za-z0-9_./$-]*scripts/[A-Za-z0-9_.-]+\.(?:py|sh))")


def referenced_scripts(sh_text: str) -> List[str]:
    """Paths of scripts/*.py|sh referenced by a shell file (best effort)."""
    refs = []
    for match in _SCRIPT_REF_RE.finditer(sh_text):
        ref = match.group(1)
        if "$" in ref:  # cannot resolve env-expanded paths statically
            continue
        refs.append(ref)
    return refs
