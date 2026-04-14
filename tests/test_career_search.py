#!/usr/bin/env python3
"""Career Search Intelligence — Phase 1 validation tests."""
import json
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import os
os.chdir(str(_REPO_ROOT))  # ensure relative paths (state/, etc.) resolve correctly

PASS = 0
FAIL = 0

def check(name, expr, detail=""):
    global PASS, FAIL
    if expr:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ FAIL: {name} {detail}")
        FAIL += 1

print("=" * 55)
print("Career Search Intelligence — Validation Tests")
print("=" * 55)

# ── Test 1: Import career_state ──────────────────────────────
print("\n[1] career_state imports")
try:
    from lib.career_state import (
        reconcile_summary, recompute_scores, _patch_frontmatter_field,
        deep_freeze, fingerprint_posting, fingerprint_action,
        fingerprint_report, cross_tracker_dedup_match,
        parse_story_bank_index, build_story_bank_index,
        next_report_number, is_campaign_active, SCORED_STATUSES,
        ALL_STATUSES,
    )
    check("All career_state symbols imported", True)
except ImportError as e:
    check("career_state import", False, str(e))
    if __name__ == "__main__":
        sys.exit(1)

# ── Test 2: Import career_trace ──────────────────────────────
print("\n[2] career_trace imports")
try:
    from lib.career_trace import CareerTrace, get_trace, GUARDRAIL_TRACE_SCHEMA_VERSION
    check("All career_trace symbols imported", True)
except ImportError as e:
    check("career_trace import", False, str(e))
    if __name__ == "__main__":
        sys.exit(1)

# ── Test 3: Import career_pdf_generator ─────────────────────
print("\n[3] career_pdf_generator imports")
try:
    from skills.career_pdf_generator import (
        CareerPdfGenerator, SkillDependencyError,
        _normalize_unicode, _slug, _warn_cliches,
    )
    check("All career_pdf_generator symbols imported", True)
except ImportError as e:
    check("career_pdf_generator import", False, str(e))
    if __name__ == "__main__":
        sys.exit(1)

# ── Test 4: deep_freeze correctness ─────────────────────────
print("\n[4] deep_freeze correctness")
raw = {"a": [1, 2, {"b": 3}], "c": "hello", "d": {1, 2}}
frozen = deep_freeze(raw)
check("top-level is MappingProxyType", isinstance(frozen, MappingProxyType))
check("list becomes tuple", isinstance(frozen["a"], tuple))
check("nested dict becomes MappingProxyType", isinstance(frozen["a"][2], MappingProxyType))
check("nested value preserved", frozen["a"][2]["b"] == 3)
try:
    frozen["new_key"] = "fail"
    check("immutability enforced", False, "should have raised TypeError")
except TypeError:
    check("immutability enforced (raises TypeError)", True)

# ── Test 5: fingerprint_posting determinism ──────────────────
print("\n[5] fingerprint_posting determinism")
fp1 = fingerprint_posting("Acme Corp", "Senior AI Engineer", "Seattle, WA", "https://jobs.acme.com/123")
fp2 = fingerprint_posting("Acme Corp", "Senior AI Engineer", "Seattle, WA", "https://jobs.acme.com/123")
fp3 = fingerprint_posting("Acme Corp", "Senior AI Engineer", "Seattle, WA", "https://jobs.acme.com/456")
check("Same args produce same fingerprint", fp1 == fp2)
check("Different URL produces different fingerprint", fp1 != fp3)

# ── Test 6: unicode normalization ────────────────────────────
print("\n[6] Unicode normalization (ATS compliance)")
text = "Experience \u2014 built systems \u201cwith care\u201d and \u2018passion\u2019"
normalized = _normalize_unicode(text)
check("em-dash replaced", "\u2014" not in normalized)
check("left double quote replaced", "\u201c" not in normalized)
check("right double quote replaced", "\u201d" not in normalized)
check("left single quote replaced", "\u2018" not in normalized)
check("text content preserved", "built systems" in normalized)

