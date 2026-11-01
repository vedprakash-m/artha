"""scripts/lib/dq_gate.py — Pull-based data quality gate for Work OS.

Evaluates quality of work-domain state files (markdown) and KB entities
using a three-dimension composite score:

    Q = w_A × A  +  w_F × F  +  w_C × C

  A — Accuracy:    provenance tier + corroboration + conflict penalty
  F — Freshness:   age relative to domain-specific TTL
  C — Completeness: file-size proxy (markdown) or field-presence check (entity)

Priority order (non-negotiable): Accuracy > Freshness > Completeness.

Canonical import pattern:
    import dq_gate as dq           # scripts/ must be on sys.path
    score = dq.file_quality(path, "calendar")
    qs    = dq.assess_quality(entity, kg)

N1 circular-import note:
    This module imports constants from knowledge_graph at MODULE LEVEL (safe).
    knowledge_graph.context_for() imports dq_gate LAZILY (inside function body).
    Never import dq_gate at module level from knowledge_graph.py.

Spec: specs/data-quality-gate.md v4
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lib.common import STATE_DIR
from lib.knowledge_graph import (
    _DQ_DOMAIN_WEIGHTS,
    _DQ_GATE_PASS,
    _DQ_GATE_STALE_SERVE,
    _DQ_GATE_WARN,
    _DQ_MIN_CONFIDENCE,
)

if TYPE_CHECKING:
    from lib.knowledge_graph import Entity, KnowledgeGraph

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quality verdict (N4: explicit integer values for correct min() behaviour)
# ---------------------------------------------------------------------------

class QualityVerdict(IntEnum):
    REFUSE      = 0  # Q < 0.3 — refuse to serve; tell user to refresh
    STALE_SERVE = 1  # 0.3 ≤ Q < 0.5 — serve stale data with strong caveat
    WARN        = 2  # 0.5 ≤ Q < 0.7 — serve with mild aging caveat
    PASS        = 3  # Q ≥ 0.7 — serve normally


# ---------------------------------------------------------------------------
# Quality score result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QualityScore:
    A: float            # accuracy dimension (0.0–1.0)
    F: float            # freshness dimension (0.0–1.0)
    C: float            # completeness dimension (0.0–1.0)
    composite: float    # Q = w_A*A + w_F*F + w_C*C (0.0–1.0)
    domain: str         # domain profile used for weights
    verdict: QualityVerdict


# ---------------------------------------------------------------------------
# Markdown file TTLs (days) — single source of truth
# ---------------------------------------------------------------------------

_FILE_TTL: dict[str, int] = {
    "work-calendar.md":        1,
    "work-comms.md":           1,
    "work-incidents.md":       1,
    "work-projects.md":        7,
    "work-people.md":          14,
    "work-accomplishments.md": 14,
    "golden-queries.md":       14,
    "work-decisions.md":       14,
    "work-scope.md":           30,
    "work-performance.md":     30,
}

# Live-data source tiers for accuracy scoring
_LIVE_PROVIDERS: frozenset[str] = frozenset(
    {"workiq", "graph_calendar", "ado_workitems", "kusto_icm"}
)

# Placeholder / empty sentinel values (spec §Completeness)
_PLACEHOLDER_VALUES: frozenset[Any] = frozenset(
    {None, "", "TBD", "See notes", "TODO", "—"}
)

# Gate decision log (JSON-lines, rotated at 1 MB)
_GATE_LOG: Path = STATE_DIR / "work" / "quality_gate.log"
_GATE_LOG_MAX_BYTES: int = 1_000_000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(path: Path) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file. Returns {} on failure."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    try:
        import yaml  # noqa: PLC0415
        result = yaml.safe_load(fm_text)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _age_days(ts: str | None) -> float | None:
    """Fractional days since an ISO-8601 timestamp string. None if unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def _verdict_for(composite: float) -> QualityVerdict:
    if composite >= _DQ_GATE_PASS:
        return QualityVerdict.PASS
    if composite >= _DQ_GATE_WARN:
        return QualityVerdict.WARN
    if composite >= _DQ_GATE_STALE_SERVE:
        return QualityVerdict.STALE_SERVE
    return QualityVerdict.REFUSE


# ---------------------------------------------------------------------------
# Public: markdown file quality
# ---------------------------------------------------------------------------

