# agentloom

**Scaffold, maintain, and upgrade autonomous agent projects from a shared,
evolving base.**

agentloom turns the anatomy that production agents converge on — an agent
home (operating manual, skills, subagents), a Python mini-app platform
(hot-reload gateway + SQLite-backed scheduler), and a Docker deployment —
into a single command:

```bash
agentloom init my-agent
```

New capabilities then arrive as folders, not redeploys: **mini-apps** for
durable/scheduled work, **skills** for agent procedures. The shared runtime
machinery ships as the **`agentloom-sdk`** package: bump one pin in
`requirements.txt`, rebuild, and every agent takes the base update.

Two distributables, one version:

| Package | What it is |
|---|---|
| `agentloom` | the CLI: scaffolds and maintains agents (pure stdlib, zero deps) |
| `agentloom-sdk` | the runtime SDK agents import (`agentloom_sdk.*`) |

## Install

```bash
pipx install .            # the agentloom command
# or straight from a GitHub release:
pipx install "agentloom @ https://github.com/ejbp/agentloom/releases/download/v0.2.0/agentloom-0.2.0-py3-none-any.whl"
```

## Quickstart

```bash
agentloom init my-agent --description "watches my homelab and fixes things"
cd my-agent
cp .env.example .env              # fill in the required secrets
# copy .qwen-docker/settings.example.json -> settings.json and wire your models
docker compose up --build -d
docker compose logs -f qwen-agent
```

## Commands

| Command | What it does |
|---|---|
| `agentloom init <dir>` | scaffold a new agent from the base template |
| `agentloom validate [dir]` | structural health checks (files, manifests, cron, env coverage, drift) |
| `agentloom upgrade [dir]` | check/apply sync of managed base files (`--apply`, `--force`) |
| `agentloom add skill\|miniapp <name>` | scaffold a capability inside an agent |
| `agentloom add package <name>` | install an optional package (see below) |
| `agentloom packages [dir]` | list available/installed packages |
| `agentloom skills [dir]` / `miniapps [dir]` | list an agent's capabilities |
| `agentloom list [parent]` | discover agentloom agents under a directory |
| `agentloom adopt [dir]` | bring an existing agent under base management (byte-match, non-destructive) |
| `agentloom fleet [status\|add\|remove]` | operator view: health + drift of every registered agent |
| `agentloom console` | open the loomkeeper operator session (pre-configured `qwen`) |
| `agentloom doctor` | environment checks (python, docker, compose, node) |
| `agentloom selfcheck` | end-to-end QA of the templates in a temp dir |

Every command supports `--json` for machine-readable output (stable payload
shape, meaningful exit codes) — see `docs/commands.md`.

## Optional packages

The base stays lean; repeatable integrations ship as packages installed
into any agent with `agentloom add package <name>`:

| Package | Adds |
|---|---|
| `telegram` | interactive Telegram channel wiring (docs + env) |
| `rich-link` | shortlinks mini-app + skill: long answers as mobile-friendly HTML pages |
| `vault` | AES-256-GCM credential store mini-app + migration + manager skill |

Scheduling/routines are already in the base (manifest jobs + scheduler).

## Managing the fleet

Agents built from agentloom are called **wefts** (the base is the loom's
*warp*; each agent is its own weft thread — the pattern it weaves is the
agent). `agentloom init` registers a weft automatically; existing agents
join with `agentloom adopt`. The registry (`~/.agentloom/fleet.json`) is
an index, never a dependency — every weft runs standalone even if
agentloom disappears.

- `agentloom fleet` — per-agent health: base version, drift, validate
  errors, installed packages.
- `agentloom console` — materializes the **loomkeeper** operator
  workspace (operator manual, `fleet-ops` skill, live fleet snapshot) and
  launches `qwen` inside it: a session pre-configured to create, update,
  and manage wefts with the full agentloom playbook.

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
    ├── requirements.txt                            # ★ pins agentloom-sdk
    ├── migrations/                                 # numbered SQL migrations
    └── miniapps/hello/                             # working example capability
```

**Update model.** The SDK (db, agent client, llm, cron, task queue, event
bus, scheduler, untrusted-content fencing) is the `agentloom-sdk` wheel,
pinned in `platform/requirements.txt` to a GitHub release asset. Updating
the base = bump the pin + `docker compose up -d --build`. Template-owned
files (gateway, entrypoint scripts, base skills, subagents) are recorded in
`.agentloom.json` and synced with `agentloom upgrade` — with drift
detection, so nobody's local edits get clobbered.

The SDK encodes hard-won production doctrine: SQLite with WAL +
`busy_timeout` on a named volume (bind mounts corrupt WAL), LLM traffic
serialized through a bounded priority queue, `agent`/`llm` split for
multi-step vs one-shot model work, untrusted-content fencing, stale-run
reaping, exponential backoff. See `docs/anatomy.md`.

## Development

```bash
python3 tests/run_tests.py      # 40 tests, stdlib only
python3 bin/agentloom selfcheck # end-to-end template QA
```

Repo layout: `src/agentloom/` (CLI), `sdk/` (the agentloom-sdk package),
`src/agentloom/templates/` (agent template + packages). Read `AGENTS.md`
before changing templates. Roadmap: `docs/roadmap.md`.

## License

MIT
