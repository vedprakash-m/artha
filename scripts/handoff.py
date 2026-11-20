#!/usr/bin/env python3
"""Owner-attested one-laptop handoff manifest for Light mode."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from lib.light_mode import configured_cutover, host_id, require_owner_terminal
from lib.state_targets import light_markdown_targets, relative_name
from lib.sync_conflicts import ConflictScanUnknown, find_conflicts
from lib.local_paths import local_dir

MANIFEST_NAME = ".handoff.json"
MAX_AGE_HOURS = 168.0  # a weekend or a week away; the hash comparison is the real check


@dataclass(frozen=True)
class HandoffResult:
    status: str
    message: str
    changed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


def _manifest_path(root: Path) -> Path:
    return Path(root) / "state" / MANIFEST_NAME


def _acceptance_path(root: Path) -> Path:
    identity = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return local_dir() / "light-handoff" / f"{identity}.json"


def _targets(root: Path, targets: Iterable[Path] | None) -> tuple[Path, ...]:
    if targets is None:
        return light_markdown_targets(Path(root))
    return tuple(sorted((Path(path) for path in targets), key=lambda path: path.as_posix()))


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def seal(
    root: Path,
    *,
    targets: Iterable[Path] | None = None,
    owner_confirmed_sync: bool,
    now: datetime | None = None,
    hostname: str | None = None,
) -> HandoffResult:
    """Seal a local handoff manifest after the owner confirms sync is complete."""
    if not owner_confirmed_sync:
        return HandoffResult(
            "unverified",
            "owner confirmation is required after OneDrive shows up to date; no manifest written",
        )
    root = Path(root)
    selected = _targets(root, targets)
    manifest = _manifest_path(root)
    try:
        for target in (*selected, manifest):
            conflicts = find_conflicts(
                target,
                canonical_targets=(*selected, manifest),
                hostnames=((hostname or host_id()),),
            )
            if conflicts:
                return HandoffResult(
                    "refused",
                    f"conflict copy present for {relative_name(target, root)}: "
                    + ", ".join(relative_name(path, root) for path in conflicts),
                )
    except ConflictScanUnknown as exc:
        return HandoffResult("refused", str(exc))

    from lib.write_refusals import RefusalStateUnknown, list_refusals

    try:
        refusals = list_refusals()
    except RefusalStateUnknown as exc:
        return HandoffResult("refused", str(exc))
    if refusals:
        return HandoffResult(
            "refused",
            f"{len(refusals)} State File write refusal(s) are uninspected; inspect and clear them "
            "(write_refusals.py list|clear) before sealing",
        )
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    files = {
        relative_name(path, root): _digest(path)
        for path in selected
    }
    payload: dict[str, object] = {
        "schema_version": 1,
        "sealed_at": stamp.isoformat(timespec="seconds"),
        "host": hostname or host_id(),
        "owner_confirmed_sync": True,
        "files": files,
    }
    try:
        _atomic_json(manifest, payload)
    except OSError as exc:
        return HandoffResult("refused", f"handoff manifest could not be written: {type(exc).__name__}")
    return HandoffResult(
        "sealed",
        f"handoff sealed for {len(files)} in-scope State Files",
    )


def verify(
    root: Path,
    *,
    targets: Iterable[Path] | None = None,
    now: datetime | None = None,
    record: bool = True,
) -> HandoffResult:
    """Compare the selected State Files with the latest handoff manifest.

    ``record=False`` (hooks) never writes the machine-local acceptance record.
    """
    root = Path(root)
    selected = _targets(root, targets)
    manifest = _manifest_path(root)
    if not manifest.is_file():
        return HandoffResult(
            "unverified",
            "no handoff manifest; stop writes until the other laptop is confirmed stopped and synced",
        )
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        sealed_at = datetime.fromisoformat(str(payload["sealed_at"]).replace("Z", "+00:00"))
        files = payload["files"]
        if payload.get("owner_confirmed_sync") is not True or not isinstance(files, dict):
            raise ValueError("invalid handoff manifest")
    except (OSError, UnicodeDecodeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return HandoffResult("unverified", "handoff manifest is unreadable or invalid")
    manifest_revision = hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    acceptance = _acceptance_path(root)
    try:
        accepted = json.loads(acceptance.read_text(encoding="utf-8"))
        if accepted.get("manifest_revision") == manifest_revision:
            if conflicts := _manifest_conflicts(root, selected, manifest, payload):
                return conflicts
            return HandoffResult(
                "verified",
                "handoff verified and active on this laptop; local changes are allowed",
            )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        pass
    if conflicts := _manifest_conflicts(root, selected, manifest, payload):
        return conflicts
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_hours = (stamp - sealed_at).total_seconds() / 3600
    if age_hours < 0 or age_hours > MAX_AGE_HOURS:
        return HandoffResult("unverified", "handoff manifest is stale or has a future timestamp")

    expected = {relative_name(path, root) for path in selected}
    recorded = set(files)
    extra = tuple(sorted(recorded - expected))
    missing_targets = tuple(sorted(expected - recorded))
    changed: list[str] = []
    missing: list[str] = []
    for target in selected:
        name = relative_name(target, root)
        recorded_digest = files.get(name)
        if not target.is_file():
            if recorded_digest is not None:
                missing.append(name)
            continue
        current = _digest(target)
        if current != recorded_digest:
            changed.append(name)
    if extra or missing_targets:
        missing.extend(missing_targets)
    if changed or missing or extra:
        detail = "; ".join(
            part
            for part in (
                f"changed: {', '.join(changed)}" if changed else "",
                f"missing: {', '.join(missing)}" if missing else "",
                f"extra entries: {', '.join(extra)}" if extra else "",
            )
            if part
        )
        return HandoffResult(
            "unverified",
            f"handoff differs from this laptop ({detail})",
            changed=tuple(sorted(changed)),
            missing=tuple(sorted(missing)),
        )
    if not record:
        return HandoffResult("verified", "handoff verified as far as this laptop can see")
    try:
        _atomic_json(
            acceptance,
            {
                "schema_version": 1,
                "manifest_revision": manifest_revision,
                "accepted_at": stamp.isoformat(timespec="seconds"),
                "host": host_id(),
            },
        )
    except OSError as exc:
        return HandoffResult(
            "unverified",
            f"handoff matched but local acceptance could not be recorded: {type(exc).__name__}",
        )
    return HandoffResult(
        "verified",
        "handoff verified as far as this laptop can see",
    )


def _manifest_conflicts(
    root: Path,
    selected: tuple[Path, ...],
    manifest: Path,
    payload: dict[str, object],
) -> HandoffResult | None:
    try:
        source_host = str(payload.get("host") or "")
        for target in (*selected, manifest):
            conflicts = find_conflicts(
                target,
                canonical_targets=(*selected, manifest),
                hostnames=(host_id(), source_host),
            )
            if conflicts:
                return HandoffResult(
                    "unverified",
                    f"conflict copy present for {relative_name(target, root)}: "
                    + ", ".join(relative_name(path, root) for path in conflicts),
                )
    except ConflictScanUnknown as exc:
        return HandoffResult("unverified", str(exc))
    return None


def auto_seal(
    root: Path,
    *,
    targets: Iterable[Path] | None = None,
    now: datetime | None = None,
    hostname: str | None = None,
) -> HandoffResult:
    """Seal automatically at the end of a session, for the laptop that is already the active one.

    It seals only in Light mode, only when no manifest exists yet or this laptop has accepted the
    current one, and only when the in-scope files changed. It keeps every ``seal`` refusal
    (conflict copies, uninspected write refusals). The sealing laptop records its own acceptance,
    so its later edits are not read as a mismatch. It cannot know the other laptop is stopped.
    """
    root = Path(root)
    if configured_cutover(root):
        return HandoffResult("skipped", "the Engine is enabled; Light-mode handoff does not apply")
    selected = _targets(root, targets)
    manifest = _manifest_path(root)
    if manifest.is_file():
        state = verify(root, targets=selected, now=now, record=False)
        if state.status != "verified" and state.message.startswith("conflict copy present"):
            return HandoffResult("refused", state.message)
        if state.status != "verified":
            return HandoffResult("skipped", f"not the active laptop: {state.message}")
        try:
            sealed = json.loads(manifest.read_text(encoding="utf-8")).get("files")
        except (OSError, UnicodeDecodeError, ValueError):
            sealed = None
        current = {relative_name(path, root): _digest(path) for path in selected}
        if sealed == current:
            return HandoffResult("skipped", "nothing changed since the last seal")
    result = seal(root, targets=selected, owner_confirmed_sync=True, now=now, hostname=hostname)
    if result.status != "sealed":
        return result
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        _atomic_json(
            _acceptance_path(root),
            {
                "schema_version": 1,
                "manifest_revision": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "accepted_at": stamp.isoformat(timespec="seconds"),
                "host": hostname or host_id(),
            },
        )
    except OSError as exc:
        return HandoffResult("refused", f"sealed, but local acceptance could not be recorded: {type(exc).__name__}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Light-mode laptop handoff")
    parser.add_argument("command", choices=("seal", "verify", "auto-seal"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--owner-confirmed",
        action="store_true",
        help="seal only after the owner confirms OneDrive reports up to date",
    )
    args = parser.parse_args(argv)
    if args.command == "seal":
        if not require_owner_terminal("handoff.py seal"):
            return 20
        result = seal(args.root, owner_confirmed_sync=args.owner_confirmed)
    elif args.command == "auto-seal":
        # Runs from the session-end hook: never fails the session, quiet unless something needs attention.
        result = auto_seal(args.root)
        if result.status in {"sealed", "skipped"}:
            return 0
        print(f"handoff not sealed ({result.status}): {result.message}", file=sys.stderr)
        return 0
    else:
        result = verify(args.root)
    stream = sys.stdout if result.status in {"sealed", "verified"} else sys.stderr
    print(f"{result.status}: {result.message}", file=stream)
    return 0 if result.status in {"sealed", "verified"} else 20


if __name__ == "__main__":
    raise SystemExit(main())
