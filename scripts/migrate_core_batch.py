"""Apply a physical core-module reorganization batch.

This helper is intentionally conservative:

- uses ``git mv`` so history stays attached
- writes compatibility stubs at the old flat paths
- skips entries that are already migrated

Usage:

    python scripts/migrate_core_batch.py --list
    python scripts/migrate_core_batch.py batch0-runtime-leaves --dry-run
    python scripts/migrate_core_batch.py batch1-runtime-foundation --apply
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BATCHES: dict[str, dict[str, str]] = {
    "batch0-runtime-leaves": {
        "core/errors.py": "core/systems/runtime/errors.py",
        "core/path_utils.py": "core/systems/runtime/path_utils.py",
        "core/retry_policy.py": "core/systems/runtime/retry_policy.py",
        "core/version.py": "core/systems/runtime/version.py",
        "core/yaml_config.py": "core/systems/runtime/yaml_config.py",
    },
    "batch1-runtime-foundation": {
        "core/entrypoints.py": "core/systems/runtime/entrypoints.py",
        "core/diagnostics.py": "core/systems/runtime/diagnostics.py",
        "core/cost_tracker.py": "core/systems/runtime/cost_tracker.py",
        "core/observability.py": "core/systems/runtime/observability.py",
        "core/private_state.py": "core/systems/context/private_state.py",
    },
}


def run_git_mv(source: Path, target: Path, *, apply: bool) -> None:
    if not apply:
        print(f"DRY-RUN git mv {source} {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "mv", str(source), str(target)], cwd=REPO_ROOT, check=True)


def write_stub(source: Path, target: Path, *, apply: bool) -> None:
    target_module = ".".join(target.with_suffix("").parts)
    content = (
        f'"""Compatibility stub for moved module `{target_module}`."""\n\n'
        f"from {target_module} import *  # noqa: F401,F403\n"
    )
    if not apply:
        print(f"DRY-RUN stub {source} -> {target_module}")
        return
    source.write_text(content, encoding="utf-8")


def migrate_batch(name: str, *, apply: bool) -> int:
    manifest = BATCHES[name]
    changed = 0
    for source_text, target_text in manifest.items():
        source = REPO_ROOT / source_text
        target = REPO_ROOT / target_text
        if not source.exists():
            if target.exists():
                print(f"SKIP already moved: {source_text} -> {target_text}")
                continue
            raise FileNotFoundError(f"Missing source file: {source_text}")
        run_git_mv(source, target, apply=apply)
        write_stub(source, target, apply=apply)
        changed += 1
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", nargs="?", choices=sorted(BATCHES))
    parser.add_argument("--list", action="store_true", help="List available batches")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without changing files")
    parser.add_argument("--apply", action="store_true", help="Apply the batch")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list:
        for batch_name, manifest in BATCHES.items():
            print(batch_name)
            for source, target in manifest.items():
                print(f"  {source} -> {target}")
        return 0

    if not args.batch:
        parser.error("batch is required unless --list is used")

    apply = args.apply and not args.dry_run
    if not args.apply and not args.dry_run:
        parser.error("choose either --apply or --dry-run")

    changed = migrate_batch(args.batch, apply=apply)
    mode = "APPLIED" if apply else "PREVIEWED"
    print(f"{mode} {changed} moves in {args.batch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
