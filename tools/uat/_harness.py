"""Shared runner for the re-artha UAT scenario scripts (tools/uat/ra_*.py).

Two targets, one command grammar (``catchup_core.cli.parse_args``):

* ``--scratch``: a synthetic workspace under a fresh temporary directory inside ``--workspace``, with private
  storage in a sibling directory and an in-memory credential store.  Commands run through
  ``catchup_core.commands.execute`` with the same dependency wiring, maintenance fence and session lock as
  ``cli._run_inspection``.  Two differences: the workspace root (the real CLI fixes it to its own checkout) and,
  for ledger-writing commands, the host background-footprint check (scheduled tasks) is stubbed as passed because
  a scratch workspace has no scheduler; the ledger integrity and privacy-history checks still run.
  Nothing outside the temporary directory is read or written; no OS keyring, network, connector or model is used.
* ``--workspace PATH`` alone (live): each command is a subprocess ``python -m catchup_core.cli <argv> --json`` with
  ``PATH/scripts`` as the working directory, i.e. the real CLI against that checkout's configuration, storage and
  keyring.  Steps are classified; ``local-write`` steps run only with ``--allow-local-writes``, ``library`` steps
  run only in scratch, and no step has external effects.  Skipped steps are reported and never counted as passes.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(CODE_ROOT / "scripts"))

READ, PREVIEW, LOCAL_WRITE, SCRATCH_ONLY, LIBRARY = "read", "preview", "local-write", "scratch-only", "library"
SHADOW = "--shadow"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
VIEWER = "owner"
HOST = socket.gethostname().split(".")[0]


class FakeKeyring:
    """In-memory credential store: scratch runs never touch the OS keyring."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.store.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.store[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        self.store.pop((service, name), None)


@dataclass
class Outcome:
    rc: int
    data: Any = None
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def shown(self) -> Any:
        return self.data if self.ok else {"rc": self.rc, "stderr": self.stderr}


@dataclass
class Report:
    requirement: str
    title: str
    target: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def check(self, command: str, expected: str, observed: Any, passed: bool, *, kind: str = READ) -> bool:
        self.steps.append({"command": command, "kind": kind, "expected": expected,
                           "observed": _short(observed), "pass": bool(passed)})
        return bool(passed)

    def skip(self, command: str, expected: str, reason: str, *, kind: str) -> None:
        self.steps.append({"command": command, "kind": kind, "expected": expected,
                           "observed": f"skipped: {reason}", "pass": None})

    def result(self) -> dict[str, Any]:
        ran = [s for s in self.steps if s["pass"] is not None]
        return {"requirement": self.requirement, "title": self.title, "target": self.target,
                "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "steps": self.steps, "skipped": len(self.steps) - len(ran),
                "limitations": self.limitations,
                "pass": bool(ran) and all(s["pass"] for s in ran)}


def _short(value: Any, limit: int = 600) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "..."


# ── scratch workspace ─────────────────────────────────────────────────────────

SCRATCH_CONFIG = {
    "sources": {"school_email": {"connector": "gmail", "account_scope": "personal"},
                "calendar": {"connector": "google_calendar", "account_scope": "family"}},
    "subjects": ["kid1", "adult1", "spouse"], "consent_required": ["adult1"], "viewer": VIEWER,
    "authority_host": HOST, "bootstrap_days": 30,
}


