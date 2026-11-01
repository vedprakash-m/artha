"""WP-19 UAT (scratch only): the legacy Work cache migration and health receipt, rows 2-5.

Runs the real ``catchup_core.work_cache.main`` (the ``plan`` / ``apply`` command) against a synthetic legacy
plaintext cache and a throwaway age key in a fresh temporary directory inside ``--workspace``:

  work_cache plan                                          JSON with bytes and sha256 only; no cache text
  work_cache apply --sha256 0000                           exit 20; legacy file unchanged; no archive
  work_cache apply --sha256 <digest>                       exit 20 (writers not confirmed stopped); unchanged
  work_cache apply --sha256 <digest> --writers-stopped     exit 0; encrypted archive decrypts to the original;
                                                           legacy replaced by a read-only retirement marker
  (same command again)                                     exit 0; archive unchanged (safe to repeat)
  load / plan after retirement                             archive is not fresh cache; plan refuses
  preflight WorkIQ checks with synthetic probe results     real receipt writer; no identity or probe output stored

Application data stays in the synthetic workspace. The Work journal is forced to "not enrolled"; the health
receipt uses a scratch path override, fake registry and probe, scrubbed environment and blocked network/processes.
There is no live mode; the owner runs the sheet's commands for live evidence.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

REQUIREMENT, TITLE = "WP-19", "Legacy Work cache migration and synthetic health receipt"
NARRATIVE = "synthetic work narrative"
LEGACY = json.dumps({"uat_synthetic_scope": {"data": [NARRATIVE]}}).encode()
BLOCKED = "Work cache migration blocked; review storage, digest and stopped-writer prerequisites."
RETIRED = "Legacy Work cache archived and retired; prior synced copies still require retention review."
LIMITATIONS = (
    "Row 5 exercises real preflight receipt writers with a synthetic probe and scratch path override; "
    "it is not owner UAT and does not establish that a Work briefing or a live provider produced a receipt.",
    "Synthetic data and a throwaway age key only; the live migration stays an owner step.",
)

_PROBE_NAME = "Synthetic Person"
_PROBE_MAIL = "synthetic.person@example.invalid"
_PROBE_TEXT = f"{_PROBE_NAME} {_PROBE_MAIL} SYNTHETIC_PROBE_ONLY"
_PROBE_STDERR = "SYNTHETIC_PROBE_ERROR"


class _NotEnrolled:
    def is_enrolled(self) -> bool:
        return False


def _external_blocked(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("scratch health receipt cannot access processes or network")


def _registry_blocked(*_args: Any, **_kwargs: Any) -> None:
    raise OSError("scratch health receipt cannot access the registry")


def health_receipt_scenario(base: Path, report: h.Report) -> None:
    """Run both real preflight checks through the real receipt writer with inert inputs."""
    root = base / "health"
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    env = {"HOME": str(root), "USERPROFILE": str(root), "PATH": "",
           "ARTHA_NO_REEXEC": "1", "ARTHA_DIR": str(repo)}
    registry = SimpleNamespace(HKEY_LOCAL_MACHINE=object(), HKEY_CURRENT_USER=object(), OpenKey=_registry_blocked)
    keyring = SimpleNamespace(get_password=_external_blocked, set_password=_external_blocked,
                              delete_password=_external_blocked, get_keyring=_external_blocked)
    cases = (("authenticated", 0, _PROBE_TEXT, True, True),
             ("auth-failed", 0, f"error: {_PROBE_TEXT}", True, False),
             ("unavailable", 1, _PROBE_TEXT, False, False))

    with contextlib.ExitStack() as guard:
        guard.enter_context(patch.dict(os.environ, env, clear=True))
        guard.enter_context(patch.dict(sys.modules, {"winreg": registry, "keyring": keyring}))
        guard.enter_context(patch("platform.system", return_value="Windows"))
        guard.enter_context(patch("shutil.which", return_value="synthetic-npx"))
        for target in ("subprocess.Popen", "socket.socket", "socket.fromfd",
                       "socket.getaddrinfo", "socket.gethostbyname", "socket.create_connection"):
            guard.enter_context(patch(target, side_effect=_external_blocked))

        spec = importlib.util.spec_from_file_location("_wp19_scratch_preflight", h.CODE_ROOT / "scripts" / "preflight.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("preflight script unavailable")
        script = importlib.util.module_from_spec(spec)
        guard.enter_context(patch.dict(sys.modules, {spec.name: script}))
        spec.loader.exec_module(script)
        from lib.workiq_health import read_receipt  # noqa: PLC0415
        from preflight import integration_checks  # noqa: PLC0415

        for entry, checks in (("preflight.py", script), ("preflight package", integration_checks)):
            for case, returncode, stdout, available, auth_valid in cases:
                path = root / entry / case / "work" / "cache" / "health.json"
                before = datetime.now(timezone.utc)
                response = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=_PROBE_STDERR)
                with (patch.object(checks, "ARTHA_DIR", str(repo)),
                      patch.object(checks, "WORKIQ_CACHE_FILE", str(path)),
                      patch.object(checks, "_SUBPROCESS_ENV", {"PATH": ""}),
                      patch("subprocess.run", return_value=response) as probe):
                    result = checks.check_workiq()
                    cached = checks.check_workiq()
                after = datetime.now(timezone.utc)
                try:
                    text = path.read_text(encoding="utf-8")
                    payload = json.loads(text)
                    if not isinstance(payload, dict):
                        payload = {}
                    checked_at = datetime.fromisoformat(payload["checked_at"])
                    timestamp_ok = (checked_at.utcoffset() == timezone.utc.utcoffset(checked_at)
                                    and before <= checked_at <= after)
                except (OSError, ValueError, TypeError, KeyError):
                    text, payload, timestamp_ok = "", {}, False
                shape_ok = (set(payload) == {"available", "auth_valid", "platform", "checked_at"}
                            and type(payload.get("available")) is bool and type(payload.get("auth_valid")) is bool
                            and payload.get("platform") == "Windows")
                flags_ok = payload.get("available") is available and payload.get("auth_valid") is auth_valid
                call = probe.call_args
                probe_ok = (probe.call_count == 1 and call is not None
                            and call.args[0] == ["synthetic-npx", "-y", f"@microsoft/workiq@{checks.WORKIQ_VERSION_PIN}",
                                                 "ask", "-q", "What is my name?"]
                            and call.kwargs.get("env") == {"PATH": ""})
                cache_ok = (read_receipt(path, max_age=86400) == payload and "cached" in cached.message.lower()
                            and result.passed is (available and auth_valid)
                            and cached.passed is (available and auth_valid))
                visible = "\n".join((text, result.message, result.fix_hint, cached.message, cached.fix_hint))
                private_absent = not any(token in visible for token in (_PROBE_NAME, _PROBE_MAIL,
                                                                        "SYNTHETIC_PROBE_ONLY", _PROBE_STDERR))
                observed = {"schema": shape_ok, "flags": flags_ok, "utc_timestamp": timestamp_ok,
                            "one_synthetic_probe": probe_ok, "cached": cache_ok, "probe_output_absent": private_absent}
                report.check(f"{entry} WorkIQ health receipt ({case})",
                             "real writer, exact technical fields and UTC time; probe output absent; cached reuse",
                             observed, all(observed.values()), kind=h.LOCAL_WRITE)


def _age_keygen() -> str | None:
    found = shutil.which("age-keygen")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/age-keygen", "/usr/local/bin/age-keygen"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _call(wc: Any, cache: Any, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    original = wc.WorkCache
    wc.WorkCache = lambda: cache
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wc.main(argv)
    finally:
        wc.WorkCache = original
    return code, out.getvalue().strip(), err.getvalue().strip()


def _no_plaintext(root: Path) -> bool:
    return all(NARRATIVE.encode() not in p.read_bytes() for p in root.rglob("*") if p.is_file())


def scenario(base: Path, report: h.Report) -> None:
    from catchup_core import work_cache as wc  # noqa: PLC0415
    from catchup_core.vault import AgeVault  # noqa: PLC0415

    keygen = _age_keygen()
    if not keygen:
        report.check("age-keygen", "available for a throwaway key", "missing", False)
        return
    report.check("age-keygen", "available for a throwaway key", keygen, True)
    identity = subprocess.run([keygen], capture_output=True, check=True).stdout
    recipient = subprocess.run([keygen, "-y"], input=identity, capture_output=True, check=True).stdout.decode().strip()
    root = base / "local" / "work" / "cache"
    legacy = base / "repo" / "tmp" / ".workiq_cache.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(LEGACY)
    cache = wc.WorkCache(root, legacy=legacy,
                         vault=AgeVault(root, identity=lambda: identity.decode(), recipient=recipient))
    digest = hashlib.sha256(LEGACY).hexdigest()
    archive = root / f"legacy-{digest}.json.age"

    code, out, err = _call(wc, cache, ["plan"])
    plan = json.loads(out) if code == 0 else {}
    report.check("work_cache plan", "exit 0; JSON {bytes, sha256} of the legacy file; no cache text",
                 {"exit": code, "plan": plan},
                 code == 0 and plan == {"bytes": len(LEGACY), "sha256": digest} and NARRATIVE not in out + err)

    for argv, label in ((["apply", "--sha256", "0000"], "work_cache apply --sha256 0000"),
                        (["apply", "--sha256", digest], "work_cache apply --sha256 <digest> (no --writers-stopped)")):
        code, out, err = _call(wc, cache, argv)
        report.check(label, f"exit 20, '{BLOCKED}'; legacy unchanged; no archive",
                     {"exit": code, "stderr": err},
                     code == 20 and err == BLOCKED and legacy.read_bytes() == LEGACY and not archive.exists(),
                     kind=h.LOCAL_WRITE)

    command = "work_cache apply --sha256 <digest> --writers-stopped"
    code, out, err = _call(wc, cache, ["apply", "--sha256", digest, "--writers-stopped"])
    marker = json.loads(legacy.read_bytes()) if code == 0 else None
    read_only = not legacy.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    restored = archive.exists() and cache.vault.read_artifact(archive).encode() == LEGACY
    report.check(command, f"exit 0, '{RETIRED}'; archive decrypts to the original; legacy is a read-only "
                 "retirement marker; no plaintext under the cache root",
                 {"exit": code, "stdout": out, "marker_keys": sorted(marker or {}), "read_only": read_only,
                  "archive_matches": restored},
                 code == 0 and out == RETIRED and isinstance(marker, dict) and marker.get("sha256") == digest
                 and NARRATIVE.encode() not in legacy.read_bytes() and read_only and restored
                 and _no_plaintext(root), kind=h.LOCAL_WRITE)

    if sys.platform == "win32" and archive.exists():
        from catchup_core.quarantine_fs_windows import open_owner_private_path  # noqa: PLC0415
        try:
            with open_owner_private_path(archive) as opened:
                private = opened.snapshot().size == archive.stat().st_size
        except Exception as exc:  # noqa: BLE001 - recorded as a failed step
            private = False
            report.check("archive DACL (Windows)", "owner-private", type(exc).__name__, False)
        else:
            report.check("archive DACL (Windows)", "owner-private", private, private)

    before = archive.read_bytes() if archive.exists() else b""
    code, out, err = _call(wc, cache, ["apply", "--sha256", digest, "--writers-stopped"])
    report.check(f"{command} (again)", "exit 0; archive unchanged (safe to repeat)", {"exit": code, "stdout": out},
                 code == 0 and out == RETIRED and archive.exists() and archive.read_bytes() == before,
                 kind=h.LOCAL_WRITE)

    entries = cache.load()
    code, _, err = _call(wc, cache, ["plan"])
    report.check("load, then work_cache plan after retirement",
                 "load returns no entries (archive is not fresh cache); plan exit 20",
                 {"entries": len(entries), "plan_exit": code}, entries == {} and code == 20 and err == BLOCKED)


def _remove(base: Path) -> None:
    def writable(function: Any, path: str, _exc: Any) -> None:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(base, onexc=writable)
    else:
        shutil.rmtree(base, onerror=writable)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{REQUIREMENT} UAT: {TITLE}")
    parser.add_argument("--workspace", required=True, type=Path,
                        help="directory that holds the temporary synthetic workspace")
    parser.add_argument("--scratch", action="store_true", help="required: synthetic data and a throwaway key")
    parser.add_argument("--keep", action="store_true", help="keep the synthetic workspace")
    parser.add_argument("--output", type=Path, help="also write the JSON report to this file")
    args = parser.parse_args(argv)
    if not args.scratch:
        print("WP-19 UAT runs only with --scratch; for the live Work cache run the sheet's "
              "`python -m catchup_core.work_cache` commands yourself.", file=sys.stderr)
        return 2
    from lib import work_publication  # noqa: PLC0415

    args.workspace.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="uat_wp19_", dir=args.workspace.resolve()))
    report = h.Report(REQUIREMENT, TITLE, "scratch")
    report.limitations.extend(LIMITATIONS)
    previous = work_publication.set_protocol_factory(_NotEnrolled)
    try:
        health_receipt_scenario(base, report)
        scenario(base, report)
    except Exception as exc:  # noqa: BLE001 - a crash is a failed step in the report, not a traceback
        report.check("(scenario)", "completes", f"{type(exc).__name__}: {exc}", False)
    finally:
        work_publication.set_protocol_factory(previous)
        if not args.keep:
            _remove(base)
    result = report.result()
    text = json.dumps(result, indent=2, default=str)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
