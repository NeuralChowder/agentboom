---
name: knowledge
description: Store and retrieve durable facts in the knowledge mini-app. Use BEFORE asking the user something they already told you, and immediately after learning any fact worth keeping (preferences, serials, decisions, how-things-work).
---

# Knowledge — the agent's memory

Notes live in the platform database and survive restarts. The discipline
is the feature: **store on sight, search before asking.**

## API

```bash
BASE=http://endpoint-platform:8000/api/knowledge

curl -s "$BASE/notes?q=printer"                 # search titles/bodies/tags
curl -s "$BASE/notes?tag=home&limit=50"
curl -s -X POST $BASE/notes -H 'Content-Type: application/json' \
  -d '{"title": "UPS shuts down the NAS gracefully",
       "body": "NUT on the NAS listens to the UPS; 70% -> clean shutdown.",
       "tags": ["homelab", "power"], "source": "user, 2026-08"}'
curl -s -X PUT $BASE/notes/12 -H 'Content-Type: application/json' \
  -d '{"body": "corrected text"}'
curl -s -X DELETE $BASE/notes/12
curl -s $BASE/tags
```

## Rules

- Search before asking the user anything factual; ask only what is
  genuinely unknown.
- Store: preferences, account/service facts, serial numbers, decisions
  and their reasons, how-a-thing-works. One fact per note, titled so a
  search will find it.
- Never store secrets (that is the vault's job) or anything the user
  asked you to forget — and when asked to forget, delete the note.
- Update stale notes rather than duplicating; the tags endpoint shows
  the vocabulary already in use.
