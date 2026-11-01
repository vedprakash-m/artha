"""Runtime wiring: build real dependencies for a (shadow) Catch-up (re-artha §6.5, §6.10).

Everything environment-specific is configured here and nowhere else:

* ledger, encrypted blobs and the sealed deletion checkpoint live under the
  machine-local directory (``~/.artha-local/catchup_core`` or ``$ARTHA_LOCAL_DIR``);
* keys come from the OS credential store (never from a file);
* pilot sources map to *configured* incumbent connectors through an owner-edited
  file (``config/catchup_core.yaml``, git-ignored), e.g.::

      sources:
        school_email: {connector: gmail,           account_scope: personal, kwargs: {query: "label:school"}}
        calendar:     {connector: google_calendar, account_scope: family}
      subjects: [kid1, kid2, adult_child]
      consent_required: [adult_child]     # adults whose restricted data needs their recorded consent
      viewer: owner

* extraction is **model-neutral** (:mod:`catchup_core.exchange`): with no command configured the run
  pauses and prints requests for whichever assistant session the owner is running, then resumes
  with its answers. ``ARTHA_EXTRACTOR_CMD`` may select the bundled deterministic extractor;
  arbitrary model CLIs are refused because their tool capabilities cannot be enforced here.
  Provider, data classes and retention remain
  the owner's decision (§9.2, §10.1); answers are untrusted and validated by the core.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from catchup_core import core_items, ids, reminders, yaml_cache
from catchup_core.adapters import fetch_via_handler
from catchup_core.backup import atomic_bytes, database_bytes, encrypted_snapshot
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode, FailureClass
from catchup_core.coordinator import ConnectorFetch, ConnectorRecord, ContextLines, Deps
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider, system_keyring
from catchup_core.domain_pack import DomainPack, PackError, composite_pack, load_pack_file
from catchup_core.exchange import ExchangeExtractor
from catchup_core.incumbent import GuardedIncumbent
from catchup_core.ledger import FactView, KeyringSealedStore, Ledger, LedgerError, PermissionDenied
from catchup_core.legacy_state import LegacyStateAdapter
from catchup_core.policy import (
    ReleaseEvidenceGuard,
    authorize_source,
    evaluation_cohort,
    validate_local_authority,
    validate_release,
)
from catchup_core.session_lock import installation_fence, require_active_installation

__all__ = ["RuntimeConfig", "RuntimeConfigError", "build_deps", "command_extractor", "load_runtime_config",
           "local_root", "make_file_publisher"]

_PACKS = Path(__file__).parent / "packs"
_PROMPTS = Path(__file__).parent / 'prompts'


class RuntimeConfigError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(self, sources: Mapping[str, Mapping[str, Any]], subjects: list[str], viewer: str,
                 consent_required: frozenset[str] = frozenset(), cutover: bool = False,
                 extractor_cmd: str | None = None, domains: tuple[str, ...] = ("kids_school",),
                 policy: Mapping[str, Any] | None = None):
        self.sources = {k: dict(v) for k, v in sources.items()}
        self.subjects = subjects
        self.viewer = viewer
        self.consent_required = consent_required
        self.cutover = cutover
        self.extractor_cmd = extractor_cmd
        self.domains = tuple(domains)
        self.policy = dict(policy or {})
        self.subject_labels: dict[str, str] = {}


def local_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    base = env.get("ARTHA_LOCAL_DIR") or str(Path.home() / ".artha-local")
    root = Path(base).expanduser() / "catchup_core"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def load_runtime_config(path: Path | str) -> RuntimeConfig:
    try:
        data = yaml_cache.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeConfigError('config must be a mapping')
    if any(field in data and type(data[field]) is not bool for field in ('cutover', 'household_from_profile')):
        raise RuntimeConfigError('cutover and household_from_profile require literal booleans')
    for field, maximum in (('bootstrap_days', 3660), ('snapshot_retention_days', 365)):
        if field in data and (type(data[field]) is not int or not 1 <= data[field] <= maximum):
            raise RuntimeConfigError(f'{field} must be an integer in 1..{maximum}')
    if not isinstance(data.get('viewer', 'owner'), str) or not data.get('viewer', 'owner').strip():
        raise RuntimeConfigError('viewer needs a resolved identifier')
    if data.get('extractor_cmd') is not None and not isinstance(data['extractor_cmd'], str):
        raise RuntimeConfigError('extractor_cmd must be a command string')
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise RuntimeConfigError("config needs a non-empty 'sources' mapping")
    _validate_runtime_sources(sources)
    subjects = data.get("subjects", [] if data.get("household_from_profile") is True else None)
    if (not isinstance(subjects, list) or not all(isinstance(s, str) and s for s in subjects)
            or len(set(subjects)) != len(subjects)
            or (not subjects and data.get("household_from_profile") is not True)):
        raise RuntimeConfigError("config needs a non-empty 'subjects' list")
    required = data.get("consent_required", [])
    if not isinstance(required, list) or not all(isinstance(s, str) and s for s in required):
        raise RuntimeConfigError("'consent_required' must be a list of subject ids")
    unknown = sorted(set(required) - set(subjects))
    if unknown:
        raise RuntimeConfigError(f"'consent_required' names subjects not in 'subjects': {', '.join(unknown)}")
    cutover = data.get("cutover", False)
    extractor_cmd = data.get("extractor_cmd")
    cfg_domains = data.get("domains")
    if 'domains' in data and (not isinstance(cfg_domains, list) or not cfg_domains
            or any(not isinstance(domain, str) or not re.fullmatch('[a-z][a-z0-9_]*', domain) for domain in cfg_domains)
            or len(set(cfg_domains)) != len(cfg_domains)):
        raise RuntimeConfigError('domains must be a nonempty list of distinct Domain identifiers')
    domains = tuple(cfg_domains) if isinstance(cfg_domains, list) else ("kids_school",)
    return RuntimeConfig(sources, [str(s) for s in subjects], str(data.get("viewer", "owner")),
                         frozenset(required), cutover=cutover,
                         extractor_cmd=str(extractor_cmd) if extractor_cmd else None,
                         domains=domains, policy=data)


def _validate_runtime_sources(sources: Mapping[str, Any]) -> None:
    """Reject malformed source policy before authorization or Connector loading."""
    for name, spec in sources.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(spec, dict)
                or any(not isinstance(spec.get(field), str) or not spec[field].strip()
                       for field in ('connector', 'account_scope'))):
            raise RuntimeConfigError(f"source {name!r} needs 'connector' and 'account_scope'")
        if not isinstance(spec.get('kwargs', {}), dict):
            raise RuntimeConfigError('source kwargs must be a mapping')
        if 'required' in spec and type(spec['required']) is not bool:
            raise RuntimeConfigError('source required must be a boolean')
        source_subjects = spec.get('subjects')
        if 'subjects' in spec and (not isinstance(source_subjects, list) or not source_subjects
                or any(not isinstance(subject, str) or not subject.strip() for subject in source_subjects)
                or len(set(source_subjects)) != len(source_subjects)):
            raise RuntimeConfigError('source subjects must be a nonempty list of distinct identifiers')
        kinds = spec.get("calendar_kinds", {})
        if not isinstance(kinds, dict) or any(
                not isinstance(k, str) or not k or v not in ("school", "travel", "out_of_office", "other")
                for k, v in kinds.items()):
            raise RuntimeConfigError("calendar_kinds must map stable calendar IDs to declared calendar kinds")


def command_extractor(command: str | None, *, timeout: float = 120.0,
                      runner: Callable[..., Any] = subprocess.run) -> Callable[[str], str]:
    """Extractor backed by an external command; fails closed when unset or failing."""
    argv = shlex.split(command) if command else []

    def extract(prompt: str) -> str:
        if not argv:
            raise RuntimeError("no extractor command configured (ARTHA_EXTRACTOR_CMD)")
        script = Path(__file__).parent / 'extractor_cmd.py'
        allowed = ((len(argv) == 2 and (Path(__file__).parents[2] / argv[1]).resolve() == script.resolve())
                   or (len(argv) == 3 and argv[1:] == ['-m', 'catchup_core.extractor_cmd']))
        if not allowed:
            raise RuntimeError('extractor command has no enforced tool-less policy; use the assistant exchange')
        environment = {k: v for k, v in os.environ.items() if k in {'SYSTEMROOT', 'WINDIR', 'LANG'}}
        with tempfile.TemporaryDirectory(prefix='artha-extract-') as isolated:
            proc = runner([sys.executable, '-I', str(script)], input=prompt, capture_output=True, text=True,
                          timeout=timeout, check=False, env=environment, cwd=isolated)
        if proc.returncode != 0:
            raise RuntimeError(f"extractor exited {proc.returncode}")
        return str(proc.stdout)
    return extract


def _id_key(provider: KeyringKeyProvider) -> ids.HmacKey:
    version = provider.current_version()
    return ids.HmacKey(version, provider.key(version))


def _cursor_kwargs(scope: str, cursor: str, fetch_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """The handler argument carrying a committed cursor; ValueError/KeyError/TypeError when it cannot be trusted."""
    if len(cursor) > 1_000_000:
        raise ValueError('cursor too large')
    if scope == 'mailbox':
        return {'sync_cursor': cursor}
    envelope = json.loads(cursor)
    collections = envelope['collections']
    configured = fetch_kwargs.get('calendar_ids')
    known = (isinstance(configured, list) and set(collections) <= set(configured)
             if scope == 'configured_calendars' else True)
    if (set(envelope) != {'version', 'collections'} or type(envelope['version']) is not int
            or envelope['version'] != 1 or not isinstance(collections, dict) or not known
            or any(not isinstance(value, str) or not value for value in collections.values())):
        raise ValueError('invalid collection cursor')
    return {'sync_tokens': collections}


def build_fetchers(
    config: RuntimeConfig,
    since: str,
    handler_loader: Callable[[str], Any],
    auth_loader: Callable[[str], Mapping[str, Any]],
    required: Mapping[str, bool],
    ledger: Ledger | None = None,
) -> dict[str, Callable[[], ConnectorFetch]]:
    """One callable per pilot source; load/auth failures become *unavailable*, not crashes."""
    fetchers: dict[str, Callable[[], ConnectorFetch]] = {}
    for pack_name, spec in config.sources.items():
        def run(pack_name: str = pack_name, spec: Mapping[str, Any] = spec) -> ConnectorFetch:
            try:
                if str(spec.get("namespace", "personal")) != "personal":
                    raise PermissionError("Work sources cannot enter personal context")
                if ledger is None and config.consent_required:
                    raise PermissionDenied("fetch requires the current consent ledger")
                if ledger is not None:
                    authorize_source(ledger, spec.get("subjects", config.subjects), config.consent_required,
                                     f"{pack_name}:{spec['account_scope']}", config.viewer)
                    if str(spec.get("namespace", "personal")) != "personal":
                        raise PermissionError("Work sources cannot enter personal context")
                handler = handler_loader(str(spec["connector"]))
                auth = auth_loader(str(spec["connector"]))
            except Exception:
                from catchup_core.contracts import FailureClass  # noqa: PLC0415
                return ConnectorFetch(pack_name, str(spec["account_scope"]), (), CoverageStatus.UNAVAILABLE,
                                      FailureClass.AUTH, ("connector could not be loaded or authorised",),
                                      required.get(pack_name, True))
            start = since
            if ledger is not None:
                last = next((r["completed_at"] for r in ledger.coverage_rows()
                             if r["connector"] == pack_name and r["account_scope"] == spec["account_scope"]), None)
                if last:
                    start = (datetime.fromisoformat(last.replace("Z", "+00:00")) - timedelta(hours=24)).isoformat()
            fetch_kwargs = dict(spec.get('kwargs') or {})
            sync_changes = fetch_kwargs.get('sync_changes') is True
            sync_scope = getattr(handler, 'SYNC_TOKEN_SCOPE', None) or (
                'configured_calendars' if spec['connector'] == 'google_calendar' else None)
            if sync_scope is not None and sync_changes and ledger is not None:
                cursor = ledger.get_cursor(pack_name, str(spec['account_scope']))
                if cursor:
                    try:
                        fetch_kwargs.update(_cursor_kwargs(sync_scope, cursor, fetch_kwargs))
                    except (ValueError, KeyError, TypeError):
                        from catchup_core.contracts import FailureClass  # noqa: PLC0415
                        return ConnectorFetch(pack_name, str(spec['account_scope']), (), CoverageStatus.DEGRADED,
                            FailureClass.PROVIDER, ('collection cursor requires explicit reset',),
                            required.get(pack_name, True))
            return fetch_via_handler(
                pack_name, str(spec["account_scope"]), handler.fetch, since=start, auth_context=auth,
                fetch_kwargs=fetch_kwargs, required=required.get(pack_name, True),
                calendar_kinds=spec.get("calendar_kinds"))
        fetchers[pack_name] = run
    return fetchers


def _default_loaders() -> tuple[Callable[[str], Any], Callable[[str], Mapping[str, Any]]]:
    # Loaded dynamically: the incumbent pipeline is outside the strict-typed surface.
    pipeline = importlib.import_module("pipeline")
    load_auth_context = importlib.import_module("lib.auth").load_auth_context

    cfg = pipeline.load_connectors_config()
    by_name = {c["name"]: c for c in pipeline._normalize_connectors(cfg)}

    def handler(name: str) -> Any:
        return pipeline._load_handler(by_name[name]["fetch"]["handler"])

    def auth(name: str) -> Mapping[str, Any]:
        return dict(load_auth_context(by_name[name]))
    return handler, auth


def _implementation_fingerprint(root: Path) -> str:
    """Resume generation includes registered providers and controls as well as the core."""
    from catchup_core.capabilities import CAPABILITIES  # noqa: PLC0415
    paths = {path for capability in CAPABILITIES for path in capability.modules if path.startswith('scripts/')}
    paths.update(p.relative_to(root).as_posix() for p in (root / 'scripts/catchup_core').glob('*.py'))
    paths.update(p.relative_to(root).as_posix() for p in (root / 'scripts/catchup_core/migrations').glob('*.sql'))
    digest = hashlib.sha256()
    for name in sorted(paths):
        path = root / name
        digest.update(name.encode() + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b'missing')
    return digest.hexdigest()


def build_deps(
    request: CatchupRequest,
    *,
    repo_root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    keyring_backend: Any = None,
    handler_loader: Callable[[str], Any] | None = None,
    auth_loader: Callable[[str], Mapping[str, Any]] | None = None,
    now: Callable[[], datetime] | None = None,
    inspection: bool = False,
    mutation: bool = False,
    safety_control: bool = False,
    configuration_control: bool = False,
) -> Deps:
    env = os.environ if environ is None else environ
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    base = Path(env.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()
    require_active_installation(root, base)
    if base.resolve().is_relative_to(root.resolve()):
        raise RuntimeConfigError('private runtime storage must be outside the repository')
    with installation_fence(base):
        deps = _build_deps(request, repo_root=root, environ=env, keyring_backend=keyring_backend,
                           handler_loader=handler_loader, auth_loader=auth_loader, now=now,
                           inspection=inspection, mutation=mutation, safety_control=safety_control,
                           configuration_control=configuration_control)
    deps.maintenance = lambda: installation_fence(base)
    return deps


def _build_deps(  # noqa: PLR0912, PLR0915 - explicit environment boundary and dependency wiring
    request: CatchupRequest,
    *,
    repo_root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    keyring_backend: Any = None,
    handler_loader: Callable[[str], Any] | None = None,
    auth_loader: Callable[[str], Mapping[str, Any]] | None = None,
    now: Callable[[], datetime] | None = None,
    inspection: bool = False,
    mutation: bool = False,
    safety_control: bool = False,
    configuration_control: bool = False,
) -> Deps:
    env = os.environ if environ is None else environ
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    require_active_installation(root, Path(env.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local'))
    config = load_runtime_config(root / "config" / "catchup_core.yaml")
    local = local_root(env)
    from catchup_core.resources import AdmissionStore, ResourcePolicy  # noqa: PLC0415
    resource_policy = None
    if 'resource_limits' in config.policy:
        try:
            resource_policy = ResourcePolicy.from_mapping(config.policy['resource_limits'])
        except ValueError:
            raise RuntimeConfigError('resource_limits requires all explicit bounded admission limits') from None
    if local.resolve().is_relative_to(root.resolve()):
        raise RuntimeConfigError("private runtime storage must be outside the repository")
    if request.mode.value == "shadow":
        local = local / "shadow"

    domains = request.pilot_domains or config.domains or ("kids_school",)
    pack = _load_packs(domains)
    ext_cmd = env.get('ARTHA_EXTRACTOR_CMD') or config.extractor_cmd
    cohort = evaluation_cohort(config.policy, domains, extractor_command=ext_cmd, workspace=root)
    effective = replace(request, mode=ExecutionMode.SHADOW) if inspection and not mutation else request
    if safety_control:
        validate_local_authority(request, config.policy, viewer=config.viewer)
    else:
        if effective.mode is ExecutionMode.AUTHORITATIVE and env.get('ARTHA_CUTOVER') == '0':
            raise PermissionDenied('authoritative cutover is disabled by the runtime environment; use --shadow')
        validate_release(effective, config.policy, viewer=config.viewer, domains=domains, cohort=cohort)
    source_class = {**{name: str(spec.get("class", name)) for name, spec in config.sources.items()},
                    "capture": "parent_note", "documents": "official_document"}
    validate_source_classes(pack, source_class)
    if inspection and not (local / "ledger.db").is_file():
        raise RuntimeConfigError("no Catch-up recorded here yet; start with catchup --shadow")

    enroll = not inspection and not (local / 'ledger.db').exists()
    cipher_provider = KeyringKeyProvider("artha-ledger", backend=keyring_backend, create=enroll)
    id_provider = KeyringKeyProvider("artha-ledger-ids", backend=keyring_backend, create=enroll)
    cipher_provider.key(cipher_provider.current_version())
    cipher = EnvelopeCipher(cipher_provider)
    id_key = _id_key(id_provider)
    release_guard = None
    if not safety_control and effective.mode is ExecutionMode.AUTHORITATIVE:
        release_guard = ReleaseEvidenceGuard(config.policy, cipher, cohort)
        release_guard.validate()
    if config.policy.get("household_from_profile") is True and (not safety_control or configuration_control):
        _bind_household(config, root, id_key, (now or (lambda: datetime.now(UTC)))().astimezone(pack.timezone).date(),
                        enroll=not inspection and not configuration_control)
    if keyring_backend is None:
        keyring_backend = system_keyring()
    ledger, blobs = _open_ledger(local, config, request, cipher, id_key, keyring_backend,
                                 read_only=(inspection and not mutation) or configuration_control)

    try:
        if not safety_control and effective.mode is ExecutionMode.AUTHORITATIVE:
            ledger.ensure_authority_host(request.host)
        if handler_loader is None or auth_loader is None:
            handler_loader, auth_loader = _default_loaders()
        required = {s.connector: s.required for s in pack.sources}
        clock: Callable[[], datetime] = now or (lambda: datetime.now(UTC))
        def now_fn() -> datetime:
            return clock().astimezone(pack.timezone)
        since = (now_fn() - timedelta(days=int(config.policy.get("bootstrap_days", 30)))).isoformat()
        fetchers = build_fetchers(config, since, handler_loader, auth_loader, required, ledger)
        fetchers["capture"] = _capture_fetcher(local, cipher, config.viewer)
        from catchup_core.documents import fetch_documents  # noqa: PLC0415
        fetchers["documents"] = lambda: fetch_documents(ledger, config.viewer)
        bundle_fetchers = _bundle_fetchers(config, ledger, keyring_backend, required, clock)
        fetchers.update(bundle_fetchers)
        def curated_reader(name: str) -> str:
            from catchup_core.curated import read_authorized_state  # noqa: PLC0415
            return read_authorized_state(root / 'state', name, ledger, config.viewer, config.subjects)
        def context_version() -> str:
            # Hash ciphertext in place; no decryption, private content, or filenames enter the journal.
            digest = hashlib.sha256()
            for path in sorted((root / 'state').iterdir()):
                if path.is_file() and path.name.endswith(('.md', '.md.age', '.yaml', '.yml')):
                    digest.update(ids.content_hmac(id_key, path.name).encode())
                    digest.update(ids.content_hmac(id_key, path.read_bytes().hex()).encode())
            return digest.hexdigest()
        def finalize(catchup_id: str) -> None:
            curated = {p.name: p.read_bytes() for p in (root / "state").iterdir()
                       if p.is_file() and p.name.endswith((".md", ".md.age", ".yaml", ".yml"))}
            def unchanged() -> bool:
                return all((root / "state" / name).read_bytes() == data for name, data in curated.items())
            encrypted_snapshot(local / "snapshots" / f"{catchup_id}.ae1", local / "ledger.db", cipher,
                               blobs=local / "blobs", curated=curated,
                               action_db=action_database_path(root) if request.mode.value == "authoritative" else None,
                               idempotent=True, guard=unchanged)
            _prune_snapshots(local / "snapshots", now_fn(), int(config.policy.get("snapshot_retention_days", 30)))
            atomic_bytes(local / "health.json", json.dumps({"last_catch_up": now_fn().isoformat(),
                "catchup_id": catchup_id, "mode": request.mode.value}).encode())
        def input_version() -> str:
            digest = hashlib.sha256()
            digest.update(json.dumps(config.policy, sort_keys=True).encode() + str(ext_cmd).encode())
            paths = [root / 'config' / name for name in (
                'catchup_core.yaml', 'user_profile.yaml', 'connectors.yaml', 'actions.yaml',
            )]
            paths.extend(_PACKS / f'{domain}.yaml' for domain in domains)
            paths.extend(sorted(_PROMPTS.glob('*.md')))
            for path in paths:
                digest.update(path.name.encode() + b'\0')
                digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b'missing')
            digest.update(_implementation_fingerprint(Path(__file__).resolve().parents[2]).encode())
            return ids.content_hmac(id_key, digest.hexdigest())
        frozen_inputs = input_version()
        def validate_inputs() -> None:
            try:
                current = input_version()
            except OSError:
                raise LedgerError('runtime inputs unavailable; start a new Catch-up') from None
            if current != frozen_inputs:
                raise LedgerError('runtime inputs changed; start a new Catch-up')
            if release_guard is not None:
                release_guard.validate()
        return Deps(
            mode=request.mode,
            ledger=ledger, pack=pack, blobs=blobs, id_key=id_key,
            fetchers=fetchers,
            extractor=(command_extractor(ext_cmd) if ext_cmd else ExchangeExtractor()),
            admissions=(AdmissionStore(ledger, resource_policy, _spend_seal(env, request.mode.value))
                        if resource_policy is not None else None),
            require_resource_policy=True,
            model_log=_model_log_writer(local, now_fn),
            metrics=_metrics_writer(local, now_fn),
            trace=_trace_writer(local, now_fn),
            provider_label=(f"command:{ext_cmd.split()[0].rsplit('/', 1)[-1]}" if ext_cmd else "session-assistant"),
            subjects=config.subjects, viewer=config.viewer, now=now_fn,
            source_class=source_class,
            incumbent=GuardedIncumbent(LegacyStateAdapter(root / "state", domains, now_fn(), reader=curated_reader)),
            publisher=make_file_publisher(local, now_fn, cipher=cipher),
            preflight=lambda: _runtime_preflight(ledger),
            inspection_preflight=lambda: _storage_preflight(ledger),
            finalizer=finalize,
            items_publisher=lambda alerts: core_items.publish(
                root / "state" / "core_open_items.md", core_items.render(alerts, config.subject_labels)),
            reminder_proposer=reminder_hook(lambda: _action_executor(root), config.subject_labels),
            goal_lines=lambda: _goal_lines(root / "state", reader=curated_reader, today=now_fn().date(),
                                           ledger=ledger, viewer=config.viewer, now=now_fn(),
                                           required_sources=tuple(s.connector for s in pack.sources if s.required)),
            goal_domains=lambda: _goal_domains(root / "state", reader=curated_reader),
            plan_lines=lambda: _plan_lines(root / "state", reader=curated_reader, today=now_fn().date(),
                                           ledger=ledger, viewer=config.viewer),
            responsibility_lines=lambda: _responsibility_lines(
                root / "state", reader=curated_reader, today=now_fn().date(), now=now_fn(),
                ledger=ledger, viewer=config.viewer),
            coordination_lines=lambda: _coordination_lines(
                root / "state", reader=curated_reader, today=now_fn().date(),
                viewer=config.viewer, primary=config.viewer, ledger=ledger),
            input_version=frozen_inputs,
            evaluation_cohort=cohort,
            extractor_identity_matched=bool(ext_cmd),
            validate_inputs=validate_inputs,
            subjects_for=lambda connector, text: _subjects_for(config, connector, text),
            access_subjects_for=lambda connector, text: _access_subjects_for(config, connector, text),
            subject_labels=config.subject_labels,
            scope_subjects={"capture": [config.viewer],
                            **{name: spec.get("subjects", config.subjects) for name, spec in config.sources.items()}},
            expected_scopes={**{name: spec['account_scope'] for name, spec in config.sources.items()},
                             'capture': config.viewer, 'documents': 'manual',
                             **{name: config.policy['bundles'][name]['account_scope'] for name in bundle_fetchers}},
            context_version=context_version,
        )
    except BaseException:
        ledger.close()
        raise


def inspection_freshness(ledger: Ledger, now: datetime) -> dict[str, Any]:
    """Freshness at a user-started entry point; never advance the verification clock on a read."""
    row = ledger.conn.execute("SELECT MAX(published_at) FROM briefings").fetchone()
    stamp = row[0] if row else None
    try:
        if not isinstance(stamp, str):
            raise ValueError("no completed Catch-up")
        age = max(0.0, (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 3600)
    except (ValueError, TypeError, AttributeError):
        age = None
    return {"last_catch_up": stamp, "age_hours": round(age, 2) if age is not None else None,
            "unseen_since": stamp or "unknown", "stale": age is None or age > 48,
            "sources": [{"connector": r["connector"], "status": r["status"], "last_verified": r["completed_at"]}
                        for r in ledger.visible_coverage(ledger._owner)]}


def _runtime_preflight(ledger: Ledger, *, footprint: Callable[[], Any] | None = None) -> None:
    """Session-start checks before interpretation or dispatch; unavailable inventory never means clear."""
    _storage_preflight(ledger)
    check = footprint or importlib.import_module("preflight.footprint_checks").check_background_footprint
    outcome = check()
    if not outcome.passed:
        raise RuntimeConfigError("runtime preflight: background footprint failed or unverified; " + outcome.message)


def _storage_preflight(ledger: Ledger) -> None:
    """Read-only evidence inspection depends on storage/privacy, never scheduler availability."""
    if not ledger.integrity_check() or not ledger.check_restore():
        raise RuntimeConfigError("runtime preflight: ledger integrity or privacy history failed")
    if os.name != "nt" and ledger._path.parent.stat().st_mode & 0o077:
        raise RuntimeConfigError("runtime preflight: private storage directory permissions are too broad")


def _bind_household(config: RuntimeConfig, root: Path, key: ids.HmacKey, today: Any, *, enroll: bool = True) -> None:
    from catchup_core.household import members  # noqa: PLC0415
    try:
        profile_path = root / "config/user_profile.yaml"
        profile = yaml_cache.safe_load(profile_path.read_text(encoding="utf-8"))
        people = members(profile, key, config.viewer, today)
        # Preserve the initial keyed IDs in the authored profile. Later name/DOB edits
        # or key rotation must not silently turn old adult data into an unknown minor.
        family = profile["family"]
        records = ([family["spouse"]] if family.get("spouse", {}).get("name")
                   and family["spouse"].get("enabled", True) else []) + family.get("children", [])
        changed = False
        for record, person in zip(records, people[1:], strict=True):
            if not record.get("id"):
                record["id"] = person.subject_id
                changed = True
        if changed and not enroll:
            raise RuntimeConfigError("household IDs need explicit initialization before read-only inspection")
        if changed:
            atomic_bytes(profile_path, yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).encode())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeConfigError("household identity could not be resolved from the profile") from exc
    config.subjects = [person.subject_id for person in people]
    config.consent_required = frozenset(person.subject_id for person in people if person.consent_required)
    config.subject_labels = {person.subject_id: person.name for person in people}
    config.policy["subject_aliases"] = {person.subject_id: list(person.aliases) for person in people}
    config.policy["withheld_names"] = sorted({alias for person in people if person.child and person.consent_required
                                              for alias in person.aliases})
    config.policy["resolved_subjects"] = config.subjects
    config.policy["consent_required"] = sorted(config.consent_required)


def action_database_path(workspace: Path) -> Path:
    queue = importlib.import_module("action_queue")
    return Path(queue.ActionQueue._resolve_db_path(workspace))


def _subjects_for(config: RuntimeConfig, connector: str, text: str) -> list[str]:
    """Resolve one subject from trusted source scope or explicit aliases, never list order."""
    candidates = config.sources.get(connector, {}).get("subjects", config.subjects)
    # A message that names an adult child whose consent is not recorded is that person's data, whatever label
    # or scope it arrived under: it is withheld before any extraction prompt is built.
    if any(re.search(r"(?<!\w)" + re.escape(str(name)) + r"(?!\w)", text, re.IGNORECASE)
           for name in config.policy.get("withheld_names", ())):
        return []
    if len(candidates) == 1:
        return [str(candidates[0])]
    aliases = config.policy.get("subject_aliases", {})
    named = [str(subject) for subject in candidates if any(
        re.search(r"(?<!\w)" + re.escape(str(alias)) + r"(?!\w)", text, re.IGNORECASE)
        for alias in (subject, *aliases.get(subject, [])))]
    # Multi-person evidence needs a scoped excerpt or explicit review before attribution.
    return named if len(named) == 1 else []


def _access_subjects_for(config: RuntimeConfig, connector: str, text: str) -> list[str]:
    """Strictest subject set for access bookkeeping only; extraction still needs ``_subjects_for`` to name one person.

    An event naming nobody (or several candidates) is recorded against every candidate, so a source is hidden when any
    candidate needs consent that is not recorded. A withheld name keeps the empty result."""
    resolved = _subjects_for(config, connector, text)
    if resolved:
        return resolved
    if any(re.search(r"(?<!\w)" + re.escape(str(name)) + r"(?!\w)", text, re.IGNORECASE)
           for name in config.policy.get("withheld_names", ())):
        return []
    return [str(subject) for subject in config.sources.get(connector, {}).get("subjects", config.subjects)]


def _open_ledger(local: Path, config: RuntimeConfig, request: CatchupRequest, cipher: EnvelopeCipher,
                 id_key: ids.HmacKey, keyring_backend: Any, *,
                 read_only: bool = False) -> tuple[Ledger, EncryptedBlobStore]:
    from catchup_core.session_lock import session_lock  # noqa: PLC0415
    with session_lock(local / "ledger.session.lock"):
        is_new = not (local / "ledger.db").exists()
        ledger = Ledger(local / "ledger.db", id_key=id_key, cipher=cipher, owner_id=config.viewer,
                        consent_subjects=config.consent_required,
                        known_subjects=frozenset((*config.subjects, config.viewer)),
                        sealed=KeyringSealedStore(keyring_backend, request.mode.value, cipher=cipher)
                        ).open(read_only=read_only)
        try:
            if read_only:
                if ledger.schema_version() != max(n for n, _ in Ledger.available_migrations()):
                    raise RuntimeConfigError("ledger upgrade required before read-only inspection")
                if ledger.conn.execute("SELECT 1 FROM deletion_journal WHERE state<>'receipted'").fetchone():
                    raise RuntimeConfigError("pending erasure recovery requires a local maintenance session")
            else:
                ledger.migrate(backup=_backup_for(local, cipher=cipher))
            if is_new:
                ledger.seal_privacy()
            if not ledger.check_restore():
                raise RuntimeConfigError("ledger is older than the sealed deletion checkpoint; replay tombstones first")
            if not read_only:
                ledger.protect_legacy_values()
            blobs = EncryptedBlobStore(local / "blobs", cipher, id_key)
            ledger._blob_deleter = blobs.delete
            ledger._artifact_deleter = lambda cids: _purge_archives(local, ledger, cids)
            ledger._replay_deleter = lambda fids: _purge_replay(local, fids)
            if not read_only:
                ledger.resume_pending_forgets()
                ledger.resume_pending_cancellations()
        except BaseException:
            ledger.close()
            raise
    return ledger, blobs


def _purge_archives(local: Path, ledger: Ledger, catchup_ids: Sequence[str]) -> None:
    for cid in catchup_ids:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
            raise RuntimeConfigError("invalid archive identity in deletion journal")
        for path in (local / "briefings").glob(f"*-{cid}.v*.ae1"):
            path.unlink(missing_ok=True)
        ledger.conn.execute("UPDATE briefings SET status='superseded' WHERE catchup_id=?", (cid,))
    # Captured notes are source evidence too, not a permanent bypass around forget.
    for row in ledger.conn.execute("SELECT upstream_id FROM tombstones WHERE connector='capture'"):
        if re.fullmatch(r"cap_[A-Za-z0-9]+", row[0]):
            (local / "inbox" / f"{row[0]}.ae1").unlink(missing_ok=True)
    for row in ledger.conn.execute("SELECT upstream_id FROM tombstones WHERE connector='documents'"):
        document = row[0].split(":", 1)[0]
        if re.fullmatch(r"doc_[a-f0-9]+", document):
            (local / "documents" / f"{document}.ae1").unlink(missing_ok=True)


def _prune_snapshots(directory: Path, now: datetime, retention_days: int) -> None:
    if not 1 <= retention_days <= 365:
        raise RuntimeConfigError("snapshot retention must be between 1 and 365 days")
    snapshots = sorted(directory.glob("*.ae1"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = (now - timedelta(days=retention_days)).timestamp()
    for path in snapshots[1:]:
        if path.stat().st_mtime < cutoff:
            path.unlink()


def validate_source_classes(pack: DomainPack, source_class: Mapping[str, str]) -> None:
    """A source whose class no predicate ranks would turn every Fact it yields into UNKNOWN."""
    ranked = {token for pred in pack.predicates.values() for token in pred.authority}
    for source in pack.sources:
        klass = source_class.get(source.connector, source.connector)
        if source.required and klass not in ranked:
            raise RuntimeConfigError(
                f"source {source.connector!r} has class {klass!r} which no predicate lists as an authority; "
                f"set 'class:' on it in config/catchup_core.yaml (one of: {', '.join(sorted(ranked))})")


def _bundle_fetchers(config: RuntimeConfig, ledger: Ledger, keyring_backend: Any, required: Mapping[str, bool],
                     clock: Callable[[], datetime]) -> dict[str, Callable[[], ConnectorFetch]]:
    """A source configured under ``bundles:`` is read from hand-run ingest bundles instead of a live Connector."""
    from catchup_core.bundles import import_bundles, load_producer_key  # noqa: PLC0415
    specs = config.policy.get("bundles") or {}
    if not isinstance(specs, dict):
        raise RuntimeConfigError("'bundles' must map a source name to its bundle settings")
    out: dict[str, Callable[[], ConnectorFetch]] = {}
    for name, spec in specs.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(spec, dict)
                or not isinstance(spec.get('account_scope'), str) or not spec['account_scope'].strip()):
            raise RuntimeConfigError('bundle sources need resolved names and account scopes')
        def run(name: str = str(name), spec: Mapping[str, Any] = spec) -> ConnectorFetch:
            if spec.get("namespace", "personal") != "personal":
                raise PermissionDenied("Work bundles cannot enter personal context")
            authorize_source(ledger, config.sources.get(name, {}).get("subjects", config.subjects),
                             ledger._consent_subjects, f"{name}:{spec['account_scope']}", config.viewer)
            key = load_producer_key(str(spec["producer"]), keyring_backend)
            keys = {key.producer: key} if key is not None else {}
            row = ledger.conn.execute("SELECT MAX(generation) FROM authority_generation").fetchone()
            result = import_bundles(
                Path(str(spec["dir"])).expanduser(), keys, name=name, namespace=str(spec.get("namespace", "personal")),
                recipient=str(spec["recipient"]), account_scope=str(spec["account_scope"]),
                authority_generation=int(row[0] or 1), now=clock(), required=required.get(name, True),
                max_age_hours=float(spec.get("max_age_hours", 48)))
            if key is None:
                return ConnectorFetch(name, str(spec["account_scope"]), (), CoverageStatus.UNAVAILABLE,
                                      FailureClass.AUTH, ("producer is not enrolled on this machine",),
                                      required.get(name, True))
            return result.fetch
        out[str(name)] = run
    return out


def _spend_seal(env: Mapping[str, str], mode: str) -> Any:
    """Spend seal in a private folder outside every snapshot; the recovery registry classifies it on creation."""
    from catchup_core import recovery_registry  # noqa: PLC0415
    from catchup_core.resources import FileSpendSeal  # noqa: PLC0415
    base = Path(env.get("ARTHA_LOCAL_DIR") or Path.home() / ".artha-local").expanduser()
    return FileSpendSeal(base / "spend-seal" / f"{mode}.json",
                         enroll=lambda: recovery_registry.enroll_spend_seal("personal", mode=mode),
                         require_existing=lambda: recovery_registry.spend_seal_required("personal", mode=mode))


def _model_log_writer(local: Path, now: Callable[[], datetime]) -> Callable[[dict[str, str]], None]:
    """Append-only monthly JSONL of *who may have seen what* (provider, data class, purpose), no content."""
    def write(entry: dict[str, str]) -> None:
        path = local / "model-requests" / f"{now():%Y-%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return write


def _metrics_writer(local: Path, now: Callable[[], datetime]) -> Callable[[dict[str, object]], None]:
    """Append-only monthly JSONL of run performance (no content), private to the owner."""
    def write(entry: dict[str, object]) -> None:
        path = local / "metrics" / f"{now():%Y-%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return write


def _trace_writer(local: Path, now: Callable[[], datetime]) -> Callable[[dict[str, object]], None]:
    """Append-only monthly JSONL of OTLP-shaped traces (no content), private to the owner."""
    from catchup_core import CORE_CONTRACT_VERSION  # noqa: PLC0415
    from catchup_core.metrics import to_trace  # noqa: PLC0415

    def write(entry: dict[str, object]) -> None:
        path = local / "traces" / f"{now():%Y-%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(to_trace(entry, version=CORE_CONTRACT_VERSION), sort_keys=True) + "\n")
    return write


def _purge_replay(local: Path, feedback_ids: Sequence[str]) -> None:
    for fid in feedback_ids:
        if re.fullmatch(r"fbk_[A-Za-z0-9]+", fid):
            (local / "replay" / f"{fid}.ae1").unlink(missing_ok=True)


def _load_packs(domains: tuple[str, ...]) -> DomainPack:
    loaded: list[DomainPack] = []
    for d in domains:
        p_path = _PACKS / f"{d}.yaml"
        if p_path.is_file():
            try:
                loaded.append(load_pack_file(p_path))
            except PackError as exc:
                raise RuntimeConfigError(str(exc)) from exc
    if not loaded:
        raise RuntimeConfigError(f"no valid domain packs found for: {domains}")
    if len(loaded) != len(domains):
        raise RuntimeConfigError("one or more requested Domain packs are unavailable")
    try:
        return composite_pack(loaded)
    except PackError as exc:
        raise RuntimeConfigError(str(exc)) from exc


def _capture_fetcher(local: Path, cipher: EnvelopeCipher, viewer: str) -> Callable[[], ConnectorFetch]:
    def fetch() -> ConnectorFetch:
        records = []
        for path in sorted((local / "inbox").glob("cap_*.ae1")):
            data = json.loads(cipher.decrypt(path.read_bytes(), aad=path.stem.encode()))
            if data["actor"] != viewer or data["id"] != path.stem:
                raise RuntimeConfigError("captured note identity does not match this viewer")
            records.append(ConnectorRecord(path.stem, data["text"], occurred_at=data["observed_at"]))
        return ConnectorFetch("capture", viewer, tuple(records), required=False)
    return fetch


def _completion_checker(ledger: Ledger | None, viewer: str,
                        dependencies: dict[str, str] | None = None,
                        linked_facts: Mapping[str, FactView] | None = None) -> Callable[[Any, str], bool]:
    from catchup_core.curated import completion_fact  # noqa: PLC0415
    def verified(reference: Any, key: str) -> bool:
        fact = completion_fact(ledger, viewer, reference, key) if ledger else None
        fact = fact or (linked_facts or {}).get(key)
        if fact is not None and dependencies is not None:
            dependencies[fact.fact_id] = fact.recorded_at
        return fact is not None
    return verified


def _active_goals(state: Path, reader: Callable[[str], str] | None) -> list[dict[str, Any]]:
    from catchup_core.curated import read_state, records  # noqa: PLC0415
    goals = records(reader("goals") if reader else read_state(state, "goals"), "goals")
    return [g for g in goals if g.get("status") == "active"]


def _plan_lines(state: Path, *, reader: Callable[[str], str] | None = None, today: date,
                ledger: Ledger | None = None, viewer: str = "owner") -> ContextLines:
    """Read plans once; interpretation and coverage failures stay visible."""
    from catchup_core.curated import goal_open_items, open_item_links  # noqa: PLC0415
    from catchup_core.plans import evaluate_plans, plan_lines  # noqa: PLC0415
    dependencies: dict[str, str] = {}
    goals = _active_goals(state, reader)
    linked_states, linked_facts = open_item_links(ledger, viewer, goals,
        goal_open_items(state, ledger, viewer, goals)) if ledger else ({}, {})
    states = evaluate_plans(goals, today, linked_items=linked_states,
                           verify=_completion_checker(ledger, viewer, dependencies, linked_facts))
    return ContextLines(plan_lines(states), ("Goal plans need review; coverage is incomplete",)
                        if any(s.state == "invalid" for s in states) else (), dependencies,
                        commitment_count=sum(s.state != 'done' for s in states))


def _responsibility_lines(state: Path, *, reader: Callable[[str], str] | None = None, today: date,
                          ledger: Ledger | None = None, viewer: str = "owner",
                          now: datetime | None = None) -> ContextLines:
    """Standing responsibilities that need attention now, with explicit coverage gaps."""
    from catchup_core.curated import read_state, records  # noqa: PLC0415
    from catchup_core.responsibilities import evaluate_responsibilities, responsibility_lines  # noqa: PLC0415
    text = reader("responsibilities") if reader else read_state(state, "responsibilities")
    dependencies: dict[str, str] = {}
    instant = now or datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=UTC)
    check_source, check_stop = _responsibility_checkers(ledger, viewer, instant, dependencies)
    authored = records(text, "responsibilities")
    states = evaluate_responsibilities(authored, today,
        verify=_completion_checker(ledger, viewer, dependencies), check_source=check_source, check_stop=check_stop)
    lines = responsibility_lines(states)
    citations = {}
    for record in authored:
        stop = record.get('stop_condition')
        if not isinstance(stop, dict):
            continue
        fid = str(stop.get('fact', ''))[5:].partition('@')[0]
        if fid in dependencies:
            citations.update({i: (fid,) for i, line in enumerate(lines) if line.startswith(f"{record['id']}: ")})
    return ContextLines(lines, ("Responsibilities need review; coverage is incomplete",)
                        if any(s.state in {"invalid", "unverified"} for s in states) else (), dependencies,
                        citations, commitment_count=sum(s.state in {'due', 'invalid', 'unverified'} for s in states))


def _responsibility_checkers(ledger: Ledger | None, viewer: str, now: datetime, dependencies: dict[str, str],
                            ) -> tuple[Callable[[str, float], bool],
                                       Callable[[Mapping[str, Any], float], bool | None]]:
    coverage = {(r['connector'], r['account_scope']): r for r in ledger.visible_coverage(viewer)} if ledger else {}
    pending = {(r['connector'], r['account_scope']) for sid in ledger.unresolved_signals()
               if (r := ledger.signal_row(sid)) is not None} if ledger else set()
    def fresh(stamp: str, hours: float) -> bool:
        try:
            observed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        except ValueError:
            return False
        age = (now - (observed if observed.tzinfo else observed.replace(tzinfo=UTC))).total_seconds() / 3600
        return 0 <= age <= hours
    def source(scope: str, hours: float) -> bool:
        connector, _, account = scope.partition(':')
        row = coverage.get((connector, account))
        if row is None or row['status'] != 'complete' or not row['completed_at'] or (connector, account) in pending:
            return False
        return fresh(row['completed_at'], hours)
    def stop(condition: Mapping[str, Any], hours: float) -> bool | None:
        from catchup_core.ledger import LedgerError  # noqa: PLC0415
        if ledger is None:
            return None
        fid, _, version = str(condition['fact'])[5:].partition('@')
        try:
            fact = ledger.get_fact(fid, viewer)
        except LedgerError:
            return None
        if (fact.recorded_at != version or fact.status != 'current' or fact.validity_unknown
                or not fresh(fact.observed_at, hours)
                or fact.provenance_class not in {'verified_source', 'user_confirmed'}
                or (fact.valid_from is not None and date.fromisoformat(fact.valid_from[:10]) > now.date())
                or (fact.valid_to is not None and date.fromisoformat(fact.valid_to[:10]) <= now.date())):
            return None
        dependencies[fact.fact_id] = fact.recorded_at
        return json.dumps(fact.value, sort_keys=True, allow_nan=False) == json.dumps(
            condition['equals'], sort_keys=True, allow_nan=False)
    return source, stop


def _coordination_lines(state: Path, *, reader: Callable[[str], str] | None = None, today: date,
                        viewer: str, primary: str = "owner", ledger: Ledger | None = None) -> ContextLines:
    """Shared obligations filtered using the fixed primary identity, never the current viewer as primary."""
    from catchup_core.coordination import coordination_lines, evaluate_tasks  # noqa: PLC0415
    from catchup_core.curated import read_state, records  # noqa: PLC0415
    text = reader("household_tasks") if reader else read_state(state, "household_tasks")
    dependencies: dict[str, str] = {}
    tasks = evaluate_tasks(records(text, "household_tasks"), today, viewer=viewer, primary=primary,
                           verify=_completion_checker(ledger, viewer, dependencies))
    return ContextLines(coordination_lines(tasks), ("Household tasks deferred; coverage is incomplete",)
                        if any(s.task_id == "deferred" for s in tasks) else (), dependencies)


def _goal_domains(state: Path, *, reader: Callable[[str], str] | None = None) -> set[str]:
    """Categories of active Goals, lower-cased; unreadable Goals simply contribute nothing."""
    try:
        return {str(g["category"]).lower() for g in _active_goals(state, reader) if g.get("category")}
    except (OSError, RuntimeError, ValueError):
        return set()


def _authored_next_action(goal: Mapping[str, Any], today: date | None) -> tuple[str, list[str]]:
    gaps = []
    authored = goal.get('next_action')
    if authored is not None and not isinstance(authored, str):
        action = 'next action needs review'
        gaps.append('Goal next action needs review')
    else:
        action = (authored or '').strip() or 'next action unknown'
    due = goal.get('next_action_date')
    if today is not None and due is not None:
        try:
            if not isinstance(due, str | date) or isinstance(due, datetime):
                raise ValueError('next action requires a date')
            days = (date.fromisoformat(str(due)) - today).days
            action += (f" (overdue by {-days} day{'s' if days != -1 else ''})" if days < 0
                       else ' (due today)' if days == 0 else f" (due in {days} day{'s' if days != 1 else ''})")
        except ValueError:
            action += ' (date unreadable)'
            gaps.append('Goal next action date needs review')
    return action, gaps


def _action_executor(root: Path) -> Any:
    legacy = importlib.import_module("action_executor")  # the untyped legacy action layer lives beside the core
    return legacy.ActionExecutor(root)


def reminder_hook(make_executor: Callable[[], Any], labels: Mapping[str, str]) -> Callable[[Sequence[Any]], None]:
    """Queue reminder proposals; the action layer's failure never fails a Catch-up whose briefing is already built."""
    def hook(alerts: Sequence[Any]) -> None:
        try:
            reminders.propose(alerts, labels, make_executor().propose)
        except Exception:  # proposals are best effort; the action layer keeps its own audit
            logging.getLogger(__name__).warning("reminder proposals unavailable", exc_info=True)
    return hook


