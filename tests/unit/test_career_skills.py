"""tests/unit/test_career_skills.py — FR-25 Career Search Intelligence.

AR-1 (P0 Phase 1 exit criterion): Verifies career skills are registered in
_ALLOWED_SKILLS so they can be invoked via the Skill Runner.

Also validates:
- state/career/ directory exists (PDF output location — §9.1 FR-CS-3)
- briefings/career/ directory exists (evaluation report archive — §7.3)
- career skills registered in config/skills.yaml

Ref: specs/career-ops.md AR-1, §9.1, §3.1
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"



# ---------------------------------------------------------------------------
# AR-1 (P0): _ALLOWED_SKILLS must include career skills
# ---------------------------------------------------------------------------

def test_career_pdf_generator_in_allowed_skills():
    """career_pdf_generator MUST be in _ALLOWED_SKILLS (spec AR-1 — P0)."""
    from skill_runner import _ALLOWED_SKILLS  # noqa: PLC0415
    assert "career_pdf_generator" in _ALLOWED_SKILLS, (
        "career_pdf_generator missing from _ALLOWED_SKILLS in skill_runner.py — "
        "this is a P0 Phase 1 exit criterion (spec AR-1)"
    )


def test_portal_scanner_in_allowed_skills():
    """portal_scanner MUST be pre-registered in _ALLOWED_SKILLS for Phase 2 readiness."""
    from skill_runner import _ALLOWED_SKILLS  # noqa: PLC0415
    assert "portal_scanner" in _ALLOWED_SKILLS, (
        "portal_scanner missing from _ALLOWED_SKILLS in skill_runner.py — "
        "Phase 2 portal scanning requires pre-registration"
    )


def test_allowed_skills_is_frozenset():
    """_ALLOWED_SKILLS must be a frozenset (immutable allowlist contract)."""
    from skill_runner import _ALLOWED_SKILLS  # noqa: PLC0415
    assert isinstance(_ALLOWED_SKILLS, frozenset), (
        f"_ALLOWED_SKILLS must be frozenset, got {type(_ALLOWED_SKILLS).__name__}"
    )


# ---------------------------------------------------------------------------
# Directory existence (FR-CS-3, §7.3)
# ---------------------------------------------------------------------------

def test_state_career_directory_exists():
    """state/career/ must exist — PDF generation target (FR-CS-3 §9.1)."""
    state_career = REPO_ROOT / "state" / "career"
    assert state_career.is_dir(), (
        f"state/career/ directory missing — create it with: mkdir -p {state_career}"
    )


def test_briefings_career_directory_exists():
    """briefings/career/ must exist — evaluation report archive (§7.3)."""
    briefings_career = REPO_ROOT / "briefings" / "career"
    assert briefings_career.is_dir(), (
        f"briefings/career/ directory missing — create it with: mkdir -p {briefings_career}"
    )


# ---------------------------------------------------------------------------
# Config/skills.yaml registration
# ---------------------------------------------------------------------------

def test_career_pdf_generator_in_skills_yaml():
    """career_pdf_generator must be registered in config/skills.yaml."""
    import yaml  # noqa: PLC0415 — this is the config_loader exemption file
    skills_path = REPO_ROOT / "config" / "skills.yaml"
    assert skills_path.exists(), "config/skills.yaml missing"
    data = yaml.safe_load(skills_path.read_text(encoding="utf-8")) or {}
    skills = data.get("skills", {})
    assert "career_pdf_generator" in skills, (
        "career_pdf_generator not registered in config/skills.yaml"
    )


def test_portal_scanner_in_skills_yaml():
    """portal_scanner must be pre-registered in config/skills.yaml for Phase 2."""
    import yaml  # noqa: PLC0415 — config read-only check
    skills_path = REPO_ROOT / "config" / "skills.yaml"
    data = yaml.safe_load(skills_path.read_text(encoding="utf-8")) or {}
    skills = data.get("skills", {})
    assert "portal_scanner" in skills, (
        "portal_scanner not pre-registered in config/skills.yaml"
    )


# ---------------------------------------------------------------------------
# Skill module syntax (imports cleanly without Playwright)
# ---------------------------------------------------------------------------

def test_career_pdf_generator_compiles():
    """career_pdf_generator.py must compile without syntax errors."""
    import py_compile  # noqa: PLC0415
    skill_path = SCRIPTS_DIR / "skills" / "career_pdf_generator.py"
    assert skill_path.exists(), "scripts/skills/career_pdf_generator.py missing"
    py_compile.compile(str(skill_path), doraise=True)


def test_career_pdf_uses_company_directory_and_reuses_cached_output(tmp_path, monkeypatch):
    from skills import career_pdf_generator as module

    output = tmp_path / "state" / "career"
    monkeypatch.setattr(module, "_OUTPUT_DIR", output)
    monkeypatch.setattr(module, "_TMP_RENDER", tmp_path / "tmp" / "render.html")

    class Trace:
        def write_pdf_event(self, **kwargs):
            pass

    calls = []

    def render(_self, _html, pdf):
        calls.append(pdf)
        pdf.write_bytes(b"synthetic pdf")
        return {"success": True, "pdf_path": pdf}

    monkeypatch.setattr(module, "CareerTrace", Trace)
    monkeypatch.setattr(module.CareerPdfGenerator, "_render_pdf", render)
    skill = module.CareerPdfGenerator(report_number="001")
    raw = {
        "cv_text": "# Synthetic Candidate\n\nExample experience",
        "report_text": "---\ncompany: Example Company\nrole: Example Role\n---\n",
        "template_html": "<html>{{CV_CONTENT}}</html>",
        "report_path": tmp_path / "report.md",
    }

    first = skill.parse(raw)
    second = skill.parse(raw)

    path = Path(first["pdf_path"])
    assert first["status"] == "success"
    assert path.parent == output / "example-company"
    assert path.name.startswith("cv-example-company-")
    assert second["cached"] is True
    assert calls == [path]


def test_career_pdf_renders_without_network_or_document_scripts(tmp_path, monkeypatch):
    from skills import career_pdf_generator as module

    sync_playwright = MagicMock()
    browser = sync_playwright.return_value.__enter__.return_value.chromium.launch.return_value
    playwright = ModuleType("playwright")
    playwright.__path__ = []
    playwright_sync = ModuleType("playwright.sync_api")
    playwright_sync.sync_playwright = sync_playwright
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", playwright_sync)
    monkeypatch.setattr(module, "CareerTrace", lambda: None)
    html = tmp_path / "synthetic.html"
    html.write_text("<html>Synthetic CV</html>", encoding="utf-8")

    result = module.CareerPdfGenerator(report_number="001")._render_pdf(html, tmp_path / "cv.pdf")

    assert result["success"] is True
    assert browser.new_context.call_args.kwargs["offline"] is True
    assert browser.new_context.call_args.kwargs["java_script_enabled"] is False


def test_career_pdf_offline_render_preserves_local_assets(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    pypdf = pytest.importorskip("pypdf")
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
    from skills import career_pdf_generator as module

    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Playwright Chromium is not installed")
        raise

    connections = []

    class Server(ThreadingHTTPServer):
        def get_request(self):
            connections.append(True)
            return super().get_request()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = Server(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        stylesheet = tmp_path / "local.css"
        stylesheet.write_text('body::before { content: "LOCAL-CSS-LOADED"; }', encoding="utf-8")
        remote = f"http://127.0.0.1:{server.server_port}"
        html = tmp_path / "synthetic.html"
        html.write_text(
            f'<link rel="preconnect" href="{remote}"><link rel="stylesheet" href="{stylesheet.as_uri()}">'
            f'<body>Synthetic<img src="{remote}/image">'
            f'<script>document.write("SCRIPT-EXECUTED"); fetch("{remote}/script")</script></body>',
            encoding="utf-8",
        )
        monkeypatch.setattr(module, "CareerTrace", lambda: None)
        pdf = tmp_path / "synthetic.pdf"
        result = module.CareerPdfGenerator(report_number="001")._render_pdf(html, pdf)
        assert result["success"], result.get("error")
        text = "".join(page.extract_text() for page in pypdf.PdfReader(pdf).pages)
        assert "LOCAL-CSS-LOADED" in text
        assert "SCRIPT-EXECUTED" not in text
        assert not connections
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_career_docx_default_is_generic_and_private(tmp_path):
    docx = pytest.importorskip("docx")
    root = tmp_path / "checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "career_docx_generator.py"
    shutil.copyfile(SCRIPTS_DIR / script.name, script)
    cv = root / "state" / "content" / "resumes" / "cv-short.md"
    cv.parent.mkdir(parents=True)
    cv.write_text("# Synthetic Candidate\n\nSynthetic contact\n", encoding="utf-8")

    before = date.today()
    result = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    outputs = list((root / "state" / "career").glob("*.docx"))
    assert len(outputs) == 1
    output = outputs[0]
    assert output.name in {f"cv-{day.isoformat()}.docx" for day in (before, date.today())}
    assert docx.Document(output).paragraphs[0].text == "Synthetic Candidate"
    assert not (root / "output").exists()

    custom = root / "state" / "career" / "example" / "custom.docx"
    result = subprocess.run([sys.executable, str(script), str(custom), str(cv)], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert docx.Document(custom).paragraphs[0].text == "Synthetic Candidate"


def test_career_state_compiles():
    """scripts/lib/career_state.py must compile cleanly."""
    import py_compile  # noqa: PLC0415
    path = SCRIPTS_DIR / "lib" / "career_state.py"
    assert path.exists(), "scripts/lib/career_state.py missing"
    py_compile.compile(str(path), doraise=True)


def test_career_trace_compiles():
    """scripts/lib/career_trace.py must compile cleanly."""
    import py_compile  # noqa: PLC0415
    path = SCRIPTS_DIR / "lib" / "career_trace.py"
    assert path.exists(), "scripts/lib/career_trace.py missing"
    py_compile.compile(str(path), doraise=True)


# ---------------------------------------------------------------------------
# Migrated unit checks from standalone test_career_search.py & test_career_content.py
# ---------------------------------------------------------------------------

def test_career_state_deep_freeze_and_fingerprints():
    from types import MappingProxyType
    from lib.career_state import SCORED_STATUSES, cross_tracker_dedup_match, deep_freeze, fingerprint_posting

    frozen = deep_freeze({"a": [1, 2, {"b": 3}], "c": "hello", "d": {1, 2}})
    assert isinstance(frozen, MappingProxyType)
    assert isinstance(frozen["a"], tuple)
    assert isinstance(frozen["a"][2], MappingProxyType)
    assert frozen["a"][2]["b"] == 3
    with pytest.raises(TypeError):
        frozen["new_key"] = "fail"  # type: ignore[index]

    fp1 = fingerprint_posting("Acme Corp", "Senior AI Engineer", "Seattle, WA", "https://jobs.acme.com/123")
    fp2 = fingerprint_posting("Acme Corp", "Senior AI Engineer", "Seattle, WA", "https://jobs.acme.com/123")
    fp3 = fingerprint_posting("Acme Corp", "Senior AI Engineer", "Seattle, WA", "https://jobs.acme.com/456")
    assert fp1 == fp2 != fp3

    for excluded in ("SKIP", "Rejected", "Discarded"):
        assert excluded not in SCORED_STATUSES
    for included in ("Evaluated", "Applied", "Offer", "Interview"):
        assert included in SCORED_STATUSES

    existing = [
        {"num": "001", "company": "Acme Corp", "role": "Senior AI Engineer", "notes": "Seattle"},
        {"num": "002", "company": "Beta Inc", "role": "ML Platform Engineer", "notes": "Remote"},
    ]
    assert cross_tracker_dedup_match("Acme Corp", "Senior AI Engineer", "Seattle", existing) == "001"
    assert cross_tracker_dedup_match("Other Corp", "Data Scientist", "NYC", existing) is None


def test_career_story_bank_index_and_state_reconciliation(tmp_path):
    import yaml
    from lib.career_state import build_story_bank_index, is_campaign_active, parse_story_bank_index, reconcile_summary

    body = "## Story Bank\n<!-- INDEX: 1:Scaling ML Pipeline(AI Platform / LLMOps), 2:Multi-Agent Orchestration(Agentic / Automation) -->\n"
    parsed = parse_story_bank_index(body)
    assert parsed[1] == ("Scaling ML Pipeline", "AI Platform / LLMOps")
    assert 2 in parsed
    assert parse_story_bank_index("## Story Bank\n<!-- INDEX: -->\n") == {}

    import re
    stories = {i: (f"Story {i}", "AI Platform / LLMOps") for i in range(1, 30)}
    index_str = build_story_bank_index(stories, {1, 2, 3, 4, 5, 6})
    ids = [int(n) for n in re.findall(r"(\d+):[^,>]+", index_str)]
    assert len(ids) <= 20
    assert sum(1 for n in (1, 2, 3, 4, 5, 6) if n in ids) <= 5

    tpl = (REPO_ROOT / "state" / "templates" / "career_search.md").read_text(encoding="utf-8")
    state_file = tmp_path / "career_search.md"
    state_file.write_text(tpl, encoding="utf-8")
    reconcile_summary(state_file)
    fm = yaml.safe_load(state_file.read_text(encoding="utf-8").split("---", 2)[1])
    assert {"total", "by_status", "average_score", "data_quality", "validation_errors"} <= set(fm["summary"])
    assert isinstance(is_campaign_active(state_file), bool)


def test_career_trace_events_and_pdf_normalization(tmp_path, monkeypatch):
    from lib.career_trace import CareerTrace
    from skills.career_pdf_generator import CareerPdfGenerator, _normalize_unicode, _slug

    normalized = _normalize_unicode("Experience \u2014 built systems \u201cwith care\u201d and \u2018passion\u2019")
    assert "\u2014" not in normalized and "\u201c" not in normalized and "\u201d" not in normalized
    assert "\u2018" not in normalized and "built systems" in normalized

    assert _slug("ACME Corp") == "acme-corp"
    assert _slug("AnthropicAI!") == "anthropicai"
    assert _slug("OpenAI, Inc.") == "openai-inc"
    assert len(_slug("A" * 100)) <= 40

    gen = CareerPdfGenerator(report_number="042")
    assert (gen.report_number, gen.name, gen.priority, gen.status) == ("042", "career_pdf_generator", "P1", "idle")
    assert len(gen.compare_fields) > 0
    monkeypatch.setattr("skills.career_pdf_generator.is_campaign_active", lambda *_a: True)
    with pytest.raises(ValueError, match="report_number is required"):
        CareerPdfGenerator().pull()
    monkeypatch.setattr("skills.career_pdf_generator.is_campaign_active", lambda *_a: False)
    assert CareerPdfGenerator(report_number="001").pull()["status"] == "skipped"

    trace = CareerTrace(trace_path=tmp_path / "trace.jsonl")
    trace.write_guardrail_event(
        guardrail="CareerJDInjectionGR", triggered=True, action="blocked",
        detail="Suspected injection: <system> tag", report_number="001", jd_url="https://jobs.test.com/123",
    )
    trace.write_eval_entry(
        report_number="001", company="Acme", role="Senior AI Engineer", archetype="AI Platform / LLMOps",
        score=4.3, blocks_completed=["A", "B", "C", "D", "E", "F", "G"],
        posting_fingerprint="acme+senior-ai-engineer+seattle+abc123", cv_content_hash="deadbeef",
        block_d_source="gemini_cli", input_tokens_estimate=18000, output_tokens_estimate=14000,
    )
    entries = trace.read_all()
    assert len(entries) == 2
    assert entries[0]["event"] == "CAREER-GUARDRAIL" and entries[0]["triggered"] is True
    assert entries[1]["event"] == "career_eval" and "input_tokens_estimate" in entries[1]
    assert entries[1].get("input_tokens") is None and len(entries[1]["blocks_completed"]) == 7


def test_career_apply_and_prep_helpers():
    from career_apply import apply as apply_fn, _find_row_index, _update_tracker_row
    from career_prep import _count_questions, _extract_section, _extract_story_titles, _tokens

    assert callable(apply_fn)
    sample_body = "| 999 | 2026-04-18 | TestCo | TPM | 4.5 | Evaluated | — | report | notes |\n"
    assert _find_row_index(sample_body, "999") == 0
    new_body, row = _update_tracker_row(sample_body, "999", "Applied", "applied 2026-04-18")
    assert "| Applied |" in new_body and row["company"] == "TestCo"
    with pytest.raises(ValueError, match=r"Report\ 999\ status\ is\ 'Applied',\ expected\ 'Evaluated'\.\ Apply\ can\ only\ transition\ Evaluated\ rows\."):
        _update_tracker_row(new_body, "999", "Applied", "")

    prep_body = (
        "## F) Interview Prep\n\n### Recommended STAR Stories for This Role\n\n"
        "| # | Story | Maps To | Status |\n|---|---|---|---|\n"
        "| 1 | ProgramX Ramp | migration | Pinned |\n| 2 | DataOps build | DX | Pinned |\n\n"
        '### Red-Flag Questions to Prepare For\n\n1. **"q1"**\n   -> A\n\n2. **"q2"**\n   -> B\n\n'
        "## G) Posting Legitimacy\n\nOK"
    )
    sec = _extract_section(prep_body, "F")
    assert "ProgramX Ramp" in sec and not sec.rstrip().endswith("---")
    assert _extract_story_titles(sec)[0] == "ProgramX Ramp" and len(_extract_story_titles(sec)) == 2
    assert _count_questions(sec) == 2
    assert "relay" in _tokens("Amazon Relay Load Board — Middle Mile")


def test_career_cover_letter_helpers_when_installed():
    mod = pytest.importorskip("skills.career_cover_letter")
    cover = mod.CareerCoverLetter(report_number="001")
    assert "md_path" in cover.compare_fields and "pdf_path" in cover.compare_fields
    fm = mod._extract_frontmatter("---\ncompany: Acme\nurl: https://a.com/b---c/d\nscore: 4.2\n---\nbody")
    assert fm.get("company") == "Acme" and fm.get("score") == 4.2
    letter = mod._compose_letter_md(
        company="Acme", role="Sr TPM", archetype="AI Platform", contact="ved@example.com",
        headline_phrases=["directly matches your roadmap"], cv_match_evidence=["- Req X -> evidence Y"],
        proof_points=[], posture="I bring the right fit", keywords=["ml", "tpm"],
    )
    assert letter.count("\n\n") >= 4 and "()" not in letter


def test_career_content_prompts_and_templates():
    emp = (REPO_ROOT / "prompts" / "employment.md").read_text(encoding="utf-8")
    assert all(k in emp for k in ("Career Search Mode", "career_search.md", "Non-compete", "Benefits continuity"))

    cs_prompt = (REPO_ROOT / "prompts" / "career_search.md").read_text(encoding="utf-8")
    for needle in ("schema_version", "Block A", "Block B", "Block C", "Block D", "Block E", "Block F", "Block G",
                   "scoring_weights", "reconcile_summary", "write_state_atomic", "CareerJDInjectionGR"):
        assert needle in cs_prompt

    tpl = (REPO_ROOT / "state" / "templates" / "career_search.md").read_text(encoding="utf-8")
    assert all(k in tpl for k in ("campaign:", "scoring_weights_fallback:", "summary:"))

    cv_html = (REPO_ROOT / "templates" / "cv-template.html").read_text(encoding="utf-8")
    assert all(k in cv_html for k in ("{{CV_CONTENT}}", "{{COMPANY}}", "{{ROLE}}", "Space Grotesk", "DM Sans", "local(", "ATS"))

