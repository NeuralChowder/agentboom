#!/usr/bin/env bash
# Install a skill's npm dependencies without a container restart.
# Usage: install-skill-deps.sh <skill-name>
set -euo pipefail

SKILL_NAME="${1:-}"
if [ -z "${SKILL_NAME}" ]; then
  echo "usage: install-skill-deps.sh <skill-name>" >&2
  exit 2
fi

SKILL_DIR="${HOME}/.qwen/skills/${SKILL_NAME}"
PKG="${SKILL_DIR}/scripts/package.json"

if [ ! -f "${PKG}" ]; then
  echo "No scripts/package.json found for skill '${SKILL_NAME}'" >&2
  exit 1
fi

cd "${SKILL_DIR}/scripts"
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi
echo "Installed dependencies for skill '${SKILL_NAME}'"
