#!/usr/bin/env python3
"""Target-anchored OneDrive conflict-copy detection for State Files."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


class ConflictScanUnknown(RuntimeError):
    """The directory could not be inspected, so its conflict state is unknown."""


def runtime_hostnames() -> tuple[str, ...]:
    """Return the local hostname without persisting personal hostnames in code."""
    from lib.light_mode import host_id

    hostname = host_id()
    names = [hostname] if hostname else []
    try:
        import json
        manifest = Path(__file__).resolve().parents[2] / "state" / ".handoff.json"
        source = json.loads(manifest.read_text(encoding="utf-8")).get("host")
        if isinstance(source, str) and source.strip() not in names:
            names.append(source.strip())
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        pass
    return tuple(names)


def _plain_pattern(stem: str, hostnames: tuple[str, ...]) -> re.Pattern[str]:
    suffixes = [
        re.escape("-conflict"),
        re.escape("-sync-conflict"),
        r"\s\(\d+\)",
        r"-DESKTOP-[A-Za-z0-9_.-]+",
        r"-LAPTOP-[A-Za-z0-9_.-]+",
        r"-PC-[A-Za-z0-9_.-]+",
        # A device-name suffix has an uppercase letter; dated or lowercase names are not conflicts.
        r"-(?-i:(?=.*[A-Z]))[A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)*",
        r".*conflicted\s+copy",
    ]
    suffixes.extend(
        re.escape("-" + hostname) + r"(?:-\d+)?"
        for hostname in hostnames
        if hostname
    )
    return re.compile(
        r"^" + re.escape(stem) + r"(?:" + "|".join(suffixes) + r")$",
        re.IGNORECASE,
    )


def _conflict_name(target_name: str, hostnames: tuple[str, ...]) -> bool:
    if target_name.endswith(".md.age"):
        stem = target_name[: -len(".md.age")]
        without_age = _plain_pattern(f"{stem}.md", hostnames)
        before_md = _plain_pattern(stem, hostnames)
        candidate_before_age = target_name
        if candidate_before_age.endswith(".age"):
            candidate_before_age = candidate_before_age[:-len(".age")]
        # Examples: finance.md-HOST.age and finance-HOST.md.age.
        return without_age.fullmatch(candidate_before_age) is not None or (
            target_name.endswith(".age") and
            before_md.fullmatch(target_name[:-len(".md.age")]) is not None
        )
    if target_name.endswith(".md"):
        stem = target_name[:-len(".md")]
        return _plain_pattern(stem, hostnames).fullmatch(
            target_name[:-len(".md")]
        ) is not None
    if target_name.startswith("."):
        return _plain_pattern(target_name, hostnames).fullmatch(
            target_name
        ) is not None
    return False


def is_conflict_copy(
    target: Path,
    candidate: Path,
    *,
    hostnames: Iterable[str] | None = None,
) -> bool:
    """Return whether *candidate* has a reviewed conflict shape for *target*."""
    target = Path(target)
    candidate = Path(candidate)
    if candidate.name.casefold() == target.name.casefold():
        return False
    hosts = tuple(hostnames or runtime_hostnames())
    if target.name.endswith(".md.age"):
        stem = target.name[: -len(".md.age")]
        age_stem = candidate.name[:-len(".age")] if candidate.name.lower().endswith(".age") else candidate.name
        if candidate.name.lower().endswith(".age"):
            if _plain_pattern(f"{stem}.md", hosts).fullmatch(age_stem):
                return True
            if age_stem.lower().endswith(".md"):
                return _plain_pattern(stem, hosts).fullmatch(age_stem[:-len(".md")]) is not None
        return False
    if target.name.endswith(".md"):
        stem = target.name[:-len(".md")]
        if candidate.name.lower().endswith(".md"):
            return _plain_pattern(stem, hosts).fullmatch(
                candidate.name[:-len(".md")]
            ) is not None
        return False
    # Other targets (the handoff manifest, session marker): the sync client inserts the suffix before
    # the extension, but a suffix after the whole name is also recognised.
    stem, extension = os.path.splitext(target.name)
    candidate_stem, candidate_extension = os.path.splitext(candidate.name)
    if candidate_extension.casefold() == extension.casefold() and _plain_pattern(
        stem, hosts
    ).fullmatch(candidate_stem):
        return True
    return _plain_pattern(target.name, hosts).fullmatch(candidate.name) is not None


def find_conflicts(
    target: Path,
    *,
    canonical_targets: Iterable[Path] = (),
    ignored: Iterable[Path] = (),
    hostnames: Iterable[str] | None = None,
) -> list[Path]:
    """List conflict copies beside *target*; scan failure is never clean."""
    target = Path(target)
    canonical = {Path(path).absolute() for path in canonical_targets}
    if not canonical:
        try:
            repository = Path(__file__).resolve().parents[2]
            target.resolve().relative_to(repository)
            from lib.state_targets import registered_state_paths
            canonical.update(path.absolute() for path in registered_state_paths(repository))
        except (OSError, ValueError, ImportError):
            pass
    ignored_paths = {Path(path).absolute() for path in ignored}
    try:
        with os.scandir(target.parent) as entries:
            candidates = [Path(entry.path) for entry in entries if entry.is_file()]
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ConflictScanUnknown(
            f"{target.parent} could not be scanned for State File conflicts: {type(exc).__name__}"
        ) from exc
    structural_targets = canonical | {
        candidate.absolute()
        for candidate in candidates
        if candidate.name.endswith((".md", ".md.age", ".yaml", ".yaml.age", ".json", ".json.age"))
        and not re.search(
            r"(?:-conflict|-sync-conflict|\s\(\d+\)|conflicted copy|-DESKTOP-|-LAPTOP-|-PC-)",
            candidate.name,
            re.IGNORECASE,
        )
    }
    found: list[Path] = []
    for candidate in candidates:
        if candidate.absolute() in canonical or candidate.absolute() in ignored_paths:
            continue
        if not is_conflict_copy(target, candidate, hostnames=hostnames):
            continue
        longer_targets = [
            other
            for other in structural_targets
            if other != target.absolute()
            and len(other.name) > len(target.name)
            and is_conflict_copy(other, candidate, hostnames=hostnames)
        ]
        if longer_targets:
            continue
        found.append(candidate)
    return sorted(found, key=lambda path: path.name.casefold())


def scan_targets(
    targets: Iterable[Path],
    *,
    ignored: Iterable[Path] = (),
    hostnames: Iterable[str] | None = None,
) -> dict[Path, list[Path]]:
    """Return only targets with visible conflicts."""
    targets = tuple(Path(path) for path in targets)
    return {
        target: found
        for target in targets
        if (found := find_conflicts(
            target,
            canonical_targets=targets,
            ignored=ignored,
            hostnames=hostnames,
        ))
    }


def discover_conflict_copies(
    state_dir: Path,
    *,
    canonical_targets: Iterable[Path] = (),
    ignored: Iterable[Path] = (),
    hostnames: Iterable[str] | None = None,
) -> list[Path]:
    """Discover registered-target conflicts, recursively and fail-closed."""
    state_dir = Path(state_dir)
    targets = tuple(Path(path) for path in canonical_targets)
    if targets:
        return sorted(
            (
                conflict
                for target, conflicts in scan_targets(
                    targets,
                    ignored=ignored,
                    hostnames=hostnames,
                ).items()
                for conflict in conflicts
            ),
            key=lambda path: path.as_posix().casefold(),
        )

    # Compatibility for isolated preflight fixtures that have no registry.
    legacy = re.compile(
        r"(?:-DESKTOP-[A-Za-z0-9_.-]+|-LAPTOP-[A-Za-z0-9_.-]+|-PC-[A-Za-z0-9_.-]+"
        r"|-(?:[A-Za-z0-9_.]+-)+\d+|-conflict|-sync-conflict|\s\(\d+\)"
        r"|.*conflicted\s+copy)",
        re.IGNORECASE,
    )
    found: list[Path] = []
    try:
        with os.scandir(state_dir) as entries:
            for entry in entries:
                if entry.is_file() and legacy.search(entry.name):
                    path = Path(entry.path)
                    if path.absolute() not in {Path(value).absolute() for value in ignored}:
                        found.append(path)
    except OSError as exc:
        raise ConflictScanUnknown(
            f"{state_dir} could not be scanned for State File conflicts: {type(exc).__name__}"
        ) from exc
    return sorted(found, key=lambda path: path.name.casefold())