# ── Test 7: SCORED_STATUSES (W-F8) ───────────────────────────
print("\n[7] SCORED_STATUSES excludes SKIP/Rejected/Discarded (W-F8)")
check("SKIP excluded", "SKIP" not in SCORED_STATUSES)
check("Rejected excluded", "Rejected" not in SCORED_STATUSES)
check("Discarded excluded", "Discarded" not in SCORED_STATUSES)
check("Evaluated included", "Evaluated" in SCORED_STATUSES)
check("Applied included", "Applied" in SCORED_STATUSES)
check("Offer included", "Offer" in SCORED_STATUSES)
check("Interview included", "Interview" in SCORED_STATUSES)

# ── Test 8: CareerPdfGenerator init ─────────────────────────
print("\n[8] CareerPdfGenerator init (§9.1)")
gen = CareerPdfGenerator(report_number="042")
check("report_number stored", gen.report_number == "042")
check("name set correctly", gen.name == "career_pdf_generator")
check("priority set correctly", gen.priority == "P1")
check("status starts idle", gen.status == "idle")
check("compare_fields non-empty", len(gen.compare_fields) > 0)

# ── Test 9: ValueError without report_number ─────────────────
print("\n[9] CareerPdfGenerator requires report_number (§9.1)")
gen_no_num = CareerPdfGenerator()
with patch("skills.career_pdf_generator.is_campaign_active", return_value=True):
    try:
        gen_no_num.pull()
        check("raises ValueError", False, "should have raised")
    except ValueError as e:
        check("raises ValueError with clear message", "report_number is required" in str(e))

# ── Test 10: Skill skips when no active campaign ─────────────
print("\n[10] Skill skips when campaign inactive (activation guard §9.1)")
gen_inactive = CareerPdfGenerator(report_number="001")
with patch("skills.career_pdf_generator.is_campaign_active", return_value=False):
    result = gen_inactive.pull()
check("status is skipped", result.get("status") == "skipped")
check("reason provided", "reason" in result)

# ── Test 11: parse_story_bank_index ─────────────────────────
print("\n[11] parse_story_bank_index (FR-CS-7)")
body = "## Story Bank\n<!-- INDEX: 1:Scaling ML Pipeline(AI Platform / LLMOps), 2:Multi-Agent Orchestration(Agentic / Automation) -->\n"
index = parse_story_bank_index(body)
check("story 1 parsed", 1 in index)
check("story 1 title correct", index[1][0] == "Scaling ML Pipeline")
check("story 1 archetype correct", index[1][1] == "AI Platform / LLMOps")
check("story 2 parsed", 2 in index)
check("empty INDEX returns empty dict", parse_story_bank_index("## Story Bank\n<!-- INDEX: -->\n") == {})

# ── Test 12: build_story_bank_index cap enforcement ──────────
print("\n[12] Story Bank INDEX cap 20 (PE-3)")
stories = {i: (f"Story {i}", "AI Platform / LLMOps") for i in range(1, 30)}
pinned = {1, 2, 3, 4, 5, 6}  # 6 pinned — should be capped at 5
index_str = build_story_bank_index(stories, pinned)
index_reparsed = {}
import re
for entry in re.findall(r"(\d+):[^,>]+", index_str):
    index_reparsed[int(entry)] = True
check("INDEX stays at 20 stories max", len(index_reparsed) <= 20)
check("Pinned stories capped at 5", sum(1 for n in [1,2,3,4,5,6] if n in index_reparsed) <= 5)

# ── Test 13: CareerTrace CAREER-GUARDRAIL schema (N-F4) ──────
print("\n[13] CareerTrace CAREER-GUARDRAIL event schema (N-F4)")
with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
    tmp_path = Path(f.name)
trace = CareerTrace(trace_path=tmp_path)
trace.write_guardrail_event(
    guardrail="CareerJDInjectionGR",
    triggered=True,
    action="blocked",
    detail="Suspected injection: <system> tag",
    report_number="001",
    jd_url="https://jobs.test.com/123",
)
entries = trace.read_all()
check("One entry written", len(entries) == 1)
e = entries[0]
check("event == CAREER-GUARDRAIL", e.get("event") == "CAREER-GUARDRAIL")
check("guardrail field present", e.get("guardrail") == "CareerJDInjectionGR")
check("triggered field present", e.get("triggered") is True)
check("action field present", e.get("action") == "blocked")
check("schema_version present", "schema_version" in e)
check("timestamp present", "timestamp" in e)
tmp_path.unlink()

