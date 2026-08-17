# CLI reference

All commands accept `--json` for machine-readable output. Exit codes:
**0** ok · **1** operation/check failed · **2** usage error. On failure
with `--json`, the payload is `{"ok": false, "error": "..."}`.

## `agentboom init <dir>`

Scaffold a new agent from the `platform` template.

Flags: `--name` (kebab-case; default: directory name) · `--description` ·
`--port-agent` (default 4170) · `--port-platform` (default 8000) ·
`--force` (non-empty target; existing files are never overwritten).

```json
{
  "ok": true,
  "path": "/abs/path", "name": "my-agent",
  "template": "platform", "base_version": "0.1.0",
  "created": ["..."], "skipped_existing": [],
  "managed_count": 26,
  "next_steps": ["..."]
}
```

Refuses a non-empty target unless `--force`. Names must match
`^[a-z][a-z0-9-]*$`.

## `agentboom validate [dir]`

Structural health checks. Levels: `error` (fails, exit 1) · `warn` ·
`info` (drift notes).

Check ids:

| id | meaning |
|---|---|
| `agentboom.registry-missing` | no `.agentboom.json` — not an agentboom agent |
| `structure.missing-file` | required file absent |
| `entrypoint.dangling-script` | entrypoint references a script that doesn't exist |
| `env.var-not-documented` | compose requires `${VAR}` (no default) missing from `.env.example` |
| `miniapp.no-main` / `.no-get-router` / `.no-manifest` / `.bad-manifest` | mini-app contract violations |
| `miniapp.bad-cron` / `.job-no-target` / `.job-no-prompt` | manifest job errors |
| `skill.no-skill-md` / `.no-frontmatter` / `.missing-name` / `.missing-description` / `.long-description` | skill contract violations |
| `base.file-missing` / `base.locally-modified` | managed-file drift (warn/info) |

```json
{"ok": true, "agent_dir": "...", "errors": 0, "warnings": 0,
 "checks": [{"id": "...", "level": "...", "message": "...", "path": "..."}]}
```

## `agentboom upgrade [dir]`

Sync managed base files. Default is **check-only**; `--apply` writes;
`--force` (with `--apply`) also overwrites locally modified files.

Decision table per managed file:

| installed state | action |
|---|---|
| matches recorded hash, base unchanged | `up_to_date` |
| matches recorded hash, base changed | `upgraded` (written on `--apply`) |
| differs from recorded hash | `locally_modified` (skipped unless `--force`) |
| deleted | `restored` (written on `--apply`) |
| shipped by base, not in registry | `new` (written on `--apply`) |
| in registry, no longer shipped | `stale` (reported, never deleted) |

```json
{"ok": true, "mode": "check|apply", "changed": true,
 "base_version_from": "0.1.0", "base_version_to": "0.1.0",
 "up_to_date": [], "upgraded": [], "new": [], "restored": [],
 "locally_modified": [], "stale": []}
```

## `agentboom add skill <name>` / `agentboom add miniapp <name>`

Scaffold inside an agent (cwd or `--dir`). Refuses duplicates and
non-kebab-case names.

```json
{"ok": true, "kind": "skill", "name": "...", "path": "...",
 "created": ["..."], "next": "..."}
```

## `agentboom add package <name>`

Install an optional package into an agent: copies files (never
overwriting), appends requirement lines to `platform/requirements.txt`
and env lines to `.env.example` (both idempotent), records the install in
`.agentboom.json`, and prints post-install steps. Packages are resolved
across all registries (`builtin` first); `--refresh` re-fetches remote
registries first. A package's `requires` list is enforced — installing
one whose dependencies are missing fails with the exact commands to run.

```json
{"ok": true, "package": "vault", "agent_dir": "...",
 "created": ["platform/migrations/002_vault.sql", "..."],
 "skipped_existing": [], "requirements_added": ["cryptography>=44.0.0"],
 "env_example_added": ["VAULT_KEY=..."],
 "post_install": ["..."]}
```

## `agentboom code miniapp|skill <name> ["prompt"]`

Scaffolds if needed (same as `add`), then launches `qwen
--prompt-interactive` **inside the agent repo** with a mission prompt that
already knows the mini-app/skill contract. You describe what you want in
plain language; the agent builds it; you keep iterating in the same
session.

```bash
agentboom code miniapp backups "watch my restic backups and alert on failures"
agentboom code skill runbook "how to restart the homelab services safely"
agentboom code miniapp x "..." --dry-run   # show the mission, don't launch
```

The split is deliberate: `add` = deterministic scaffold only; `code` =
hand the scaffold to the agent.

