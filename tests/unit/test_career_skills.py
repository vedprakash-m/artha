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

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


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
    from skills import career_pdf_generator as module

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
        if not result["success"] and "Executable doesn't exist" in result.get("error", ""):
            pytest.skip("Playwright Chromium is not installed")
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
