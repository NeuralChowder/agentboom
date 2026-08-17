# AGENTS.md — working on agentloom itself

You are working on **agentloom**, the scaffold/maintenance base for agent
projects. Other agents use this tool and the agents it generates, so
everything here must stay agent-readable and dependency-free.

## Repo map

```
bin/agentloom                 zero-install CLI entry (python3 shim)
src/agentloom/
  cli.py                      argparse dispatch + human/JSON output
  render.py                   {{PLACEHOLDER}} rendering (strict)
  registry.py                 .agentloom.json + managed-file hashing
  checks.py                   stdlib validators (cron, frontmatter, env)
  commands/                   init, validate, upgrade, add, listcmd, doctor, selfcheck
  templates/
    platform/                 THE agent template (rendered at init)
      .agentloom-template.json  managed-file patterns + executables
    skills-base/              base skills copied into every agent's skills/
tests/                        stdlib unittest suite (tests/run_tests.py)
docs/                         commands.md, anatomy.md, roadmap.md
```

## Hard rules

1. **Python stdlib only.** Never add a runtime dependency to agentloom.
   The template's platform may use FastAPI etc. (declared in its own
   requirements.txt) — the CLI itself must not.
2. **Templates are rendered with `{{UPPER_SNAKE}}` placeholders.** Adding a
   placeholder means adding it to `commands/init.py` variables. Rendering is
   strict: unknown placeholders raise — that is correct, do not soften it.
3. **Managed files** are listed by fnmatch patterns in
   `templates/platform/.agentloom-template.json` (`*` matches `/`). Base
   skills are always managed. If you add a file that should survive
   `agentloom upgrade` across all agents, add its pattern there and put a
   `# agentloom:managed` (or `<!-- -->`) marker line in the file.
4. **Upgrade must stay non-destructive.** Never auto-delete files removed
   from the base (report as `stale`). Never overwrite locally modified
   files without `--force`. Preserve these invariants in any refactor.
5. **Migrations inside the template are append-only.** `001_core.sql` is
   applied by real agents — to change the base schema, add `002_*.sql`.
6. **Agent-ready output:** every user-facing command keeps `--json`
   payloads stable (no breaking field renames without a major version bump)
   and exit codes meaningful (0 ok, 1 failed, 2 usage).

## Verify before claiming done

```bash
python3 tests/run_tests.py -v     # unit + lifecycle suite
python3 bin/agentloom selfcheck   # e2e: init, validate, upgrade, add
```

If you changed templates, also eyeball a fresh agent:

```bash
python3 bin/agentloom init /tmp/scratch-agent --description "scratch"
python3 bin/agentloom validate /tmp/scratch-agent
find /tmp/scratch-agent -name '*.py' -exec python3 -m py_compile {} +
```

## Design lore (why things are the way they are)

- The anatomy mirrors two production agents: an infra-monitoring agent
  (SQLite, deterministic fingerprinting) and a personal-assistant agent
  (21 mini-apps, 25 skills, Telegram channel). Everything in the base is
  extracted from code that survived real incidents; the doctrine comments
  in `templates/platform/platform/**` cite those incidents — keep them when
  porting changes.
- The managed/user-owned seam is deliberate: machinery upgrades centrally,
  identity (`AGENTS.md`, compose, mini-apps) stays per-agent. Do not move
  identity files into the managed set.
- Ports publish loopback-only by default; admin endpoints use HTTP Basic
  with constant-time compare; SQLite lives on a named volume. These are
  security lessons, not style choices.
