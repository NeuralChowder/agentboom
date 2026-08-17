# CLI reference

All commands accept `--json` for machine-readable output. Exit codes:
**0** ok · **1** operation/check failed · **2** usage error. On failure
with `--json`, the payload is `{"ok": false, "error": "..."}`.

## `agentloom init <dir>`

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

## `agentloom validate [dir]`

Structural health checks. Levels: `error` (fails, exit 1) · `warn` ·
`info` (drift notes).

Check ids:

| id | meaning |
|---|---|
| `agentloom.registry-missing` | no `.agentloom.json` — not an agentloom agent |
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

## `agentloom upgrade [dir]`

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

## `agentloom add skill <name>` / `agentloom add miniapp <name>`

Scaffold inside an agent (cwd or `--dir`). Refuses duplicates and
non-kebab-case names.

```json
{"ok": true, "kind": "skill", "name": "...", "path": "...",
 "created": ["..."], "next": "..."}
```

## `agentloom add package <name>`

Install an optional package into an agent: copies files (never
overwriting), appends requirement lines to `platform/requirements.txt`
and env lines to `.env.example` (both idempotent), records the install in
`.agentloom.json`, and prints post-install steps.

```json
{"ok": true, "package": "vault", "agent_dir": "...",
 "created": ["platform/migrations/002_vault.sql", "..."],
 "skipped_existing": [], "requirements_added": ["cryptography>=44.0.0"],
 "env_example_added": ["VAULT_KEY=..."],
 "post_install": ["..."]}
```

## `agentloom packages [dir]`

List available packages, and (with an agent dir) installed ones.

```json
{"ok": true,
 "available": [{"name": "telegram", "description": "..."}],
 "installed": {"vault": {"installed_at": "...", "files": ["..."]}},
 "agent_dir": "..."}
```

## `agentloom skills [dir]` / `agentloom miniapps [dir]`

List an agent's capabilities.

```json
{"ok": true, "skills": [{"name": "...", "description": "...",
  "managed": true, "files": 3, "path": "..."}]}
{"ok": true, "miniapps": [{"name": "...", "description": "...",
  "version": "0.1.0", "status": "active", "jobs": 2,
  "public": false, "has_main": true, "path": "..."}]}
```

## `agentloom list [parent]`

Discover agents (directories with `.agentloom.json`) one level down.

```json
{"ok": true, "parent": "...", "agents": [{"name": "...", "path": "...",
  "template": "platform", "base_version": "0.1.0", "created_at": "..."}]}
```

## `agentloom doctor`

Toolchain checks. `failed_required` drives the exit code; node/npm/git are
optional.

```json
{"ok": true, "checks": [{"name": "docker", "ok": true,
  "detail": "Docker version ...", "required": true}],
 "failed_required": []}
```

## `agentloom selfcheck`

End-to-end QA in a temp dir: init → validate → upgrade check (must be
clean) → add skill + miniapp → validate again. Run this after changing
templates.

```json
{"ok": true, "steps": [{"step": "init", "ok": true, "detail": "..."}]}
```

## `agentloom version`

```json
{"ok": true, "version": "0.1.0", "templates": ["platform"],
 "default_template": "platform"}
```
