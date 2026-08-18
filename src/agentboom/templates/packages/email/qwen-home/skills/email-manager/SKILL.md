---
name: email-manager
description: Manage the agent's mailboxes and read cached mail — add/test/remove mailboxes, check sync health, list or read messages. Use for anything about email accounts or already-received mail; the needs-attention queue lives in the email-actions app.
---

# Email Manager

The email package collects mail from configured mailboxes into a local
cache and publishes `email.received`. This skill covers mailboxes and
the cache; triage/replies are the `email-actions` app if installed.

## API

```bash
ACC=http://endpoint-platform:8000/api/email-accounts
SYNC=http://endpoint-platform:8000/api/email-sync

curl -s $ACC/providers                     # supported providers + notes
curl -s $ACC/accounts                      # mailboxes (never includes passwords)
curl -s -X POST $ACC/accounts -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "label": "personal",
       "provider": "gmail", "password": "the APP password"}'
curl -s -X POST $ACC/accounts/1/test       # sign in with the vault credential
curl -s -X DELETE $ACC/accounts/1          # stop collecting (cache is dropped)

curl -s -X POST $SYNC/sync                 # collect now
curl -s "$SYNC/emails?limit=20"            # newest cached mail
curl -s $SYNC/emails/123                   # one message with body
curl -s $SYNC/stats
curl -s "$SYNC/filters/skipped?limit=50"   # what the filters dropped (receipts)
```

## Rules

- **Passwords go straight to the vault.** The add-account endpoint vaults
  the password itself — never write it to a file, env, or reply, and
  never read it back out of the vault unless a step truly needs it.
- Gmail/Google Workspace: the user must create an **app password**
  (Google Account → Security → 2-Step Verification → App passwords).
- A filter that drops mail writes a receipt (`/filters/skipped`). When
  mail "goes missing", check the receipts before suspecting the sync.
- If `/stats` shows no syncs or an account shows `last_error`, run the
  test endpoint and report the exact error — do not guess.
