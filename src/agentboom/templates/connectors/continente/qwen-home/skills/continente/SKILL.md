---
name: continente
description: Set up and manage the Continente (continente.pt) session, and add products to the user's Continente cart on request. Use when the user says 'set up continente', asks to add something to the Continente cart, or when /api/continente reports a missing or expired session.
---

# continente — session setup + cart

The continente package gives the agent read access to continente.pt
(search, product pages) for free, and cart adds once a session is
stored. The session is a cookie jar from the user's real browser, kept
encrypted in the vault (service `continente.pt:cookies`). There is no
login flow — the site's IdP blocks non-browser clients, so the user's
browser is the only source of a session.

## Check state first

```bash
curl -s "${PLATFORM_API}/api/continente/status"
```

- `session: missing` → run the setup flow below.
- `session: stored` + `logged_in: true` → ready; confirm and move on.
- `session: stored` + `logged_in: false` → the session expired or was
  anonymous — re-run setup (same flow, fresh cookies).

## Setup flow

1. Ask the user to log in to **continente.pt** in a browser — their
   phone is fine for logging in, but copying the cookie needs devtools,
   so if they are on a phone, do this on a desktop/laptop instead.
2. Once logged in, have them open the devtools console on
   continente.pt and run this one-liner:

   ```js
   copy(document.cookie)
   ```

   (Chrome/Edge: copies the whole jar to the clipboard. Safari: use
   `console.log(document.cookie)` and copy the printed line.)
3. Ask them to paste the result back in chat. It is a single line like
   `sid=…; dwsid=…; cquid=…`.
4. Store it:

   ```bash
   curl -s -X POST "${PLATFORM_API}/api/continente/session" \
     -H 'Content-Type: application/json' \
     -d '{"cookies": "<the pasted string>"}'
   ```

5. Verify:

   ```bash
   curl -s "${PLATFORM_API}/api/continente/status"
   ```

   `logged_in: true` → confirm to the user that Continente is set up.
   `logged_in: false` → the cookies were not a logged-in session
   (logged out, or copied from the wrong tab) — ask them to re-copy
   from a tab where they are actually logged in (the header shows their
   name, and "As minhas moradas" lists their addresses).

## Using the session

- Search (no session needed):
  `curl -s "${PLATFORM_API}/api/continente/search?q=arroz"`.
- Add to cart: `POST ${PLATFORM_API}/api/continente/items/<pid>/add`
  with `{"quantity": 2}`. A 503 means the session is missing/expired —
  re-run setup, do not retry in a loop.
- Anything that spends money stays user-initiated: add to cart only
  when the user explicitly asks for that product, and read the cart
  back to them afterwards.

## Rules

- The cookie string is a SECRET: never print it back in chat, never
  write it to a file, never include it in a page or log. Refer to it
  as "the Continente session".
- If a later probe fails (`logged_in: false`), the session expired —
  sessions are short-lived; re-run the setup flow.
- The agent must not attempt to log in to continente.pt itself (username
  + password will not work — the IdP rejects non-browser clients).
