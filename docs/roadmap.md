# Roadmap

Where agentloom goes next, roughly in priority order. Items marked
**(designed-in)** already have their extension point in place.

## Near term

- **`agentloom adopt <dir>`** — bring an existing hand-built agent under
  base management: create `.agentloom.json`, map its SDK/scheduler files
  onto managed files, report the diff. The two production agents that
  seeded this project are the first candidates.
- **Platform MCP server template** — expose the gateway to external agents
  over MCP (catalog, job triggering, run queries), mirroring the pattern
  both seed agents converged on.
- **Vault module** — AES-256-GCM credential store (master key from env,
  audit-logged decrypts, blackout in public contexts), extracted from the
  personal-assistant agent. Skills that need credentials (email, OAuth)
  depend on this.
- **More base skills** — candidates extracted and proven in production:
  `browser-scraper` (HTML-first, screenshot-matrix fallback for canvas),
  `rich-link` (long answer → shareable HTML page; needs a shortlinks
  mini-app), `transcribe-audio`, `email-manager` + `email-account-setup`
  (once the vault lands), `integration-coordinator`.

## Medium term

- **Dashboard template** — manifest-driven Next.js admin UI (the
  personal-assistant agent's dashboard renders any mini-app's `ui` block
  with zero per-app code). Opt-in at init (`--with-dashboard`).
- **Channels templates** — Telegram (and generic webhook) channel wiring
  in `settings.example.json` + entrypoint, with strict sender allowlists.
- **Multi-template support (designed-in)** — `templates/` already supports
  sibling templates with their own `.agentloom-template.json`; e.g. a
  `chat-only` profile (agent home without the platform) or a `full`
  profile (platform + dashboard).
- **Skill distribution** — `agentloom add skill --from <git-url>` to
  install community skills into an agent, plus per-agent skill pinning in
  the registry.

## Later

- **TypeScript SDK twin** — for agents whose mini-apps are Node.
- **CI kit** — GitHub Actions workflow template: selfcheck + validate on
  every PR to an agent repo.
- **Base version policy** — semver for the base; `upgrade --to <version>`;
  changelog generation from template diffs.

## Deliberately not planned

- No orchestrator/DSL: agents stay plain repos (Docker + Python + Qwen
  Code). agentloom scaffolds and maintains; it does not run anything.
- No runtime dependencies in the CLI, ever.
