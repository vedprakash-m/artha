# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/correction_tracker.py — Agent feedback loop: correction capture (EAR-12).

Captures user corrections from natural language and persists them in
per-agent correction files.  On next invocation, top corrections are
injected as anti-patterns into the delegation prompt.

Correction detection: conservative pattern matching only (no LLM).
Supported correction patterns:
  - "Wrong — X is not Y, it's Z"
  - "Incorrect: ..."
  - "Actually, ..."
  - "Not X but Y"
  - "Correct: ..."

Data model:
  tmp/ext-agent-memory/<agent-name>/corrections.md

Schema:
  ---
  schema_version: "1.0"
  ---
  # Corrections for <agent-name>
  ## [YYYY-MM-DD] <entity>
  - Wrong: <wrong_claim>
  - Correct: <correct_claim>
  - Source: user correction

Safety:
  - Max 20 corrections per agent (oldest evicted).
  - Corrections injected as SCOPED context (not for untrusted agents).
  - Correction detection uses conservative patterns (precision ≥ 0.9 target).
  - Injection goes through injection scan before dispatch.

Ref: specs/ext-agent-reloaded.md §EAR-12
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MEMORY_DIR = (
    Path(__file__).resolve().parent.parent.parent / "tmp" / "ext-agent-memory"
)
_MAX_CORRECTIONS = 20
_MAX_INJECT_CORRECTIONS = 5
_SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Correction detection patterns
# ---------------------------------------------------------------------------

