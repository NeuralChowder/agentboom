#!/usr/bin/env bash
# Validate a skill directory structure and SKILL.md frontmatter.
# Usage: validate-skill.sh <path/to/skill-dir>
set -euo pipefail

SKILL_DIR="${1:-}"
if [ -z "${SKILL_DIR}" ] || [ ! -d "${SKILL_DIR}" ]; then
  echo "usage: validate-skill.sh <skill-dir>" >&2
  exit 2
fi

SKILL_MD="${SKILL_DIR}/SKILL.md"
if [ ! -f "${SKILL_MD}" ]; then
  echo "FAIL: ${SKILL_DIR} has no SKILL.md" >&2
  exit 1
fi

errors=0

# Frontmatter block exists
if ! head -n 1 "${SKILL_MD}" | grep -q '^---$'; then
  echo "FAIL: SKILL.md does not start with a --- frontmatter block" >&2
  errors=$((errors + 1))
else
  fm="$(awk 'NR==1{next} /^---$/{exit} {print}' "${SKILL_MD}")"
  for field in name description; do
    if ! printf '%s\n' "${fm}" | grep -Eq "^${field}:"; then
      echo "FAIL: frontmatter missing '${field}'" >&2
      errors=$((errors + 1))
    fi
  done
  desc="$(printf '%s\n' "${fm}" | sed -n 's/^description:[[:space:]]*//p' | head -n 1)"
  if [ "${#desc}" -gt 1024 ]; then
    echo "WARN: description is ${#desc} chars — keep it concise" >&2
  fi
fi

# Directory name should match frontmatter name (kebab-case)
base="$(basename "${SKILL_DIR}")"
if ! printf '%s' "${base}" | grep -Eq '^[a-z][a-z0-9-]*$'; then
  echo "FAIL: skill directory '${base}' is not kebab-case" >&2
  errors=$((errors + 1))
fi

# scripts/package.json without node_modules is fine (installed at boot),
# but a package.json must be valid JSON if present.
if [ -f "${SKILL_DIR}/scripts/package.json" ]; then
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" \
      "${SKILL_DIR}/scripts/package.json" 2>/dev/null; then
    echo "FAIL: scripts/package.json is not valid JSON" >&2
    errors=$((errors + 1))
  fi
fi

if [ "${errors}" -gt 0 ]; then
  echo "skill validation: FAIL (${errors} errors)" >&2
  exit 1
fi
echo "skill validation: PASS (${base})"
