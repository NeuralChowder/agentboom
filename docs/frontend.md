# Frontend architecture

agentboom's user-facing surface is a **Next.js** dashboard, and it is
deliberately a thin, generic shell: it does not hard-code any mini-app.
Instead it renders whatever the platform's mini-apps declare. This is what
lets a *growing* agent add capabilities (and their UI) without frontend
releases.

## The three pieces

```
@agentboom/ui          design system + renderers (this repo: ui/)
   │                   tokens (design systems), UI-manifest types, React renderers
   ▼
Next.js dashboard      a thin host app: layout + routing + auth
   │                   uses <AgentBoomProvider>, <DashboardNav>, <MiniAppView>
   ▼
platform gateway       /api/catalog + /api/<miniapp>/* + /api/settings/*
```

1. **`@agentboom/ui`** (`ui/`) — the framework. Token-based design system
   (`tokens.ts`), TypeScript types for the UI-manifest DSL (`manifest.ts`),
   a tiny `PlatformClient`, and React renderers for `table` / `list` /
   `form` / `stats` views (`components.tsx`).

2. **The dashboard** — a Next.js app that mounts the provider and renders
   the catalog. It shows a nav of mini-apps (grouped by `ui.nav.group`)
   and renders the selected app's views. New mini-apps appear automatically.

3. **The platform gateway** — the source of truth. `/api/catalog` lists
   every mini-app including its `ui` manifest; the settings mini-app
   exposes profile/global-config editing.

## How a mini-app gets UI

A mini-app declares screens in its `.miniapp.json` `ui` field (see
`ui/README.md` for the full contract). The dashboard's `<MiniAppView>`
fetches the view's `source` endpoint and renders it. Because the manifest
is typed, mismatches surface at build time.

This is the same declarative pattern the reference personal-assistant
instance's dashboard uses — agentboom generalizes it so any agent gets it
for free.

## Design systems are generated from tokens

Components never hard-code colors or sizes; they read `--ab-*` CSS custom
properties emitted from a `Theme`. A design system is therefore just a set
of token values:

- Swap the theme -> re-skin the whole dashboard (dark / light / brand).
- Generate new design systems by producing token sets, not new components.

`@agentboom/ui` ships `defaultTheme` (agentboom dark) and `lightTheme`.

## Global config in the UI

The dashboard edits the agent's global config through the `settings`
mini-app: profile (name, language, timezone, country) and the editable
AGENTS.md context blocks. The agent itself can update these too — the UI is
one writer among several, all going through the same endpoints.

## Why Next.js

Next.js gives app routing, server components (for fetching the catalog at
request time), and static export for simple hosting. `@agentboom/ui` is
framework-agnostic React, so the host could be another React framework if
ever needed — but Next.js is the supported default.
