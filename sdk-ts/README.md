# @agentboom/sdk — the Node/TypeScript bridge

The TypeScript SDK is a **bridge, not a re-implementation**. Every piece
of shared logic — database access, LLM calls, agent turns, capability
resolution, cron scheduling — lives **once**, in the platform gateway
(Python). This package is a thin loopback-HTTP client onto it, so Node
mini-apps get full platform support with **zero duplicated logic** and
zero native dependencies.

```
┌──────────────────────────┐        loopback HTTP
│  Node mini-app (sidecar) │  ──────────────────────────────┐
│  main.mjs + @agentboom/sdk                               │
└──────────────────────────┘                               ▼
                              ┌──────────────────────────────────────────┐
                              │ platform gateway (one brain, Python)     │
                              │ /api/bridge/db   → agentboom_sdk.db      │
                              │ /api/llm/complete→ agentboom_sdk.llm     │
                              │ /api/agent/ask   → agentboom_sdk.agent   │
                              │ /api/capabilities→ capability registry   │
                              │ scheduler        → manifest cron jobs    │
                              └──────────────────────────────────────────┘
```

## Why a bridge and not a twin?

The shared logic is not a stateless library — the event bus, scheduler,
DB connections, capability registry and loaded apps all live **in the
running gateway process**. Re-implementing them per language would fork
the brain (and every bug fix would need a twin). A direct TS↔Python
bridge library would have the same problem: it spawns a *second* Python
runtime that can't see the gateway's state. The correct bridge is the
gateway's own HTTP surface — robust, boring, dependency-free, and it
works for any future language, not just TypeScript.

## Usage

```ts
import { db, llm, agent, capabilities } from "@agentboom/sdk";

const rows = await db.fetchAll("SELECT * FROM things WHERE x = ?", 1);
await db.batch([ // one transaction
  { sql: "INSERT INTO a VALUES (?)", params: [1] },
  { sql: "UPDATE b SET n = n + 1" },
]);

const text = await llm.complete("Summarise...");
const obj = await llm.completeJson("Extract...", {}); // parsed in the gateway

const answer = await agent.ask("What failed last night?");

const res = await capabilities.call("contacts.lookup", { text: "Maria" });
```

## Mini-app contract (Node)

A Node mini-app is a folder with `main.mjs` and `.miniapp.json`
(`"language": "node"`). The gateway spawns `node main.mjs` with:

| env | meaning |
|---|---|
| `PORT` | listen here (gateway proxies `/api/<name>/*` to it) |
| `MINIAPP_NAME` | the app's directory name |
| `PLATFORM_INTERNAL_URL` | the gateway base URL (SDK default) |
| + `DATA_DIR`, `QWEN_*`, `LLM_*`, `DATABASE_URI` | passed through |

Serve `/health` and your endpoints on `PORT`. Manifest `jobs` are invoked
by the gateway scheduler over HTTP — no cron parsing in Node.

## What lives where

| Concern | Where | Node gets it via |
|---|---|---|
| SQL / transactions | `agentboom_sdk.db` | `db.*` → `/api/bridge/db` |
| Migrations | gateway startup | automatic |
| LLM completions | `agentboom_sdk.llm` | `llm.*` → `/api/llm/complete` |
| Agent turns | `agentboom_sdk.agent` | `agent.*` → `/api/agent/ask` |
| Cross-app calls | capability registry | `capabilities.call` → `/api/capabilities` |
| Scheduling | gateway scheduler | manifest `jobs` |
