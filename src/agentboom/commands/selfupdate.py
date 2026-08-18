"""`agentboom self-update` — check for / install the latest release.

agentboom ships as a wheel attached to a GitHub release, so updating the
CLI itself is a reinstall from the newest release asset. This command
removes the guesswork:

    agentboom self-update            # check + print the exact update command
    agentboom self-update --apply    # actually run the installer

Detection is conservative and stdlib-only: it asks the GitHub API for the
latest release, compares versions, picks the `agentboom-<ver>-py3-none-any.whl`
asset, and chooses pipx vs pip based on how agentboom was installed. Without
`--apply` it never runs an installer — it only tells you what to run.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from typing import Optional

from agentboom import __version__

REPO = "NeuralChowder/agentboom"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"


class SelfUpdateError(RuntimeError):
    pass


def _latest_release(timeout: int = 20) -> dict:
    req = urllib.request.Request(API_LATEST, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "agentboom-self-update",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — surface as a clean error
        raise SelfUpdateError(f"could not query GitHub releases: {exc}")


def _version_tuple(version: str) -> tuple:
    parts = []
    for chunk in str(version).lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _wheel_asset(release: dict) -> Optional[str]:
    """The CLI wheel's download URL for this release, if present."""
    tag = str(release.get("tag_name", "")).lstrip("v")
    wanted = f"agentboom-{tag}-py3-none-any.whl"
    for asset in release.get("assets", []):
        if asset.get("name") == wanted:
            return asset.get("browser_download_url")
    for asset in release.get("assets", []):  # tolerate a slightly different name
        name = asset.get("name", "")
        if name.startswith("agentboom-") and name.endswith(".whl") \
                and "sdk" not in name:
            return asset.get("browser_download_url")
    return None


def _in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _is_root() -> bool:
    try:
        return os.geteuid() == 0  # POSIX only
    except AttributeError:
        return False


def _installed_via_pipx() -> bool:
    """Best-effort: pipx installs run from a pipx-managed venv."""
    if not shutil.which("pipx"):
        return False
    return "pipx" in (sys.executable or "")


def installer_command(wheel_url: str) -> list:
    """The reinstall command for however agentboom was installed."""
    spec = f"agentboom @ {wheel_url}"
    if _installed_via_pipx():
        return ["pipx", "install", "--force", spec]
    args = [sys.executable, "-m", "pip", "install", "--force-reinstall"]
    if not _in_venv() and not _is_root():
        args.append("--user")
    args.append(spec)
    return args


def run(args) -> dict:
    release = _latest_release()
    latest = str(release.get("tag_name", "")).lstrip("v")
    current = __version__
    result = {
        "ok": True,
        "current": current,
        "latest": latest,
        "update_available": _version_tuple(latest) > _version_tuple(current),
    }
    if not result["update_available"]:
        result["message"] = f"agentboom is up to date ({current})"
        return result

    wheel_url = _wheel_asset(release)
    if not wheel_url:
        raise SelfUpdateError(
            f"latest release v{latest} has no agentboom wheel asset — "
            "update manually from the release page")
    cmd = installer_command(wheel_url)
    result["wheel_url"] = wheel_url
    result["command"] = " ".join(cmd)

    if not getattr(args, "apply", False):
        result["message"] = (
            f"update available: {current} -> {latest}\n"
            f"  run: {' '.join(cmd)}\n"
            f"  or:  agentboom self-update --apply")
        return result

    proc = subprocess.run(cmd, capture_output=True, text=True)
    result["applied"] = True
    result["returncode"] = proc.returncode
    result["stdout_tail"] = (proc.stdout or "")[-600:]
    result["stderr_tail"] = (proc.stderr or "")[-600:]
    result["ok"] = proc.returncode == 0
    result["message"] = (f"updated to {latest}" if proc.returncode == 0
                         else "update command failed — see stderr_tail")
    return result
