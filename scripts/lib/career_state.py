"""scripts/lib/career_state.py — Deterministic state helpers for Career Search Intelligence.

Implements:
  - reconcile_summary()          Recompute frontmatter summary: block from tracker table
  - recompute_scores()           Deterministic composite score from per-dimension integers
  - _patch_frontmatter_field()   Create-or-update a dot-notation key in YAML frontmatter
  - deep_freeze()                Recursively freeze nested dicts/lists for DomainSignal metadata
  - fingerprint_posting()        Generate posting deduplication fingerprint
  - fingerprint_action()         Generate action proposal deduplication key

All writes use the local write_state_atomic() helper (atomic tmp→rename).
All Python state mutations are deterministic — zero LLM calls in this module.

Ref: specs/artha-tech-spec.md §30, specs/artha-prd.md FR-25
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Local file I/O helpers (inlined to avoid work.helpers cross-dependency)
# ---------------------------------------------------------------------------

def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a Markdown state file."""
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore[import]  # noqa: PLC0415
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                return yaml.safe_load(text[3:end]) or {}
    except Exception:
        pass
    return {}


def _read_body(path: Path) -> str:
    """Return Markdown body (after frontmatter) of a state file."""
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                return text[end + 3:].strip()
        return text.strip()
    except Exception:
        return ""


def _validate_frontmatter(content: str) -> None:
    """Raise ValueError if YAML frontmatter is malformed."""
    import yaml  # noqa: PLC0415
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("Malformed YAML frontmatter: missing closing '---'")
    yaml.safe_load(parts[1])


