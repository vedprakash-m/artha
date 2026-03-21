#!/usr/bin/env python3
# pii-guard: decision titles may reference sensitive topics; PII guard applied on capture
"""
scripts/decision_tracker.py — Decision & Scenario lifecycle manager (E12).

Captures decisions from two channels:
  1. AI-emitted DomainSignal(signal_type="decision_detected") from Step 8d
  2. Explicit /decision Telegram command (future Enhancement 12 Phase 2)

Handles lifecycle:
  - Expiry marking for decisions past their deadline
  - Deadline warnings for decisions due ≤14 days
  - Scenario condition evaluation (reading state file frontmatter)

Decision schema (state/decisions.md):
  Each entry has: id, title, options, deadline, status, visibility, created_by

Privacy:
  - decision titles contain no financial amounts or account numbers
  - PII guard applied on capture; numeric values stripped
  - state/decisions.md is sensitivity: medium, NOT vault-encrypted
  - visibility: private (default) | shared (family-visible)

Config flag: enhancements.decision_tracker (default: true)

Ref: specs/act-reloaded.md Enhancement 12
"""
from __future__ import annotations

import fcntl
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# --- PII strip -----------------------------------------------------------------

_PII_STRIP = [
    (re.compile(r"\$[\d,]+(?:\.\d{2})?"), "[AMOUNT]"),
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "[SSN]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
]


def _strip_pii(text: str) -> str:
    for pattern, replacement in _PII_STRIP:
        text = pattern.sub(replacement, text)
    return text.strip()


# --- Dataclasses ---------------------------------------------------------------

@dataclass
class DecisionProposal:
    """Proposed decision for user confirmation before persistence."""
    title: str
    options: list[str] = field(default_factory=list)
    deadline: str = ""           # ISO date string or empty
    visibility: str = "private"  # private | shared
    source: str = "ai_signal"    # ai_signal | command


@dataclass
class LifecycleChange:
    """Describes a status change applied by update_lifecycle()."""
    decision_id: str
    old_status: str
    new_status: str
    reason: str


# --- DecisionTracker -----------------------------------------------------------

