#!/usr/bin/env python3
"""Run the test suite against a clean copy of the checkout (re-artha §11.7).

Why: the pre-existing suite reads and writes ``state/``, ``briefings/``,
``tmp/`` and ``backups/`` under the repository root.  Run in place it modifies
live Artha data (observed 2026-10-11: test briefings appended to the real
archive, a test checkpoint left in ``state/``).  This runner copies exactly what
CI would see — tracked files plus untracked-but-not-ignored files, with their
current working-tree content — into a temporary workspace with empty data
directories, and runs pytest there.

Usage: python tools/run_tests_isolated.py [pytest args...]

Local-only private Work evidence (not shared CI or release authority):
    python tools/run_tests_isolated.py --private-work --evidence ABSOLUTE_NEW_JSON
Only the reviewed synthetic producer selection is permitted; its output is suppressed.
This is a cooperative isolated test workspace, not an OS or host sandbox.
"""
from __future__ import annotations

import os
import hashlib
import json
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

MARKER = ".artha-test-workspace"
EMPTY_DIRS = ("state", "briefings", "summaries", "tmp", "backups", "output", "scratch")
# Never copied even when tracked: live data directories keep only their committed templates.
_DATA_PREFIXES = ("briefings/", "summaries/", "tmp/", "backups/", "output/")
_STATE_KEEP = ("state/templates/", "state/learned_procedures/", "state/.gitkeep")
PRIVATE_WORK_TEST = "tests/work/test_work_publication_producers.py"
# Reviewed same-package imports of the synthetic producer selection. No directory copy.
PRIVATE_WORK_FILES = (
    "scripts/work_loop.py", "scripts/work_bootstrap.py", "scripts/work_warm_start.py",
    "scripts/work_domain_writers.py", "scripts/work_reader.py", "scripts/work_notes.py",
    "scripts/work/__init__.py", "scripts/work/helpers.py", "scripts/work/briefing.py",
    "scripts/work/health.py", "scripts/work/decisions.py", "scripts/work/discovery.py",
    "scripts/work/meetings.py", "scripts/work/career.py", "scripts/work/narrative.py",
    "scripts/work/reflect.py", "scripts/work/daily.py", "scripts/work/compaction_manifest.py",
    "scripts/work/reflection_key.py", "scripts/work/sweep.py", "scripts/work/scoring.py",
    "scripts/work/reconcile.py", "scripts/work/ops_dashboard.py", PRIVATE_WORK_TEST,
)


def _no_links(path: Path, *, allow_missing_leaf: bool = False) -> os.stat_result | None:
    leaf = None
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if parent == path and allow_missing_leaf:
                continue
            raise
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("linked path refused")
        if parent == path:
            leaf = info
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("non-directory ancestor")
    return leaf


def _source_hash(root: Path, rel: str) -> str:
    relative = Path(rel)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe source path")
    path = root / relative
    info = _no_links(path, allow_missing_leaf=True)
    if info is None or not stat.S_ISREG(info.st_mode):
        raise ValueError("missing source")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _private_manifest(root: Path) -> dict[str, str]:
    public = [rel for rel in _git_files(root, _git_env()) if _wanted(rel)]
    return {rel: _source_hash(root, rel) for rel in sorted(set(public) | set(PRIVATE_WORK_FILES))}


def _check_sources(root: Path, manifest: dict[str, str]) -> None:
    for rel, digest in manifest.items():
        if _source_hash(root, rel) != digest:
            raise ValueError("changed source")


def _build_private_workspace(root: Path, dest: Path, manifest: dict[str, str]) -> None:
    _check_sources(root, manifest)
    build_workspace(root, dest)
    for rel in PRIVATE_WORK_FILES:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
    _check_sources(root, manifest)
    _check_sources(dest, manifest)
    copied = {path.relative_to(dest).as_posix() for path in dest.rglob("*") if path.is_file()}
    if copied != set(manifest) | {MARKER}:
        raise ValueError("unfingerprinted copied source")


