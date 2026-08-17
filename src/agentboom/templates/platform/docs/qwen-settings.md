# Wiring `.qwen-docker/settings.json`

`settings.json` is the Qwen Code runtime config for this agent: models,
approval mode, MCP servers, and channels. It carries secrets, so it is
**gitignored** — copy `settings.example.json` and edit:

```bash
cp .qwen-docker/settings.example.json .qwen-docker/settings.json
```

## Blocks

### `env`
Key/value pairs exported into the agent environment and referenced by
`envKey` in model providers. Put API keys here (never inline in blocks).

### `tools.approvalMode`
- `"yolo"` — execute tools without asking (the usual choice inside a
  container whose blast radius you control).
- Other modes prompt for approval — only practical with an interactive
  terminal attached.

### `modelProviders.openai[]`
Any OpenAI-compatible endpoint (self-hosted gateway, cloud API):

```json
{
  "id": "generic",
  "name": "generic",
  "baseUrl": "http://your-llm-gateway:4000/v1",
  "envKey": "MY_LLM_API_KEY",
  "generationConfig": { "contextWindowSize": 128000 }
}
```

`model.name` selects the default provider id; `fastModel` (optional)
selects a cheaper model for background work.

### `mcpServers`
MCP servers the agent can use. Two transports:

```json
{
  "puppeteer": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
    "type": "stdio"
  },
  "web-search": {
    "httpUrl": "http://your-search-service:3115/mcp",
    "trust": true
  }
}
```

stdio servers run inside the agent container (the tool binaries must be
installed there — add them in the root `Dockerfile`); http servers are
remote.

### `channels` (optional)
Chat channels (e.g. Telegram) served by `qwen serve`. Example shape:

```json
{
  "channels": {
    "my-telegram": {
      "type": "telegram",
      "token": "123456:BOT-TOKEN",
      "senderPolicy": "allowlist",
      "allowedUsers": ["<telegram-user-id>"],
      "sessionScope": "user",
      "cwd": "/home/user",
      "instructions": "Short channel-specific behaviour notes."
    }
  }
}
```

Enable with `QWEN_SERVE_ARGS=--channel all` in `.env`. Keep the allowlist
strict — a channel is a remote shell into this agent.

## After editing

Restart the agent container: `docker compose restart qwen-agent`.
