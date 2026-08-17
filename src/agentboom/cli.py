"""agentboom command-line interface.

Design goals:
- Agent-ready: every command supports --json with a stable payload shape and
  meaningful exit codes (0 ok, 1 check/operation failed, 2 usage error).
- Human-friendly: without --json, output is plain one-line-per-fact text.
- Zero runtime dependencies: Python stdlib only.
"""
import argparse
import json
import sys

from agentboom import __version__
from agentboom import fleet as fleet_reg
from agentboom import registries as registries_mod
from agentboom.commands import add as add_cmd
from agentboom.commands import adopt as adopt_cmd
from agentboom import runtimes
from agentboom.commands import code as code_cmd
from agentboom.commands import console as console_cmd
from agentboom.commands import fleetcmd
from agentboom.commands import doctor as doctor_cmd
from agentboom.commands import init as init_cmd
from agentboom.commands import listcmd
from agentboom.commands import packages as packages_cmd
from agentboom.commands import registrycmd
from agentboom.commands import selfcheck as selfcheck_cmd
from agentboom.commands import upgrade as upgrade_cmd
from agentboom.commands import validate as validate_cmd
from agentboom.commands import DEFAULT_TEMPLATE, list_templates
from agentboom.render import TemplateError


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="machine-readable JSON output (agent/CI mode)")

    parser = argparse.ArgumentParser(
        prog="agentboom",
        description="Scaffold, maintain, and upgrade agent projects from a shared base.",
        epilog=(
            "examples:\n"
            "  agentboom init my-agent --description \"watches the homelab\"\n"
            "  agentboom code miniapp backups \"alert when backups fail\"\n"
            "  agentboom add package vault\n"
            "  agentboom fleet            # health of every agent\n"
            "  agentboom upgrade --apply  # sync the base\n"
            "\n"
            "every command accepts --json for machine-readable output."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"agentboom {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common],
                       help="create a new agent project from the base template")
    p.add_argument("dir", help="target directory (created if missing)")
    p.add_argument("--name", help="agent name in kebab-case (default: directory name)")
    p.add_argument("--description", help="one-line agent description for AGENTS.md")
    p.add_argument("--port-agent", type=int, default=4170,
                   help="host port publishing the agent HTTP API (default 4170)")
    p.add_argument("--port-platform", type=int, default=8000,
                   help="host port publishing the platform gateway (default 8000)")
    p.add_argument("--force", action="store_true",
                   help="allow a non-empty target dir (existing files are never overwritten)")

    p = sub.add_parser("validate", parents=[common],
                       help="structural health checks for an agent project")
    p.add_argument("dir", nargs="?", default=".", help="agent directory (default: cwd)")

    p = sub.add_parser("upgrade", parents=[common],
                       help="sync managed base files with this agentboom version")
    p.add_argument("dir", nargs="?", default=".", help="agent directory (default: cwd)")
    p.add_argument("--apply", action="store_true",
                   help="write changes (default is check-only)")
    p.add_argument("--force", action="store_true",
                   help="with --apply: overwrite locally modified managed files")

    p = sub.add_parser("add", parents=[common],
                       help="scaffold a skill, mini-app, or optional package")
    add_sub = p.add_subparsers(dest="add_kind", required=True)
    for kind, runner in (("skill", "run_skill"), ("miniapp", "run_miniapp")):
        sp = add_sub.add_parser(kind, parents=[common],
                                help=f"scaffold a new {kind} inside an agent")
        sp.add_argument("name", help=f"{kind} name in kebab-case")
        sp.add_argument("--description", help="one-line description")
        sp.add_argument("--dir", default=".", help="agent directory (default: cwd)")
    sp = add_sub.add_parser("package", parents=[common],
                            help="install an optional package into an agent")
    sp.add_argument("name", help="package name (see `agentboom packages`)")
    sp.add_argument("--dir", default=".", help="agent directory (default: cwd)")
    sp.add_argument("--refresh", action="store_true",
                    help="re-fetch remote registries before resolving")

    p = sub.add_parser("code", parents=[common],
                       help="let a qwen agent build a mini-app or skill for you")
    code_sub = p.add_subparsers(dest="code_kind", required=True)
    for kind in ("miniapp", "skill"):
        sp = code_sub.add_parser(kind, parents=[common],
            help=f"scaffold (if needed) then open qwen with a mission to build the {kind}")
        sp.add_argument("name", help=f"{kind} name in kebab-case")
        sp.add_argument("prompt", nargs="?", default="",
                        help="what the %s should do, in plain language" % kind)
        sp.add_argument("--description", help="one-line description for the scaffold")
        sp.add_argument("--dir", default=".", help="agent directory (default: cwd)")
        sp.add_argument("--runtime", default="qwen",
                        help="agent runtime to use (qwen today; opencode/claude prepared)")
        sp.add_argument("--dry-run", action="store_true",
                        help="prepare the mission prompt but do not launch the runtime")

    p = sub.add_parser("install-runtime", parents=[common],
                       help="show/run the install command for an agent runtime")
    p.add_argument("name", help="runtime name (qwen, opencode, claude)")
    p.add_argument("--yes", action="store_true",
                   help="run the install command without asking")

    p = sub.add_parser("packages", parents=[common],
                       help="list available packages and those installed in an agent")
    p.add_argument("dir", nargs="?", default=None, help="agent directory (optional)")
    p.add_argument("--refresh", action="store_true",
                   help="re-fetch remote registries first")

    p = sub.add_parser("registries", parents=[common],
                       help="package sources: builtin plus any repo/dir you add")
    reg_sub = p.add_subparsers(dest="registries_action")
    reg_sub.add_parser("list", parents=[common], help="show configured registries")
    sp = reg_sub.add_parser("add", parents=[common],
                            help="add a registry (git URL or local packages dir)")
    sp.add_argument("name", help="short registry name")
    sp.add_argument("ref", help="https:// git URL or a local directory path")
    sp.add_argument("--subdir", default="packages",
                    help="directory inside the repo holding packages (default: packages)")
    sp.add_argument("--branch", default="main", help="branch to fetch (default: main)")
    sp = reg_sub.add_parser("remove", parents=[common], help="remove a registry")
    sp.add_argument("name", help="registry name")

    p = sub.add_parser("adopt", parents=[common],
                       help="bring an existing agent under base management")
    adopt_cmd.add_arguments(p)

    p = sub.add_parser("fleet", parents=[common],
                       help="the operator view: status/add/remove registered agents")
    fleet_sub = p.add_subparsers(dest="fleet_action")
    fleet_sub.add_parser("status", parents=[common],
                         help="health + drift of every registered agent")
    sp = fleet_sub.add_parser("add", parents=[common],
                              help="register an agent in the fleet")
    sp.add_argument("dir", help="agent directory (must have .agentboom.json)")
    sp = fleet_sub.add_parser("remove", parents=[common],
                              help="unregister an agent")
    sp.add_argument("name", help="agent name or path")

    p = sub.add_parser("console", parents=[common],
                       help="open the boomkeeper operator session (qwen)")
    p.add_argument("--dry-run", action="store_true",
                   help="refresh the workspace but do not launch qwen")
    p.add_argument("qwen_args", nargs=argparse.REMAINDER,
                   help="extra arguments passed through to qwen")

    p = sub.add_parser("skills", parents=[common], help="list skills in an agent")
    p.add_argument("dir", nargs="?", default=".", help="agent directory (default: cwd)")

    p = sub.add_parser("miniapps", parents=[common], help="list mini-apps in an agent")
    p.add_argument("dir", nargs="?", default=".", help="agent directory (default: cwd)")

    p = sub.add_parser("list", parents=[common],
                       help="discover agentboom agents under a directory")
    p.add_argument("dir", nargs="?", default=".", help="parent directory (default: cwd)")

    sub.add_parser("doctor", parents=[common], help="environment checks")
    sub.add_parser("selfcheck", parents=[common],
                   help="end-to-end QA: init+validate+upgrade+add in a temp dir")
    sub.add_parser("version", parents=[common], help="print version")

    return parser


# ── human output ──────────────────────────────────────────────────

def _print_validate(result: dict) -> None:
    icons = {"error": "ERROR", "warn": "warn ", "info": "info "}
    for check in result["checks"]:
        line = f"[{icons[check['level']]}] {check['id']}: {check['message']}"
        print(line)
    status = "PASS" if result["ok"] else "FAIL"
    print(f"agentboom validate: {status} "
          f"({result['errors']} errors, {result['warnings']} warnings) in {result['agent_dir']}")


def _print_upgrade(result: dict) -> None:
    print(f"agentboom upgrade ({result['mode']}): "
          f"{result['base_version_from']} -> {result['base_version_to']}")
    for key in ("upgraded", "new", "restored", "locally_modified", "stale", "up_to_date"):
        items = result.get(key, [])
        if not items:
            continue
        print(f"  {key} ({len(items)}):")
        for rel in items:
            print(f"    {rel}")
    if not result["changed"]:
        print("  base is up to date")
    elif result["mode"] == "check":
        print("  re-run with --apply to write these changes")


def _print_init(result: dict) -> None:
    print(f"Initialized agent '{result['name']}' at {result['path']}")
    print(f"  template: {result['template']}  base: {result['base_version']}  "
          f"files: {len(result['created'])}  managed: {result['managed_count']}")
    if result["skipped_existing"]:
        print(f"  skipped (already existed): {', '.join(result['skipped_existing'])}")
    print("Next steps:")
    for line in result["next_steps"]:
        print(f"  {line}")


def _print_human(command: str, result: dict) -> None:
    if command == "init":
        _print_init(result)
    elif command == "validate":
        _print_validate(result)
    elif command == "upgrade":
        _print_upgrade(result)
    elif command == "doctor":
        for check in result["checks"]:
            flag = "ok  " if check["ok"] else "FAIL"
            req = "" if check["required"] else " (optional)"
            print(f"[{flag}] {check['name']}{req}: {check['detail']}")
        print(f"agentboom doctor: {'PASS' if result['ok'] else 'FAIL'}")
    elif command == "list":
        if not result["agents"]:
            print(f"No agentboom agents found under {result['parent']}")
        for agent in result["agents"]:
            print(f"{agent['name']}  base={agent['base_version']}  {agent['path']}")
    elif command == "skills":
        if not result["skills"]:
            print("No skills found.")
        for skill in result["skills"]:
            managed = " [base]" if skill["managed"] else ""
            print(f"{skill['name']}{managed} — {skill['description']}")
    elif command == "miniapps":
        if not result["miniapps"]:
            print("No mini-apps found.")
        for app in result["miniapps"]:
            pub = " [public]" if app["public"] else ""
            print(f"{app['name']}{pub} v{app['version']} ({app['status']}, "
                  f"{app['jobs']} jobs) — {app['description']}")
    elif command == "add":
        if "package" in result:
            print(f"Installed package '{result['package']}' into {result['agent_dir']}")
            for rel in result["created"]:
                print(f"  + {rel}")
            for rel in result["skipped_existing"]:
                print(f"  = {rel} (already existed)")
            for line in result["requirements_added"]:
                print(f"  + requirements: {line}")
            for line in result["env_example_added"]:
                print(f"  + .env.example: {line.split('=')[0]}")
            if result["post_install"]:
                print("Post-install steps:")
                for line in result["post_install"]:
                    print(f"  - {line}")
        else:
            print(f"Created {result['kind']} '{result['name']}' at {result['path']}")
            print(f"Next: {result['next']}")
    elif command == "packages":
        print("Available packages:")
        for pkg in result["available"]:
            icon = f"{pkg['icon']} " if pkg.get("icon") else ""
            kind = " [connector]" if pkg.get("kind") == "connector" else ""
            src = "" if pkg.get("source") == "builtin" else f" ({pkg['source']})"
            req = (f" — requires {', '.join(pkg['requires'])}"
                   if pkg.get("requires") else "")
            print(f"  {icon}{pkg['name']}{kind}{src} — {pkg['description']}{req}")
        for reg in result.get("unreachable_registries", []):
            print(f"  [warn] registry '{reg['registry']}' unreachable: {reg['error']}")
        if result["agent_dir"]:
            installed = result["installed"]
            if installed:
                print(f"Installed in {result['agent_dir']}:")
                for name in sorted(installed):
                    print(f"  {name} (since {installed[name]['installed_at']})")
            else:
                print(f"None installed in {result['agent_dir']}")
    elif command == "registries":
        if "added" in result:
            entry = result["added"]
            print(f"Added registry '{entry['name']}' -> "
                  f"{entry.get('url') or entry.get('path')}")
        elif "removed" in result:
            print(f"Removed registry '{result['removed']}'")
        else:
            print("Registries:")
            for reg in result["registries"]:
                print(f"  {reg['name']:12} {reg['source_ref']}")
            print("Add more: agentboom registries add <name> <git-url-or-dir>")
    elif command == "adopt":
        print(f"Adopted '{result['name']}' at {result['agent_dir']}")
        print(f"  managed (byte-identical to base): {len(result['managed_matched'])}")
        print(f"  agent-owned/diverged: {len(result['owned_or_diverged'])}")
        for line in result["next"]:
            print(f"  next: {line}")
    elif command == "fleet":
        if "registered" in result:
            print(f"Registered {result['registered']['name']} ({result['registered']['path']})")
        elif "removed" in result:
            print(f"Removed {result['removed']} from the fleet")
        else:
            if not result["agents"]:
                print("Fleet is empty — `agentboom init <dir>` registers automatically.")
            for row in result["agents"]:
                if not row.get("ok"):
                    print(f"[----] {row['name']}: {row.get('error')}")
                    continue
                drift = len(row["drift_modified"]) + len(row["drift_missing"])
                flag = "ok  " if row["validate_errors"] == 0 else "FAIL"
                pkgs = f" pkgs={','.join(row['packages'])}" if row["packages"] else ""
                print(f"[{flag}] {row['name']}  base={row['base_version']}"
                      f"  drift={drift}  errors={row['validate_errors']}{pkgs}")
                print(f"       {row['path']}")
    elif command == "install-runtime":
        if result.get("installed"):
            print(f"installed runtime '{result['runtime']}'")
        elif result.get("command"):
            print(f"to install '{result['runtime']}' run:\n  {result['command']}")
            print("(or: agentboom install-runtime %s --yes)" % result["runtime"])
        else:
            print(result.get("note", ""))
    elif command == "code":
        print(f"{'Scaffolded' if result['scaffolded'] else 'Using existing'} "
              f"{result['kind']} '{result['name']}' in {result['agent_dir']}")
        if not result.get("launched"):
            print(result.get("note", ""))
    elif command == "console":
        print(f"Boomkeeper workspace refreshed at {result['workspace']}")
        if not result.get("launched"):
            print(result.get("note", ""))
    elif command == "selfcheck":
        for step in result["steps"]:
            flag = "ok  " if step["ok"] else "FAIL"
            detail = step.get("detail") or step.get("error") or ""
            print(f"[{flag}] {step['step']}: {detail}")
        print(f"agentboom selfcheck: {'PASS' if result['ok'] else 'FAIL'}")
    elif command == "version":
        print(result["version"])
    else:
        print(json.dumps(result, indent=2))


def main(argv=None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 2
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            result = init_cmd.run(args)
        elif args.command == "validate":
            result = validate_cmd.run(args)
        elif args.command == "upgrade":
            result = upgrade_cmd.run(args)
        elif args.command == "add":
            if args.add_kind == "skill":
                result = add_cmd.run_skill(args)
            elif args.add_kind == "miniapp":
                result = add_cmd.run_miniapp(args)
            else:
                result = packages_cmd.run_add_package(args)
        elif args.command == "packages":
            result = packages_cmd.run_packages(args)
        elif args.command == "registries":
            action = getattr(args, "registries_action", None)
            if action == "add":
                result = registrycmd.run_add(args)
            elif action == "remove":
                result = registrycmd.run_remove(args)
            else:
                result = registrycmd.run_list(args)
        elif args.command == "adopt":
            result = adopt_cmd.run(args)
        elif args.command == "fleet":
            action = getattr(args, "fleet_action", None)
            if action == "add":
                result = fleetcmd.run_add(args)
            elif action == "remove":
                result = fleetcmd.run_remove(args)
            else:
                result = fleetcmd.run_status(args)
        elif args.command == "install-runtime":
            result = runtimes.install_runtime(args.name, yes=args.yes)
        elif args.command == "code":
            result = (code_cmd.run_miniapp(args) if args.code_kind == "miniapp"
                      else code_cmd.run_skill(args))
        elif args.command == "console":
            result = console_cmd.run(args)
        elif args.command == "skills":
            result = listcmd.run_skills(args)
        elif args.command == "miniapps":
            result = listcmd.run_miniapps(args)
        elif args.command == "list":
            result = listcmd.run_list(args)
        elif args.command == "doctor":
            result = doctor_cmd.run(args)
        elif args.command == "selfcheck":
            result = selfcheck_cmd.run(args)
        elif args.command == "version":
            result = {"ok": True, "version": __version__, "templates": list_templates(),
                      "default_template": DEFAULT_TEMPLATE}
        else:  # pragma: no cover — argparse enforces this
            parser.error(f"unknown command {args.command}")
            return 2
    except (init_cmd.InitError, upgrade_cmd.UpgradeError, add_cmd.AddError,
            code_cmd.CodeError, runtimes.RuntimeError_,
            packages_cmd.PackageError, registrycmd.RegistriesError,
            registries_mod.RegistryError, adopt_cmd.AdoptError,
            fleetcmd.FleetError, console_cmd.ConsoleError,
            TemplateError, FileNotFoundError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"agentboom: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(args.command, result)
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    sys.exit(main())
