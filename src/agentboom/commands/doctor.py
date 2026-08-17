"""`agentboom doctor` — environment checks for running agentboom agents."""
import shutil
import subprocess
import sys


def _run(cmd) -> tuple:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20
        )
        out = (proc.stdout or proc.stderr).strip().splitlines()
        return proc.returncode == 0, (out[0] if out else "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def run(args) -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str, required: bool = True):
        checks.append({"name": name, "ok": ok, "detail": detail, "required": required})

    py_ok = sys.version_info >= (3, 10)
    add(
        "python",
        py_ok,
        f"{sys.version.split()[0]} (agentboom needs >= 3.10)",
    )

    docker_ok, docker_detail = _run(["docker", "--version"])
    add("docker", docker_ok, docker_detail or "docker CLI not found")

    compose_ok, compose_detail = _run(["docker", "compose", "version"])
    add("docker-compose", compose_ok, compose_detail or "docker compose plugin not found")

    node_ok, node_detail = _run(["node", "--version"])
    add("node", node_ok, node_detail or "node not found (needed for skill scripts)", required=False)

    npm_ok, npm_detail = _run(["npm", "--version"])
    add("npm", npm_ok, npm_detail or "npm not found", required=False)

    git_ok, git_detail = _run(["git", "--version"])
    add("git", git_ok, git_detail or "git not found", required=False)

    failed_required = [c for c in checks if c["required"] and not c["ok"]]
    return {
        "ok": not failed_required,
        "checks": checks,
        "failed_required": [c["name"] for c in failed_required],
    }