def file_quality(path: Path, domain: str) -> float:
    """Compute composite Q for a markdown state file using domain-aware weights.

    Reads YAML frontmatter for Accuracy (provenance), Freshness (age vs TTL),
    and Completeness (file-size proxy). Returns Q in [0.0, 1.0].

    A missing file returns 0.0 — callers should treat this as a silent skip
    (the section is omitted from output), not as a hard REFUSE.
    """
    if not path.exists():
        return 0.0

    fm = _parse_frontmatter(path)
    ttl = _FILE_TTL.get(path.name, 14)
    weights = _DQ_DOMAIN_WEIGHTS.get(domain, _DQ_DOMAIN_WEIGHTS["default"])

    # -- Freshness -----------------------------------------------------------
    last_updated = fm.get("last_updated")
    age = _age_days(str(last_updated) if last_updated is not None else None)
    if age is None:
        F = 0.0
    else:
        F = max(0.0, min(1.0, 1.0 - age / ttl))

    # -- Accuracy (provenance tier) ------------------------------------------
    providers: list[str] = fm.get("providers_used") or []
    generated_by: str = fm.get("generated_by") or ""
    if providers and any(p in _LIVE_PROVIDERS for p in providers):
        A = 0.90  # live-data provenance — highest trust
    elif generated_by in ("work_loop", "work_domain_writers", "workiq_refresh"):
        A = 0.80  # pipeline-generated, no live provider list
    elif generated_by:
        A = 0.70  # known generator, not a live pipeline
    else:
        A = 0.60  # unknown provenance — manual or untracked

    # -- Completeness (binary file-size proxy) --------------------------------
    try:
        C = 1.0 if path.stat().st_size > 1000 else 0.3
    except OSError:
        C = 0.0

    return weights["A"] * A + weights["F"] * F + weights["C"] * C


# ---------------------------------------------------------------------------
# Public: KB entity quality
# ---------------------------------------------------------------------------

def assess_quality(entity: "Entity", kg: "Any") -> QualityScore:
    """Compute QualityScore for a KB entity.

    Called lazily from knowledge_graph.context_for() to avoid circular imports
    (see N1 module dependency note at top of file).

    Entities with confidence < _DQ_MIN_CONFIDENCE should be excluded from
    context assembly by the caller before calling this function.
    """
    domain = entity.domain or "default"
    weights = _DQ_DOMAIN_WEIGHTS.get(domain, _DQ_DOMAIN_WEIGHTS["default"])

    # -- Accuracy ------------------------------------------------------------
    # Base from source_type tier
    if entity.source_type in _LIVE_PROVIDERS:
        A_base = 0.90
    elif entity.source_type in ("bootstrap", "manual", "kb_extract"):
        A_base = 0.80
    elif entity.source_type:
        A_base = 0.70
    else:
        A_base = 0.60

    # Corroboration boost: multiple independent sources increase trust
    if entity.corroborating_sources >= 2:
        A_base = min(1.0, A_base + 0.10)

    # Scale by entity-level confidence
    A = A_base * entity.confidence

    # Conflict penalty: two sources disagree — halve accuracy
    try:
        if kg.entity_has_active_conflicts(entity.id):
            A *= 0.5
    except Exception:
        pass  # KB unavailable — apply no penalty

    # -- Freshness -----------------------------------------------------------
    age = _age_days(entity.last_validated)
    if age is None:
        # No last_validated — fall back to effective_staleness flag
        _STALENESS_F = {"fresh": 0.9, "aging": 0.6, "stale": 0.3, "expired": 0.0}
        F = _STALENESS_F.get(entity.effective_staleness, 0.0)
    else:
        ttl = max(1, entity.staleness_ttl_days)
        F = max(0.0, min(1.0, 1.0 - age / ttl))

    # -- Completeness (field-presence proxy) ---------------------------------
    # Two-level: name-only = 0.5, name + non-placeholder summary = 1.0
    if entity.name and entity.name not in _PLACEHOLDER_VALUES:
        if entity.summary and entity.summary not in _PLACEHOLDER_VALUES:
            C = 1.0
        else:
            C = 0.5
    else:
        C = 0.0

    composite = max(0.0, min(1.0, weights["A"] * A + weights["F"] * F + weights["C"] * C))

    return QualityScore(
        A=round(A, 4),
        F=round(F, 4),
        C=round(C, 4),
        composite=round(composite, 4),
        domain=domain,
        verdict=_verdict_for(composite),
    )


# ---------------------------------------------------------------------------
# Observability: structured gate decision log
# ---------------------------------------------------------------------------

def _log_gate_decision(
    section: str,
    path: Path,
    domain: str,
    score: float,
    verdict: QualityVerdict,
    *,
    command: str = "",
    A: float = 0.0,
    F: float = 0.0,
    C: float = 0.0,
    age_days: float = 0.0,
    ttl_days: int = 0,
) -> None:
    """Append one JSON-lines gate decision record to state/work/quality_gate.log.

    Silently swallowed on any I/O error — the gate log must never crash the
    read path.
    """
    try:
        _GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "command": command,
            "section": section,
            "file": path.name if path else "",
            "domain": domain,
            "score": round(score, 4),
            "verdict": verdict.name,
            "A": round(A, 4),
            "F": round(F, 4),
            "C": round(C, 4),
            "age_days": round(age_days, 2),
            "ttl_days": ttl_days,
        }
        # Rotate log if over size limit (simple truncation)
        if _GATE_LOG.exists() and _GATE_LOG.stat().st_size > _GATE_LOG_MAX_BYTES:
            _GATE_LOG.unlink()
        with _GATE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        _log.debug("gate_log_write_failed: %s", exc)
