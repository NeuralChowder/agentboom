# Agent anatomy — the shape agentloom scaffolds

Every agentloom agent has the same skeleton. This document is written so
that **any agent (or human) can navigate any agentloom-based agent** just
by reading it.

## Layers

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Deployment trio (repo root)                                   │
│    Dockerfile (agent container) · docker-compose.yml ·           │
│    entrypoint.sh · .env.example                                  │
├─────────────────────────────────────────────────────────────────┤
│ 2. Agent home  (.qwen-docker/  →  ~/.qwen inside the container)  │
│    AGENTS.md (operating manual) · settings.json (models/MCP/     │
│    channels) · agents/ (subagents) · skills/ (procedures) ·      │
│    memories/ (persistent memory index)                           │
├─────────────────────────────────────────────────────────────────┤
│ 3. Platform  (platform/)                                         │
│    api_gateway.py (hot-reload mini-app host) · migrations/ ·     │
│    miniapps/ + public-apps/ · requirements.txt                   │
│    runtime SDK: agentloom_sdk package (pip, pinned)              │
├─────────────────────────────────────────────────────────────────┤
│ 4. Data (named Docker volume, SQLite WAL)                        │
└─────────────────────────────────────────────────────────────────┘
```

## The runtime SDK (`agentloom_sdk`)

Installed as a package (pinned in `platform/requirements.txt` to a
GitHub release asset of ejbp/agentloom):

| Module | Purpose |
|---|---|
| `agentloom_sdk.config` | env parsing helpers |
| `agentloom_sdk.log` | logging setup |
| `agentloom_sdk.db` | SQLite (WAL) + migration runner |
| `agentloom_sdk.agent` | run agent turns via `qwen serve` HTTP (`ask`) |
| `agentloom_sdk.llm` | one-shot completions (`complete`, `complete_json`) |
| `agentloom_sdk.cron` | cron parsing + next-fire |
| `agentloom_sdk.task_queue` | bounded priority queue serializing LLM traffic |
| `agentloom_sdk.events` | in-process pub/sub bus |
| `agentloom_sdk.untrusted` | fence external content before models see it |
| `agentloom_sdk.services.scheduler` | SQLite-backed manifest job scheduler |

**Updating the base** = bump the pin + rebuild. Template-owned glue files
are synced with `agentloom upgrade` (drift-aware, never clobbers local
edits without `--force`).

## Growth mechanisms

| Need | Mechanism |
|---|---|
| New durable/scheduled capability | mini-app: folder in `platform/miniapps/<name>/` (`main.py` with `get_router()` + `.miniapp.json` manifest). Hot-loaded at `/api/<name>/` within ~2 s |
| New agent procedure/knowledge | skill: `.qwen-docker/skills/<name>/SKILL.md` + `references/` + `scripts/` |
| Scheduled work | manifest `jobs` (cron or interval; `http` or `agent` type) — never host crontabs |
| Cross-capability signals | `agentloom_sdk.events` publish/subscribe (manifest `subscribes` + `handle_event`) |
| New schema | numbered SQL migration in `platform/migrations/` (append-only) |
| Repeatable integration | `agentloom add package <name>` (telegram, rich-link, vault, ...) |
| New domain tooling | apt packages via `EXTRA_APT_PACKAGES` or extra `RUN` layers in the root Dockerfile |

## Discovery protocol (for agents entering an unknown agentloom agent)

1. Read `.qwen-docker/AGENTS.md` — identity, rules, locations.
2. `GET /api/catalog` (platform gateway) — every mini-app, endpoints, jobs.
3. `ls .qwen-docker/skills/` — available procedures.
4. `agentloom validate` / `skills` / `miniapps` / `packages` — machine
   inventory (run from the agent repo).

## Doctrine encoded in the base

- **Deterministic first** — rules/SQL/scripts before LLM; LLM results
  cached; LLM traffic serialized (bounded priority queue, default
  parallelism 1).
- **Counters over status lights** — verify by numbers that only move when
  work happens; zero is a smell; never trust a green light.
- **Untrusted content is data** — fence it (`agentloom_sdk.untrusted.wrap`)
  before any model sees it.
- **Secrets in `.env` / `settings.json` / the vault package only** —
  nothing secret is ever committed, logged, or echoed.
- **Confirm outward/destructive actions; read-only by default** on
  infrastructure.
- **SQLite on a named volume** — WAL mode, `busy_timeout=30000`,
  `synchronous=NORMAL`; host bind mounts corrupt WAL.
- **Scheduled jobs are recorded runs** — `job_runs` is the audit trail;
  stuck runs are reaped (600 s) and failures back off exponentially.
- **Loopback-only ports by default** — expose via an allowlist reverse
  proxy; `/admin/*` needs HTTP Basic auth.
