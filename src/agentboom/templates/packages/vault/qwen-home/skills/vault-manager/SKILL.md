---
name: vault-manager
description: Store, read, rotate, and audit credentials in the encrypted vault mini-app. Use whenever an integration needs a secret (API keys, passwords, tokens) — never hardcode or env-file secrets that belong in the vault.
---

# Vault Manager

The vault mini-app (`/api/vault/`) stores credentials AES-256-GCM
encrypted in SQLite; every decrypt is audit-logged. It exists so secrets
live in exactly one place — never in code, skills, commits, or chat.

## API

```bash
BASE=http://endpoint-platform:8000/api/vault

# store (never echo the secret back afterwards)
curl -s -X PUT $BASE/credentials/my-service \
  -H 'Content-Type: application/json' \
  -d '{"secret": "...", "note": "what this credential is for"}'

# read (audit-logged — only when actually needed)
curl -s $BASE/credentials/my-service

# list services (no secret material)
curl -s $BASE/credentials

# rotate
curl -s -X POST $BASE/credentials/my-service/rotate \
  -H 'Content-Type: application/json' -d '{"secret": "new-value"}'

# audit trail
curl -s "$BASE/audit?service=my-service&limit=50"
```

## Rules

- **Store on sight.** When a user hands you a credential or an integration
  needs one, it goes to the vault immediately; delete it from scratch
  files, shell history, and transcripts of your reasoning where practical.
- **Read just-in-time.** Fetch a secret only in the step that uses it.
  Never print, log, or include secrets in replies, pages, or error
  messages. Mask them (`abcd…wxyz`) if you must refer to them.
- **Name services semantically**: `email:google:oauth-refresh`,
  `hosting:digitalocean:api-token` — one credential per purpose.
- **Rotate proactively** when a credential may have been exposed, and
  check `/audit` to see who/what decrypted it.
- If `/health` reports `enabled: false`, `VAULT_KEY` is missing from the
  platform env — tell the user; do not work around it with plaintext.
