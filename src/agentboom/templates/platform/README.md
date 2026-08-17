# {{AGENT_TITLE}}

{{AGENT_DESCRIPTION}}.

Built with [agentboom](https://github.com/NeuralChowder/agentboom) — the agent base
provides the runtime skeleton (agent home, platform, deployment); everything
domain-specific in this repo grows on top of it.

## Quickstart

```bash
cp .env.example .env        # fill in the required secrets
docker compose up --build -d
docker compose logs -f qwen-agent
```

- Agent HTTP API: `http://127.0.0.1:{{PORT_AGENT}}`
- Platform gateway: `http://127.0.0.1:{{PORT_PLATFORM}}`
  (catalog: `/api/catalog`, dashboard-less API docs: `/docs`)

Ports are loopback-only by default. Expose externally via a reverse proxy
with an allowlist — the platform has no end-user auth of its own.

## Structure

```
├── Dockerfile              # agent container (Qwen Code + tools)
├── docker-compose.yml      # qwen-agent + endpoint-platform
├── entrypoint.sh           # agent container boot (skill deps, prune, qwen serve)
├── .env.example            # secrets template (never commit .env)
├── .qwen-docker/           # the agent's home (mounted as ~/.qwen in-container)
│   ├── AGENTS.md           # ★ the agent's operating manual
│   ├── settings.example.json
│   ├── agents/             # subagent definitions
│   ├── skills/             # skills (SKILL.md + scripts)
│   └── memories/           # persistent memory index
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
