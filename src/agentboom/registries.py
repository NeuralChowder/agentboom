"""Package registries: where `agentboom add package` finds packages.

A registry is anything that contains a `packages/`-style directory: each
package is a directory holding its files + `.agentboom-package.json` meta,
optionally nested one or two category levels deep. The builtin registry
keeps addons and connectors in separate trees (`templates/packages` and
`templates/connectors`) so the kind is visible in the path.

Two source kinds:
  builtin   the packages shipped inside this agentboom installation
  url/path  a local directory, or a git repository fetched over HTTPS

The registry index lives at $AGENTBOOM_HOME/registries.json. It is an
index, never a dependency: deleting it only forgets where extra package
sources live.

Remote repositories are fetched, never executed: files are copied into
the target agent, where the agent owner reviews them like any other code.
Only add registries you trust.
"""
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PACKAGE_META_NAME = ".agentboom-package.json"
REGISTRIES_NAME = "registries.json"
BUILTIN = "builtin"
FETCH_TTL_SEC = 3600  # remote listings stay fresh for an hour


class RegistryError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _home() -> Path:
    home = Path(os.environ.get("AGENTBOOM_HOME", str(Path.home() / ".agentboom")))
    home.mkdir(parents=True, exist_ok=True)
    return home


def registries_path() -> Path:
    return _home() / REGISTRIES_NAME


def cache_root() -> Path:
    root = _home() / "registries"
    root.mkdir(parents=True, exist_ok=True)
    return root


def packages_root() -> Path:
    """Builtin registry, addon tree: feature packages bundled here."""
    return Path(__file__).resolve().parent / "templates" / "packages"


def connectors_root() -> Path:
    """Builtin registry, connector tree: external-service integrations."""
    return Path(__file__).resolve().parent / "templates" / "connectors"


def builtin_roots() -> List[Path]:
    """Both builtin trees, scan order = collision priority."""
    return [packages_root(), connectors_root()]


def load_config() -> dict:
    path = registries_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("registries"), list):
                return data
        except json.JSONDecodeError:
            pass
    return {"registries": []}


def save_config(config: dict) -> None:
    registries_path().write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def list_registries() -> List[dict]:
    """builtin first (always present), then user-configured sources."""
    entries = [{"name": BUILTIN, "source": "builtin", "source_ref": "(bundled)"}]
    for reg in load_config()["registries"]:
        entries.append({
            "name": reg["name"],
            "source": "url" if "url" in reg else "path",
            "source_ref": reg.get("url") or reg.get("path"),
            "subdir": reg.get("subdir", "packages"),
            "branch": reg.get("branch", "main"),
        })
    return entries


def add_registry(name: str, ref: str, subdir: str = "packages",
                 branch: str = "main") -> dict:
    if name == BUILTIN:
        raise RegistryError(f"'{BUILTIN}' is reserved for the bundled packages")
    ref = ref.strip()
    entry: dict = {"name": name}
    if "://" in ref or ref.startswith("git@"):
        entry["url"] = ref.rstrip("/")
        entry["branch"] = branch
    else:
        path = Path(ref).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.is_dir():
            raise RegistryError(f"Not a directory: {path}")
        entry["path"] = str(path)
    entry["subdir"] = subdir.strip("/")

    config = load_config()
    config["registries"] = [r for r in config["registries"] if r["name"] != name]
    config["registries"].append(entry)
    save_config(config)
    return entry


def remove_registry(name: str) -> bool:
    config = load_config()
    before = len(config["registries"])
    config["registries"] = [r for r in config["registries"] if r["name"] != name]
    if len(config["registries"]) == before:
        return False
    save_config(config)
    shutil.rmtree(cache_root() / name, ignore_errors=True)
    return True


# ── resolving a registry to a local directory of packages ─────────