_CORRECTION_PATTERNS: list[re.Pattern] = [
    # "Wrong — X is Y" or "Wrong: X"
    re.compile(
        r'\b(?:wrong|incorrect|that\'?s wrong|not right)\b[^.!?]{0,10}'
        r'[—\-–:]\s*(.{5,120})',
        re.IGNORECASE,
    ),
    # "Actually, ..."
    re.compile(
        r'\bactually[,\s]+(?:it\'?s\s+)?(.{5,120})',
        re.IGNORECASE,
    ),
    # "Not X but Y" / "not X, it's Y"
    re.compile(
        r'\bnot\s+(.{3,40})\s+but\s+(.{3,80})',
        re.IGNORECASE,
    ),
    # "Correct: ..." / "The correct answer is..."
    re.compile(
        r'\b(?:correct(?:ion)?:|the correct (?:answer|fact) is)\s+(.{5,120})',
        re.IGNORECASE,
    ),
    # "It should be ..."
    re.compile(
        r'\bit should (?:be|say)\s+(.{5,120})',
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Weighted correction scoring (ST-02 — specs/steal.md §15.2.3)
# ---------------------------------------------------------------------------

_WEIGHTED_CORRECTIONS: list[tuple[float, re.Pattern]] = [
    # Strong indicators (weight 1.0)
    (1.0, re.compile(r'\b(?:wrong|incorrect|that\'?s wrong|not right)\b', re.IGNORECASE)),
    (1.0, re.compile(r'\bcorrection[:\s]', re.IGNORECASE)),
    (1.0, re.compile(r'\bit should (?:be|say)\b', re.IGNORECASE)),
    # Medium indicators (weight 0.7)
    (0.7, re.compile(r'\bactually,?\s+(?:it\'?s|that\'?s|the)\b', re.IGNORECASE)),
    (0.7, re.compile(r'\bnot\s+\S+\s+but\b', re.IGNORECASE)),
    # Weak indicators (weight 0.3)
    (0.3, re.compile(r'\bno[\s,]+wait\b', re.IGNORECASE)),
    (0.3, re.compile(r'\bI meant\b', re.IGNORECASE)),
    (0.3, re.compile(r'\bactually\b', re.IGNORECASE)),
]

_CORRECTION_THRESHOLD: float = 0.5

# Artha command patterns: skip scoring when ≤5 words and matches one of these
_COMMAND_SKIP_RE = re.compile(
    r'^(?:brief|work|items|goals|content|guide|health|domain\s+\S+|'
    r'catch\s+me\s+up|flash\s+briefing|ok\s+continue|yes|no)$',
    re.IGNORECASE,
)


def correction_score(text: str) -> float:
    """Return a weighted score [0.0..1.0] indicating likelihood the text is a correction.

    Exclusion rule: if ≤5 words AND matches a known Artha command, returns 0.0.
    Score is capped at 1.0; the first matching weight at each tier contributes once.
    """
    stripped = text.strip()
    if len(stripped.split()) <= 5 and _COMMAND_SKIP_RE.match(stripped):
        return 0.0

    total = sum(
        weight
        for weight, pattern in _WEIGHTED_CORRECTIONS
        if pattern.search(stripped)
    )
    return min(1.0, total)


def compute_quality_metrics(session_texts: list[str]) -> dict:
    """Compute correction quality metrics for a session's user messages.

    Args:
        session_texts: List of raw user message strings from a session.

    Returns:
        Dict with:
          correction_count: int   — items scoring at or above _CORRECTION_THRESHOLD
          total_count: int        — total items evaluated
          correction_rate: float  — fraction scoring above threshold
          mean_score: float       — mean correction score across all items
    """
    if not session_texts:
        return {"correction_count": 0, "total_count": 0, "correction_rate": 0.0, "mean_score": 0.0}

    scores = [correction_score(t) for t in session_texts]
    above = [s for s in scores if s >= _CORRECTION_THRESHOLD]

    return {
        "correction_count": len(above),
        "total_count": len(session_texts),
        "correction_rate": round(len(above) / len(scores), 4),
        "mean_score": round(sum(scores) / len(scores), 4),
    }


# ---------------------------------------------------------------------------
# Per-agent lock
# ---------------------------------------------------------------------------

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(agent_name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if agent_name not in _LOCKS:
            _LOCKS[agent_name] = threading.Lock()
        return _LOCKS[agent_name]


# ---------------------------------------------------------------------------
# Parsed correction type
# ---------------------------------------------------------------------------

class Correction:
    __slots__ = ("entity", "wrong", "correct", "date_str", "source")

    def __init__(
        self,
        entity: str,
        wrong: str,
        correct: str,
        date_str: str = "",
        source: str = "user correction",
    ) -> None:
        self.entity = entity
        self.wrong = wrong
        self.correct = correct
        self.date_str = date_str or date.today().isoformat()
        self.source = source

    def to_md_block(self) -> str:
        return (
            f"\n## [{self.date_str}] {self.entity}\n"
            f"- Wrong: {self.wrong}\n"
            f"- Correct: {self.correct}\n"
            f"- Source: {self.source}\n"
        )

    def to_anti_pattern(self) -> str:
        return f"- {self.entity}: NOT '{self.wrong}' → correct: '{self.correct}'"


# ---------------------------------------------------------------------------
# CorrectionTracker
# ---------------------------------------------------------------------------

class CorrectionTracker:
    """Captures, persists, and injects user corrections.

    Parameters:
        agent_name: Agent slug.
        memory_dir: Override base dir (for testing).
    """

    def __init__(self, agent_name: str, memory_dir: Path | None = None) -> None:
        self._agent = agent_name
        self._base = (memory_dir or _MEMORY_DIR) / agent_name
        self._corrections_file = self._base / "corrections.md"
        self._lock = _get_lock(agent_name)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_correction(user_message: str) -> Optional["Correction"]:
        """Detect a correction in a user message.

        Returns a Correction or None if no correction detected.
        Conservative: only unambiguous correction patterns match.
        """
        msg = user_message.strip()
        if len(msg) < 10:
            return None

        for pat in _CORRECTION_PATTERNS:
            m = pat.search(msg)
            if not m:
                continue

            groups = [g.strip() for g in m.groups() if g and g.strip()]
            if not groups:
                continue

            if len(groups) == 2:
                # "not X but Y" pattern
                wrong_claim = groups[0][:80]
                correct_claim = groups[1][:80]
                entity = wrong_claim[:40]
            else:
                correct_claim = groups[0][:100]
                wrong_claim = "(previous response)"
                entity = correct_claim[:40]

            # Safety: reject if the correction triggers injection patterns
            try:
                from lib.injection_detector import InjectionDetector  # noqa: PLC0415
                det = InjectionDetector()
                if det.scan(correct_claim).injection_detected:
                    return None
            except ImportError:
                pass

            return Correction(
                entity=entity,
                wrong=wrong_claim,
                correct=correct_claim,
            )

        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_correction(self, correction: Correction) -> None:
        """Append correction to corrections.md. Thread-safe. Never raises."""
        try:
            with self._lock:
                self._base.mkdir(parents=True, exist_ok=True)

                # Read existing
                existing_text = ""
                if self._corrections_file.exists():
                    existing_text = self._corrections_file.read_text(
                        encoding="utf-8", errors="ignore"
                    )

                # Count existing entries
                entry_count = len(re.findall(r'^## \[', existing_text, re.MULTILINE))

                # Evict oldest if at cap
                if entry_count >= _MAX_CORRECTIONS:
                    existing_text = self._evict_oldest(existing_text)

                # Append new entry
                if not existing_text.strip():
                    header = (
                        f"---\nschema_version: \"{_SCHEMA_VERSION}\"\n---\n"
                        f"# Corrections for {self._agent}\n"
                    )
                    existing_text = header

                new_text = existing_text.rstrip() + "\n" + correction.to_md_block()

                # Atomic write
                tmp_fd, tmp_path = tempfile.mkstemp(
                    dir=self._base, prefix=".corr_tmp_", suffix=".md"
                )
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                        fh.write(new_text)
                    os.replace(tmp_path, self._corrections_file)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _evict_oldest(text: str) -> str:
        """Remove the oldest correction entry block."""
        # Find all entry positions
        positions = [m.start() for m in re.finditer(r'^## \[', text, re.MULTILINE)]
        if len(positions) < 2:
            # Only one entry — clear all corrections, keep header
            header_end = text.find("# Corrections for")
            if header_end >= 0:
                line_end = text.find("\n", header_end)
                return text[: line_end + 1] if line_end >= 0 else text[:header_end]
            return text
        # Remove from first entry start up to second entry start
        return text[: positions[0]] + text[positions[1] :]

    # ------------------------------------------------------------------
    # Load & Inject
    # ------------------------------------------------------------------

    def load_corrections(self) -> list[Correction]:
        """Load all persisted corrections for this agent."""
        corrections: list[Correction] = []
        try:
            if not self._corrections_file.exists():
                return corrections

            text = self._corrections_file.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(
                r'^## \[(\d{4}-\d{2}-\d{2})\]\s+(.+?)\n'
                r'- Wrong:\s*(.+?)\n'
                r'- Correct:\s*(.+?)\n',
                text,
                re.MULTILINE,
            ):
                corrections.append(Correction(
                    entity=m.group(2).strip(),
                    wrong=m.group(3).strip(),
                    correct=m.group(4).strip(),
                    date_str=m.group(1),
                ))
        except Exception:  # noqa: BLE001
            pass
        return corrections

    def build_anti_pattern_block(self) -> str:
        """Build a formatted anti-pattern injection block from top corrections.

        Returns empty string if no corrections.
        """
        corrections = self.load_corrections()
        if not corrections:
            return ""

        # Most recent first (assuming file order = chronological)
        top = corrections[-_MAX_INJECT_CORRECTIONS:]
        lines = ["## KNOWN CORRECTIONS (do not repeat these mistakes)"]
        for c in reversed(top):
            lines.append(c.to_anti_pattern())
        return "\n".join(lines) + "\n"

    def get_all_corrections(self) -> list[dict]:
        """Return corrections as plain dicts (for display commands)."""
        return [
            {
                "entity": c.entity,
                "wrong": c.wrong,
                "correct": c.correct,
                "date": c.date_str,
            }
            for c in self.load_corrections()
        ]
