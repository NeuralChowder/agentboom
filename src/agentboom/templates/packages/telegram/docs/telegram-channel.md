# Telegram channel for {{AGENT_TITLE}}

Lets you talk to the agent from Telegram (mobile-friendly). Transport is
Qwen Code's channel system: `qwen serve --channel all` reads the
`channels` block of `.qwen-docker/settings.json`.

## 1. Create the bot

Message **@BotFather** on Telegram → `/newbot` → copy the token
(`123456789:AA...`) into `.env` as `TELEGRAM_BOT_TOKEN`.

## 2. settings.json — add the channels block

```json
{
  "channels": {
    "{{AGENT_NAME}}-telegram": {
      "type": "telegram",
      "token": "PASTE-TOKEN-OR-USE-ENV-REFERENCE",
      "senderPolicy": "allowlist",
      "allowedUsers": ["<your-telegram-user-id>"],
      "sessionScope": "user",
      "cwd": "/home/user",
      "groupPolicy": "disabled",
      "instructions": "You are talking to your owner over Telegram. Keep replies brief and skimmable; the user is on a phone. Check skills and MCP tools before acting; delegate long-running work to sub-agents."
    }
  }
}
```

- `allowedUsers`: message **@userinfobot** to get your numeric id. Keep
  this list strict — anyone in it can drive the agent.
- `sessionScope: "user"` keeps one conversation per user.
- Token in settings.json stays out of git (the file is gitignored).

## 3. Enable channels on the daemon

Already added to `.env.example` by this package:

```
QWEN_SERVE_ARGS=--channel all
```

## 4. Restart and test

```bash
docker compose restart qwen-agent
docker compose logs -f qwen-agent   # watch for the channel registration line
```

Send `/start`-style message to your bot; the agent answers per its
operating manual (`.qwen-docker/AGENTS.md`).

## Notes

- Long answers: pair this with the `rich-link` package so the agent can
  reply with a short summary + a readable page.
- Voice notes are not transcribed unless you add a transcription skill.
- If the channel misbehaves, check `docker compose logs qwen-agent` for
  registration errors before suspecting the bot token.
