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
  with its answers.  ``ARTHA_EXTRACTOR_CMD`` may name any command that reads the prompt on stdin
  and prints the JSON on stdout for unattended runs.  Provider, data classes and retention remain
  the owner's decision (§9.2, §10.1); answers are untrusted and validated by the core.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from catchup_core import ids
from catchup_core.adapters import fetch_via_handler
from catchup_core.backup import atomic_bytes, database_bytes, encrypted_snapshot
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode, FailureClass
from catchup_core.coordinator import ConnectorFetch, ConnectorRecord, Deps
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
from catchup_core.domain_pack import DomainPack, PackError, composite_pack, load_pack_file
from catchup_core.exchange import ExchangeExtractor
from catchup_core.incumbent import GuardedIncumbent
from catchup_core.ledger import KeyringSealedStore, Ledger, PermissionDenied
from catchup_core.legacy_state import LegacyStateAdapter
from catchup_core.policy import authorize_source, validate_release, verify_rollback

__all__ = ["RuntimeConfig", "RuntimeConfigError", "build_deps", "command_extractor", "load_runtime_config",
           "local_root", "make_file_publisher"]

_PACKS = Path(__file__).parent / "packs"


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
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeConfigError(f"cannot read {path}: {exc}") from exc
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise RuntimeConfigError("config needs a non-empty 'sources' mapping")
    for name, spec in sources.items():
        if not isinstance(spec, dict) or not spec.get("connector") or not spec.get("account_scope"):
            raise RuntimeConfigError(f"source {name!r} needs 'connector' and 'account_scope'")
    subjects = data.get("subjects", [] if data.get("household_from_profile") is True else None)
    if (not isinstance(subjects, list) or not all(isinstance(s, str) and s for s in subjects)
            or (not subjects and data.get("household_from_profile") is not True)):
        raise RuntimeConfigError("config needs a non-empty 'subjects' list")
    required = data.get("consent_required", [])
    if not isinstance(required, list) or not all(isinstance(s, str) and s for s in required):
        raise RuntimeConfigError("'consent_required' must be a list of subject ids")
    unknown = sorted(set(required) - set(subjects))
    if unknown:
        raise RuntimeConfigError(f"'consent_required' names subjects not in 'subjects': {', '.join(unknown)}")
    cutover = bool(data.get("cutover", False))
    extractor_cmd = data.get("extractor_cmd")
    cfg_domains = data.get("domains")
    domains = tuple(cfg_domains) if isinstance(cfg_domains, list) else ("kids_school",)
    return RuntimeConfig(sources, [str(s) for s in subjects], str(data.get("viewer", "owner")),
                         frozenset(required), cutover=cutover,
                         extractor_cmd=str(extractor_cmd) if extractor_cmd else None,
                         domains=domains, policy=data)


def command_extractor(command: str | None, *, timeout: float = 120.0,
                      runner: Callable[..., Any] = subprocess.run) -> Callable[[str], str]:
    """Extractor backed by an external command; fails closed when unset or failing."""
    argv = shlex.split(command) if command else []

    def extract(prompt: str) -> str:
        if not argv:
            raise RuntimeError("no extractor command configured (ARTHA_EXTRACTOR_CMD)")
        proc = runner(argv, input=prompt, capture_output=True, text=True, timeout=timeout, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"extractor exited {proc.returncode}")
        return str(proc.stdout)
    return extract


def _id_key(provider: KeyringKeyProvider) -> ids.HmacKey:
    version = provider.current_version()
    return ids.HmacKey(version, provider.key(version))


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
            return fetch_via_handler(
                pack_name, str(spec["account_scope"]), handler.fetch, since=start, auth_context=auth,
                fetch_kwargs=spec.get("kwargs") or {}, required=required.get(pack_name, True))
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