def _outside_sync(path: Path, root: Path) -> None:
    if not path.is_absolute() or ".." in path.parts or str(path).startswith("\\\\"):
        raise ValueError("unsafe local destination")
    resolved = path.resolve()
    if resolved.is_relative_to(root.resolve()):
        raise ValueError("destination inside checkout")
    if any(part.casefold() == "cloudstorage" or part.casefold().startswith("onedrive")
           for part in resolved.parts):
        raise ValueError("synced evidence destination")
    for key, value in os.environ.items():
        if key.casefold() in {"onedrive", "onedriveconsumer", "onedrivecommercial"} and value:
            sync_root = Path(value)
            if not sync_root.is_absolute():
                raise ValueError("invalid sync root")
            if resolved.is_relative_to(sync_root.resolve()):
                raise ValueError("synced evidence destination")


def _evidence_path(path: Path, root: Path) -> Path:
    if path.suffix != ".json" or ":" in path.name:
        raise ValueError("unsafe evidence destination")
    _outside_sync(path, root)
    try:
        info = _no_links(path, allow_missing_leaf=True)
    except FileNotFoundError as exc:
        raise ValueError("missing evidence parent") from exc
    if info is not None:
        raise ValueError("unsafe evidence destination")
    resolved = path.resolve()
    return resolved


def verify_private_work(root: Path, evidence: Path, args: list[str]) -> int:
    """Run only synthetic private tests; never persist or echo pytest output."""
    workspace = None
    record = None
    try:
        evidence = _evidence_path(evidence, root)
        args = args or [PRIVATE_WORK_TEST, "-q", "--tb=short"]
        normalized = [Path(arg).as_posix() for arg in args]
        allowed = {PRIVATE_WORK_TEST, "-q", "--tb=short"}
        if normalized.count(PRIVATE_WORK_TEST) != 1 or any(arg not in allowed for arg in normalized):
            raise ValueError("unreviewed pytest arguments")
        manifest = _private_manifest(root)
        executable = Path(sys.executable)
        record = {
            "schema": "artha.private-work-verification.v1",
            "scope": "local-synthetic-only",
            "isolation": "cooperative-test-workspace-not-host-sandbox",
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "sources": manifest,
            "private_sources": list(PRIVATE_WORK_FILES),
            "pytest_args": args,
            "command": [executable.name, "-m", "pytest", "-p", "no:cacheprovider", *args],
            "runtime": {
                "python_version": sys.version,
                "implementation": platform.python_implementation(),
                "executable_name": executable.name,
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "system": platform.system(), "release": platform.release(),
                "version": platform.version(), "machine": platform.machine(),
            },
            "outcome": "refused",
            "returncode": None,
        }
        workspace_parent = Path(tempfile.gettempdir())
        _outside_sync(workspace_parent, root)
        info = _no_links(workspace_parent)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid private workspace parent")
        workspace = Path(tempfile.mkdtemp(prefix="artha-private-", dir=workspace_parent))
        _outside_sync(workspace, root)
        _no_links(workspace)
        dest = workspace / "checkout"
        _build_private_workspace(root, dest, manifest)
        env = _test_env(workspace)
        # Environment-supplied pytest options would invalidate the exact selection.
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
            cwd=str(dest), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        record["returncode"] = result.returncode
        _check_sources(root, manifest)
        _check_sources(dest, manifest)
        record["outcome"] = "passed" if result.returncode == 0 else "failed"
        exit_code = result.returncode
    except (OSError, ValueError, RuntimeError):
        exit_code = 2
        print("private Work verification refused; no test output disclosed", file=sys.stderr)
    finally:
        if workspace is not None:
            try:
                shutil.rmtree(workspace)
            except OSError:
                exit_code = 2
                if record is not None:
                    record["outcome"] = "cleanup-failed"
                print("private Work workspace cleanup failed; private source may remain in owned workspace",
                      file=sys.stderr)
    if record is not None:
        try:
            record["verification_exitcode"] = exit_code
            record["finished_utc"] = datetime.now(timezone.utc).isoformat()
            _evidence_path(evidence, root)
            with evidence.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True, indent=2)
                stream.write("\n")
        except (OSError, ValueError):
            print("private Work evidence destination refused", file=sys.stderr)
            return 2
    return exit_code