Runtimes: `code` launches the mission with an agent runtime — `qwen`
today (`--runtime qwen`, default), with `opencode` and `claude` prepared
in the runtime registry for later. If the runtime is missing locally,
the error prints the exact install command, or:

```bash
agentboom install-runtime qwen          # show the install command
agentboom install-runtime qwen --yes    # run it
```

On the agents themselves the runtime already exists inside their
containers, so `code` just works there.

## `agentboom packages [dir]`

List available packages across every registry, and (with an agent dir)
installed ones. Each entry carries `kind` (`addon` or `connector` — a
connector also installs an importable client under
`platform/connectors/<name>/` for mini-apps to use), its `source`
registry, and any `requires`. `--refresh` re-fetches remote registries.

```json
{"ok": true,
 "available": [{"name": "telegram", "description": "...",
                "kind": "addon", "icon": "", "requires": [],
                "source": "builtin"}],
 "unreachable_registries": [],
 "installed": {"vault": {"installed_at": "...", "files": ["..."]}},
 "agent_dir": "..."}
```

## `agentboom registries [list|add|remove]`

Package sources. `builtin` ships with agentboom; add any git repository
or local directory as an extra registry and its packages become
installable exactly like builtin ones. Packages live in a `packages/`
directory (configurable with `--subdir`), one folder per package with an
`.agentboom-package.json` meta file. Remote repos are fetched (cached an
hour), never executed; only add registries you trust.

```bash
agentboom registries                                   # list sources
agentboom registries add community https://github.com/org/agent-packages
agentboom registries add local /path/to/my-packages-repo
agentboom registries remove community
```

```json
{"ok": true, "registries": [
  {"name": "builtin", "source": "builtin", "source_ref": "(bundled)"},
  {"name": "community", "source": "url",
   "source_ref": "https://github.com/org/agent-packages",
   "subdir": "packages", "branch": "main"}]}
```

## `agentboom adopt [dir]`

Bring an existing agent under base management. A file is marked managed
ONLY when it is byte-identical to the template rendering with the stored
variables; everything else stays agent-owned. Non-destructive by
construction. Refuses if `.agentboom.json` already exists.

```json
{"ok": true, "name": "...", "template": "platform",
 "managed_matched": ["..."], "owned_or_diverged": ["..."]}
```

## `agentboom fleet`

Operator view over the fleet registry (`~/.agentboom/fleet.json`).
`fleet add <dir>` registers an agent (requires `.agentboom.json` —
`adopt` first); `fleet remove <name>` unregisters; bare `fleet` (or
`fleet status`) reports every agent: base version, template, packages,
managed-file drift, validate errors.

```json
{"ok": true, "agents": [{"name": "...", "path": "...", "ok": true,
  "base_version": "0.5.0", "template": "platform", "packages": [],
  "drift_modified": [], "drift_missing": [],
  "validate_errors": 0, "validate_warnings": 0}]}
```

## `agentboom console`

Materialize the boomkeeper operator workspace under
`~/.agentboom/console/` (operator AGENTS.md, `fleet-ops` skill, live
`fleet-snapshot.md`) and exec `qwen` in it. `--dry-run` refreshes the
workspace without launching. Extra arguments pass through to `qwen`.

## `agentboom skills [dir]` / `agentboom miniapps [dir]`

List an agent's capabilities.

```json
{"ok": true, "skills": [{"name": "...", "description": "...",
  "managed": true, "files": 3, "path": "..."}]}
{"ok": true, "miniapps": [{"name": "...", "description": "...",
  "version": "0.1.0", "status": "active", "jobs": 2,
  "public": false, "has_main": true, "path": "..."}]}
```

## `agentboom list [parent]`

Discover agents (directories with `.agentboom.json`) one level down.

```json
{"ok": true, "parent": "...", "agents": [{"name": "...", "path": "...",
  "template": "platform", "base_version": "0.1.0", "created_at": "..."}]}
```

## `agentboom doctor`

Toolchain checks. `failed_required` drives the exit code; node/npm/git are
optional.

```json
{"ok": true, "checks": [{"name": "docker", "ok": true,
  "detail": "Docker version ...", "required": true}],
 "failed_required": []}
```

## `agentboom selfcheck`

End-to-end QA in a temp dir: init → validate → upgrade check (must be
clean) → add skill + miniapp → validate again. Run this after changing
templates.

```json
{"ok": true, "steps": [{"step": "init", "ok": true, "detail": "..."}]}
```

## `agentboom version`

```json
{"ok": true, "version": "0.1.0", "templates": ["platform"],
 "default_template": "platform"}
```
