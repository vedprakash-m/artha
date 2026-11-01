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
# Launched as a script, this package's own directory leads sys.path and its actions.py would shadow scripts/actions/.
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != Path(__file__).resolve().parent]
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
    ResponseDocument,
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


def parse_args(argv: Sequence[str]) -> argparse.Namespace:  # noqa: PLR0912, PLR0915 - flat command grammar
    parser = argparse.ArgumentParser(prog="catchup_core", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("catchup", help="run one Catch-up")
    run.add_argument("--json", action="store_true", help="print the typed result as JSON")
    run.add_argument("--shadow", action="store_true", help="comparison run: never publishes")
    run.add_argument("--id", default=None, help="Catch-up ID / idempotency key (default: new)")
    run.add_argument("--format", choices=[f.value for f in BriefingFormat], default="standard")
    run.add_argument("--everything", action="store_true",
                     help="show everything: the deep format with every Open Item and State File listed")
    run.add_argument("--responses", default=None, metavar="PATH|-",
                     help="answers to a paused run's extraction requests (JSON; '-' reads stdin)")
    run.add_argument("--domain", action="append", default=None, dest="domains",
                     help="domain pack to run (repeatable, default: from config or kids_school)")
    run.add_argument("--host", default="", help="designated authority host (required unless --shadow)")
    run.add_argument("--actor", default=None)
    run.add_argument("--channel", default="cli")
    _add_bundle_commands(sub)
    sub.add_parser("mcp", help="read-only MCP server over stdio (launched by your assistant; no listener)")
    for name in ("beliefs", "why", "forget", "feedback", "correct", "health", "history", "items",
                 "goals", "capture", "export", "restore", "activity", "pause", "resume", "reconcile", "resolve",
                 "ingest", "review", "complete", "transfer-authority", "retire-authority", "field-review", "retention",
                 "cancel-extraction", "interchange", "emergency", "configure", "usage", "consent"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true")
        command.add_argument("--shadow", action="store_true")
        command.add_argument("--actor", default=None)
        command.add_argument("--detail", action="store_true", help="include internal record IDs")
        if name == "beliefs":
            command.add_argument("--domain")
            command.add_argument("--subject")
        elif name == 'goals':
            command.add_argument('--weekly', action='store_true', help='compare dated source metrics seven days apart')
        elif name in ("why", "correct"):
            command.add_argument("fact_id")
            if name == "why":
                command.add_argument("--as-of")
            else:
                command.add_argument("--value", required=True)
                command.add_argument("--apply")
        elif name == "complete":
            command.add_argument('record', help='milestone:<goal>:<item>, responsibility:<id>, '
                                 'household:<id> or open_item:<id>')
            command.add_argument("--apply")
            command.add_argument("--reopen", action="store_true", help="withdraw reviewed completion evidence")
        elif name == "consent":
            command.add_argument("action", choices=("grant", "list"))
            command.add_argument("--subject", help="your full name as it appears in the household profile")
            command.add_argument("--scope", action="append", default=None,
                                 help="source:account, e.g. outlook_inbox:personal")
            command.add_argument("--days", type=int, default=90)
        elif name == 'field-review':
            command.add_argument('fact_id')
            command.add_argument('--apply')
            command.add_argument('--reopen', action='store_true')
        elif name == 'retention':
            command.add_argument('--at', help='exact timezone-aware preview reference time')
            command.add_argument('--apply', help='reviewed preview digest; application remains held')
        elif name == 'cancel-extraction':
            command.add_argument('catchup_id')
        elif name == 'usage':
            command.add_argument('catchup_id')
            command.add_argument('request_key')
            command.add_argument('--input-tokens', type=int, required=True, help='provider-reported input tokens')
            command.add_argument('--output-tokens', type=int, required=True, help='provider-reported output tokens')
            command.add_argument('--source', required=True, help='where the numbers came from, e.g. provider-receipt')
            command.add_argument('--zero-reason', help='required when both token counts are 0: why nothing was used')
        elif name == 'interchange':
            command.add_argument('path')
            command.add_argument('--read', action='store_true', help='validate and summarize a file; applies nothing')
        elif name == 'configure':
            command.add_argument('key', help='bootstrap_days or source.<declared source name>')
            command.add_argument('value', help='integer, or a complete source JSON object/null for removal')
            command.add_argument('--apply', help='digest from the reviewed preview')
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
        elif name == "ingest":
            command.add_argument("path")
            command.add_argument("--subject", required=True, help="person's name or subject ID")
        elif name == "review":
            command.add_argument("feedback_id", nargs="?")
            command.add_argument("--label", choices=("correct", "incorrect", "handled", "timing"))
        elif name in ("reconcile", "resolve"):
            command.add_argument("action_id")
            if name == "resolve":
                command.add_argument("outcome", choices=("succeeded", "failed"))
        elif name in ("export", "restore"):
            command.add_argument("path")
            if name == "restore":
                command.add_argument("destination")
        elif name == "transfer-authority":
            command.add_argument("new_host")
            command.add_argument("--expected-generation", type=int, required=True)
            command.add_argument("--checkpoint", required=True, help="path to verified ACK2 full-state checkpoint")
            command.add_argument("--retirement-receipt", help="receipt file issued by the former host")
            command.add_argument("--former-host-key", help="key fingerprint printed by the former host")
            command.add_argument("--former-quiesced", action="store_true",
                                 help="UNVERIFIED: owner says the former executor is quiesced (use when it is lost)")
            command.add_argument("--credentials-revoked", action="store_true",
                                 help="UNVERIFIED: owner says former host write credentials are revoked")
        elif name == "retire-authority":
            command.add_argument("new_host")
            command.add_argument("--checkpoint", required=True, help="checkpoint the new host will restore")
            command.add_argument("--output", required=True, help="where to write the signed receipt")
    return parser.parse_args(list(argv))


def _read_responses(source: str | None, stdin: TextIO) -> ResponseDocument | None:
    from catchup_core.exchange import _MAX_DOCUMENT_BYTES, parse_response_document  # noqa: PLC0415
    if source is None:
        return None
    if source == "-":
        return parse_response_document(stdin.read(_MAX_DOCUMENT_BYTES + 1))
    with open(source, encoding="utf-8") as fh:
        return parse_response_document(fh.read(_MAX_DOCUMENT_BYTES + 1))


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
    if args.command == "mcp":
        return _mcp(stdin, stdout, stderr)
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
            briefing_format=BriefingFormat.DEEP if args.everything else BriefingFormat(args.format), host=args.host,
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
            from catchup_core.exchange import verify_response_identity  # noqa: PLC0415
            try:
                matched = verify_response_identity(responses, deps.evaluation_cohort,
                                                   required=request.mode is ExecutionMode.AUTHORITATIVE)
            except ValueError as exc:
                print(f'blocked: {exc}', file=stderr)
                return int(ExitCode.BLOCKED)
            deps = dataclasses.replace(deps, extractor=ExchangeExtractor(responses.responses),
                                       extractor_identity_matched=matched)
        result: CatchupResult = run_catchup(request, deps)
    except NotConfigured as exc:
        print(f"not configured: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    except AwaitingExtraction as paused:
        print(render_bundle(paused.catchup_id, paused.requests, deps.evaluation_cohort if deps else {}), file=stdout)
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


def _pause_actions(request: CatchupRequest, config: Any, root: Path) -> dict[str, Any]:
    from catchup_core.backup import atomic_bytes  # noqa: PLC0415
    from catchup_core.policy import validate_local_authority  # noqa: PLC0415
    if request.mode is ExecutionMode.SHADOW:
        raise LedgerError("shadow mode cannot change live action controls")
    validate_local_authority(request, config.policy, viewer=config.viewer)
    atomic_bytes(root / "config" / "actions.paused", b"Actions paused by the local owner.\n")
    return {"actions_paused": True}


def _mutates(args: argparse.Namespace) -> bool:
    """Whether this command writes the live ledger, so storage must open read-write."""
    mutations = {"correct", "forget", "feedback", "capture", "ingest", "pause", "resume",
                 "reconcile", "resolve", "complete", "transfer-authority", "retire-authority", "field-review",
                 "cancel-extraction",
                 "configure", "usage"}
    return ((args.command in mutations
             and (args.command not in {"correct", "complete", "configure"} or args.apply is not None))
            or (args.command == "review" and args.label is not None)
            or (args.command == "consent" and args.action == "grant"))


def _run_inspection(args: argparse.Namespace) -> dict[str, Any]:
    """Open storage, run one command under the session lock, close it.  Shared by the CLI and the MCP server."""
    import socket  # noqa: PLC0415

    from catchup_core.commands import execute  # noqa: PLC0415
    from catchup_core.runtime import build_deps, inspection_freshness, load_runtime_config  # noqa: PLC0415
    from catchup_core.session_lock import session_lock  # noqa: PLC0415
    root = Path(__file__).resolve().parents[2]
    deps = None
    try:
        config = load_runtime_config(root / "config" / "catchup_core.yaml")
        request = CatchupRequest(f"view_{uuid.uuid4().hex[:12]}", args.actor or config.viewer, "cli",
            mode=ExecutionMode.SHADOW if args.shadow else ExecutionMode.AUTHORITATIVE,
            host=socket.gethostname().split(".")[0])
        if args.command == "pause":
            return _pause_actions(request, config, root)
        mutation = _mutates(args)
        deps = build_deps(request, inspection=True, mutation=mutation,
                          safety_control=args.command in {"pause", "forget", "transfer-authority", "retire-authority",
                                                              "configure"},
                          configuration_control=args.command == "configure")
        with deps.maintenance(), session_lock(deps.ledger._path.with_suffix(".session.lock")):
            if args.command not in {"pause", "forget", "transfer-authority", "retire-authority", "configure"}:
                check = deps.preflight if mutation else (deps.inspection_preflight or deps.preflight)
                check()
            result = execute(args, deps, root)
            result["freshness"] = inspection_freshness(deps.ledger, deps.now())
            return result
    finally:
        if deps is not None:
            deps.ledger.close()


def _inspect(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    import json  # noqa: PLC0415
    try:
        result = _run_inspection(args)
        if args.json:
            print(json.dumps(result, indent=2, default=str), file=stdout)
        else:
            from catchup_core.views import render  # noqa: PLC0415
            print(render(args.command, result, detail=args.detail), file=stdout)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=stderr)
        return int(ExitCode.BLOCKED)


def _mcp(stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    """Read-only MCP server over stdio, launched on demand by the user's own assistant (no listener)."""
    from pathlib import Path as _Path  # noqa: PLC0415

    from catchup_core.mcp import serve, tool_runner  # noqa: PLC0415
    from catchup_core.runtime import RuntimeConfigError, load_runtime_config  # noqa: PLC0415
    try:
        config = load_runtime_config(_Path(__file__).resolve().parents[2] / "config" / "catchup_core.yaml")
    except RuntimeConfigError as exc:
        print(f"not configured: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)

    def execute(namespace: argparse.Namespace) -> dict[str, Any]:
        namespace.json, namespace.actor, namespace.detail = True, None, False
        namespace.shadow = not config.cutover                    # read what this host actually holds
        return _run_inspection(namespace)
    return serve(stdin, stdout, tool_runner(execute))


def _bundle(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    """Hand-run multi-machine data path (re-artha 6.10): no listener, no replication, records only."""
    import importlib  # noqa: PLC0415
    import json  # noqa: PLC0415
    import socket  # noqa: PLC0415
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from catchup_core.bundles import BundleError, enroll_producer, export_fetch, load_producer_key  # noqa: PLC0415
    from catchup_core.crypto import system_keyring  # noqa: PLC0415
    from catchup_core.runtime import load_runtime_config  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    try:
        backend = system_keyring() if args.command == "bundle-enroll" else importlib.import_module("keyring")
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
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        from catchup_core.runtime import _default_loaders, build_deps, build_fetchers  # noqa: PLC0415
        authority = build_deps(CatchupRequest("bundle_export", config.viewer, "cli", mode=ExecutionMode.SHADOW),
                               inspection=False)
        try:
            authorize_source(authority.ledger, authority.subjects, authority.ledger._consent_subjects,
                             f"{args.source}:{spec['account_scope']}", config.viewer, operations=("ingest", "export"))
            config.subjects = list(authority.subjects)
            config.consent_required = authority.ledger._consent_subjects
            handler_loader, auth_loader = _default_loaders()
            fetch = build_fetchers(config, since, handler_loader, auth_loader,
                                   {args.source: True}, authority.ledger)[args.source]()
        finally:
            authority.ledger.close()
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
        privacy = KeyringSealedStore(backend, "shadow" if args.shadow else "authoritative", cipher=cipher)
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
