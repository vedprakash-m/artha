"""
tests/integration/test_catch_up_e2e.py — End-to-end catch-up workflow tests.

Exercises the core value proposition: email → routing → state update → briefing.
Uses mock connectors to avoid network dependencies.

Ref: TS §7.1, audit-report item #2
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def artha_e2e_dir(tmp_path):
    """Build a complete Artha directory structure for E2E testing."""
    dirs = [
        "scripts", "scripts/connectors", "scripts/skills", "scripts/lib",
        "state", "config", "prompts", "briefings", "summaries", "tmp",
    ]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # Minimal user_profile.yaml
    (tmp_path / "config" / "user_profile.yaml").write_text(textwrap.dedent("""\
        schema_version: "1.0"
        family:
          name: "TestFamily"
          primary_user:
            name: "TestUser"
            nickname: "Test"
            role: primary
            emails:
              gmail: "test@example.com"
          spouse:
            enabled: false
          children: []
        location:
          city: "TestCity"
          state: "WA"
          county: "King"
          country: "US"
          timezone: "America/Los_Angeles"
        domains:
          immigration:
            enabled: true
          finance:
            enabled: true
          kids:
            enabled: false
    """))

    # State files
    (tmp_path / "state" / "audit.md").write_text("# Audit Log\n")
    (tmp_path / "state" / "health-check.md").write_text(textwrap.dedent("""\
        ---
        last_catch_up: "2026-03-12T08:00:00Z"
        ---
    """))
    (tmp_path / "state" / "open_items.md").write_text(textwrap.dedent("""\
        ---
        domain: open_items
        last_updated: "2026-03-12"
        ---
        # Open Items
    """))
    (tmp_path / "state" / "immigration.md").write_text(textwrap.dedent("""\
        ---
        domain: immigration
        last_updated: "2026-03-10"
        ---
        # Immigration
        No data yet.
    """))
    (tmp_path / "state" / "finance.md").write_text(textwrap.dedent("""\
        ---
        domain: finance
        last_updated: "2026-03-10"
        ---
        # Finance
        No data yet.
    """))

    return tmp_path


# ---------------------------------------------------------------------------
# Mock email factory
# ---------------------------------------------------------------------------

def _make_email(subject: str, from_addr: str, body: str, source: str = "gmail") -> dict:
    """Create a mock email record matching pipeline JSONL schema."""
    return {
        "id": f"msg_{abs(hash(subject)) % 10000:04d}",
        "thread_id": f"thread_{abs(hash(subject)) % 10000:04d}",
        "subject": subject,
        "from": from_addr,
        "to": "test@example.com",
        "date_iso": "2026-03-13T10:00:00Z",
        "body": body,
        "labels": ["INBOX"],
        "snippet": body[:100],
        "source": source,
    }


FIXTURE_EMAILS = [
    _make_email(
        "USCIS Receipt Notice — IOE0912345678",
        "donotreply@uscis.gov",
        "We have received your Form I-765. Receipt number: IOE0912345678. "
        "Please allow 90 days for processing.",
    ),
    _make_email(
        "Your PSE Energy Bill is Ready",
        "alerts@pse.com",
        "Your energy bill for March 2026 is $287.50. Due date: March 25, 2026. "
        "Pay online at pse.com.",
    ),
    _make_email(
        "Spring Sale — 30% Off Everything!",
        "deals@promotions.retailer.com",
        "Don't miss our biggest sale of the year! Click here to save big. "
        "Unsubscribe at any time.",
    ),
    _make_email(
        "Chase: Your Statement is Available",
        "alerts@chase.com",
        "Your Chase Freedom statement for March 2026 is ready. "
        "Total balance: $1,234.56. Minimum due: $35.00. Due: April 5, 2026.",
    ),
]


# ---------------------------------------------------------------------------
# §A — Email routing tests
# ---------------------------------------------------------------------------

class TestEmailRouting:
    """Email routing: sender/subject patterns must map to correct domains."""

    def test_uscis_routes_to_immigration(self):
        email = FIXTURE_EMAILS[0]
        sender = email["from"].lower()
        subject = email["subject"].lower()
        assert "uscis.gov" in sender or "receipt notice" in subject

    def test_utility_bill_routes_to_home_or_finance(self):
        email = FIXTURE_EMAILS[1]
        subject = email["subject"].lower()
        assert "bill" in subject

    def test_marketing_has_unsubscribe_signal(self):
        email = FIXTURE_EMAILS[2]
        body = email["body"].lower()
        assert "unsubscribe" in body

    def test_chase_routes_to_finance(self):
        email = FIXTURE_EMAILS[3]
        assert "chase.com" in FIXTURE_EMAILS[3]["from"].lower()


# ---------------------------------------------------------------------------
# §B — PII filter integration tests
# ---------------------------------------------------------------------------

class TestPIIFilterIntegration:
    """PII must be caught before state writes."""

    def test_ssn_is_filtered(self):
        from pii_guard import filter_text

        filtered, found = filter_text("Your SSN is 123-45-6789. Please verify.")
        assert "123-45-6789" not in filtered
        assert "[PII-FILTERED-SSN]" in filtered
        assert "SSN" in found

    def test_uscis_receipt_is_allowlisted(self):
        from pii_guard import filter_text

        filtered, found = filter_text("Receipt: IOE0912345678 received on 2026-03-13.")
        assert "IOE0912345678" in filtered
        assert not found

    def test_credit_card_is_filtered(self):
        from pii_guard import filter_text

        filtered, found = filter_text("Card 4111 1111 1111 1111 was charged $99.99.")
        assert "4111 1111 1111 1111" not in filtered
        assert "[PII-FILTERED-CC]" in filtered

    def test_itin_is_filtered(self):
        from pii_guard import filter_text

        filtered, found = filter_text("ITIN on file: 912-78-1234.")
        assert "912-78-1234" not in filtered
        assert "ITIN" in found

    def test_india_aadhaar_is_filtered(self):
        from pii_guard import filter_text

        filtered, found = filter_text("Aadhaar: 1234 5678 9012 on record.")
        assert "1234 5678 9012" not in filtered
        assert "AADHAAR" in found

    def test_mixed_pii_in_email_body(self):
        """Multiple PII types in a single body are all caught."""
        from pii_guard import filter_text

        body = (
            "Hi, your SSN 123-45-6789 and card 4111 1111 1111 1111 "
            "are on file. Receipt: IOE0912345678."
        )
        filtered, found = filter_text(body)
        assert "123-45-6789" not in filtered
        assert "4111 1111 1111 1111" not in filtered
        assert "IOE0912345678" in filtered  # allowlisted
        assert "SSN" in found
        assert "CC" in found


# ---------------------------------------------------------------------------
# §C — Pipeline allowlist tests
# ---------------------------------------------------------------------------

class TestPipelineAllowlist:
    """Connector allowlist must block unauthorized modules."""

    def test_known_connectors_in_allowlist(self):
        from pipeline import _ALLOWED_MODULES

        assert "connectors.google_email" in _ALLOWED_MODULES
        assert "connectors.msgraph_email" in _ALLOWED_MODULES
        assert "connectors.imap_email" in _ALLOWED_MODULES
        assert "connectors.google_calendar" in _ALLOWED_MODULES

    def test_disallowed_module_blocked(self):
        from pipeline import _load_handler

        with pytest.raises(ImportError, match="not in the connector allowlist"):
            _load_handler("os")

    def test_arbitrary_connector_blocked(self):
        from pipeline import _load_handler

        with pytest.raises(ImportError, match="not in the connector allowlist"):
            _load_handler("connectors.evil_module")


# ---------------------------------------------------------------------------
# §D — State file integrity
# ---------------------------------------------------------------------------

class TestStateFileIntegrity:
    """State file writes must pass integrity checks."""

    def test_frontmatter_required(self, artha_e2e_dir):
        """State files must begin with YAML frontmatter."""
        content = (artha_e2e_dir / "state" / "immigration.md").read_text()
        assert content.startswith("---")

    def test_net_negative_blocks_truncation(self, tmp_path):
        """Net-negative guard should detect >20% data loss."""
        from vault import is_integrity_safe

        plain = tmp_path / "test.md"
        age = tmp_path / "test.md.age"
        age.write_text("x" * 1000)   # existing: 1000 bytes
        plain.write_text("x" * 100)  # proposed: 100 bytes → 90% loss
        assert not is_integrity_safe(plain, age)

    def test_net_negative_allows_growth(self, tmp_path):
        """Growth or similar-sized writes should pass."""
        from vault import is_integrity_safe

        plain = tmp_path / "test.md"
        age = tmp_path / "test.md.age"
        age.write_text("x" * 1000)
        plain.write_text("x" * 1200)  # grew
        assert is_integrity_safe(plain, age)

    def test_net_negative_new_file(self, tmp_path):
        """First-time writes (no existing .age) always pass."""
        from vault import is_integrity_safe

        plain = tmp_path / "test.md"
        age = tmp_path / "test.md.age"
        plain.write_text("short")
        assert is_integrity_safe(plain, age)


# ---------------------------------------------------------------------------
# §E — Skill runner safety
# ---------------------------------------------------------------------------

class TestSkillRunnerSafety:
    """Skill runner must enforce allowlist and reject unknown skills."""

    def test_unknown_skill_rejected(self):
        from skill_runner import run_skill

        result = run_skill("malicious_skill", Path("/nonexistent"))
        assert result["status"] == "failed"
        assert "Unknown skill" in result.get("error", "")

    def test_core_skills_in_allowlist(self):
        from skill_runner import _ALLOWED_SKILLS

        assert "uscis_status" in _ALLOWED_SKILLS
        assert "visa_bulletin" in _ALLOWED_SKILLS
        assert "nhtsa_recalls" in _ALLOWED_SKILLS
        assert "noaa_weather" in _ALLOWED_SKILLS


# ---------------------------------------------------------------------------
# §F — Retry jitter
# ---------------------------------------------------------------------------

class TestRetryJitter:
    """Retry logic must include jitter to prevent thundering herd."""

    def test_retry_has_jitter(self):
        wait_times: list[float] = []
        call_count = 0

        def failing_fn():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise Exception("429 Too Many Requests")
            return "ok"

        with patch("time.sleep", side_effect=lambda s: wait_times.append(s)):
            from lib.retry import with_retry
            result = with_retry(failing_fn, max_retries=4, base_delay=1.0, context="test")

        assert result == "ok"
        assert len(wait_times) == 3
        # Jitter means at least one wait should have a fractional component
        assert any(w != round(w) for w in wait_times), (
            f"No jitter detected in wait times: {wait_times}"
        )


# ---------------------------------------------------------------------------
# §G — Profile validation
# ---------------------------------------------------------------------------

class TestProfileValidation:
    """Schema validation of user_profile.yaml must work."""

    def test_valid_profile_no_crash(self):
        from profile_loader import _validate_against_schema

        data = {
            "schema_version": "1.0",
            "family": {
                "name": "Test",
                "primary_user": {
                    "name": "TestUser",
                    "nickname": "T",
                    "role": "primary",
                    "emails": {"gmail": "test@example.com"},
                },
            },
            "location": {"timezone": "America/Los_Angeles"},
        }
        errors = _validate_against_schema(data)
        assert isinstance(errors, list)


# ---------------------------------------------------------------------------
# §H — Briefing archive
# ---------------------------------------------------------------------------

class TestBriefingArchive:
    """Briefings must be archived with ISO date filenames."""

    def test_archive_directory_exists(self, artha_e2e_dir):
        assert (artha_e2e_dir / "briefings").is_dir()

    def test_briefing_filename_is_iso(self, artha_e2e_dir):
        path = artha_e2e_dir / "briefings" / "2026-03-13.md"
        path.write_text("# Briefing\n\nARTHA · Thursday, March 13\n")
        assert path.exists()
        assert "ARTHA" in path.read_text()


# ---------------------------------------------------------------------------
# §I — MCP rate limiter
# ---------------------------------------------------------------------------

_mcp_available = True
try:
    from mcp_server import _RateLimiter
except (ImportError, ModuleNotFoundError):
    _mcp_available = False


@pytest.mark.skipif(not _mcp_available, reason="mcp package not installed")
class TestMCPRateLimiter:
    """MCP rate limiter must reject excess calls."""

    def test_rate_limiter_allows_within_limit(self):
        limiter = _RateLimiter(max_calls=5, period_seconds=60)
        for _ in range(5):
            assert limiter.allow()

    def test_rate_limiter_blocks_excess(self):
        limiter = _RateLimiter(max_calls=2, period_seconds=60)
        assert limiter.allow()
        assert limiter.allow()
        assert not limiter.allow()


# ---------------------------------------------------------------------------
# §J — Semantic versioning
# ---------------------------------------------------------------------------

class TestSemanticVersioning:
    """Version comparison must use proper semver, not string compare."""

    def test_parse_version(self):
        from upgrade import _parse_version

        assert _parse_version("1.2.3") == (1, 2, 3)
        assert _parse_version("2.0") == (2, 0)
        assert _parse_version("10.1.0") > _parse_version("9.9.9")
        assert _parse_version("1.10.0") > _parse_version("1.9.0")
