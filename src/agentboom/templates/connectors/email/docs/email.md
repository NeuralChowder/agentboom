# Email — the foundation package

The email stack is three packages with honest dependencies:

| Package | What it adds | Requires |
|---|---|---|
| `email` | IMAP/SMTP connector + mailbox manager + sync engine | `vault` |
| `email-actions` | the "needs attention" queue with ready-to-send replies | `email` |
| `email-search` | search / ask questions over the cached mail | `email` |

`agentboom add package` refuses to install one whose dependencies are
missing — install `vault` first, then `email`, then whichever of
`email-actions` / `email-search` you want.

## How it works

1. **Mailboxes** (`/api/email-accounts/`): add a mailbox with provider
   presets (gmail, outlook, privateemail, fastmail, custom imap). The
   password is written to the vault and the mailbox is signed into
   *before* it is saved.
2. **Sync** (`/api/email-sync/`): every 5 minutes each mailbox is
   polled; new messages are cached in SQLite and published as
   `email.received` events. Ignore-filters drop mail *with receipts*
   (`/filters/skipped`), because a filter that drops silently is
   indistinguishable from a bug.
3. **Build on top**: mini-apps subscribe to `email.received`; agents
   read the cache over HTTP. The connector
   (`from connectors.email import fetch_new, send_for_account`) is
   available to any mini-app.

## Security posture

- Passwords: vault only, read just-in-time, audit-logged on every read.
- Bodies: cached locally in the agent's own database — nothing leaves
  for a third-party API.
- Sending: SMTP through the mailbox's own server; confirm-before-send
  is enforced by `email-actions` for anything user-visible.