def _git_files(root: Path, env: dict[str, str]) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=str(root), capture_output=True, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.decode(errors='replace').strip()}")
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _wanted(rel: str) -> bool:
    if rel.startswith(_DATA_PREFIXES):
        return rel.endswith(".gitkeep")
    if rel.startswith("state/"):
        return rel.startswith(_STATE_KEEP)
    return not rel.startswith((".git/", ".venv/", ".archive/"))


def build_workspace(root: Path, dest: Path, env: dict[str, str] | None = None) -> int:
    env = env or _git_env()
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for rel in _git_files(root, env):
        if not _wanted(rel):
            continue
        src = root / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied += 1
    for name in EMPTY_DIRS:
        (dest / name).mkdir(exist_ok=True)
    # Same runtime directories the CI workflow creates before the suite.
    for name in ("output/career", "briefings/career"):
        (dest / name).mkdir(parents=True, exist_ok=True)
    (dest / MARKER).write_text("isolated test workspace — safe to delete\n")
    return copied


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    clt = Path("/Library/Developer/CommandLineTools")
    if sys.platform == "darwin" and (clt / "usr/bin/git").exists():
        env.setdefault("DEVELOPER_DIR", str(clt))
    return env


def _test_env(dest: Path) -> dict[str, str]:
    env = _git_env()
    env["ARTHA_NO_REEXEC"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    test_temp = dest / ".pytest-tmp"
    test_temp.mkdir(parents=True, exist_ok=True)
    for key in ("TMP", "TEMP", "TMPDIR"):
        env[key] = str(test_temp)
    return env


def normalize_coverage_xml(report: Path, checkout: Path, origin: Path) -> int:
    tree = ET.parse(report)
    sources = tree.findall("./sources/source")
    if not sources:
        raise ValueError(f"coverage XML has no sources: {report}")
    windows = PureWindowsPath(str(checkout)).is_absolute()
    source_root = PureWindowsPath(str(checkout)) if windows else checkout.resolve()
    replacements = []
    for source in sources:
        if not source.text or not source.text.strip():
            raise ValueError(f"coverage XML has an empty source: {report}")
        path = PureWindowsPath(source.text) if windows else Path(source.text)
        if not path.is_absolute():
            path = source_root / path
        if not windows:
            path = path.resolve()
        relative = path.relative_to(source_root)
        if ".." in relative.parts:
            raise ValueError(f"coverage source escapes checkout: {source.text}")
        replacements.append((source, (origin / Path(*relative.parts)).as_posix()))
    for source, replacement in replacements:
        source.text = replacement
    tree.write(report, encoding="utf-8", xml_declaration=True)
    return len(replacements)


def _xml_reports(args: list[str], checkout: Path) -> list[Path]:
    reports = []
    for index, arg in enumerate(args):
        if arg == "--cov-report" and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith("--cov-report="):
            value = arg.split("=", 1)[1]
        else:
            continue
        if value == "xml" or value.startswith("xml:"):
            path = Path(value[4:] if value.startswith("xml:") else "coverage.xml")
            reports.append(path if path.is_absolute() else checkout / path)
    return list(dict.fromkeys(reports))


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    if "--private-work" in argv:
        if len(argv) < 3 or argv[:2] != ["--private-work", "--evidence"]:
            print("usage: --private-work --evidence ABSOLUTE_NEW_JSON [reviewed pytest args]",
                  file=sys.stderr)
            return 2
        return verify_private_work(root, Path(argv[2]), argv[3:])
    workspace = Path(tempfile.mkdtemp(prefix="artha-ws-"))
    dest = workspace / "checkout"
    try:
        copied = build_workspace(root, dest)
        print(f"isolated workspace: {dest} ({copied} files)", file=sys.stderr)
        args = argv or ["tests", "-q", "--tb=short"]
        env = _test_env(workspace)
        reports = _xml_reports(args, dest)
        before = {path: path.stat() if path.exists() else None for path in reports}
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
            cwd=str(dest), env=env,
        )
        for report in reports:
            if report.exists() and report.stat() != before[report]:
                normalize_coverage_xml(report, dest, root)
        return result.returncode
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
