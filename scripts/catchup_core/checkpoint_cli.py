"""``python -m catchup_core.checkpoint_cli``: full-state checkpoint and restore (WP-18, hand-run).

Commands: ``inventory``, ``create``, ``verify``, ``recovery``, ``diff``, ``restore``,
metadata-only ``registry-preview``, ``registry-declare``, ``registry-review`` and the
Work-only ``work-privacy-status``, ``work-privacy-enroll``, ``work-privacy-reconcile``,
``work-privacy-recover-pending`` and ``work-privacy-revoke`` lifecycle commands.
These Work commands report metadata only; enrollment, reconciliation and revocation require
``--reviewed``, and status never repairs pending authority.
``storage-preview`` and ``storage-adopt --sha256 HASH --reviewed --writers-stopped`` inspect/adopt
only the existing configured local root and installation lock, never inventory or keys.
Stopped writers are operator-attested; exclusive installation-lock access excludes cooperating writers only.
Exit codes: 0 done, 20 blocked, 30 usage.  The passphrase is read from the terminal only; it is never an argument.
Profiles: ``personal`` (this repository plus the machine-local store) and ``work`` (Work state only; its own
passphrase, destination on this host, never merged with the personal checkpoint).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TextIO

from catchup_core import checkpoint as ck
from catchup_core.contracts import ExitCode
from catchup_core.crypto import (
    DecryptionError,
    EnvelopeCipher,
    KeyringKeyProvider,
    KeyUnavailableError,
    StaticKeyProvider,
    system_keyring,
)
from catchup_core.session_lock import SessionBusy, default_installation_dir, installation_fence, session_lock

_REPO = Path(__file__).resolve().parents[2]
_KEY_SERVICES = ("artha-ledger", "artha-ledger-ids")
_WORK_KEY_SERVICES = ("artha-work-checkpoint",)
_RECOVERY_KEY_SERVICE = {
    "personal": "artha-credential-recovery-personal",
    "work": "artha-credential-recovery-work",
}
_CRED_SERVICE = {
    "personal": "artha.credentials.v1",
    "work": "artha.credentials.work.v1",
}
_KNOWN_CREDENTIALS = {
    "personal": (
        "google.gmail-oauth-token",
        "google.gcal-oauth-token",
        "google.gdrive-oauth-token",
        "msgraph.personal",
    ),
    "work": ("msgraph.work", "msgraph.work-mcp"),
}
_KNOWN_CLIENTS = {
    "personal": ("gmail-client-id", "gmail-client-secret", "msgraph-client-id"),
    "work": ("msgraph-work-client-id",),
}
_EXCLUDED = {"repo/.venv": "reproducible environment", "repo/.venv-docx": "reproducible environment",
             "repo/.antigravitycli": "IDE tool cache containing a symbolic link"}
_WORK = dict.fromkeys(('repo/state/work', 'repo/.tokens/msgraph-work-token.json',
                      'repo/.tokens/msgraph-work-mcp-token.json', 'repo/tmp/.workiq_cache.json',
                      'repo/tmp/.workiq_breaker.json', 'repo/tmp/.workiq_breaker.lock',
                      'repo/tmp/.workiq_breaker.json.tmp', 'repo/tmp/.workiq_cache.json.tmp'),
                      'Work profile: separate policy, keys and checkpoint')


def _include_dependency(roots: dict[str, Path], name: str, path: Path) -> None:
    """Retain an existing dependency once, including a link so inventory can refuse it explicitly."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISLNK(metadata.st_mode):
        if any(path.resolve().is_relative_to(root.resolve()) for root in roots.values() if root.is_dir()):
            return
        if stat.S_ISDIR(metadata.st_mode) and any(
                root.resolve().is_relative_to(path.resolve()) for root in roots.values()):
            raise ck.CheckpointBlocked('installation dependency roots overlap; select a dedicated store')
    roots[name] = path


def _entries(directory: Path) -> list[Path]:
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(metadata.st_mode):
        raise ck.CheckpointBlocked('Work artifact directory must be a directory without links')
    return sorted(directory.iterdir())


