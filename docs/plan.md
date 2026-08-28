# AgentBoom product plan

The plan for turning a personal self-evolving assistant into a reusable
per-user product. This file is the single source of truth for the program —
status, decisions, queue, and the rules every piece of work must respect.
(Updated 2026-08-28.)

## Vision

- **One Docker instance per user.** Each user gets their own agent that
  grows with their requests — professional life, private life, eventually
  family. The user is **not a developer**: simple language, simple
  onboarding, the agent does most of the setup itself.
- **Lean defaults, not an app center.** The default package set is a
  constitution, not a store. Everything else is installed on demand from
  the package registry (`agentboom add package`) — the agent checks the
  registry before coding its own capabilities.
- **Self-evolving.** The framework auto-updates; the instance agent diffs
  upstream code/docs regularly and makes verified self-improvements
  (the `self-evolve` package runs this nightly).
- **Everything reusable.** Every mini-app exposes capabilities others can
  reuse; shared building blocks get promoted into the SDK.

## Repos and their split

| Repo | Role |
|---|---|
| `../agentboom` (this one) | The growing generic framework: CLI, Python + TS SDKs, package/connector registry, platform + UI + dashboard templates, self-update. Fully refactorable — no backwards-compat constraints. |
| `../agentboom-agent` (to be created) | The per-user instance template: `agentboom init` + the curated default package set + one docker-compose per user (ports, DB choice, base URLs in per-instance `.env`). |

## Hard rules (enforced in code, not convention)

1. **Public boundary.** Exactly one public surface: frontend `/public/*`
   and API `/public/api/*`. Every other route is always
   Bearer-`PLATFORM_TOKEN`-protected (gateway auth middleware by path
   prefix). No flag, config, or env can weaken it; even an explicit user
   request to expose something elsewhere must be refused. Mini-apps get
   public routes only via explicit manifest declaration mounted under
   `/public/api/<app>/`.
2. **Dual database.** SQLite by default (users likely have no Postgres);
   PostgreSQL when `DATABASE_URI` is set. Portable-SQL doctrine:
   ISO-8601 UTC TEXT timestamps produced in Python, JSON stored as TEXT,
   `$n` placeholders (the db layer translates per dialect), no
   `NOW()`/`GREATEST`/`jsonb`/`text[]`/advisory locks, booleans as
   INTEGER 0/1, every migration ships a `.sql` **and** `.pg.sql` variant
   (enforced by test). Only per-dialect branch allowed: atomic claims
   (`INSERT ... ON CONFLICT ... RETURNING`).
3. **Zero personal data.** Nothing personal from the original assistant
   may leak into framework or instance templates. Every piece is
   verified with the leak grep (names, LAN IPs, addresses, personal
   identifiers) before commit.
4. **Queues.** Agent jobs run through the in-memory priority task queue
   (`max_concurrent=1`, `max_queue=20`) — no durable queue table.
   Cross-app reactions use the durable event bus: at-least-once HTTP
   delivery, dedupe keys, backoff, dead-letter, and deliveries carry the
   platform Bearer token.
