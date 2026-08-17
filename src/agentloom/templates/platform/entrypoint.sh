#!/usr/bin/env bash
# {{AGENT_TITLE}} — agent container entrypoint.
# Order matters: skill deps -> session seed -> background maintenance -> qwen serve.
set -euo pipefail

QWEN_HOME="${HOME}/.qwen"
PLATFORM_DIR="${HOME}/platform"

# ── 1. Install skill script dependencies ────────────────────────────────
# Skills may ship Node scripts (skills/<name>/scripts/package.json).
# Installing here means adding a skill with deps needs no image rebuild;
# re-runs are cheap because npm no-ops when node_modules is up to date.
shopt -s nullglob
for pkg in "${QWEN_HOME}"/skills/*/scripts/package.json; do
  dir="$(dirname "${pkg}")"
  if [ ! -d "${dir}/node_modules" ] || [ "${pkg}" -nt "${dir}/package-lock.json" ]; then
    echo "[entrypoint] installing skill deps: ${dir}"
    (cd "${dir}" && npm install --no-audit --no-fund) \
      || echo "[entrypoint] WARN: npm install failed for ${dir}"
  fi
done
shopt -u nullglob

# ── 2. Seed session state (qwen serve expects sessions/state.json) ──────
mkdir -p "${QWEN_HOME}/sessions/default"
[ -f "${QWEN_HOME}/sessions/state.json" ] \
  || echo '{"sessions":{}}' > "${QWEN_HOME}/sessions/state.json"
[ -f "${QWEN_HOME}/sessions/default/context.json" ] \
  || echo '{}' > "${QWEN_HOME}/sessions/default/context.json"

# ── 3. Background: prune agent transcripts every 6 hours ───────────────
(
  while true; do
    /opt/platform-venv/bin/python "${PLATFORM_DIR}/scripts/prune_agent_transcripts.py" \
      >/dev/null 2>&1 || true
    sleep 21600
  done
) &

# ── 4. Run the agent runtime ────────────────────────────────────────────
# Extra flags (e.g. --channel all, --workspace /somewhere) come from the env.
# shellcheck disable=SC2086
exec qwen serve --hostname 0.0.0.0 ${QWEN_SERVE_ARGS:-}
