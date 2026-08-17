# Architecture — {{AGENT_TITLE}}

## The two containers

```
                    ┌──────────────────────────────┐
                    │ qwen-agent                    │
   HTTP API :4170 ◄─┤  qwen serve (Qwen Code)       │
                    │  ~/.qwen: AGENTS.md, skills,  │
                    │  agents, memories, settings   │
                    └───────▲──────────────┬────────┘
                            │              │
              agentboom_sdk.agent.ask │              │ HTTP (mini-app endpoints)
            (sessions+SSE)  │              │
                    ┌───────┴──────────────▼────────┐
                    │ endpoint-platform              │
                    │  api_gateway (FastAPI)         │
                    │  mini-apps  ← hot reload (2 s) │
                    │  scheduler  ← SQLite jobs      │
                    │  events bus │ SQLite (WAL)     │
                    └────────────────────────────────┘
```

- **qwen-agent** is the brain: `qwen serve` running Qwen Code with this
  repo's `.qwen-docker/` as its home. It reads `AGENTS.md`, uses skills,
  and can call any mini-app endpoint over HTTP.
- **endpoint-platform** is the body: deterministic services, storage, and
  schedules. It can start agent turns over HTTP (`agentboom_sdk.agent.ask`) for
  work that needs judgement — e.g. an `agent`-type scheduled job.

Both share `QWEN_SERVER_TOKEN` (bearer auth) and the SQLite data volume.

## Request/data flows

**Scheduled work:** scheduler ticks → manifest job due →
`http` job POSTs the mini-app endpoint *or* `agent` job runs an agent
turn → result recorded in `job_runs`.

**Agent-initiated work:** agent (you) → `GET /api/catalog` to discover →
mini-app endpoints for data/actions → `agentboom_sdk.db` for direct reads when
appropriate.

**Growth:** drop a folder in `platform/miniapps/` → watcher digest
changes within 2 s → gateway re-imports and remounts → job manifest
re-registered. No restart at any point.

## Storage

- SQLite on a **named Docker volume** (`agent-data`): WAL mode,
  `busy_timeout=30000`, `synchronous=NORMAL`. Host bind mounts corrupt
  WAL databases — never move this.
- Migrations: numbered `.sql` files, immutable once applied, tracked in
  `_migrations`.

## Security posture

- Platform ports publish on **127.0.0.1 only**; expose via an allowlist
  reverse proxy if needed.
- `/admin/*` requires HTTP Basic auth (`PLATFORM_ADMIN_PASSWORD`,
  constant-time compare).
- Secrets live in `.env` / `settings.json` — both gitignored.
- External content (email, web, documents) is fenced with
  `agentboom_sdk.untrusted.wrap` before any model sees it: data, never instructions.
- The agent container runs as a non-root user; mounts of infrastructure
  (if you add any) should be read-only by default.

## Failure doctrine

- Job runs are append-only evidence; a stuck `running` run is reaped at
  600 s and marked failed — silent wedges are how outages hide.
- Failed jobs back off exponentially (60 s → 1 h cap).
- Mini-app load errors surface in `/admin/status` and `/api/catalog` —
  nothing fails silently.
- LLM traffic is serialized (default parallelism 1) so bursts never
  congest the model gateway.