def _github_tarball(url: str, branch: str) -> bytes:
    # https://github.com/ORG/REPO(.git)? -> codeload tarball of the branch.
    parts = url.replace("https://", "").replace("http://", "").split("/")
    if parts[0] != "github.com" or len(parts) < 3:
        raise RegistryError(
            f"Only github.com https URLs are fetchable without git: {url}"
        )
    org, repo = parts[1], parts[2].removesuffix(".git")
    tarball_url = f"https://codeload.github.com/{org}/{repo}/tar.gz/refs/heads/{branch}"
    req = urllib.request.Request(tarball_url, headers={"User-Agent": "agentboom"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as exc:
        raise RegistryError(f"fetch failed for {tarball_url}: {exc}") from exc


def _extract_subdir(tar_bytes: bytes, subdir: str, dest: Path) -> None:
    """Copy <root>/<subdir>/* from a GitHub tarball into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        root_prefix = tar.getnames()[0].split("/", 1)[0] + "/"
        wanted = root_prefix + subdir.rstrip("/") + "/"
        for member in tar.getmembers():
            if not member.name.startswith(wanted) or not member.isfile():
                continue
            rel = member.name[len(wanted):]
            if not rel:
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is not None:
                out.write_bytes(src.read())


def _fetch_remote(reg: dict, refresh: bool) -> Path:
    """Materialise a remote registry into the cache; honour the TTL."""
    name = reg["name"]
    stamp = cache_root() / name / ".fetched-at"
    if stamp.is_file() and not refresh:
        try:
            fetched = float(stamp.read_text(encoding="utf-8").strip())
            if time.time() - fetched < FETCH_TTL_SEC:
                return cache_root() / name / "packages"
        except ValueError:
            pass

    target = cache_root() / name
    shutil.rmtree(target, ignore_errors=True)
    url = reg["url"]
    subdir = reg.get("subdir", "packages")
    if "github.com" in url:
        _extract_subdir(_github_tarball(url, reg.get("branch", "main")),
                         subdir, target / "packages")
    else:
        if shutil.which("git") is None:
            raise RegistryError(
                f"registry '{name}' is a non-GitHub git URL and git is not "
                "on PATH — install git or use a local path registry"
            )
        clone_dir = target / "clone"
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", reg.get("branch", "main"),
             url, str(clone_dir)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RegistryError(f"git clone failed for {url}: {proc.stderr.strip()}")
        shutil.copytree(clone_dir / subdir, target / "packages", dirs_exist_ok=True)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(time.time()), encoding="utf-8")
    return target / "packages"


def iter_package_dirs(root: Path, max_depth: int = 3):
    """Package directories (ones holding `.agentboom-package.json`) under
    `root`, sorted, depth-first, at most `max_depth` levels down — so a
    registry may group packages into category subfolders."""
    def walk(current: Path, depth: int):
        if depth > max_depth:
            return
        try:
            children = sorted(current.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            if (child / PACKAGE_META_NAME).is_file():
                yield child
            else:
                yield from walk(child, depth + 1)
    yield from walk(root, 1)


def _looks_like_packages_dir(path: Path) -> bool:
    return next(iter_package_dirs(path), None) is not None


def registry_packages_dir(reg: dict, refresh: bool = False) -> Path:
    if reg["source"] == "builtin":
        return packages_root()
    if reg["source"] == "path":
        base = Path(reg["source_ref"])
        # Accept either a repo root (packages under `subdir`) or the
        # packages directory itself — whichever actually holds packages.
        for candidate in (base / reg.get("subdir", "packages"), base):
            if candidate.is_dir() and _looks_like_packages_dir(candidate):
                return candidate
        raise RegistryError(
            f"registry '{reg['name']}': no packages found under {base} "
            f"(looked in '{reg.get('subdir', 'packages')}/' and the dir itself)"
        )
    return _fetch_remote(reg, refresh=refresh)


def discover_packages(refresh: bool = False) -> List[dict]:
    """Every package across every registry, tagged with its source.

    The builtin registry wins on name collisions (addon tree before
    connector tree); among remote registries, the first configured one
    wins. Deterministic and predictable.
    """
    out: List[dict] = []
    seen = set()
    for reg in list_registries():
        if reg["source"] == "builtin":
            roots = [r for r in builtin_roots() if r.is_dir()]
        else:
            try:
                root = registry_packages_dir(reg, refresh=refresh)
            except RegistryError as exc:
                out.append({"name": f"(registry {reg['name']} unreachable)",
                            "source": reg["name"], "error": str(exc),
                            "description": "", "kind": "addon"})
                continue
            roots = [root]
        for root in roots:
            if not root.is_dir():
                continue
            for pkg_dir in iter_package_dirs(root):
                meta_path = pkg_dir / PACKAGE_META_NAME
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                name = meta.get("name", pkg_dir.name)
                if name in seen:
                    continue
                seen.add(name)
                out.append({
                    "name": name,
                    "description": meta.get("description", ""),
                    "kind": meta.get("kind", "addon"),
                    "icon": meta.get("icon", ""),
                    "requires": meta.get("requires", []),
                    "source": reg["name"],
                    "path": str(pkg_dir),
                })
    return out
