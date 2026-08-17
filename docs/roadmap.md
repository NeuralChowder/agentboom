# Roadmap

Where agentloom goes next, roughly in priority order.

## Near term

- **agentloom-sdk: Postgres backend** — `agentloom_sdk.db` speaks SQLite
  today; the personal-assistant agent (mycelium) runs PostgreSQL/asyncpg.
  A second backend unlocks its full adoption (db, then idle).
- **Upstream mycelium's evolved modules** — `agent.py` v2 session
  lifecycle (named sessions, caps, idle reaping) and `untrusted` injection
  scoring are production-proven superset versions waiting to come home.
- **Platform MCP server template** — expose the gateway to external agents
  over MCP (catalog, job triggering, run queries).
- **More packages** — candidates extracted and proven in production:
  `browser-scraper` (HTML-first, screenshot-matrix fallback for canvas),
  `transcribe-audio`, `email-manager` + `email-account-setup` (build on
  the `vault` package), `integration-coordinator`, `calendar-manager`.
- **Note for devops-monitor (infra-watch):** it is pinned to
  agentloom-sdk v0.2.1. v0.3.0's cron defaults its matching timezone to
  Europe/Lisbon — audit scheduler call sites (or set an explicit tz)
  before bumping the pin.

## Medium term

- **Dashboard template** — manifest-driven Next.js admin UI (the
  personal-assistant agent's dashboard renders any mini-app's `ui` block
  with zero per-app code). Opt-in at init (`--with-dashboard`).
- **Multi-template support (designed-in)** — `templates/` supports sibling
  templates with their own `.agentloom-template.json`; e.g. a `chat-only`
  profile (agent home without the platform).
- **Skill distribution** — `agentloom add skill --from <git-url>` to
  install community skills into an agent, plus per-agent skill pinning.
- **PyPI publishing** — release workflow already produces wheels; add
  trusted publishing so `pip install agentloom-sdk` works without URLs.

## Later

- **TypeScript SDK twin** — for agents whose mini-apps are Node.
- **CI kit** — GitHub Actions workflow template: selfcheck + validate on
  every PR to an agent repo.

## Shipped

- v0.5.0 — fleet management: `adopt`, fleet registry + `agentloom fleet`,
  and `agentloom console` (the loomkeeper operator session). Wefts are
  indexed in `~/.agentloom/fleet.json` — an index, never a dependency.

- v0.3.0 — upstreamed mycelium's tz-aware cron (dow 7 = Sunday) and the
  `accepted` envelope into the SDK; base scheduler passes SCHEDULER_TZ
  explicitly. Mycelium (edu-bot) adoption started on branch
  `agentloom-sdk` (re-export shims + wheel pin, behaviour-verified in the
  live container; deploy pending review).
- v0.2.1 — migrations-dir resolution fix for pip-installed SDK.
  devops-monitor (infra-watch) fully migrated: vendored sdk removed,
  19/19 e2e cases green, deployed healthy on owl.
- v0.2.0 — `agentloom-sdk` as an installable package (update the base in
  one place, agents take it via a pin bump); optional packages mechanism
  with `telegram`, `rich-link`, `vault`; GitHub release pipeline
  (tag → build wheels → release assets).
- v0.1.0 — CLI (init/validate/upgrade/add/list/doctor/selfcheck),
  platform template, 4 base skills, managed-file registry, test suite.

## Deliberately not planned

- No orchestrator/DSL: agents stay plain repos (Docker + Python + Qwen
  Code). agentloom scaffolds and maintains; it does not run anything.
- No runtime dependencies in the CLI, ever.
