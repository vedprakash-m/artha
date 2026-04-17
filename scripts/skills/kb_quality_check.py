"""scripts/skills/kb_quality_check.py — Work KB Quality Checker Skill.

Scans knowledge/*.md domain KB files using heuristic rules across 7 quality
dimensions. Writes scored results to state/kb_quality_results.json.

If any KB falls below the configured threshold (default 8.0/10), spawns
kb_improver.py as a non-blocking background subprocess and sets
improvement_triggered=True in the results.

Design constraints:
  - Deterministic only — no LLM calls (DP-3)
  - Zero new dependencies — stdlib only (DP-8)
  - BaseSkill.pull() takes NO arguments (ARCH-1)
  - Runs in daily cadence, non-blocking (fires after pipeline stage)

Output schema (state/kb_quality_results.json):
  {
    "run_at": "<ISO timestamp>",
    "threshold": 8.0,
    "overall_avg": 7.4,
    "below_threshold": ["xpf-fleet-health-kb", ...],
    "improvement_triggered": true,
    "files": {
      "xpf-repairs-kb": {
        "score": 8.2,
        "dimensions": {"accuracy": 8, "completeness": 8, ...},
        "issues": ["stub in §5", ...]
      },
      ...
    }
  }

Skill registry entry (config/skills.yaml):
  kb_quality_check:
    enabled: true
    priority: P1
    cadence: daily
    class: background
    command_namespace: work
    requires_vault: false
    safety_critical: false
    description: "Score all domain KBs on 7 quality dimensions. Trigger improvement if below threshold."
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

_log = logging.getLogger("artha.skills.kb_quality_check")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD = 9.5

# Domain KB files to score (relative to knowledge/)
_DOMAIN_KB_FILES = [
    "xpf-repairs-kb.md",
    "xpf-deployment-kb.md",
    "xpf-networking-kb.md",
    "xpf-monitoring-kb.md",
    "xpf-safety-kb.md",
    "xpf-fleet-health-kb.md",
    "armada-kb.md",
    "titan-convergence-kb.md",
    "rubik-kb.md",
    "dd-xpf-kb.md",
    "sku-generations-kb.md",
    "xstore-kb.md",
]

# Patterns for quality dimension scoring
_TODO_PATTERN = re.compile(r"\b(TODO|TBD|PLACEHOLDER|stub|unverified|needs verification)\b", re.IGNORECASE)
_KQL_BLOCK_PATTERN = re.compile(r"```(?:kusto|kql)\s*\n(.+?)```", re.DOTALL | re.IGNORECASE)
_KQL_REF_PATTERN = re.compile(r"\bGQ-\d+\b|\bQ-[A-Z]\d+\b|\bQ-[A-Z]{1,3}-\d+\b")
_H2_PATTERN = re.compile(r"^## .+", re.MULTILINE)
_H3_PATTERN = re.compile(r"^### .+", re.MULTILINE)
_LAST_UPDATED_PATTERN = re.compile(
    r"(?:last[_\s]updated|generated|as of)\s*[:\|]?\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_DATE_IN_CONTENT_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_CROSS_REF_PATTERN = re.compile(r"\[.*?kb\.md\]|see.*?kb\.md|→.*?kb\.md", re.IGNORECASE)
_ADO_PATTERN = re.compile(r"\bADO\s*\d{6,}\b|\b\d{7,8}\b")
_ICM_PATTERN = re.compile(r"\bIcM\s*\d{6,}\b|\b\d{9,}\b")
_PLAYBOOK_PATTERN = re.compile(r"^#+\s*(playbook|debug|triage|investigation|war room)", re.MULTILINE | re.IGNORECASE)
_BROKEN_TIMESTAMP = re.compile(r"`[^`]*\btimestamp\b[^`]*`", re.IGNORECASE)
_PRECISE_TIMESTAMP = re.compile(r"PreciseTimeStamp", re.IGNORECASE)
_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.+?)\n---", re.DOTALL)
_SECTION_NUMBER_PATTERN = re.compile(r"^## (\d+)\.", re.MULTILINE)
_DUPLICATE_H2_PATTERN = re.compile(r"^## (.+)$", re.MULTILINE)


def _score_accuracy(content: str, kb_name: str) -> tuple[int, list[str]]:
    """Score accuracy 0–10. Penalise broken queries, speculation, future-dated text."""
    score = 10
    issues: list[str] = []

    # Penalise TODO/TBD occurrences (up to -3)
    todo_count = len(_TODO_PATTERN.findall(content))
    if todo_count >= 10:
        score -= 3
        issues.append(f"{todo_count} TODO/TBD markers")
    elif todo_count >= 5:
        score -= 2
        issues.append(f"{todo_count} TODO/TBD markers")
    elif todo_count >= 1:
        score -= 1
        issues.append(f"{todo_count} TODO/TBD markers")

    # Penalise TIMESTAMP usage in KQL blocks (should be PreciseTimeStamp) (-2)
    kql_blocks = _KQL_BLOCK_PATTERN.findall(content)
    for block in kql_blocks:
        if re.search(r"\btimestamp\b", block, re.IGNORECASE) and not re.search(r"PreciseTimeStamp", block):
            score -= 2
            issues.append("KQL block uses 'timestamp' instead of 'PreciseTimeStamp'")
            break

    # Penalise speculative/opinion language in authoritative sections (-1)
    speculative = re.search(r"(?:unclear|hypothesis|hypothesis|debated|speculative|possibly|may or may not)", content, re.IGNORECASE)
    if speculative:
        score -= 1
        issues.append("Speculative language in content")

    # Penalise future-dated text vs file's own last_updated date (-1)
    fm_match = _LAST_UPDATED_PATTERN.search(content[:2000])
    if fm_match:
        try:
            file_date = datetime.fromisoformat(fm_match.group(1))
            dates_in_body = _DATE_IN_CONTENT_PATTERN.findall(content[2000:])
            future_dates = [d for d in dates_in_body if d > fm_match.group(1) and d < "2027"]
            if len(future_dates) > 3:
                score -= 1
                issues.append(f"Future-dated references ({len(future_dates)}) beyond last_updated")
        except (ValueError, AttributeError):
            pass

    return max(0, score), issues


def _score_completeness(content: str, kb_name: str) -> tuple[int, list[str]]:
    """Score completeness 0–10. Check sections present, stubs, key elements."""
    score = 10
    issues: list[str] = []

    h2_sections = _H2_PATTERN.findall(content)
    section_count = len(h2_sections)

    # Expect at minimum: arch/overview, kusto queries, key people, cross-references
    required_patterns = [
        (re.compile(r"^## .*(kusto|queries|query)", re.MULTILINE | re.IGNORECASE), "Kusto section"),
        (re.compile(r"^## .*(people|contacts|team)", re.MULTILINE | re.IGNORECASE), "Key people section"),
        (re.compile(r"^## .*(cross.ref|references|glossary)", re.MULTILINE | re.IGNORECASE), "Cross-references section"),
        (re.compile(r"^## .*(playbook|debug|triage|escalat)", re.MULTILINE | re.IGNORECASE), "Playbook section"),
    ]
    for pattern, label in required_patterns:
        if not pattern.search(content):
            score -= 1
            issues.append(f"Missing {label}")

    # Penalise very thin sections (< 50 chars after heading) (-2 max)
    stub_sections = 0
    for m in re.finditer(r"^(## .+)\n(.*?)(?=^##|\Z)", content, re.MULTILINE | re.DOTALL):
        body = m.group(2).strip()
        if len(body) < 50 and body:
            stub_sections += 1
    if stub_sections >= 3:
        score -= 2
        issues.append(f"{stub_sections} stub H2 sections (< 50 chars)")
    elif stub_sections >= 1:
        score -= 1
        issues.append(f"{stub_sections} stub H2 sections")

    # Penalise if total sections < 8 (-2)
    if section_count < 8:
        score -= 2
        issues.append(f"Only {section_count} top-level sections (expected ≥ 8)")
    elif section_count < 5:
        score -= 3
        issues.append(f"Very few sections: {section_count}")

    return max(0, score), issues


def _score_freshness(content: str, kb_name: str) -> tuple[int, list[str]]:
    """Score freshness 0–10. Based on last_updated date and age of metrics."""
    score = 10
    issues: list[str] = []
    today = datetime.now(timezone.utc).date()

    # Find last_updated in frontmatter or first 1000 chars
    fm_match = _LAST_UPDATED_PATTERN.search(content[:2000])
    if not fm_match:
        score -= 3
        issues.append("No last_updated date found")
        return max(0, score), issues

    try:
        last_updated = datetime.fromisoformat(fm_match.group(1)).date()
        age_days = (today - last_updated).days

        if age_days > 180:
            score -= 4
            issues.append(f"Last updated {age_days} days ago (> 180)")
        elif age_days > 90:
            score -= 3
            issues.append(f"Last updated {age_days} days ago (> 90)")
        elif age_days > 60:
            score -= 2
            issues.append(f"Last updated {age_days} days ago (> 60)")
        elif age_days > 30:
            score -= 1
            issues.append(f"Last updated {age_days} days ago (> 30)")
    except (ValueError, AttributeError):
        score -= 2
        issues.append("Cannot parse last_updated date")

    # Check for metric sections with data anchored > 60 days old
    metric_date_match = re.search(r"(?:as of|current state|metrics?)\s*[:\(]?\s*(\w+\s+\d{4}|\d{4}-\d{2}-\d{2})", content, re.IGNORECASE)
    if metric_date_match:
        # Already penalised by age check above — no double penalty
        pass

    return max(0, score), issues


def _score_structure(content: str, kb_name: str) -> tuple[int, list[str]]:
    """Score structure 0–10. Check hierarchy, numbering, duplication."""
    score = 10
    issues: list[str] = []

    # Check for broken section numbering (x.5 style oddities)
    half_sections = re.findall(r"^## \d+\.\d+\s", content, re.MULTILINE)
    if half_sections:
        score -= 1
        issues.append(f"Non-integer section numbering: {half_sections[:3]}")

    # Check for duplicated H2 headings
    h2_titles = [m.group(1).strip().lower() for m in re.finditer(r"^## (.+)$", content, re.MULTILINE)]
    seen: set[str] = set()
    dups = [t for t in h2_titles if t in seen or seen.add(t)]  # type: ignore[func-returns-value]
    if dups:
        score -= 2
        issues.append(f"Duplicate H2 headings: {dups[:3]}")

    # Check for content bleed (person-row pattern inside non-people sections)
    # Detect table rows with email-like content in non-people sections
    content_blocks = re.split(r"^## .+$", content, flags=re.MULTILINE)
    h2_titles_ordered = re.findall(r"^## (.+)$", content, re.MULTILINE)
    for i, (title, block) in enumerate(zip(h2_titles_ordered, content_blocks[1:])):
        is_people = bool(re.search(r"people|team|contacts", title, re.IGNORECASE))
        if not is_people:
            email_rows = re.findall(r"\|.*@microsoft\.com.*\|", block)
            if len(email_rows) > 2:
                score -= 2
                issues.append(f"Possible copy-paste corruption: email rows in '{title}'")
                break

    # Check overall size — very thin KBs lack depth
    word_count = len(content.split())
    if word_count < 1000:
        score -= 3
        issues.append(f"Very thin KB ({word_count} words)")
    elif word_count < 3000:
        score -= 1
        issues.append(f"Thin KB ({word_count} words)")

    return max(0, score), issues


def _score_kusto(content: str, kb_name: str) -> tuple[int, list[str]]:
    """Score Kusto coverage 0–10. Count real KQL blocks vs references."""
    score = 10
    issues: list[str] = []

    kql_blocks = _KQL_BLOCK_PATTERN.findall(content)
    kql_refs = _KQL_REF_PATTERN.findall(content)
    ref_count = len(set(kql_refs))
    block_count = len(kql_blocks)

    # If many refs but few blocks — queries are stubs
    if ref_count >= 5 and block_count == 0:
        score -= 5
        issues.append(f"{ref_count} query refs but zero KQL code blocks")
    elif ref_count >= 3 and block_count == 0:
        score -= 4
        issues.append(f"{ref_count} query refs but zero KQL code blocks")
    elif block_count == 0 and ref_count == 0:
        score -= 4
        issues.append("No KQL blocks and no query references")
    elif ref_count > block_count * 2 and block_count < 3:
        score -= 2
        issues.append(f"Only {block_count} KQL blocks for {ref_count} query references")

    # Check parameterization — hard-coded time windows are fragile
    hardcoded = sum(
        1 for b in kql_blocks
        if re.search(r'ago\(\d+[dh]\)', b) and not re.search(r'<.*time.*>', b)
    )
    if hardcoded >= 3:
        score -= 1
        issues.append(f"{hardcoded} KQL blocks with hard-coded time windows")

    # Reward good coverage
    if block_count >= 8:
        pass  # Full score
    elif block_count >= 5:
        score -= 1
    elif block_count >= 3:
        score -= 2
        if not issues:
            issues.append(f"Only {block_count} KQL blocks — consider adding more coverage")
    elif block_count >= 1:
        score -= 3
        issues.append(f"Only {block_count} KQL block(s)")

    return max(0, score), issues


def _score_crossrefs(content: str, kb_name: str) -> tuple[int, list[str]]:
    """Score cross-references 0–10. Check KB links, ADO items, IcMs."""
    score = 10
    issues: list[str] = []

    # Check for KB cross-reference section
    has_crossref_section = bool(re.search(r"^## .*(cross.ref|references)", content, re.MULTILINE | re.IGNORECASE))
    if not has_crossref_section:
        score -= 2
        issues.append("No cross-references section")

    # Count KB file links
    kb_links = re.findall(r"[a-z-]+-kb\.md", content)
    unique_kb_links = len(set(kb_links))
    if unique_kb_links == 0:
        score -= 2
        issues.append("No links to other KB files")
    elif unique_kb_links < 3:
        score -= 1
        issues.append(f"Only {unique_kb_links} KB cross-links")

    # Check for ADO item references
    ado_refs = _ADO_PATTERN.findall(content)
    if not ado_refs:
        score -= 1
        issues.append("No ADO item references")

    # Check for IcM references (XPF operational KBs should have these)
    is_operational = bool(re.search(r"xpf|xstore|armada|titan|rubik|dd-xpf", kb_name, re.IGNORECASE))
    icm_refs = _ICM_PATTERN.findall(content)
    if is_operational and not icm_refs and not re.search(r"IcM", content):
        score -= 1
        issues.append("No IcM incident references")

    return max(0, score), issues


def _score_actionability(content: str, kb_name: str) -> tuple[int, list[str]]:
    """Score actionability 0–10. Debug playbooks, executable queries, escalation."""
    score = 10
    issues: list[str] = []

    # Check for debug/triage playbook sections
    playbook_count = len(_PLAYBOOK_PATTERN.findall(content))
    if playbook_count == 0:
        score -= 3
        issues.append("No debug/triage playbooks found")
    elif playbook_count == 1:
        score -= 1
        issues.append("Only 1 playbook section")

    # Check for escalation guidance
    has_escalation = bool(re.search(r"escalat", content, re.IGNORECASE))
    if not has_escalation:
        score -= 1
        issues.append("No escalation guidance")

    # Check for key people section
    has_people = bool(re.search(r"^## .*(people|contacts|team)", content, re.MULTILINE | re.IGNORECASE))
    if not has_people:
        score -= 1
        issues.append("No key people/contacts section")

    # Reward for having executable KQL (already scored in kusto, no double-penalty)
    kql_blocks = _KQL_BLOCK_PATTERN.findall(content)
    if not kql_blocks:
        score -= 1
        issues.append("No executable KQL — limits live triage capability")

    return max(0, score), issues


def _score_kb(kb_path: Path) -> dict[str, Any]:
    """Score a single KB file on all 7 dimensions. Returns scoring dict."""
    try:
        content = kb_path.read_text(encoding="utf-8")
    except OSError as e:
        return {
            "score": 0.0,
            "dimensions": {},
            "issues": [f"Cannot read file: {e}"],
            "file": kb_path.name,
        }

    kb_name = kb_path.stem

    dim_scores: dict[str, int] = {}
    all_issues: list[str] = []

    for dim_name, scorer in [
        ("accuracy", _score_accuracy),
        ("completeness", _score_completeness),
        ("freshness", _score_freshness),
        ("structure", _score_structure),
        ("kusto", _score_kusto),
        ("cross_refs", _score_crossrefs),
        ("actionability", _score_actionability),
    ]:
        s, iss = scorer(content, kb_name)
        dim_scores[dim_name] = s
        all_issues.extend([f"[{dim_name}] {i}" for i in iss])

    avg_score = round(sum(dim_scores.values()) / len(dim_scores), 1)

    return {
        "score": avg_score,
        "dimensions": dim_scores,
        "issues": all_issues,
        "file": kb_path.name,
        "word_count": len(content.split()),
    }


class KbQualityCheckSkill(BaseSkill):
    """Work KB Quality Checker skill.

    Scans all domain KB files heuristically and scores them on 7 dimensions
    (accuracy, completeness, freshness, structure, kusto, cross_refs,
    actionability) on a 0–10 scale.

    If any KB < threshold, spawns kb_improver.py as a non-blocking background
    process and sets improvement_triggered=True in the output.
    """

    def __init__(self, artha_dir: Path) -> None:
        super().__init__(name="kb_quality_check", priority="P1")
        self._artha_dir = artha_dir
        self._kb_dir = artha_dir / "knowledge"
        self._output_path = artha_dir / "state" / "kb_quality_results.json"
        self._improver_path = artha_dir / "scripts" / "kb_improver.py"
        self._threshold = _DEFAULT_THRESHOLD

    def pull(self) -> dict[str, Any]:
        """Scan all domain KB files and return quality results."""
        results: dict[str, Any] = {}
        below_threshold: list[str] = []

        if not self._kb_dir.exists():
            _log.warning("KB directory not found: %s", self._kb_dir)
            return {
                "error": f"KB directory not found: {self._kb_dir}",
                "files": {},
                "below_threshold": [],
                "overall_avg": 0.0,
                "improvement_triggered": False,
            }

        for kb_file in _DOMAIN_KB_FILES:
            kb_path = self._kb_dir / kb_file
            if not kb_path.exists():
                _log.warning("KB file not found: %s", kb_path)
                results[kb_path.stem] = {
                    "score": 0.0,
                    "dimensions": {},
                    "issues": ["File not found"],
                    "file": kb_file,
                }
                below_threshold.append(kb_path.stem)
                continue

            scored = _score_kb(kb_path)
            results[kb_path.stem] = scored
            if scored["score"] < self._threshold:
                below_threshold.append(kb_path.stem)

        scores = [v["score"] for v in results.values() if isinstance(v.get("score"), (int, float))]
        overall_avg = round(sum(scores) / len(scores), 1) if scores else 0.0

        improvement_triggered = False
        if below_threshold:
            improvement_triggered = self._trigger_improvement(below_threshold, results)

        return {
            "files": results,
            "below_threshold": below_threshold,
            "overall_avg": overall_avg,
            "threshold": self._threshold,
            "improvement_triggered": improvement_triggered,
        }

    def _trigger_improvement(self, below_threshold: list[str], results: dict) -> bool:
        """Spawn kb_improver.py as a non-blocking background process."""
        if not self._improver_path.exists():
            _log.warning("kb_improver.py not found at %s — skipping improvement trigger", self._improver_path)
            return False
        try:
            subprocess.Popen(
                [sys.executable, str(self._improver_path), "--auto"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _log.info(
                "kb_quality_check: triggered kb_improver.py for %d below-threshold KBs: %s",
                len(below_threshold),
                below_threshold,
            )
            return True
        except Exception as e:
            _log.warning("Failed to spawn kb_improver.py: %s", e)
            return False

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Write results to state/ and return structured output."""
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "run_at": datetime.now(timezone.utc).isoformat(),
                **raw_data,
            }
            self._output_path.write_text(
                __import__("json").dumps(payload, indent=2),
                encoding="utf-8",
            )
            _log.info(
                "kb_quality_check: overall_avg=%.1f, below_threshold=%d, written to %s",
                raw_data.get("overall_avg", 0),
                len(raw_data.get("below_threshold", [])),
                self._output_path,
            )
        except Exception as e:
            _log.warning("Could not write kb_quality_results.json: %s", e)
        return raw_data

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "last_run": self.last_run,
            "error": self.error,
        }

    @property
    def compare_fields(self) -> list[str]:
        return ["overall_avg", "below_threshold"]


def get_skill(artha_dir: Path) -> KbQualityCheckSkill:
    """Entry point for skill_runner.py."""
    return KbQualityCheckSkill(artha_dir)
