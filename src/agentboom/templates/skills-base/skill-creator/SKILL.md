---
name: skill-creator
description: Author, structure, and validate skills for this agent following the agentskills.io convention. Use whenever creating a new skill or fixing an existing SKILL.md.
---

# Skill Creator

Create skills that this agent can discover and invoke.

## Before you create — is a new skill the right move?

1. **Reuse first:** search `skills/` — an existing skill may already cover the
   domain. If so, extend it (add a `references/` playbook or script flag).
2. **Create** only for a *repeatable* procedure (done more than once, or will
   be again). A one-off task does not earn a skill.
3. **Refactor/merge** when the new skill would overlap an existing one —
   consolidate and delete the redundant skill.
4. **Retire** skills the user no longer needs; an obsolete skill is worse than
   none.
The aim: every repeat gets easier and faster, and your context stays lean.

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
