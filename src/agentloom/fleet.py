"""The fleet registry — agentloom's index of known agents ("wefts").

Stored at $AGENTLOOM_HOME/fleet.json (default ~/.agentloom/fleet.json).

This is an INDEX, never a dependency: agents run independently of it,
and deleting agentloom (or this file) affects no running agent. It only
lets the operator side — `agentloom fleet`, `agentloom console` — know
where the weave lives.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agentloom.registry import REGISTRY_NAME, load_registry, now_iso


def agentloom_home() -> Path:
    home = Path(os.environ.get("AGENTLOOM_HOME", str(Path.home() / ".agentloom")))
    home.mkdir(parents=True, exist_ok=True)
    return home


def fleet_path() -> Path:
    return agentloom_home() / "fleet.json"


def load_fleet() -> dict:
    path = fleet_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("agents"), list):
                return data
        except json.JSONDecodeError:
            pass
    return {"agents": []}


def save_fleet(fleet: dict) -> None:
    fleet_path().write_text(
        json.dumps(fleet, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def find_entry(fleet: dict, name_or_path: str) -> Optional[dict]:
    resolved = str(Path(name_or_path).expanduser().resolve())
    for entry in fleet["agents"]:
        if entry.get("name") == name_or_path or entry.get("path") == resolved:
            return entry
    return None


def register(agent_dir: Path) -> dict:
    """Add (or refresh) an agent in the fleet. Requires a registry file."""
    agent_dir = agent_dir.expanduser().resolve()
    registry = load_registry(agent_dir)
    if registry is None:
        raise FileNotFoundError(
            f"No {REGISTRY_NAME} in {agent_dir} — run `agentloom adopt` "
            "there first (or `agentloom init` for new agents)."
        )
    fleet = load_fleet()
    entry = find_entry(fleet, str(agent_dir))
    if entry is None:
        entry = {"path": str(agent_dir), "added_at": now_iso()}
        fleet["agents"].append(entry)
    entry["name"] = registry.get("name", agent_dir.name)
    save_fleet(fleet)
    return entry


def unregister(name_or_path: str) -> bool:
    fleet = load_fleet()
    entry = find_entry(fleet, name_or_path)
    if entry is None:
        return False
    fleet["agents"].remove(entry)
    save_fleet(fleet)
    return True


def register_best_effort(agent_dir: Path) -> None:
    """Used by `agentloom init` — fleet bookkeeping must never break init."""
    try:
        register(agent_dir)
    except Exception:  # noqa: BLE001 — best effort by design
        pass
