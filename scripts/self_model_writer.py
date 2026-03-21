#!/usr/bin/env python3
# pii-guard: ignore-file — self-model contains behavioral observations only, no PII
"""
scripts/self_model_writer.py — Artha Self-Model metacognition writer (AR-2).

Updates state/self_model.md from accumulated memory facts and health-check
telemetry. Called at Step 11c of the catch-up workflow.

The self-model tracks:
  - Domain Confidence: where Artha is reliably accurate vs. struggles
  - Effective Strategies: what approaches work well for this user
  - Known Blind Spots: past mistakes and areas requiring extra care

Activation threshold: minimum 5 catch-up runs before first write (need data).
Character cap: 3,000 chars (raised from AR-2's 1,500 to accommodate 20+ domains).

Config flags:
  harness.agentic.self_model.enabled   — master toggle (default: true)
  harness.agentic.self_model.max_chars — cap (default: 3000)
  enhancements.memory_activation       — umbrella flag for E11

Privacy:
  - Self-model contains behavioral observations only (no PII, no financials)
  - PII guard applied to any extracted text from memory facts
  - File is NOT vault-encrypted (standard sensitivity, behavioral data only)

Ref: specs/act-reloaded.md Enhancement 11, specs/agentic-reloaded.md AR-2
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure scripts/ on path
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

# --- Constants ---------------------------------------------------------------

_DEFAULT_MAX_CHARS = 3_000
_MIN_CATCHUP_RUNS = 5   # minimum sessions before first write

_SECTION_HEADERS = [
    "### Domain Confidence",
    "### Effective Strategies",
    "### Known Blind Spots",
    "### User Interaction Patterns",
]

# PII patterns applied to extracted text
_PII_STRIP = [
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
]


def _strip_pii(text: str) -> str:
    for pattern, replacement in _PII_STRIP:
        text = pattern.sub(replacement, text)
    return text


# --- Self-Model Writer -------------------------------------------------------

class SelfModelWriter:
    """Updates state/self_model.md from memory and health-check data.

    Usage:
        writer = SelfModelWriter()
        updated = writer.update(
            memory_path=Path("state/memory.md"),
            health_check_path=Path("state/health-check.md"),
            self_model_path=Path("state/self_model.md"),
        )
    """

    def __init__(self, max_chars: int = _DEFAULT_MAX_CHARS) -> None:
        self.max_chars = max_chars

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        memory_path: Path,
        health_check_path: Path,
        self_model_path: Path,
    ) -> bool:
        """Update self-model from accumulated memory and health-check data.

        Returns True if the self-model was modified.

        Activation gate: requires min 5 catch-up runs in health-check.md.
        Character cap: enforced; truncation strategy = rolling top-K.
        """
        if not _load_flag("agentic.self_model.enabled"):
            return False

        if not _load_flag("enhancements.memory_activation", default=True):
            return False

        # --- Load health check data ----------------------------------
        health_data = self._load_frontmatter(health_check_path)
        catchup_runs = health_data.get("catch_up_runs", []) if isinstance(health_data, dict) else []
        # Phase 2 fix: fall back to Markdown-parsed run history when YAML
        # frontmatter does not contain a structured catch_up_runs list
        if not catchup_runs and health_check_path.exists():
            try:
                catchup_runs = _parse_catchup_runs_from_markdown(
                    health_check_path.read_text(encoding="utf-8")
                )
            except Exception:  # noqa: BLE001
                catchup_runs = []
        if len(catchup_runs) < _MIN_CATCHUP_RUNS:
            return False  # Not enough data yet

        # --- Load memory facts --------------------------------------
        memory_data = self._load_frontmatter(memory_path)
        facts: list[dict] = []
        if isinstance(memory_data, dict):
            facts_raw = memory_data.get("facts", [])
            if isinstance(facts_raw, list):
                facts = [f for f in facts_raw if isinstance(f, dict)]

        # --- Load existing self-model (preserve personality section) --
        existing_content = ""
        if self_model_path.exists():
            existing_content = self_model_path.read_text(encoding="utf-8")

        # --- Build new sections ------------------------------------
        domain_confidence = self._build_domain_confidence(facts, catchup_runs)
        effective_strategies = self._build_effective_strategies(facts, catchup_runs)
        blind_spots = self._build_blind_spots(facts, catchup_runs)

        # --- Preserve personality profile section ------------------
        personality_section = self._extract_personality_section(existing_content)

        # --- Assemble new body (respect max_chars) -----------------
        body_sections = []
        if domain_confidence:
            body_sections.append(f"### Domain Confidence\n{domain_confidence}")
        if effective_strategies:
            body_sections.append(f"### Effective Strategies\n{effective_strategies}")
        if blind_spots:
            body_sections.append(f"### Known Blind Spots\n{blind_spots}")
        if personality_section:
            body_sections.append(personality_section)

        new_body = "\n\n".join(body_sections)
        # Enforce char cap
        if len(new_body) > self.max_chars:
            new_body = new_body[: self.max_chars - 3] + "..."

        # --- Write file --------------------------------------------
        now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        frontmatter = (
            f"---\n"
            f"domain: self_model\n"
            f"last_updated: {now_iso}\n"
            f"schema_version: '1.0'\n"
            f"max_chars: {self.max_chars}\n"
            f"---\n"
        )
        preamble = (
            "\n## Artha Self-Model\n\n"
            "Bounded self-awareness file. Updated at session boundaries (Step 11c),\n"
            "never mid-turn. Frozen at session start for cache stability.\n\n"
            f"Maximum {self.max_chars:,} characters. Consolidate when approaching limit.\n\n"
        )
        new_content = frontmatter + preamble + new_body

        # Only write if content actually changed
        if new_content.strip() == existing_content.strip():
            return False

        self_model_path.write_text(new_content, encoding="utf-8")
        return True

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_domain_confidence(
        self,
        facts: list[dict],
        catchup_runs: list[dict],
    ) -> str:
        """Build domain confidence section from correction facts."""
        # Corrections with high confidence = reliable domains
        corrections = [
            f for f in facts
            if f.get("type") == "correction" and f.get("confidence", 0) >= 0.9
        ]
        if not corrections:
            return ""

        lines = []
        seen_domains: set[str] = set()
        for fact in corrections[:8]:  # top 8 by recency
            domain = fact.get("domain", "unknown")
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            stmt = _strip_pii(fact.get("statement", ""))
            if stmt and len(stmt) < 120:
                lines.append(f"- {domain.capitalize()}: {stmt}")

        return "\n".join(lines) if lines else ""

    def _build_effective_strategies(
        self,
        facts: list[dict],
        catchup_runs: list[dict],
    ) -> str:
        """Build effective strategies from preference facts and run telemetry."""
        preference_facts = [
            f for f in facts if f.get("type") == "preference"
        ]

        lines = []
        for fact in preference_facts[:6]:
            stmt = _strip_pii(fact.get("statement", ""))
            if stmt and len(stmt) < 120:
                lines.append(f"- {stmt}")

        # Infer from run history
        if len(catchup_runs) >= _MIN_CATCHUP_RUNS:
            flash_count = sum(
                1 for r in catchup_runs[-10:]
                if isinstance(r, dict) and r.get("briefing_format") == "flash"
            )
            if flash_count >= 6:
                lines.append("- Flash briefings preferred (detected from run history)")

            skip_rate = _compute_skip_rate(catchup_runs)
            if skip_rate > 0.7:
                lines.append(
                    "- User often skips calibration questions — reduce to 0 by default"
                )

        return "\n".join(lines) if lines else ""

    def _build_blind_spots(
        self,
        facts: list[dict],
        catchup_runs: list[dict],
    ) -> str:
        """Build blind spots from past correction facts."""
        corrections = [
            f for f in facts
            if f.get("type") == "correction"
        ]
        if not corrections:
            return ""

        lines = []
        for fact in corrections[:6]:
            stmt = _strip_pii(fact.get("statement", ""))
            domain = fact.get("domain", "")
            if stmt and len(stmt) < 120:
                prefix = f"{domain.capitalize()}: " if domain else ""
                lines.append(f"- {prefix}{stmt}")

        return "\n".join(lines) if lines else ""

    def _extract_personality_section(self, content: str) -> str:
        """Preserve the Personality Profile section from existing self-model."""
        marker = "### Personality Profile"
        idx = content.find(marker)
        if idx == -1:
            return ""
        # Find end of section (next ### or end of file)
        next_section = content.find("\n###", idx + len(marker))
        if next_section == -1:
            return content[idx:].strip()
        return content[idx:next_section].strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_frontmatter(self, path: Path) -> dict[str, Any]:
        """Load YAML frontmatter from a state file."""
        if not path.exists():
            return {}
        if not _YAML_AVAILABLE:
            return {}
        content = path.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return {}
        try:
            data = yaml.safe_load(match.group(1))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _compute_skip_rate(runs: list[dict]) -> float:
    """Compute calibration skip rate from last 10 runs."""
    recent = [r for r in runs[-10:] if isinstance(r, dict)]
    if not recent:
        return 0.0
    skipped = sum(1 for r in recent if r.get("calibration_skipped", False))
    return skipped / len(recent)


def _parse_catchup_runs_from_markdown(content: str) -> list[dict]:
    """Parse catch-up run entries from the freeform Markdown history section.

    Reads the ``## Catch-Up Run History`` section and returns a list of
    minimal run dicts that satisfy the ``_MIN_CATCHUP_RUNS`` gate::

        [{"timestamp": "YYYY-MM-DDT00:00:00Z", "briefing_format": "standard"}, ...]

    Returns an empty list when the section is absent or unparseable.
    Known limitation: ``calibration_skipped`` is always False for
    markdown-parsed runs (field not recorded in freeform history).
    """
    history_match = re.search(r"## Catch-Up Run History", content)
    if not history_match:
        return []

    # Slice to the section; stop at the next ## heading
    section = content[history_match.end():]
    next_h2 = re.search(r"\n## ", section)
    if next_h2:
        section = section[:next_h2.start()]

    runs: list[dict] = []
    current_run: dict | None = None

    for line in section.splitlines():
        stripped = line.strip()
        h3 = re.match(r"^### (\d{4}-\d{2}-\w+)(?:\s+\(([^)]*)\))?", stripped)
        if h3:
            date_slug = h3.group(1)  # e.g. "2026-03-20b"
            label = (h3.group(2) or "").lower()  # e.g. "standard catch-up"
            # Extract ISO date (strip trailing letter suffixes like "b", "c")
            date_m = re.match(r"(\d{4}-\d{2}-\d{2})", date_slug)
            date_part = date_m.group(1) if date_m else date_slug[:10]
            briefing_format = "flash" if "flash" in label else "standard"
            current_run = {
                "timestamp": f"{date_part}T00:00:00Z",
                "briefing_format": briefing_format,
                "calibration_skipped": False,  # not recorded in freeform history
            }
            runs.append(current_run)
        elif current_run is not None and stripped.startswith("- mode:"):
            # Override format from the explicit mode line if present
            if "flash" in stripped:
                current_run["briefing_format"] = "flash"
            elif "standard" in stripped:
                current_run["briefing_format"] = "standard"

    return runs


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI: python scripts/self_model_writer.py"""
    from lib.common import ARTHA_DIR  # type: ignore[import]

    state_dir = ARTHA_DIR / "state"
    writer = SelfModelWriter()
    updated = writer.update(
        memory_path=state_dir / "memory.md",
        health_check_path=state_dir / "health-check.md",
        self_model_path=state_dir / "self_model.md",
    )
    if updated:
        print("✅ Self-model updated")
        return 0
    print("ℹ️  Self-model unchanged (insufficient data or no change)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