# ── Test 14: CareerTrace eval entry SM-5 token fields ────────
print("\n[14] CareerTrace eval entry token _estimate suffix (SM-5)")
with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
    tmp_path = Path(f.name)
trace2 = CareerTrace(trace_path=tmp_path)
trace2.write_eval_entry(
    report_number="001",
    company="Acme",
    role="Senior AI Engineer",
    archetype="AI Platform / LLMOps",
    score=4.3,
    blocks_completed=["A", "B", "C", "D", "E", "F", "G"],
    posting_fingerprint="acme+senior-ai-engineer+seattle+abc123",
    cv_content_hash="deadbeef",
    block_d_source="gemini_cli",
    input_tokens_estimate=18000,
    output_tokens_estimate=14000,
)
entries2 = trace2.read_all()
check("eval entry written", len(entries2) == 1)
ev = entries2[0]
check("event == career_eval", ev.get("event") == "career_eval")
check("input_tokens_estimate present", "input_tokens_estimate" in ev)
check("output_tokens_estimate present", "output_tokens_estimate" in ev)
check("NO 'input_tokens' without _estimate", "input_tokens" not in ev or ev.get("input_tokens") is None)
check("blocks_completed complete", len(ev.get("blocks_completed", [])) == 7)
check("enrichments dict present", isinstance(ev.get("enrichments"), dict))
tmp_path.unlink()

# ── Test 15: reconcile_summary with real state file ──────────
print("\n[15] reconcile_summary with real state/career_search.md")
state_path = Path("state/career_search.md")
if state_path.exists():
    result = reconcile_summary(state_path)
    check("reconcile_summary runs without error", True)
    # Read back
    from work.helpers import _read_frontmatter
    fm = _read_frontmatter(state_path)
    summary = fm.get("summary", {})
    check("summary.total present", "total" in summary)
    check("summary.by_status present", "by_status" in summary)
    check("summary.average_score present (may be null)", "average_score" in summary)
    check("summary.data_quality present", "data_quality" in summary)
    check("summary.validation_errors present", "validation_errors" in summary)
else:
    print("  ⏭ SKIP: state/career_search.md not present (gitignored on CI)")

# ── Test 16: is_campaign_active ──────────────────────────────
print("\n[16] is_campaign_active reads campaign.status")
state_path = Path("state/career_search.md")
if state_path.exists():
    result = is_campaign_active(state_path)
    check("is_campaign_active returns bool", isinstance(result, bool))
    check("campaign active (expected True from template)", result is True)
else:
    print("  ⏭ SKIP: state/career_search.md not present (gitignored on CI)")

# ── Test 17: _slug ───────────────────────────────────────────
print("\n[17] _slug for PDF filename generation")
check("ACME Corp slug", _slug("ACME Corp") == "acme-corp")
check("AnthropicAI slug", _slug("AnthropicAI!") == "anthropicai")
check("OpenAI, Inc. slug", _slug("OpenAI, Inc.") == "openai-inc")
check("Max 40 chars", len(_slug("A" * 100)) <= 40)

# ── Test 18: cross_tracker_dedup_match ───────────────────────
print("\n[18] cross_tracker_dedup_match (Jaccard dedup FR-CS-6)")
existing = [
    {"num": "001", "company": "Acme Corp", "role": "Senior AI Engineer", "notes": "Seattle"},
    {"num": "002", "company": "Beta Inc", "role": "ML Platform Engineer", "notes": "Remote"},
]
match = cross_tracker_dedup_match("Acme Corp", "Senior AI Engineer", "Seattle", existing)
check("Exact match found", match == "001")
no_match = cross_tracker_dedup_match("Other Corp", "Data Scientist", "NYC", existing)
check("No match returns None", no_match is None)

# ── Summary ──────────────────────────────────────────────────
print("\n" + "=" * 55)
print(f"Results: {PASS} passed, {FAIL} failed")
print("=" * 55)
if __name__ == "__main__":
    if FAIL > 0:
        sys.exit(1)
    else:
        print("ALL TESTS PASSED ✅")
