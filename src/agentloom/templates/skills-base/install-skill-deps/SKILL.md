---
name: install-skill-deps
description: Install a skill's Node dependencies without restarting the agent container. Use right after adding or updating a skill that ships a scripts/package.json.
---

# Install Skill Deps

Skills with Node scripts ship a `scripts/package.json`. The container
installs them automatically at boot — but after adding a skill live, use
this procedure instead of restarting.

## Procedure

```bash
bash ~/.qwen/skills/install-skill-deps/scripts/install-skill-deps.sh <skill-name>
```

The script:
1. Locates `~/.qwen/skills/<skill-name>/scripts/package.json`
2. Runs `npm install` in that directory (creates/updates `node_modules`)
3. Reports success or the npm error verbatim

## Notes

- If the install fails on native builds, the missing system packages must
  be added to the agent `Dockerfile` (`EXTRA_APT_PACKAGES`) and the
  container rebuilt — npm alone cannot fix that.
- After installing, verify the skill's script actually runs (one test
  invocation) before declaring success.
