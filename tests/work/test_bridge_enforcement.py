"""
tests/work/test_bridge_enforcement.py — Bridge artifact enforcement tests.

Implements §9.8 (Enforcement & Test Matrix):
  - Bridge artifacts with prohibited fields MUST be rejected on write
  - Bridge artifacts with unknown fields MUST be rejected
  - Valid artifacts MUST be accepted
  - Schema version mismatches MUST produce clear errors
  - Cross-surface access: work commands reading personal state MUST FAIL
  - Personal commands reading work state MUST FAIL

Run: pytest tests/work/test_bridge_enforcement.py -v
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the schema module under test
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from schemas.bridge_schemas import (  # type: ignore
    BridgeValidationError,
    ProhibitedFieldError,
    validate_bridge_artifact,
    write_bridge_artifact,
    read_bridge_artifact,
    make_schedule_mask,
    make_work_load_pulse,
    PROHIBITED_FIELDS,
    WORK_LOAD_PULSE_PHASES,
    sanitize_advisory,
    validate_alert_isolation,
)


# ===========================================================================
# §9.8 Test Group 1: Prohibited field rejection
# "Bridge artifact with prohibited field → MUST REJECT on write"
# ===========================================================================

class TestProhibitedFieldRejection:

    def test_schedule_mask_rejects_title(self):
        artifact = make_schedule_mask("2026-03-24", [])
        artifact["title"] = "Dentist appointment"  # PROHIBITED
        with pytest.raises(ProhibitedFieldError, match="title"):
            validate_bridge_artifact("personal_schedule_mask", artifact)

    def test_schedule_mask_rejects_attendees(self):
        artifact = make_schedule_mask("2026-03-24", [])
        artifact["attendees"] = ["Alice", "Bob"]  # PROHIBITED
        with pytest.raises(ProhibitedFieldError, match="attendees"):
            validate_bridge_artifact("personal_schedule_mask", artifact)

    def test_schedule_mask_rejects_notes(self):
        artifact = make_schedule_mask("2026-03-24", [])
        artifact["notes"] = "Personal medical notes"  # PROHIBITED
        with pytest.raises(ProhibitedFieldError, match="notes"):
            validate_bridge_artifact("personal_schedule_mask", artifact)

    def test_work_pulse_rejects_meeting_names(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 0, 0.8, 0.6)
        artifact["meeting_names"] = ["Architecture Review"]  # PROHIBITED
        with pytest.raises(ProhibitedFieldError, match="meeting_names"):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_work_pulse_rejects_people(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 0, 0.8, 0.6)
        artifact["people"] = ["Alice"]  # PROHIBITED
        with pytest.raises(ProhibitedFieldError):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_work_pulse_rejects_projects(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 0, 0.8, 0.6)
        artifact["projects"] = ["Project Falcon"]  # PROHIBITED
        with pytest.raises(ProhibitedFieldError):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_work_pulse_rejects_messages(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 0, 0.8, 0.6)
        artifact["messages"] = ["Teams message body here"]  # PROHIBITED
        with pytest.raises(ProhibitedFieldError):
            validate_bridge_artifact("work_load_pulse", artifact)

    @pytest.mark.parametrize("field", sorted(PROHIBITED_FIELDS))
    def test_all_prohibited_fields_rejected_in_pulse(self, field):
        """Every prohibited field must be rejected in work_load_pulse."""
        artifact = make_work_load_pulse("2026-03-24", 0, 0, 1.0, 1.0)
        artifact[field] = "injected value"
        with pytest.raises(ProhibitedFieldError):
            validate_bridge_artifact("work_load_pulse", artifact)


# ===========================================================================
# §9.8 Test Group 2: Unknown field rejection
# "Bridge artifact with unknown field → MUST REJECT on write"
# ===========================================================================

class TestUnknownFieldRejection:

    def test_schedule_mask_rejects_unexpected_field(self):
        artifact = make_schedule_mask("2026-03-24", [{"busy_start": "09:00", "busy_end": "10:00", "type": "hard"}])
        artifact["organizer"] = "SomeCalendarProp"  # Not in schema
        with pytest.raises(BridgeValidationError):
            validate_bridge_artifact("personal_schedule_mask", artifact)

    def test_work_pulse_rejects_unexpected_field(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 2, 0.7, 0.4)
        artifact["unknown_field"] = "extra data"
        with pytest.raises(BridgeValidationError):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_schedule_block_rejects_extra_field(self):
        artifact = make_schedule_mask("2026-03-24", [
            {"busy_start": "09:00", "busy_end": "10:00", "type": "hard", "location": "Office"}
        ])
        with pytest.raises(BridgeValidationError):
            validate_bridge_artifact("personal_schedule_mask", artifact)


# ===========================================================================
# §9.8 Test Group 3: Valid artifact acceptance
# ===========================================================================

class TestValidArtifactAcceptance:

    def test_valid_empty_schedule_mask(self):
        artifact = make_schedule_mask("2026-03-24", [])
        validate_bridge_artifact("personal_schedule_mask", artifact)  # must not raise

    def test_valid_schedule_mask_with_blocks(self):
        artifact = make_schedule_mask("2026-03-24", [
            {"busy_start": "09:00", "busy_end": "10:00", "type": "hard"},
            {"busy_start": "14:00", "busy_end": "15:30", "type": "soft"},
        ])
        validate_bridge_artifact("personal_schedule_mask", artifact)

    def test_valid_work_load_pulse(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 3, 0.6, 0.35)
        validate_bridge_artifact("work_load_pulse", artifact)

    def test_valid_pulse_boundary_values(self):
        # boundary_score and focus_availability_score can be exactly 0.0 and 1.0
        artifact = make_work_load_pulse("2026-03-24", 0.0, 0, 0.0, 0.0)
        validate_bridge_artifact("work_load_pulse", artifact)
        artifact2 = make_work_load_pulse("2026-03-24", 24.0, 99, 1.0, 1.0)
        validate_bridge_artifact("work_load_pulse", artifact2)


# ===========================================================================
# §9.8 Test Group 4: Schema version enforcement
# ===========================================================================

class TestSchemaVersionEnforcement:

    def test_wrong_schema_key_rejected(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 2, 0.7, 0.4)
        artifact["$schema"] = "artha/bridge/work_load_pulse/v99"  # wrong version
        with pytest.raises(BridgeValidationError, match="must equal"):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_unknown_artifact_type_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown bridge artifact type"):
            validate_bridge_artifact("unknown_type", {})


# ===========================================================================
# §9.8 Test Group 5: Atomic write semantics
# ===========================================================================

class TestAtomicWrite:

    def test_valid_write_creates_file(self, tmp_path):
        target = tmp_path / "work_load_pulse.json"
        artifact = make_work_load_pulse("2026-03-24", 5.2, 2, 0.7, 0.4)
        write_bridge_artifact(target, "work_load_pulse", artifact)
        assert target.exists()
        data = json.loads(target.read_text())
        assert data["total_meeting_hours"] == 5.2

    def test_invalid_write_preserves_existing_file(self, tmp_path):
        target = tmp_path / "work_load_pulse.json"
        # Write a valid file first
        valid = make_work_load_pulse("2026-03-24", 3.0, 1, 0.8, 0.5)
        write_bridge_artifact(target, "work_load_pulse", valid)

        # Attempt to overwrite with invalid artifact — previous must be preserved
        bad = make_work_load_pulse("2026-03-24", 0, 0, 1.0, 1.0)
        bad["people"] = ["Alice"]  # prohibited
        with pytest.raises(ProhibitedFieldError):
            write_bridge_artifact(target, "work_load_pulse", bad)

        # Original file must be intact
        data = json.loads(target.read_text())
        assert data["total_meeting_hours"] == 3.0
        assert "people" not in data

    def test_no_tmp_file_left_behind_after_failure(self, tmp_path):
        target = tmp_path / "work_load_pulse.json"
        bad = make_work_load_pulse("2026-03-24", 0, 0, 1.0, 1.0)
        bad["title"] = "forbidden"  # prohibited
        try:
            write_bridge_artifact(target, "work_load_pulse", bad)
        except ProhibitedFieldError:
            pass
        # Temp file must be cleaned up
        assert not target.with_suffix(".json.tmp").exists()

    def test_read_missing_file_returns_empty(self, tmp_path):
        result = read_bridge_artifact(tmp_path / "missing.json", "work_load_pulse")
        assert result == {}

    def test_read_valid_file_returns_dict(self, tmp_path):
        target = tmp_path / "pulse.json"
        artifact = make_work_load_pulse("2026-03-24", 4.0, 1, 0.75, 0.5)
        write_bridge_artifact(target, "work_load_pulse", artifact)
        result = read_bridge_artifact(target, "work_load_pulse")
        assert result["boundary_score"] == 0.75


# ===========================================================================
# §9.8 Test Group 6: Range / type validation
# ===========================================================================

class TestRangeValidation:

    def test_boundary_score_above_max_rejected(self):
        # Bypass the factory's clamping; inject the out-of-range value directly.
        artifact = make_work_load_pulse("2026-03-24", 1.0, 0, 1.0, 0.5)
        artifact["boundary_score"] = 1.5  # invalid — exceeds maximum
        with pytest.raises(BridgeValidationError, match="exceeds maximum"):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_boundary_score_below_min_rejected(self):
        # Bypass the factory's clamping; inject the out-of-range value directly.
        artifact = make_work_load_pulse("2026-03-24", 1.0, 0, 0.0, 0.5)
        artifact["boundary_score"] = -0.1  # invalid — below minimum
        with pytest.raises(BridgeValidationError, match="below minimum"):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_meeting_hours_negative_rejected(self):
        artifact = make_work_load_pulse("2026-03-24", -1.0, 0, 0.8, 0.5)
        with pytest.raises(BridgeValidationError, match="below minimum"):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_wrong_type_for_total_meeting_hours(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 2, 0.8, 0.5)
        artifact["total_meeting_hours"] = "five point two"  # wrong type: string
        with pytest.raises(BridgeValidationError, match="wrong type"):
            validate_bridge_artifact("work_load_pulse", artifact)

    def test_block_type_enum_enforced(self):
        artifact = make_schedule_mask("2026-03-24", [
            {"busy_start": "09:00", "busy_end": "10:00", "type": "definite"}  # not in enum
        ])
        with pytest.raises(BridgeValidationError, match="must be one of"):
            validate_bridge_artifact("personal_schedule_mask", artifact)


# ===========================================================================
# §9.8 Test Group 7: Cross-surface access assertions
# "Work command reads personal state → MUST FAIL" (path-level assertion)
# "Personal command reads work state → MUST FAIL" (path-level assertion)
#
# These are path-boundary tests — they verify the *policy contract*, which
# will be enforced by the command router in 0D.1. For Phase 0, they assert
# that the path-check helper behaves correctly for both surfaces.
# ===========================================================================

from pathlib import Path as _Path

def _is_personal_state(path: _Path) -> bool:
    """True if path is inside state/ (personal) but NOT state/work/ or state/bridge/."""
    state_root = _Path("state").resolve()
    work_root  = _Path("state/work").resolve()
    bridge_root = _Path("state/bridge").resolve()
    try:
        resolved = path.resolve()
        in_state = resolved.is_relative_to(state_root)
        in_work  = resolved.is_relative_to(work_root)
        in_bridge = resolved.is_relative_to(bridge_root)
        return in_state and not in_work and not in_bridge
    except Exception:
        return False


def _is_work_state(path: _Path) -> bool:
    """True if path is inside state/work/ (work surface, excluding bridge/)."""
    work_root = _Path("state/work").resolve()
    try:
        return path.resolve().is_relative_to(work_root)
    except Exception:
        return False


class TestCrossSurfaceAccess:

    def test_work_command_cannot_access_personal_state(self):
        """Work commands must not access personal state files (§9.4, §9.5)."""
        personal_files = [
            _Path("state/calendar.md"),
            _Path("state/health.md.age"),
            _Path("state/finance.md.age"),
            _Path("state/goals.md"),
            _Path("state/open_items.md"),
        ]
        for path in personal_files:
            assert _is_personal_state(path), \
                f"{path} should be classified as personal state"
            # A work command should check this and refuse
            assert not _is_work_state(path), \
                f"Work command must not access personal state: {path}"

    def test_personal_command_cannot_access_work_state(self):
        """Personal commands must not access work state files (§9.4, §9.5)."""
        work_files = [
            _Path("state/work/work-calendar.md"),
            _Path("state/work/work-comms.md"),
            _Path("state/work/work-people.md"),
            _Path("state/work/work-career.md"),
        ]
        for path in work_files:
            assert _is_work_state(path), \
                f"{path} should be classified as work state"
            assert not _is_personal_state(path), \
                f"Personal command must not access work state: {path}"

    def test_bridge_schedule_mask_is_personal_to_work_only(self):
        """personal_schedule_mask.json is readable by work commands, not personal."""
        mask_path = _Path("state/bridge/personal_schedule_mask.json")
        # Not personal state (excluded from personal read surface)
        assert not _is_personal_state(mask_path)
        # Not work domain state
        assert not _is_work_state(mask_path)

    def test_bridge_work_pulse_is_work_to_personal_only(self):
        """work_load_pulse.json is readable by personal commands, not work."""
        pulse_path = _Path("state/bridge/work_load_pulse.json")
        # Not personal state
        assert not _is_personal_state(pulse_path)
        # Not work domain state
        assert not _is_work_state(pulse_path)

    def test_work_audit_does_not_mix_with_personal_audit(self):
        """Work audit entries must go to state/work/work-audit.md, not state/audit.md."""
        personal_audit = _Path("state/audit.md")
        work_audit = _Path("state/work/work-audit.md")
        assert _is_personal_state(personal_audit)
        assert _is_work_state(work_audit)
        assert personal_audit != work_audit


# ===========================================================================
# §9.6 Alert Isolation — validate_alert_isolation()
# "Work alerts and personal alerts must never co-mingle"
# ===========================================================================


class TestAlertIsolation:

    # ── work→personal direction ───────────────────────────────────────────

    def test_valid_work_pulse_passes_isolation_check(self):
        artifact = make_work_load_pulse("2026-03-24", 5.2, 2, 0.75, 0.50)
        validate_alert_isolation(artifact, "work_to_personal")  # must not raise

    def test_work_pulse_zero_values_passes(self):
        artifact = make_work_load_pulse("2026-03-24", 0.0, 0, 0.0, 0.0)
        validate_alert_isolation(artifact, "work_to_personal")  # must not raise

    def test_work_to_personal_rejects_extra_string_field(self):
        """Any string field beyond $schema/generated_at/date is a content leak."""
        artifact = make_work_load_pulse("2026-03-24", 3.0, 1, 0.8, 0.5)
        artifact["alert_message"] = "🔴 3 blocked items in sprint"
        with pytest.raises(BridgeValidationError, match="isolation violation"):
            validate_alert_isolation(artifact, "work_to_personal")

    def test_work_to_personal_rejects_embedded_list(self):
        """Embedded lists could carry meeting names or alert titles."""
        artifact = make_work_load_pulse("2026-03-24", 3.0, 1, 0.8, 0.5)
        artifact["urgent_items"] = ["Ship v2", "Fix bug #1234"]
        with pytest.raises((BridgeValidationError, Exception)):
            validate_alert_isolation(artifact, "work_to_personal")

    def test_work_to_personal_rejects_embedded_dict(self):
        """Embedded dicts could carry structured alert content."""
        artifact = make_work_load_pulse("2026-03-24", 3.0, 1, 0.8, 0.5)
        artifact["top_alert"] = {"type": "blocked", "item": "Deploy to prod blocked on cert"}
        with pytest.raises((BridgeValidationError, Exception)):
            validate_alert_isolation(artifact, "work_to_personal")

    def test_work_to_personal_rejects_prohibited_field(self):
        """Prohibited fields must still be caught by the underlying schema validator."""
        artifact = make_work_load_pulse("2026-03-24", 3.0, 1, 0.8, 0.5)
        artifact["people"] = ["Alice", "Bob"]
        with pytest.raises(Exception):  # ProhibitedFieldError or BridgeValidationError
            validate_alert_isolation(artifact, "work_to_personal")

    # ── personal→work direction ───────────────────────────────────────────

    def test_valid_schedule_mask_passes_isolation_check(self):
        artifact = make_schedule_mask("2026-03-24", [
            {"busy_start": "09:00", "busy_end": "10:30", "type": "hard"},
            {"busy_start": "14:00", "busy_end": "15:00", "type": "soft"},
        ])
        validate_alert_isolation(artifact, "personal_to_work")  # must not raise

    def test_personal_to_work_rejects_extra_field(self):
        """personal→work artifact must not carry any extra metadata."""
        artifact = make_schedule_mask("2026-03-24", [])
        artifact["mood"] = "stressed"  # leaks personal signal
        with pytest.raises(BridgeValidationError):
            validate_alert_isolation(artifact, "personal_to_work")

    def test_personal_to_work_rejects_prohibited_field(self):
        artifact = make_schedule_mask("2026-03-24", [])
        artifact["title"] = "Dentist appointment"  # personal event title
        with pytest.raises(Exception):
            validate_alert_isolation(artifact, "personal_to_work")

    # ── unknown surface raises ────────────────────────────────────────────

    def test_unknown_surface_raises_value_error(self):
        artifact = make_work_load_pulse("2026-03-24", 1.0, 0, 0.8, 0.5)
        with pytest.raises(ValueError, match="Unknown surface"):
            validate_alert_isolation(artifact, "both_ways")


# ===========================================================================
# Bridge v1.1 — phase/advisory optional fields (§8.7 v2.3.0)
# ===========================================================================

class TestBridgeV11:
    """Bridge schema v1.1: optional phase + advisory fields pass validation."""

    def test_phase_valid_value_accepted(self):
        artifact = make_work_load_pulse(
            "2026-03-24", 5.0, 2, 0.7, 0.4, phase="sprint_deadline"
        )
        validate_bridge_artifact("work_load_pulse", artifact)  # must not raise

    def test_phase_invalid_value_raises(self):
        with pytest.raises(Exception):
            make_work_load_pulse(
                "2026-03-24", 5.0, 2, 0.7, 0.4, phase="unknown_phase_xyz"
            )

    def test_phase_frozenset_has_expected_values(self):
        assert "normal" in WORK_LOAD_PULSE_PHASES
        assert "sprint_deadline" in WORK_LOAD_PULSE_PHASES
        assert "connect_submission" in WORK_LOAD_PULSE_PHASES

    def test_artifact_without_phase_still_valid(self):
        """Backward compat — existing v1.0 artifacts must still validate."""
        artifact = make_work_load_pulse("2026-03-24", 3.0, 1, 0.8, 0.5)
        assert "phase" not in artifact
        validate_bridge_artifact("work_load_pulse", artifact)  # must not raise

    def test_advisory_pii_email_stripped(self):
        result = sanitize_advisory("Sprint deadline: contact alice@company.com ASAP")
        assert "@" not in result

    def test_advisory_pii_full_name_stripped(self):
        result = sanitize_advisory("Reviewed by John Smith for Q2 review")
        assert "John Smith" not in result

    def test_advisory_truncated_at_100(self):
        long_text = "X" * 200
        result = sanitize_advisory(long_text)
        assert len(result) <= 103  # 100 + "..."

    def test_phase_and_advisory_pass_isolation_check(self):
        artifact = make_work_load_pulse(
            "2026-03-24", 4.0, 1, 0.75, 0.5,
            phase="normal",
            advisory="Sprint load is elevated this week",
        )
        validate_alert_isolation(artifact, "work_to_personal")  # must not raise
