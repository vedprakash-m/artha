"""scripts/lib/memory_writer.py — Write-time cap and FIFO eviction (DEBT-017).

Public API
----------
add_fact(fact, memory_path, *, domain="general", max_facts=30) -> bool
    Append *fact* to the ``facts:`` frontmatter list in *memory_path*.

    • Returns ``True`` when the fact was written.
    • Returns ``False`` when an identical ``text`` key already exists (duplicate).
    • Returns ``False`` when *domain* is in the high_sensitivity_domains list
      (DEBT-MEM-003) — blocked with MEMORY_FACT_SENSITIVITY_BLOCKED audit entry.
    • Evicts the oldest fact (FIFO) when ``len(facts) >= max_facts`` before
      appending, and logs a ``MEMORY_EVICTION`` line to ``state/audit.md``.
    • Writes atomically via a temp-file + ``os.replace()`` swap so a crash
      during the write cannot corrupt the frontmatter.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_AUDIT = Path(__file__).resolve().parents[2] / "state" / "audit.md"
_MEMORY_CONFIG = Path(__file__).resolve().parents[2] / "config" / "memory.yaml"

# ---------------------------------------------------------------------------
# DEBT-MEM-003: High-sensitivity domain list (loaded once; refreshed each process)
# ---------------------------------------------------------------------------

def _load_high_sensitivity_domains() -> frozenset[str]:
    """Read high_sensitivity_domains from config/memory.yaml (best-effort)."""
    try:
        import yaml  # type: ignore[import]
        with _MEMORY_CONFIG.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        # Key is nested under `privacy:` in config/memory.yaml
        privacy = cfg.get("privacy") or {}
        domains = privacy.get("high_sensitivity_domains", [])
        if isinstance(domains, list):
            return frozenset(str(d).lower() for d in domains)
    except Exception:  # noqa: BLE001
        pass
    # Fallback: hard-coded minimal set (mirrors foundation.py + DEBT-VAULT-001)
    return frozenset({
        "immigration", "finance", "insurance", "estate", "health",
        "audit", "vehicle", "contacts", "occasions", "transactions",
        "kids", "employment",
    })


_HIGH_SENSITIVITY_DOMAINS: frozenset[str] = _load_high_sensitivity_domains()


# ---------------------------------------------------------------------------
# Internal helpers (reuse the same pattern as correction_feeder.py)
# ---------------------------------------------------------------------------

def _read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
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
        import yaml  # type: ignore[import]
        fm = yaml.safe_load(fm_raw) or {}
    except Exception:  # noqa: BLE001
        fm = {}
    return fm, body


def _write_frontmatter(path: Path, fm: dict[str, Any], body: str) -> None:
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


def _log_eviction(evicted: dict[str, Any], fact_count: int, audit_path: Path) -> None:
    """Append a MEMORY_EVICTION record to the audit log."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    evicted_text = str(evicted.get("text", ""))[:60].replace("\n", " ")
    line = (
        f"| {now} | MEMORY_EVICTION | fact_count:{fact_count} "
        f"| evicted:{evicted_text} |\n"
    )
    try:
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _log_sensitivity_block(domain: str, fact_text: str, audit_path: Path) -> None:
    """Append a MEMORY_FACT_SENSITIVITY_BLOCKED record to the audit log.

    DEBT-MEM-003: Called when add_fact() rejects a high-sensitivity domain fact.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    truncated = str(fact_text)[:40].replace("\n", " ")
    line = (
        f"| {now} | MEMORY_FACT_SENSITIVITY_BLOCKED "
        f"| domain:{domain} | fact_preview:{truncated} |\n"
    )
    try:
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_fact(
    fact: dict[str, Any],
    memory_path: Path,
    *,
    domain: str = "general",
    max_facts: int = 30,
    audit_path: Path | None = None,
) -> bool:
    """Add *fact* to the facts list in *memory_path* with FIFO eviction.

    Parameters
    ----------
    fact:
        Dict with at minimum a ``text`` key (str).  Typically also includes
        ``type``, ``domain``, ``added``, and ``ttl`` keys.
    memory_path:
        Path to the ``state/memory.md`` YAML-frontmatter file.
    domain:
        Logical domain name (e.g. "immigration", "finance").  If the domain is
        in the ``high_sensitivity_domains`` list from ``config/memory.yaml``,
        the fact is rejected with a MEMORY_FACT_SENSITIVITY_BLOCKED audit entry.
        (DEBT-MEM-003 Part 1)
    max_facts:
        Hard cap on stored facts.  When ``len(facts) >= max_facts`` the
        oldest entry is evicted before the new fact is appended.
    audit_path:
        Path to append audit log lines.  Defaults to ``state/audit.md``
        relative to the repository root.

    Returns
    -------
    bool
        ``True`` if the fact was successfully written;
        ``False`` if the fact is a duplicate (same ``text``) or if the domain
        is high-sensitivity (blocked by DEBT-MEM-003).
    """
    if audit_path is None:
        audit_path = _DEFAULT_AUDIT

    # DEBT-MEM-003: Block high-sensitivity domain facts from state/memory.md.
    # These domains must be stored in encrypted vault state, not plaintext memory.
    if domain.lower() in _HIGH_SENSITIVITY_DOMAINS:
        _log_sensitivity_block(domain, str(fact.get("text", "")), audit_path)
        return False

    fm, body = _read_frontmatter(memory_path)
    facts: list[dict[str, Any]] = fm.get("facts") or []

    # Duplicate check — compare on normalised text
    new_text = str(fact.get("text", "")).strip()
    for existing in facts:
        if str(existing.get("text", "")).strip() == new_text:
            return False

    # FIFO eviction: remove oldest entries until below cap (leaving space for new one)
    while len(facts) >= max_facts:
        evicted = facts.pop(0)
        _log_eviction(evicted, len(facts) + 1, audit_path)

    facts.append(fact)
    fm["facts"] = facts
    _write_frontmatter(memory_path, fm, body)
    return True
