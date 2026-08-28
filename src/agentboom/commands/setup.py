"""`agentboom setup` — configure an agent's secrets (`.env`) and model
config (`.qwen-docker/settings.json`) so a non-developer can go from
`agentboom init` to a running agent.

Two entry points share the same pure helpers:

- `agentboom init --generate-env`  -> generates a `.env` + `settings.json`
  using the LLM flags passed on the command line (fully non-interactive).
- `agentboom setup`                -> an interactive wizard (a few plain
  questions) with a `--non-interactive` mode driven by `AGENT_*` env vars
  for scripts and CI.

Design notes
------------
- The LLM model provider in `settings.json` is what powers the agent's
  `qwen serve` brain. The `LLM_*` vars in `.env` are the platform's
  one-shot completion endpoint (mini-apps). We write the same model name
  to both so a single answer covers both.
- Secrets are generated with `secrets` (OS CSPRNG) and written only to
  gitignored files. They are NEVER echoed to stdout / JSON output — the
  result payload lists which keys were set, not their values.
- Filling is idempotent: keys that already carry a value are left
  untouched, so re-running `setup` after installing packages never
  regenerates existing tokens — it only fills the new gaps.
"""
import json
import os
import re
import secrets as _secrets
from pathlib import Path
from typing import Dict, List, Tuple

from agentboom.registry import REGISTRY_NAME, load_registry

QWEN_SETTINGS_EXAMPLE = ".qwen-docker/settings.example.json"
QWEN_SETTINGS = ".qwen-docker/settings.json"

# Where the model provider reads its API key from in settings.json.
_SETTINGS_ENV_KEY = "AGENT_LLM_API_KEY"

# Env vars the non-interactive / scripted mode reads (distinct prefix so a
# leaked shell var like DATABASE_URI can never be mistaken for our config).
ENV_LLM_URL = "AGENT_LLM_URL"
ENV_LLM_API_KEY = "AGENT_LLM_API_KEY"
ENV_LLM_MODEL = "AGENT_LLM_MODEL"
ENV_TIMEZONE = "AGENT_TIMEZONE"

# Env keys we know how to auto-generate, mapped to a generator.
_HEX32 = lambda: _secrets.token_hex(32)          # 64 hex chars = 32 bytes
SECRET_GENERATORS = {
    "QWEN_SERVER_TOKEN": _HEX32,
    "PLATFORM_TOKEN": _HEX32,
    "VAULT_KEY": _HEX32,            # vault mini-app requires exactly 32 bytes
    "PLATFORM_ADMIN_PASSWORD": lambda: _secrets.token_urlsafe(12),
}

# Env keys that take their value from the LLM answer (filled only when we
# have something to put there).
LLM_ENV_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SetupError(RuntimeError):
    pass


# ── pure helpers (unit-tested) ─────────────────────────────────────────


def parse_env(text: str) -> Dict[str, str]:
    """Parse a dotenv-style file into {KEY: value} for set (non-empty) keys."""
    out: Dict[str, str] = {}
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value and _KEY_RE.match(key):
            out[key] = value
    return out


