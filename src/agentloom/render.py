"""Template rendering: replace {{PLACEHOLDER}} variables in template files."""
import re
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}")


class TemplateError(RuntimeError):
    """Raised when a template references an unknown placeholder."""


def render_text(text: str, variables: dict, source: str = "<text>") -> str:
    """Replace every {{VAR}} with variables[VAR].

    Strict on purpose: an unknown placeholder means the template and the CLI
    drifted apart, and emitting it literally would produce broken configs.
    """
    missing = set()

    def _sub(match):
        key = match.group(1)
        if key not in variables:
            missing.add(key)
            return match.group(0)
        return str(variables[key])

    rendered = PLACEHOLDER.sub(_sub, text)
    if missing:
        raise TemplateError(
            f"{source}: unknown placeholder(s): {', '.join(sorted(missing))}"
        )
    return rendered


def is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def render_file(src: Path, dst: Path, variables: dict) -> bool:
    """Copy src to dst, rendering placeholders in text files.

    Returns True if the file was rendered as text, False if copied verbatim.
    """
    data = src.read_bytes()
    if is_probably_binary(data):
        dst.write_bytes(data)
        return False
    text = data.decode("utf-8")
    dst.write_text(render_text(text, variables, source=str(src)), encoding="utf-8")
    return True
