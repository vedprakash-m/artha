"""Machine-private dependency metadata, never native secret or activation authority."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from catchup_core.session_lock import SessionBusy, installation_fence, session_lock

if TYPE_CHECKING:
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory


class RegistryError(RuntimeError):
    pass


_SCHEMA = "artha.recovery-registry.v1"
_KINDS = frozenset({"key", "key_head", "credential", "client", "bundle", "privacy", "vault", "spend_seal"})
# The resource spend seal is a private file kept outside every snapshot. The registry only names it, so
# capture can tell a known seal from an unknown file; it holds no value and is never restored.
SPEND_SEAL = ("artha-spend-seal", "seal")
SPEND_SEAL_ACCOUNTS = frozenset({'seal', 'seal.shadow', 'seal.authoritative'})
_STATES = frozenset({"pending", "active", "deleted", "declared"})
_MAX_BYTES = 4 * 1024 * 1024
_MAX_ENTRIES = 10000
_ENTRY_FIELDS = {"kind", "service", "account", "generation", "state", "attempt"}
_DOCUMENT_FIELDS = {"schema", "profile", "host", "revision", "legacy_review", "entries"}


def _text(value: Any, *, empty: bool = False) -> bool:
    return (isinstance(value, str) and len(value) <= 1024
            and (empty or bool(value.strip()))
            and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value))


def profile_for(service: str, account: str = "") -> str:
    """Classify known Work namespaces; callers explicitly enroll shared age keys twice."""
    if (not _text(service) or not _text(account, empty=True)
            or any(char in service or char in account for char in ("/", "\\"))):
        raise RegistryError("invalid dependency identity")
    work_services = ("artha-work", "artha-credential-recovery-work", "artha.credentials.work",
                     "msgraph-work-", "work.")
    return "work" if (service.startswith(work_services) or account.startswith("work.")
                      or account in ("msgraph.work", "msgraph.work-mcp")) else "personal"


def _profile(profile: Any) -> None:
    if not isinstance(profile, str) or profile not in ("personal", "work"):
        raise RegistryError("unsupported registry profile")


def _identity(profile: str, kind: str, service: str, account: str, generation: str) -> None:
    _profile(profile)
    if not isinstance(kind, str) or kind not in _KINDS or not _text(generation):
        raise RegistryError("invalid dependency kind or generation")
    dependency_profile = profile_for(service, account)
    if dependency_profile == "work" and profile != "work":
        raise RegistryError("Work dependencies require the Work profile")
    if (profile == "work" and dependency_profile != "work"
            and (service, account) != ("age-key", "artha")):
        raise RegistryError("personal dependencies are not permitted in the Work profile")
    if kind == 'spend_seal' and (profile != 'personal' or service != SPEND_SEAL[0]
                                  or account not in SPEND_SEAL_ACCOUNTS):
        raise RegistryError('spend seal identity is invalid')


def _time(value: Any) -> None:
    if not _text(value):
        raise RegistryError("review requires an explicit timezone-aware time")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None or parsed > datetime.now(UTC):
            raise ValueError
    except (ValueError, OverflowError):
        raise RegistryError("review requires a nonfuture timezone-aware time") from None


def _validate(document: Any, profile: str) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
        raise RegistryError("invalid registry document")
    if (document["schema"] != _SCHEMA or document["profile"] != profile
            or document["host"] != platform.node() or not _text(document["host"])
            or type(document["revision"]) is not int or document["revision"] < 0):
        raise RegistryError("registry schema, profile, host or revision mismatch")
    entries = document["entries"]
    if not isinstance(entries, list) or len(entries) > _MAX_ENTRIES:
        raise RegistryError("invalid registry entries")
    identities = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
            raise RegistryError("invalid dependency metadata fields")
        _identity(profile, entry["kind"], entry["service"], entry["account"], entry["generation"])
        if (not isinstance(entry["state"], str) or entry["state"] not in _STATES
                or not _text(entry["attempt"])):
            raise RegistryError("invalid dependency lifecycle")
        identity = (entry["service"], entry["account"])
        if identity in identities:
            raise RegistryError("duplicate dependency identity")
        identities.add(identity)
    review = document["legacy_review"]
    if review is not None:
        if (not isinstance(review, dict)
                or set(review) != {"host", "profile", "digest", "reviewer", "reviewed_at"}
                or review["host"] != document["host"] or review["profile"] != profile
                or not _text(review["reviewer"]) or not isinstance(review["digest"], str)
                or len(review["digest"]) != 64
                or any(char not in "0123456789abcdef" for char in review["digest"])):
            raise RegistryError("invalid historical review receipt")
        _time(review["reviewed_at"])
    return document


def registry_digest(document: dict[str, Any]) -> str:
    """Digest canonical metadata only, including its revision and existing receipt."""
    if not isinstance(document, dict):
        raise RegistryError("invalid registry document")
    _profile(document.get("profile"))
    _validate(document, document["profile"])
    return hashlib.sha256(_encoded(document)).hexdigest()


def _encoded(document: dict[str, Any]) -> bytes:
    try:
        data = json.dumps(document, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise RegistryError("invalid registry metadata") from None
    if len(data) > _MAX_BYTES:
        raise RegistryError("registry metadata exceeds size bound")
    return data


def _check(path: Path, *, private: bool, directory: bool) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400
            or (not stat.S_ISDIR(metadata.st_mode) if directory else not stat.S_ISREG(metadata.st_mode))
            or (not directory and metadata.st_nlink != 1)):
        raise RegistryError("registry paths must be regular and unlinked")
    if private and sys.platform != "win32" and (
            metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077):
        raise RegistryError("registry metadata must be owner-private")
    return True


def _paths(profile: str, root: Path | None) -> tuple[Path, Path, Path]:
    _profile(profile)
    local = (Path.home() / ".artha-local" if root is None else Path(root)).absolute()
    if ".." in local.parts:
        raise RegistryError("registry root must not contain parent traversal")
    return local, local / "recovery-registry" / f"{profile}.json", local / "recovery-registry" / f"{profile}.lock"


def _paths_check(local: Path, path: Path, lock: Path, *, create: bool = False) -> None:
    if sys.platform == "win32":
        _windows_paths_check(local, path, lock, create=create)
        return

    _posix_paths_check(local, path, lock, create=create)


def _posix_paths_check(local: Path, path: Path, lock: Path, *, create: bool) -> None:
    for ancestor in reversed((local, *local.parents)):
        exists = _check(ancestor, private=ancestor == local, directory=True)
        if not exists and create:
            ancestor.mkdir(mode=0o700)
            if os.name != "nt":
                ancestor.chmod(0o700, follow_symlinks=False)
    directory = path.parent
    if not _check(directory, private=True, directory=True) and create:
        directory.mkdir(mode=0o700)
        if os.name != "nt":
            directory.chmod(0o700, follow_symlinks=False)
    for candidate in (path, path.with_suffix(".json.tmp"), lock, local / "installation.lock"):
        _check(candidate, private=True, directory=False)
    if create:
        for candidate in (lock, local / "installation.lock"):
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                _check(candidate, private=True, directory=False)
            else:
                try:
                    if sys.platform != "win32":
                        os.fchmod(descriptor, 0o600)
                finally:
                    os.close(descriptor)


def _windows_directory(path: Path, *, create: bool) -> WindowsQuarantineDirectory | None:
    from catchup_core.quarantine_fs_windows import (  # noqa: PLC0415
        QuarantineFilesystemError,
        WindowsQuarantineDirectory,
    )

    if _check(path, private=True, directory=True):
        try:
            return WindowsQuarantineDirectory.open(path)
        except QuarantineFilesystemError:
            raise RegistryError("registry directory is unsafe or unavailable") from None
    if not create:
        return None
    if path.parent != path and not _check(path.parent, private=False, directory=True):
        parent = _windows_directory(path.parent, create=True)
        if parent is not None:
            parent.close()
    try:
        return WindowsQuarantineDirectory.create(path, lock_file=False)
    except QuarantineFilesystemError:
        try:
            return WindowsQuarantineDirectory.open(path)
        except QuarantineFilesystemError:
            raise RegistryError("registry directory is unsafe or unavailable") from None


def _windows_paths_check(local: Path, path: Path, lock: Path, *, create: bool) -> None:
    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError  # noqa: PLC0415

    for ancestor in reversed((local, *local.parents)):
        _check(ancestor, private=False, directory=True)
    local_directory = _windows_directory(local, create=create)
    if local_directory is None:
        return
    registry_directory = None
    try:
        registry_directory = _windows_directory(path.parent, create=create)
        candidates = [(local / "installation.lock", local_directory)]
        if registry_directory is not None:
            candidates.extend((candidate, registry_directory) for candidate in (
                path, path.with_suffix(".json.tmp"), lock))
        create_targets = {local / "installation.lock", lock}
        for candidate, directory in candidates:
            if _check(candidate, private=True, directory=False):
                try:
                    with directory.open_file(candidate.name):
                        pass
                except QuarantineFilesystemError:
                    raise RegistryError("registry file is unsafe or unavailable") from None
            elif create and candidate in create_targets:
                try:
                    with directory.create_file(candidate.name) as opened:
                        opened.flush()
                except QuarantineFilesystemError:
                    try:
                        with directory.open_file(candidate.name):
                            pass
                    except QuarantineFilesystemError:
                        raise RegistryError("registry lock is unsafe or unavailable") from None
    finally:
        if registry_directory is not None:
            registry_directory.close()
        local_directory.close()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError("duplicate registry JSON field")
        result[key] = value
    return result


def _load(path: Path, profile: str, *, initialize: bool = False) -> dict[str, Any]:
    if not _check(path, private=True, directory=False):
        if initialize:
            return {"schema": _SCHEMA, "profile": profile, "host": platform.node(),
                    "revision": 0, "legacy_review": None, "entries": []}
        raise RegistryError("registry missing; explicit historical inventory review is required")
    if sys.platform == "win32":
        from catchup_core.quarantine_fs_windows import (  # noqa: PLC0415
            QuarantineFilesystemError,
            WindowsQuarantineDirectory,
        )

        try:
            with (WindowsQuarantineDirectory.open(path.parent) as directory,
                  directory.open_file(path.name) as handle):
                data = handle.read(_MAX_BYTES + 1)
        except QuarantineFilesystemError:
            raise RegistryError("registry metadata is unsafe or unavailable") from None
    else:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise RegistryError("registry is not a regular file")
            data = handle.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise RegistryError("registry metadata exceeds size bound")
    try:
        document = json.loads(data, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise RegistryError("malformed registry JSON") from None
    return _validate(document, profile)


def _save(local: Path, path: Path, lock: Path, document: dict[str, Any]) -> None:
    _paths_check(local, path, lock)
    document["revision"] += 1
    _validate(document, document["profile"])
    data = _encoded(document)
    if sys.platform == "win32":
        _windows_save(path, data)
    else:
        from catchup_core.backup import atomic_bytes  # noqa: PLC0415

        atomic_bytes(path, data)


def _windows_save(path: Path, data: bytes) -> None:
    from catchup_core.quarantine_fs_windows import (  # noqa: PLC0415
        QuarantineFilesystemError,
        WindowsQuarantineDirectory,
    )

    temporary = path.with_suffix(".json.tmp")
    try:
        with WindowsQuarantineDirectory.open(path.parent) as directory:
            if _check(temporary, private=True, directory=False):
                staged = directory.open_file(temporary.name, writable=True)
            else:
                staged = directory.create_file(temporary.name)
            with staged:
                staged.truncate(0)
                staged.seek(0)
                staged.write_all(data)
                staged.flush()
                staged_identity = staged.identity
            if _check(path, private=True, directory=False):
                with directory.open_file(path.name) as previous:
                    replaced_identity = previous.identity
                directory.replace_file(
                    temporary.name, path.name,
                    expected_identity=staged_identity,
                    replaced_identity=replaced_identity,
                )
            else:
                directory.publish_write_once(
                    temporary.name, path.name, expected_identity=staged_identity,
                )
    except QuarantineFilesystemError:
        raise RegistryError("registry metadata publication was refused") from None


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except (OSError, SessionBusy):
        raise RegistryError("registry metadata is unavailable, unsafe or busy") from None


def snapshot(profile: str, *, root: Path | None = None, require_review: bool = True) -> dict[str, Any]:
    """Read metadata without enrollment; inspection alone permits pending intents."""
    local, path, lock = _paths(profile, root)
    with _errors():
        _paths_check(local, path, lock)
        document = _load(path, profile)
    if require_review and (document["legacy_review"] is None
                           or any(entry["state"] == "pending" for entry in document["entries"])):
        raise RegistryError("registry has pending dependencies or lacks historical review")
    return document


def _entry(document: dict[str, Any], service: str, account: str) -> dict[str, Any] | None:
    return next((entry for entry in document["entries"]
                 if (entry["service"], entry["account"]) == (service, account)), None)


def has_pending(profile: str, kind: str, service: str, account: str, generation: str, *,
                root: Path | None = None) -> bool:
    """Inspect pending intent without enrollment; mismatched pending intent is refused."""
    _identity(profile, kind, service, account, generation)
    local, path, lock = _paths(profile, root)
    with _errors():
        _paths_check(local, path, lock)
        if not _check(path, private=True, directory=False):
            return False
        entry = _entry(_load(path, profile), service, account)
    if entry is None or entry["state"] != "pending":
        return False
    if entry["kind"] != kind or entry["generation"] != generation:
        raise RegistryError("pending dependency intent does not match")
    return True


@contextmanager
def _record_lock(local: Path, path: Path, profile: str, service: str, account: str) -> Iterator[None]:
    identity = json.dumps([profile, service, account], ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("ascii")).hexdigest()
    lock = path.parent / f"record-{digest}.lock"
    _paths_check(local, path, lock, create=True)
    with session_lock(lock):
        yield


@contextmanager
def mutation(profile: str, kind: str, service: str, account: str, generation: str, *,
             root: Path | None = None, deleted: bool = False) -> Iterator[None]:
    """Caller performs its existing native write and verified readback inside the context."""
    _identity(profile, kind, service, account, generation)
    if type(deleted) is not bool:
        raise RegistryError("deleted must be a boolean")
    local, path, lock = _paths(profile, root)
    with _errors():
        _paths_check(local, path, lock, create=True)
        with installation_fence(local), _record_lock(local, path, profile, service, account):
            with session_lock(lock):
                document = _load(path, profile, initialize=True)
                entry = _entry(document, service, account)
                if entry is not None and entry["kind"] != kind:
                    raise RegistryError("dependency kind does not match")
                if entry is None:
                    entry = {"kind": kind, "service": service, "account": account}
                    document["entries"].append(entry)
                attempt = uuid.uuid4().hex
                entry.update(generation=generation, state="pending", attempt=attempt)
                _save(local, path, lock, document)
            yield
            with session_lock(lock):
                document = _load(path, profile)
                entry = _entry(document, service, account)
                if entry is None or entry["attempt"] != attempt or entry["state"] != "pending":
                    raise RegistryError("registry mutation was superseded; stale completion refused")
                entry["state"] = "deleted" if deleted else "active"
                _save(local, path, lock, document)


def declare(profile: str, kind: str, service: str, account: str, generation: str, *,
            root: Path | None = None) -> dict[str, Any]:
    """Declare historical metadata without reading or verifying any native value."""
    _identity(profile, kind, service, account, generation)
    local, path, lock = _paths(profile, root)
    with _errors():
        _paths_check(local, path, lock, create=True)
        with installation_fence(local), _record_lock(local, path, profile, service, account), session_lock(lock):
            document = _load(path, profile, initialize=True)
            entry = _entry(document, service, account)
            if entry is not None:
                if (entry["kind"] != kind or entry["generation"] != generation
                        or entry["state"] != "declared"):
                    raise RegistryError("declaration cannot replace an existing dependency")
                return document
            document["entries"].append({"kind": kind, "service": service, "account": account,
                                        "generation": generation, "state": "declared",
                                        "attempt": uuid.uuid4().hex})
            document["legacy_review"] = None
            _save(local, path, lock, document)
            return document



def attest_legacy(profile: str, expected_digest: str, reviewer: str, reviewed_at: str, *,
                  root: Path | None = None) -> dict[str, Any]:
    """Record explicit host/profile-bound historical inventory review, not custody proof."""
    if not _text(reviewer):
        raise RegistryError("review requires an explicit reviewer")
    _time(reviewed_at)
    local, path, lock = _paths(profile, root)
    with _errors():
        _paths_check(local, path, lock)
        with installation_fence(local), session_lock(lock):
            document = _load(path, profile)
            if (any(entry["state"] == "pending" for entry in document["entries"])
                    or registry_digest(document) != expected_digest):
                raise RegistryError("historical review is stale or dependencies remain pending")
            document["legacy_review"] = {"host": document["host"], "profile": profile,
                                         "digest": expected_digest, "reviewer": reviewer,
                                         "reviewed_at": reviewed_at}
            _save(local, path, lock, document)
            return document


def _spend_account(mode: str | None) -> str:
    if mode is None:
        return SPEND_SEAL[1]
    if not isinstance(mode, str) or mode not in {'shadow', 'authoritative'}:
        raise RegistryError('unsupported spend seal namespace')
    return f'seal.{mode}'


def enroll_spend_seal(profile: str = "personal", *, root: Path | None = None, mode: str | None = None) -> Any:
    """Context for creating the spend seal file: the entry is pending while it is written, then active."""
    return mutation(profile, "spend_seal", SPEND_SEAL[0], _spend_account(mode), "1", root=root)


def spend_seal_enrolled(profile: str = "personal", *, root: Path | None = None, mode: str | None = None) -> bool:
    """True when the seal is recorded as active. Missing, pending or unreadable metadata is not enrolled."""
    try:
        document = snapshot(profile, root=root, require_review=False)
        accounts = SPEND_SEAL_ACCOUNTS if mode is None else {_spend_account(mode), SPEND_SEAL[1]}
    except RegistryError:
        return False
    return any(entry['service'] == SPEND_SEAL[0] and entry['account'] in accounts
               and entry['state'] == 'active' and entry['kind'] == 'spend_seal' for entry in document['entries'])


def spend_seal_required(profile: str = "personal", *, root: Path | None = None, mode: str | None = None) -> bool:
    """Known seal custody, including retirement, forbids initialization of a fresh spend history.

    This is an admission boundary, unlike best-effort enrollment inspection: unsafe or uncertain metadata
    must refuse. Reading it never creates or repairs registry files or attests historical custody.
    """
    account = _spend_account(mode)
    local, path, lock = _paths(profile, root)
    with _errors():
        _paths_check(local, path, lock)
        if not _check(path, private=True, directory=False):
            return False
        document = _load(path, profile)
    entries = [_entry(document, SPEND_SEAL[0], candidate) for candidate in {account, SPEND_SEAL[1]}]
    for entry in entries:
        if entry is not None and (entry['kind'] != 'spend_seal' or entry['state'] not in {'active', 'deleted'}):
            raise RegistryError('spend seal custody is pending, unverified or inconsistent')
    return any(entry is not None for entry in entries)
