#!/usr/bin/env python3
# pii-guard: ignore-file — orchestrator only; no personal data stored here
"""scripts/post_catchup_memory.py — Deterministic post-catch-up memory pipeline.

Runs after briefing delivery (Step 21). Creates session summary from the
latest briefing, extracts facts directly from the briefing file, updates
the self-model.  Non-blocking — catch-up completes regardless of errors.

Usage:
    python3 scripts/post_catchup_memory.py --briefing briefings/YYYY-MM-DD.md
    python3 scripts/post_catchup_memory.py --briefing briefings/YYYY-MM-DD.md --dry-run
    python3 scripts/post_catchup_memory.py --rebuild-self-model
    python3 scripts/post_catchup_memory.py --discover   # bootstrap / manual only

Outputs one line to stdout:
    [run_id] N facts persisted (M extracted), self-model [updated|unchanged]

Also appends a structured JSON record to state/memory_pipeline_runs.jsonl
for health-check reporting and consecutive-zero-fact alerting.

Config flag: harness.agentic.post_catchup_memory.enabled (default: true)

Ref: specs/mem.md Phase 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ is on sys.path (script invoked from project root)
_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_id() -> str:
    """Generate a unique run identifier (mem-YYYYMMDD-HHMMSS)."""
    return datetime.now(tz=timezone.utc).strftime("mem-%Y%m%d-%H%M%S")


def _is_enabled(artha_dir: Path) -> bool:
    """Check config flag harness.agentic.post_catchup_memory.enabled."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config")
        return bool(
            cfg.get("harness", {})
            .get("agentic", {})
            .get("post_catchup_memory", {})
            .get("enabled", True)
        )
    except Exception:  # noqa: BLE001
        return True  # default on when config is unreadable


def _next_session_n(artha_dir: Path) -> int:
    """Return the next session sequence number for tmp/session_history_N."""
    import glob  # noqa: PLC0415
    existing = glob.glob(str(artha_dir / "tmp" / "session_history_*.md"))
    if not existing:
        return 1
    nums: list[int] = []
    for p in existing:
        stem = Path(p).stem  # "session_history_3"
        try:
            nums.append(int(stem.split("_")[-1]))
        except ValueError:
            pass
    return (max(nums) + 1) if nums else 1


def _write_session_history(briefing_path: Path, artha_dir: Path) -> Path | None:
    """Write tmp/session_history_N.md + .json from briefing metadata.

    The session history is used by session_search and context recovery.
    It is NOT used for fact extraction (facts are extracted directly from
    the briefing file to avoid the lossy to_markdown() round-trip).

    Returns the path to the written Markdown file, or None on failure.
    """
    try:
        from session_summarizer import create_session_summary, summarize_to_file  # noqa: PLC0415
        summary = create_session_summary(
            session_intent="morning catch-up",
            command_executed="/catch-up",
            key_findings=[f"Catch-up briefing generated: {briefing_path.name}"],
            state_mutations=[str(briefing_path.relative_to(artha_dir)
                               if briefing_path.is_relative_to(artha_dir)
                               else briefing_path)],
            open_threads=[],
            next_suggested="/catch-up",
            trigger_reason="post_command",
        )
        session_n = _next_session_n(artha_dir)
        return summarize_to_file(summary, session_n, artha_dir)
    except Exception:  # noqa: BLE001
        return None  # Non-fatal — session history is for search/recovery only


def _append_run_log(record: dict, artha_dir: Path) -> None:
    """Append a structured run record to state/memory_pipeline_runs.jsonl."""
    log_path = artha_dir / "state" / "memory_pipeline_runs.jsonl"
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass  # Non-fatal


