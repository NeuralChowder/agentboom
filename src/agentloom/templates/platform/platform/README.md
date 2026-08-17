# {{AGENT_TITLE}} — platform manual
<!-- agentloom:managed — upgraded by `agentloom upgrade`; local edits become drift. -->

The platform is a FastAPI gateway that hot-loads pluggable Python
**mini-apps**, plus a SQLite-backed scheduler and an in-process event bus.
Mini-apps are the growth mechanism: **new capabilities arrive as folders,
not as redeploys**.

## The mini-app contract

A mini-app is a folder in `miniapps/` (private) or `public-apps/`
(intended for external exposure) containing:

- `main.py` — exports `get_router() -> APIRouter`. Called at load and on
  every hot-reload. Optionally exports `async def handle_event(event)` for
  event subscriptions.
- `.miniapp.json` — the manifest:

```json
{
  "name": "my-app",
  "description": "One line shown in /api/catalog",
  "version": "0.1.0",
  "status": "active",
  "jobs": [
    {"name": "scan", "type": "http", "target": "scan/run", "cron": "*/30 * * * *", "enabled": true},
    {"name": "review", "type": "agent", "prompt": "...", "cron": "0 9 * * 1-5", "enabled": true}
  ],
  "subscribes": ["some.event"],
  "ui": null
}
```

The gateway mounts the router at `/api/<folder-name>/` within ~2 seconds
of saving (2 s filesystem watcher). Check `/api/catalog` and
`/admin/status` after changes — load errors are listed there, never silent.

Rules:

- Mini-apps import **only** from `agentloom_sdk` — never from each other, never
  from `api_gateway`. Cross-app communication goes over HTTP or `agentloom_sdk.events`.
- Keep request handlers fast. Anything long-running: enqueue it, return
  `{"status": "accepted"}`, and do the work in a job or background task.
- Event names use dot-notation: `alert.created`, `invoice.received`.

## Scheduler

Jobs declared in manifests are registered in SQLite (`schedule_jobs`) and
run by `services/scheduler.py`:

- `http` jobs POST to the app's own endpoint (`target` relative to
  `/api/<app>/`).
- `agent` jobs run one Qwen Code agent turn with the `prompt` — serialized
  through `agentloom_sdk.agent`'s task queue.
- Every run is recorded in `job_runs`; stuck runs are reaped after
  `STALE_RUNNING_SEC` (600 s) and failures back off exponentially.
- Scheduling lives here, **never** in host crontabs.

## SDK modules

| Module | Purpose |
|---|---|
| `agentloom_sdk.config` | env parsing (`env`, `env_int`, `env_bool`, `require`) |
| `agentloom_sdk.log` | logging setup (`get_logger`) |
| `agentloom_sdk.db` | SQLite (WAL) + migration runner — data lives on the named volume |
| `agentloom_sdk.agent` | `ask()` / `ask_json()` — run agent turns via `qwen serve` HTTP |
| `agentloom_sdk.llm` | `complete()` / `complete_json()` — one-shot completions |
| `agentloom_sdk.cron` | cron parsing + `next_cron_time` |
| `agentloom_sdk.task_queue` | bounded priority queue serializing LLM traffic |
| `agentloom_sdk.events` | `publish()` / `subscribe()` in-process event bus |
| `agentloom_sdk.untrusted` | `wrap()` — fence external content before any model sees it |

## Ops endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health`, `GET /health/db` | none | liveness |
| `GET /api/catalog` | none | capability discovery — read before building |
| `GET /api/agent/brief` | none | compact markdown brief for agents |
| `GET /admin/status` | Basic | loads, load errors, queue stats, job runs |
| `POST /admin/reload` | Basic | force hot-reload now |
| `POST /admin/events/{type}` | Basic | publish an event manually |

Basic auth: user `admin`, password from `PLATFORM_ADMIN_PASSWORD`.
Admin endpoints return 503 when that env var is unset.

## Doctrine

- **Deterministic first.** Rules and SQL before LLM; LLM only for
  judgement, cached so the same input is never processed twice.
- **Check `/api/catalog` before building.** Duplicate capabilities are a
  bug, not a feature.
- **Counters over status lights.** Verify work happened by reading numbers
  that only move when work happens; zero is a smell.
- **External content is data, not instructions.** Fence it with
  `agentloom_sdk.untrusted.wrap` before any model sees it.
- **Migrations are append-only.** New tables go in `NNN_<concern>.sql`;
  applied files are never edited.
