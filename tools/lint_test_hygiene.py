#!/usr/bin/env python3
"""Static hygiene gates for tracked tests (AST cross-imports, unapproved sleeps, ticket filenames)."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tests where time.sleep is intrinsic to real child-process concurrency/timeouts
# or already gated behind @pytest.mark.slow.
SLEEP_ALLOWLIST = frozenset({
    "tests/catchup_core/test_authority_transfer_protocol.py",
    "tests/catchup_core/test_checkpoint_restore_integrity.py",
    "tests/catchup_core/test_crypto.py",
    "tests/catchup_core/test_recovery_registry.py",
    "tests/catchup_core/test_renewal_runner.py",
    "tests/catchup_core/test_schema_history.py",
    "tests/catchup_core/test_state_checkpoint.py",
    "tests/catchup_core/test_work_member_mutations.py",
    "tests/catchup_core/test_work_privacy.py",
    "tests/catchup_core/test_work_stream_checkpoint.py",
    "tests/ext_agents/test_fan_out.py",
})

BANNED_FILENAME_RE = re.compile(r"(?:^|_)(?:wave\d*|steal|sprint\d+|debt\d*|phase\d+)(?:_|\.py$)", re.IGNORECASE)
FILENAME_EXCEPTIONS = frozenset({
    "tests/dev/test_lint_debt_ratchet.py",
})
# Ratchet baseline of existing ticket-named files scheduled for Wave 2B dissolution (empty after Wave 2B).
LEGACY_TICKET_FILENAMES = frozenset({
    "tests/unit/test_checkpoint_steal.py",
    "tests/unit/test_checkpoint_wave1.py",
    "tests/unit/test_context_budget_steal.py",
    "tests/unit/test_correction_tracker_steal.py",
    "tests/unit/test_domain_index_wave1.py",
    "tests/unit/test_re_debts_wave1.py",
    "tests/unit/test_re_debts_wave2.py",
    "tests/unit/test_re_debts_wave3.py",
    "tests/unit/test_sprint1_debt.py",
    "tests/unit/test_telemetry_steal.py",
})


def tracked_test_files(root: Path = ROOT) -> list[str]:
    """Return all tracked public Python files under ``tests/`` (excluding ``tests/work/``)."""
    if (root / ".git").exists():
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "tests"],
            capture_output=True,
            check=False,
        )
        if res.returncode == 0:
            return sorted(
                p for p in res.stdout.decode("utf-8", "surrogateescape").split("\0")
                if p and p.endswith(".py") and "work" not in Path(p).parts
            )
    return sorted(
        p.relative_to(root).as_posix()
        for p in (root / "tests").rglob("*.py")
        if "work" not in p.parts and "__pycache__" not in p.parts
    )


def _has_slow_marker(node: ast.AST) -> bool:
    for deco in getattr(node, "decorator_list", ()):
        if isinstance(deco, ast.Attribute) and deco.attr == "slow":
            return True
    return False


def _walk_non_slow_calls(node: ast.AST):
    """Yield Call AST nodes in ``node`` without descending into ``@pytest.mark.slow`` subtrees."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and _has_slow_marker(child):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _walk_non_slow_calls(child)


def scan_hygiene(root: Path = ROOT, *, enforce_filenames: bool = True, enforce_sleeps: bool = True) -> list[str]:
    """Return human-readable hygiene violation strings."""
    errors: list[str] = []
    for rel in tracked_test_files(root):
        path = root / rel
        if not path.exists():
            continue
        if (
            enforce_filenames
            and rel not in FILENAME_EXCEPTIONS
            and rel not in LEGACY_TICKET_FILENAMES
            and Path(rel).name.startswith("test_")
            and BANNED_FILENAME_RE.search(Path(rel).name)
        ):
            errors.append(f"{rel}: ticket/wave/steal/sprint/debt/phase test filename is forbidden")

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{rel}: cannot parse ({exc})")
            continue

        file_is_slow = False
        for top in tree.body:
            if isinstance(top, ast.Assign):
                for tgt in top.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "pytestmark":
                        src_seg = ast.unparse(top.value) if hasattr(ast, "unparse") else ""
                        if "slow" in src_seg:
                            file_is_slow = True

        # 1. Cross-test imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    leaf = alias.name.split(".")[-1]
                    if leaf.startswith("test_") or ".test_" in alias.name:
                        errors.append(f"{rel}:{node.lineno}: cross-test import 'import {alias.name}' forbidden")
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if any(pt.startswith("test_") for pt in parts) or (
                    node.module == "tests" and any(a.name.startswith("test_") for a in node.names)
                ):
                    errors.append(f"{rel}:{node.lineno}: cross-test import 'from {node.module} import ...' forbidden")

        # 2. Unapproved time.sleep outside @pytest.mark.slow / SLEEP_ALLOWLIST
        if enforce_sleeps and rel not in SLEEP_ALLOWLIST and not file_is_slow:
            for sub in _walk_non_slow_calls(tree):
                fn = sub.func
                is_sleep = (isinstance(fn, ast.Attribute) and fn.attr == "sleep") or (
                    isinstance(fn, ast.Name) and fn.id == "sleep"
                )
                if is_sleep:
                    errors.append(f"{rel}:{sub.lineno}: time.sleep() outside @pytest.mark.slow / allowlist")
    return errors


def main(argv: list[str]) -> int:
    enforce_filenames = "--no-filename-check" not in argv
    enforce_sleeps = "--no-sleep-check" not in argv
    violations = scan_hygiene(ROOT, enforce_filenames=enforce_filenames, enforce_sleeps=enforce_sleeps)
    if violations:
        print("Test hygiene violations:\n" + "\n".join(f"  {v}" for v in violations), file=sys.stderr)
        return 1
    print(f"OK: {len(tracked_test_files(ROOT))} test files clean (cross-imports, sleeps, filenames)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
