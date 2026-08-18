---
name: ntfy
description: Push a notification to the user's phone via ntfy. Use for alerts, heads-ups, and "ping me when done" requests — anything the user should see even when away from a screen.
---

# ntfy — push notifications

One POST publishes a notification to the configured topic; the user's
phone (subscribed to the same topic) shows it. No accounts, no keys.

## Send (from this container)

```bash
TOPIC="$NTFY_TOPIC"                       # set in the platform env
curl -s -d "Build finished ✅" "https://ntfy.sh/$TOPIC"
```

With title / priority / emoji tag:

```bash
curl -s -H "Title: backups" -H "Priority: high" -H "Tags: warning" \
     -d "Nightly backup FAILED on db-1" "https://ntfy.sh/$TOPIC"
```

Priority: `min`, `low`, `default`, `high`, `max` (use high/max only for
things that genuinely warrant waking someone).

## From a mini-app

```python
from connectors.ntfy import send
await send("Deploy done", title="deploys", priority=3, tags=["rocket"])
```

## Rules

- The topic name is a secret: never print it, put it in pages, or send
  it to anyone. Anyone with the topic can read every notification.
- Notify, don't spam: batch related facts into one message; never send
  more than a handful per incident.
- If NTFY_TOPIC is unset, tell the user push is not configured — do not
  fall back to emailing/messaging yourself.