def _goal_lines(state: Path, *, reader: Callable[[str], str] | None = None,
                today: date | None = None, ledger: Ledger | None = None, viewer: str = "owner",
                now: datetime | None = None, required_sources: Sequence[str] = (),
                persist: bool = True, weekly: bool = False,
                open_items: Sequence[Mapping[str, Any]] | None = None) -> ContextLines:
    """One line per active Goal: next action, whether it is due or overdue, and the last authored progress."""
    from catchup_core.curated import goal_open_items, read_state, records  # noqa: PLC0415
    from catchup_core.goals import METRIC_KINDS, observe_goals, tradeoff_lines, validate_sprints  # noqa: PLC0415
    try:
        text = reader('goals') if reader else read_state(state, 'goals')
        all_goals = records(text, 'goals')
        goals = [g for g in all_goals if g.get('status') == 'active']
        sprints = records(text, 'sprints')
        sprint_problems = validate_sprints(all_goals, sprints)
    except (OSError, RuntimeError, ValueError):
        return ContextLines((), ("Goals unavailable; progress is unknown.",))
    output = [f'Sprint review needed: {problem}' for problem in sprint_problems]
    gaps = ['Sprint commitments need review'] if sprint_problems else []
    dependencies: dict[str, str] = {}
    citations: dict[int, tuple[str, ...]] = {}
    calibrations: dict[str, Any] = {}
    measured: dict[str, Any] = {}
    if ledger is not None and now is not None:
        items = goal_open_items(state, ledger, viewer, goals) if open_items is None else open_items
        for authored in goals:
            try:
                local: dict[str, str] = {}
                derived_lines = observe_goals(ledger, [authored], viewer, now,
                                         required_sources=required_sources, persist=persist, dependencies=local,
                                         weekly=weekly, open_items=items, sprints=sprints,
                                         calibrations=calibrations, observed=measured)
                for fid, version in local.items():
                    if fid in dependencies and dependencies[fid] != version:
                        raise ValueError('Goal evidence changed during composition')
                    dependencies[fid] = version
                if local:
                    citations.update({len(output)+index: tuple(local) for index in range(len(derived_lines))})
                output.extend(derived_lines)
            except (ValueError, KeyError, TypeError):
                output.append(f"{authored.get('title', 'Goal')}: observation unknown; check the metric definition.")
                gaps.append("Goal observation unknown; check the metric definition")
                if persist:
                    ledger.record_capability("RA-11", str(authored.get("id", "unknown")), succeeded=False)
        if weekly:
            for line, fact_ids in tradeoff_lines(goals, calibrations, measured):
                citations[len(output)] = fact_ids
                output.append(line)
    for goal in goals:
        observed = goal.get("last_progress") or "unknown"
        action, action_gaps = _authored_next_action(goal, today)
        gaps.extend(action_gaps)
        if (isinstance(goal.get('metric'), dict) and goal['metric'].get('kind') in METRIC_KINDS and ledger):
            output.append(f"{goal.get('id', 'Goal')}: {goal.get('title', '')}; {action}; "
                          'authored next action [src: state:goals]')
            continue
        output.append(f"{goal.get('id', 'Goal')}: {goal.get('title', '')}; {action}; "
                      f"authored observation {observed}, not re-verified [src: state:goals]")
    return ContextLines(output, gaps, dependencies, citations, commitment_count=len(goals))


