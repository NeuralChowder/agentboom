# Roadmap

Where agentboom goes next, roughly in priority order.

## Near term

- **Upstream the reference instance's evolved modules** — `agent.py` v2
  session lifecycle (named sessions, caps, idle reaping) and the durable
  event bus are production-proven superset versions waiting to come home.
- **Dashboard in the scaffold** — `ui/` and `dashboard/` exist as a
  reference implementation; wire `manifest.ui` through the catalog payload
  and add an opt-in at init (`--with-dashboard`).
- **Platform MCP server template** — expose the gateway to external agents
  over MCP (catalog, job triggering, run queries).
- **More packages** — candidates extracted and proven in production:
  `browser-scraper` (HTML-first, screenshot-matrix fallback for canvas),
  `transcribe-audio`, `email-manager` + `email-account-setup` (build on
  the `vault` package), `integration-coordinator`, `calendar-manager`.

## Medium term

- **Multi-template support (designed-in)** — `templates/` supports sibling
  templates with their own `.agentboom-template.json`; e.g. a `chat-only`
  profile (agent home without the platform).
- **Skill distribution** — `agentboom add skill --from <git-url>` to
  install community skills into an agent, plus per-agent skill pinning.
- **PyPI publishing** — release workflow already produces wheels; add
  trusted publishing so `pip install agentboom-sdk` works without URLs.

## Later

- **TypeScript SDK: bridge tests + workspace** — `sdk-ts` ships the
  gateway bridge (agent/llm/db/capabilities) for Node mini-apps; the test
  suite and npm workspace wiring are still missing.
- **CI kit** — GitHub Actions workflow template: selfcheck + validate on
  every PR to an agent repo.

## Shipped

- v0.7.0 — dual-backend data layer in the SDK (SQLite default, PostgreSQL
  when `DATABASE_URI` is set, `.pg.sql` migration variants); reference
  dashboard + `@agentboom/ui` manifest renderer; `self-update` command;
  Google OAuth package (Gmail + Calendar, vault-stored tokens).

- v0.5.0 — fleet management: `adopt`, fleet registry + `agentboom fleet`,
  and `agentboom console` (the boomkeeper operator session). Wefts are
  indexed in `~/.agentboom/fleet.json` — an index, never a dependency.

- v0.3.0 — upstreamed the reference instance's tz-aware cron
  (dow 7 = Sunday) and the `accepted` envelope into the SDK; base
  scheduler passes SCHEDULER_TZ explicitly. Adoption in the reference
  personal-assistant instance started on branch `agentboom-sdk`
  (re-export shims + wheel pin, behaviour-verified in the live container).
- v0.2.1 — migrations-dir resolution fix for pip-installed SDK.
  A second production consumer fully migrated: vendored sdk removed,
  19/19 e2e cases green, deployed healthy.
- v0.2.0 — `agentboom-sdk` as an installable package (update the base in
  one place, agents take it via a pin bump); optional packages mechanism
  with `telegram`, `rich-link`, `vault`; GitHub release pipeline
  (tag → build wheels → release assets).
- v0.1.0 — CLI (init/validate/upgrade/add/list/doctor/selfcheck),
  platform template, 4 base skills, managed-file registry, test suite.

## Deliberately not planned

- No orchestrator/DSL: agents stay plain repos (Docker + Python + Qwen
  Code). agentboom scaffolds and maintains; it does not run anything.
- No runtime dependencies in the CLI, ever.
