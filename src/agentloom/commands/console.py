"""`agentloom console` — drop into a pre-configured operator session.

Materializes the loomkeeper workspace (operator manual + fleet-ops skill
+ a live snapshot of the fleet) under $AGENTLOOM_HOME/console and execs
`qwen` (Qwen Code) inside it. The session arrives already knowing how to
use every agentloom command and how to work on any registered agent —
the independence guarantee holds throughout: the console only ever acts
through agentloom commands and git/docker on the agent repos.
"""
import os
import shutil
from pathlib import Path

from agentloom import fleet as fleet_reg
from agentloom.render import render_file

from . import templates_root
from .fleetcmd import _row

CONSOLE_DIR_NAME = "console"


class ConsoleError(RuntimeError):
    pass


def console_dir() -> Path:
    return fleet_reg.agentloom_home() / CONSOLE_DIR_NAME


def _workspace_files() -> dict:
    """Rendered workspace content that is refreshed on every launch."""
    fleet = fleet_reg.load_fleet()
    rows = [_row(e) for e in fleet["agents"]]

    lines = [
        "# Fleet snapshot",
        "",
        f"_Generated at console launch. {len(rows)} agent(s) registered._",
        "",
    ]
    if not rows:
        lines.append("The fleet is empty. Create the first agent with "
                     "`agentloom init <dir>`.")
    for row in rows:
        lines.append(f"## {row['name']}")
        lines.append("")
        if not row.get("ok"):
            lines.append(f"- **problem:** {row.get('error')}")
            lines.append(f"- path: `{row['path']}`")
            lines.append("")
            continue
        lines.append(f"- path: `{row['path']}`")
        lines.append(f"- base: agentloom {row['base_version']} "
                     f"(template: {row['template']}"
                     f"{', adopted' if row.get('adopted') else ''})")
        if row["packages"]:
            lines.append(f"- packages: {', '.join(row['packages'])}")
        lines.append(f"- managed files: {row['managed_files']} "
                     f"(drift: {len(row['drift_modified'])} modified, "
                     f"{len(row['drift_missing'])} missing)")
        lines.append(f"- validate: {row['validate_errors']} errors, "
                     f"{row['validate_warnings']} warnings")
        lines.append("")
    return {"fleet-snapshot.md": "\n".join(lines) + "\n"}


def run(args) -> dict:
    workspace = console_dir()
    source = templates_root() / "console"
    if not source.is_dir():
        raise ConsoleError(f"console template missing at {source}")

    workspace.mkdir(parents=True, exist_ok=True)

    # Operator manual + skills are owned by agentloom and refreshed each
    # launch; user files inside the workspace are left alone.
    refreshed = []
    for src in sorted(source.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(source).as_posix()
        dst = workspace / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        render_file(src, dst, {})
        refreshed.append(rel)

    for rel, content in _workspace_files().items():
        (workspace / rel).write_text(content, encoding="utf-8")
        refreshed.append(rel)

    result = {
        "ok": True,
        "workspace": str(workspace),
        "refreshed": refreshed,
        "launched": False,
    }

    if getattr(args, "dry_run", False):
        result["note"] = "dry-run: workspace refreshed, qwen not launched"
        return result

    qwen = shutil.which("qwen")
    if not qwen:
        raise ConsoleError(
            "qwen (Qwen Code) not found on PATH. Install it "
            "(npm install -g @qwen-code/qwen-code), then re-run "
            "`agentloom console`."
        )

    extra = getattr(args, "qwen_args", None) or []
    os.chdir(workspace)
    os.execvp(qwen, [qwen, *extra])
    return result  # unreachable; keeps the JSON contract explicit