def _configured_bundle_dirs(repo: Path) -> tuple[dict[str, Path], set[str]]:
    cfg_path = repo / "config/catchup_core.yaml"
    try:
        cfg_meta = cfg_path.lstat()
    except FileNotFoundError:
        return {}, set()
    if not stat.S_ISREG(cfg_meta.st_mode):
        raise ck.CheckpointBlocked("runtime configuration must be a regular file")
    import yaml  # noqa: PLC0415
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError):
        raise ck.CheckpointBlocked('bundle dependency configuration cannot be parsed') from None
    if not isinstance(raw, dict):
        raise ck.CheckpointBlocked('bundle dependency configuration must be a mapping')
    if 'bundles' not in raw:
        return {}, set()
    if not isinstance(raw['bundles'], dict):
        raise ck.CheckpointBlocked('bundle dependencies must be a mapping')
    dirs: dict[str, Path] = {}
    producers: set[str] = set()
    for source, spec in sorted(raw["bundles"].items()):
        if (not isinstance(source, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', source)
                or not isinstance(spec, dict)):
            raise ck.CheckpointBlocked('bundle dependency binding is malformed')
        raw_producer = spec.get("producer")
        producer = raw_producer if raw_producer is not None else source
        if not isinstance(producer, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', producer):
            raise ck.CheckpointBlocked('bundle dependency producer binding is malformed')
        producers.add(producer)
        raw_dir = spec.get("dir")
        if not isinstance(raw_dir, str) or not raw_dir.strip():
            raise ck.CheckpointBlocked('bundle dependency needs a directory')
        candidate = Path(raw_dir).expanduser()
        directory = candidate if candidate.is_absolute() else (repo / candidate)
        dirs[source] = directory
    return dirs, producers


def profile_layout(profile: str, local: Path | None = None, *,
                   registry_root: Path | None = None) -> tuple[dict[str, Path], dict[str, str]]:
    try:
        roots, excluded = _profile_layout(profile, local)
        base = local or Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
        protocol_paths = (
            (base / 'work-privacy', 'Work local recovery authority'),
            (base / 'work-privacy.lock', 'Work local recovery lock'),
            (base / 'work-staging', 'Work cache verification staging'),
            (default_installation_dir() / 'work-privacy.lock', 'Work local recovery lock'),
        )
        for path, reason in protocol_paths:
            target = Path(os.path.abspath(path))
            for name, root in roots.items():
                root_path = Path(os.path.abspath(root))
                try:
                    relative = target.relative_to(root_path)
                except ValueError:
                    continue
                if relative.parts:
                    excluded[f'{name}/{relative.as_posix()}'] = reason
        registry_dir = (registry_root or Path.home() / '.artha-local') / 'recovery-registry'
        if profile == 'personal':
            for name, root in roots.items():
                if (registry_dir / 'work.json').is_relative_to(root):
                    excluded[f'{name}/{(registry_dir / "work.json").relative_to(root).as_posix()}'] = (
                        'Work recovery registry: separate profile')
        _include_dependency(roots, 'recovery_registry', registry_dir / f'{profile}.json')
        if profile == 'personal':
            _classify_spend_seal(roots, excluded, local, registry_root)
        return roots, excluded
    except OSError as exc:
        raise ck.CheckpointBlocked(f'installation dependency metadata is unavailable ({type(exc).__name__})') from None


_SPEND_SEAL_FILES = frozenset({'shadow.json', 'authoritative.json'})
_SPEND_SEAL_LOCKS = frozenset({f'{name}.lock' for name in _SPEND_SEAL_FILES})


def _classify_spend_seal(roots: dict[str, Path], excluded: dict[str, str], local: Path | None,
                         registry_root: Path | None) -> None:
    """A spend seal file must be named by the recovery registry; it is then kept out of every snapshot."""
    base = local or Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
    seal_dir = base / 'spend-seal'
    if not seal_dir.exists() and not seal_dir.is_symlink():
        return
    from catchup_core import recovery_registry as registry  # noqa: PLC0415
    if not stat.S_ISDIR(seal_dir.lstat().st_mode):
        raise ck.CheckpointBlocked('spend seal storage needs an ordinary directory')
    for child in seal_dir.iterdir():
        metadata = child.lstat()
        if (child.name not in _SPEND_SEAL_FILES | _SPEND_SEAL_LOCKS or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1 or getattr(metadata, 'st_file_attributes', 0) & 0x400):
            raise ck.CheckpointBlocked('spend seal storage contains an unclassified or unsafe artifact')
        if child.name in _SPEND_SEAL_LOCKS:
            continue                                  # empty lock file beside a seal; the directory is excluded below
        try:
            classified = registry.spend_seal_required('personal', root=registry_root, mode=child.stem)
        except registry.RegistryError:
            raise ck.CheckpointBlocked('the recovery registry does not classify current spend seal custody') from None
        if not classified:
            raise ck.CheckpointBlocked('a spend seal exists that the recovery registry does not classify')
    for name, root in roots.items():
        if root.is_dir() and seal_dir.resolve().is_relative_to(root.resolve()):
            excluded[f"{name}/{seal_dir.resolve().relative_to(root.resolve()).as_posix()}"] = (
                'spend seal: classified by the recovery registry, deliberately never restored')


def _profile_layout(profile: str, local: Path | None) -> tuple[dict[str, Path], dict[str, str]]:  # noqa: PLR0912
    if profile not in {'personal', 'work'}:
        raise ValueError("profile must be 'personal' or 'work'")
    from catchup_core.runtime import action_database_path  # noqa: PLC0415
    base = local or Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
    if (base.resolve().is_relative_to(_REPO.resolve()) or _REPO.resolve().is_relative_to(base.resolve())):
        raise ck.CheckpointBlocked('machine-local storage must be outside the repository and Work profile')
    queue = action_database_path(_REPO)
    if not queue.is_absolute():
        raise ck.CheckpointBlocked('the configured action store needs an absolute path')
    work_paths = [_REPO / name.removeprefix('repo/') for name in _WORK] + [base / 'work']
    work_paths.extend(path for path in _entries(_REPO / 'tmp')
                      if path.name.startswith('..workiq_cache.json-') and path.name.endswith('.tmp'))
    work_paths.extend(path for path in _entries(base / 'credential-recovery')
                      if path.name.startswith('work-') and path.name.endswith('.ae1'))
    if any(queue.resolve().is_relative_to(path.resolve()) for path in work_paths):
        raise ck.CheckpointBlocked('personal action storage cannot overlap the Work content boundary')
    if profile == "personal":
        roots = {'repo': _REPO, 'local': base}
        _include_dependency(roots, 'actions', queue if os.environ.get('ARTHA_LOCAL_DB', '').strip() else queue.parent)
        _include_dependency(roots, 'privacy', Path.home() / '.artha-local/privacy')
        if configured_privacy := os.environ.get('ARTHA_PRIVACY_DIR'):
            privacy = Path(configured_privacy).expanduser()
            if any(privacy.resolve().is_relative_to(path.resolve()) for path in work_paths):
                raise ck.CheckpointBlocked('personal privacy storage cannot overlap the Work content boundary')
            _include_dependency(roots, 'privacy_override', privacy)
        bundle_dirs, _ = _configured_bundle_dirs(_REPO)
        for source, bundle_dir in sorted(bundle_dirs.items()):
            if any(bundle_dir.resolve().is_relative_to(path.resolve()) for path in work_paths):
                raise ck.CheckpointBlocked('personal bundle storage cannot overlap the Work content boundary')
            try:
                metadata = bundle_dir.lstat()
            except FileNotFoundError:
                raise ck.CheckpointBlocked('configured bundle dependency is missing') from None
            if not stat.S_ISDIR(metadata.st_mode):
                raise ck.CheckpointBlocked('configured bundle dependency needs an ordinary directory')
            _include_dependency(roots, f'bundle_{source}', bundle_dir)
        excluded = {**_EXCLUDED, **_WORK}
        for name, root in roots.items():
            if root.is_dir():
                for work_path in work_paths:
                    if work_path.resolve().is_relative_to(root.resolve()):
                        key = f"{name}/{work_path.resolve().relative_to(root.resolve()).as_posix()}"
                        excluded[key] = 'Work artifact: separate profile'
                for path in (root / 'credential-recovery').glob('work-*.ae1'):
                    key = f'{name}/{path.relative_to(root).as_posix()}'
                    excluded[key] = 'Work credential recovery: separate profile'
        return roots, excluded
    roots = {"work_state": _REPO / "state" / "work", "work_local": base / "work"}
    for index, path in enumerate(work_paths):
        if path != _REPO / 'state/work' and path != base / 'work':
            _include_dependency(roots, f'work_artifact_{index}', path)
    return roots, {'work_local/cache/cache.lock': 'ephemeral Work cache process lock'}


def busy_reasons(local: Path, *, check_sessions: bool = True, action_paths: Sequence[Path] = (),  # noqa: PLR0912
                 repo: Path | None = None) -> list[str]:
    """Refuse to capture while a Catch-up, bundle import, reflection, migration or action is active (fail closed)."""
    reasons: list[str] = []
    if check_sessions:
        for pattern in (
            "catchup_core/**/ledger.session.lock",
            "catchup_core/**/bundle.import.lock",
            "credential-locks/*.lock",
            "writer-locks/*.lock",
        ):
            for lock in sorted(local.glob(pattern)):
                try:
                    with session_lock(lock):
                        pass
                except SessionBusy:
                    reasons.append(f"a Catch-up owns {lock.parent.name}")
    repo_root = repo or _REPO
    try:
        (repo_root / '.artha-decrypted').lstat()
        reasons.append('State Files are decrypted; re-encrypt before capture')
    except FileNotFoundError:
        pass
    except OSError:
        reasons.append('State File encryption state cannot be determined')
    reflect_lock = repo_root / "state/work/.reflect-lock"
    try:
        reflect_lock.lstat()
        reasons.append("a Work reflection session is active")
    except FileNotFoundError:
        pass
    except OSError:
        reasons.append("Work reflection state cannot be determined")
    for queue in {local / 'actions.db', *action_paths}:
        try:
            queue.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            reasons.append('action state cannot be determined')
            continue
        try:
            with closing(sqlite3.connect(queue.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute("SELECT 1 FROM actions WHERE status='executing' LIMIT 1").fetchone():
                    reasons.append("an action is executing")
        except sqlite3.Error:
            reasons.append("action state cannot be determined")
    return reasons


def _registry_snapshot(profile: str, root: Path | None = None, *, review: bool = True) -> dict[str, Any]:
    """Read dependency metadata, never native values or restored review authority."""
    try:
        from catchup_core import recovery_registry as registry  # noqa: PLC0415
    except ImportError:
        raise ck.CheckpointBlocked('recovery registry implementation is unavailable') from None
    try:
        return registry.snapshot(profile, root=root, require_review=review)
    except registry.RegistryError:
        raise ck.CheckpointBlocked('recovery registry is unavailable, pending or requires historical review') from None


def _registered_entries(doc: Mapping[str, Any], profile: str) -> list[dict[str, Any]]:
    from catchup_core.recovery_registry import RegistryError, profile_for  # noqa: PLC0415
    from catchup_core.work_privacy import WorkPrivacy  # noqa: PLC0415
    entries = doc.get('entries')
    if doc.get('profile') != profile or not isinstance(entries, list) or len(entries) > 10000:
        raise ck.CheckpointBlocked('recovery registry profile or entries are invalid')
    found: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ck.CheckpointBlocked('recovery registry entry is malformed')
        kind, service, account = (entry.get(field) for field in ('kind', 'service', 'account'))
        if isinstance(service, str) and service in WorkPrivacy.EXCLUDED_SERVICES:
            raise ck.CheckpointBlocked('dedicated Work privacy authority cannot be checkpointed')
        if (kind not in {'key', 'key_head', 'credential', 'client', 'bundle', 'privacy', 'vault', 'spend_seal'}
                or not isinstance(service, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', service)
                or not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', account)
                or entry.get('state') not in {'active', 'declared', 'deleted'}):
            raise ck.CheckpointBlocked('recovery registry entry is malformed or pending')
        try:
            owner = profile_for(service, account)
        except RegistryError:
            raise ck.CheckpointBlocked('recovery registry service is invalid') from None
        if kind == 'credential' and account in _KNOWN_CREDENTIALS['work']:
            owner = 'work'
        if owner != profile and (service, account) != ('age-key', 'artha'):
            raise ck.CheckpointBlocked('recovery registry contains cross-profile dependencies')
        valid = {
            'key': bool(re.fullmatch(r'key\.[1-9][0-9]{0,9}', account)),
            'key_head': account == 'current_version',
            'credential': service in {'artha.credentials.v1', _CRED_SERVICE[profile]}
                          and bool(re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', account))
                          and not account.endswith('.pending') and '.chunk.' not in account,
            'client': account == 'artha',
            'bundle': service.startswith('artha-bundle-') and account == 'secret',
            'privacy': service.startswith('artha-privacy-') and account == 'checkpoint',
            'vault': (service, account) == ('age-key', 'artha'),
            'spend_seal': service == 'artha-spend-seal' and account in {
                'seal', 'seal.shadow', 'seal.authoritative'} and profile == 'personal',
        }[kind]
        identity = (service, account)
        if not valid or identity in identities:
            raise ck.CheckpointBlocked('recovery registry record binding is invalid')
        identities.add(identity)
        found.append(entry)
    # The spend seal is named so capture can classify it; it is not a native secret and has no escrow item.
    return [entry for entry in found if entry['kind'] != 'spend_seal']


def _safe_get(backend: Any, service: str, username: str) -> str | None:
    try:
        val = backend.get_password(service, username)
    except Exception:
        raise ck.CheckpointBlocked(f"OS credential store failed while reading {service}") from None
    if val is not None and not isinstance(val, str):
        raise ck.CheckpointBlocked(f"OS credential record {service} is malformed")
    return val


def _parse_cred_head(raw: str) -> dict[str, Any]:
    from lib.credentials import CredentialError, CredentialStore  # noqa: PLC0415
    try:
        head = CredentialStore(backend=SimpleNamespace(get_password=lambda *_: raw))._head('checkpoint')
    except CredentialError:
        raise ck.CheckpointBlocked('credential head is malformed: invalid current or retired generation') from None
    if head is None:
        raise ck.CheckpointBlocked('credential head is malformed: missing head')
    refs = head.get('retired', []) + ([] if head.get('deleted') is True else [head])
    if len({ref['generation'] for ref in refs}) != len(refs):
        raise ck.CheckpointBlocked('credential head is malformed: duplicate generation refs')
    return head


def _collect_credential_recovery_and_records(  # noqa: PLR0912, PLR0915
    backend: Any,
    items: dict[str, bytes],
    *,
    profile: str,
    repo: Path,
    local: Path,
    entries: Sequence[Mapping[str, Any]],
) -> None:
    from lib.credential_migration import (  # noqa: PLC0415
        _CLIENTS,
        _PERSONAL,
        _WORK,
        MigrationError,
        _decode,
        _read_recovery,
    )
    from lib.credentials import CredentialError, CredentialStore  # noqa: PLC0415

    recovery_service = _RECOVERY_KEY_SERVICE[profile]
    recovery_dir = local / "credential-recovery"
    recovery_files = (
        sorted(
            p
            for p in recovery_dir.glob(f"{profile}-*.ae1")
            if re.fullmatch(profile + r"-([0-9a-f]{64})\.ae1", p.name)
        )
        if recovery_dir.is_dir()
        else []
    )
    sealed_tokens: list[Path] = []
    tokens_dir = repo / ".tokens"
    if tokens_dir.is_dir():
        names = set(_WORK if profile == 'work' else _PERSONAL)
        names.update({'msgraph-client-creds.json'} if profile == 'work' else _CLIENTS)
        for filename in sorted(names):
            token_path = tokens_dir / filename
            if token_path.is_file():
                with token_path.open('rb') as handle:
                    if handle.read(3) == b'AE1':
                        sealed_tokens.append(token_path)

    has_recovery_version = _safe_get(backend, recovery_service, "current_version") is not None
    if recovery_files or sealed_tokens or has_recovery_version:
        try:
            provider = KeyringKeyProvider(recovery_service, backend=backend, create=False)
            current = provider.current_version()
            for version in range(1, current + 1):
                items[f"{recovery_service}/key.{version}"] = provider.key(version)
            recovery_cipher: EnvelopeCipher | None = EnvelopeCipher(provider)
        except Exception:
            raise ck.CheckpointBlocked(
                f"credential recovery key {recovery_service} is unavailable or damaged"
            ) from None
    else:
        recovery_cipher = None

    required_records: set[str] = set()
    required_clients: set[str] = set()
    for rec_file in recovery_files:
        try:
            _, rec_records, rec_clients = _read_recovery(rec_file, backend, profile == "work")
        except (MigrationError, DecryptionError, KeyUnavailableError, ValueError, TypeError) as exc:
            raise ck.CheckpointBlocked(
                f"credential recovery backup cannot be decrypted with current OS keys: {exc}"
            ) from None
        required_records.update(rec_records)
        required_clients.update(rec_clients)

    for token_path in sealed_tokens:
        if recovery_cipher is None:
            raise ck.CheckpointBlocked("recovery cipher is required for sealed token records")
        try:
            raw = recovery_cipher.decrypt(
                token_path.read_bytes(),
                aad=f"artha.credential.source:{token_path.name}".encode(),
            )
            _, tok_records, tok_clients, _ = _decode({token_path.name: raw}, profile == "work")
        except (MigrationError, DecryptionError, ValueError, UnicodeError) as exc:
            raise ck.CheckpointBlocked(
                f"sealed token file cannot be decrypted with current OS keys: {exc}"
            ) from None
        required_records.update(tok_records)
        required_clients.update(tok_clients)

    other_profile = "personal" if profile == "work" else "work"
    forbidden_records = set(_KNOWN_CREDENTIALS[other_profile])
    cred_services = ("artha.credentials.v1", _CRED_SERVICE[profile])
    store = CredentialStore(backend=backend, local=local)
    for cred_service in dict.fromkeys(cred_services):
        candidate_names = {
            name for name in (*_KNOWN_CREDENTIALS[profile], *required_records)
            if store._service(name) == cred_service or (profile == 'work' and cred_service == 'artha.credentials.v1')
        }
        candidate_names.update(entry['account'] for entry in entries
                               if entry['kind'] == 'credential' and entry['service'] == cred_service)

        for name in sorted(candidate_names):
            if name in forbidden_records:
                continue
            if _safe_get(backend, cred_service, f"{name}.pending") is not None:
                raise ck.CheckpointBlocked("credential rotation or migration is incomplete")
            raw_head = _safe_get(backend, cred_service, name)
            if raw_head is None:
                continue
            try:
                head = _parse_cred_head(raw_head)
            except ck.CheckpointBlocked:
                if any(entry['kind'] == 'credential' and entry['service'] == cred_service
                       and entry['account'] == name and entry['state'] == 'deleted' for entry in entries):
                    raise ck.CheckpointBlocked('deleted registry dependency has an invalid credential head') from None
                raise
            items[f"{cred_service}/{name}"] = raw_head.encode("utf-8")
            if head.get("deleted") is True:
                if name in required_records:
                    raise ck.CheckpointBlocked(f"migrated credential {name} was deleted from the OS store")
            elif store._service(name) == cred_service:
                try:
                    store.read_versioned(name)
                except CredentialError as exc:
                    raise ck.CheckpointBlocked(f"credential record {name} is incomplete: {exc}") from None
            refs = ([] if head.get('deleted') is True else [head]) + head.get('retired', [])
            for ref in refs:
                for index in range(ref['chunks']):
                    chunk_key = f"{name}.chunk.{ref['generation']}.{index}"
                    chunk_val = _safe_get(backend, cred_service, chunk_key)
                    if chunk_val is None:
                        raise ck.CheckpointBlocked(f"credential generation is incomplete: chunk {chunk_key} is missing")
                    items[f"{cred_service}/{chunk_key}"] = chunk_val.encode("utf-8")

    for req_name in sorted(required_records):
        if not any(f"{svc}/{req_name}" in items for svc in dict.fromkeys(cred_services)):
            raise ck.CheckpointBlocked(f"migrated credential {req_name} is missing from the OS store")

    candidate_clients = set(_KNOWN_CLIENTS[profile]) | required_clients | {
        entry['service'] for entry in entries if entry['kind'] == 'client' and entry['state'] != 'deleted'}
    for client in sorted(candidate_clients):
        secret = _safe_get(backend, client, "artha")
        if secret is None:
            if client in required_clients:
                raise ck.CheckpointBlocked(f"migrated client credential {client} is missing from the OS store")
            continue
        if not secret.strip() or any(ord(ch) < 32 for ch in secret):
            raise ck.CheckpointBlocked(f"client credential {client} in the OS store is invalid")
        items[f"{client}/artha"] = secret.encode("utf-8")


def _discover_bundle_producers(repo: Path, local: Path) -> set[str]:
    bundle_dirs, producers = _configured_bundle_dirs(repo)
    scan_dirs = [*bundle_dirs.values()]
    for base in (repo, local):
        if base.is_dir():
            scan_dirs.extend(p for p in base.rglob("*") if p.is_dir() and "quarantine" not in p.parts)
            scan_dirs.append(base)
    seen: set[Path] = set()
    for directory in scan_dirs:
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved in seen or not directory.is_dir() or "quarantine" in directory.parts:
            continue
        seen.add(resolved)
        for pattern in ("bundle-*.json", "ack-*.json"):
            for artifact in sorted(directory.glob(pattern)):
                if "quarantine" in artifact.parts or not artifact.is_file():
                    continue
                doc: Any = None
                with suppress(OSError, ValueError):
                    doc = json.loads(artifact.read_text(encoding="utf-8"))
                header = doc.get("header") if isinstance(doc, dict) else None
                producer = (
                    doc.get("producer")
                    if isinstance(doc, dict) and isinstance(doc.get("producer"), str)
                    else (header.get("producer") if isinstance(header, dict) else None)
                )
                if isinstance(producer, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", producer):
                    producers.add(producer)
    return producers


def _collect_bundle_and_privacy_records(  # noqa: PLR0912
    backend: Any,
    items: dict[str, bytes],
    *,
    repo: Path,
    local: Path,
    cipher: EnvelopeCipher,
    entries: Sequence[Mapping[str, Any]],
) -> None:
    from catchup_core.bundles import load_producer_key  # noqa: PLC0415
    from catchup_core.ledger import KeyringSealedStore, LedgerError  # noqa: PLC0415
    from catchup_core.privacy_history import ANCHOR_SCHEMA  # noqa: PLC0415

    producers = _discover_bundle_producers(repo, local) | {
        entry['service'].removeprefix('artha-bundle-') for entry in entries
        if entry['kind'] == 'bundle' and entry['state'] != 'deleted'}
    for producer in sorted(producers):
        _safe_get(backend, f"artha-bundle-{producer}", "secret")
        pkey = load_producer_key(producer, backend)
        if pkey is None:
            raise ck.CheckpointBlocked(f"bundle signing key for {producer} is unavailable or malformed")
        items[f"artha-bundle-{producer}/secret"] = pkey.secret

    privacy_roots = [
        Path(os.environ.get("ARTHA_PRIVACY_DIR") or Path.home() / ".artha-local/privacy").expanduser(),
        local / "privacy",
    ]
    namespaces = {"shadow", "authoritative"}
    catchup_dir = local / "catchup_core"
    if catchup_dir.is_dir():
        for child in sorted(catchup_dir.iterdir()):
            if child.is_dir():
                namespaces.add("shadow" if child.name.startswith("shadow") else "authoritative")
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", child.name):
                    namespaces.add(child.name)
    namespaces.update(entry['service'].removeprefix('artha-privacy-') for entry in entries
                      if entry['kind'] == 'privacy' and entry['state'] != 'deleted')

    for ns in sorted(namespaces):
        service = f"artha-privacy-{ns}"
        ns_hash = hashlib.sha256(service.encode("utf-8")).hexdigest()
        capsule_root: Path | None = None
        has_capsules = False
        for root in privacy_roots:
            candidate = root / ns_hash
            if candidate.is_dir() and any(candidate.glob("*.ae1")):
                capsule_root = root
                has_capsules = True
                break
        raw_head = _safe_get(backend, service, "checkpoint")
        if raw_head is None:
            if has_capsules:
                raise ck.CheckpointBlocked(f"privacy anchor head {service}/checkpoint is missing from the OS store")
            continue
        history_root = capsule_root or privacy_roots[0]
        try:
            sealed = KeyringSealedStore(backend, ns, cipher=cipher, history_root=history_root)
            cp = sealed.read()
            if cp is None:
                raise ck.CheckpointBlocked(f"privacy anchor head {service}/checkpoint is empty")
            parsed_head = json.loads(raw_head)
            if isinstance(parsed_head, dict) and parsed_head.get("schema") == ANCHOR_SCHEMA:
                sealed.replay()
        except (LedgerError, ValueError, TypeError) as exc:
            raise ck.CheckpointBlocked(f"privacy anchor {service} is damaged: {exc}") from None
        items[f"{service}/checkpoint"] = raw_head.encode("utf-8")


def require_classified(items: Mapping[str, bytes], key_services: Sequence[str],
                       entries: Sequence[Mapping[str, Any]]) -> None:
    """One rule for capture and recovery: every recovery record is named by the reviewed inventory."""
    registered = {(entry['service'], entry['account']) for entry in entries}
    for name in items:
        service, account = name.split('/', 1)
        logical_account = account.split('.chunk.', 1)[0]
        if (service, logical_account) not in registered:
            raise ck.CheckpointBlocked('native recovery dependency is absent from the reviewed registry')
    for service in key_services:
        if (service, 'current_version') not in registered:
            raise ck.CheckpointBlocked('required key head is absent from the reviewed registry')


def collect_escrow(backend: Any = None, *, profile: str = 'personal', create: bool = False,  # noqa: PLR0912, PLR0915
                   repo: Path | None = None, local: Path | None = None,
                   registry_root: Path | None = None) -> dict[str, bytes]:
    """All profile OS-backed keys, credentials, bundle secrets and privacy heads, without cross-profile leakage."""
    doc = _registry_snapshot(profile, registry_root)
    entries = _registered_entries(doc, profile)
    backend = backend or system_keyring()
    repo_root = repo or _REPO
    local_root = local or Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
    try:
        vault_key = backend.get_password('age-key', 'artha')
    except Exception:
        raise ck.CheckpointBlocked('the shared age vault key is unavailable') from None
    if not isinstance(vault_key, str) or not vault_key.startswith('AGE-SECRET-KEY-'):
        raise ck.CheckpointBlocked('the shared age vault key is unavailable; checkpoint recovery would be incomplete')
    if create and profile == 'work':
        service = _WORK_KEY_SERVICES[0]
        with installation_fence(default_installation_dir(), exclusive=True):
            if _safe_get(backend, service, 'current_version') is None:
                if registry_root is not None and registry_root.resolve() != (Path.home() / '.artha-local').resolve():
                    raise ck.CheckpointBlocked('first Work key enrollment requires the canonical recovery registry')
                if _registry_snapshot(profile, registry_root) != doc:
                    raise ck.CheckpointBlocked('recovery registry changed before Work key enrollment')
                if (any(entry['service'] == service for entry in entries)
                        or _safe_get(backend, service, 'key.1') is not None):
                    raise ck.CheckpointBlocked('first Work key enrollment cannot adopt existing/deleted dependencies')
                KeyringKeyProvider(service, backend=backend, create=True).current_version()
                doc = _registry_snapshot(profile, registry_root)
                entries = _registered_entries(doc, profile)
    items: dict[str, bytes] = {'age-key/artha': vault_key.encode()}
    services = _WORK_KEY_SERVICES if profile == 'work' else _KEY_SERVICES
    primary_provider: KeyringKeyProvider | None = None
    for service in services:
        provider = KeyringKeyProvider(service, backend=backend, create=False)
        if primary_provider is None:
            primary_provider = provider
        for version in range(1, provider.current_version() + 1):
            items[f"{service}/key.{version}"] = provider.key(version)
    for entry in entries:
        service, account = entry['service'], entry['account']
        if entry['state'] == 'deleted':
            continue
        if entry['kind'] in {'key', 'key_head'}:
            if service == _RECOVERY_KEY_SERVICE[profile]:
                continue
            provider = KeyringKeyProvider(service, backend=backend, create=False)
            current = provider.current_version()
            if entry['kind'] == 'key_head' and str(entry['generation']) != str(current):
                raise ck.CheckpointBlocked('registered key head generation changed')
            if entry['kind'] == 'key' and str(entry['generation']) != account.removeprefix('key.'):
                raise ck.CheckpointBlocked('registered key generation is invalid')
            for version in range(1, current + 1):
                items[f'{service}/key.{version}'] = provider.key(version)
            if entry['kind'] == 'key':
                items[f'{service}/{account}'] = provider.key(int(account.removeprefix('key.')))
    _collect_credential_recovery_and_records(
        backend,
        items,
        profile=profile,
        repo=repo_root,
        local=local_root,
        entries=entries,
    )
    if profile == "personal":
        if primary_provider is None:
            raise ck.CheckpointBlocked("personal checkpoint requires a ledger key provider")
        _collect_bundle_and_privacy_records(
            backend,
            items,
            repo=repo_root,
            local=local_root,
            cipher=EnvelopeCipher(primary_provider),
            entries=entries,
        )
    for entry in entries:
        service, account = entry['service'], entry['account']
        if entry['state'] == 'deleted':
            raw = _safe_get(backend, service, account)
            if (entry['kind'] == 'credential'
                    and _safe_get(backend, service, f'{account}.pending') is not None):
                raise ck.CheckpointBlocked('deleted registry dependency has a pending credential write')
            if raw is not None:
                if entry['kind'] != 'credential':
                    raise ck.CheckpointBlocked('deleted registry dependency remains active in the OS store')
                try:
                    head = _parse_cred_head(raw)
                except ck.CheckpointBlocked:
                    raise ck.CheckpointBlocked('deleted registry dependency has an invalid credential head') from None
                if head.get('deleted') is not True:
                    raise ck.CheckpointBlocked('deleted registry dependency remains active in the OS store')
            if items.get(f'{service}/{account}') != (raw.encode('utf-8') if raw is not None else None):
                raise ck.CheckpointBlocked('deleted registry escrow differs from the current native record')
            continue
        raw = _safe_get(backend, service, account)
        if raw is None:
            raise ck.CheckpointBlocked('registered recovery dependency is missing from the OS store')
        if entry['kind'] == 'key_head':
            items[f'{service}/{account}'] = raw.encode('utf-8')
        value = items.get(f'{service}/{account}')
        if value is None:
            raise ck.CheckpointBlocked('registered recovery dependency is missing from escrow')
        try:
            expected = (base64.b64decode(raw, validate=True) if entry['kind'] in {'key', 'bundle'}
                        else raw.encode('utf-8'))
        except ValueError:
            raise ck.CheckpointBlocked('registered native recovery dependency is malformed') from None
        if value != expected:
            raise ck.CheckpointBlocked('registered escrow field differs from the current native record')
        if entry['kind'] == 'credential':
            head = _parse_cred_head(raw)
            if head.get('deleted') is True or str(head.get('generation')) != str(entry['generation']):
                raise ck.CheckpointBlocked('registered credential generation changed or was deleted')
        if entry['kind'] == 'key_head' and str(entry['generation']) != raw:
            raise ck.CheckpointBlocked('registered key head generation changed')
    require_classified(items, services, entries)
    _validate_escrow_records(items, profile, entries=entries)
    if _registry_snapshot(profile, registry_root) != doc:
        raise ck.CheckpointBlocked('recovery registry changed during collection')
    return items


def _passphrase(confirm: bool) -> str:
    first = getpass.getpass("Checkpoint passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != first:
        raise ck.CheckpointBlocked("passphrases differ")
    return first


def _cipher(backend: Any = None, *, profile: str = 'personal') -> EnvelopeCipher:
    service = _WORK_KEY_SERVICES[0] if profile == 'work' else _KEY_SERVICES[0]
    return EnvelopeCipher(KeyringKeyProvider(service, backend=backend or system_keyring(), create=False))


def _work_destination(destination: Path, host_platform: str, local: Path | None = None) -> None:
    if host_platform != 'win32':
        raise ck.CheckpointBlocked('Work checkpoint and recovery require the Windows Work host')
    sync_roots = [Path(value).expanduser() for name in ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')
                  if (value := os.environ.get(name))]
    sync_roots.extend(parent for parent in _REPO.resolve().parents
                      if parent.name.casefold().startswith('onedrive') or parent.name == 'CloudStorage')
    protected = [_REPO, default_installation_dir(), *sync_roots]
    if local is not None:
        protected.append(local)
    if not ck._outside(destination, protected):
        raise ck.CheckpointBlocked(
            'Work checkpoint/recovery destination must be outside personal roots and known sync roots'
        )


def _quarantine_parsers(sub: Any) -> None:
    for name in ('quarantine-list', 'quarantine-review', 'quarantine-view', 'quarantine-release',
                 'quarantine-export-view'):
        command = sub.add_parser(name)
        command.add_argument('path', help='inactive restored destination')
        command.add_argument('--actor')
        command.add_argument('--profile', choices=('personal', 'work'), default='personal')
        if name == 'quarantine-list':
            command.add_argument('--details', action='store_true', help='review-authorized member metadata')
        else:
            if name != 'quarantine-export-view':
                command.add_argument('member_id')
            command.add_argument('output_name')
        if name in ('quarantine-view', 'quarantine-release'):
            command.add_argument('--review', required=True, help='exact unexpired file-review token')
        if name == 'quarantine-release':
            command.add_argument('--attest-clean', action='store_true',
                                 help='explicitly attest the reviewed file contains no forgotten or unauthorized data')


def _parse(argv: Sequence[str]) -> argparse.Namespace:  # noqa: PLR0912 - explicit command-specific arguments
    parser = argparse.ArgumentParser(prog="checkpoint_cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    _quarantine_parsers(sub)
    digest = sub.add_parser('privacy-digest', help='SHA-256 of the verified current privacy history')
    digest.add_argument('--mode', action='append', choices=('shadow', 'authoritative'), required=True)
    digest.add_argument('--actor')
    sub.add_parser('storage-preview', help='inspect the existing configured root and installation lock')
    storage = sub.add_parser('storage-adopt', help='adopt only the exactly reviewed two native DACLs')
    storage.add_argument('--sha256', required=True, help='exact storage-preview sha256')
    storage.add_argument('--reviewed', action='store_true')
    storage.add_argument('--writers-stopped', action='store_true',
                         help='operator attestation; exclusive installation-lock access excludes cooperating writers')
    for name in ('registry-preview', 'registry-declare', 'registry-review'):
        command = sub.add_parser(name)
        command.add_argument('--profile', choices=('personal', 'work'), required=True)
        if name == 'registry-declare':
            command.add_argument('--kind', choices=('key', 'key_head', 'credential', 'client', 'bundle',
                                                   'privacy', 'vault'), required=True)
            for field in ('service', 'account', 'generation'):
                command.add_argument('--' + field, required=True)
        if name == 'registry-review':
            for field in ('sha256', 'reviewer', 'reviewed-at'):
                command.add_argument('--' + field, required=True)
    for name in ('work-privacy-status', 'work-privacy-enroll', 'work-privacy-reconcile',
                 'work-privacy-recover-pending', 'work-privacy-revoke'):
        command = sub.add_parser(name)
        if name in {'work-privacy-enroll', 'work-privacy-reconcile', 'work-privacy-revoke'}:
            command.add_argument('--reviewed', action='store_true',
                                 help='confirm the required explicit Work review')
        if name == 'work-privacy-revoke':
            command.add_argument('--member', required=True, help='bound Work member alias/path')
    for name in ("inventory", "create", "diff"):
        command = sub.add_parser(name)
        command.add_argument("--profile", choices=("personal", "work"), default="personal")
        if name == "create":
            command.add_argument("--dest", required=True, help="directory outside the checkpointed tree")
            command.add_argument("--copy-to", help="independent second copy (write-once)")
            command.add_argument("--cloud-synced", action="store_true", help="--copy-to is a OneDrive-synced folder")
        if name == "diff":
            command.add_argument("path")
            command.add_argument("--tables", action="store_true", help="include SQLite table-level row reconciliation")
    for name in ("verify", "recovery"):
        command = sub.add_parser(name)
        command.add_argument("path")
        if name == 'verify':
            command.add_argument('--profile', choices=('personal', 'work'), default='personal')
        if name == 'recovery':
            command.add_argument('--inspection-only', action='store_true',
                                 help='authenticate with the passphrase in memory; never install keys or activate')
            command.add_argument('--restore-keys', action='store_true',
                                 help='held until current privacy history is verified; use --inspection-only')
    restore = sub.add_parser("restore")
    restore.add_argument("path")
    restore.add_argument("--to", required=True)
    restore.add_argument('--profile', choices=('personal', 'work'), default='personal')
    restore.add_argument('--actor')
    return parser.parse_args(list(argv))


def _registry_action(args: argparse.Namespace, root: Path | None) -> Mapping[str, Any]:
    """Explicit historical metadata review; this surface never opens a secret backend."""
    from catchup_core import recovery_registry as registry  # noqa: PLC0415
    try:
        if args.command == 'registry-declare':
            if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value)
                   for value in (args.service, args.account, args.generation)):
                raise ck.CheckpointBlocked('registry declaration names are invalid or unbounded')
            doc = registry.declare(args.profile, args.kind, args.service, args.account, args.generation, root=root)
        elif args.command == 'registry-review':
            if (not re.fullmatch(r'[0-9a-f]{64}', args.sha256)
                    or not 1 <= len(args.reviewer) <= 128 or not args.reviewer.strip()
                    or any(ord(char) < 32 or ord(char) == 127 for char in args.reviewer)
                    or not 1 <= len(args.reviewed_at) <= 64):
                raise ck.CheckpointBlocked('registry review metadata is invalid or unbounded')
            doc = registry.attest_legacy(args.profile, args.sha256, args.reviewer, args.reviewed_at, root=root)
        else:
            doc = registry.snapshot(args.profile, root=root, require_review=False)
        records = [{field: entry.get(field) for field in
                    ('kind', 'service', 'account', 'generation', 'state', 'attempt')}
                   for entry in doc['entries']]
        return {'profile': args.profile, 'count': len(records), 'records': records,
                'sha256': registry.registry_digest(doc)}
    except registry.RegistryError:
        raise ck.CheckpointBlocked('recovery registry operation refused; inspect metadata and review its exact digest'
                                   ) from None


def _validate_versioned_service_keys(items: Mapping[str, bytes], service: str, *, required: bool) -> dict[int, bytes]:
    prefix = service + "/key."
    matching = [name for name in items if name.startswith(prefix)]
    if not matching:
        if required:
            raise ck.CheckpointBlocked("inspection requires all checkpoint envelope and identity key versions")
        return {}
    if any(not re.fullmatch(r'[1-9][0-9]{0,9}', name[len(prefix):]) for name in matching):
        raise ck.CheckpointBlocked(f"malformed key version in {service}")
    keys = {int(name[len(prefix):]): items[name] for name in matching}
    if (
        sorted(keys) != list(range(1, len(keys) + 1))
        or any(not 1 <= version < 2 ** 32 or len(material) != 32 for version, material in keys.items())
    ):
        raise ck.CheckpointBlocked(f"incomplete or invalid key version history for {service}")
    return keys


def _validate_escrow_records(items: Mapping[str, bytes], profile: str, *,  # noqa: PLR0912, PLR0915
                             entries: Sequence[Mapping[str, Any]] = ()) -> None:
    from catchup_core.work_privacy import WorkPrivacy  # noqa: PLC0415
    if any(name.split('/', 1)[0] in WorkPrivacy.EXCLUDED_SERVICES for name in items):
        raise ck.CheckpointBlocked('checkpoint escrow contains dedicated Work privacy authority')
    other_profile = "personal" if profile == "work" else "work"
    forbidden_prefixes = (
        *(_KEY_SERVICES if profile == "work" else _WORK_KEY_SERVICES),
        _RECOVERY_KEY_SERVICE[other_profile],
        *((_CRED_SERVICE[other_profile],) if _CRED_SERVICE[other_profile] != "artha.credentials.v1" else ()),
        *_KNOWN_CLIENTS[other_profile],
    )
    if any(name == p or name.startswith(p + "/") for name in items for p in forbidden_prefixes):
        raise ck.CheckpointBlocked("checkpoint escrow contains cross-profile keys or credentials")
    if profile == "work" and any(
        name.startswith(("artha-bundle-", "artha-privacy-")) for name in items
    ):
        raise ck.CheckpointBlocked("Work recovery cannot contain personal bundle or privacy records")

    _validate_versioned_service_keys(items, _RECOVERY_KEY_SERVICE[profile], required=False)

    forbidden_records = set(_KNOWN_CREDENTIALS[other_profile])
    allowed_cred_services = tuple(dict.fromkeys(("artha.credentials.v1", _CRED_SERVICE[profile])))
    for cred_service in allowed_cred_services:
        cred_prefix = cred_service + "/"
        cred_items = {name[len(cred_prefix):]: val for name, val in items.items() if name.startswith(cred_prefix)}
        heads = {k: v for k, v in cred_items.items() if ".chunk." not in k}
        seen_chunks: set[str] = set()
        for record_name, raw_head in sorted(heads.items()):
            if record_name in forbidden_records or (profile == "personal" and record_name.startswith("work.")):
                raise ck.CheckpointBlocked("checkpoint escrow contains cross-profile credentials")
            if record_name.endswith(".pending"):
                raise ck.CheckpointBlocked("escrow cannot contain uncommitted pending credentials")
            try:
                head = _parse_cred_head(raw_head.decode("utf-8"))
            except UnicodeError:
                raise ck.CheckpointBlocked(f"escrowed credential head {record_name} is invalid UTF-8") from None
            refs = ([] if head.get('deleted') is True else [head]) + head.get('retired', [])
            for ref in refs:
                parts: list[str] = []
                for idx in range(ref['chunks']):
                    ckey = f"{record_name}.chunk.{ref['generation']}.{idx}"
                    if ckey not in cred_items:
                        raise ck.CheckpointBlocked(f"escrowed credential {record_name} is missing chunk {idx}")
                    seen_chunks.add(ckey)
                    try:
                        parts.append(cred_items[ckey].decode("utf-8"))
                    except UnicodeError:
                        raise ck.CheckpointBlocked(f"escrowed credential chunk {ckey} is invalid UTF-8") from None
                joined = "".join(parts).encode("utf-8")
                if not joined.isascii():
                    raise ck.CheckpointBlocked(f"escrowed credential {record_name} contains non-ASCII chunks")
                if ref is head and hashlib.sha256(joined).hexdigest() != head['digest']:
                    raise ck.CheckpointBlocked(f"escrowed credential {record_name} failed digest check")
        if set(cred_items) - set(heads) - seen_chunks:
            raise ck.CheckpointBlocked("escrowed credential store contains orphan chunk records")

    clients = set(_KNOWN_CLIENTS[profile]) | {entry['service'] for entry in entries
                                            if entry['kind'] == 'client' and entry['state'] != 'deleted'}
    key_services = set(_WORK_KEY_SERVICES if profile == 'work' else _KEY_SERVICES) | {
        entry['service'] for entry in entries if entry['kind'] in {'key', 'key_head'}
        and entry['state'] != 'deleted'}
    for service in key_services:
        _validate_versioned_service_keys(items, service, required=True)
    for client in clients:
        key = f"{client}/artha"
        if key in items:
            try:
                text = items[key].decode("utf-8")
            except UnicodeError:
                raise ck.CheckpointBlocked(f"escrowed client secret {client} is invalid") from None
            if not text.strip() or any(ord(ch) < 32 for ch in text):
                raise ck.CheckpointBlocked(f"escrowed client secret {client} is empty or invalid")

    allowed_names = (
        "age-key/artha",
        *(f"{c}/artha" for c in clients),
        *(f"{service}/current_version" for service in key_services),
        f"{_RECOVERY_KEY_SERVICE[profile]}/current_version",
    )
    allowed_prefixes = (
        *(f"{s}/key." for s in key_services),
        f"{_RECOVERY_KEY_SERVICE[profile]}/key.",
        *(f"{s}/" for s in allowed_cred_services),
    )
    for name, value in items.items():
        if name in allowed_names or any(name.startswith(prefix) for prefix in allowed_prefixes):
            continue
        if profile == "personal" and name.startswith("artha-bundle-") and name.endswith("/secret"):
            producer = name.removeprefix("artha-bundle-").removesuffix("/secret")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", producer) or len(value) != 32:
                raise ck.CheckpointBlocked(f"escrowed bundle key {name} is invalid")
            continue
        if profile == "personal" and name.startswith("artha-privacy-") and name.endswith("/checkpoint"):
            ns = name.removeprefix("artha-privacy-").removesuffix("/checkpoint")
            try:
                doc = json.loads(value.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise ck.CheckpointBlocked(f"escrowed privacy head {name} is malformed") from None
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", ns) or not isinstance(doc, dict):
                raise ck.CheckpointBlocked(f"escrowed privacy head {name} is invalid")
            continue
        raise ck.CheckpointBlocked(f"unexpected record {name} in checkpoint escrow")

    for entry in entries:
        name = f"{entry['service']}/{entry['account']}"
        registered_value = items.get(name)
        if entry['state'] == 'deleted':
            if registered_value is not None and (entry['kind'] != 'credential'
                    or _parse_cred_head(registered_value.decode('utf-8')).get('deleted') is not True):
                raise ck.CheckpointBlocked('deleted registry dependency remains active in escrow')
            continue
        if registered_value is None:
            raise ck.CheckpointBlocked('registered recovery dependency is missing from escrow')
        generation = str(entry['generation'])
        if entry['kind'] == 'key':
            if generation != entry['account'].removeprefix('key.'):
                raise ck.CheckpointBlocked('registered key generation is invalid')
        elif entry['kind'] == 'key_head':
            keys = _validate_versioned_service_keys(items, entry['service'], required=True)
            if registered_value != str(max(keys)).encode('ascii') or registered_value != generation.encode('utf-8'):
                raise ck.CheckpointBlocked('registered key head generation differs from escrow')
        elif entry['kind'] == 'credential':
            head = _parse_cred_head(registered_value.decode('utf-8'))
            if head.get('deleted') is True or head.get('generation') != generation:
                raise ck.CheckpointBlocked('registered credential generation differs from escrow')
        elif entry['kind'] == 'privacy':
            if generation != 'legacy' and str(json.loads(registered_value)['checkpoint_id']) != generation:
                raise ck.CheckpointBlocked('registered privacy generation differs from escrow')
        elif entry['kind'] == 'bundle':
            from catchup_core.bundles import BUNDLE_SCHEMA  # noqa: PLC0415
            if generation not in {'legacy', BUNDLE_SCHEMA}:
                raise ck.CheckpointBlocked('registered bundle generation differs from escrow')


def _recovery_cipher(items: Mapping[str, bytes], profile: str, *,
                     entries: Sequence[Mapping[str, Any]] = ()) -> EnvelopeCipher:
    """Validate and pin the complete captured key generation, independent of later OS-store rotation."""
    providers = {}
    services = _WORK_KEY_SERVICES if profile == 'work' else _KEY_SERVICES
    if not items.get('age-key/artha', b'').startswith(b'AGE-SECRET-KEY-'):
        raise ck.CheckpointBlocked('checkpoint vault key recovery is incomplete')
    for service in services:
        keys = _validate_versioned_service_keys(items, service, required=True)
        providers[service] = StaticKeyProvider(keys)
    _validate_escrow_records(items, profile, entries=entries)
    return EnvelopeCipher(providers[services[0]])


def recover_escrow(path: Path, passphrase: str, backend: Any = None, *,
                   expected_profile: str | None = None,
                   repo: Path | None = None, local: Path | None = None) -> dict[str, Any]:
    """Authenticate recovery material; OS installation needs independently verified current privacy history."""
    result = _inspect_recovery(path, passphrase)
    if expected_profile is not None and result['profile'] != expected_profile:
        raise ck.CheckpointBlocked('checkpoint profile does not match the requested recovery profile')
    raise ck.CheckpointBlocked('OS key installation requires independently verified current privacy history; '
                               'use recovery --inspection-only')


def _authenticated_recovery(path: Path, secret: str) -> tuple[dict[str, bytes], dict[str, Any]]:
    with path.open('rb', buffering=0) as source:
        generation, escrow = ck._read_head(source)
        items = ck._decrypt_escrow(escrow, secret)
        profile = 'work' if any(name.startswith('artha-work-checkpoint/') for name in items) else 'personal'
        service = (_WORK_KEY_SERVICES if profile == 'work' else _KEY_SERVICES)[0]
        cipher = EnvelopeCipher(StaticKeyProvider(_validate_versioned_service_keys(items, service, required=True)))
        source.seek(0)
        manifest = ck._scan(source, cipher)
    context = manifest.get('context', {})
    dependency_inventory = context.get('recovery_registry') if isinstance(context, dict) else None
    if not isinstance(dependency_inventory, dict):
        raise ck.CheckpointBlocked('checkpoint records no reviewed dependency inventory; recovery would be incomplete')
    entries = _registered_entries(dependency_inventory, profile)
    require_classified(items, _WORK_KEY_SERVICES if profile == 'work' else _KEY_SERVICES, entries)
    _recovery_cipher(items, profile, entries=entries)
    if (manifest['generation'] != generation
            or manifest['escrow_sha256'] != hashlib.sha256(escrow).hexdigest()):
        raise ck.CheckpointBlocked('checkpoint changed during recovery authentication')
    if manifest.get('escrow_items') != sorted(items):
        raise ck.CheckpointBlocked('checkpoint recovery inventory differs from its authenticated manifest')
    if manifest.get('profile') != profile:
        raise ck.CheckpointBlocked('checkpoint profile differs from its recovery key namespace')
    return items, manifest


def _inspect_recovery(path: Path, secret: str) -> dict[str, Any]:
    _, manifest = _authenticated_recovery(path, secret)
    out = {'verified': True, 'inspection_only': True, 'activation_allowed': False,
           'privacy_history': 'unverified', 'generation': manifest['generation'],
           'profile': manifest['profile'], 'files': len(manifest['entries'])}
    if manifest['profile'] == 'work':
        context = manifest.get('context')
        binding = context.get('work_binding') if isinstance(context, dict) else None
        out['work_binding'] = 'present_unverified' if isinstance(binding, dict) else 'unbound_inspection_only'
    return out


def _remove_artifact(store: Path, name: str) -> None:
    ck._safe("artifact/" + name)
    (store / name).unlink(missing_ok=True)


def _purge_archives(store: Path, db: sqlite3.Connection, catchup_ids: Sequence[str]) -> None:
    for catchup_id in catchup_ids:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", catchup_id):
            raise ck.CheckpointError("invalid archive identity in privacy history")
        for archive in (store / "briefings").glob(f"*-{catchup_id}.v*.ae1"):
            archive.unlink(missing_ok=True)
        db.execute("UPDATE briefings SET status='superseded' WHERE catchup_id=?", (catchup_id,))


def _privacy_hook(backend: Any, sealed_for: Callable[[str], Any] | None = None) -> Callable[[Path], None]:
    """Replay the host's current consent and tombstones into every restored ledger, or fail closed.

    Erased Signals, their evidence blobs, replay and corpus records, captured documents and archived Briefings are
    deleted from the restored copy; a restored ledger that cannot be verified against the trusted privacy
    anchor on this host (or a host with no anchor) blocks the restore and the destination is removed.

    Replay proves no lineage for arbitrary State Files, caches or other nonledger copies, even ciphertext.
    Normal restore of surviving nonledger artifacts is unsupported pending explicit owner review; no content
    matching or automatic purge is attempted. Refusal leaves the original authenticated checkpoint unchanged
    for later reviewed handling, not a plaintext quarantine presented as current data.
    """
    def hook(dest: Path) -> None:
        from catchup_core.backup import apply_privacy_history  # noqa: PLC0415
        from catchup_core.ledger import KeyringSealedStore, LedgerLocked  # noqa: PLC0415
        ledgers = set(dest.glob('**/ledger.db'))
        nonledger = [path for path in dest.rglob('*') if path.is_file() and path not in ledgers
                     and path != dest / '.artha-restored.json']
        for ledger in sorted(ledgers):
            store = ledger.parent
            mode = "shadow" if store.name.startswith("shadow") else "authoritative"
            privacy = sealed_for(mode) if sealed_for else KeyringSealedStore(backend, mode, cipher=_cipher(backend))
            checkpoint = privacy.read()
            if checkpoint is None:
                raise LedgerLocked(f"{store.name}: this host has no trusted privacy history to replay")
            with closing(sqlite3.connect(ledger)) as db:
                def remove(name: str, store: Path = store) -> None:
                    _remove_artifact(store, name)

                def purge(ids: list[str], store: Path = store, db: sqlite3.Connection = db) -> None:
                    _purge_archives(store, db, ids)
                apply_privacy_history(db, remove, checkpoint, privacy.replay(), purge)
        if any(path.exists() or path.is_symlink() for path in nonledger):
            raise ck.CheckpointBlocked('nonledger privacy lineage is unproven; normal restore is unsupported '
                                       'pending explicit owner review; retain the original encrypted checkpoint '
                                       'and use recovery --inspection-only for structural metadata')
        if not ledgers:
            raise ck.CheckpointBlocked('this host has no restored ledger with trusted privacy history to replay')
    return hook


def _work_privacy(repo: Path, local: Path, backend: Any) -> Any:
    from catchup_core.work_privacy import for_installation  # noqa: PLC0415

    return for_installation(repo, local, backend=backend)


def _work_privacy_errors() -> tuple[type[BaseException], ...]:
    from keyring.errors import KeyringError  # noqa: PLC0415

    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError  # noqa: PLC0415
    from catchup_core.work_privacy import WorkPrivacyBlocked  # noqa: PLC0415

    return (WorkPrivacyBlocked, QuarantineFilesystemError, KeyUnavailableError,
            SessionBusy, KeyringError, OSError)


def _work_privacy_summary(current: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'enrolled': True,
        'current': True,
        'generation': current['generation'],
        'member_count': len(current['members']),
    }


def _work_privacy_command(args: argparse.Namespace, repo: Path, local: Path,
                          backend: Any) -> Mapping[str, Any]:
    protocol = _work_privacy(repo, local, backend)
    if args.command == 'work-privacy-status':
        if not protocol.is_enrolled():
            return {'enrolled': False, 'current': False}
        try:
            return _work_privacy_summary(protocol.current())
        except _work_privacy_errors() as exc:
            raise ck.CheckpointBlocked('current Work privacy status is unavailable') from exc
    if args.command == 'work-privacy-enroll':
        try:
            return _work_privacy_summary(protocol.enroll_generated(reviewed=True))
        except _work_privacy_errors() as exc:
            raise ck.CheckpointBlocked('Work enrollment could not be completed') from exc
    if args.command == 'work-privacy-reconcile':
        try:
            return _work_privacy_summary(protocol.reconcile(reviewed=True))
        except _work_privacy_errors() as exc:
            raise ck.CheckpointBlocked('Work drift reconciliation could not be completed') from exc
    if args.command == 'work-privacy-recover-pending':
        try:
            return _work_privacy_summary(protocol.recover_pending())
        except _work_privacy_errors() as exc:
            raise ck.CheckpointBlocked('pending Work recovery could not be completed') from exc
    if args.command == 'work-privacy-revoke':
        try:
            with protocol.transaction() as transaction:
                if args.member not in transaction.pinned['members']:
                    raise ck.CheckpointBlocked('revocation requires a current Work member')
                return _work_privacy_summary(transaction.publish({}, revoke=(args.member,)))
        except _work_privacy_errors() as exc:
            raise ck.CheckpointBlocked('Work member revocation could not be completed') from exc
    raise ck.CheckpointBlocked('unknown Work privacy operation')


def _work_binding_matches(binding: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    head = hashlib.sha256(json.dumps(current['head'], sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()
    return (binding.get('identity') == current.get('identity')
            and binding.get('generation') == current.get('generation')
            and binding.get('head_sha256') == head
            and binding.get('members') == current.get('members'))


def _verify_work_checkpoint_inventory(binding: Mapping[str, Any], roots: Mapping[str, Path],
                                      exclude: Mapping[str, str]) -> None:
    members = binding.get('members')
    if not isinstance(members, Mapping):
        raise ck.CheckpointBlocked('Work checkpoint member binding is invalid')
    found, problems, _ = ck._walk(roots, exclude)
    if problems:
        raise ck.CheckpointBlocked('Work checkpoint inventory is incomplete')
    actual = {name: path for name, path in found.items()
              if name.startswith(('work_state/', 'work_local/'))}
    if set(actual) != set(members):
        raise ck.CheckpointBlocked('Work checkpoint differs from its journaled member inventory')
    for name, member in members.items():
        path = actual[name]
        if ck._is_sqlite(path):
            raise ck.CheckpointBlocked('Work recovery does not yet support SQLite member images')
        if (not isinstance(member, Mapping) or path.stat().st_size != member.get('size')
                or ck._file_sha(path) != member.get('sha256')):
            raise ck.CheckpointBlocked('Work checkpoint member differs from its journaled bytes')


def _create_checkpoint(args: argparse.Namespace, backend: Any, local: Path | None,  # noqa: PLR0912, PLR0915 - ordered capture gates
                       passphrase: Callable[[bool], str], host_platform: str,
                       registry_root: Path | None = None) -> Mapping[str, Any]:
    if args.profile == 'work' and args.copy_to:
        raise ck.CheckpointBlocked('the Work checkpoint stays on this host (owner decision D-7)')
    state_local = local or Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
    if args.profile == 'work':
        _work_destination(Path(args.dest).expanduser(), host_platform, state_local)
    work_protocol = _work_privacy(_REPO, state_local, backend) if args.profile == 'work' else None
    work_binding: dict[str, Any] | None = None
    if work_protocol is not None:
        _registered_entries(_registry_snapshot('work', registry_root), 'work')
        if not work_protocol.is_enrolled():
            raise ck.CheckpointBlocked('explicit reviewed Work enrollment is required before recovery capture')
        work_binding = work_protocol.bind_checkpoint()
    secret = passphrase(True)
    ck._check_passphrase(secret)
    from catchup_core.runtime import action_database_path  # noqa: PLC0415
    queues = (action_database_path(_REPO),)
    if any(not queue.is_absolute() for queue in queues):
        raise ck.CheckpointBlocked('the configured action store needs an absolute path')
    with ExitStack() as locks:
        for directory in sorted({state_local.resolve(), (registry_root or Path.home() / '.artha-local').resolve(),
                     (Path.home() / '.artha-local').resolve(),
                                 *(queue.parent.resolve() for queue in queues)}):
            locks.enter_context(installation_fence(directory, exclusive=True))
        if args.profile == 'work':
            locks.enter_context(session_lock(state_local / 'work/cache/cache.lock'))
        for pattern in (
            'catchup_core/**/ledger.session.lock',
            'catchup_core/**/bundle.import.lock',
            'credential-locks/*.lock',
            'writer-locks/*.lock',
        ):
            for lock in sorted(state_local.glob(pattern)):
                locks.enter_context(session_lock(lock))
        if (action_database_path(_REPO),) != queues:
            raise ck.CheckpointBlocked('installation dependencies changed before capture; start a new checkpoint')
        roots, exclude = profile_layout(args.profile, local, registry_root=registry_root)
        _registry_snapshot(args.profile, registry_root)
        if profile_layout(args.profile, local, registry_root=registry_root) != (roots, exclude):
            raise ck.CheckpointBlocked('installation dependencies changed before capture; start a new checkpoint')
        if work_protocol is not None:
            if work_binding is None:
                raise ck.CheckpointBlocked('current Work checkpoint binding is unavailable')
            transaction = locks.enter_context(work_protocol.transaction())
            if not _work_binding_matches(work_binding, transaction.pinned):
                raise ck.CheckpointBlocked('current Work privacy head changed before capture')
            _verify_work_checkpoint_inventory(work_binding, roots, exclude)
        busy = busy_reasons(state_local, check_sessions=False, action_paths=queues, repo=_REPO)
        if busy:
            raise ck.CheckpointBlocked('not quiesced: ' + '; '.join(busy))
        escrow = collect_escrow(backend, profile=args.profile, create=True, repo=_REPO, local=state_local,
                                registry_root=registry_root)
        registry_doc = _registry_snapshot(args.profile, registry_root)
        entries = _registered_entries(registry_doc, args.profile)
        def check_keys() -> None:
            if work_protocol is not None:
                if work_binding is None:
                    raise ck.CheckpointBlocked('current Work checkpoint binding is unavailable')
                try:
                    current_work = transaction.check_current()
                except _work_privacy_errors() as exc:
                    raise ck.CheckpointBlocked(
                        'current Work identity, history or members changed during capture'
                    ) from exc
                if not _work_binding_matches(work_binding, current_work):
                    raise ck.CheckpointBlocked('current Work privacy head changed during capture')
                _verify_work_checkpoint_inventory(work_binding, roots, exclude)
            if _registry_snapshot(args.profile, registry_root) != registry_doc:
                raise ck.CheckpointBlocked('recovery registry changed during capture; start a new checkpoint')
            if profile_layout(args.profile, local, registry_root=registry_root) != (roots, exclude):
                raise ck.CheckpointBlocked('installation dependencies changed during capture; start a new checkpoint')
            if collect_escrow(backend, profile=args.profile, repo=_REPO, local=state_local,
                              registry_root=registry_root) != escrow:
                raise ck.CheckpointBlocked('recovery keys changed during capture; start a new checkpoint')
        path = ck.create(args.profile, roots, Path(args.dest).expanduser(),
                         _recovery_cipher(escrow, args.profile, entries=entries), escrow,
                         secret, now=datetime.now(UTC), lock_path=state_local / 'checkpoint.lock',
                         exclude=exclude, quiesce=lambda: busy_reasons(state_local, check_sessions=False,
                                                                      action_paths=queues, repo=_REPO),
                         context={'host_os': host_platform, 'recovery_registry': registry_doc, **({'preservation': {
                             'schema': 'artha.installation-preservation.v1', 'repository_root': 'repo',
                             'runtime_root': 'local', 'root_names': sorted(roots),
                             'inventory': 'recursive-fail-closed'}} if args.profile == 'personal' else {}),
                                 **({'work_binding': work_binding} if work_binding is not None else {})},
                         before_publish=check_keys)
    out = {'checkpoint': str(path), 'bytes': path.stat().st_size}
    if args.copy_to:
        copy = ck.copy_to(path, Path(args.copy_to).expanduser(), list(roots.values()),
                          cloud_synced=args.cloud_synced)
        out = {**out, 'copy': str(copy), 'uploaded': 'unconfirmed: check the sync status'}
    return out


def _authorize_recovery(access: Any) -> None:
    from catchup_core.runtime import RuntimeConfigError  # noqa: PLC0415
    try:
        access.authorize()
    except RuntimeConfigError:
        raise ck.CheckpointBlocked('current owner configuration and trusted privacy history are required '
                                   'before historical-file recovery') from None


def _quarantine_command(args: argparse.Namespace, backend: Any, local: Path,
                        sealed_for: Callable[[str], Any] | None,
                        now: Callable[[], datetime] | None,
                        host_platform: str) -> Mapping[str, Any]:
    from catchup_core import quarantine_review  # noqa: PLC0415
    from catchup_core.recovery_access import RecoveryAccess, WorkRecoveryAccess  # noqa: PLC0415
    export = args.command != 'quarantine-list' or args.details
    if args.profile == 'work':
        _work_destination(Path(args.path).expanduser().absolute(), host_platform, local)
        protocol = _work_privacy(_REPO, local, backend)
        try:
            protocol.current()
        except _work_privacy_errors() as exc:
            raise ck.CheckpointBlocked('same-host current Work privacy authority is required') from exc
        work_access = WorkRecoveryAccess(_REPO, protocol, _cipher(backend, profile='work'), actor=args.actor)
        store = work_access.open_store(Path(args.path).expanduser().absolute(), export=export)
    else:
        personal_access = RecoveryAccess(_REPO, backend or system_keyring(), _cipher(backend), sealed_for=sealed_for,
                                         actor=args.actor, now=now)
        _authorize_recovery(personal_access)
        store = personal_access.open_store(Path(args.path).expanduser().absolute(), export=export)
    instant = (now or (lambda: datetime.now(UTC)))()
    if args.command == 'quarantine-list':
        fields = ('member_id', 'size', 'kind', 'state', 'path') if args.details else (
            'member_id', 'size', 'kind', 'state')
        out = {'profile': args.profile, 'members': [{field: entry[field] for field in fields}
               for entry in store.inventory()], 'activation_allowed': False}
    elif args.command == 'quarantine-review':
        out = quarantine_review.preview(store, args.member_id, args.output_name, now=instant)
    elif args.command == 'quarantine-export-view':
        exported = quarantine_review.read_export(store, args.output_name, now=instant)
        body = exported['data']
        if len(body) > quarantine_review.MAX_VIEW_BYTES:
            raise ck.CheckpointBlocked('export exceeds the bounded text-view limit')
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            raise ck.CheckpointBlocked('export is not supported UTF-8 text') from None
        out = {key: value for key, value in exported.items() if key != 'data'}
        out = {**out, 'text': text, 'untrusted': True, 'view_only': True, 'format': 'untrusted_text'}
    elif args.command == 'quarantine-view':
        out = quarantine_review.view(store, args.member_id, args.output_name, args.review, now=instant)
    else:
        out = quarantine_review.release(store, args.member_id, args.output_name, args.review,
                                        attest_clean=args.attest_clean, now=instant)
    return out


def run(argv: Sequence[str], stdout: TextIO, stderr: TextIO, *, backend: Any = None,  # noqa: PLR0911, PLR0912, PLR0915 - flat CLI router with early refusal exits
        local: Path | None = None, passphrase: Callable[[bool], str] = _passphrase,
    sealed_for: Callable[[str], Any] | None = None, host_platform: str | None = None,
    registry_root: Path | None = None, now: Callable[[], datetime] | None = None) -> int:
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return int(ExitCode.USAGE_ERROR) if exc.code else 0
    out: Mapping[str, Any]
    state_local = local or Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
    try:
        if args.command in {'storage-preview', 'storage-adopt'}:
            from catchup_core import storage_adoption  # noqa: PLC0415
            try:
                out = (storage_adoption.preview() if args.command == 'storage-preview' else
                       storage_adoption.adopt(args.sha256, reviewed=args.reviewed,
                                              writers_stopped=args.writers_stopped))
            except storage_adoption.StorageAdoptionError:
                raise ck.CheckpointBlocked('storage operation refused; obtain a fresh exact preview and review'
                                           ) from None
            print(json.dumps(out), file=stdout)
            return int(ExitCode.COMPLETE)
        if args.command.startswith('quarantine-'):
            out = _quarantine_command(args, backend, state_local, sealed_for, now, host_platform or sys.platform)
            print(json.dumps(out), file=stdout)
            return int(ExitCode.COMPLETE)
        if args.command == 'privacy-digest':
            from catchup_core.recovery_access import RecoveryAccess  # noqa: PLC0415
            digest_access = RecoveryAccess(_REPO, backend or system_keyring(), _cipher(backend),
                                           sealed_for=sealed_for, actor=args.actor, now=now)
            _authorize_recovery(digest_access)
            modes = sorted(set(args.mode))
            print(json.dumps({'modes': modes, 'history_sha256': digest_access.history_digest(set(modes))}),
                  file=stdout)
            return int(ExitCode.COMPLETE)
        if args.command.startswith('work-privacy-'):
            if (args.command in {'work-privacy-enroll', 'work-privacy-reconcile', 'work-privacy-revoke'}
                    and not args.reviewed):
                raise ck.CheckpointBlocked('explicit --reviewed confirmation is required')
            out = _work_privacy_command(args, _REPO, state_local, backend)
            print(json.dumps(out), file=stdout)
            return int(ExitCode.BLOCKED if out.get('current') is False else ExitCode.COMPLETE)
        if args.command in ("inventory", "diff"):
            roots, exclude = profile_layout(args.profile, local, registry_root=registry_root)
        if args.command.startswith('registry-'):
            out = _registry_action(args, registry_root)
        elif args.command == "inventory":
            found, problems, excluded = ck._walk(roots, exclude)
            out = {"files": len(found), "excluded": len(excluded), "blocking": len(problems),
                   "ready": not problems}
        elif args.command == "create":
            out = _create_checkpoint(args, backend, local, passphrase, host_platform or sys.platform, registry_root)
        elif args.command == "verify":
            manifest = ck.verify(Path(args.path), _cipher(backend, profile=args.profile))
            if manifest.get('profile') != args.profile:
                raise ck.CheckpointBlocked('checkpoint does not match the requested profile')
            out = {"generation": manifest["generation"], "profile": manifest["profile"],
                   "files": len(manifest["entries"]), "verified": True}
        elif args.command == "recovery":
            if args.inspection_only:
                print(json.dumps(_inspect_recovery(Path(args.path), passphrase(False))), file=stdout)
                return int(ExitCode.COMPLETE)
            if getattr(args, "restore_keys", False):
                print(
                    json.dumps(
                        recover_escrow(Path(args.path), passphrase(False), backend, repo=_REPO, local=state_local)
                    ),
                    file=stdout,
                )
                return int(ExitCode.COMPLETE)
            items, manifest = _authenticated_recovery(Path(args.path), passphrase(False))
            profile = manifest['profile']
            work_status: dict[str, Any] = {}
            if profile == 'work':
                context = manifest.get('context')
                binding = context.get('work_binding') if isinstance(context, dict) else None
                if not isinstance(binding, dict):
                    raise ck.CheckpointBlocked('unbound Work checkpoints are inspection-only')
                protocol = _work_privacy(_REPO, state_local, backend)
                try:
                    checked = protocol.check_checkpoint(binding)
                    denied = protocol.denied_members(binding, list(binding['members']))
                    confirmed = protocol.current()
                except _work_privacy_errors() as exc:
                    raise ck.CheckpointBlocked(
                        'same-host current Work identity and member history are required'
                    ) from exc
                if checked.get('head') != confirmed.get('head'):
                    raise ck.CheckpointBlocked('current Work privacy head changed during recovery inspection')
                work_status = {'work_authority_verified': True, 'work_members_blocked': len(denied)}
            live = collect_escrow(backend, profile=profile, repo=_REPO, local=state_local, registry_root=registry_root)
            out = {"escrow_items": len(items), "recoverable": sorted(items) == sorted(live)
                   and all(items[k] == live[k] for k in live), **work_status}
        elif args.command == "diff":
            changed = ck.diff(
                Path(args.path),
                _cipher(backend, profile=args.profile),
                roots,
                exclude,
                include_tables=getattr(args, "tables", False),
            )
            out = {k: len(v) if isinstance(v, list) else v for k, v in changed.items()}
        elif args.profile == 'work':
            work_destination = Path(args.to).expanduser()
            _work_destination(work_destination, host_platform or sys.platform, state_local)
            _, manifest = _authenticated_recovery(Path(args.path), passphrase(False))
            protocol = _work_privacy(_REPO, state_local, backend)
            from catchup_core.recovery_access import WorkRecoveryAccess  # noqa: PLC0415
            cipher = _cipher(backend, profile='work')
            work_access = WorkRecoveryAccess(_REPO, protocol, cipher, actor=args.actor)
            work_access.authorize_checkpoint(manifest)
            work_roots, _ = profile_layout('work', local, registry_root=registry_root)
            out = ck.restore(Path(args.path), work_destination, cipher,
                             list(work_roots.values()), replay_privacy=work_access.replay,
                             expected_profile='work', quarantine_factory=work_access.factory)
        else:
            roots_live, _ = profile_layout("personal", local, registry_root=registry_root)
            from catchup_core.recovery_access import RecoveryAccess  # noqa: PLC0415
            cipher = _cipher(backend)
            personal_access = RecoveryAccess(_REPO, backend or system_keyring(), cipher, sealed_for=sealed_for,
                                             actor=args.actor, now=now)
            _authorize_recovery(personal_access)
            out = ck.restore(Path(args.path), Path(args.to).expanduser(), cipher,
                             list(roots_live.values()), replay_privacy=personal_access.replay,
                             expected_profile='personal', quarantine_factory=personal_access.factory)
        print(json.dumps(out, default=str), file=stdout)
        return int(ExitCode.BLOCKED if out.get('ready') is False or out.get('recoverable') is False
                   else ExitCode.COMPLETE)
    except SessionBusy:
        print('blocked: not quiesced; an installation session or checkpoint is active', file=stderr)
        return int(ExitCode.BLOCKED)
    except (ck.CheckpointError, KeyUnavailableError, OSError, RuntimeError, ValueError) as exc:
        print(f"blocked: {type(exc).__name__ if isinstance(exc, OSError) else exc}", file=stderr)
        return int(ExitCode.BLOCKED)


if __name__ == "__main__":      # pragma: no cover
    sys.exit(run(sys.argv[1:], sys.stdout, sys.stderr))
