#!/usr/bin/env python3
"""Content validation — verify file structure matches spec requirements."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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
print("Career Search — File Content Validation")
print("=" * 55)

# employment.md
print("\n[1] prompts/employment.md — Career Search Mode extension")
text = (REPO / "prompts" / "employment.md").read_text()
check("Career Search Mode section added", "Career Search Mode" in text)
check("career_search.md referenced", "career_search.md" in text)
check("Non-compete coverage", "Non-compete" in text)
check("Benefits continuity note", "Benefits continuity" in text)

# career_search.md prompt
print("\n[2] prompts/career_search.md — Evaluation blocks & spec compliance")
text = (REPO / "prompts" / "career_search.md").read_text()
check("schema_version header", "schema_version" in text)
check("Block A present", "Block A" in text)
check("Block B present", "Block B" in text)
check("Block C present", "Block C" in text)
check("Block D present", "Block D" in text)
check("Block E present", "Block E" in text)
check("Block F present", "Block F" in text)
check("Block G present", "Block G" in text)
check("scoring_weights reference", "scoring_weights" in text)
check("reconcile_summary reference", "reconcile_summary" in text)
check("write_state_atomic reference", "write_state_atomic" in text)
check("CareerJDInjectionGR reference", "CareerJDInjectionGR" in text)
check("NEVER auto-submit rule", "auto-submit" in text.lower() and "never" in text.lower())
check("CS-3 deferred exec rule", "CS-3" in text or "deferred" in text.lower())
check("Auth wall detection", "Auth wall" in text or "auth wall" in text.lower())
check("Pre-eval confirmation gate (V-F2)", "Pre-flight" in text or "confirmation gate" in text.lower())
check("Immigration filter", "Immigration filter" in text or "Immigration Blocker" in text)
check("Prompt injection guard", "prompt injection" in text.lower())

# state/career_search.md
print("\n[3] state/career_search.md — Schema completeness")
text = (REPO / "state" / "career_search.md").read_text()
check("schema_version: 1.0", 'schema_version:' in text and ('"1.0"' in text or "'1.0'" in text))
check("campaign: block", "campaign:" in text)
check("status: active", "status: active" in text)
check("archetypes: block", "archetypes:" in text)
check("scoring_weights: block", "scoring_weights:" in text)
check("scoring_weights_fallback: block", "scoring_weights_fallback:" in text)
check("cv_content_hash field", "cv_content_hash:" in text)
check("summary: block", "summary:" in text)
check("by_status dict", "by_status:" in text)
check("average_score field", "average_score:" in text)
check("validation_errors field", "validation_errors:" in text)
check("data_quality field", "data_quality:" in text)
check("Story Bank section", "## Story Bank" in text)
check("INDEX comment present", "<!-- INDEX:" in text)
check("Applications table", "## Applications" in text)

# state/templates/career_search.md
print("\n[4] state/templates/career_search.md — Template completeness")
text = (REPO / "state" / "templates" / "career_search.md").read_text()
check("Template campaign block", "campaign:" in text)
check("Template scoring_weights_fallback", "scoring_weights_fallback:" in text)
check("Template summary block", "summary:" in text)

# templates/cv-template.html
print("\n[5] templates/cv-template.html — ATS CV template")
text = (REPO / "templates" / "cv-template.html").read_text()
check("CV_CONTENT placeholder", "{{CV_CONTENT}}" in text)
check("COMPANY placeholder", "{{COMPANY}}" in text)
check("ROLE placeholder", "{{ROLE}}" in text)
check("Space Grotesk font", "Space Grotesk" in text)
check("DM Sans font", "DM Sans" in text)
check("Self-hosted fonts (no CDN)", "local(" in text)
check("ATS note comment", "ATS" in text)
check("Single-column layout", "single-column" in text.lower() or "ATS" in text)
check("Unicode normalization handled", "{{CV_CONTENT}}" in text)

# Directories
print("\n[6] Required directories created")
check("briefings/career/ exists", (REPO / "briefings" / "career").is_dir())
check("output/career/ exists", (REPO / "output" / "career").is_dir())
check("templates/ exists", (REPO / "templates").is_dir())

print(f"\n{'=' * 55}")
print(f"Results: {PASS} passed, {FAIL} failed")
print("=" * 55)
if FAIL == 0:
    print("ALL CONTENT VALIDATION CHECKS PASSED ✅")
if __name__ == "__main__":
    sys.exit(0 if FAIL == 0 else 1)
