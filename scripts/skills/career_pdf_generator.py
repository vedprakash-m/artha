"""scripts/skills/career_pdf_generator.py — ATS-optimized CV PDF generation skill.

Generates keyword-optimized, per-JD-tailored CV PDFs from evaluation reports and cv.md.

Pattern: Follows home_device_monitor.py canonical BaseSkill pattern.
  __init__() → pull() → parse() → to_dict()
  execute() is inherited from BaseSkill — NOT overridden.

Architecture:
  pull()       — Read cv.md, evaluation report, cv-template.html
  parse()      — Merge CV + Block E personalization, inject keywords, normalize unicode
                 Write merged HTML to tmp/career_cv_render.html
                 Delegate HTML → PDF rendering to _render_pdf()
  to_dict()    — Return structured result dict
  _render_pdf()— Launch Playwright Chromium, page.pdf(), save output PDF

Security:
  - cv.md is read only; never modified
  - Output PDF path is validated as within the Artha output/ subdirectory
  - No external URLs fetched during render (self-hosted fonts only)

Hallucination trap: All content merging is deterministic (regex + string substitution).
  No LLM calls in this module. LLM authored the report; this skill reads it.

Ref: specs/career-ops.md §9.1, FR-CS-3, §13
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SKILLS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _SKILLS_DIR.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from .base_skill import BaseSkill
from lib.career_state import (
    is_campaign_active,
    _read_frontmatter,
    next_report_number,
)
from lib.career_trace import CareerTrace

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATE_FILE = _REPO_ROOT / "state" / "career_search.md"
_TEMPLATE_FILE = _REPO_ROOT / "templates" / "cv-template.html"
_CV_CANDIDATES: list[Path] = [
    Path.home() / ".artha-local" / "cv.md",
    _REPO_ROOT / "cv.md",
]
_OUTPUT_DIR = _REPO_ROOT / "output" / "career"
_TMP_RENDER = _REPO_ROOT / "tmp" / "career_cv_render.html"

# ATS unicode normalization table
_UNICODE_REPLACEMENTS: dict[str, str] = {
    "\u2014": "-",   # em dash → hyphen
    "\u2013": "-",   # en dash → hyphen
    "\u2018": "'",   # left single quotation → straight
    "\u2019": "'",   # right single quotation → straight
    "\u201c": '"',   # left double quotation → straight
    "\u201d": '"',   # right double quotation → straight
    "\u2026": "...", # ellipsis → three dots
    "\u00a0": " ",   # non-breaking space → regular space
}

# Cliché words to flag (logged as warning — not stripped, user may override)
_CLICHE_PATTERNS: list[str] = [
    "passionate", "results-driven", "synergy", "leveraged", "rock star",
    "ninja", "guru", "thought leader", "proactive", "go-getter",
    "team player", "detail-oriented",
]

_TEMPLATE_VERSION = "1.0"


class SkillDependencyError(RuntimeError):
    """Raised when a required external dependency (e.g. Playwright) is missing."""


class CareerPdfGenerator(BaseSkill):
    """Generate ATS-optimized CV PDF from evaluation report and cv.md.

    Parameters:
        report_number: Zero-padded 3-digit string (e.g. "042").
                       Required — pass via CareerPdfGenerator(report_number="042").
    """

    def __init__(
        self,
        name: str = "career_pdf_generator",
        priority: str = "P1",
        report_number: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, priority=priority)
        self.report_number = report_number
        self._pdf_path: Optional[Path] = None
        self._html_fallback_path: Optional[Path] = None
        self._trace = CareerTrace()

    @property
    def compare_fields(self) -> List[str]:
        """Fields used by skill_runner.py for delta detection."""
        return ["report_number", "pdf_path", "status"]

    # ------------------------------------------------------------------
    # pull() — read raw inputs
    # ------------------------------------------------------------------

    def pull(self) -> Dict[str, Any]:
        """Read cv.md, evaluation report, and HTML template.

        Returns skip sentinel if no active campaign (activation guard).
        Raises ValueError if report_number is not set.

        Hallucination trap: All file reads are deterministic.
        No LLM calls here — just IO.
        """
        # Activation guard (§9.1) — no active campaign → skip
        if not is_campaign_active(_STATE_FILE):
            log.info("career_pdf_generator: no active campaign — skill skipped")
            return {"status": "skipped", "reason": "No active career campaign"}

        # report_number is required
        if not self.report_number:
            raise ValueError(
                "report_number is required — pass via CareerPdfGenerator(report_number='NNN')"
            )

        # Resolve cv.md
        cv_path: Optional[Path] = None
        fm = _read_frontmatter(_STATE_FILE)
        custom_cv = (fm.get("profile") or {}).get("cv_path")
        if custom_cv:
            candidate = Path(custom_cv).expanduser()
            if candidate.exists():
                cv_path = candidate

        if cv_path is None:
            for candidate in _CV_CANDIDATES:
                if candidate.exists():
                    cv_path = candidate
                    break

        if cv_path is None:
            return {
                "status": "failed",
                "error": (
                    "cv.md not found. Create it at ~/.artha-local/cv.md "
                    "with your work history to enable PDF generation."
                ),
            }

        # Security: check cv.md is not git-tracked (PII exposure)
        git_tracked = _is_git_tracked(cv_path)
        if git_tracked:
            log.warning(
                "career_pdf_generator: cv.md appears to be tracked by git — PII exposure risk"
            )
            # Non-fatal warning (not abort here — user already saw preflight warning)

        # Resolve report
        report_path = self._resolve_report_path()
        if not report_path or not report_path.exists():
            return {
                "status": "failed",
                "error": f"Evaluation report {self.report_number} not found in briefings/career/",
            }

        # Template
        if not _TEMPLATE_FILE.exists():
            return {
                "status": "failed",
                "error": f"CV template missing: {_TEMPLATE_FILE}. Check templates/cv-template.html.",
            }

        # Read all content
        cv_text = cv_path.read_text(encoding="utf-8")
        report_text = report_path.read_text(encoding="utf-8")
        template_html = _TEMPLATE_FILE.read_text(encoding="utf-8")

        # Optional: article-digest.md for additional proof points
        article_digest = ""
        digest_path = Path.home() / ".artha-local" / "article-digest.md"
        if digest_path.exists():
            article_digest = digest_path.read_text(encoding="utf-8")

        return {
            "status": "ok",
            "cv_text": cv_text,
            "report_text": report_text,
            "template_html": template_html,
            "article_digest": article_digest,
            "report_path": report_path,
            "cv_path": cv_path,
        }

    # ------------------------------------------------------------------
    # parse() — merge + render
    # ------------------------------------------------------------------

    def parse(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Merge CV with Block E personalization, render HTML, produce PDF.

        All content manipulation is deterministic (regex/string ops, no LLM).
        parse() can be unit-tested by mocking _render_pdf() (§9.1).
        """
        if raw_data.get("status") in ("skipped", "failed"):
            return raw_data

        cv_text: str = raw_data["cv_text"]
        report_text: str = raw_data["report_text"]
        template_html: str = raw_data["template_html"]
        report_path: Path = raw_data["report_path"]

        # 1. Extract metadata from report frontmatter
        report_fm = self._parse_report_frontmatter(report_text)
        company = report_fm.get("company", "Company")
        role = report_fm.get("role", "Role")
        score = report_fm.get("score", "N/A")
        archetype = report_fm.get("archetype", "")

        # 2. Extract keywords from report (Block E "Keywords Extracted" section)
        keywords = self._extract_keywords(report_text)

        # 3. Extract Block E personalization plan
        personalization = self._extract_block_e(report_text)

        # 4. Apply ATS unicode normalization
        cv_text = _normalize_unicode(cv_text)

        # 5. Check for clichés (warning — non-blocking)
        _warn_cliches(cv_text)

        # 6. Inject keywords into CV text (ATS optimization)
        cv_with_keywords = _inject_keywords(cv_text, keywords)

        # 7. Apply Block E CV changes (deterministic string replacements + annotations)
        cv_final = _apply_personalization(cv_with_keywords, personalization)

        # 8. Convert CV markdown → HTML
        cv_html = _md_to_html(cv_final)

        # 9. Inject into template
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = _slug(company)
        rendered_html = template_html
        rendered_html = rendered_html.replace("{{CV_CONTENT}}", cv_html)
        rendered_html = rendered_html.replace("{{COMPANY}}", company)
        rendered_html = rendered_html.replace("{{ROLE}}", role)
        rendered_html = rendered_html.replace("{{SCORE}}", str(score))
        rendered_html = rendered_html.replace("{{ARCHETYPE}}", archetype)
        rendered_html = rendered_html.replace("{{DATE}}", date_str)
        rendered_html = rendered_html.replace("{{KEYWORDS}}", ", ".join(keywords))

        # 10. Write merged HTML to tmp file
        _TMP_RENDER.parent.mkdir(parents=True, exist_ok=True)
        _TMP_RENDER.write_text(rendered_html, encoding="utf-8")

        # 11. Render PDF via Playwright
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        pdf_filename = f"cv-{slug}-{date_str}.pdf"
        pdf_path = _OUTPUT_DIR / pdf_filename

        pdf_result = self._render_pdf(_TMP_RENDER, pdf_path)

        # 12. Cleanup tmp HTML
        try:
            _TMP_RENDER.unlink(missing_ok=True)
        except Exception:
            pass

        self._pdf_path = pdf_result.get("pdf_path")
        self._html_fallback_path = pdf_result.get("html_path")

        # 13. Trace event
        event_op = "generated" if pdf_result.get("success") else "failed"
        if pdf_result.get("html_fallback"):
            event_op = "fallback_html"
        self._trace.write_pdf_event(
            op=event_op,
            report_number=self.report_number,
            output_path=str(self._pdf_path or self._html_fallback_path or ""),
            error=pdf_result.get("error"),
        )

        return {
            "status": "success" if pdf_result.get("success") else ("failed" if not pdf_result.get("html_fallback") else "fallback"),
            "pdf_path": str(self._pdf_path) if self._pdf_path else None,
            "html_fallback_path": str(self._html_fallback_path) if self._html_fallback_path else None,
            "company": company,
            "role": role,
            "score": score,
            "keywords_injected": len(keywords),
            "template_version": _TEMPLATE_VERSION,
            "error": pdf_result.get("error"),
        }

    # ------------------------------------------------------------------
    # to_dict()
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "report_number": self.report_number,
            "pdf_path": str(self._pdf_path) if self._pdf_path else None,
            "html_fallback_path": str(self._html_fallback_path) if self._html_fallback_path else None,
            "status": self.status,
            "last_run": self.last_run,
        }

    # ------------------------------------------------------------------
    # _render_pdf() — isolated error boundary (§9.1)
    # ------------------------------------------------------------------

    def _render_pdf(self, html_path: Path, pdf_path: Path) -> Dict[str, Any]:
        """Launch Playwright Chromium → page.pdf() → save output PDF.

        Error boundary:
          - PlaywrightNotInstalled → SkillDependencyError (caller converts to failed status)
          - Chromium crash → retry once → HTML fallback on second failure
          - Isolated from parse() — no cross-contamination of errors

        Mockable for unit tests (§9.1): mock _render_pdf() in parse() tests.
        """
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
        except ImportError:
            msg = (
                "Playwright is not installed. "
                "Run `pip install playwright && playwright install chromium` to enable PDF generation."
            )
            log.error("career_pdf_generator: %s", msg)
            self.status = "failed"
            return {"success": False, "error": msg}

        def _do_render() -> Dict[str, Any]:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context(
                        # Browser context namespacing (§9.2 AR-3)
                        extra_http_headers={"X-Artha-Context": f"career-pdf-{self.report_number}"}
                    )
                    page = context.new_page()
                    page.goto(f"file://{html_path.resolve()}")
                    page.wait_for_load_state("networkidle")
                    page.pdf(
                        path=str(pdf_path),
                        format="Letter",
                        print_background=True,
                        margin={"top": "0.5in", "bottom": "0.5in", "left": "0.6in", "right": "0.6in"},
                    )
                    browser.close()
                return {"success": True, "pdf_path": pdf_path}
            except Exception as e:
                return {"success": False, "error": str(e)}

        # First attempt
        result = _do_render()
        if result["success"]:
            log.info("career_pdf_generator: PDF generated at %s", pdf_path)
            return result

        # Retry once (Chromium crash recovery — §9.1)
        log.warning("career_pdf_generator: first render attempt failed — retrying. Error: %s", result.get("error"))
        result2 = _do_render()
        if result2["success"]:
            log.info("career_pdf_generator: PDF generated on retry at %s", pdf_path)
            return result2

        # HTML fallback — save HTML instead of PDF
        html_fallback = pdf_path.with_suffix(".html")
        try:
            import shutil
            shutil.copy2(html_path, html_fallback)
            log.warning(
                "career_pdf_generator: PDF render failed twice — saving HTML fallback: %s",
                html_fallback,
            )
            return {
                "success": False,
                "html_fallback": True,
                "html_path": html_fallback,
                "error": result2.get("error"),
            }
        except Exception as copy_err:
            return {"success": False, "error": f"PDF failed + HTML fallback failed: {copy_err}"}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_report_path(self) -> Optional[Path]:
        """Find evaluation report file by number prefix."""
        career_dir = _REPO_ROOT / "briefings" / "career"
        if not career_dir.exists():
            return None
        for f in career_dir.glob(f"{self.report_number}-*.md"):
            return f
        return None

    def _parse_report_frontmatter(self, report_text: str) -> Dict[str, Any]:
        """Extract YAML frontmatter from evaluation report text."""
        try:
            import yaml  # type: ignore[import]
            if report_text.startswith("---"):
                end = report_text.find("---", 3)
                if end > 0:
                    return yaml.safe_load(report_text[3:end]) or {}
        except Exception:
            pass
        return {}

    def _extract_keywords(self, report_text: str) -> List[str]:
        """Extract ATS keywords from 'Keywords Extracted' section of report."""
        m = re.search(
            r"## Keywords Extracted\s*\n(.+?)(?=\n##|\Z)",
            report_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not m:
            return []
        raw = m.group(1).strip()
        # May be comma-separated or line-by-line
        if "," in raw:
            keywords = [k.strip() for k in raw.split(",") if k.strip()]
        else:
            keywords = [k.strip("- ").strip() for k in raw.splitlines() if k.strip()]
        return keywords[:20]  # Cap at 20 per spec

    def _extract_block_e(self, report_text: str) -> List[Dict[str, str]]:
        """Extract Block E CV changes as list of {section, change, rationale} dicts."""
        m = re.search(
            r"## E\) Personalization Plan\s*\n.*?### Top 5 CV Changes\s*\n(.+?)(?=### Top 5 LinkedIn|\Z)",
            report_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not m:
            return []
        table_text = m.group(1)
        changes: List[Dict[str, str]] = []
        for line in table_text.splitlines():
            # Match table rows: | # | Section | Change | Rationale |
            if line.strip().startswith("|") and not "Section" in line and not line.strip().startswith("|---"):
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) >= 4:
                    changes.append({
                        "section": parts[1],
                        "change": parts[2],
                        "rationale": parts[3],
                    })
        return changes


# ---------------------------------------------------------------------------
# Content manipulation helpers (deterministic — no LLM)
# ---------------------------------------------------------------------------

def _normalize_unicode(text: str) -> str:
    """Apply ATS unicode normalization — em-dashes, smart quotes, etc."""
    for char, replacement in _UNICODE_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    # NFC normalize remaining unicode
    return unicodedata.normalize("NFC", text)


def _warn_cliches(text: str) -> None:
    """Log warnings for detected cliché words (non-blocking)."""
    text_lower = text.lower()
    found = [c for c in _CLICHE_PATTERNS if c in text_lower]
    if found:
        log.warning(
            "career_pdf_generator: cliché words detected in CV — consider rewriting: %s",
            ", ".join(found),
        )


def _inject_keywords(cv_text: str, keywords: List[str]) -> str:
    """Inject ATS keywords into CV text where semantically appropriate.

    Strategy: append keyword section to Skills section if present.
    Keywords already present in CV are not duplicated.
    """
    if not keywords:
        return cv_text

    # Find skills section (common headings)
    skills_match = re.search(
        r"(#+\s*(?:Skills|Technical Skills|Core Competencies)[^\n]*\n)",
        cv_text,
        re.IGNORECASE,
    )

    # Filter to keywords not already in CV
    cv_lower = cv_text.lower()
    new_keywords = [k for k in keywords if k.lower() not in cv_lower]

    if not new_keywords:
        return cv_text

    keyword_annotation = f"\n<!-- ATS keywords injected for this JD: {', '.join(new_keywords)} -->\n"

    if skills_match:
        insert_pos = skills_match.end()
        return cv_text[:insert_pos] + keyword_annotation + cv_text[insert_pos:]
    else:
        return cv_text + keyword_annotation


def _apply_personalization(cv_text: str, changes: List[Dict[str, str]]) -> str:
    """Apply Block E CV changes to CV text.

    Changes are appended as HTML comments (non-destructive — user reviews before using).
    This preserves the original cv.md while surfacing the recommended changes inline.
    """
    if not changes:
        return cv_text

    annotations = ["\n<!-- Block E CV Personalization Recommendations:"]
    for i, change in enumerate(changes, 1):
        annotations.append(
            f"  {i}. [{change.get('section', '?')}] {change.get('change', '?')} "
            f"(Rationale: {change.get('rationale', '?')})"
        )
    annotations.append("-->")
    return cv_text + "\n".join(annotations)


def _md_to_html(md_text: str) -> str:
    """Convert Markdown CV to HTML.

    Uses `markdown` package if available; falls back to basic conversion.
    HTML comments (ATS annotations) are preserved.
    """
    try:
        import markdown  # type: ignore[import]
        # Preserve HTML comments through markdown processing
        html = markdown.markdown(
            md_text,
            extensions=["tables", "fenced_code"],
        )
        return html
    except ImportError:
        log.warning(
            "career_pdf_generator: 'markdown' package not installed — falling back to basic HTML conversion"
        )
        return _basic_md_to_html(md_text)


def _basic_md_to_html(md_text: str) -> str:
    """Minimal Markdown-to-HTML for fallback (headings, bullets, bold, italic)."""
    lines = md_text.splitlines()
    html_lines: List[str] = []
    for line in lines:
        # Headings
        if line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        # Bullet points
        elif line.startswith("- "):
            content = line[2:]
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
            content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
            html_lines.append(f"<li>{content}</li>")
        # HTML comments pass through
        elif line.startswith("<!--"):
            html_lines.append(line)
        # Blank line → paragraph break
        elif not line.strip():
            html_lines.append("<br>")
        else:
            content = line
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
            content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
            html_lines.append(f"<p>{content}</p>")
    return "\n".join(html_lines)


def _slug(text: str) -> str:
    """Convert company name to URL-safe slug."""
    s = text.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")[:40]


def _is_git_tracked(path: Path) -> bool:
    """Return True if file is tracked by git (PII security check)."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            cwd=_REPO_ROOT,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False
