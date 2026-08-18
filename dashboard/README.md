# agentboom dashboard

A concrete, scalable **Next.js** dashboard for an agentboom agent. It is
*catalog-driven*: it fetches `/api/catalog` and renders whatever each
mini-app declares in its `ui` manifest. New capabilities appear with **no
frontend changes** — that is the scalability property.

Built on [`@agentboom/ui`](../ui/README.md) (the design system + manifest
renderers), so the look is token-driven and the screens (table / list /
form / stats) come from the shared library.

## Run it (development)

From the repo root (npm workspaces link `@agentboom/ui` automatically):

```bash
npm install                 # installs ui + dashboard
npm run build:ui            # build the design system once
npm run dev:dashboard       # next dev on http://127.0.0.1:3000
```

The dashboard proxies `/api/*` to the platform gateway (default
`http://127.0.0.1:8000` — the published loopback port), so no CORS setup is
needed. Point it elsewhere with `AGENTBOOM_PLATFORM_URL`:

```bash
AGENTBOOM_PLATFORM_URL=http://127.0.0.1:8000 npm run dev:dashboard
```

## Production

```bash
npm run build -w dashboard
npm run start -w dashboard
```

Run it behind the same reverse proxy as the platform if you expose it.

## How it scales

- The nav, views, columns, and actions all come from each mini-app's
  `.miniapp.json` `ui` field — the dashboard has no per-app code.
- `@agentboom/ui` types the manifest, so a malformed `ui` is caught at
  build time, not at runtime.
- Theming is tokens: swap the `Theme` passed to `<AgentBoomProvider>` to
  re-skin everything.