def build_deps(  # noqa: PLR0915 - explicit environment boundary and dependency wiring
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
) -> Deps:
    env = os.environ if environ is None else environ
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    config = load_runtime_config(root / "config" / "catchup_core.yaml")
    local = local_root(env)
    if local.resolve().is_relative_to(root.resolve()):
        raise RuntimeConfigError("private runtime storage must be outside the repository")
    if request.mode.value == "shadow":
        local = local / "shadow"

    domains = request.pilot_domains or config.domains or ("kids_school",)
    validate_release(replace(request, mode=ExecutionMode.SHADOW) if inspection and not mutation else request,
                     config.policy, viewer=config.viewer, domains=domains)
    pack = _load_packs(domains)
    source_class = {**{name: str(spec.get("class", name)) for name, spec in config.sources.items()},
                    "capture": "parent_note", "documents": "official_document"}
    validate_source_classes(pack, source_class)
    if inspection and not (local / "ledger.db").is_file():
        raise RuntimeConfigError("no Catch-up recorded here yet; start with catchup --shadow")

    cipher_provider = KeyringKeyProvider("artha-ledger", backend=keyring_backend)
    id_provider = KeyringKeyProvider("artha-ledger-ids", backend=keyring_backend)
    cipher = EnvelopeCipher(cipher_provider)
    id_key = _id_key(id_provider)
    if request.mode is ExecutionMode.AUTHORITATIVE and not inspection:
        verify_rollback(config.policy, cipher)
    if config.policy.get("household_from_profile") is True:
        _bind_household(config, root, id_key, (now or (lambda: datetime.now(UTC)))().astimezone(pack.timezone).date())
    if keyring_backend is None:
        keyring_backend = importlib.import_module("keyring")
    ledger, blobs = _open_ledger(local, config, request, cipher, id_key, keyring_backend)

    if handler_loader is None or auth_loader is None:
        handler_loader, auth_loader = _default_loaders()
    required = {s.connector: s.required for s in pack.sources}
    clock: Callable[[], datetime] = now or (lambda: datetime.now(UTC))
    def now_fn() -> datetime:
        return clock().astimezone(pack.timezone)
    since = (now_fn() - timedelta(days=int(config.policy.get("bootstrap_days", 30)))).isoformat()
    ext_cmd = env.get("ARTHA_EXTRACTOR_CMD") or config.extractor_cmd
    fetchers = build_fetchers(config, since, handler_loader, auth_loader, required, ledger)
    fetchers["capture"] = _capture_fetcher(local, cipher, config.viewer)
    from catchup_core.documents import fetch_documents  # noqa: PLC0415
    fetchers["documents"] = lambda: fetch_documents(ledger, config.viewer)
    fetchers.update(_bundle_fetchers(config, ledger, keyring_backend, required, clock))
    def curated_reader(name: str) -> str:
        from catchup_core.curated import read_state  # noqa: PLC0415
        subjects = [config.viewer] if name == "goals" else config.subjects
        authorize_source(ledger, subjects, config.consent_required, f"curated:{name}", config.viewer)
        return read_state(root / "state", name)
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
    inputs = json.dumps(config.policy, sort_keys=True) + str(ext_cmd)
    inputs += "".join((_PACKS / f"{d}.yaml").read_text() for d in domains)
    inputs += "".join(p.read_text() for p in sorted(Path(__file__).parent.glob("*.py")))
    inputs += "".join(p.read_text() for p in sorted((Path(__file__).parent / "prompts").glob("*.md")))
    return Deps(
        mode=request.mode,
        ledger=ledger, pack=pack, blobs=blobs, id_key=id_key,
        fetchers=fetchers,
        extractor=(command_extractor(ext_cmd) if ext_cmd else ExchangeExtractor()),
        model_log=_model_log_writer(local, now_fn),
        provider_label=(f"command:{ext_cmd.split()[0].rsplit('/', 1)[-1]}" if ext_cmd else "session-assistant"),
        subjects=config.subjects, viewer=config.viewer, now=now_fn,
        source_class=source_class,
        incumbent=GuardedIncumbent(LegacyStateAdapter(root / "state", domains, now_fn(), reader=curated_reader)),
        publisher=make_file_publisher(local, now_fn, cipher=cipher),
        coverage_line=_coverage_line,
        finalizer=finalize,
        goal_lines=lambda: _goal_lines(root / "state", reader=curated_reader, today=now_fn().date(),
                                       ledger=ledger, viewer=config.viewer, now=now_fn(),
                                       required_sources=tuple(s.connector for s in pack.sources if s.required)),
        goal_domains=lambda: _goal_domains(root / "state", reader=curated_reader),
        input_version=hashlib.sha256(inputs.encode()).hexdigest(),
        subjects_for=lambda connector, text: _subjects_for(config, connector, text),
        subject_labels=config.subject_labels,
    )


def _bind_household(config: RuntimeConfig, root: Path, key: ids.HmacKey, today: Any) -> None:
    from catchup_core.household import members  # noqa: PLC0415
    try:
        profile_path = root / "config/user_profile.yaml"
        profile = yaml.safe_load(profile_path.read_text())
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
        if changed:
            atomic_bytes(profile_path, yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).encode())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeConfigError("household identity could not be resolved from the profile") from exc
    config.subjects = [person.subject_id for person in people]
    config.consent_required = frozenset(person.subject_id for person in people if person.consent_required)
    config.subject_labels = {person.subject_id: person.name for person in people}
    config.policy["subject_aliases"] = {person.subject_id: list(person.aliases) for person in people}
    config.policy["resolved_subjects"] = config.subjects
    config.policy["consent_required"] = sorted(config.consent_required)


def action_database_path(workspace: Path) -> Path:
    queue = importlib.import_module("action_queue")
    return Path(queue.ActionQueue._resolve_db_path(workspace))


