---
name: self-heal
description: Detect, diagnose and recover from a broken platform — a down gateway/dashboard/scheduler or a frontend the agent itself broke. Use whenever a health probe fails, a service won't start, or jobs keep erroring. Recover from the internal git without ever touching user data.
---

# Self-Heal

You own your platform's health. When something is red, fix it from the root
cause — and prove it with a moving counter, not a single "ok".

## 1. Detect (counters, not vibes)

Probe the real surfaces before concluding anything:
- Gateway: `GET /health` and `GET /health/db`
- Dashboard: `GET /health`
- Scheduler/catalog: `GET /api/catalog` (with your bearer token)

A failure is a fact; a green light is not proof. Confirm with a counter that
moves after your fix (requests served, jobs run, rows written).

## 2. Diagnose (infra vs. bad work)

- `docker compose logs --tail=200 <service>` for the failing service.
- `git log --oneline -10` in your repo — **your most recent commit is the
  prime suspect** when the failure started right after it.
- Classify the cause:
  - **Infra outage** — model/LLM down, DB locked, port busy, disk full.
    Not your code's fault.
  - **Bad work** — your own recent change broke it.

## 3. Recover

- **Bad work:** roll back the affected code to the last good commit
  (`git revert` or check out the path), then rebuild/restart only that
  service (`docker compose up --build -d <service>`; for the dashboard run
  its build first). User data is NOT in git, so a code rollback never
  touches it.
- **Infra:** restart the service; do not punish yourself — refund retry
  budgets on outage, never on bad work.
- Re-run the failing probe after each step; stop changing things once green.

## 4. Verify

Probes green **and** a counter moving. Record what broke, the root cause and
the fix (the repair loop and the user both read this).

## 5. Escalate

If bounded steps don't restore it, tell the user exactly what is broken, what
you tried, and the current state. Never leave it red silently.

## Freedom is preserved

Healing never reduces your ability to grow: after a rollback you may re-attempt
the change more carefully, and installing MCPs, skills, sub-agents and packages
stays allowed — that is your evolution. The discipline is only: verify, keep a
rollback point, and root-cause.
