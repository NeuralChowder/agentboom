"""`agentloom fleet` — the operator's view of every known agent."""
import argparse
from pathlib import Path

from agentloom import fleet as fleet_reg
from agentloom.registry import load_registry, sha256_file
from agentloom.commands import validate as validate_cmd


class FleetError(RuntimeError):
    pass


def _drift(agent_dir: Path, registry: dict) -> dict:
    """Compare managed files against their recorded shipped hashes."""
    modified, missing = [], []
    for rel, recorded in sorted(registry.get("managed", {}).items()):
        path = agent_dir / rel
        if not path.is_file():
            missing.append(rel)
        elif sha256_file(path) != recorded:
            modified.append(rel)
    return {"modified": modified, "missing": missing}


def _row(entry: dict) -> dict:
    path = Path(entry.get("path", ""))
    row = {
        "name": entry.get("name", path.name),
        "path": str(path),
        "added_at": entry.get("added_at"),
        "ok": False,
    }
    if not path.is_dir():
        row["error"] = "directory missing"
        return row
    registry = load_registry(path)
    if registry is None:
        row["error"] = "no .agentloom.json (run: agentloom adopt)"
        return row
    drift = _drift(path, registry)
    checks = validate_cmd.run(argparse.Namespace(dir=str(path)))
    row.update({
        "ok": True,
        "base_version": registry.get("base_version"),
        "template": registry.get("template"),
        "adopted": bool(registry.get("adopted")),
        "packages": sorted(registry.get("packages", {})),
        "managed_files": len(registry.get("managed", {})),
        "drift_modified": drift["modified"],
        "drift_missing": drift["missing"],
        "validate_errors": checks["errors"],
        "validate_warnings": checks["warnings"],
    })
    return row


def run_status(args) -> dict:
    fleet = fleet_reg.load_fleet()
    rows = [_row(e) for e in fleet["agents"]]
    return {
        "ok": True,
        "fleet_file": str(fleet_reg.fleet_path()),
        "agents": rows,
    }


def run_add(args) -> dict:
    agent_dir = Path(args.dir).expanduser()
    try:
        entry = fleet_reg.register(agent_dir)
    except FileNotFoundError as exc:
        raise FleetError(str(exc))
    return {"ok": True, "registered": entry}


def run_remove(args) -> dict:
    if fleet_reg.unregister(args.name):
        return {"ok": True, "removed": args.name}
    raise FleetError(
        f"'{args.name}' is not in the fleet (agentloom fleet to list)."
    )
