# ntfy — push notifications

[ntfy](https://ntfy.sh) is pub/sub over HTTP. Publishing a notification
is one POST to a *topic*; the phone app subscribed to that topic shows
it. No accounts, no API keys on the public server — which also means
**the topic name is the only secret**: pick something unguessable.

## Setup

1. `NTFY_TOPIC=alert-<yourname>-<random>` in `.env`.
2. Install the *ntfy* app (iOS/Android/F-Droid), subscribe to the same
   topic on `ntfy.sh` (or your server).
3. `docker compose restart endpoint-platform`.

Self-hosting (recommended long-term — topics then sit behind your own
auth): `docker run -p 2586:80 binwiederhier/ntfy serve`, then set
`NTFY_BASE_URL` (and `NTFY_TOKEN` if you enable access tokens).

## Use it

- Agent side: the `ntfy` skill (a curl one-liner).
- Mini-apps: `from connectors.ntfy import send` — see the connector
  docstring for all options (title, priority 1–5, emoji tags, click URL,
  attachments, scheduled sends).

## Notes

- Public ntfy.sh messages are visible to whoever knows the topic; do
  not include secrets in notification bodies.
- Priorities 4–5 may be rate-limited on the public server.
