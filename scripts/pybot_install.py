"""``pybot install`` CLI — thin wrapper around :mod:`core.plugin_sdk.installer`.

Examples
--------

Install a local extension::

    python scripts/pybot_install.py install ./my-skill

Install from git::

    python scripts/pybot_install.py install https://github.com/user/pybot-skill.git

List installed::

    python scripts/pybot_install.py list

Remove::

    python scripts/pybot_install.py uninstall my-skill

The marketplace lookup is currently delegated to ``workspace/marketplace.json``
(see W5).  When that file is present, ``install <name>`` resolves the name
through it; otherwise a marketplace install fails fast with a clear error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.plugin_sdk.installer import (
    InstallSource,
    InstallSourceKind,
    install,
    list_installed,
    uninstall,
)
from core.plugin_sdk.manifest import ManifestError
from core.plugin_sdk.marketplace import MarketplaceError, MarketplaceIndex


_DEFAULT_MARKETPLACE = Path("workspace/marketplace.json")


def _load_marketplace() -> MarketplaceIndex:
    if not _DEFAULT_MARKETPLACE.exists():
        raise SystemExit(
            f"marketplace not found ({_DEFAULT_MARKETPLACE}); use a git URL or local path"
        )
    try:
        return MarketplaceIndex.from_path(_DEFAULT_MARKETPLACE)
    except MarketplaceError as exc:
        raise SystemExit(str(exc)) from exc


def _marketplace_resolver(name: str) -> InstallSource:
    index = _load_marketplace()
    entry = index.find(name)
    if entry is None:
        raise SystemExit(f"marketplace entry {name!r} not found in {_DEFAULT_MARKETPLACE}")
    url = entry.install_url()
    kind = (
        InstallSourceKind.LOCAL
        if Path(url).expanduser().exists()
        else InstallSourceKind.GIT
    )
    return InstallSource(kind=kind, location=url)


def _cmd_install(args: argparse.Namespace) -> int:
    try:
        result = install(
            args.source,
            extensions_root=args.extensions_root,
            marketplace_resolver=_marketplace_resolver,
            grant_permissions=set(args.permissions or []) if args.permissions else None,
            dry_run=args.dry_run,
        )
    except (ManifestError, PermissionError, RuntimeError) as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 2

    suffix = " (dry-run)" if args.dry_run else ""
    upgraded = (
        f" (upgraded from {result.upgraded_from})" if result.upgraded_from else ""
    )
    print(
        f"OK{suffix}: {result.manifest.kind.value} '{result.manifest.id}' "
        f"v{result.manifest.version} -> {result.install_dir}{upgraded}"
    )
    if result.manifest.permissions:
        print("  permissions: " + ", ".join(result.manifest.permissions))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    manifests = list_installed(args.extensions_root)
    if not manifests:
        print("(no extensions installed)")
        return 0
    for m in manifests:
        print(f"{m.kind.value:9s} {m.id:30s} v{m.version}  {m.description}")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    ok = uninstall(args.id, extensions_root=args.extensions_root)
    if not ok:
        print(f"not installed: {args.id}", file=sys.stderr)
        return 1
    print(f"removed {args.id}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    try:
        index = _load_marketplace()
    except SystemExit:
        raise
    results = index.search(args.query or "", kind=args.kind)
    if not results:
        print("(no matching entries)")
        return 0
    print(f"{'kind':9s} {'id':30s} {'version':10s} description")
    for entry in results:
        print(f"{entry.kind:9s} {entry.id:30s} {entry.version:10s} {entry.description}")
    return 0


def _cmd_upgrade(args: argparse.Namespace) -> int:
    """Upgrade a single installed extension by re-installing from its source."""
    installed = list_installed(args.extensions_root)
    target = next((m for m in installed if m.id == args.id), None)
    if target is None:
        print(f"not installed: {args.id}", file=sys.stderr)
        return 1
    # Default behaviour: look up in the marketplace and re-install.
    args2 = argparse.Namespace(
        source=args.id,
        extensions_root=args.extensions_root,
        permissions=None,
        dry_run=False,
    )
    return _cmd_install(args2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pybot")
    parser.add_argument(
        "--extensions-root",
        default=None,
        help="override workspace/extensions/ root",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_install = sub.add_parser("install", help="install or upgrade an extension")
    p_install.add_argument("source", help="local path, git URL or marketplace id")
    p_install.add_argument(
        "--permissions",
        nargs="*",
        help="explicitly grant permissions (default: grant everything declared)",
    )
    p_install.add_argument("--dry-run", action="store_true")
    p_install.set_defaults(func=_cmd_install)

    p_list = sub.add_parser("list", help="list installed extensions")
    p_list.set_defaults(func=_cmd_list)

    p_uninstall = sub.add_parser("uninstall", help="remove an installed extension")
    p_uninstall.add_argument("id")
    p_uninstall.set_defaults(func=_cmd_uninstall)

    p_search = sub.add_parser("search", help="search the local marketplace index")
    p_search.add_argument("query", nargs="?", default="")
    p_search.add_argument("--kind", default=None, help="filter by kind (skill/agent/plugin/channel/mcp)")
    p_search.set_defaults(func=_cmd_search)

    p_upgrade = sub.add_parser("upgrade", help="re-install an extension from its marketplace source")
    p_upgrade.add_argument("id")
    p_upgrade.set_defaults(func=_cmd_upgrade)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
