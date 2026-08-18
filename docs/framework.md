# Framework architecture

How agentboom lets you build capabilities once and reuse them safely —
across mini-apps, across languages, and across databases — without
hand-wiring URLs or duplicating logic.

## The two mini-app runtimes

Every capability is a mini-app: a folder in `platform/miniapps/<name>/`
with a manifest (`.miniapp.json`) and an entry point. The gateway
hot-loads it at `/api/<name>/` within ~2s. Two languages are supported:

| `"language"` | Entry point | Runs as |
|---|---|---|
| `python` (default) | `main.py` exporting `get_router()` | in-process FastAPI router |
| `node` | `main.mjs` / `main.js` | managed child process; `/api/<name>/*` is proxied to it |

**Python** mini-apps return a `fastapi.APIRouter` and may also export
`async def handle_event(event)` for event subscriptions.

**Node** mini-apps start an HTTP server on the `PORT` env var the gateway
assigns; the gateway proxies the public path to it and passes through
`MINIAPP_NAME`, `PLATFORM_INTERNAL_URL`, `DATA_DIR`, `QWEN_*`, `LLM_*`,
`DATABASE_URI`. The gateway owns the process lifecycle (start, restart on
change, stop on unload). Node apps talk to the shared brain through the
TypeScript bridge SDK (below) — never by re-implementing it.

## Capabilities: reuse without hard-wiring

A mini-app can **expose** a capability and **consume** others', declared
in the manifest — no hard-coded URLs:

```json
{
  "provides": [
    {"name": "contacts.lookup", "endpoint": "POST /lookup",
     "description": "resolve a name or email to contacts"}
  ],
  "uses": ["contacts.lookup"]
}
```

- At load, the gateway builds one **capability registry** from every
  loaded manifest and serves it at `GET /api/capabilities`.
- It validates every `uses` against the registry. Anything missing is
  reported (with the app that needs it) in the catalog and
  `/admin/status` — an actionable "install the package that provides it",
  not a silent 404 later.
- Multiple providers of one name: first wins, the conflict is reported.

Callers resolve + call through the SDK, never via a literal URL:

```python
from agentboom_sdk.capabilities import call, CapabilityError
try:
    result = await call("contacts.lookup", {"text": "Maria"})
except CapabilityError as exc:
    ...  # message explains exactly what's missing and why
```

### Two layers of dependency safety

- **Package `requires`** (install-time): `agentboom add package` refuses to
  install a package whose dependencies aren't present, with the exact
  commands to fix it. You can't install something that can't work.
- **Manifest `provides`/`uses`** (run-time): the gateway maps who offers
  what and flags anything unsatisfied. Consumers degrade gracefully at the
  call site when a capability is absent.

## One brain, every language (the bridge)

Shared logic lives **once**, in the Python platform. TypeScript mini-apps
get it through loopback-HTTP bridge endpoints on the gateway:

| Endpoint | Backs | Python source |
|---|---|---|
| `POST /api/bridge/db` | `db.execute/fetchOne/fetchAll/fetchVal/batch` | `agentboom_sdk.db` |
| `POST /api/llm/complete` | one-shot completions (+JSON extraction) | `agentboom_sdk.llm` |
| `POST /api/agent/ask` | agent turns (session + SSE) | `agentboom_sdk.agent` |
| `GET  /api/capabilities` | capability registry | gateway |

`@agentboom/sdk` (see `sdk-ts/`) is a thin, dependency-free client onto
these — a bridge, not a re-implementation. Scheduling (cron) is owned by
the gateway scheduler for **all** mini-apps: declare `jobs` in the
manifest, serve the target endpoint; no cron parsing in any language.

## LLM configuration — one place, zero fuss

One-shot completions (`agentboom_sdk.llm`) read three env vars set in the
agent's `.env`, passed to the platform container:

```
LLM_BASE_URL=...   # any OpenAI-compatible endpoint
LLM_API_KEY=...
LLM_MODEL=...      # your model tag
```

Point these at your own local server (llama.cpp / vLLM / Ollama / LiteLLM
— `http://host.docker.internal:8080/v1`, already mapped via
`extra_hosts`) or a hosted API. Verify wiring without leaving the agent:

```
GET  /api/llm/health   # is it configured?
POST /api/llm/test     # one tiny completion
```

Mini-apps that reason degrade gracefully when no LLM is configured;
everything else still works.

## One data layer, two backends

`agentboom_sdk.db` speaks **SQLite by default** (zero setup, file on the
data volume) and **PostgreSQL when `DATABASE_URI` is set**. Mini-apps and
migrations should be written backend-neutral:

- Use `?` or `$n` placeholders — the layer rewrites per backend.
- No `AUTOINCREMENT`, no dialect date functions — compute dates in code.
- A migration may ship `NNN_name.pg.sql` next to `NNN_name.sql`; the
  runner uses the `.pg.sql` variant on PostgreSQL agents.

Users never need Postgres; agents that run it (e.g. a personal-assistant
on asyncpg) get the same code for free.

## Where things live

```
platform/
  api_gateway.py        hot-reload host, capability registry, bridge endpoints
  connectors/<name>/    importable service clients (email, caldav, ntfy, ...)
  miniapps/<name>/      python (main.py) or node (main.mjs) mini-apps
  migrations/           numbered SQL, base + optional .pg.sql variants
sdk/                    agentboom_sdk (Python) — the source of all shared logic
sdk-ts/                 @agentboom/sdk (TypeScript) — thin bridge client
```
