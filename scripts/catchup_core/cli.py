"""Command entry point of the new core (re-artha §12.4 contract: entry point and result).

``python -m catchup_core.cli catchup --json [--shadow] --id ID [--format F]``

Exit codes: 0 complete · 10 degraded · 20 blocked · 30 usage error.  Result JSON goes
to stdout, diagnostics to stderr.  Dependencies are injected through ``deps_factory``;
the default factory wires real dependencies for ``--shadow`` only and refuses an
authoritative run, because the pilot has not been cut over (that needs consented real
data and owner sign-off) — the command can never silently publish a Briefing.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from catchup_core.contracts import (  # noqa: E402
    BriefingFormat,
    CatchupRequest,
    CatchupResult,
    ContractError,
    ExecutionMode,
    ExitCode,
)
from catchup_core.coordinator import Deps, run_catchup  # noqa: E402
from catchup_core.crypto import KeyUnavailableError  # noqa: E402
from catchup_core.exchange import (  # noqa: E402
    AwaitingExtraction,
    ExchangeExtractor,
    parse_responses,
    render_bundle,
)
from catchup_core.ledger import LedgerError  # noqa: E402

__all__ = ["main", "parse_args"]

DepsFactory = Callable[[CatchupRequest], Deps]


class NotConfigured(RuntimeError):
    pass


def _default_factory(request: CatchupRequest) -> Deps:
    """Real wiring: allows authoritative runs when cutover is active or configured."""
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from catchup_core.runtime import RuntimeConfigError, build_deps, load_runtime_config  # noqa: PLC0415
    from catchup_core.session_lock import SessionBusy  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    cfg_path = root / "config" / "catchup_core.yaml"
    cutover = False
    if cfg_path.is_file():
        try:
            cfg = load_runtime_config(cfg_path)
            cutover = cfg.cutover
        except Exception:  # noqa: S110
            pass
    if os.environ.get("ARTHA_CUTOVER") == "1":
        cutover = True
    elif os.environ.get("ARTHA_CUTOVER") == "0":
        cutover = False

    if request.mode is not ExecutionMode.SHADOW and not cutover:
        raise NotConfigured("authoritative runs are disabled until the pilot is cut over; use --shadow")
    try:
        return build_deps(request)
    except (RuntimeConfigError, OSError, ContractError, LedgerError, KeyUnavailableError, SessionBusy) as exc:
        raise NotConfigured(str(exc)) from exc


def _add_bundle_commands(sub: Any) -> None:
    """Hand-run multi-machine data path (re-artha 6.10)."""
    enroll = sub.add_parser("bundle-enroll", help="create or import the shared secret for an ingest-bundle producer")
    enroll.add_argument("producer")
    enroll.add_argument("--secret", default=None, help="base64 secret printed by enrollment on the other machine")
    export = sub.add_parser("bundle-export", help="fetch one source here and publish an ingest bundle (hand-run)")
    export.add_argument("source")
    export.add_argument("--days", type=int, default=30, help="history window for this export")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="catchup_core", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("catchup", help="run one Catch-up")
    run.add_argument("--json", action="store_true", help="print the typed result as JSON")
    run.add_argument("--shadow", action="store_true", help="comparison run: never publishes")
    run.add_argument("--id", default=None, help="Catch-up ID / idempotency key (default: new)")
    run.add_argument("--format", choices=[f.value for f in BriefingFormat], default="standard")
    run.add_argument("--responses", default=None, metavar="PATH|-",
                     help="answers to a paused run's extraction requests (JSON; '-' reads stdin)")
    run.add_argument("--domain", action="append", default=None, dest="domains",
                     help="domain pack to run (repeatable, default: from config or kids_school)")
    run.add_argument("--host", default="", help="designated authority host (required unless --shadow)")
    run.add_argument("--actor", default=None)
    run.add_argument("--channel", default="cli")
    _add_bundle_commands(sub)
    for name in ("beliefs", "why", "forget", "feedback", "correct", "health", "history", "items",
                 "goals", "capture", "export", "restore", "activity", "pause", "resume", "reconcile", "resolve"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true")
        command.add_argument("--shadow", action="store_true")
        command.add_argument("--actor", default=None)
        if name == "beliefs":
            command.add_argument("--domain")
            command.add_argument("--subject")
        elif name in ("why", "correct"):
            command.add_argument("fact_id")
            if name == "why":
                command.add_argument("--as-of")
            else:
                command.add_argument("--value", required=True)
                command.add_argument("--apply")
        elif name == "forget":
            command.add_argument("--connector", required=True)
            command.add_argument("--scope", required=True)
            command.add_argument("--upstream", required=True)
        elif name == "feedback":
            command.add_argument("obligation")
            command.add_argument("response", choices=("useful", "dismiss", "snooze", "acknowledge"))
            command.add_argument("--reason", required=True)
            command.add_argument("--days", type=int, default=7)
        elif name == "capture":
            command.add_argument("text")
        elif name in ("reconcile", "resolve"):
            command.add_argument("action_id")
            if name == "resolve":
                command.add_argument("outcome", choices=("succeeded", "failed"))
        elif name in ("export", "restore"):
            command.add_argument("path")
            if name == "restore":
                command.add_argument("destination")
    return parser.parse_args(list(argv))


def _read_responses(source: str | None, stdin: TextIO) -> dict[str, str] | None:
    if source is None:
        return None
    if source == "-":
        return parse_responses(stdin.read())
    with open(source, encoding="utf-8") as fh:
        return parse_responses(fh.read())


def main(argv: Sequence[str] | None = None, *, deps_factory: DepsFactory = _default_factory,
         out: TextIO | None = None, err: TextIO | None = None, stdin: TextIO | None = None) -> int:
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    stdin = stdin if stdin is not None else sys.stdin
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return int(ExitCode.USAGE_ERROR) if exc.code not in (0, None) else 0
    if args.command == "restore":
        return _restore(args, stdout, stderr)
    if args.command in ("bundle-enroll", "bundle-export"):
        return _bundle(args, stdout, stderr)
    if args.command != "catchup":
        return _inspect(args, stdout, stderr)
    return _catchup(args, deps_factory, stdout, stderr, stdin)


def _actor(args: argparse.Namespace, factory: DepsFactory) -> str:
    actor = args.actor or "user"
    if args.actor is None and factory is _default_factory:
        from catchup_core.runtime import RuntimeConfigError, load_runtime_config  # noqa: PLC0415
        try:
            return load_runtime_config(Path(__file__).resolve().parents[2] / "config/catchup_core.yaml").viewer
        except RuntimeConfigError:
            return actor
    return str(actor)


def _catchup(args: argparse.Namespace, deps_factory: DepsFactory,
             stdout: TextIO, stderr: TextIO, stdin: TextIO) -> int:
    try:
        request = CatchupRequest(
            catchup_id=args.id or f"cu_{uuid.uuid4().hex[:12]}", actor=_actor(args, deps_factory), channel=args.channel,
            mode=ExecutionMode.SHADOW if args.shadow else ExecutionMode.AUTHORITATIVE,
            briefing_format=BriefingFormat(args.format), host=args.host,
            pilot_domains=tuple(args.domains) if args.domains else ())
    except ContractError as exc:
        print(f"usage error: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    try:
        responses = _read_responses(args.responses, stdin)
    except (OSError, ValueError) as exc:
        print(f"usage error: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    deps = None
    try:
        deps = deps_factory(request)
        if responses is not None:
            deps = dataclasses.replace(deps, extractor=ExchangeExtractor(responses))
        result: CatchupResult = run_catchup(request, deps)
    except NotConfigured as exc:
        print(f"not configured: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    except AwaitingExtraction as paused:
        print(render_bundle(paused.catchup_id, paused.requests), file=stdout)
        print(f"awaiting extraction: {len(paused.requests)} request(s); resume with --id "
              f"{paused.catchup_id} --responses", file=stderr)
        return int(ExitCode.AWAITING_EXTRACTION)
    finally:
        if deps is not None and deps_factory is _default_factory:
            deps.ledger.close()
    if args.json:
        print(result.to_json(), file=stdout)
    else:
        if result.briefing_text:
            print(result.briefing_text, file=stdout, end="")
        print(f"{result.status.value}: {len(result.artifact_ids)} alert(s); "
              f"briefing {result.briefing_id or result.reason_unpublished or 'not published'}", file=stdout)
    return int(result.exit_code())


def _inspect(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    import json  # noqa: PLC0415
    import socket  # noqa: PLC0415

    from catchup_core.commands import execute  # noqa: PLC0415
    from catchup_core.runtime import build_deps, load_runtime_config  # noqa: PLC0415
    from catchup_core.session_lock import session_lock  # noqa: PLC0415
    root = Path(__file__).resolve().parents[2]
    deps = None
    try:
        config = load_runtime_config(root / "config" / "catchup_core.yaml")
        request = CatchupRequest(f"view_{uuid.uuid4().hex[:12]}", args.actor or config.viewer, "cli",
            mode=ExecutionMode.SHADOW if args.shadow else ExecutionMode.AUTHORITATIVE,
            host=socket.gethostname().split(".")[0])
        deps = build_deps(request, inspection=True)
        with session_lock(deps.ledger._path.with_suffix(".session.lock")):
            result = execute(args, deps, root)
        if args.json:
            print(json.dumps(result, indent=2, default=str), file=stdout)
        else:
            from catchup_core.views import render  # noqa: PLC0415
            print(render(args.command, result), file=stdout)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=stderr)
        return int(ExitCode.BLOCKED)
    finally:
        if deps is not None:
            deps.ledger.close()


def _bundle(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Hand-run multi-machine data path (re-artha 6.10): no listener, no replication, records only."""
    import importlib  # noqa: PLC0415
    import json  # noqa: PLC0415
    import socket  # noqa: PLC0415
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from catchup_core.bundles import BundleError, enroll_producer, export_fetch, load_producer_key  # noqa: PLC0415
    from catchup_core.runtime import _default_loaders, build_fetchers, load_runtime_config  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    try:
        backend = importlib.import_module("keyring")
        if args.command == "bundle-enroll":
            secret = enroll_producer(args.producer, backend, args.secret)
            shown = {"producer": args.producer, "enrolled": True}
            if not args.secret:
                shown["secret_to_copy_once"] = secret
                shown["next"] = f"On the other machine run: bundle-enroll {args.producer} --secret <that value>"
            print(json.dumps(shown), file=stdout)
            return 0
        config = load_runtime_config(root / "config" / "catchup_core.yaml")
        spec = (config.policy.get("bundles") or {}).get(args.source)
        if not isinstance(spec, dict) or args.source not in config.sources:
            raise BundleError(f"{args.source!r} needs both a 'sources' entry and a 'bundles' entry in the config")
        key = load_producer_key(str(spec["producer"]), backend)
        if key is None:
            raise BundleError("producer is not enrolled on this machine; run bundle-enroll first")
        now = datetime.now(UTC)
        since = (now - timedelta(days=args.days)).isoformat()
        handler_loader, auth_loader = _default_loaders()
        fetch = build_fetchers(config, since, handler_loader, auth_loader, {args.source: True})[args.source]()
        path = export_fetch(
            Path(str(spec["dir"])).expanduser(), key, fetch, namespace=str(spec.get("namespace", "personal")),
            recipient=str(spec["recipient"]), authority_generation=int(spec.get("authority_generation", 1)),
            since=since, now=now)
        host = socket.gethostname().split(".")[0]
        print(json.dumps({"published": path.name, "records": len(fetch.records), "host": host}), file=stdout)
        return 0
    except (BundleError, OSError, ValueError, KeyError, RuntimeError) as exc:
        print(str(exc), file=stderr)
        return int(ExitCode.BLOCKED)


def _restore(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Recovery must work even when the live ledger is missing or locked."""
    import importlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    from catchup_core.backup import restore_encrypted_snapshot  # noqa: PLC0415
    from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider  # noqa: PLC0415
    from catchup_core.ledger import KeyringSealedStore  # noqa: PLC0415
    from catchup_core.runtime import load_runtime_config  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    try:
        config = load_runtime_config(root / "config/catchup_core.yaml")
        if args.actor and args.actor != config.viewer:
            raise PermissionError("restore actor does not match the local viewer")
        destination = Path(args.destination).expanduser().resolve()
        if destination.is_relative_to(root):
            raise PermissionError("restore sensitive runtime storage outside the repository")
        backend = importlib.import_module("keyring")
        cipher = EnvelopeCipher(KeyringKeyProvider("artha-ledger", backend=backend))
        privacy = KeyringSealedStore(backend, "shadow" if args.shadow else "authoritative")
        restore_encrypted_snapshot(Path(args.path).expanduser(), destination, cipher, privacy)
        print(json.dumps({"restored_to": str(destination), "dispatchable": False,
                          "next": "Review this generation before activating it; action history is quarantined."}),
              file=stdout)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=stderr)
        return int(ExitCode.BLOCKED)


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
