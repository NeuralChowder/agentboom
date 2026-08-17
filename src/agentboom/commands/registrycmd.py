"""`agentboom registries` — manage package sources.

A registry is where packages live: the builtin one ships with agentboom;
any other is a local directory or a git repository added here. Packages
from extra registries are installable exactly like builtin ones —
`agentboom add package <name>` searches all of them.
"""
from agentboom import registries as registries_mod


class RegistriesError(RuntimeError):
    pass


def run_list(args) -> dict:
    return {"ok": True, "registries": registries_mod.list_registries()}


def run_add(args) -> dict:
    entry = registries_mod.add_registry(
        args.name.strip().lower(), args.ref,
        subdir=args.subdir, branch=args.branch,
    )
    # Validate eagerly: a bad source should fail at add-time, not at
    # the first `agentboom packages`.
    try:
        registries_mod.registry_packages_dir(
            {**entry, "source": "url" if "url" in entry else "path",
             "source_ref": entry.get("url") or entry.get("path")},
            refresh=True,
        )
    except registries_mod.RegistryError as exc:
        registries_mod.remove_registry(entry["name"])
        raise RegistriesError(f"Registry added then rolled back — {exc}")
    return {"ok": True, "added": entry}


def run_remove(args) -> dict:
    if registries_mod.remove_registry(args.name):
        return {"ok": True, "removed": args.name}
    raise RegistriesError(
        f"'{args.name}' is not a configured registry "
        "(agentboom registries to list)."
    )
