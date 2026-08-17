"""Agent runtimes for `agentboom code`.

A runtime is a coding agent CLI that can take a mission prompt and continue
interactively. Today only Qwen Code is fully supported; the registry is
the extension point for future runtimes (opencode, claude, ...): add a
RuntimeSpec and, when the CLI's prompt/interactive flags are known, set
`supported = True`.

On the agents themselves the runtime already exists (it runs inside the
agent container). Locally, `agentboom install-runtime` offers the install
command for you.
"""
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional


class RuntimeError_(RuntimeError):
    """Raised when a runtime is unavailable or not yet supported."""


@dataclass
class RuntimeSpec:
    name: str
    binary: str
    install_cmd: str
    supported: bool
    note: str = ""
    # builds the argv to launch an interactive session with a mission
    launch: Optional[object] = None

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def launch_argv(self, mission: str) -> List[str]:
        if not self.supported:
            raise RuntimeError_(
                f"runtime '{self.name}' is detected but not supported by "
                f"agentboom yet ({self.note}). Use --runtime qwen for now."
            )
        if not self.available():
            raise RuntimeError_(
                f"runtime '{self.name}' not found on PATH. Install it with:\n"
                f"  {self.install_cmd}\n"
                f"or run: agentboom install-runtime {self.name}"
            )
        return self.launch(mission)


def _qwen_launch(mission: str) -> List[str]:
    # execute the mission, then stay interactive for iteration
    return ["qwen", "--prompt-interactive", mission]


RUNTIMES: List[RuntimeSpec] = [
    RuntimeSpec(
        name="qwen",
        binary="qwen",
        install_cmd="npm install -g @qwen-code/qwen-code",
        supported=True,
        launch=_qwen_launch,
    ),
    RuntimeSpec(
        name="opencode",
        binary="opencode",
        install_cmd="see https://opencode.ai (curl -fsSL https://opencode.ai/install | bash)",
        supported=False,
        note="launch flags for a mission prompt are not wired yet",
    ),
    RuntimeSpec(
        name="claude",
        binary="claude",
        install_cmd="npm install -g @anthropic-ai/claude-code",
        supported=False,
        note="launch flags for a mission prompt are not wired yet",
    ),
]


def get_runtime(name: str) -> RuntimeSpec:
    for r in RUNTIMES:
        if r.name == name:
            return r
    known = ", ".join(r.name for r in RUNTIMES)
    raise RuntimeError_(f"unknown runtime '{name}'. Known runtimes: {known}")


def install_runtime(name: str, yes: bool = False) -> dict:
    """Offer (and with yes=True run) the install command for a runtime."""
    spec = get_runtime(name)
    if spec.available():
        return {"ok": True, "runtime": name,
                "note": f"'{spec.binary}' already on PATH", "installed": False}
    if not yes:
        return {"ok": True, "runtime": name, "installed": False,
                "command": spec.install_cmd,
                "note": "re-run with --yes to install now"}
    # show exactly what runs, then run it
    print(f"$ {spec.install_cmd}")
    proc = subprocess.run(spec.install_cmd, shell=True)
    if proc.returncode != 0:
        return {"ok": False, "runtime": name, "installed": False,
                "error": f"install exited {proc.returncode}"}
    return {"ok": True, "runtime": name, "installed": True}