def _subjects_for(config: RuntimeConfig, connector: str, text: str) -> list[str]:
    """Resolve one subject from trusted source scope or explicit aliases, never list order."""
    candidates = config.sources.get(connector, {}).get("subjects", config.subjects)
    if len(candidates) == 1:
        return [str(candidates[0])]
    aliases = config.policy.get("subject_aliases", {})
    named = [str(subject) for subject in candidates if any(
        re.search(r"(?<!\w)" + re.escape(str(alias)) + r"(?!\w)", text, re.IGNORECASE)
        for alias in (subject, *aliases.get(subject, [])))]
    # Multi-person evidence needs a scoped excerpt or explicit review before attribution.
    return named if len(named) == 1 else []


def _open_ledger(local: Path, config: RuntimeConfig, request: CatchupRequest, cipher: EnvelopeCipher,
                 id_key: ids.HmacKey, keyring_backend: Any) -> tuple[Ledger, EncryptedBlobStore]:
    from catchup_core.session_lock import session_lock  # noqa: PLC0415
    with session_lock(local / "ledger.session.lock"):
        is_new = not (local / "ledger.db").exists()
        ledger = Ledger(local / "ledger.db", id_key=id_key, cipher=cipher, owner_id=config.viewer,
                        consent_subjects=config.consent_required,
                        known_subjects=frozenset(config.subjects),
                        sealed=KeyringSealedStore(keyring_backend, request.mode.value)).open()
        try:
            ledger.migrate(backup=_backup_for(local, cipher=cipher))
            if is_new:
                ledger.seal_privacy()
            if not ledger.check_restore():
                raise RuntimeConfigError("ledger is older than the sealed deletion checkpoint; replay tombstones first")
            ledger.protect_legacy_values()
            blobs = EncryptedBlobStore(local / "blobs", cipher, id_key)
            ledger._blob_deleter = blobs.delete
            ledger._artifact_deleter = lambda cids: _purge_archives(local, ledger, cids)
            ledger._replay_deleter = lambda fids: _purge_replay(local, fids)
            ledger.resume_pending_forgets()
        except Exception:
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


def _model_log_writer(local: Path, now: Callable[[], datetime]) -> Callable[[dict[str, str]], None]:
    """Append-only monthly JSONL of *who may have seen what* (provider, data class, purpose), no content."""
    def write(entry: dict[str, str]) -> None:
        path = local / "model-requests" / f"{now():%Y-%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
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
    return composite_pack(loaded)


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


def _active_goals(state: Path, reader: Callable[[str], str] | None) -> list[dict[str, Any]]:
    from catchup_core.curated import read_state, records  # noqa: PLC0415
    goals = records(reader("goals") if reader else read_state(state, "goals"), "goals")
    return [g for g in goals if g.get("status") == "active"]


def _goal_domains(state: Path, *, reader: Callable[[str], str] | None = None) -> set[str]:
    """Categories of active Goals, lower-cased; unreadable Goals simply contribute nothing."""
    try:
        return {str(g["category"]).lower() for g in _active_goals(state, reader) if g.get("category")}
    except (OSError, RuntimeError, ValueError):
        return set()


def _goal_lines(state: Path, *, reader: Callable[[str], str] | None = None,
                today: date | None = None, ledger: Ledger | None = None, viewer: str = "owner",
                now: datetime | None = None, required_sources: Sequence[str] = ()) -> list[str]:
    """One line per active Goal: next action, whether it is due or overdue, and the last authored progress."""
    try:
        goals = _active_goals(state, reader)
    except (OSError, RuntimeError, ValueError):
        return ["Goals unavailable; progress is unknown."]
    from catchup_core.goals import observe_goals  # noqa: PLC0415
    output = []
    if ledger is not None and now is not None:
        for authored in goals:
            try:
                output.extend(observe_goals(ledger, [authored], viewer, now, required_sources=required_sources))
            except (ValueError, KeyError, TypeError):
                output.append(f"{authored.get('title', 'Goal')}: observation unknown; check the metric definition.")
                ledger.record_capability("RA-11", str(authored.get("id", "unknown")), succeeded=False)
    for goal in goals:
        if isinstance(goal.get("metric"), dict) and goal["metric"].get("kind") == "on_time_obligations" and ledger:
            continue
        observed = goal.get("last_progress") or "unknown"
        next_action = goal.get("next_action") or "next action unknown"
        timing = ""
        due = str(goal.get("next_action_date") or "")[:10]
        if today is not None and due:
            try:
                days = (date.fromisoformat(due) - today).days
                timing = (f" (overdue by {-days} day{'s' if days != -1 else ''})" if days < 0
                          else " (due today)" if days == 0 else f" (due in {days} day{'s' if days != 1 else ''})")
            except ValueError:
                timing = " (date unreadable)"
        output.append(f"{goal.get('id', 'Goal')}: {goal.get('title', '')}; {next_action}{timing}; "
                      f"authored observation {observed}, not re-verified [src: state:goals]")
    return output


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


def _coverage_line() -> str:
    try:
        from lib.connector_coverage import line_from_store  # noqa: PLC0415
        return line_from_store()
    except Exception:
        return "Coverage: not summarized."


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