class ScratchRunner:
    """Synthetic workspace: ``<tmp>/repo`` (config, state) and ``<tmp>/private`` (ledgers; keys held in memory)."""

    live = False

    def __init__(self, base: Path) -> None:
        import yaml  # noqa: PLC0415
        self.base = base
        self.root = base / "repo"
        self.private = base / "private"
        (self.root / "config").mkdir(parents=True)
        (self.root / "state").mkdir()
        (self.root / "config" / "catchup_core.yaml").write_text(yaml.safe_dump(SCRATCH_CONFIG), encoding="utf-8")
        self.environ = {"ARTHA_LOCAL_DIR": str(self.private)}
        self.keyring = FakeKeyring()
        self.allow_local_writes = True

    def _request(self, mode: str, actor: str | None = None) -> Any:
        from catchup_core.contracts import CatchupRequest, ExecutionMode  # noqa: PLC0415
        return CatchupRequest(f"uat_{os.urandom(4).hex()}", actor or VIEWER, "cli",
                              mode=ExecutionMode(mode), host=HOST)

    def build(self, mode: str = "shadow", **kw: Any) -> Any:
        from catchup_core import runtime  # noqa: PLC0415
        return runtime.build_deps(self._request(mode, kw.pop("actor", None)), repo_root=self.root,
                                  environ=self.environ, keyring_backend=self.keyring,
                                  handler_loader=lambda _name: None, auth_loader=lambda _name: {},
                                  now=lambda: NOW, **kw)

    def initialise(self) -> None:
        """Create both ledgers the way a first run would: the shadow ledger and the authority host's own."""
        for mode, extra in (("shadow", {}), ("authoritative", {"safety_control": True})):
            self.build(mode, **extra).ledger.close()

    def seed(self, fn: Callable[[Any], Any], mode: str = "shadow") -> Any:
        deps = self.build(mode, **({"safety_control": True} if mode == "authoritative" else {}))
        try:
            return fn(deps)
        finally:
            deps.ledger.close()

    def local(self, mode: str = "shadow") -> Path:
        base = self.private / "catchup_core"
        return base / "shadow" if mode == "shadow" else base

    def run(self, argv: Sequence[str]) -> Outcome:
        """The steps of ``cli._run_inspection`` with the scratch workspace as root."""
        from catchup_core import cli, runtime  # noqa: PLC0415
        from catchup_core.commands import execute  # noqa: PLC0415
        from catchup_core.contracts import ExitCode  # noqa: PLC0415
        from catchup_core.runtime import inspection_freshness  # noqa: PLC0415
        from catchup_core.session_lock import session_lock  # noqa: PLC0415
        try:
            args = cli.parse_args([*argv, "--json"])
        except SystemExit:
            return Outcome(int(ExitCode.USAGE_ERROR), stderr="usage error")
        mutations = {"correct", "forget", "feedback", "capture", "ingest", "pause", "resume", "reconcile",
                     "resolve", "complete", "transfer-authority", "retire-authority", "field-review",
                     "cancel-extraction", "configure", "usage"}
        mutation = ((args.command in mutations
                     and (args.command not in {"correct", "complete", "configure"} or args.apply is not None))
                    or (args.command == "review" and args.label is not None))
        controls = {"pause", "forget", "transfer-authority", "retire-authority", "configure"}
        deps = None
        try:
            deps = self.build("shadow" if args.shadow else "authoritative", actor=args.actor, inspection=True,
                              mutation=mutation, safety_control=args.command in controls,
                              configuration_control=args.command == "configure")
            with deps.maintenance(), session_lock(deps.ledger._path.with_suffix(".session.lock")):
                if args.command not in controls:
                    if mutation:
                        runtime._runtime_preflight(deps.ledger, footprint=_scratch_footprint)
                    else:
                        (deps.inspection_preflight or deps.preflight)()
                result = execute(args, deps, self.root)
                result["freshness"] = inspection_freshness(deps.ledger, deps.now())
            return Outcome(0, json.loads(json.dumps(result, default=str)))
        except (OSError, ValueError, RuntimeError) as exc:
            return Outcome(int(ExitCode.BLOCKED), stderr=str(exc))
        finally:
            if deps is not None:
                deps.ledger.close()

    def mcp(self, messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """``cli._mcp`` over the scratch workspace: shadow reads, viewer fixed by configuration."""
        from catchup_core.mcp import serve, tool_runner  # noqa: PLC0415

        def execute(namespace: argparse.Namespace) -> dict[str, Any]:
            outcome = self.run(_namespace_argv(namespace))
            if not outcome.ok:
                raise RuntimeError(outcome.stderr)
            return dict(outcome.data)
        out = io.StringIO()
        serve(io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out, tool_runner(execute))
        return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _scratch_footprint() -> Any:
    """The scratch workspace has no scheduler of its own; the host background-footprint check is not about it."""
    return argparse.Namespace(passed=True, message="scratch workspace: no background scheduler")


def _namespace_argv(ns: argparse.Namespace) -> list[str]:
    argv = [ns.command, "--shadow"]
    if ns.command == "beliefs":
        argv += [*(["--domain", ns.domain] if ns.domain else []), *(["--subject", ns.subject] if ns.subject else [])]
    elif ns.command == "why":
        argv += [ns.fact_id, *(["--as-of", ns.as_of] if ns.as_of else [])]
    elif ns.command == "correct":
        argv += [ns.fact_id, "--value", ns.value]
    elif ns.command == "goals" and getattr(ns, "weekly", False):
        argv.append("--weekly")
    return argv


# ── live workspace ────────────────────────────────────────────────────────────

class LiveRunner:
    """Real CLI subprocesses against an Artha checkout; read-mostly."""

    live = True

    def __init__(self, workspace: Path, allow_local_writes: bool) -> None:
        self.root = workspace
        self.allow_local_writes = allow_local_writes
        if not (workspace / "scripts" / "catchup_core" / "cli.py").is_file():
            raise SystemExit(f"{workspace} is not an Artha checkout (scripts/catchup_core/cli.py missing)")

    def run(self, argv: Sequence[str]) -> Outcome:
        process = subprocess.run([sys.executable, "-m", "catchup_core.cli", *argv, "--json"],  # noqa: S603
                                 cwd=self.root / "scripts", capture_output=True, text=True, timeout=600, check=False)
        try:
            data = json.loads(process.stdout) if process.stdout.strip() else None
        except ValueError:
            data = process.stdout
        return Outcome(process.returncode, data, process.stderr.strip())

    def mcp(self, messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        process = subprocess.run([sys.executable, "-m", "catchup_core.cli", "mcp"],  # noqa: S603
                                 input="".join(json.dumps(m) + "\n" for m in messages),
                                 cwd=self.root / "scripts", capture_output=True, text=True, timeout=600, check=False)
        return [json.loads(line) for line in process.stdout.splitlines() if line.strip()]


# ── entry point ───────────────────────────────────────────────────────────────

Scenario = Callable[[Any, Report], None]


def main(requirement: str, title: str, scenario: Scenario, argv: Sequence[str] | None = None,
         *, setup: Callable[[ScratchRunner], None] | None = None, limitations: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=f"{requirement} UAT: {title}")
    parser.add_argument("--workspace", required=True, type=Path,
                        help="Artha checkout (live), or with --scratch the directory that holds the synthetic one")
    parser.add_argument("--scratch", action="store_true", help="build and use a synthetic workspace; no real data")
    parser.add_argument("--allow-local-writes", action="store_true",
                        help="live only: also run steps that write the local ledger (never external effects)")
    parser.add_argument("--keep", action="store_true", help="scratch only: keep the synthetic workspace")
    parser.add_argument("--output", type=Path, help="also write the JSON report to this file")
    args = parser.parse_args(argv)
    base = None
    if args.scratch:
        args.workspace.mkdir(parents=True, exist_ok=True)
        base = Path(tempfile.mkdtemp(prefix=f"uat_{requirement.lower().replace('-', '')}_",
                                     dir=args.workspace.resolve()))
        runner: Any = ScratchRunner(base)
        report = Report(requirement, title, "scratch")
    else:
        runner = LiveRunner(args.workspace.resolve(), args.allow_local_writes)
        report = Report(requirement, title, "live")
    report.limitations.extend(limitations)
    try:
        if base is not None:
            runner.initialise()
            if setup is not None:
                setup(runner)
        scenario(runner, report)
    except Exception as exc:  # noqa: BLE001 - a crash is a failed step in the report, not a traceback
        report.check("(scenario)", "completes", f"{type(exc).__name__}: {exc}", False)
    finally:
        if base is not None and not args.keep:
            shutil.rmtree(base, ignore_errors=True)
    result = report.result()
    text = json.dumps(result, indent=2, default=str)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if result["pass"] else 1


def gate(runner: Any, report: Report, command: str, expected: str, kind: str) -> bool:
    """True when a step may run on this target; otherwise it is recorded as skipped."""
    if kind == LIBRARY and runner.live:
        report.skip(command, expected, "library check, scratch only (no CLI surface)", kind=kind)
        return False
    if kind == SCRATCH_ONLY and runner.live:
        report.skip(command, expected, "changes or deletes existing owner records; scratch only", kind=kind)
        return False
    if kind == LOCAL_WRITE and runner.live and not runner.allow_local_writes:
        report.skip(command, expected, "writes the local ledger; rerun with --allow-local-writes", kind=kind)
        return False
    return True


def seed_school_fact(deps: Any, *, upstream: str = "m1", text: str = "Registration closes 2026-10-14",
                     value: str = "2026-10-14", subject: str = "kid1", obligation: str = "exam") -> dict[str, str]:
    """One verified Fact with quoted evidence and an Alert, as an earlier Catch-up would have recorded."""
    from catchup_core.ledger import FactInput, SignalInput  # noqa: PLC0415
    led = deps.ledger
    ref = deps.blobs.put(f"mail_{upstream}", text)
    sid, _ = led.upsert_signal(SignalInput("school_email", "personal", upstream, "v1", NOW.isoformat(),
                                           f"h_{upstream}", ref))
    led.set_signal_subjects(sid, [subject])
    fid = led.put_fact(FactInput(domain="kids_school", predicate="exam_registration_deadline", value=value,
                                 primary_subject_id=subject, observed_at=NOW.isoformat(),
                                 evidence=((sid, 0, len(text)),)))
    led.upsert_alert(obligation, domain="kids_school", primary_subject_id=subject, severity="P1", fact_ids=[fid])
    return {"fact_id": fid, "signal_id": sid, "obligation": obligation}


def beliefs(runner: Any, report: Report, *extra: str, expected: str = "exit 0 and a list of Facts",
            check: Callable[[list[dict[str, Any]]], bool] = lambda _b: True) -> list[dict[str, Any]]:
    # Live, `--shadow` reads a separate comparison store, so its Fact IDs are unknown to the live ledger and MCP.
    argv = ["beliefs", *(() if getattr(runner, "live", False) else (SHADOW,)), *extra]
    outcome = runner.run(argv)
    found = outcome.data.get("beliefs") if outcome.ok and isinstance(outcome.data, dict) else None
    report.check(" ".join(argv), expected, outcome.shown(), isinstance(found, list) and check(found))
    return found or []


def first_fact(runner: Any, report: Report, *extra: str) -> dict[str, Any] | None:
    """The Fact a scenario works on: the seeded one in scratch, the first visible one live."""
    found = beliefs(runner, report, *extra, expected="exit 0 and at least one visible Fact",
                    check=lambda rows: bool(rows))
    return found[0] if found else None


def run_step(runner: Any, report: Report, argv: Sequence[str], expected: str,
             check: Callable[[Outcome], bool], *, kind: str = READ) -> Outcome | None:
    """Gate, run one CLI command and record whether ``check`` held."""
    command = " ".join(argv)
    if not gate(runner, report, command, expected, kind):
        return None
    outcome = runner.run(list(argv))
    try:
        passed = bool(check(outcome))
    except (KeyError, TypeError, AttributeError, IndexError):
        passed = False
    report.check(command, expected, outcome.shown(), passed, kind=kind)
    return outcome


def refused(outcome: Outcome, *fragments: str) -> bool:
    return not outcome.ok and all(f.lower() in outcome.stderr.lower() for f in fragments)


def call(ident: int, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


def tool_payload(response: dict[str, Any]) -> tuple[bool, Any]:
    """(isError, decoded text payload) of one MCP tools/call response."""
    result = response.get("result") or {}
    text = (result.get("content") or [{}])[0].get("text", "null")
    return bool(result.get("isError")), json.loads(text)


def text_pdf(text: str) -> bytes:
    """A one-page PDF with an extractable text layer, built by hand (no PDF library needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
               b" /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def contains(path: Path, plaintext: str) -> bool:
    return plaintext.encode() in path.read_bytes()