5. **Work discipline.** Work is broken into small verified pieces: one
   piece = one commit, suite green, leak grep clean. Sub-agents run ONE
   AT A TIME (shared local LLM/GPU with the owner's sessions); paused
   sub-agents leave a handoff file so any agent can continue.

## Registry layout (decided 2026-08-28)

- `templates/packages/<slug>/` — addon packages (platform features).
- `templates/connectors/<slug>/` — connector packages (external-service
  integrations: credentials → vault, client lib under `platform/connectors/`).
  Kind is visible in the path.
- Discovery is recursive (depth 3): any registry (builtin, local path,
  remote git) may group packages into category subfolders later without
  another refactor. Not used yet — flat for now.
- Slugs: existing ones are stable (baked into routes/migrations/docs);
  **new packages require specific, self-evident slugs**.
- `requires` in the manifest is enforced at install time.

## Completed

Committed in this repo (see `git log`):

- Package/connector registry with builtin + path + git registries,
  `requires` enforcement, install rendering, `agentboom validate`.
- Durable event bus (SDK) + gateway wiring + bearer-token delivery fix.
- Dual-DB portable-SQL doctrine in SDK db layer + migration runner.
- Packages: `events`, `mfa-relay` (generic, time-critical code relay,
  sender-verified) + `email` auth_results, `telegram` (setup skill that
  teaches the agent the channel), `hello`, `settings`, `vault`,
  `email-actions`/`email-search`/`email-templates`, `calendar`, `google`,
  `ntfy`, `rss-feeds`, `weather`, `github-watch`, `digests`, `brain`,
  `contacts`, `knowledge`, `storage`, `reminders`, `documents`, `finance`,
  `rich-link`.
- **Commando**: the dashboard home is the command center (live stats,
  onboarding cards, agent-UI link, grouped nav, mobile select-header).
- Template bug fixes found via e2e: detached-fetch "Illegal invocation"
  in the UI client, empty-div GlobalStyles, `trailingSlash` breaking the
  dashboard proxy.
- `agentboom init` persists chosen host ports into `.env.example`.
- `connectors/` registry tree + recursive discovery (3ab6ee4).

## In flight

- **self-evolve** — all package files complete (8 flat tables, 29 routes,
  2 agent jobs, settings default OFF, nightly + weekly-meta jobs, repair
  loop, metrics, guardrails); test file being written by a sub-agent.

## Queue (FIFO, one sub-agent at a time)

1. **continente** (connector) — package 6/6 files done + validated on a
   scratch agent; remaining: test part 2 (spec in
   `/tmp/continente-handoff.md`) + resolve 1 failing parser test
   (`parse_order_date("17 Augosto 25")`), then move to
   `connectors/continente/` and commit. Parent does the live e2e.
2. **movienight** — brief at `/tmp/movienight-port-brief.md`.
3. **sdk-ts** — tests + dist build, scope `sdk-ts/` only.

## Remaining

1. **Release 0.8.0** — lockstep version bump (pyproject, sdk pyproject,
   ui + dashboard + sdk-ts package.json), release notes, docs sync, full
   suite green.
2. **agentboom-agent scaffold** — create the per-user instance repo via
   `agentboom init` + lean default package set (brain, knowledge, storage,
   vault, settings, contacts, calendar, reminders, events, email suite,
   digests, movienight, mfa-relay, self-evolve, telegram, ntfy, rich-link,
   continente) + compose; zero personal data. (init/validate/npm-build
   already verified in a /tmp scaffold.)
3. **Onboarding final pass** in the real agentboom-agent: Commando ✓,
   telegram-setup skill ✓, agent-UI link ✓, mobile ✓ — remaining: the
   first-run flow (join telegram + explain self-evolve, simple not strict).
4. **port-platform + edu-bot-cleanup** — BLOCKED on the other agent's
   feed-hub cutover in edu-bot (worktree dirty; migrations untracked).
   When free: one-way edu-bot → agentboom-agent migration snapshot from
   the **working tree**, digestos final state, empty-states, then
   de-hardcode edu-bot in place (env-ify assistant name, timezones, base
   URLs, channel name — no behavior change, no data loss).
5. **Handoff** — next-agent docs + skills in both repos (this file,
   the ops lessons below, per-package READMEs).
6. **Final verification** — full suite, `docker compose config`, zero-leak
   grep across both repos, dual-DB smoke, edu-bot still healthy (its
   containers keep running), and the MCP list delivered to the user
   (postgres-mcp, web-search `192.168.100.206:3115/mcp`,
   puppeteer/playwright, doc-extraction, sqlite option).

## Dropped (explicit decisions)

- money, invoices, compras suggestion engine, whatson, 88chess — not
  defaults, not ported. Continente survives **only** as a connector
  (cookies → vault; search/PDP/orders/cart; no login flow — Akamai
  blocks it).

## Ops lessons (for the next agent)

- **Sub-agents**: the `coder` type fails to launch here — use
  `general-purpose`. general-purpose agents can die mid-run (LLM request
  timeout when context bloats — one hit a 483s timeout at ~7M tokens):
  briefs must demand slim context (grep before reading, never dump big
  files). Diagnose via transcript tail + `git status`; relaunch clean
  with a continuation brief pointing at the on-disk state.
- **Servers**: persistent test servers only via managed background
  shells; `nohup &` children die at tool-call end. `sleep N; cmd` in one
  call is blocked — split calls. Manage the gateway by PID file, not
  `pkill -f` (self-match kills the calling shell).
- **Env**: always `env -u DATABASE_URI` on SDK commands (shell leaks the
  prod Postgres URI); re-`pip install` the SDK into `/tmp/ab-venv` after
  any SDK edit; seed SQLite **after** gateway start (migrations apply at
  startup).
- **Tests**: stdlib unittest via `tests/run_tests.py`; temp `DATA_DIR` +
  pop `DATABASE_URI` before importing `agentboom_sdk.db`; per-class event
  loop + helper named `run_async` (never `run`);
  `tearDownModule` resets `db._op_lock._lock`.
- **Template hygiene**: the installer copies EVERY file in a package —
  never leave `__pycache__` in `src/agentboom/templates/` (py_compile
  writes `.pyc` even with `PYTHONDONTWRITEBYTECODE=1`).
- **E2E harness**: test agent at `/tmp/ab-test-dash` (SQLite), gateway
  `env -u DATABASE_URI VAULT_KEY=<32 hex> PLATFORM_TOKEN=testtoken123
  PLATFORM_INTERNAL_URL=http://127.0.0.1:8130 DATA_DIR=/tmp/ab-test-dash/
  data MIGRATIONS_DIR=/tmp/ab-test-dash/platform/migrations uvicorn
  api_gateway:app --app-dir platform --port 8130`; re-copy package files
  into the dash after repo edits, restart the gateway, curl with
  `Bearer testtoken123`.
