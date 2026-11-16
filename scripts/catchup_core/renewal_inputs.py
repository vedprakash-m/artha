"""Bounded source identities for renewal planning; these comparisons grant no authority.

The caller must authenticate the baseline and component policy independently. This
module performs no I/O, imports no candidate code and never issues a release decision.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_SCHEMA = "artha.renewal-source.v1"
_MAX_BYTES = 1024 * 1024
_MAX_FILES = 10_000
_ERROR = "invalid renewal source manifest"


class InputRejected(ValueError):
    """Unusable metadata; the error never contains caller-controlled values."""


def _path(value: Any) -> bool:
    return (isinstance(value, str) and len(value) <= 1024
            and bool(re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value))
            and not any(part in {".", "..", ".git"} for part in value.split("/")))


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    mode: str

    def __post_init__(self) -> None:
        if (not _path(self.path) or not isinstance(self.sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
                or self.mode not in ("100644", "100755")):
            raise InputRejected(_ERROR)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise InputRejected(_ERROR)
        document[key] = value
    return document


def _constant(_value: str) -> None:
    raise InputRejected(_ERROR)


@dataclass(frozen=True)
class SourceManifest:
    files: tuple[SourceFile, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.files, tuple) or len(self.files) > _MAX_FILES
                or any(not isinstance(entry, SourceFile) for entry in self.files)
                or len({entry.path.casefold() for entry in self.files}) != len(self.files)):
            raise InputRejected(_ERROR)
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda entry: entry.path)))
        if len(self.encode()) > _MAX_BYTES:
            raise InputRejected(_ERROR)

    @classmethod
    def parse(cls, raw: bytes) -> SourceManifest:
        if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
            raise InputRejected(_ERROR)
        try:
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                                  parse_constant=_constant)
            if (not isinstance(document, dict) or set(document) != {"schema", "files"}
                    or document["schema"] != _SCHEMA or not isinstance(document["files"], list)
                    or len(document["files"]) > _MAX_FILES):
                raise InputRejected(_ERROR)
            files = []
            for entry in document["files"]:
                if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "mode"}:
                    raise InputRejected(_ERROR)
                files.append(SourceFile(entry["path"], entry["sha256"], entry["mode"]))
            return cls(tuple(files))
        except (ValueError, TypeError, RecursionError):
            raise InputRejected(_ERROR) from None

    def encode(self) -> bytes:
        return json.dumps({"schema": _SCHEMA, "files": [
            {"path": entry.path, "sha256": entry.sha256, "mode": entry.mode} for entry in self.files
        ]}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.encode()).hexdigest()


@dataclass(frozen=True)
class SourceComparison:
    changed: tuple[str, ...]
    held: tuple[str, ...]


def compare_sources(root: SourceManifest, target: SourceManifest, *,
                    routine: frozenset[str], protected: frozenset[str]) -> SourceComparison:
    """Compare the approved root, never just the preceding automatic renewal.

    An ordinary content change is merely eligible for further checks. Additions,
    deletions, executable-mode changes and unknown/protected files require review.
    A matching manifest says nothing about current file custody or permissions.
    """
    if (not isinstance(root, SourceManifest) or not isinstance(target, SourceManifest)
            or not isinstance(routine, frozenset) or not isinstance(protected, frozenset)
            or any(not _path(path) for path in routine | protected)):
        raise InputRejected(_ERROR)
    before = {entry.path: entry for entry in root.files}
    after = {entry.path: entry for entry in target.files}
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    held = tuple(path for path in changed if path not in routine or path in protected
                 or path not in before or path not in after or before[path].mode != after[path].mode)
    return SourceComparison(tuple(changed), held)
