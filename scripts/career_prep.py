"""scripts/career_prep.py — Assemble a per-role interview prep packet.

Takes a report number (or company name) and composes a single markdown packet:
  - STAR stories to lead with (from Block F of the eval report)
  - Rehearsed counters for red-flag questions (Block F)
  - Case-study framework (Block F)
  - Deep-dive reference sections from state/interview_prep.md that match the
    stories being recommended (token-overlap heuristic, deterministic)
  - Posture + immigration reminder lifted from Block A TL;DR

Deterministic Python — no LLM calls. All packet content is derived from
existing artifacts. Emits a `career_prep` trace event.

Usage:
    python3 scripts/career_prep.py 001
    python3 scripts/career_prep.py netflix

Output:
    briefings/career/{NNN}-interview-prep.md

Ref: prompts/career_search.md §Block F (Interview Prep contract)
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.career_trace import CareerTrace

log = logging.getLogger(__name__)

_BRIEFINGS_DIR = _REPO_ROOT / "briefings" / "career"
_INTERVIEW_PREP_FILE = _REPO_ROOT / "state" / "interview_prep.md"


# ---------------------------------------------------------------------------
# Report resolution
# ---------------------------------------------------------------------------


def _resolve_report_path(arg: str) -> Optional[Path]:
    """Arg is either a number ('001'/'1') or a company slug ('netflix')."""
    if not _BRIEFINGS_DIR.exists():
        return None

    if arg.isdigit():
        num = str(int(arg)).zfill(3)
        for p in _BRIEFINGS_DIR.glob(f"{num}-*.md"):
            if "cover-letter" in p.name or "interview-prep" in p.name:
                continue
            return p
        return None

    slug = re.sub(r"[^a-z0-9]+", "-", arg.lower()).strip("-")
    candidates: list[Path] = []
    for p in _BRIEFINGS_DIR.glob("*.md"):
        if "cover-letter" in p.name or "interview-prep" in p.name:
            continue
        if slug in p.stem.lower():
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.stem, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Deterministic extractors
# ---------------------------------------------------------------------------


_FM_END_RE = re.compile(r"^---\s*$", re.MULTILINE)


def _extract_frontmatter(report_text: str) -> dict:
    try:
        import yaml  # noqa: PLC0415
        if report_text.startswith("---\n"):
            end = report_text.find("\n---", 4)
            if end > 0:
                return yaml.safe_load(report_text[4:end]) or {}
    except Exception:
        pass
    return {}


def _extract_section(report_text: str, letter: str) -> str:
    """Return body of '## {letter}) ...' block (without the heading).

    Strips trailing horizontal-rule separators that divide blocks in the source.
    """
    m = re.search(
        rf"## {letter}\).+?\n(.+?)(?=\n## [A-Z]\)|\n## Scoring|\n## Next Steps|\Z)",
        report_text,
        re.DOTALL,
    )
    if not m:
        return ""
    body = m.group(1).strip()
    body = re.sub(r"\n+---\s*$", "", body)
    return body.strip()


def _extract_tldr(report_text: str) -> str:
    m = re.search(r"\*\*TL;DR:\*\*\s*(.+?)(?=\n\n|\n##|\Z)", report_text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_manual_verification(report_text: str) -> str:
    m = re.search(
        r"(🔵\s*\*\*Manual Verification Required[^\n]*\*\*[^\n]*(?:\n(?!---|##).+)*)",
        report_text,
    )
    return m.group(1).strip() if m else ""


def _extract_story_titles(block_f: str) -> list[str]:
    """Parse the 'Recommended STAR Stories' table → list of story names."""
    m = re.search(r"\|\s*#\s*\|\s*Story\s*\|.+?\n((?:\|.*?\n)+)", block_f, re.DOTALL)
    if not m:
        return []
    titles: list[str] = []
    for row in m.group(1).splitlines():
        parts = [p.strip() for p in row.strip().strip("|").split("|")]
        if len(parts) >= 2 and parts[0].isdigit():
            titles.append(parts[1])
    return titles


def _count_questions(block_f: str) -> int:
    """Count red-flag Qs (bold-numbered list items)."""
    return len(re.findall(r"^\d+\.\s+\*\*", block_f, re.MULTILINE))


# ---------------------------------------------------------------------------
# KB section matching
# ---------------------------------------------------------------------------


_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "for", "to", "on", "at", "by",
    "with", "from", "—", "-", "·", "story", "stories", "program", "project",
    "build", "migration", "bank", "ml", "ai",
}


def _tokens(s: str) -> set[str]:
    return {
        t for t in re.split(r"[^a-z0-9]+", s.lower())
        if t and t not in _STOPWORDS and len(t) >= 3
    }


def _extract_kb_sections() -> list[tuple[str, str]]:
    """Return list of (section_title, section_body) from interview_prep.md."""
    if not _INTERVIEW_PREP_FILE.exists():
        return []
    text = _INTERVIEW_PREP_FILE.read_text(encoding="utf-8")
    out: list[tuple[str, str]] = []
    matches = list(re.finditer(r"^##\s+(.+?)$", text, re.MULTILINE))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = m.group(1).strip()
        body = text[start:end].strip()
        out.append((title, body))
    return out


def _select_kb_sections(
    story_titles: list[str], max_sections: int = 4, min_overlap: int = 2
) -> list[tuple[str, str]]:
    """For each story, pick the KB section with highest token overlap."""
    sections = _extract_kb_sections()
    if not sections or not story_titles:
        return []

    story_tokensets = [_tokens(t) for t in story_titles]
    scored: list[tuple[int, int, str, str]] = []  # (overlap, idx, title, body)
    used_titles: set[str] = set()

    for i, story_toks in enumerate(story_tokensets):
        best_score = 0
        best_idx = -1
        for j, (title, _body) in enumerate(sections):
            if title in used_titles:
                continue
            section_toks = _tokens(title)
            overlap = len(story_toks & section_toks)
            if overlap > best_score:
                best_score = overlap
                best_idx = j
        if best_score >= min_overlap and best_idx >= 0:
            title, body = sections[best_idx]
            scored.append((best_score, i, title, body))
            used_titles.add(title)

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(t, b) for _s, _i, t, b in scored[:max_sections]]


# ---------------------------------------------------------------------------
# Packet composition
# ---------------------------------------------------------------------------


def _compose_packet(
    *,
    report_number: str,
    company: str,
    role: str,
    score: str,
    archetype: str,
    recommendation: str,
    block_f: str,
    tldr: str,
    manual_verify: str,
    kb_sections: list[tuple[str, str]],
) -> str:
    today = date.today().isoformat()

    # Re-flow nested h3 headings inside KB bodies so the packet's h2 Deep-Dive
    # section reads cleanly (KB source uses h3 inside h2; keep that shape).
    if kb_sections:
        kb_block = "## Deep-Dive Reference — Past Experience\n\n" + "\n\n---\n\n".join(
            f"### {title}\n\n{body}" for title, body in kb_sections
        )
    else:
        kb_block = (
            "## Deep-Dive Reference — Past Experience\n\n"
            "*No matching sections in `state/interview_prep.md` for these stories. "
            "Add to KB before the screen.*"
        )

    manual_line = f"\n\n{manual_verify}" if manual_verify else ""

    return f"""---
