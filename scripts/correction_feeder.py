#!/usr/bin/env python3
"""scripts/correction_feeder.py — Inject correction facts into domain context.

Reads the `facts:` frontmatter list from state/memory.md and filters to
corrections and thresholds that are relevant for the current domain context.
Outputs a YAML fragment of filtered corrections for injection into catch-up
Step 6.

Filtering rules:
- `type` must be in ('correction', 'threshold')
- `ttl` must not be expired (relative to today; missing TTL = keep forever)
- Per-domain cap: 10 most-recent facts (FIFO evict oldest on cap breach)
- Global cap: 50 facts across all domains

PII guard: strips values matching personal name / email patterns before output.

Config gate: harness.eval.correction_injection.enabled (default: true)

Usage:
    python scripts/correction_feeder.py [--domain DOMAIN] [--gc] [--json]
    python scripts/correction_feeder.py --gc  # prune expired facts from memory.md in-place

Ref: specs/eval.md EV-9
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass

_MEMORY_FILE = _ARTHA_DIR / "state" / "memory.md"
_CONFIG_FILE = _ARTHA_DIR / "config" / "artha_config.yaml"

_ALLOWED_TYPES = {"correction", "threshold"}
_PER_DOMAIN_CAP = 10
_GLOBAL_CAP = 50

# PII guard: simple heuristic patterns for names and emails.
_PII_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# Detect "Firstname Lastname" (two or more title-case words)
_PII_NAME_RE = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b")


def _is_enabled() -> bool:
    """Check harness.eval.correction_injection.enabled."""
    try:
        import yaml  # type: ignore[import]
        if not _CONFIG_FILE.exists():
            return True
        raw = _CONFIG_FILE.read_text(encoding="utf-8")
        cfg = yaml.safe_load(raw) or {}
        return bool(
            cfg.get("harness", {})
            .get("eval", {})
            .get("correction_injection", {})
            .get("enabled", True)
        )
    except Exception:  # noqa: BLE001
        return True


def _strip_pii(value: str) -> str:
    """Replace PII matches with redaction tokens."""
    value = _PII_EMAIL_RE.sub("[EMAIL]", value)
    value = _PII_NAME_RE.sub("[NAME]", value)
    return value


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a Markdown file.

    Returns (frontmatter_dict, body_text).  If no frontmatter, returns
    ({}, full_text).
    """
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return {}, path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_raw = text[3:end]
    body = text[end + 4:]
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except Exception:  # noqa: BLE001
        fm = {}
    return fm, body


def _write_frontmatter(path: Path, fm: dict[str, Any], body: str) -> None:
    """Write frontmatter + body back to file atomically."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return
    fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    new_content = f"---\n{fm_text}---\n{body}"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# TTL helpers
# ---------------------------------------------------------------------------

def _is_expired(fact: dict[str, Any]) -> bool:
    """Return True if the fact's TTL has passed."""
    ttl = fact.get("ttl")
    if not ttl:
        return False
    today = date.today()
    # TTL can be a date object, an ISO string, or None
    if isinstance(ttl, date) and not isinstance(ttl, datetime):
        return today > ttl
    if isinstance(ttl, str):
        try:
            ttl_date = date.fromisoformat(ttl)
            return today > ttl_date
        except ValueError:
            pass
    return False


# ---------------------------------------------------------------------------
# Core filter
# ---------------------------------------------------------------------------

def _filter_facts(
    facts: list[dict[str, Any]],
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Return filtered list of correction-injection-eligible facts.

    Applies: type filter → TTL filter → domain match → per-domain cap →
    PII scrub → global cap.
    """
    eligible = [
        f for f in facts
        if isinstance(f, dict)
        and f.get("type") in _ALLOWED_TYPES
        and not _is_expired(f)
    ]

    # Domain filter (if requested — None means all domains)
    if domain:
        domain_lower = domain.lower()
        eligible = [
            f for f in eligible
            if not f.get("domain")
            or str(f.get("domain", "")).lower() == domain_lower
        ]

    # Group by domain for per-domain cap
    domain_buckets: dict[str, list[dict[str, Any]]] = {}
    for f in eligible:
        d = str(f.get("domain", "_global")).lower()
        domain_buckets.setdefault(d, []).append(f)

    capped: list[dict[str, Any]] = []
    for bucket in domain_buckets.values():
        # FIFO: keep the last _PER_DOMAIN_CAP (most recent = end of list)
        capped.extend(bucket[-_PER_DOMAIN_CAP:])

    # PII scrub on the value field
    scrubbed: list[dict[str, Any]] = []
    for f in capped:
        cleaned = dict(f)
        if "value" in cleaned and isinstance(cleaned["value"], str):
            cleaned["value"] = _strip_pii(cleaned["value"])
        scrubbed.append(cleaned)

    # Global cap: keep last _GLOBAL_CAP
    return scrubbed[-_GLOBAL_CAP:]


# ---------------------------------------------------------------------------
# GC mode: prune expired facts from memory.md in-place
# ---------------------------------------------------------------------------

def gc_expired(memory_path: Path = _MEMORY_FILE) -> int:
    """Remove expired facts from memory.md frontmatter.

    Returns the number of facts pruned.
    """
    fm, body = _read_frontmatter(memory_path)
    facts = fm.get("facts", [])
    if not isinstance(facts, list):
        return 0
    original_count = len(facts)
    live_facts = [f for f in facts if isinstance(f, dict) and not _is_expired(f)]
    pruned = original_count - len(live_facts)
    if pruned > 0:
        fm["facts"] = live_facts
        _write_frontmatter(memory_path, fm, body)
    return pruned


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> "argparse.Namespace":
    p = argparse.ArgumentParser(
        prog="correction_feeder.py",
        description="Output correction facts from memory.md for domain context injection",
    )
    p.add_argument(
        "--domain",
        metavar="DOMAIN",
        default=None,
        help="Filter to a specific domain (e.g. 'finance', 'immigration'). "
             "Omit to return all domains.",
    )
    p.add_argument(
        "--gc",
        action="store_true",
        help="Prune expired facts from state/memory.md in-place and exit.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON array instead of YAML fragment.",
    )
    p.add_argument(
        "--no-check",
        action="store_true",
        help="Skip config-enabled check (always run).",
    )
    p.add_argument(
        "--memory-file",
        default=str(_MEMORY_FILE),
        metavar="PATH",
        help=f"Path to state/memory.md (default: {_MEMORY_FILE})",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    memory_path = Path(args.memory_file)

    # GC mode
    if args.gc:
        n = gc_expired(memory_path)
        print(f"[correction_feeder] Pruned {n} expired facts from {memory_path.name}", file=sys.stderr)
        return 0

    # Config gate
    if not args.no_check and not _is_enabled():
        print("[correction_feeder] disabled via config flag.", file=sys.stderr)
        return 0

    fm, _ = _read_frontmatter(memory_path)
    facts = fm.get("facts", []) or []
    if not isinstance(facts, list):
        facts = []

    filtered = _filter_facts(facts, domain=args.domain)

    if not filtered:
        print("[correction_feeder] No eligible correction facts found.", file=sys.stderr)
        return 0

    if args.json:
        import json
        print(json.dumps(filtered, ensure_ascii=False, indent=2, default=str))
    else:
        try:
            import yaml  # type: ignore[import]
            print(yaml.dump(
                {"corrections": filtered},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            ))
        except ImportError:
            import json
            print(json.dumps({"corrections": filtered}, ensure_ascii=False, indent=2, default=str))

    print(
        f"[correction_feeder] {len(filtered)} facts "
        f"({'domain=' + args.domain if args.domain else 'all domains'})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
