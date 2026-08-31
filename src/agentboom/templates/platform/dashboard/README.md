# Dashboard

A concrete, scalable **Next.js** dashboard for this agent. It is
*catalog-driven*: it fetches `/api/catalog` and renders whatever each
mini-app declares in its `ui` manifest. New capabilities appear with **no
frontend changes** — that is the scalability property.

Built on [`@agentboom/ui`](../ui/README.md) (the design system + manifest
renderers), so the look is token-driven and the screens (table / list /
form / stats) come from the shared library.

## Run it

In production it runs as the `dashboard` compose service (port
`${PORT_DASHBOARD:-3000}`). The source is bind-mounted, so a
`docker compose restart dashboard` serves the current source — the
entrypoint rebuilds ui + dashboard at container start.

For development from the repo root (npm workspaces link `@agentboom/ui`
automatically):

```bash
npm install                 # installs ui + dashboard
npm run build -w @agentboom/ui    # build the design system once
npm run dev:dashboard       # next dev on http://127.0.0.1:3000
```

## Authentication

The platform gateway enforces a **hard public boundary**: every non-public
route requires `Authorization: Bearer $PLATFORM_TOKEN`; only `/public/*` is
open. The dashboard is a thin same-origin proxy — it carries the token in
every request, inlined at build time from `NEXT_PUBLIC_PLATFORM_TOKEN`
(compose sets it from `PLATFORM_TOKEN`), and it also proxies `/public/*`
for the public surface. There is no way to weaken the boundary from here.

The proxy target defaults to the published loopback port
(`http://127.0.0.1:8000`); point it elsewhere with `AGENTBOOM_PLATFORM_URL`.

## How it scales

- The nav, views, columns, and actions all come from each mini-app's
  `.miniapp.json` `ui` field — the dashboard has no per-app code.
- `@agentboom/ui` types the manifest, so a malformed `ui` is caught at
  build time, not at runtime.
- Theming is tokens: swap the `Theme` passed to `<AgentBoomProvider>` to
  re-skin everything.

## Theming

Presets live in `app/theme.tsx` (Theme objects) and `app/globals.css`
(token values) — keep them in sync. The user's choice is one id
(`dawn`, `dusk`, or `<mode>-<accent>`, e.g. `dusk-ocean`), applied as
`data-theme` on `<html>` so the token values flip everywhere. It
persists in `localStorage` and is mirrored best-effort to
`profile.theme` via the settings mini-app; a boot script in
`app/layout.tsx` applies it before first paint. Default is light.
