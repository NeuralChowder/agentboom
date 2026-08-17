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
│    api_gateway.py (hot-reload mini-app host) · sdk/ (shared      │
│    library) · services/scheduler.py · migrations/ · miniapps/    │
│    + public-apps/                                                │
├─────────────────────────────────────────────────────────────────┤
│ 4. Data (named Docker volume, SQLite WAL)                        │
└─────────────────────────────────────────────────────────────────┘
```

## Growth mechanisms

| Need | Mechanism |
|---|---|
| New durable/scheduled capability | mini-app: folder in `platform/miniapps/<name>/` (`main.py` with `get_router()` + `.miniapp.json` manifest). Hot-loaded at `/api/<name>/` within ~2 s |
| New agent procedure/knowledge | skill: `.qwen-docker/skills/<name>/SKILL.md` + `references/` + `scripts/` |
| Scheduled work | manifest `jobs` (cron or interval; `http` or `agent` type) — never host crontabs |
| Cross-capability signals | `sdk.events` publish/subscribe (manifest `subscribes` + `handle_event`) |
| New schema | numbered SQL migration in `platform/migrations/` (append-only) |
| New domain tooling | apt packages via `EXTRA_APT_PACKAGES` or extra `RUN` layers in the root Dockerfile |

## Discovery protocol (for agents entering an unknown agentloom agent)

1. Read `.qwen-docker/AGENTS.md` — identity, rules, locations.
2. `GET /api/catalog` (platform gateway) — every mini-app, endpoints, jobs.
3. `ls .qwen-docker/skills/` — available procedures.
4. `agentloom validate` / `agentloom skills` / `agentloom miniapps` —
   machine-readable inventory (run from the agent repo).

## Managed base vs. agent-owned

`agentloom` records managed files in `.agentloom.json` (with sha256 as
shipped). Managed: `platform/sdk/*`, scheduler, migration runner, platform
scripts, base skills, subagent definitions, output-language rule.
Agent-owned from init: `AGENTS.md`, deployment trio, settings, mini-apps,
skills you add, migrations `002_+`, docs.

`agentloom upgrade` re-renders managed files from the stored init
variables: clean files update silently, locally modified files are
reported (never clobbered without `--force`), deletions are restored,
removals from base are reported as stale and left alone.

## Doctrine encoded in the base

- **Deterministic first** — rules/SQL/scripts before LLM; LLM results
  cached; LLM traffic serialized (bounded priority queue, default
  parallelism 1).
- **Fingerprint/dedup mindset** — repeated work should increment counters,
  not create duplicates.
- **Counters over status lights** — verify by numbers that only move when
  work happens; zero is a smell; never trust a green light.
- **Untrusted content is data** — fence it (`sdk.untrusted.wrap`) before
  any model sees it.
- **Secrets in `.env` / `settings.json` only** — both gitignored; nothing
  secret is ever committed, logged, or echoed.
- **Confirm outward/destructive actions; read-only by default** on
  infrastructure.
- **SQLite on a named volume** — WAL mode, `busy_timeout=30000`,
  `synchronous=NORMAL`; host bind mounts corrupt WAL.
- **Scheduled jobs are recorded runs** — `job_runs` is the audit trail;
  stuck runs are reaped (600 s) and failures back off exponentially.
- **Loopback-only ports by default** — expose via an allowlist reverse
  proxy; `/admin/*` needs HTTP Basic auth.