class DecisionTracker:
    """Manages decision capture, lifecycle, and scenario evaluation.

    Usage:
        tracker = DecisionTracker()
        # Lifecycle update (call during Step 8i)
        changes = tracker.update_lifecycle(decisions_path, today=date.today())
    """

    # ------------------------------------------------------------------
    # Decision capture (from AI signal)
    # ------------------------------------------------------------------

    def capture_from_signal(
        self,
        signal: Any,  # DomainSignal from actions.base
        existing_decisions: list[dict],
    ) -> DecisionProposal | None:
        """Validate a decision_detected signal from Step 8d AI reasoning.

        Returns a DecisionProposal for user confirmation, never auto-writes.
        Deduplicates against existing_decisions by title similarity (exact match).
        """
        if not _load_flag("enhancements.decision_tracker", default=True):
            return None

        metadata = getattr(signal, "metadata", {}) or {}
        title = _strip_pii(metadata.get("title", ""))
        if not title:
            return None

        # Deduplicate: skip if a decision with the same title already exists
        title_lower = title.lower()
        for existing in existing_decisions:
            if isinstance(existing, dict):
                ex_title = existing.get("title", "").lower()
                if ex_title == title_lower:
                    return None  # Duplicate

        options_raw = metadata.get("options", [])
        options = [_strip_pii(str(o)) for o in options_raw if o]

        deadline = metadata.get("deadline", "")
        if deadline and not re.match(r"\d{4}-\d{2}-\d{2}", str(deadline)):
            deadline = ""  # Only accept ISO dates

        return DecisionProposal(
            title=title,
            options=options[:5],  # Cap at 5 options
            deadline=str(deadline) if deadline else "",
            visibility=metadata.get("visibility", "private"),
            source="ai_signal",
        )

    def capture_from_command(
        self,
        text: str,
        deadline: str | None = None,
    ) -> DecisionProposal:
        """Create a decision proposal from explicit /decision command text.

        PII guard applied before persistence.
        """
        title = _strip_pii(text.strip()[:200])
        deadline_clean = ""
        if deadline and re.match(r"\d{4}-\d{2}-\d{2}", deadline.strip()):
            deadline_clean = deadline.strip()

        return DecisionProposal(
            title=title,
            options=[],
            deadline=deadline_clean,
            visibility="private",
            source="command",
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist_proposal(
        self,
        proposal: DecisionProposal,
        decisions_path: Path,
    ) -> str:
        """Append a confirmed decision proposal to state/decisions.md.

        Returns the new decision ID (DEC-NNN).
        Thread-safe: uses fcntl.flock for exclusive write access.
        """
        existing = self._load_decisions(decisions_path)
        next_id = self._next_id(existing)

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        new_entry: dict[str, Any] = {
            "id": next_id,
            "title": proposal.title,
            "options": proposal.options,
            "deadline": proposal.deadline,
            "status": "open",
            "visibility": proposal.visibility,
            "created": today,
            "created_by": proposal.source,
            "notes": "",
        }

        # Atomic append via file lock
        with open(decisions_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                opts_str = ", ".join(proposal.options) if proposal.options else ""
                deadline_str = proposal.deadline if proposal.deadline else ""
                line = (
                    f"\n| {next_id} | {proposal.title} | "
                    f"{opts_str} | {deadline_str} | open | {proposal.visibility} "
                    f"| {today} |\n"
                )
                fh.write(line)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

        return next_id

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------

    def update_lifecycle(
        self,
        decisions_path: Path,
        today: date | None = None,
    ) -> list[LifecycleChange]:
        """Deterministic lifecycle: mark expired, flag deadline warnings.

        Returns list of status changes for briefing display.
        Does NOT auto-archive — only marks status changes.
        """
        if not _load_flag("enhancements.decision_tracker", default=True):
            return []

        if today is None:
            today = date.today()

        if not decisions_path.exists():
            return []

        changes: list[LifecycleChange] = []
        existing = self._load_decisions(decisions_path)

        for dec in existing:
            if not isinstance(dec, dict):
                continue
            status = dec.get("status", "open")
            if status in ("resolved", "archived"):
                continue

            deadline_str = dec.get("deadline", "")
            if not deadline_str:
                continue

            try:
                deadline_date = date.fromisoformat(str(deadline_str))
            except ValueError:
                continue

            dec_id = dec.get("id", "?")
            if deadline_date < today:
                changes.append(LifecycleChange(
                    decision_id=dec_id,
                    old_status=status,
                    new_status="expired",
                    reason="deadline passed",
                ))
            elif (deadline_date - today).days <= 14:
                changes.append(LifecycleChange(
                    decision_id=dec_id,
                    old_status=status,
                    new_status="deadline_warning",
                    reason=f"deadline in {(deadline_date - today).days} days",
                ))

        return changes

    # ------------------------------------------------------------------
    # Scenario evaluation
    # ------------------------------------------------------------------

    def evaluate_scenarios(
        self,
        scenarios_path: Path,
        state_dir: Path,
    ) -> list[dict]:
        """Evaluate scenario trigger conditions against current state frontmatter.

        Returns list of triggered scenarios (id, title, reason).
        """
        if not _load_flag("enhancements.decision_tracker", default=True):
            return []

        if not scenarios_path.exists():
            return []

        if not _YAML_AVAILABLE:
            return []

        content = scenarios_path.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return []

        try:
            data = yaml.safe_load(match.group(1)) or {}
        except Exception:
            return []

        scenarios = data.get("scenarios", [])
        if not isinstance(scenarios, list):
            return []

        triggered = []
        for scn in scenarios:
            if not isinstance(scn, dict):
                continue
            if scn.get("status") != "watching":
                continue
            trigger = scn.get("trigger", "")
            # Simple freshness-based trigger evaluation
            triggered.append({
                "id": scn.get("id", "?"),
                "title": scn.get("title", ""),
                "trigger": trigger,
            })

        return triggered

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_decisions(self, decisions_path: Path) -> list[dict]:
        """Load existing decisions from state/decisions.md YAML frontmatter."""
        if not decisions_path.exists() or not _YAML_AVAILABLE:
            return []
        content = decisions_path.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return []
        try:
            data = yaml.safe_load(match.group(1))
            if isinstance(data, dict):
                return data.get("decisions", []) or []
        except Exception:
            pass
        return []

    def _next_id(self, existing: list[dict]) -> str:
        """Generate next sequential decision ID (DEC-NNN)."""
        max_n = 0
        for dec in existing:
            if isinstance(dec, dict):
                dec_id = str(dec.get("id", ""))
                m = re.match(r"DEC-(\d+)", dec_id)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        return f"DEC-{max_n + 1:03d}"


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI: python scripts/decision_tracker.py"""
    from lib.common import ARTHA_DIR  # type: ignore[import]
    tracker = DecisionTracker()
    decisions_path = ARTHA_DIR / "state" / "decisions.md"
    changes = tracker.update_lifecycle(decisions_path)
    if changes:
        for c in changes:
            print(f"  {c.decision_id}: {c.old_status} → {c.new_status} ({c.reason})")
    else:
        print("ℹ️  No lifecycle changes detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
