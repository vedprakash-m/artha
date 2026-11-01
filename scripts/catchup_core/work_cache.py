"""Encrypted, machine-local WorkIQ cache and explicit legacy retirement (WP-19).

No provider calls, environment-secret fallback, automatic migration or fresh-evidence
claim for archived legacy content. Public diagnostics contain no source text.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import stat
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from catchup_core.backup import atomic_bytes
from catchup_core.crypto import system_keyring
from catchup_core.session_lock import SessionBusy, require_active_installation, session_lock
from catchup_core.vault import AgeVault, VaultError

MAX_CACHE_BYTES = 32 * 1024 * 1024
_REPARSE_POINT = 0x400  # Windows FILE_ATTRIBUTE_REPARSE_POINT, including directory junctions.
_CLOUD_REPARSE_MASK = 0xFFFF0000
_CLOUD_REPARSE_TAG = 0x90000000  # Windows IO_REPARSE_TAG_CLOUD* (non-surrogate cloud sync filter tags).
_SCHEMA = 'artha.work-cache.v1'
_RETIRED = 'artha.work-cache.retired.v1'
_REPO = Path(__file__).resolve().parents[2]


class WorkCacheBlocked(RuntimeError):
    """A cache cannot be read or persisted safely; never fall back to plaintext."""


def _is_cloud_reparse(metadata: os.stat_result | Any) -> bool:
    return (getattr(metadata, "st_reparse_tag", 0) & _CLOUD_REPARSE_MASK) == _CLOUD_REPARSE_TAG


def _identity() -> str:
    try:
        value = system_keyring().get_password('age-key', 'artha')
        if not isinstance(value, str) or not value.startswith('AGE-SECRET-KEY-'):
            raise WorkCacheBlocked('shared age identity is unavailable')
        return value
    except Exception:
        raise WorkCacheBlocked('shared OS age identity is unavailable') from None


def _vault(root: Path) -> AgeVault:
    recipient = importlib.import_module("profile_loader").get('encryption.age_recipient', '')
    if not isinstance(recipient, str) or not recipient.startswith('age1'):
        raise WorkCacheBlocked('age recipient is unavailable')
    return AgeVault(root, identity=_identity, recipient=recipient)


def _bounded(path: Path, *, allow_cloud_tag: bool = False) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkCacheBlocked('Work cache artifact must be a regular file')
    if (getattr(metadata, 'st_file_attributes', 0) & _REPARSE_POINT) and not (
        allow_cloud_tag and _is_cloud_reparse(metadata)
    ):
        raise WorkCacheBlocked('Work cache artifact cannot be a link or junction reparse point')
    if metadata.st_size > MAX_CACHE_BYTES:
        raise WorkCacheBlocked('Work cache exceeds its byte bound')
    with path.open('rb') as handle:
        data = handle.read(MAX_CACHE_BYTES + 1)
    if len(data) > MAX_CACHE_BYTES:
        raise WorkCacheBlocked('Work cache exceeds its byte bound')
    return data


class WorkCache:
    def __init__(self, root: Path | None = None, *, legacy: Path | None = None, vault: AgeVault | None = None):
        if root is None:
            from lib.local_paths import work_cache_dir  # noqa: PLC0415
            try:
                root = work_cache_dir()
            except ValueError:
                raise WorkCacheBlocked('Work cache requires machine-local storage') from None
        self.root = root
        self.path = root / 'workiq.json.age'
        self.legacy = legacy if legacy is not None else _REPO / 'tmp/.workiq_cache.json'
        self._vault = vault

    @property
    def vault(self) -> AgeVault:
        if self._vault is None:
            self._vault = _vault(self.root)
        return self._vault

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            require_active_installation(self.root, self.legacy.parent)
            root_chain = {self.root, *self.root.parents}
            legacy_chain = {self.legacy.parent, *self.legacy.parent.parents}
            for directory in root_chain:
                try:
                    metadata = directory.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & _REPARSE_POINT:
                    raise WorkCacheBlocked('Work cache directory links or reparse points require review')
            for directory in legacy_chain - root_chain:
                try:
                    metadata = directory.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(metadata.st_mode) or (
                    (getattr(metadata, 'st_file_attributes', 0) & _REPARSE_POINT)
                    and not _is_cloud_reparse(metadata)
                ):
                    raise WorkCacheBlocked('Work cache directory links or reparse points require review')
            if (
                self.legacy.parent.resolve().is_relative_to(self.root.resolve())
                or self.root.resolve().is_relative_to(self.legacy.parent.resolve())
            ):
                raise WorkCacheBlocked('Work cache root and legacy source cannot overlap')
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if (self.root / 'cache.lock').is_symlink():
                raise WorkCacheBlocked('Work cache lock must not be a link')
            with session_lock(self.root / 'cache.lock'):
                yield
        except WorkCacheBlocked:
            raise
        except (OSError, ValueError, TypeError, RecursionError, VaultError, SessionBusy):
            raise WorkCacheBlocked('Work cache unavailable, busy or quarantined; no plaintext fallback') from None

    def _retired_digest(self, data: bytes) -> str | None:
        try:
            marker = json.loads(data)
        except (ValueError, UnicodeError):
            return None
        if (isinstance(marker, dict) and set(marker) == {'schema', 'sha256'} and marker['schema'] == _RETIRED
                and isinstance(marker['sha256'], str) and len(marker['sha256']) == 64
                and all(c in '0123456789abcdef' for c in marker['sha256'])):
            archive = self.root / f"legacy-{marker['sha256']}.json.age"
            if _bounded(archive) is None:
                raise WorkCacheBlocked('retired Work cache archive is missing')
            return str(marker['sha256'])
        return None

    def _require_retired(self) -> None:
        data = _bounded(self.legacy, allow_cloud_tag=True)
        if data is not None and self._retired_digest(data) is None:
            raise WorkCacheBlocked('legacy Work cache requires reviewed migration; stop old writers first')

    def _load(self) -> dict[str, Any]:
        self._require_retired()
        if _bounded(self.path) is None:
            return {}
        value = json.loads(self.vault.read_artifact(self.path))
        if (not isinstance(value, dict) or set(value) != {'schema', 'entries'} or value['schema'] != _SCHEMA
                or not isinstance(value['entries'], dict)):
            raise WorkCacheBlocked('Work cache schema is invalid')
        return dict(value['entries'])

    def load(self) -> dict[str, Any]:
        with self._locked():
            return self._load()

    def _publish(self, target: Path, text: str) -> None:
        if len(text.encode()) > MAX_CACHE_BYTES - 65536:
            raise WorkCacheBlocked('Work cache exceeds its byte bound')
        _bounded(target)  # refuse links/nonregular or inaccessible artifacts before replacement
        blob = self.vault.encrypt_artifact(text)
        if len(blob) > MAX_CACHE_BYTES:
            raise WorkCacheBlocked('Work cache exceeds its byte bound')
        from lib import work_publication  # noqa: PLC0415
        if work_publication.enrolled():
            self._publish_journaled(target, text, blob)
            return
        if sys.platform == 'win32':
            self._publish_native(target, text, blob)
            return
        descriptor, temporary = tempfile.mkstemp(prefix='.cipher-', suffix='.age', dir=self.root)
        path = Path(temporary)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            if self.vault.read_artifact(path) != text:
                raise WorkCacheBlocked('Work cache encryption verification failed')
            os.replace(path, target)
            if os.name != 'nt':
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            path.unlink(missing_ok=True)

    def _publish_native(self, target: Path, text: str, blob: bytes) -> None:
        from catchup_core.blobs import private_atomic_bytes  # noqa: PLC0415
        from catchup_core.quarantine_fs_windows import QuarantineFilesystemError  # noqa: PLC0415
        path = self.root / f'.cipher-{uuid.uuid4().hex}.age'
        try:
            private_atomic_bytes(path, blob)
            if self.vault.read_artifact(path) != text:
                raise WorkCacheBlocked('Work cache encryption verification failed')
            private_atomic_bytes(target, blob)
        except QuarantineFilesystemError:
            raise WorkCacheBlocked('Work cache publication requires owner-private native storage') from None
        finally:
            path.unlink(missing_ok=True)

    def _publish_journaled(self, target: Path, text: str, blob: bytes) -> None:
        """Verify the ciphertext in owner-private staging, then publish it as one Work journal generation."""
        from catchup_core.blobs import private_atomic_bytes  # noqa: PLC0415
        from lib import work_publication  # noqa: PLC0415
        native_errors: tuple[type[BaseException], ...] = (OSError,)
        if sys.platform == 'win32':
            from catchup_core.quarantine_fs_windows import QuarantineFilesystemError  # noqa: PLC0415
            native_errors += (QuarantineFilesystemError,)
        path = work_publication.staging_dir() / f'.cipher-{uuid.uuid4().hex}.age'
        try:
            private_atomic_bytes(path, blob)
            if self.vault.read_artifact(path) != text:
                raise WorkCacheBlocked('Work cache encryption verification failed')
            work_publication.publish({target: blob}, legacy=_enrollment_changed)
        except work_publication.WorkPublicationBlocked:
            raise WorkCacheBlocked('Work cache publication refused by the Work journal') from None
        except native_errors:
            raise WorkCacheBlocked('Work cache publication requires owner-private native storage') from None
        finally:
            path.unlink(missing_ok=True)

    def update(self, entries: Mapping[str, Any]) -> None:
        with self._locked(), _journal_session():
            cache = self._load()
            cache.update(entries)
            text = json.dumps({'schema': _SCHEMA, 'entries': cache}, ensure_ascii=False, allow_nan=False)
            self._publish(self.path, text)

    def migration_plan(self) -> dict[str, Any]:
        with self._locked():
            data = _bounded(self.legacy, allow_cloud_tag=True)
            if data is None:
                raise WorkCacheBlocked('no legacy Work cache to migrate')
            if self._retired_digest(data):
                raise WorkCacheBlocked('legacy Work cache is already retired')
            return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def migrate(self, digest: str, *, writers_stopped: bool) -> Path:
        if not writers_stopped:
            raise WorkCacheBlocked('confirm old Work writers are stopped before migration')
        # Enrolled: the archive publishes through the Work journal before the legacy marker (outside the roots).
        with self._locked(), _journal_session():
            data = _bounded(self.legacy, allow_cloud_tag=True)
            if data is None:
                raise WorkCacheBlocked('legacy Work cache is missing')
            retired = self._retired_digest(data)
            if retired == digest:
                archive = self.root / f'legacy-{digest}.json.age'
                if hashlib.sha256(self.vault.read_artifact(archive).encode()).hexdigest() != digest:
                    raise WorkCacheBlocked('retired Work cache archive does not match')
                self.legacy.chmod(0o400)
                return archive
            if retired or hashlib.sha256(data).hexdigest() != digest:
                raise WorkCacheBlocked('legacy Work cache changed; review a new plan')
            archive = self.root / f'legacy-{digest}.json.age'
            # Existing archive must agree; do not overwrite unrecognized recovery material.
            if _bounded(archive) is None:
                self._publish(archive, data.decode('utf-8'))
            if self.vault.read_artifact(archive).encode() != data:
                raise WorkCacheBlocked('legacy Work cache archive does not match')
            if _bounded(self.legacy, allow_cloud_tag=True) != data:
                raise WorkCacheBlocked('legacy Work cache changed during migration')
            atomic_bytes(self.legacy, json.dumps({'schema': _RETIRED, 'sha256': digest}).encode())
            self.legacy.chmod(0o400)
            return archive


@contextmanager
def _journal_session() -> Iterator[None]:
    from lib import work_publication  # noqa: PLC0415
    try:
        with work_publication.session():
            yield
    except work_publication.WorkPublicationBlocked:
        raise WorkCacheBlocked('Work cache publication refused by the Work journal') from None


def _enrollment_changed() -> None:
    raise WorkCacheBlocked('Work enrollment changed during publication; nothing was written')


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Work cache migration; no provider calls or source text output')
    parser.add_argument('command', choices=('plan', 'apply'))
    parser.add_argument('--sha256', default='')
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args(argv)
    try:
        cache = WorkCache()
        if args.command == 'plan':
            print(json.dumps(cache.migration_plan()))
        else:
            cache.migrate(args.sha256, writers_stopped=args.writers_stopped)
            print('Legacy Work cache archived and retired; prior synced copies still require retention review.')
        return 0
    except (WorkCacheBlocked, ValueError, OSError):
        print('Work cache migration blocked; review storage, digest and stopped-writer prerequisites.', file=sys.stderr)
        return 20


if __name__ == '__main__':
    raise SystemExit(main())
