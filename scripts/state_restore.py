#!/usr/bin/env python3
"""List, inspect and restore one previous State File revision safely."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from foundation import get_age_vault
from lib.light_mode import require_owner_terminal
from lib.local_paths import local_dir
from lib.state_snapshot import list_snapshots
from lib.state_writer import (
    StateWriteConflict,
    create_if_absent_token,
    read_versioned,
    write as state_write,
)

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"


def _target(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(STATE_DIR.resolve()):
        raise ValueError("target must be a State File under state/")
    if not (resolved.name.endswith(".md") or resolved.name.endswith(".md.age")):
        raise ValueError("only Markdown State Files are supported")
    return resolved


def _domain(path: Path) -> str:
    name = path.name
    if name.endswith(".md.age"):
        return name[: -len(".md.age")]
    if name.endswith(".md"):
        return name[: -len(".md")]
    raise ValueError("not a Markdown State File")


def snapshots_for(path: Path) -> list[Path]:
    if path.name.endswith(".md.age"):
        return [
            candidate
            for number in range(1, 4)
            if (candidate := path.with_name(f"{path.name}.v{number}")).is_file()
        ]
    return list_snapshots(_domain(path), artha_dir=ROOT, target=path)


def read_snapshot(path: Path, snapshot: Path) -> str:
    vault = get_age_vault(state_dir=snapshot.parent)
    return vault.read_artifact(snapshot)


def _preserve_current(path: Path) -> Path:
    quarantine = local_dir() / "state-restore-quarantine"
    quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = quarantine / path.name
    if destination.exists():
        counter = 1
        while destination.with_name(f"{destination.name}.{counter}").exists():
            counter += 1
        destination = destination.with_name(f"{destination.name}.{counter}")
    descriptor, temporary = tempfile.mkstemp(dir=quarantine, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(path.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return destination


def apply_snapshot(path: Path, snapshot: Path) -> int:
    content = read_snapshot(path, snapshot)
    if path.name.endswith(".md.age"):
        from catchup_core.vault import (
            VaultConflict,
            VaultUncertain,
            record_ids,
        )

        vault = get_age_vault(state_dir=path.parent)
        exists, revision = vault.revision_for(_domain(path))
        previous = None
        if exists:
            try:
                previous = vault.read(_domain(path))
            except Exception:
                _preserve_current(path)
        try:
            approved_removals = (
                record_ids(previous) - record_ids(content)
                if previous is not None
                else ()
            )
            vault.write(
                _domain(path),
                content,
                previous=previous if previous is not None else content,
                expected_revision=revision,
                approved_removals=approved_removals,
            )
        except VaultConflict:
            return 20
        except VaultUncertain:
            return 30
        except Exception:
            return 20
    else:
        expected = (
            read_versioned(path)[1]
            if path.exists()
            else create_if_absent_token(path)
        )
        try:
            result = state_write(
                path,
                content,
                domain=_domain(path),
                source="state_restore",
                expected_token=expected,
            )
        except StateWriteConflict:
            return 20
        if result.uncertain:
            return 30
        if not result.success:
            return 20
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore one Light-mode State File revision")
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list")
    list_parser.add_argument("target")
    show_parser = sub.add_parser("show")
    show_parser.add_argument("target")
    show_parser.add_argument("snapshot")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("target")
    apply_parser.add_argument("snapshot")
    args = parser.parse_args(argv)
    if args.command == "apply" and not require_owner_terminal("state_restore.py apply"):
        return 20
    try:
        path = _target(args.target)
        snapshots = snapshots_for(path)
        if args.command == "list":
            for snapshot in snapshots:
                print(snapshot.name)
            return 0
        selected = next((item for item in snapshots if item.name == args.snapshot), None)
        if selected is None:
            print("snapshot not found", file=sys.stderr)
            return 20
        if args.command == "show":
            sys.stdout.write(read_snapshot(path, selected))
            return 0
        return apply_snapshot(path, selected)
    except (OSError, ValueError) as exc:
        print(f"restore refused: {exc}", file=sys.stderr)
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