def fill_env_text(example_text: str, existing: dict, llm: dict) -> Tuple[str, List[str]]:
    """Fill empty values in a rendered `.env.example`.

    - Known secret keys -> carried over from `existing` if set, else generated.
    - LLM keys -> carried over, else filled from the LLM answer (if present).
    - Anything already set, or unknown, is left byte-for-byte untouched.

    Returns (new_text, filled_keys) where filled_keys is a list of the key
    NAMES we wrote a value to (values are deliberately not returned).
    """
    existing = existing or {}
    llm_values = {
        "LLM_BASE_URL": llm.get("base_url", "") or "",
        "LLM_API_KEY": llm.get("api_key", "") or "",
        "LLM_MODEL": llm.get("model", "") or "",
    }
    lines = example_text.split("\n")
    filled: List[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # Only ever touch empty template values (e.g. PORT_AGENT=4170 is kept).
        if value != "" or not _KEY_RE.match(key):
            continue

        before = existing.get(key, "")
        if key in SECRET_GENERATORS:
            # Carry the existing secret into the output; generate only when
            # absent. Never regenerate one that is already set.
            written = before or SECRET_GENERATORS[key]()
            lines[i] = f"{key}={written}"
            if before == "":
                filled.append(key)  # newly generated
        elif key in llm_values:
            # LLM config is not a secret: a fresh answer wins, else keep what
            # is already in .env (re-running without new flags must not
            # clobber a working setup).
            if llm_values[key]:
                written = llm_values[key]
            elif before:
                written = before
            else:
                continue  # nothing known for this key — leave it empty
            lines[i] = f"{key}={written}"
            if written != before:
                filled.append(key)  # value actually changed
    return "\n".join(lines), filled


def build_settings_dict(example: dict, llm: dict,
                        existing_model: dict = None,
                        existing_env: dict = None) -> dict:
    """Produce a ready `settings.json` from the example + the LLM answer.

    Reuses the example's structure (tools, permissions, mcpServers,
    generationConfig) and wires the first `openai` provider to the user's
    endpoint + model, with the API key carried in `env` under a stable
    envKey. A value the caller did not provide is preserved from
    `existing_model` / `existing_env` (so re-running without new flags does
    not clobber a working setup); only when nothing is known is a clear
    placeholder written, keeping the file valid and the gap obvious.
    """
    existing_model = existing_model or {}
    existing_env = existing_env or {}
    settings = json.loads(json.dumps(example))
    settings.pop("$comment", None)

    model_name = ((llm.get("model") or "").strip()
                  or existing_model.get("name") or "generic")
    base_url = ((llm.get("base_url") or "").strip()
                or existing_model.get("baseUrl") or "http://YOUR_LLM_SERVER:4000/v1")
    api_key = ((llm.get("api_key") or "").strip()
               or existing_env.get(_SETTINGS_ENV_KEY) or "not-needed")

    providers = settings.setdefault("modelProviders", {}).setdefault("openai", [])
    if not providers:
        providers.append({
            "id": model_name, "name": model_name,
            "generationConfig": {"contextWindowSize": 128000},
        })
    prov = providers[0]
    prov["id"] = model_name
    prov["name"] = model_name
    prov["baseUrl"] = base_url
    prov["envKey"] = _SETTINGS_ENV_KEY

    model = settings.setdefault("model", {})
    model["name"] = model_name
    model["baseUrl"] = base_url

    settings["env"] = {_SETTINGS_ENV_KEY: api_key}
    return settings


def llm_from_env() -> Dict[str, str]:
    """LLM config from AGENT_* env vars (scripted mode). Missing -> empty."""
    return {
        "base_url": os.environ.get(ENV_LLM_URL, "").strip(),
        "api_key": os.environ.get(ENV_LLM_API_KEY, "").strip(),
        "model": os.environ.get(ENV_LLM_MODEL, "").strip(),
    }


def detect_timezone() -> str:
    """Best-effort IANA timezone for the host (Linux-oriented). Fails to UTC."""
    try:
        path = Path("/etc/localtime")
        if path.is_symlink():
            target = os.readlink(path)  # e.g. /usr/share/zoneinfo/Europe/Lisbon
            marker = "/zoneinfo/"
            if marker in target:
                return target.split(marker, 1)[1]
        etc = Path("/etc/timezone")
        if etc.is_file():
            tz = etc.read_text().strip()
            if tz:
                return tz
    except OSError:
        pass
    import time
    abbr = time.tzname[0]
    if abbr and abbr not in ("UTC", "GMT", ""):
        return abbr
    return "UTC"


# ── I/O (writes the files) ─────────────────────────────────────────────


def _write_env(agent_dir: Path, llm: dict) -> Tuple[List[str], bool]:
    """Write agent_dir/.env from .env.example, preserving existing values."""
    env_path = agent_dir / ".env"
    example_path = agent_dir / ".env.example"
    if not example_path.is_file():
        raise SetupError(f"no .env.example in {agent_dir} (is this an agent?)")
    existing = parse_env(env_path.read_text(encoding="utf-8")) if env_path.is_file() else {}
    new_text, filled = fill_env_text(example_path.read_text(encoding="utf-8"),
                                     existing, llm)
    env_path.write_text(new_text, encoding="utf-8")
    _chmod_600(env_path)
    return filled, bool(filled)


def _write_settings(agent_dir: Path, llm: dict) -> bool:
    """Write agent_dir/.qwen-docker/settings.json from the example + llm,
    preserving the current model/endpoint/key when the caller gives none."""
    example_path = agent_dir / QWEN_SETTINGS_EXAMPLE
    dest = agent_dir / QWEN_SETTINGS
    if not example_path.is_file():
        return False
    example = json.loads(example_path.read_text(encoding="utf-8"))

    existing_model: dict = {}
    existing_env: dict = {}
    if dest.is_file():
        try:
            current = json.loads(dest.read_text(encoding="utf-8"))
            existing_model = current.get("model", {}) or {}
            existing_env = current.get("env", {}) or {}
        except (json.JSONDecodeError, OSError):
            pass

    settings = build_settings_dict(example, llm, existing_model, existing_env)
    dest.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    _chmod_600(dest)
    return True


def _chmod_600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _set_profile_timezone(agent_dir: Path, timezone: str) -> bool:
    profile_path = agent_dir / ".qwen-docker" / "profile.json"
    if not profile_path.is_file() or not timezone:
        return False
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if profile.get("timezone") == timezone:
        return False
    profile["timezone"] = timezone
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    return True


def generate_env(agent_dir: Path, llm: dict) -> dict:
    """Create `.env` and `settings.json` from the examples, preserving any
    values already present in `.env` (so it is safe to re-run). Returns a
    summary with NO secret values — only which keys changed and the model
    the written settings.json will actually use (read back, not echoed)."""
    agent_dir = Path(agent_dir)
    filled, env_changed = _write_env(agent_dir, llm)
    settings_written = _write_settings(agent_dir, llm)

    actual = {"base_url": "", "model": "generic"}
    settings_path = agent_dir / QWEN_SETTINGS
    if settings_path.is_file():
        try:
            written = json.loads(settings_path.read_text(encoding="utf-8"))
            model = written.get("model", {})
            actual = {"base_url": model.get("baseUrl", ""),
                      "model": model.get("name", "generic")}
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "env_keys_set": filled,
        "env_changed": env_changed,
        "settings_written": settings_written,
        "llm": actual,
    }


