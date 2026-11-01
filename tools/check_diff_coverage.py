"""Apply diff-cover's threshold, refusing empty matches for changed Python lines."""
from __future__ import annotations

import argparse
import sys

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
    args = parser.parse_args(argv)
    GitPathTool.set_cwd(None)
    diff = GitDiffTool(args.diff_range_notation, False)
    percent, count = generate_coverage_report(
        [args.report], args.compare_branch, diff, report_formats={}, include=args.include,
    )
    if not count:
        reporter = GitDiffReporter(args.compare_branch, diff, include=args.include)
        if any(path.endswith(".py") and reporter.lines_changed(path)
               for path in reporter.src_paths_changed()):
            print("ERROR: changed Python lines have no matching coverage in the report.", file=sys.stderr)
            return 1
    if percent < args.fail_under:
        print(f"Failure. Coverage is below {args.fail_under:g}%.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))