def make_file_publisher(repo_root: Path, now: Callable[[], datetime], *,
                        cipher: EnvelopeCipher | None = None) -> Callable[[str, str, int], None]:
    """Publisher that writes the canonical briefing file to briefings/YYYY-MM-DD.md."""
    def publish(catchup_id: str, text: str, version: int) -> None:
        briefings_dir = repo_root / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        today = now().strftime("%Y-%m-%d")
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        if not catchup_id or any(c not in alphabet for c in catchup_id):
            raise RuntimeConfigError("invalid Catch-up archive identity")
        dest = briefings_dir / f"{today}-{catchup_id}.v{version}.{'ae1' if cipher else 'md'}"
        previous_versions = list(briefings_dir.glob(f"*-{catchup_id}.v{version}.{'ae1' if cipher else 'md'}"))
        if len(previous_versions) > 1:
            raise RuntimeConfigError("multiple archives claim this Catch-up version")
        if previous_versions:
            dest = previous_versions[0]  # resume across midnight keeps its first archive identity
        aad = f"archive:{catchup_id}:{version}".encode()
        if dest.exists():
            previous = (cipher.decrypt(dest.read_bytes(), aad=aad).decode() if cipher
                        else dest.read_text(encoding="utf-8"))
            if previous != text:
                raise RuntimeConfigError("archive version already contains different text")
            return
        data = cipher.encrypt(text.encode(), aad=aad)[0] if cipher else text.encode()
        atomic_bytes(dest, data)
    return publish


def _backup_for(local: Path, *, cipher: EnvelopeCipher | None = None) -> Callable[[Path], Path]:
    def backup(db: Path) -> Path:
        dest = local / f"ledger.pre-migration.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
        if cipher is not None:
            data = database_bytes(db)
            envelope, _ = cipher.encrypt(data, aad=b"migration-backup")
            if cipher.decrypt(envelope, aad=b"migration-backup") != data:
                raise RuntimeConfigError("migration backup verification failed")
            dest = dest.with_suffix(".ae1")
            atomic_bytes(dest, envelope)
            return dest
        with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(dest)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeConfigError("migration backup integrity check failed")
        return dest
    return backup