# ── interactive wizard ─────────────────────────────────────────────────


def _ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        return input(f"{question}{suffix}: ").strip() or default
    except (EOFError, KeyboardInterrupt):
        return default


def _ask_secret(question: str, default: str = "") -> str:
    """Read a secret without echoing it (getpass), falling back to input()."""
    prompt = question + (f" [{default}]" if default else "") + ": "
    try:
        import getpass
        return getpass.getpass(prompt).strip() or default
    except (EOFError, KeyboardInterrupt):
        return default
    except Exception:  # not a tty / getpass unavailable — degrade gracefully
        return _ask(question, default)


def interactive_llm(llm: dict) -> dict:
    """Ask the plain-language questions. `llm` supplies any values the
    caller already knows (e.g. from flags) so those are pre-filled."""
    print()
    print("This sets up the model that powers your agent.")
    print("Press Enter to accept the value shown in [brackets].")
    print()
    choice = (_ask(
        "Where does your model run?", "1"
    ) or "1").lower()
    if choice.startswith(("2", "hosted", "remote", "api")):
        base_url = _ask("API base URL", "https://api.openai.com/v1")
        api_key = _ask_secret("API key", "")
    elif choice.startswith(("3", "skip", "later", "no")):
        base_url = llm.get("base_url", "") or "http://YOUR_LLM_SERVER:4000/v1"
        api_key = llm.get("api_key", "")
    else:  # local (default)
        base_url = _ask(
            "Model server URL",
            llm.get("base_url", "") or "http://host.docker.internal:4000/v1",
        )
        api_key = _ask_secret("API key (local servers: not-needed)",
                              llm.get("api_key", "") or "not-needed")
    model = _ask("Model name / tag", llm.get("model", "") or "generic")
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model or "generic",
    }


# ── command entrypoint ─────────────────────────────────────────────────


def run(args) -> dict:
    agent_dir = Path(getattr(args, "dir", ".")).expanduser()
    if not agent_dir.is_absolute():
        agent_dir = Path.cwd() / agent_dir
    agent_dir = agent_dir.resolve()

    if not (agent_dir / REGISTRY_NAME).is_file():
        raise SetupError(
            f"{agent_dir} is not an agentboom agent (no {REGISTRY_NAME}). "
            "Run `agentboom init` first, or point at the agent directory."
        )
    registry = load_registry(agent_dir) or {}
    name = registry.get("name", agent_dir.name)

    no_prompt = (bool(getattr(args, "non_interactive", False))
                 or bool(getattr(args, "yes", False)))
    llm = {
        "base_url": (getattr(args, "llm_url", None) or "").strip(),
        "api_key": (getattr(args, "llm_key", None) or "").strip(),
        "model": (getattr(args, "llm_model", None) or "").strip(),
    }

    if no_prompt:
        # Scripted path: flags win, then AGENT_* env vars, else a placeholder
        # below. Never prompts, so it is safe in CI / a pipeline.
        for k, v in llm_from_env().items():
            if not llm[k]:
                llm[k] = v
    elif not llm["base_url"] or not llm["model"]:
        llm = interactive_llm(llm)

    tz_flag = (getattr(args, "timezone", None) or "").strip()
    if no_prompt:
        timezone = tz_flag or os.environ.get(ENV_TIMEZONE, "").strip()
    else:
        timezone = tz_flag or detect_timezone()
    if timezone:
        _set_profile_timezone(agent_dir, timezone)

    env_result = generate_env(agent_dir, llm)
    written = env_result["llm"]

    next_steps = [
        "docker compose up --build -d",
        "docker compose logs -f qwen-agent",
    ]
    if written["base_url"] == "http://YOUR_LLM_SERVER:4000/v1":
        next_steps.insert(
            0,
            "  # model not configured yet — re-run `agentboom setup` or edit "
            ".env / .qwen-docker/settings.json",
        )

    return {
        "ok": True,
        "agent_dir": str(agent_dir),
        "name": name,
        "env_written": True,
        "env_keys_set": env_result["env_keys_set"],
        "env_changed": env_result["env_changed"],
        "settings_written": env_result["settings_written"],
        "llm": written,
        "timezone": timezone,
        "next": next_steps,
    }
