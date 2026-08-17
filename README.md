# agentloom

**Scaffold, maintain, and upgrade autonomous agent projects from a shared,
evolving base.**

agentloom turns the anatomy that production agents converge on — an agent
home (operating manual, skills, subagents), a Python mini-app platform
(hot-reload gateway, SDK, SQLite-backed scheduler), and a Docker deployment
— into a single command:

```bash
agentloom init my-agent
```

New capabilities then arrive as folders, not redeploys: **mini-apps** for
durable/scheduled work, **skills** for agent procedures. When the base
itself improves, `agentloom upgrade` syncs the managed files across every
agent — with drift detection, so nobody's local edits get clobbered.

Pure Python stdlib. No runtime dependencies — agents and CI can run it
anywhere `python3 >= 3.10` exists.

## Install

From a checkout (zero-install):

```bash
./bin/agentloom --help
```

Or install the `agentloom` command:

```bash
pipx install .        # or: pip install .
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
| `agentloom validate [dir]` | structural health checks (required files, manifests, cron, env coverage, drift) |
| `agentloom upgrade [dir]` | check/apply sync of managed base files (`--apply`, `--force`) |
| `agentloom add skill <name>` | scaffold a skill inside an agent |
| `agentloom add miniapp <name>` | scaffold a mini-app inside an agent |
| `agentloom skills [dir]` / `miniapps [dir]` | list capabilities of an agent |
| `agentloom list [parent]` | discover agentloom agents under a directory |
| `agentloom doctor` | environment checks (python, docker, compose, node) |
| `agentloom selfcheck` | end-to-end QA of the templates in a temp dir |

Every command supports `--json` for machine-readable output (stable payload
shape, meaningful exit codes) — see `docs/commands.md`.

## What's in the base

```
my-agent/
├── Dockerfile, docker-compose.yml, entrypoint.sh   # deployment trio
├── .env.example                                    # secrets template
├── .qwen-docker/                                   # the agent's home (~/.qwen in-container)
│   ├── AGENTS.md                                   # ★ the agent's operating manual
│   ├── settings.example.json                       # models / MCP / channels
│   ├── agents/                                     # coder, web-explorer, text-analyst, ...
│   ├── skills/                                     # base skills (managed, upgradable)
│   └── memories/
└── platform/                                       # mini-app platform
    ├── api_gateway.py                              # hot-reload mini-app host
    ├── sdk/                                        # db, agent, llm, cron, task_queue, events, ...
    ├── services/scheduler.py                       # SQLite-backed manifest job scheduler
    ├── migrations/                                 # numbered SQL migrations
    └── miniapps/hello/                             # working example capability
```

**Managed vs. user-owned:** files that belong to the shared base (SDK,
scheduler, migration runner, base skills, subagent definitions, ...) are
recorded in `.agentloom.json` with content hashes. `agentloom upgrade`
re-renders them from the template, overwrites clean copies, reports
locally-modified ones, restores deleted ones, and never deletes anything.
Everything else — `AGENTS.md`, compose, mini-apps, your migrations — is
yours from `init` onward.

The SDK encodes hard-won production doctrine: SQLite with WAL +
`busy_timeout` on a named volume (bind mounts corrupt WAL), LLM traffic
serialized through a bounded priority queue, `agent`/`llm` split for
multi-step vs one-shot model work, untrusted-content fencing, stale-run
reaping, exponential backoff. See `docs/anatomy.md`.

## Development

```bash
python3 tests/run_tests.py      # 32 tests, stdlib only
python3 bin/agentloom selfcheck # end-to-end template QA
```

Templates live in `src/agentloom/templates/`. Read `AGENTS.md` (this repo)
before changing them. Roadmap: `docs/roadmap.md`.

## License

MIT
