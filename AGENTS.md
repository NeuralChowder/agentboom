# AGENTS.md — working on agentboom itself

You are working on **agentboom**, the scaffold/maintenance base for agent
projects. Other agents use this tool and the agents it generates, so
everything here must stay agent-readable and the CLI dependency-free.

## Repo map

```
bin/agentboom                 zero-install CLI entry (python3 shim)
src/agentboom/
  cli.py                      argparse dispatch + human/JSON output
  render.py                   {{PLACEHOLDER}} rendering (strict)
  registry.py                 .agentboom.json + managed-file hashing
  checks.py                   stdlib validators (cron, frontmatter, env)
  commands/                   init, validate, upgrade, add, packages, listcmd, doctor, selfcheck
  templates/
    platform/                 THE agent template (rendered at init)
      .agentboom-template.json  managed-file patterns + executables
      qwen-home/              becomes .qwen-docker/ in generated agents
    skills-base/              base skills copied into every agent
    packages/                 optional packages (telegram, rich-link, vault)
sdk/                          the agentboom-sdk distributable (pip package)
  src/agentboom_sdk/          db, agent, llm, cron, task_queue, events,
                              untrusted, config, log, services.scheduler
tests/                        stdlib unittest suite (tests/run_tests.py)
.github/workflows/            ci.yml (tests) + release.yml (tag -> wheels -> release)
docs/                         commands.md, anatomy.md, roadmap.md
```

## Hard rules

1. **agentboom CLI stays Python stdlib only.** Never add a runtime
   dependency to the CLI. The SDK package (`sdk/`) may depend on
   httpx/aiosqlite; generated agents declare their own deps.
2. **One base, one version.** `agentboom` and `agentboom-sdk` are versioned
   in lockstep (bump root pyproject, `src/agentboom/__init__.py`,
   `sdk/pyproject.toml`, `sdk/src/agentboom_sdk/__init__.py`, and the pin
   URL in `templates/platform/platform/requirements.txt` together).
3. **Templates are rendered with `{{UPPER_SNAKE}}` placeholders.** Adding a
   placeholder means adding it to `commands/init.py` variables. Rendering is
   strict: unknown placeholders raise — that is correct, do not soften it.
4. **Managed files** are listed by fnmatch patterns in
   `templates/platform/.agentboom-template.json` (`*` matches `/`). Base
   skills are always managed. The SDK is NOT managed — it is installed as a
   package; the pin in requirements.txt is managed.
5. **Upgrade must stay non-destructive.** Never auto-delete files removed
   from the base (report as `stale`). Never overwrite locally modified
   files without `--force`. Preserve these invariants in any refactor.
6. **Migrations inside the template are append-only.** `001_core.sql` is
   applied by real agents — to change the base schema, add `002_*.sql`.
7. **Agent-ready output:** every user-facing command keeps `--json`
   payloads stable (no breaking field renames without a major version bump)
   and exit codes meaningful (0 ok, 1 failed, 2 usage).
8. **Packaging gotcha:** setuptools globs skip dot-directories. Anything
   that must ship in the wheel may not live behind a dot-dir — that's why
   the template stores `qwen-home/` and `init` maps it to `.qwen-docker/`.

## Verify before claiming done

```bash
python3 tests/run_tests.py -v     # unit + lifecycle + packages suite
python3 bin/agentboom selfcheck   # e2e: init, validate, upgrade, add
```

If you changed templates, also eyeball a fresh agent:

```bash
python3 bin/agentboom init /tmp/scratch-agent --description "scratch"
python3 bin/agentboom validate /tmp/scratch-agent
find /tmp/scratch-agent -name '*.py' -exec python3 -m py_compile {} +
```

For SDK changes: `pip install sdk/` into a scratch venv and
`python -c "import agentboom_sdk"`.

## Releasing

```bash
git tag -a v0.2.1 -m "release: v0.2.1" && git push origin v0.2.1
```

The Release workflow builds both wheels and attaches them to a GitHub
Release; agents install the SDK pin straight from the release asset URL.

## Design lore (why things are the way they are)

- The anatomy mirrors two production agents: an infra-monitoring agent
  (SQLite, deterministic fingerprinting) and a personal-assistant agent
  (21 mini-apps, 25 skills, Telegram channel). Everything in the base is
  extracted from code that survived real incidents; the doctrine comments
  in `sdk/src/agentboom_sdk/**` cite those incidents — keep them.
- The managed/user-owned/package seam is deliberate: machinery upgrades
  via the SDK pin, template glue via `agentboom upgrade`, identity
  (`AGENTS.md`, compose, mini-apps) stays per-agent.
- Ports publish loopback-only by default; admin endpoints use HTTP Basic
  with constant-time compare; SQLite lives on a named volume. These are
  security lessons, not style choices.
