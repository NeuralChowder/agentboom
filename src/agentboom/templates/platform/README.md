# {{AGENT_TITLE}}

{{AGENT_DESCRIPTION}}.

Built with [agentboom](https://github.com/NeuralChowder/agentboom) — the agent base
provides the runtime skeleton (agent home, platform, deployment); everything
domain-specific in this repo grows on top of it.

## Quickstart

The easy path — one wizard asks a few plain questions, generates the secrets,
and writes a ready `.env` + `settings.json`:

```bash
agentboom setup                 # interactive (asks about your model + timezone)
docker compose up --build -d
docker compose logs -f qwen-agent
```

For a script or CI (no prompts — driven by env vars):

```bash
AGENT_LLM_URL=http://host.docker.internal:4000/v1 \
AGENT_LLM_MODEL=generic \
AGENT_LLM_API_KEY=not-needed \
agentboom setup --non-interactive
```

Or do it all at `init` time: `agentboom init <dir> --generate-env
--llm-url ... --llm-model ...`. If you prefer to fill things in by hand:
`cp .env.example .env`, then copy `.qwen-docker/settings.example.json` to
`.qwen-docker/settings.json` and edit the model provider.

- Agent HTTP API: `http://127.0.0.1:{{PORT_AGENT}}`
- Dashboard: `http://127.0.0.1:{{PORT_DASHBOARD}}`
- Platform gateway: `http://127.0.0.1:{{PORT_PLATFORM}}`
  (catalog: `/api/catalog`, dashboard-less API docs: `/docs`)

Ports are loopback-only by default. Expose externally via a reverse proxy
with an allowlist — the gateway's hard public boundary (bearer token on
every non-public route, `/public/*` the only open surface) is the real
protection.

## Structure

```
├── Dockerfile              # agent container (Qwen Code + tools)
├── docker-compose.yml      # qwen-agent + endpoint-platform + dashboard
├── entrypoint.sh           # agent container boot (skill deps, prune, qwen serve)
├── package.json            # frontend workspaces (ui + dashboard)
├── .env.example            # secrets template (never commit .env)
├── .qwen-docker/           # the agent's home (mounted as ~/.qwen in-container)
│   ├── AGENTS.md           # ★ the agent's operating manual
│   ├── settings.example.json
│   ├── agents/             # subagent definitions
│   ├── skills/             # skills (SKILL.md + scripts)
│   └── memories/           # persistent memory index
├── ui/                     # design system + manifest renderers (@agentboom/ui)
├── dashboard/              # Next.js dashboard (catalog-driven)
└── platform/               # mini-app platform (see platform/README.md)
    ├── api_gateway.py      # hot-reload mini-app host
    ├── migrations/         # numbered SQL migrations
    └── miniapps/           # capabilities, hot-loaded at /api/<name>/
```

The shared runtime machinery (db, agent, llm, cron, task queue, events,
scheduler) is the `agentboom-sdk` package — pinned in
`platform/requirements.txt`, imported as `agentboom_sdk`.

## Operating the agent

- The agent's behaviour is defined by `.qwen-docker/AGENTS.md` — edit it
  to give this agent its identity and rules.
- Add capabilities: mini-apps (`platform/miniapps/`) for durable/scheduled
  work, skills (`.qwen-docker/skills/`) for agent procedures.
- `agentboom validate` checks structure; `agentboom upgrade` syncs the
  managed base files when the upstream base evolves.

See `docs/architecture.md` for the full tour and `docs/qwen-settings.md`
for wiring models, MCP servers, and channels.
