"""Apply diff-cover's threshold, refusing empty matches for changed Python lines."""
from __future__ import annotations

import argparse
import fnmatch
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from diff_cover.diff_cover_tool import generate_coverage_report
from diff_cover.diff_reporter import GitDiffReporter
from diff_cover.git_diff import GitDiffTool
from diff_cover.git_path import GitPathTool


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--compare-branch", default="origin/main")
    parser.add_argument("--diff-range-notation", default="...")
    parser.add_argument("--include", action="append")
    parser.add_argument("--fail-under", type=float, required=True)
    parser.add_argument("--per-file-include", action="append", default=[])
    parser.add_argument("--per-file-fail-under", type=float, default=95.0)
    args = parser.parse_args(argv)
    GitPathTool.set_cwd(None)
    diff = GitDiffTool(args.diff_range_notation, False)
    percent, _count = generate_coverage_report(
        [args.report], args.compare_branch, diff, report_formats={}, include=args.include,
    )
    reporter = GitDiffReporter(args.compare_branch, diff, include=args.include)
    measured = _report_files(args.report)
    unmatched = [
        path for path in reporter.src_paths_changed()
        if path.endswith(".py") and reporter.lines_changed(path)
        and (Path.cwd() / path).resolve() not in measured
    ]
    if unmatched:
        # A changed file the report does not contain means the report and the diff disagree on paths;
        # fail closed even when other changed files matched.
        print("ERROR: changed Python lines have no matching coverage in the report: "
              + ", ".join(sorted(unmatched)), file=sys.stderr)
        return 1
    if percent < args.fail_under:
        print(f"Failure. Coverage is below {args.fail_under:g}%.", file=sys.stderr)
        return 1
    if args.per_file_include:
        per_reporter = GitDiffReporter(args.compare_branch, diff, include=args.per_file_include)
        per_unmatched = [
            path for path in per_reporter.src_paths_changed()
            if path.endswith(".py") and per_reporter.lines_changed(path)
            and (Path.cwd() / path).resolve() not in measured
        ]
        if per_unmatched:
            print("ERROR: changed per-file-gated Python lines have no matching coverage in the report: "
                  + ", ".join(sorted(per_unmatched)), file=sys.stderr)
            return 1
        for path in sorted(per_reporter.src_paths_changed()):
            if not path.endswith(".py") or not per_reporter.lines_changed(path):
                continue
            norm = path.replace("\\", "/")
            if not any(fnmatch.fnmatchcase(norm, pat) for pat in args.per_file_include):
                continue
            file_pct, file_count = generate_coverage_report(
                [args.report], args.compare_branch, diff, report_formats={}, include=[norm],
            )
            if file_count > 0 and file_pct < args.per_file_fail_under:
                print(
                    f"Failure. Per-file diff coverage for {norm} is {file_pct:.2f}% "
                    f"(below {args.per_file_fail_under:g}%).",
                    file=sys.stderr,
                )
                return 1
    return 0


def _report_files(report: str) -> set[Path]:
    """Absolute paths of every file a Cobertura coverage report measures."""
    root = ET.parse(report).getroot()
    sources = [Path(node.text) for node in root.iter("source") if node.text]
    files = set()
    for node in root.iter("class"):
        name = node.get("filename")
        if name:
            files.update((source / name).resolve() for source in sources or [Path.cwd()])
    return files


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))