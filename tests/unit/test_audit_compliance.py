"""tests/unit/test_audit_compliance.py — Unit tests for audit_compliance.py"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from scripts.audit_compliance import (
    ComplianceReport,
    audit_latest_briefing,
    _check_connector_health_block,
    _check_domain_sections_present,
    _check_no_unacknowledged_snippets,
    _check_one_thing_present,
    _check_pii_footer,
    _check_preflight_executed,
    _check_state_files_referenced,
    _detect_degraded_mode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _briefing(content: str, tmp_path: Path) -> Path:
    p = tmp_path / "briefing.md"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

class TestCheckPreflightExecuted:
    def test_passes_with_preflight_word(self):
        r = _check_preflight_executed("Preflight: PASS — all P0 checks passed.")
        assert r.passed

    def test_passes_with_advisory_mode(self):
        r = _check_preflight_executed("⚠️ ADVISORY MODE — 3 advisories")
        assert r.passed

    def test_fails_when_absent(self):
        r = _check_preflight_executed("Today was a nice day.")
        assert not r.passed

    def test_weight_is_20(self):
        assert _check_preflight_executed("").weight == 20


class TestCheckConnectorHealthBlock:
    def test_passes_with_table(self):
        text = "### 🔌 Connector & Token Health\n| Connector | Status |\n|-----------|--------|\n"
        r = _check_connector_health_block(text)
        assert r.passed

    def test_passes_with_connector_offline_phrase(self):
        r = _check_connector_health_block("2 connectors offline — MS Graph missing")
        assert r.passed

    def test_fails_when_absent(self):
        r = _check_connector_health_block("Great briefing with some domain info.")
        assert not r.passed

    def test_weight_is_25(self):
        assert _check_connector_health_block("").weight == 25


class TestCheckStateFilesReferenced:
    def test_passes_with_two_files(self):
        r = _check_state_files_referenced("Loaded health-check.md and open_items.md")
        assert r.passed

    def test_passes_with_all_three(self):
        r = _check_state_files_referenced(
            "Loaded health-check.md, open_items, and memory.md"
        )
        assert r.passed

    def test_fails_with_only_one(self):
        r = _check_state_files_referenced("Read health-check.md only.")
        assert not r.passed

    def test_fails_when_absent(self):
        r = _check_state_files_referenced("No state file references here.")
        assert not r.passed


class TestCheckPiiFooter:
    def test_passes_with_emails_scanned(self):
        r = _check_pii_footer("PII stats: emails_scanned: 72, redactions_applied: 3")
        assert r.passed

    def test_passes_with_pii_scan_phrase(self):
        r = _check_pii_footer("PII scan: 48 emails, 0 redactions")
        assert r.passed

    def test_fails_when_absent(self):
        r = _check_pii_footer("Nothing about pii here.")
        assert not r.passed


class TestCheckNoUnacknowledgedSnippets:
    def test_passes_when_no_snippets(self):
        r = _check_no_unacknowledged_snippets("Full email bodies were read.")
        assert r.passed

    def test_passes_when_snippet_acknowledged(self):
        text = "Subject: Renewal [snippet — verify in email client]"
        r = _check_no_unacknowledged_snippets(text)
        assert r.passed

    def test_fails_on_raw_snippet(self):
        r = _check_no_unacknowledged_snippets("Email snippet: Your bill is due soon.")
        assert not r.passed


class TestCheckDomainSections:
    def test_passes_with_two_domains(self):
        r = _check_domain_sections_present("## Finance\n...\n## Immigration\n...")
        assert r.passed

    def test_fails_with_one_domain(self):
        r = _check_domain_sections_present("## Finance\nonly one domain here.")
        assert not r.passed


class TestCheckOneThingPresent:
    def test_passes_with_one_thing_heading(self):
        r = _check_one_thing_present("## ONE THING\nFile your I-765 today.")
        assert r.passed

    def test_passes_with_uia_score(self):
        r = _check_one_thing_present("[U×I×A = 9] immigration")
        assert r.passed

    def test_fails_when_absent(self):
        r = _check_one_thing_present("Nice briefing but no priority block.")
        assert not r.passed


# ---------------------------------------------------------------------------
# Degraded mode detection
# ---------------------------------------------------------------------------

class TestDetectDegradedMode:
    def test_detects_read_only_from_session_metadata(self):
        text = textwrap.dedent("""
            ## Session Metadata
            - environment: cowork_vm
            - mode: read-only
            - state_files_read: 3
        """)
        degraded, meta = _detect_degraded_mode(text)
        assert degraded
        assert meta["mode"] == "read-only"

    def test_detects_read_only_from_header(self):
        text = "⚠️ READ-ONLY MODE — no state files updated this session"
        degraded, meta = _detect_degraded_mode(text)
        assert degraded

    def test_normal_mode_not_flagged(self):
        text = "Everything ran normally. All connectors healthy."
        degraded, meta = _detect_degraded_mode(text)
        assert not degraded

    def test_degraded_mode_from_connectors_offline(self):
        text = "2 connectors offline — degraded briefing generated"
        degraded, _ = _detect_degraded_mode(text)
        assert degraded


# ---------------------------------------------------------------------------
# Full audit_latest_briefing integration
# ---------------------------------------------------------------------------

class TestAuditLatestBriefing:
    def test_missing_file_returns_zero_score(self, tmp_path):
        report = audit_latest_briefing(str(tmp_path / "nonexistent.md"))
        assert report.compliance_score == 0
        assert len(report.warnings) > 0

    def test_perfect_briefing_scores_100(self, tmp_path):
        content = textwrap.dedent("""\
            # Catch-Up Briefing — 2026-03-15

            Preflight: PASS — all P0 checks passed.

            Loaded health-check.md, open_items.md, and memory.md

            ## Finance
            Bills up to date.

            ## Immigration
            No urgent items.

            ## ONE THING
            [U×I×A = 9] immigration — File N-400 by April 1.

            ### 🔌 Connector & Token Health
            | Connector | Status |
            |-----------|--------|
            | Gmail     | ✅ Online |

            PII stats: emails_scanned: 48, redactions_applied: 0
        """)
        p = _briefing(content, tmp_path)
        report = audit_latest_briefing(str(p))
        assert report.compliance_score == 100
        assert not report.degraded_mode
        assert report.non_compliant_steps == []

    def test_empty_briefing_scores_low(self, tmp_path):
        p = _briefing("Nothing here.", tmp_path)
        report = audit_latest_briefing(str(p))
        assert report.compliance_score < 30

    def test_degraded_mode_reduces_connector_weight(self, tmp_path):
        content = textwrap.dedent("""\
            ⚠️ READ-ONLY MODE

            Preflight: advisory mode — 3 advisories.

            Loaded health-check.md and open_items.md.

            ## Finance\nOK. ## Immigration\nOK.

            ## ONE THING\n[U×I×A = 6] finance

            ## Session Metadata
            - mode: read-only
            - environment: cowork_vm

            PII scan: limited (MCP data only)
        """)
        p = _briefing(content, tmp_path)
        report = audit_latest_briefing(str(p))
        assert report.degraded_mode
        # Connector block absent but score still passes (reduced weight)
        connector_check = next(
            (c for c in report.checks if c.name == "connector_health_block_present"), None
        )
        assert connector_check is not None
        assert connector_check.weight == 15  # reduced from 25 in degraded mode

    def test_to_dict_is_json_serializable(self, tmp_path):
        p = _briefing("Some briefing text.", tmp_path)
        report = audit_latest_briefing(str(p))
        d = report.to_dict()
        # Must be JSON-serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert "compliance_score" in parsed
        assert "checks" in parsed
        assert "non_compliant_steps" in parsed

    def test_non_compliant_steps_listed(self, tmp_path):
        p = _briefing("No relevant content at all.", tmp_path)
        report = audit_latest_briefing(str(p))
        assert "preflight_executed" in report.non_compliant_steps
        assert "connector_health_block_present" in report.non_compliant_steps

    def test_snippet_violation_recorded(self, tmp_path):
        content = "This email snippet says your visa is expiring soon."
        p = _briefing(content, tmp_path)
        report = audit_latest_briefing(str(p))
        assert "email_bodies_not_snippets" in report.non_compliant_steps


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

class TestCli:
    def test_cli_produces_json(self, tmp_path, capsys):
        content = "Preflight PASS. health-check.md loaded. memory.md referenced. Finance OK."
        p = _briefing(content, tmp_path)
        from scripts.audit_compliance import main
        main([str(p), "--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "compliance_score" in data

    def test_cli_exit_1_below_threshold(self, tmp_path):
        p = _briefing("Minimal content.", tmp_path)
        from scripts.audit_compliance import main
        rc = main([str(p), "--json", "--threshold", "90"])
        assert rc == 1

    def test_cli_exit_0_above_threshold(self, tmp_path):
        content = textwrap.dedent("""\
            Preflight PASS. health-check.md, open_items.md, memory.md.
            Finance. Immigration. ONE THING [U×I×A = 9].
            Connector & Token Health table.
            emails_scanned: 20, redactions_applied: 0
        """)
        p = _briefing(content, tmp_path)
        from scripts.audit_compliance import main
        rc = main([str(p), "--json", "--threshold", "50"])
        assert rc == 0