def write_state_atomic(path: Path, content: str) -> None:
    """Write content to path atomically via tmp→validate→os.replace()."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=target.parent, suffix=".tmp", prefix=".career-"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        if content.startswith("---"):
            _validate_frontmatter(content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATE_FILE = _REPO_ROOT / "state" / "career_search.md"

# Statuses that count toward average_score computation (W-F8)
SCORED_STATUSES: frozenset[str] = frozenset({
    "Evaluated",
    "PartialEval",
    "Applied",
    "Responded",
    "Interview",
    "Offer",
})

# All canonical tracker statuses
ALL_STATUSES: frozenset[str] = frozenset({
    *SCORED_STATUSES,
    "Rejected",
    "Discarded",
    "SKIP",
})

# Closed-vocabulary tag sets (FR-CS-7)
_CAPABILITY_DOMAIN_TAGS: frozenset[str] = frozenset({
    "Engineering", "Product", "Leadership", "Research",
    "Customer", "Operations", "Data", "Security", "Infrastructure",
})
_LEADERSHIP_SIGNAL_TAGS: frozenset[str] = frozenset({
    "People Management", "Technical Leadership", "Cross-Functional",
    "Mentoring", "Strategy", "Crisis Response",
})
_METRIC_TYPE_TAGS: frozenset[str] = frozenset({
    "Revenue", "Cost Reduction", "Scale", "Quality",
    "Speed", "Adoption", "Reliability",
})

# Regex to extract per-dimension scores from a report markdown body
_SCORE_ROW_PATTERN = re.compile(
    r"\|\s*(CV Match|North Star Alignment|Compensation|Cultural Signals|Level Fit|Red Flags)"
    r"\s*\|\s*[\d.]+\s*\|\s*(\d)\s*\|",
    re.IGNORECASE,
)

_DIMENSION_KEY_MAP: dict[str, str] = {
    "cv match": "cv_match",
    "north star alignment": "north_star",
    "compensation": "compensation",
    "cultural signals": "culture",
    "level fit": "level_fit",
    "red flags": "red_flags",
}


# ---------------------------------------------------------------------------
# Tracker table parsing
# ---------------------------------------------------------------------------

def _parse_tracker_rows(body: str) -> list[dict[str, str]]:
    """Parse the Applications Markdown table into a list of row dicts.

    Returns only rows with a valid Status column value.
    Malformed rows (wrong column count, unparseable score) are skipped
    with a WARNING log — they increment validation_errors.
    """
    rows: list[dict[str, str]] = []
    in_table = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "Company" in stripped and "Role" in stripped:
            in_table = True
            continue
        if in_table and stripped.startswith("|---"):
            continue
        if in_table and stripped.startswith("|"):
            parts = [p.strip() for p in stripped.strip("|").split("|")]
            if len(parts) < 9:
                log.warning("career_state: malformed tracker row (too few columns): %r", line)
                continue
            row = {
                "num": parts[0],
                "date": parts[1],
                "company": parts[2],
                "role": parts[3],
                "score_raw": parts[4],
                "status": parts[5],
                "pdf": parts[6],
                "report": parts[7],
                "notes": parts[8],
            }
            if row["status"] not in ALL_STATUSES:
                log.warning(
                    "career_state: unknown status %r in row %s — skipping",
                    row["status"],
                    row["num"],
                )
                continue
            rows.append(row)
        elif in_table and not stripped.startswith("|") and stripped:
            # End of Applications table
            in_table = False
    return rows


def _parse_score(score_raw: str) -> Optional[float]:
    """Parse '4.3/5' → 4.3.  Returns None on failure."""
    m = re.match(r"([\d.]+)", score_raw.strip())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Core: reconcile_summary()
# ---------------------------------------------------------------------------

def reconcile_summary(state_path: Optional[Path] = None) -> bool:
    """Recompute summary.by_status from the tracker table rows.

    Reads the Markdown tracker table, counts rows by status, computes
    average_score over SCORED_STATUSES rows, then patches the frontmatter
    summary: block.

    Returns True if the summary was changed or any validation errors detected.

    This function is the ONLY authoritative source for summary: frontmatter.
    The LLM-authored tracker table is the log of record.

    Hallucination trap (SM-1): If count differs between table and prior
    frontmatter, the table wins. Emit WARNING but continue (partial-reconcile-
    with-warning — not skip-all).
    """
    path = state_path or _STATE_FILE
    if not path.exists():
        log.warning("career_state: state file not found: %s", path)
        return False

    fm = _read_frontmatter(path)
    body = _read_body(path)

    rows = _parse_tracker_rows(body)
    valid_rows: list[dict[str, str]] = []
    validation_errors = 0

    counts: dict[str, int] = {s: 0 for s in ALL_STATUSES}
    scored_values: list[float] = []

    for row in rows:
        status = row["status"]
        if status not in ALL_STATUSES:
            validation_errors += 1
            continue
        counts[status] = counts.get(status, 0) + 1
        valid_rows.append(row)
        if status in SCORED_STATUSES:
            score = _parse_score(row["score_raw"])
            if score is not None:
                scored_values.append(score)

    total = sum(counts.values())
    average_score = round(sum(scored_values) / len(scored_values), 2) if scored_values else None
    data_quality = "partial" if validation_errors > 0 else "ok"

    # Detect whether anything changed vs prior frontmatter
    prior_summary = fm.get("summary", {}) or {}
    prior_by_status = prior_summary.get("by_status", {}) or {}
    counts_changed = any(
        counts.get(s, 0) != prior_by_status.get(s, 0) for s in ALL_STATUSES
    )
    prior_avg = prior_summary.get("average_score")
    avg_changed = prior_avg != average_score

    if counts_changed or avg_changed or validation_errors > 0:
        log.info(
            "career_state: reconcile_summary changed — total=%d validation_errors=%d",
            total,
            validation_errors,
        )

    # Build updated summary dict
    new_summary: dict[str, Any] = {
        "total": total,
        "by_status": dict(counts),
        "last_eval_score": prior_summary.get("last_eval_score"),
        "average_score": average_score,
        "last_scan_date": prior_summary.get("last_scan_date"),
        "new_portal_matches": prior_summary.get("new_portal_matches", 0),
        "data_quality": data_quality,
        "validation_errors": validation_errors,
    }

    fm["summary"] = new_summary
    _write_frontmatter(path, fm, body)
    return counts_changed or avg_changed or validation_errors > 0


# ---------------------------------------------------------------------------
# Core: recompute_scores() — PE-1 deterministic fallback
# ---------------------------------------------------------------------------

def recompute_scores(report_path: Path, state_path: Optional[Path] = None) -> float:
    """Extract per-dimension integers from evaluation report and recompute composite score.

    Hallucination trap (PE-1): LLM composite score is probabilistic. This function
    re-derives it deterministically from the structured scoring table in the report.

    Uses scoring_weights from state frontmatter. If Block D (compensation) is missing
    from the report, uses scoring_weights_fallback. Returns recomputed composite score.
    """
    path = state_path or _STATE_FILE
    if not report_path.exists():
        log.error("career_state: report not found: %s", report_path)
        return 0.0

    report_text = report_path.read_text(encoding="utf-8")
    fm = _read_frontmatter(path)

    # Extract per-dimension scores from scoring table in report
    dim_scores: dict[str, int] = {}
    for m in _SCORE_ROW_PATTERN.finditer(report_text):
        dim_name = m.group(1).lower().strip()
        score_int = int(m.group(2))
        key = _DIMENSION_KEY_MAP.get(dim_name)
        if key:
            dim_scores[key] = score_int

    if not dim_scores:
        log.warning("career_state: no dimension scores extracted from %s", report_path)
        return 0.0

    # Choose weight table — fallback if compensation missing
    compensation_available = "compensation" in dim_scores
    if compensation_available:
        weights: dict[str, float] = fm.get("scoring_weights", {}) or {}
        if not weights:
            weights = {
                "cv_match": 0.30, "north_star": 0.20, "compensation": 0.15,
                "culture": 0.15, "level_fit": 0.10, "red_flags": 0.10,
            }
    else:
        weights = fm.get("scoring_weights_fallback", {}) or {}
        if not weights:
            weights = {
                "cv_match": 0.35, "north_star": 0.24, "culture": 0.18,
                "level_fit": 0.12, "red_flags": 0.11,
            }
        log.info("career_state: using scoring_weights_fallback (compensation data unavailable)")

    # Deterministic weighted sum
    total_weight = 0.0
    weighted_sum = 0.0
    for dim, score in dim_scores.items():
        w = weights.get(dim, 0.0)
        weighted_sum += w * score
        total_weight += w

    if total_weight == 0:
        log.warning("career_state: zero total weight — scores not matched to weight table")
        return 0.0

    composite = round(weighted_sum / total_weight, 2)
    log.info("career_state: recomputed score=%.2f for %s", composite, report_path.name)
    return composite


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

def _patch_frontmatter_field(state_path: Path, key: str, value: Any) -> None:
    """Create-or-update a dot-notation key in YAML frontmatter (W-F7).

    Supports one-level dot notation: 'summary.average_score' accesses
    fm['summary']['average_score'].

    Implements create-or-update semantics for schema migration (step 1.13d):
    pre-existing state files lacking new fields will have them created on
    first reconcile_summary() run.
    """
    fm = _read_frontmatter(state_path)
    body = _read_body(state_path)

    parts = key.split(".", 1)
    if len(parts) == 1:
        fm[parts[0]] = value
    else:
        top, rest = parts
        if top not in fm or not isinstance(fm[top], dict):
            fm[top] = {}
        sub_parts = rest.split(".", 1)
        if len(sub_parts) == 1:
            fm[top][sub_parts[0]] = value
        else:
            # Two levels deep
            sub_top, sub_key = sub_parts
            if sub_top not in fm[top] or not isinstance(fm[top][sub_top], dict):
                fm[top][sub_top] = {}
            fm[top][sub_top][sub_key] = value

    _write_frontmatter(state_path, fm, body)


def _write_frontmatter(path: Path, fm: dict[str, Any], body: str) -> None:
    """Serialize frontmatter dict + body back to file via write_state_atomic."""
    import yaml  # type: ignore[import]

    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content = f"---\n{fm_str}---\n\n{body}\n"
    write_state_atomic(path, content)


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------

def fingerprint_posting(company: str, role: str, location: str, url: str) -> str:
    """Generate posting deduplication fingerprint.

    Key: company + normalized_role + normalized_location + url_hash
    Used for tracker dedup and cross-tracker re-posting detection.
    """
    norm_role = _normalize_title(role)
    norm_loc = _normalize_location(location)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
    raw = f"{company.lower()}+{norm_role}+{norm_loc}+{url_hash}"
    return raw


def fingerprint_report(posting_fp: str, cv_path: Optional[Path] = None) -> str:
    """Generate report deduplication fingerprint.

    Key: posting_fingerprint + cv_content_hash
    Audit-only — not used as an execution gate (W-F4, PE-2).
    """
    cv_hash = _compute_cv_hash(cv_path)
    return f"{posting_fp}:{cv_hash}"


def fingerprint_action(signal_type: str, entity: str, date: str) -> str:
    """Generate action proposal dedup key (prevents duplicate proposals same-day)."""
    raw = f"{signal_type}:{entity.lower()}:{date}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _compute_cv_hash(cv_path: Optional[Path] = None) -> str:
    """Compute SHA-256 of the CV source. Returns 'unknown' if no file resolves.

    Resolution order (first match wins):
      1. Explicit ``cv_path`` argument
      2. ``profile.cv_path`` from state/career_search.md frontmatter (expanded)
      3. ``~/.artha-local/cv-short.md`` (PDF render source, preferred)
      4. ``~/.artha-local/cv.md`` (narrative source)
      5. ``{repo}/cv.md`` (fallback / test fixture location)
    """
    candidates: list[Optional[Path]] = [cv_path]

    state_file = _REPO_ROOT / "state" / "career_search.md"
    if state_file.exists():
        try:
            fm = _read_frontmatter(state_file)
            custom = (fm.get("profile") or {}).get("cv_path")
            if custom:
                candidates.append(Path(str(custom)).expanduser())
        except Exception:
            pass  # frontmatter read is advisory only; fall through to defaults

    candidates.extend([
        Path.home() / ".artha-local" / "cv-short.md",
        Path.home() / ".artha-local" / "cv.md",
        _REPO_ROOT / "cv.md",
    ])

    for p in candidates:
        if p and p.exists():
            return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return "unknown"


def _normalize_title(title: str) -> str:
    """Normalize role title for dedup comparison."""
    t = title.lower().strip()
    replacements = {
        "sr.": "senior", "jr.": "junior", "eng.": "engineer",
        "mgr.": "manager", "dir.": "director", "vp.": "vice president",
    }
    for abbr, full in replacements.items():
        t = t.replace(abbr, full)
    # Remove punctuation
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_location(location: str) -> str:
    """Normalize location string for dedup."""
    loc = location.lower().strip()
    # Strip common noise
    loc = re.sub(r"\b(inc\.|corp\.|llc\.?|ltd\.?|pbc\.?)\b", "", loc)
    return re.sub(r"\s+", " ", loc).strip()


def cross_tracker_dedup_match(
    company: str, role: str, location: str,
    existing_rows: list[dict[str, str]],
    threshold: float = 0.85,
) -> Optional[str]:
    """Check if company+role+location matches an existing tracker entry via token-set ratio.

    Returns the existing entry's # number if match found (≥threshold), else None.
    Uses Jaccard similarity on normalized unigrams (FR-CS-6 dedup rule).
    """
    def _tokenize(s: str) -> set[str]:
        s = _normalize_title(s)
        return set(s.split())

    tokens_role = _tokenize(role)
    tokens_loc = _tokenize(_normalize_location(location))
    norm_company = company.lower().strip()

    for row in existing_rows:
        exist_company = row.get("company", "").lower().strip()
        if exist_company != norm_company:
            continue
        exist_role_tokens = _tokenize(row.get("role", ""))
        exist_loc_tokens = _tokenize(_normalize_location(row.get("notes", "")))

        # Jaccard similarity on role tokens
        if tokens_role and exist_role_tokens:
            union = tokens_role | exist_role_tokens
            intersection = tokens_role & exist_role_tokens
            sim = len(intersection) / len(union) if union else 0.0
            if sim >= threshold:
                return row.get("num")

    return None


# ---------------------------------------------------------------------------
# deep_freeze() — metadata immutability for DomainSignal (§9.2)
# ---------------------------------------------------------------------------

def deep_freeze(obj: Any) -> Any:
    """Recursively convert dicts → MappingProxyType and lists → tuples.

    Enforces immutability for DomainSignal metadata in portal scanner signals.
    A frozen dataclass prevents attribute reassignment but not nested dict mutation.
    deep_freeze() closes this gap for complex nested structures.
    """
    if isinstance(obj, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(deep_freeze(item) for item in obj)
    if isinstance(obj, (set, frozenset)):
        return frozenset(deep_freeze(item) for item in obj)
    return obj


# ---------------------------------------------------------------------------
# Story Bank helpers
# ---------------------------------------------------------------------------

def validate_story_tags(
    archetype: str,
    capability: str,
    leadership: str,
    metric: str,
    archetypes_in_state: list[str],
) -> tuple[bool, list[str]]:
    """Validate story tags against closed vocabularies.

    Returns (is_valid, list_of_warnings).
    Tags failing validation are noted for WARNING logging to career_audit.jsonl.
    """
    warnings: list[str] = []

    if archetype not in archetypes_in_state:
        warnings.append(f"Unknown archetype tag: {archetype!r}")

    if capability not in _CAPABILITY_DOMAIN_TAGS:
        warnings.append(f"Unknown capability domain tag: {capability!r}")

    if leadership and leadership not in _LEADERSHIP_SIGNAL_TAGS:
        warnings.append(f"Unknown leadership signal tag: {leadership!r}")

    if metric and metric not in _METRIC_TYPE_TAGS:
        warnings.append(f"Unknown metric type tag: {metric!r}")

    return len(warnings) == 0, warnings


def parse_story_bank_index(body: str) -> dict[int, tuple[str, str]]:
    """Parse the Story Bank INDEX comment.

    Expected format:
      <!-- INDEX: 1:Title(Archetype), 2:Title(Archetype) -->

    Returns dict: {story_number: (title, archetype_tag)}
    """
    index: dict[int, tuple[str, str]] = {}
    m = re.search(r"<!--\s*INDEX:\s*(.*?)\s*-->", body, re.DOTALL)
    if not m or not m.group(1).strip():
        return index
    for entry in m.group(1).split(","):
        entry = entry.strip()
        em = re.match(r"(\d+):(.+?)\((.+?)\)", entry)
        if em:
            num = int(em.group(1))
            title = em.group(2).strip()
            archetype = em.group(3).strip()
            index[num] = (title, archetype)
    return index


def build_story_bank_index(stories: dict[int, tuple[str, str]], pinned: set[int]) -> str:
    """Build the INDEX comment string from story dict.

    Enforces 20-story cap (INDEX cap — PE-3):
    - Always include pinned stories (max 5)
    - Fill remaining slots with most-recent non-pinned
    """
    pinned_entries = {n: v for n, v in stories.items() if n in pinned}
    non_pinned = {n: v for n, v in stories.items() if n not in pinned}

    # Respect caps
    pinned_capped = dict(sorted(pinned_entries.items(), reverse=True)[:5])
    remaining_slots = 20 - len(pinned_capped)
    non_pinned_sorted = dict(sorted(non_pinned.items(), reverse=True)[:remaining_slots])

    combined = {**pinned_capped, **non_pinned_sorted}
    sorted_entries = sorted(combined.items())
    parts = [f"{n}:{title}({arch})" for n, (title, arch) in sorted_entries]
    return f"<!-- INDEX: {', '.join(parts)} -->"


# ---------------------------------------------------------------------------
# Report number management
# ---------------------------------------------------------------------------

def next_report_number(state_path: Optional[Path] = None) -> str:
    """Return next zero-padded 3-digit report number.

    Based on max existing number in the briefings/career/ directory.
    Idempotent — scanning files, not state.
    """
    path = state_path or _STATE_FILE
    career_dir = path.parent.parent / "briefings" / "career"
    if not career_dir.exists():
        return "001"
    nums: list[int] = []
    for f in career_dir.glob("*.md"):
        m = re.match(r"^(\d{3})-", f.name)
        if m:
            nums.append(int(m.group(1)))
    return str(max(nums, default=0) + 1).zfill(3)


# ---------------------------------------------------------------------------
# Campaign status helpers
# ---------------------------------------------------------------------------

def is_campaign_active(state_path: Optional[Path] = None) -> bool:
    """return True if campaign.status == 'active' in state frontmatter."""
    path = state_path or _STATE_FILE
    if not path.exists():
        return False
    fm = _read_frontmatter(path)
    return (fm.get("campaign") or {}).get("status") == "active"
