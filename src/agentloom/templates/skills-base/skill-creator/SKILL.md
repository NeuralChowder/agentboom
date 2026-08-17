---
name: skill-creator
description: Author, structure, and validate skills for this agent following the agentskills.io convention. Use whenever creating a new skill or fixing an existing SKILL.md.
---

# Skill Creator

Create skills that this agent can discover and invoke.

## Skill anatomy

```
skills/<skill-name>/
├── SKILL.md            # required: frontmatter + instructions
├── references/         # optional: playbooks, tables, long docs loaded on demand
└── scripts/            # optional: deterministic helpers (bash/node/python)
```

## SKILL.md requirements

1. Frontmatter with exactly two required fields:

   ```markdown
   ---
   name: my-skill
   description: One line that tells the model WHEN to use this skill.
   ---
   ```

   The description is the only text the model sees when choosing skills —
   it must name the trigger situation, not just the topic. Keep it under
   ~500 characters.

2. Body sections, in this order: **When to use** → **Procedure** →
   **Notes** (constraints, safety, edge cases).

## Design rules

- **Deterministic first.** If a step can be a script (gather logs, query
  an API, parse a file), write the script in `scripts/` and have the model
  reason over its output. Open-ended model probing is the fallback, not
  the design.
- **Fixed output shapes.** When the skill produces a report or JSON, put
  the exact template in the SKILL.md so outputs are consistent.
- **References for bulk knowledge.** Symptom tables, checklists, and
  runbooks longer than a screen belong in `references/*.md`, referenced
  from SKILL.md — keeps discovery cheap.
- **Read-only by default.** Skills that touch infrastructure must state
  their mutation policy explicitly.

## Node dependencies

If `scripts/` needs npm packages, add `scripts/package.json` — the agent
container installs skill dependencies automatically at startup (and the
`install-skill-deps` skill installs them without a restart).

## Validate before finishing

Run the bundled validator:

```bash
bash scripts/validate-skill.sh skills/<skill-name>
```

It checks frontmatter, required fields, and description length.
