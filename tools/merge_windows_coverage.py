#!/usr/bin/env python3
"""Verify sharded Windows contract manifests and merge shard .coverage files into XML."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent


def normalize_coverage_db(db_path: Path, repo_root: Path) -> int:
    """Rewrite isolated checkout paths inside a ``coverage`` SQLite DB to ``repo_root``."""
    updated = 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id, path FROM file").fetchall()
        for row_id, raw_path in rows:
            parts = PureWindowsPath(raw_path.replace("\\", "/")).parts if ":" in raw_path or "\\" in raw_path else Path(raw_path).parts
            if "checkout" in parts:
                rel_parts = parts[parts.index("checkout") + 1:]
            elif "scripts" in parts:
                rel_parts = parts[parts.index("scripts"):]
            else:
                continue
            new_path = str((repo_root / Path(*rel_parts)).resolve())
            if new_path != raw_path:
                conn.execute("UPDATE file SET path = ? WHERE id = ?", (new_path, row_id))
                updated += 1
        conn.commit()
    return updated


def verify_shards(
    manifests: list[dict],
    shard_nodeids: list[set[str]],
    expected_union: set[str],
    *,
    commit: str,
    run_id: str,
    run_attempt: str,
) -> None:
    """Verify exact CI identity, non-emptiness, pairwise disjointness, and complete union."""
    if len(manifests) != len(shard_nodeids) or not manifests:
        raise ValueError("Missing shard manifests or nodeids")
    seen_shards: set[int] = set()
    union: set[str] = set()
    for manifest, nodes in zip(manifests, shard_nodeids, strict=True):
        if manifest.get("commit") != commit or str(manifest.get("run_id")) != str(run_id) or str(manifest.get("run_attempt")) != str(run_attempt):
            raise ValueError(f"Shard manifest provenance mismatch: {manifest}")
        shard = int(manifest["shard"])
        seen_shards.add(shard)
        if not nodes:
            raise ValueError(f"Windows shard {shard} executed zero tests")
        overlap = union & nodes
        if overlap:
            raise ValueError(f"Windows shards overlap on {len(overlap)} tests (e.g. {next(iter(overlap))})")
        union |= nodes
    if seen_shards != set(range(1, len(manifests) + 1)):
        raise ValueError(f"Incomplete shard set: {sorted(seen_shards)}")
    missing = expected_union - union
    extra = union - expected_union
    if missing or extra:
        raise ValueError(
            f"Windows shard union != unsharded collection (missing={len(missing)}, extra={len(extra)})"
        )


def _load_and_verify_group(
    shards_root: Path,
    shards: int,
    artifact_prefix: str,
    *,
    commit: str,
    run_id: str,
    run_attempt: str,
    collect_args: list[str],
    targets: list[str],
) -> list[Path]:
    """Load, prove disjoint+complete, and return `.coverage` paths for one shard group."""
    manifests: list[dict] = []
    shard_nodes: list[set[str]] = []
    cov_files: list[Path] = []
    for idx in range(1, shards + 1):
        sub = shards_root / f"{artifact_prefix}-{commit}-{run_id}-{run_attempt}-shard-{idx}"
        flat_names = ("manifest.json", f"shard-{idx}.nodeids", f"shard-{idx}.coverage")
        if shards == 1 and not sub.exists() and all((shards_root / name).is_file() for name in flat_names):
            sub = shards_root
        manifest_path = sub / "manifest.json"
        nodes_path = sub / f"shard-{idx}.nodeids"
        cov_path = sub / f"shard-{idx}.coverage"
        for required in (manifest_path, nodes_path, cov_path):
            if not required.is_file():
                raise FileNotFoundError(f"Missing shard artifact file: {required}")
        manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        shard_nodes.append({line.strip() for line in nodes_path.read_text(encoding="utf-8").splitlines() if line.strip()})
        cov_files.append(cov_path)

    collect = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q",
         *collect_args, *targets],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    expected = {
        line.strip() for line in collect.stdout.splitlines()
        if "::" in line and not line.lstrip().startswith(("[", "=", "-", "<"))
    }
    verify_shards(
        manifests, shard_nodes, expected,
        commit=commit, run_id=run_id, run_attempt=run_attempt,
    )
    return cov_files


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards-dir", required=True)
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--artifact-prefix", default="win-cov")
    parser.add_argument("--extra-group", nargs=4, action="append", default=[],
                        metavar=("PREFIX", "SHARDS", "DIR", "TARGETS_COMMA"))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output-xml", required=True)
    parser.add_argument("--include-target", action="append", default=[])
    parser.add_argument("--collect-arg", action="append", default=[])
    parser.add_argument("--rcfile", default=None)
    parser.add_argument("--fail-under", type=float, default=None)
    parser.add_argument("targets", nargs="+")
    args = parser.parse_args(argv)

    cov_files = _load_and_verify_group(
        Path(args.shards_dir), args.shards, args.artifact_prefix,
        commit=args.commit, run_id=args.run_id, run_attempt=args.run_attempt,
        collect_args=args.collect_arg, targets=args.targets,
    )
    for prefix, shards_str, sdir, targets_csv in args.extra_group:
        cov_files.extend(_load_and_verify_group(
            Path(sdir), int(shards_str), prefix,
            commit=args.commit, run_id=args.run_id, run_attempt=args.run_attempt,
            collect_args=args.collect_arg, targets=[t for t in targets_csv.split(",") if t],
        ))

    with tempfile.TemporaryDirectory(prefix="artha-win-cov-") as tmp:
        work = Path(tmp)
        for i, src in enumerate(cov_files, start=1):
            dst = work / f".coverage.shard{i}"
            dst.write_bytes(src.read_bytes())
            normalize_coverage_db(dst, ROOT)
        env = {**os.environ, "COVERAGE_FILE": str(work / ".coverage")}
        rc_args = [f"--rcfile={Path(args.rcfile).resolve()}"] if args.rcfile else []
        subprocess.run([sys.executable, "-m", "coverage", "combine", *rc_args], cwd=str(work), env=env, check=True)
        if args.fail_under is not None:
            rep_cmd = [sys.executable, "-m", "coverage", "report", *rc_args, f"--fail-under={args.fail_under:g}"]
            if args.include_target:
                rep_cmd.append("--include=" + ",".join(args.include_target))
            subprocess.run(rep_cmd, cwd=str(ROOT), env=env, check=True)
        out_xml = Path(args.output_xml).resolve()
        xml_cmd = [sys.executable, "-m", "coverage", "xml", *rc_args, "-o", str(out_xml)]
        if args.include_target:
            xml_cmd.append("--include=" + ",".join(args.include_target))
        subprocess.run(xml_cmd, cwd=str(ROOT), env=env, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
