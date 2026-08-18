---
name: needs-attention
description: Work the email needs-attention queue — show what waits on the user, read messages, send chosen replies, redraft, settle items. Use whenever the user asks about emails needing answers or wants to clear their queue.
---

# Needs Attention (email-actions)

Incoming mail is triaged into a short queue of decisions, each with
ready-to-send reply options. Your job: present the queue, never send
anything without the user choosing it.

## API

```bash
BASE=http://endpoint-platform:8000/api/email-actions

curl -s "$BASE/queue?limit=15"              # what waits on the user
curl -s $BASE/items/7                       # item + email + proposals
curl -s $BASE/items/7/message               # full cached message
curl -s -X POST $BASE/items/7/redraft -H 'Content-Type: application/json' \
  -d '{"instructions": "friendlier, mention Thursday"}'
curl -s -X POST $BASE/items/7/execute -H 'Content-Type: application/json' \
  -d '{"proposal_id": 12}'                  # SENDS the reply immediately
curl -s -X POST $BASE/items/7/done
curl -s -X POST $BASE/items/7/skip
curl -s $BASE/stats
curl -s -X POST $BASE/triage                # triage pending now
```

## Rules

- **Never call /execute without the user's explicit pick** — it sends
  real email through their mailbox. Quote the draft, ask, then execute.
- Present items urgency-first; include the sender, subject, and the
  triage reason. Offer the proposals as options.
- Redraft takes plain-language instructions ("shorter", "decline
  politely", "in Portuguese") — iterate as many times as the user wants.
- Done = handled outside the queue; Skip = not worth answering. Neither
  touches the mail itself.
- If /stats shows pending items piling up, trigger POST /triage.
