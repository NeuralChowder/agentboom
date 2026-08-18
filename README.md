<p align="center">
  <img src="assets/logo.png" alt="agentboom logo" width="180">
</p>

<h1 align="center">agentboom</h1>

<p align="center"><a href="https://agentboom.dev">agentboom.dev</a> ·
**Scaffold, maintain, and upgrade autonomous agent projects from a shared,
evolving base.**

agentboom turns the anatomy that production agents converge on — an agent
home (operating manual, skills, subagents), a Python mini-app platform
(hot-reload gateway + SQLite-backed scheduler), and a Docker deployment —
into a single command:

```bash
agentboom init my-agent
```

New capabilities then arrive as folders, not redeploys: **mini-apps** for
durable/scheduled work, **skills** for agent procedures. The shared runtime
machinery ships as the **`agentboom-sdk`** package: bump one pin in
`requirements.txt`, rebuild, and every agent takes the base update.

Two distributables, one version:

| Package | What it is |
|---|---|
| `agentboom` | the CLI: scaffolds and maintains agents (pure stdlib, zero deps) |
| `agentboom-sdk` | the runtime SDK agents import (`agentboom_sdk.*`) |

## Install

Requires **Python 3.10+**. Works on Linux, macOS, and Windows.

**Recommended — `pipx`** (isolates the install and puts `agentboom` on PATH):

```bash
pipx install .
# or straight from a GitHub release:
pipx install "agentboom @ https://github.com/NeuralChowder/agentboom/releases/download/v0.7.0/agentboom-0.7.0-py3-none-any.whl"
```

**No `pipx`? Plain `pip` works too:**

```bash
pip install "agentboom @ https://github.com/NeuralChowder/agentboom/releases/download/v0.7.0/agentboom-0.7.0-py3-none-any.whl"
```

`pip` may put the `agentboom` command somewhere not on your `PATH`
(`~/.local/bin` on Linux, `~/Library/Python/3.x/bin` on macOS,
`%APPDATA%\Python\...\Scripts` on Windows). Two easy fixes:

- **Run it as a module — always works, no PATH setup:**
  ```bash
  python3 -m agentboom ...     # Linux / macOS
  python  -m agentboom ...     # Windows
  ```
- **Or put pip's scripts dir on PATH once**, then use `agentboom` directly
  (e.g. `export PATH="$HOME/.local/bin:$PATH"` on Linux).

## Quickstart

```bash
agentboom init my-agent --description "watches my homelab and fixes things"
cd my-agent
cp .env.example .env              # fill in the required secrets
# copy .qwen-docker/settings.example.json -> settings.json and wire your models
docker compose up --build -d
docker compose logs -f qwen-agent
```

## Commands

| Command | What it does |
|---|---|
| `agentboom init <dir>` | scaffold a new agent from the base template |
| `agentboom validate [dir]` | structural health checks (files, manifests, cron, env coverage, drift) |
| `agentboom upgrade [dir]` | check/apply sync of managed base files (`--apply`, `--force`) |
| `agentboom add skill\|miniapp <name>` | scaffold a capability inside an agent |
| `agentboom code miniapp\|skill <name> ["prompt"]` | scaffold then open `qwen` with a mission to build it |
| `agentboom add package <name>` | install an optional package (see below) |
| `agentboom packages [dir]` | list available/installed packages across registries |
| `agentboom registries [list\|add\|remove]` | package sources: builtin + any git repo or dir |
| `agentboom skills [dir]` / `miniapps [dir]` | list an agent's capabilities |
| `agentboom list [parent]` | discover agentboom agents under a directory |
| `agentboom adopt [dir]` | bring an existing agent under base management (byte-match, non-destructive) |
| `agentboom fleet [status\|add\|remove]` | operator view: health + drift of every registered agent |
| `agentboom console` | open the boomkeeper operator session (pre-configured `qwen`) |
| `agentboom doctor` | environment checks (python, docker, compose, node) |
| `agentboom selfcheck` | end-to-end QA of the templates in a temp dir |

Every command supports `--json` for machine-readable output (stable payload
shape, meaningful exit codes) — see `docs/commands.md`.

## Optional packages

The base stays lean; repeatable integrations ship as packages installed
into any agent with `agentboom add package <name>`:

| Package | Adds |
|---|---|
| `telegram` | interactive Telegram channel wiring (docs + env) |
| `rich-link` | shortlinks mini-app + skill: long answers as mobile-friendly HTML pages |
| `vault` | AES-256-GCM credential store mini-app + migration + manager skill |

Scheduling/routines are already in the base (manifest jobs + scheduler).

## Managing the fleet

`agentboom init` registers a new agent in the fleet automatically;
existing agents join with `agentboom adopt`. The registry
(`~/.agentboom/fleet.json`) is an index, never a dependency — every
agent runs standalone even if agentboom disappears.

- `agentboom fleet` — per-agent health: base version, drift, validate
  errors, installed packages.
- `agentboom console` — materializes the **boomkeeper** operator
  workspace (operator manual, `fleet-ops` skill, live fleet snapshot) and
  launches `qwen` inside it: a session pre-configured to create, update,
  and manage agents with the full agentboom playbook.

## What's in the base

```
my-agent/
├── Dockerfile, docker-compose.yml, entrypoint.sh   # deployment trio
├── .env.example                                    # secrets template
├── .qwen-docker/                                   # the agent's home (~/.qwen in-container)
│   ├── AGENTS.md                                   # ★ the agent's operating manual
│   ├── settings.example.json                       # models / MCP / channels
│   ├── agents/                                     # coder, web-explorer, text-analyst, ...
│   ├── skills/                                     # base skills
│   └── memories/
└── platform/                                       # mini-app platform
    ├── api_gateway.py                              # hot-reload mini-app host
    ├── requirements.txt                            # ★ pins agentboom-sdk
    ├── migrations/                                 # numbered SQL migrations
    └── miniapps/hello/                             # working example capability
```

**Update model.** The SDK (db, agent client, llm, cron, task queue, event
bus, scheduler, untrusted-content fencing) is the `agentboom-sdk` wheel,
pinned in `platform/requirements.txt` to a GitHub release asset. Updating
the base = bump the pin + `docker compose up -d --build`. Template-owned
files (gateway, entrypoint scripts, base skills, subagents) are recorded in
`.agentboom.json` and synced with `agentboom upgrade` — with drift
detection, so nobody's local edits get clobbered.

The SDK encodes hard-won production doctrine: SQLite with WAL +
`busy_timeout` on a named volume (bind mounts corrupt WAL), LLM traffic
serialized through a bounded priority queue, `agent`/`llm` split for
multi-step vs one-shot model work, untrusted-content fencing, stale-run
reaping, exponential backoff. See `docs/anatomy.md`.

## Development

```bash
python3 tests/run_tests.py      # stdlib-only test suite
python3 bin/agentboom selfcheck # end-to-end template QA
```

Repo layout: `src/agentboom/` (CLI), `sdk/` (the agentboom-sdk package),
`src/agentboom/templates/` (agent template + packages). Read `AGENTS.md`
before changing templates. Roadmap: `docs/roadmap.md`.

## License

MIT
