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
      subjects: [kid1, kid2]
      viewer: owner

* the extraction model is an **external command** (``ARTHA_EXTRACTOR_CMD``) that reads
  the prompt on stdin and prints the JSON on stdout.  It is deliberately not bundled:
  provider, data classes and retention are the owner's decision (§9.2, §10.1).  With
  no command configured, extraction fails closed per Signal and the Catch-up reports
  itself degraded instead of guessing.
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
from catchup_core.domain_pack import DomainPack, PackError, load_pack_file
from catchup_core.incumbent import GuardedIncumbent
from catchup_core.ledger import FileSealedStore, Ledger
from catchup_core.legacy_state import LegacyStateAdapter

__all__ = ["RuntimeConfig", "RuntimeConfigError", "build_deps", "command_extractor", "load_runtime_config",
           "local_root"]

_PACKS = Path(__file__).parent / "packs"


class RuntimeConfigError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(self, sources: Mapping[str, Mapping[str, Any]], subjects: list[str], viewer: str):
        self.sources = {k: dict(v) for k, v in sources.items()}
        self.subjects = subjects
        self.viewer = viewer


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
    return RuntimeConfig(sources, [str(s) for s in subjects], str(data.get("viewer", "owner")))


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

    domains = request.pilot_domains or ("kids_school",)
    try:
        pack: DomainPack = load_pack_file(_PACKS / f"{domains[0]}.yaml")
    except PackError as exc:
        raise RuntimeConfigError(str(exc)) from exc

    cipher_provider = KeyringKeyProvider("artha-ledger", backend=keyring_backend)
    id_provider = KeyringKeyProvider("artha-ledger-ids", backend=keyring_backend)
    cipher = EnvelopeCipher(cipher_provider)
    id_key = _id_key(id_provider)

    ledger = Ledger(local / "ledger.db", id_key=id_key, cipher=cipher, owner_id=config.viewer,
                    sealed=FileSealedStore(local / "sealed" / "checkpoint.json")).open()
    ledger.migrate(backup=_backup_for(local))
    if not ledger.check_restore():
        raise RuntimeConfigError("ledger is older than the sealed deletion checkpoint; replay tombstones first")
    blobs = EncryptedBlobStore(local / "blobs", cipher, id_key)

    if handler_loader is None or auth_loader is None:
        handler_loader, auth_loader = _default_loaders()
    since = (datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    required = {s.connector: s.required for s in pack.sources}
    return Deps(
        ledger=ledger, pack=pack, blobs=blobs, id_key=id_key,
        fetchers=build_fetchers(config, since, handler_loader, auth_loader, required),
        extractor=command_extractor(env.get("ARTHA_EXTRACTOR_CMD")),
        subjects=config.subjects, viewer=config.viewer, now=now or (lambda: datetime.now(UTC)),
        source_class={name: name for name in config.sources},
        incumbent=GuardedIncumbent(LegacyStateAdapter(root / "state", domains, datetime.now(UTC))),
        coverage_line=_coverage_line,
    )


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
