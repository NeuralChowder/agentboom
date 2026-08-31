# Code Rules — engineering standards

The cares every change must respect, regardless of which model writes it.
When in doubt, follow these over cleverness. Each rule cites the real bug
that motivated it so you can judge edge cases instead of blindly obeying.

## 1. Idempotency & re-runs
- **Never regenerate a secret that already exists.** Generate only when
  absent; carry the existing value over. (Re-running setup once rotated
  live tokens and broke running services.)
- **A re-run must never drop user edits.** Build output from the existing
  file and merge; append newly-introduced keys. Never rebuild over user
  data from a blank template.
- Prefer "carry over, then fill gaps" over "recreate".

## 2. Time & clocks
- **Use a `None` sentinel for "not yet happened".** Never `0` or epoch: on a
  fresh boot `monotonic()` is small, so `now - 0 < ttl` is wrongly true and a
  cache never fires. (Login-probe sat dead on fresh containers.)
- **Separate liveness from progress.** A heartbeat must not reset the
  "is this turn making progress" clock, or a stuck job looks alive forever.
- **Store parsed/normalised timestamps**, not the raw request string.
  (Scheduler once stored the raw `run_at` and mis-scheduled.)

## 3. Secrets & privacy
- Secrets go only to gitignored files with `0600` perms. Never echo a secret
  to stdout, logs, or an API/JSON payload — return key *names*, not values.
- **Zero personal data** in framework/templates. Run the leak grep (names,
  LAN IPs, addresses, emails) before committing.
- Resolve credentials from the vault at call time; never bake them into code
  or committed config.

## 4. Database
- **asyncpg validates Python types client-side.** A `$1::date` cast does NOT
  fix a string — pass native types matching the column (`date`,
  tz-aware `datetime`, `list`, `dict`). (`'str' object has no toordinal`.)
- Close pools explicitly / atexit; a process exiting early leaks connections.
- Portable SQL: `$n` placeholders, ISO-8601 UTC TEXT timestamps, JSON as
  TEXT, booleans as 0/1; ship a `.sql` **and** `.pg.sql` per migration.

## 5. Deploys & processes
- Give services a `stop_grace_period` longer than pool teardown + in-flight
  cancel, or stops land as SIGKILL (137) and strand work. (10s default killed
  the gateway mid-teardown.)
- Key TLS-only behaviour (e.g. `Secure` cookies) to the actual scheme, not
  `NODE_ENV`/assumptions. (Secure cookie over plain HTTP locked users out.)

## 6. HTTP & resilience
- **Distinguish infra failure from bad work.** Refund retry budgets on
  outage/5xx/timeout; do NOT refund on validation errors, or bugs retry
  forever.
- Probe/health caches must not depend on the clock for "have I probed yet".

## 7. Tests & CI
- CI must install the same deps tests import (keep the test requirements in
  sync), or CI fails with `ModuleNotFoundError` that never happens locally.
- Add a regression test for every bug fixed — it should fail on the old code.
- Run the full suite + selfcheck before declaring done.

## 8. Style
- No lambda assigned to a named constant; use `def`. Imports at top. Let
  Ctrl-C abort wizards (catch `EOFError`, not `KeyboardInterrupt`).
- Prefer editing existing files over creating new; three similar lines beat a
  premature helper. Validate only at system boundaries (user input, external
  APIs) — no error handling for impossible states.
