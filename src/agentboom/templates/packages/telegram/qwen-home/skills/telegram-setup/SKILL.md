---
name: telegram-setup
description: Configure (or reconfigure) the Telegram channel so the user can talk to the agent from their phone. Use when the user wants Telegram set up, says they created a bot, provides a bot token, or asks why Telegram is not working.
---

# telegram-setup — get the Telegram channel working

The channel transport is Qwen Code's channel system: `qwen serve --channel all`
reads the `channels` block of `.qwen-docker/settings.json`. Full reference:
`docs/telegram-channel.md` (installed with the telegram package).

## Check state first

Before asking the user for anything, find out what already exists:

1. `grep -n "TELEGRAM_BOT_TOKEN" .env .env.example 2>/dev/null` — token present?
2. Read `.qwen-docker/settings.json` — is there a `channels` block? Which
   channel names, what `allowedUsers`?
3. `grep -n "QWEN_SERVE_ARGS" .env` — does it contain `--channel all`?

Report what is missing; do the missing steps in order below.

## Step 1 — the bot (the user does this part)

If there is no token, walk the user through it, one step at a time:

1. On their phone, open Telegram and message **@BotFather** → send `/newbot`
   → choose a name → copy the token it returns (`123456789:AA...`).
2. Message **@userinfobot** → it replies with their numeric user id.

Ask for both. The token is a secret: never echo it back in chat, never write
it into a page, never commit it.

## Step 2 — .env

```
TELEGRAM_BOT_TOKEN=<token>
QWEN_SERVE_ARGS=--channel all
```

If `QWEN_SERVE_ARGS` already exists, append `--channel all` instead of
replacing. Keep a matching (empty) line in `.env.example` so fresh copies
know the variable exists.

## Step 3 — settings.json `channels` block

Merge (do not clobber) a block shaped like:

```json
"channels": {
  "<agent>-telegram": {
    "type": "telegram",
    "token": "<token>",
    "senderPolicy": "allowlist",
    "allowedUsers": ["<user id>"],
    "sessionScope": "user",
    "cwd": "/home/user",
    "groupPolicy": "disabled"
  }
}
```

- One channel entry per user is enough; keep the existing block if the user
  re-runs setup — update the token/user id, keep everything else.
- `allowedUsers` is a REMOTE SHELL allowlist. Exactly the user's id, nothing
  else. Never add ids the user has not explicitly given you.

## Step 4 — restart and verify

```bash
docker compose restart qwen-agent
docker compose logs qwen-agent --tail 50
```

Look for the channel registration line. Then tell the user to send the bot a
message; if the agent does not answer within a couple of minutes, re-check the
logs for registration errors before suspecting the token.

## Rules

- Do not print the token or the user id in your replies.
- If the user cannot create a bot right now, leave everything else in place
  and say exactly which step is pending — setup must be resumable.
- If `settings.json` has no `channels` key at all, create it; if the file is
  malformed, stop and show the user the parse error instead of overwriting.
