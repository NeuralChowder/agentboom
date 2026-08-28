# @agentboom/ui — design system + UI framework (Next.js)

The frontend half of agentboom. It turns the platform's **mini-app UI
manifests** (the `ui` field in `.miniapp.json`) into working screens, and
it does it through a **token-based design system** — so a new mini-app gets
real UI with zero frontend code, and a new look is just a new set of
tokens.

```
┌────────────────────────────────────────────────────────────┐
│ Next.js dashboard                                          │
│  └─ @agentboom/ui                                          │
│       ├─ tokens.ts       design tokens (the design system) │
│       ├─ manifest.ts     types for mini-app `ui` specs     │
│       ├─ client.ts       PlatformClient + {{templating}}   │
│       └─ components.tsx  renderers (table/list/form/stats) │
└────────────────────────────────────────────────────────────┘
                     │ HTTP (loopback / proxy)
                     ▼
        platform gateway  /api/catalog, /api/<miniapp>/*, /api/settings/*
```

## Quick start (Next.js App Router)

```tsx
// app/dashboard/page.tsx
"use client";
import { useEffect, useState } from "react";
import {
  AgentBoomProvider, PlatformClient, DashboardNav, MiniAppView,
  defaultTheme, type MiniAppEntry,
} from "@agentboom/ui";

const client = new PlatformClient({ baseUrl: "/api/platform" }); // proxied

export default function Dashboard() {
  const [apps, setApps] = useState<MiniAppEntry[]>([]);
  const [active, setActive] = useState<MiniAppEntry | null>(null);

  useEffect(() => {
    client.catalog().then((c) => {
      setApps(c.apps);
      setActive(c.apps.find((a) => a.ui?.views?.length) ?? null);
    });
  }, []);

  return (
    <AgentBoomProvider client={client} theme={defaultTheme}>
      <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 24 }}>
        <DashboardNav apps={apps} active={active?.name} onSelect={setActive} />
        <main>{active ? <MiniAppView app={active} /> : null}</main>
      </div>
    </AgentBoomProvider>
  );
}
```

The dashboard talks to the platform gateway. In production put the gateway
behind a same-origin Next.js route (`/api/platform/[...]`) or reverse proxy
so the browser never needs the raw loopback port.

## Design systems via tokens

A design system is a `Theme`. Components only read `--ab-*` CSS custom
properties, so swapping the theme re-skins everything. Author a new design
system by overriding tokens:

```ts
import { defaultTheme, type Theme } from "@agentboom/ui";

export const brandTheme: Theme = {
  ...defaultTheme,
  name: "my-brand",
  colors: { ...defaultTheme.colors, accent: "#7c3aed" },
};
```

Generate whole families (dark/light/brand) by producing token sets — the
renderers don't change. This is how agentboom supports "many design
systems" without many component libraries.

## The UI manifest contract

Mini-apps declare screens in `.miniapp.json` under `ui`:

```json
{
  "ui": {
    "nav": { "label": "Mailboxes", "icon": "at-sign", "group": "Email", "order": 3 },
    "views": [
      { "id": "accounts", "title": "Mailboxes", "type": "table",
        "source": "/mailboxes", "rows": "mailboxes",
        "columns": [{ "field": "email", "label": "Mailbox", "primary": true }],
        "actions": [{ "label": "Test", "method": "POST", "path": "/accounts/{{id}}/test" }] }
    ]
  }
}
```

View types: `table`, `list`, `form`, `stats`. Actions support templated
paths/bodies (`{{field}}`), confirmation, prompting, and result dialogs.
`@agentboom/ui` ships TypeScript types for all of it (`manifest.ts`), so
manifests are checked at build time.

## What's intentionally minimal

- No styling runtime or component marketplace — tokens + CSS variables keep
  the bundle lean and the design system swappable.
- `ActionButton` confirmation/prompting defaults to `window.confirm/prompt`;
  inject richer dialogs via `<AgentBoomProvider confirmAction promptAction>`.
