---
name: miniapp-dev
description: Discover, consume, and build platform mini-apps — the manifest contract, hot-reload semantics, jobs, events, and the verify loop. Use whenever work involves /api/* endpoints or creating a capability.
---

# Mini-app Development

Mini-apps are how this agent grows durable, scheduled capabilities.
A folder in `platform/miniapps/<name>/` becomes `/api/<name>/` within
~2 seconds — no restart, no redeploy.

## Discover first

```bash
curl -s http://endpoint-platform:8000/api/catalog | python3 -m json.tool
curl -s http://endpoint-platform:8000/api/agent/brief
```

If a capability already exists, consume or extend it — never duplicate.

## The contract

- `main.py` exports `get_router()` returning a FastAPI `APIRouter`.
  It may also export `async def handle_event(event)` for subscriptions.
- `.miniapp.json` manifest: `name`, `description`, `version`, `status`,
  `jobs[]`, `subscribes[]`, `ui`.
- Import **only** from `agentloom_sdk.` — never other mini-apps, never the gateway.

### Jobs (scheduling)

```json
{"name": "scan", "type": "http", "target": "scan/run", "cron": "*/30 * * * *", "enabled": true}
{"name": "review", "type": "agent", "prompt": "...", "cron": "0 9 * * *", "enabled": true}
```

- `http` jobs POST the app's own endpoint — keep targets idempotent and
  fast; do long work in the background.
- `agent` jobs run an agent turn with the prompt.
- Runs land in `job_runs`; failures back off. Check them when debugging.

### Events

`subscribes: ["some.event"]` + `handle_event(event)` receives in-process
events published with `await agentloom_sdk.events.publish("some.event", {...})`.

## Build loop

1. Scaffold: `agentloom add miniapp <name>` (or copy `miniapps/hello`).
2. Implement routes; keep handlers fast.
3. Save — the gateway hot-reloads within ~2 s.
4. **Verify**: app appears in `/api/catalog`; endpoints answer;
   `/admin/status` (Basic auth) shows no load errors; job rows appear in
   `schedule_jobs` with sane `next_run`.
5. If the app failed to load, the error's tail is in `/admin/status` →
   `load_errors`. Fix and save again; reload is automatic.

## Notes

- The watcher skips `__pycache__`; editing `.pyc` files does nothing.
- Removing the folder unloads the app and deregisters its jobs.
- New tables go in new numbered migrations (`platform/migrations/`),
  applied at gateway start: `python migrations/run.py`.
