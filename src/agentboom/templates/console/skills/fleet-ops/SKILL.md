---
name: fleet-ops
description: Procedures for operating the agentboom fleet — create agents, sync the base, install packages, triage drift and health. Use for any multi-agent management task.
---

# Fleet Ops

## Triage an agent

1. `agentboom fleet --json` — drift, validate errors, base version.
2. Inside the agent repo: `git status --short` (WIP present?),
   `git log --oneline -5`.
3. If the platform runs: `curl -s <gateway>/health` and `/api/catalog`.
4. Report findings before acting.

## Sync the base across the fleet

1. The SDK: bump the pin (`agentboom_sdk @ .../vX.Y.Z/...whl`) in each
   agent's `platform/requirements.txt`/Dockerfile, rebuild, run the
   agent's tests, redeploy only after green.
2. Template glue: `agentboom upgrade` per agent (check-only first;
   review `locally_modified`; apply; use `--force` only with
   confirmation and only file-by-file).
3. Never upgrade two agents in parallel if they share live state.

## Add a capability everywhere

- Repeated integration (channels, docs, credential stores) → check
  `agentboom packages`; if missing, build a new package in the agentboom
  repo (`templates/packages/<name>/`) rather than copy-pasting into agents.
- Agent-specific procedure → `agentboom add skill` inside that agent.

## Rules

- One branch per concern; commit messages say what was verified.
- A redeploy requires: green tests + health check plan + user confirmation.
- If an agent has uncommitted work, work around it or stop and say so —
  never commit someone else's WIP.
