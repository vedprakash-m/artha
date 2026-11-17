#!/usr/bin/env python3
"""Run mutmut inside a disposable isolated workspace on core decision modules."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = (
    "catchup_core/policy.py",
    "catchup_core/coordinator.py",
    "catchup_core/triage.py",
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_tests_isolated", ROOT / "tools" / "run_tests_isolated.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_results(output: str) -> dict[str, str]:
    """Parse ``mutmut results --all true`` output into {mutant_id: status}."""
    statuses: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        mutant_id, status = line.rsplit(":", 1)
        mutant_id = mutant_id.strip()
        status = status.strip().lower()
        if ".x" in mutant_id and status:
            statuses[mutant_id] = status
    return statuses


def compare_baselines(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Return mutants killed in ``before`` that are no longer killed in ``after``."""
    lost: list[str] = []
    for mutant_id, prev_status in sorted(before.items()):
        if prev_status == "killed" and mutant_id in after and after[mutant_id] != "killed":
            lost.append(f"{mutant_id}: killed -> {after[mutant_id]}")
    return lost


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Write mutant status map JSON here")
    parser.add_argument("--baseline", help="Compare against baseline JSON and fail on lost kills")
    parser.add_argument("--max-children", type=int, default=4)
    args = parser.parse_args(argv)

    runner = _load_runner()
    with tempfile.TemporaryDirectory(prefix="artha-mutmut-") as tmp:
        ws = Path(tmp)
        checkout = ws / "checkout"
        runner.build_workspace(ROOT, checkout, synthetic_only=True)
        # Mutmut clones into checkout/mutants; keep tests in checkout pointing cleanly at mutants.
        shutil.copytree(checkout / "scripts" / "catchup_core", checkout / "catchup_core")
        conftest = checkout / "tests" / "conftest.py"
        conftest.write_text(
            conftest.read_text(encoding="utf-8")
            + "\nif _PROJECT_ROOT in sys.path: sys.path.remove(_PROJECT_ROOT)\nsys.path.insert(0, _PROJECT_ROOT)\n",
            encoding="utf-8",
        )
        pyproject = checkout / "pyproject.toml"
        target_rx = re.compile(
            r"catchup_core\.(?:policy|coordinator|triage)\b|"
            r"from\s+catchup_core\s+import\b[^#\n]*\b(?:policy|coordinator|triage)\b"
        )
        excluded_files = {
            "test_renewal_e2e.py",
            "test_renewal_runtime.py",
            "test_release_evidence_tools.py",
            "test_release_boundaries.py",
            "test_release_acceptance_v4.py",
            "test_release_artifact_coherence.py",
            "test_release_regressions.py",
            "test_runtime.py",
            "test_runtime_closure.py",
            "test_runtime_paths.py",
            "test_state_checkpoint_cli.py",
            "test_quarantine_restore.py",
            "test_quarantine_review.py",
            "test_recovery_quarantine.py",
            "test_recovery_quarantine_cli.py",
            "test_recovery_access.py",
            "test_work_stream_recovery.py",
        }
        test_files = sorted(
            f"tests/catchup_core/{p.name}"
            for p in (checkout / "tests" / "catchup_core").glob("test_*.py")
            if p.name not in excluded_files
            and target_rx.search(p.read_text(encoding="utf-8", errors="ignore"))
        )
        cfg_block = (
            '\n[tool.mutmut]\n'
            'source_paths = ["catchup_core"]\n'
            'only_mutate = [\n'
            + "".join(f'    "{t}",\n' for t in DEFAULT_TARGETS)
            + ']\n'
            'do_not_mutate = ["*__init__.py", "*capabilities.py"]\n'
            'also_copy = ["scripts", "tools", "config", "docs", "prompts", "state", "briefings", "artha.py"]\n'
            'pytest_add_cli_args = ["-p", "no:cacheprovider", "-m", "not slow"]\n'
            f'pytest_add_cli_args_test_selection = {json.dumps(test_files)}\n'
        )
        pyproject.write_text(pyproject.read_text(encoding="utf-8") + cfg_block, encoding="utf-8")

        env = runner._test_env(ws)
        env["ARTHA_ALLOW_INPLACE_TESTS"] = "1"
        env["TEMP"] = str(ws)
        env["PYTHONPATH"] = str(checkout / "scripts")
        mutmut_bin = str(Path(sys.executable).parent / "mutmut")
        if not Path(mutmut_bin).exists():
            mutmut_bin = "mutmut"

        run_proc = subprocess.run(
            [mutmut_bin, "run", "--max-children", str(args.max_children)],
            cwd=str(checkout), env=env, text=True, check=False,
        )
        if run_proc.returncode not in (0,):
            print(f"mutmut run exited with {run_proc.returncode}", file=sys.stderr)
            return run_proc.returncode or 1

        res_proc = subprocess.run(
            [mutmut_bin, "results", "--all", "true"],
            cwd=str(checkout), env=env, capture_output=True, text=True, check=True,
        )
        statuses = parse_results(res_proc.stdout)
        killed = sum(1 for s in statuses.values() if s == "killed")
        print(f"Mutants evaluated: {len(statuses)}, killed: {killed}")

        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(statuses, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.baseline:
            before = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            lost = compare_baselines(before, statuses)
            if lost:
                print("Lost mutant kills:\n" + "\n".join(f"  {item}" for item in lost), file=sys.stderr)
                return 1
            print(f"OK: 0 lost kills across {len(set(before) & set(statuses))} shared mutants")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