report_number: "{report_number}"
type: interview_prep
company: {company}
generated_on: "{today}"
---

# Interview Prep Packet — {company} · {role}

**Score:** {score} · **Archetype:** {archetype} · **Recommendation:** {recommendation}

**Posture (TL;DR):** {tldr}{manual_line}

---

## Block F — Prep Assets from Eval Report

{block_f}

---

{kb_block}

---

## Packet Usage

1. **Day-of prep (30 min):** Re-read the TL;DR + Block F stories; say them out loud once.
2. **Screens:** Lead with the top pinned story mapped to the interviewer's likely question. Counters for the 5 red-flag Qs are rehearsed above.
3. **Onsite / panel:** Use the Deep-Dive Reference section for metrics and context you cannot afford to misremember (e.g., the 🟢 verified figures).
4. **Post-interview:** Append any unanticipated question + your answer to `state/interview_prep.md` so future packets benefit.

Packet generated {today}.
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def prep(arg: str) -> dict:
    """Build the interview-prep packet. Returns a result dict."""
    report_path = _resolve_report_path(arg)
    if not report_path:
        raise ValueError(f"No evaluation report found for '{arg}'")

    report_text = report_path.read_text(encoding="utf-8")
    fm = _extract_frontmatter(report_text)
    company = fm.get("company", "Company")
    role = fm.get("role", "Role")
    score = str(fm.get("score", ""))
    archetype = fm.get("archetype", "")
    recommendation = fm.get("recommendation", "")
    report_number = str(fm.get("report_number", arg)).zfill(3)

    block_f = _extract_section(report_text, "F")
    if not block_f:
        raise ValueError(
            f"Report {report_number} has no Block F (Interview Prep). "
            "Re-run /career eval with the full 7-block template."
        )

    tldr = _extract_tldr(report_text)
    manual_verify = _extract_manual_verification(report_text)
    story_titles = _extract_story_titles(block_f)
    kb_sections = _select_kb_sections(story_titles)
    q_count = _count_questions(block_f)

    packet = _compose_packet(
        report_number=report_number,
        company=company,
        role=role,
        score=score,
        archetype=archetype,
        recommendation=recommendation,
        block_f=block_f,
        tldr=tldr,
        manual_verify=manual_verify,
        kb_sections=kb_sections,
    )

    _BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _BRIEFINGS_DIR / f"{report_number}-interview-prep.md"

    new_hash = hashlib.sha256(packet.encode("utf-8")).hexdigest()[:32]
    cached = False
    if out_path.exists():
        existing_hash = hashlib.sha256(
            out_path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()[:32]
        if existing_hash == new_hash:
            cached = True

    if not cached:
        out_path.write_text(packet, encoding="utf-8")

    CareerTrace().write_prep_event(
        op="cached" if cached else "generated",
        report_number=report_number,
        output_path=str(out_path),
        stories_included=len(story_titles),
        questions_included=q_count,
    )

    return {
        "status": "success",
        "report_number": report_number,
        "company": company,
        "role": role,
        "output_path": str(out_path),
        "stories_included": len(story_titles),
        "questions_included": q_count,
        "kb_sections_included": len(kb_sections),
        "cached": cached,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble an interview-prep packet.")
    parser.add_argument("target", help="Report number (001) or company slug (netflix)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        result = prep(args.target)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    cached = " (cached)" if result["cached"] else ""
    print(f"[OK] Report {result['report_number']} — {result['company']} · {result['role']}{cached}")
    print(f"     packet: {result['output_path']}")
    print(
        f"     stories: {result['stories_included']} · "
        f"questions: {result['questions_included']} · "
        f"KB sections: {result['kb_sections_included']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
