"""
evidence_lake.py — S-04: Evidence Lake for work accomplishments.

Stores evidence entries keyed by opaque source_hash (no raw PII).
Uses YAML front matter + entries list in a markdown file.
All writes are atomic (mkstemp + os.replace).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# Maximum entries allowed (A-12 budget constraint)
MAX_ENTRIES = 500

_VALID_SOURCE_TYPES = {"email", "meeting", "ado", "icm", "manual"}
_VALID_CONFIDENCE = {"state", "signaled", "inferred", "live", "user-confirmed"}
_VALID_IMPACT = {"activity", "output", "outcome", "impact"}

# PII field names that must never be stored
_FORBIDDEN_FIELDS = {
    "subject", "title", "name", "email", "sender", "recipient",
    "from", "to", "cc", "body", "content", "raw",
}


def _parse_lake(lake_path: Path) -> tuple[dict, list[dict]]:
    """Parse lake file → (frontmatter_dict, entries_list)."""
    if not lake_path.exists():
        fm: dict[str, Any] = {
            "schema_version": "1.0",
            "domain": "evidence-lake",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "entry_count": 0,
            "ttl_days": 90,
            "archive_path": "state/work/evidence-archive/",
        }
        return fm, []

    text = lake_path.read_text(encoding="utf-8")

    # Split YAML front matter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            import yaml  # type: ignore[import-not-found]
            fm = yaml.safe_load(parts[1]) or {}
            body = parts[2]
        else:
            fm = {}
            body = text
    else:
        fm = {}
        body = text

    # Parse entries from body
    try:
        import yaml  # type: ignore[import-not-found]
        # Find the "entries:" section in the body
        body_stripped = body.strip()
        if "entries:" in body_stripped:
            # Extract just the entries block
            idx = body_stripped.find("entries:")
            entries_yaml = body_stripped[idx:]
            parsed = yaml.safe_load(entries_yaml)
            if isinstance(parsed, dict) and "entries" in parsed:
                raw = parsed["entries"]
                entries = raw if isinstance(raw, list) else []
            else:
                entries = []
        else:
            entries = []
    except Exception:
        entries = []

    return fm, entries


def _write_lake(lake_path: Path, fm: dict, entries: list[dict]) -> None:
    """Atomically write lake file with updated front matter and entries."""
    import yaml  # type: ignore[import-not-found]

    fm["entry_count"] = len(entries)
    fm["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
    entries_text = yaml.dump({"entries": entries}, default_flow_style=False, allow_unicode=True)

    content = (
        "---\n"
        + fm_text
        + "---\n"
        "# Evidence Lake\n"
        "<!-- Auto-managed by evidence_lake.py. Do not edit entries manually. -->\n"
        "\n"
        + entries_text
    )

    lake_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=lake_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, lake_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _validate_entry(entry: dict) -> None:
    """Raise ValueError if entry contains forbidden fields or missing required fields."""
    # Check for forbidden PII fields
    entry_keys_lower = {k.lower() for k in entry}
    forbidden_found = entry_keys_lower & _FORBIDDEN_FIELDS
    if forbidden_found:
        raise ValueError(
            f"Entry contains forbidden PII fields: {sorted(forbidden_found)}. "
            "Store only source_hash (opaque), not raw identifiers."
        )

    required = {"source_hash", "source_type", "date", "summary", "goal_tags", "confidence", "impact_level"}
    missing = required - set(entry)
    if missing:
        raise ValueError(f"Entry missing required fields: {sorted(missing)}")

    if entry.get("source_type") not in _VALID_SOURCE_TYPES:
        raise ValueError(
            f"source_type must be one of {_VALID_SOURCE_TYPES}, got {entry.get('source_type')!r}"
        )

    if entry.get("confidence") not in _VALID_CONFIDENCE:
        raise ValueError(
            f"confidence must be one of {_VALID_CONFIDENCE}, got {entry.get('confidence')!r}"
        )

    if entry.get("impact_level") not in _VALID_IMPACT:
        raise ValueError(
            f"impact_level must be one of {_VALID_IMPACT}, got {entry.get('impact_level')!r}"
        )


def insert_entry(lake_path: Path, entry: dict) -> bool:
    """
    Insert an evidence entry into the lake. Idempotent by source_hash.

    Returns True if inserted, False if already present (dedup by source_hash).
    Raises ValueError for invalid/PII-containing entries.
    Raises RuntimeError if MAX_ENTRIES budget would be exceeded.
    """
    _validate_entry(entry)

    fm, entries = _parse_lake(lake_path)

    source_hash = entry["source_hash"]
    existing_hashes = {e.get("source_hash") for e in entries}
    if source_hash in existing_hashes:
        return False

    if len(entries) >= MAX_ENTRIES:
        raise RuntimeError(
            f"Evidence lake at capacity ({MAX_ENTRIES} entries). "
            "Run enforce_ttl() to archive old entries before inserting."
        )

    # Build clean entry (only allowed fields)
    clean: dict[str, Any] = {
        "source_hash": source_hash,
        "source_type": entry["source_type"],
        "date": entry["date"],
        "goal_tags": list(entry["goal_tags"]),
        "confidence": entry["confidence"],
        "impact_level": entry["impact_level"],
        "summary": entry["summary"],
    }
    # Preserve optional id field if provided
    if "id" in entry:
        clean["id"] = entry["id"]

    entries.append(clean)
    _write_lake(lake_path, fm, entries)
    return True


def query_entries(
    lake_path: Path,
    goal_tag: str | None = None,
    min_impact: str | None = None,
) -> list[dict]:
    """
    Query entries from the lake.

    goal_tag: filter to entries containing this tag in goal_tags.
    min_impact: filter by minimum impact level
                (activity < output < outcome < impact).
    Returns a list of matching entry dicts (copies).
    """
    _impact_order = {"activity": 0, "output": 1, "outcome": 2, "impact": 3}
    _, entries = _parse_lake(lake_path)

    result = []
    for e in entries:
        if goal_tag is not None:
            tags = e.get("goal_tags") or []
            if goal_tag not in tags:
                continue
        if min_impact is not None:
            level = e.get("impact_level", "activity")
            if _impact_order.get(level, 0) < _impact_order.get(min_impact, 0):
                continue
        result.append(dict(e))
    return result


def enforce_ttl(
    lake_path: Path,
    archive_dir: Path,
    ttl_days: int = 90,
) -> int:
    """
    Archive entries older than ttl_days to archive_dir.

    Returns the count of entries archived.
    Archive file: archive_dir/evidence-archive-YYYY-MM.yaml (by entry date).
    """
    import yaml  # type: ignore[import-not-found]

    fm, entries = _parse_lake(lake_path)
    ttl = fm.get("ttl_days", ttl_days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(ttl))
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    keep = []
    to_archive: list[dict] = []
    for e in entries:
        entry_date = e.get("date", "")
        if entry_date and entry_date < cutoff_str:
            to_archive.append(e)
        else:
            keep.append(e)

    if not to_archive:
        return 0

    # Group archived entries by YYYY-MM
    by_month: dict[str, list[dict]] = {}
    for e in to_archive:
        month = str(e.get("date", "unknown"))[:7]  # "YYYY-MM"
        by_month.setdefault(month, []).append(e)

    archive_dir.mkdir(parents=True, exist_ok=True)
    for month, month_entries in by_month.items():
        archive_file = archive_dir / f"evidence-archive-{month}.yaml"
        existing: list[dict] = []
        if archive_file.exists():
            try:
                parsed = yaml.safe_load(archive_file.read_text(encoding="utf-8"))
                if isinstance(parsed, list):
                    existing = parsed
            except Exception:
                existing = []
        combined = existing + month_entries
        content = yaml.dump(combined, default_flow_style=False, allow_unicode=True)
        fd, tmp = tempfile.mkstemp(dir=archive_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, archive_file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    _write_lake(lake_path, fm, keep)
    return len(to_archive)
