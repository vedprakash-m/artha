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

import importlib
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from catchup_core import ids
from catchup_core.adapters import fetch_via_handler
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CatchupRequest, CoverageStatus
from catchup_core.coordinator import ConnectorFetch, Deps
from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
from catchup_core.domain_pack import DomainPack, PackError, composite_pack, load_pack_file
from catchup_core.exchange import ExchangeExtractor
from catchup_core.incumbent import GuardedIncumbent
from catchup_core.ledger import FileSealedStore, Ledger
from catchup_core.legacy_state import LegacyStateAdapter

__all__ = ["RuntimeConfig", "RuntimeConfigError", "build_deps", "command_extractor", "load_runtime_config",
           "local_root", "make_file_publisher"]

_PACKS = Path(__file__).parent / "packs"


class RuntimeConfigError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(self, sources: Mapping[str, Mapping[str, Any]], subjects: list[str], viewer: str,
                 consent_required: frozenset[str] = frozenset(), cutover: bool = False,
                 extractor_cmd: str | None = None, domains: tuple[str, ...] = ("kids_school",)):
        self.sources = {k: dict(v) for k, v in sources.items()}
        self.subjects = subjects
        self.viewer = viewer
        self.consent_required = consent_required
        self.cutover = cutover
        self.extractor_cmd = extractor_cmd
        self.domains = tuple(domains)


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
    subjects = data.get("subjects")
    if not isinstance(subjects, list) or not all(isinstance(s, str) and s for s in subjects) or not subjects:
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
                         domains=domains)


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
) -> dict[str, Callable[[], ConnectorFetch]]:
    """One callable per pilot source; load/auth failures become *unavailable*, not crashes."""
    fetchers: dict[str, Callable[[], ConnectorFetch]] = {}
    for pack_name, spec in config.sources.items():
        def run(pack_name: str = pack_name, spec: Mapping[str, Any] = spec) -> ConnectorFetch:
            try:
                handler = handler_loader(str(spec["connector"]))
                auth = auth_loader(str(spec["connector"]))
            except Exception:
                from catchup_core.contracts import FailureClass  # noqa: PLC0415
                return ConnectorFetch(pack_name, str(spec["account_scope"]), (), CoverageStatus.UNAVAILABLE,
                                      FailureClass.AUTH, ("connector could not be loaded or authorised",),
                                      required.get(pack_name, True))
            return fetch_via_handler(
                pack_name, str(spec["account_scope"]), handler.fetch, since=since, auth_context=auth,
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


def build_deps(
    request: CatchupRequest,
    *,
    repo_root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    keyring_backend: Any = None,
    handler_loader: Callable[[str], Any] | None = None,
    auth_loader: Callable[[str], Mapping[str, Any]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> Deps:
    env = os.environ if environ is None else environ
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    config = load_runtime_config(root / "config" / "catchup_core.yaml")
    local = local_root(env)

    domains = request.pilot_domains or config.domains or ("kids_school",)
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
    pack: DomainPack = composite_pack(loaded)

    cipher_provider = KeyringKeyProvider("artha-ledger", backend=keyring_backend)
    id_provider = KeyringKeyProvider("artha-ledger-ids", backend=keyring_backend)
    cipher = EnvelopeCipher(cipher_provider)
    id_key = _id_key(id_provider)

    ledger = Ledger(local / "ledger.db", id_key=id_key, cipher=cipher, owner_id=config.viewer,
                    consent_subjects=config.consent_required,
                    sealed=FileSealedStore(local / "sealed" / "checkpoint.json")).open()
    ledger.migrate(backup=_backup_for(local))
    if not ledger.check_restore():
        raise RuntimeConfigError("ledger is older than the sealed deletion checkpoint; replay tombstones first")
    blobs = EncryptedBlobStore(local / "blobs", cipher, id_key)

    if handler_loader is None or auth_loader is None:
        handler_loader, auth_loader = _default_loaders()
    since = (datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    required = {s.connector: s.required for s in pack.sources}
    now_fn = now or (lambda: datetime.now(UTC))
    ext_cmd = env.get("ARTHA_EXTRACTOR_CMD") or config.extractor_cmd
    return Deps(
        ledger=ledger, pack=pack, blobs=blobs, id_key=id_key,
        fetchers=build_fetchers(config, since, handler_loader, auth_loader, required),
        extractor=(command_extractor(ext_cmd) if ext_cmd else ExchangeExtractor()),
        subjects=config.subjects, viewer=config.viewer, now=now_fn,
        source_class={name: name for name in config.sources},
        incumbent=GuardedIncumbent(LegacyStateAdapter(root / "state", domains, datetime.now(UTC))),
        publisher=make_file_publisher(root, now_fn),
        coverage_line=_coverage_line,
    )


def make_file_publisher(repo_root: Path, now: Callable[[], datetime]) -> Callable[[str, str, int], None]:
    """Publisher that writes the canonical briefing file to briefings/YYYY-MM-DD.md."""
    def publish(catchup_id: str, text: str, version: int) -> None:
        briefings_dir = repo_root / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        today = now().strftime("%Y-%m-%d")
        dest = briefings_dir / f"{today}.md" if version == 1 else briefings_dir / f"{today}.v{version}.md"
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
    return publish


def _coverage_line() -> str:
    try:
        from lib.connector_coverage import line_from_store  # noqa: PLC0415
        return line_from_store()
    except Exception:
        return "Coverage: not summarized."


def _backup_for(local: Path) -> Callable[[Path], Path]:
    def backup(db: Path) -> Path:
        import shutil  # noqa: PLC0415
        dest = local / f"ledger.pre-migration.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
        shutil.copy2(db, dest)
        return dest
    return backup
