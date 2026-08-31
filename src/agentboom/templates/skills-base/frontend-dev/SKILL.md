---
name: frontend-dev
description: Develop YOUR OWN dashboard/frontend as the agent — iterate on it continuously, rebuild and restart it safely, and roll back via your internal git when a change breaks it. Use whenever you build or change any user-facing UI (dashboard, mini-app views, shared pages).
---

# Frontend dev — your UI is yours to grow

Your dashboard is **your** product surface. Each agent's frontend is different
because each user is different — you build and evolve it continuously, and you
own restarting it. The agentboom **console** (the product's private area) only
*links* to your dashboard; it never embeds or builds it, because nobody can
know in advance what your user will ask you to make. Keep them separate.

## Iterate

- Your dashboard lives in `platform/dashboard/` (Next.js) and mini-app views
  are declared in each mini-app manifest and rendered by the catalog-driven UI.
- Prefer **extending the catalog-driven views** (declare a view in your
  mini-app; the dashboard renders it) over forking a new app.
- For bespoke pages, add routes/components under `platform/dashboard/app/`.
- Hot path: edit → `npm run build` in `platform/dashboard/` → restart the
  dashboard service. Mini-app API changes hot-reload without restart.

## Restart safely

- Restart only what changed: `docker compose up -d --build dashboard` (or
  `docker compose restart dashboard`). Never restart the whole stack for a UI
  change.
- Before a risky UI change, create a rollback point: commit your current code
  to your internal git (see the Recovery section of your operating manual).

## Roll back when you break it

- If the dashboard fails to build or render after your change: check
  `docker compose logs --tail=200 dashboard`, then revert the offending commit
  (`git revert` / check out the path) and rebuild. Your user's **data is never
  in git**, so a code rollback never touches it.
- Verify with a health probe + a rendered check before declaring success
  (see the `self-heal` skill).

## Standards (non-negotiable)

- Use the design tokens (`globals.css` / theme) — never hardcode brand colors.
- Respect the user's theme (light/dark/accent) via `data-theme`.
- Mobile-first: it must work at 320–560px with no horizontal overflow and
  ≥44px tap targets.
- Keep the user's data private; never leak it into logs, markup, or URLs.