def _detect_parser_format(briefing_path: Path) -> str:
    """Detect the parser format from briefing content.

    Precedence:
    1. `source:` frontmatter field (canonical — written by briefing_archive.py)
    2. ━━ box-drawing glyph (legacy Telegram format, pre-canonical-frontmatter)
    """
    try:
        raw = briefing_path.read_text(encoding="utf-8", errors="replace")
        # Check for canonical frontmatter source field first
        m = re.search(r"^source:\s*(\S+)", raw, re.MULTILINE)
        if m:
            return m.group(1)  # "telegram", "vscode", "email", "mcp"
        # Legacy fallback: Telegram ━━ glyph
        return "telegram" if "\u2501\u2501" in raw else "markdown"
    except OSError:
        return "unknown"


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run(
    briefing_path: Path,
    artha_dir: Path,
    dry_run: bool = False,
) -> dict:
    """Run the post-catch-up memory pipeline on a single briefing file.

    Steps:
    1. Write session history to tmp/ (for session_search and context recovery)
    2. Extract facts DIRECTLY from the briefing (bypasses lossy SessionSummary
       round-trip — extracts from the full domain content)
    3. Persist new facts to state/memory.md (AR-1 capacity enforced)
    4. Update state/self_model.md (AR-2, requires ≥5 catch-up runs)
    5. Append structured run record to state/memory_pipeline_runs.jsonl

    Args:
        briefing_path: Absolute path to the briefing markdown file.
        artha_dir: Artha project root.
        dry_run: If True, extract and report but do not write any state files.

    Returns:
        Dict with run metadata fields (run_id, facts_extracted, …).
    """
    t0 = time.monotonic()
    run_record: dict = {
        "run_id": _run_id(),
        "briefing_path": (
            str(briefing_path.relative_to(artha_dir))
            if briefing_path.is_relative_to(artha_dir)
            else str(briefing_path)
        ),
        "facts_extracted": 0,
        "facts_persisted": 0,
        "fact_types": {},
        "parser_format": _detect_parser_format(briefing_path),
        "self_model_updated": False,
        "duration_ms": 0,
        "error": None,
    }

    try:
        from fact_extractor import extract_facts_from_summary, persist_facts  # noqa: PLC0415
        from self_model_writer import SelfModelWriter  # noqa: PLC0415

        # Step 1: Session history for search/recovery (not for extraction)
        if not dry_run:
            _write_session_history(briefing_path, artha_dir)

        # Step 2: Extract facts DIRECTLY from briefing
        facts = extract_facts_from_summary(briefing_path, artha_dir)
        run_record["facts_extracted"] = len(facts)

        # Tally fact types for observability
        type_counts: dict[str, int] = {}
        for f in facts:
            type_counts[f.type] = type_counts.get(f.type, 0) + 1
        run_record["fact_types"] = type_counts

        # Step 3: Persist facts
        if not dry_run:
            persisted = persist_facts(facts, artha_dir) if facts else 0
            run_record["facts_persisted"] = persisted

        # Step 4: Update self-model
        if not dry_run:
            writer = SelfModelWriter()
            updated = writer.update(
                memory_path=artha_dir / "state" / "memory.md",
                health_check_path=artha_dir / "state" / "health-check.md",
                self_model_path=artha_dir / "state" / "self_model.md",
            )
            run_record["self_model_updated"] = updated

    except Exception as exc:  # noqa: BLE001
        run_record["error"] = str(exc)

    run_record["duration_ms"] = int((time.monotonic() - t0) * 1000)

    # Step 5: Append observability log (always, even on error)
    if not dry_run:
        _append_run_log(run_record, artha_dir)

    # Step 6: Log digest (EV-8) — non-blocking, gated by harness.eval.log_digest.enabled
    if not dry_run:
        _run_log_digest(artha_dir)

    return run_record


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def _is_log_digest_enabled(artha_dir: Path) -> bool:
    """Check config flag harness.eval.log_digest.enabled."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config")
        return bool(
            cfg.get("harness", {})
            .get("eval", {})
            .get("log_digest", {})
            .get("enabled", False)
        )
    except Exception:  # noqa: BLE001
        return False


def _write_eval_alerts(new_anomalies: list[dict], artha_dir: Path) -> None:
    """Append new anomalies to state/eval_alerts.yaml (with R8 deduplication).

    - Dedup window: 14 days (same code+connector = skip)
    - Escalation: unacked P1 alerts > 24h get escalated: true
    - Capacity: keeps last 20 entries (config: harness.eval.alerts.max_entries)
    """
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return

    alerts_path = artha_dir / "state" / "eval_alerts.yaml"
    now = datetime.now(timezone.utc)
    dedup_cutoff = now - timedelta(days=14)

    # Load existing alerts
    existing: list[dict] = []
    if alerts_path.exists():
        try:
            raw = alerts_path.read_text(encoding="utf-8")
            loaded = yaml.safe_load(raw) or {}
            existing = loaded.get("alerts", [])
        except Exception:  # noqa: BLE001
            existing = []

    # Escalate unacked P1s older than 24h
    for alert in existing:
        if alert.get("severity") == "P1" and not alert.get("acked") and not alert.get("escalated"):
            ts_str = alert.get("detected_at", "")
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if (now - ts).total_seconds() > 86400:
                    alert["escalated"] = True
            except (ValueError, TypeError):
                pass

    # R8 dedup: build set of (code, connector) seen within 14d
    recent_keys: set[tuple[str, str]] = set()
    for alert in existing:
        ts_str = alert.get("detected_at", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= dedup_cutoff:
                recent_keys.add((alert.get("code", ""), alert.get("connector", "")))
        except (ValueError, TypeError):
            pass

    # Append new non-duplicate anomalies
    for anomaly in new_anomalies:
        key = (anomaly.get("code", ""), anomaly.get("connector", ""))
        if key in recent_keys:
            continue
        existing.append({
            **anomaly,
            "detected_at": now.isoformat(),
            "acked": False,
            "escalated": False,
        })
        recent_keys.add(key)

    # Capacity enforcement (default: 20)
    max_entries = 20
    existing = existing[-max_entries:]

    # Atomic write
    (artha_dir / "state").mkdir(exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=artha_dir / "state", prefix=".eval_alerts-", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            yaml.dump({"alerts": existing}, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path_str, alerts_path)
    except Exception:  # noqa: BLE001
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass


def _run_log_digest(artha_dir: Path) -> None:
    """Run log digest, write tmp/log_digest.json, update eval_alerts.yaml.

    Non-blocking — failures are silently swallowed after logging to stderr.
    """
    if not _is_log_digest_enabled(artha_dir):
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "log_digest", _SCRIPTS_DIR / "log_digest.py"
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        digest = mod.build_digest()
        mod.write_digest(digest)
        anomalies = digest.get("anomalies", [])
        if anomalies:
            _write_eval_alerts(anomalies, artha_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[log_digest] non-fatal error: {exc}", file=sys.stderr)



    """Rebuild state/self_model.md from existing memory.md + health-check.md.

    Idempotent and safe to run at any time.  Does not re-extract facts.
    """
    try:
        from self_model_writer import SelfModelWriter  # noqa: PLC0415
        writer = SelfModelWriter()
        return writer.update(
            memory_path=artha_dir / "state" / "memory.md",
            health_check_path=artha_dir / "state" / "health-check.md",
            self_model_path=artha_dir / "state" / "self_model.md",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Self-model rebuild failed: {exc}", file=sys.stderr)
        return False


def _discover_latest_briefing(artha_dir: Path) -> Path | None:
    """Find the most recently modified briefing file (--discover mode only)."""
    briefings_dir = artha_dir / "briefings"
    if not briefings_dir.exists():
        return None
    candidates = sorted(
        briefings_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Parse arguments and dispatch to the appropriate sub-command."""
    parser = argparse.ArgumentParser(
        description="Post-catch-up memory pipeline — extract, persist, update.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/post_catchup_memory.py --briefing briefings/2026-03-21.md\n"
            "  python3 scripts/post_catchup_memory.py --briefing briefings/2026-03-21.md --dry-run\n"
            "  python3 scripts/post_catchup_memory.py --rebuild-self-model\n"
            "  python3 scripts/post_catchup_memory.py --discover\n"
        ),
    )
    parser.add_argument(
        "--briefing",
        metavar="PATH",
        help="Path to the briefing file to process (required unless --discover or --rebuild-self-model).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract facts but do not persist to state files.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help=(
            "Auto-discover the most recently modified briefing. "
            "Use for bootstrap and manual re-processing only — "
            "Step 21 must always pass --briefing explicitly."
        ),
    )
    parser.add_argument(
        "--rebuild-self-model",
        action="store_true",
        help="Rebuild state/self_model.md from existing memory.md without re-extracting facts.",
    )
    parser.add_argument(
        "--artha-dir",
        metavar="DIR",
        default=str(_ARTHA_DIR),
        help="Artha project root (default: auto-detected from script location).",
    )
    args = parser.parse_args()
    artha_dir = Path(args.artha_dir).resolve()

    # --rebuild-self-model mode
    if args.rebuild_self_model:
        updated = rebuild_self_model(artha_dir)
        print("✅ Self-model rebuilt." if updated else "ℹ️  Self-model unchanged.")
        return 0

    # Resolve briefing path
    briefing_path: Path | None = None
    if args.briefing:
        bp = Path(args.briefing)
        briefing_path = bp if bp.is_absolute() else artha_dir / bp
    elif args.discover:
        briefing_path = _discover_latest_briefing(artha_dir)
        if briefing_path is None:
            print("⚠️  No briefing files found.", file=sys.stderr)
            return 1
    else:
        parser.error(
            "--briefing PATH is required (or use --discover for auto-detection, "
            "--rebuild-self-model to rebuild the self-model only)"
        )

    if not briefing_path.exists():  # type: ignore[union-attr]
        print(f"⚠️  Briefing not found: {briefing_path}", file=sys.stderr)
        _append_run_log({
            "run_id": _run_id(),
            "briefing_path": str(briefing_path),
            "facts_extracted": 0,
            "facts_persisted": 0,
            "fact_types": {},
            "parser_format": "unknown",
            "self_model_updated": False,
            "duration_ms": 0,
            "error": "briefing_not_found",
        }, artha_dir)
        return 1

    if not _is_enabled(artha_dir):
        print("ℹ️  post_catchup_memory disabled via config flag.", file=sys.stderr)
        return 0

    result = run(briefing_path, artha_dir, dry_run=args.dry_run)

    # One-line status output
    prefix = "🔍 [dry-run]" if args.dry_run else f"[{result['run_id']}]"
    status_self = "updated" if result["self_model_updated"] else "unchanged"

    if result.get("error"):
        print(f"{prefix} ⚠️  Error: {result['error']}", file=sys.stderr)
        return 2

    print(
        f"{prefix} {result['facts_persisted']} facts persisted "
        f"({result['facts_extracted']} extracted), "
        f"self-model {status_self}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
