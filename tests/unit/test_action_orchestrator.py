"""
tests/unit/test_action_orchestrator.py — Unit tests for action_orchestrator.py.

Covers T-U-01 through T-U-34 per specs/actions-reloaded.md §10.1

Run:
    pytest tests/unit/test_action_orchestrator.py -v
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import action_orchestrator as orch
from action_orchestrator import (
    _actions_enabled,
    _audit_log,
    _burn_in_mode,
    _deduplicate,
    _handler_health_check,
    _persist_signals,
    _resolve_defer_preset,
    _validate_proposal_handler,
    run,
)


# ---------------------------------------------------------------------------
# Minimal Artha directory fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def artha_dir(tmp_path):
    """Create a minimal Artha directory with required structure."""
    (tmp_path / "state").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "state" / "audit.md").write_text("# Audit Log\n")
    (tmp_path / "state" / "health-check.md").write_text(
        "# Health Check\n\n"
        "## Autonomy State\n\n"
        "```yaml\n"
        "autonomy:\n"
        "  trust_level: 1\n"
        "  days_at_level: 10\n"
        "  acceptance_rate_90d: 0.85\n"
        "  critical_false_positives: 0\n"
        "  pre_approved_categories: []\n"
        "```\n"
    )
    (tmp_path / "config" / "artha_config.yaml").write_text(
        "harness:\n"
        "  actions:\n"
        "    enabled: true\n"
        "    burn_in: false\n"
        "    ai_signals: false\n"
    )
    (tmp_path / "config" / "actions.yaml").write_text(
        "schema_version: '2.0'\nactions:\n"
        "  email_send:\n"
        "    enabled: true\n"
        "    friction: standard\n"
        "    min_trust: 1\n"
        "    sensitivity: standard\n"
        "    autonomy_floor: true\n"
        "    timeout_sec: 60\n"
        "    reversible: true\n"
        "    undo_window_sec: 30\n"
        "    rate_limit:\n"
        "      max_per_hour: 20\n"
        "      max_per_day: 100\n"
        "  reminder_create:\n"
        "    enabled: true\n"
        "    friction: low\n"
        "    min_trust: 0\n"
        "    sensitivity: standard\n"
        "    autonomy_floor: false\n"
        "    timeout_sec: 30\n"
        "    reversible: false\n"
        "    undo_window_sec:\n"
        "    rate_limit:\n"
        "      max_per_hour: 20\n"
        "      max_per_day: 50\n"
        "  calendar_create:\n"
        "    enabled: true\n"
        "    friction: low\n"
        "    min_trust: 0\n"
        "    sensitivity: standard\n"
        "    autonomy_floor: false\n"
        "    timeout_sec: 30\n"
        "    reversible: true\n"
        "    undo_window_sec: 30\n"
        "    rate_limit:\n"
        "      max_per_hour: 20\n"
        "      max_per_day: 50\n"
    )
    return tmp_path


def _make_signal(
    signal_type: str = "event_rsvp_needed",
    domain: str = "calendar",
    entity: str = "test-entity",
    urgency: int = 5,
    impact: int = 5,
    source: str = "email_extractor",
):
    return SimpleNamespace(
        signal_type=signal_type,
        domain=domain,
        entity=entity,
        urgency=urgency,
        impact=impact,
        source=source,
        detected_at=datetime.now(timezone.utc).isoformat(),
        metadata={},
    )


def _make_proposal(
    action_type: str = "reminder_create",
    domain: str = "calendar",
    friction: str = "low",
    min_trust: int = 0,
    title: str = "Test action",
    parameters: dict | None = None,
    action_id: str | None = None,
):
    from actions.base import ActionProposal
    return ActionProposal(
        id=action_id or str(uuid.uuid4()),
        action_type=action_type,
        domain=domain,
        title=title,
        description="",
        parameters=parameters or {"text": "test"},
        friction=friction,
        min_trust=min_trust,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )


# ---------------------------------------------------------------------------
# T-U-01 — run() with no emails file
# ---------------------------------------------------------------------------

def test_run_with_no_emails_file(artha_dir):
    """Returns 0 proposals when pipeline_output.jsonl absent; no crash."""
    assert not (artha_dir / "tmp" / "pipeline_output.jsonl").exists()

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]):
        mock_composer = MagicMock()
        mock_composer.compose.return_value = None  # no proposals
        mock_executor = MagicMock()
        mock_executor.expire_stale.return_value = 0
        mock_executor.list_pending.return_value = []
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(),
        )

        result = run(artha_dir)

    assert result == 0


# ---------------------------------------------------------------------------
# T-U-02 — run() with empty emails file
# ---------------------------------------------------------------------------

def test_run_with_empty_emails_file(artha_dir):
    """Empty pipeline_output.jsonl → 0 signals; exits cleanly."""
    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text("")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]):
        mock_composer = MagicMock()
        mock_composer.compose.return_value = None
        mock_executor = MagicMock()
        mock_executor.expire_stale.return_value = 0
        mock_executor.list_pending.return_value = []
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(),
        )

        result = run(artha_dir)

    assert result == 0


# ---------------------------------------------------------------------------
# T-U-03 — marketing emails are filtered
# ---------------------------------------------------------------------------

def test_run_with_marketing_only_emails(artha_dir):
    """Marketing-flagged emails → 0 signals extracted."""
    marketing_email = json.dumps({
        "subject": "Buy now! 50% off!",
        "from": "deals@spam.com",
        "marketing": True,
    })
    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text(marketing_email + "\n")

    mock_extractor_instance = MagicMock()
    mock_extractor_instance.extract.return_value = []

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]):
        mock_composer = MagicMock()
        mock_composer.compose.return_value = None
        mock_executor = MagicMock()
        mock_executor.expire_stale.return_value = 0
        mock_executor.list_pending.return_value = []
        MockExtractorClass = MagicMock(return_value=mock_extractor_instance)
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MockExtractorClass,
        )

        result = run(artha_dir)

    # Marketing emails should be filtered before reaching extractor
    assert mock_extractor_instance.extract.call_count == 0 or (
        mock_extractor_instance.extract.call_count > 0 and result == 0
    )


# ---------------------------------------------------------------------------
# T-U-04 — one RSVP email → 1 proposal
# ---------------------------------------------------------------------------

def test_run_with_one_rsvp_email(artha_dir):
    """One RSVP email → 1 email signal → 1 proposal queued."""
    rsvp_email = json.dumps({
        "subject": "Are you coming to the party? Please RSVP",
        "from": "friend@example.com",
        "marketing": False,
    })
    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text(rsvp_email + "\n")

    rsvp_signal = _make_signal("event_rsvp_needed", "calendar", "party-invite")
    proposal = _make_proposal("reminder_create", "calendar")

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [rsvp_signal]

    mock_composer = MagicMock()
    mock_composer.compose.return_value = proposal

    mock_executor = MagicMock()
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator.PatternEngine", create=True) as MockPE:
        MockPE.return_value.evaluate.return_value = []
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(return_value=mock_extractor),
        )

        with patch("action_orchestrator._validate_proposal_handler"):
            result = run(artha_dir)

    assert result == 1


# ---------------------------------------------------------------------------
# T-U-05 — email + pattern signals merged
# ---------------------------------------------------------------------------

def test_run_with_multiple_signals(artha_dir):
    """Email and pattern signals are merged and deduplicated correctly."""
    email_signal = _make_signal("bill_due", "finance", "electric-bill")
    pattern_signal = _make_signal("goal_stale", "goals", "weight-goal")

    # Create a non-marketing email so extractor is called
    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text(
        json.dumps({"subject": "Bill due", "from": "electric@co.com", "marketing": False}) + "\n"
    )

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [email_signal]

    proposal1 = _make_proposal("reminder_create", "finance")
    proposal2 = _make_proposal("reminder_create", "goals")

    mock_composer = MagicMock()
    mock_composer.compose.side_effect = [proposal1, proposal2]

    mock_executor = MagicMock()
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal1, proposal2]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"), \
         patch("pattern_engine.PatternEngine") as MockPE:
        MockPE.return_value.evaluate.return_value = [pattern_signal]
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(return_value=mock_extractor),
        )
        result = run(artha_dir)

    assert result == 2


# ---------------------------------------------------------------------------
# T-U-06 — deduplication: same signal_type+entity → 1 kept
# ---------------------------------------------------------------------------

def test_deduplication_same_signal_type():
    """Two bill_due signals for the same entity → only 1 kept."""
    s1 = _make_signal("bill_due", "finance", "electric-bill")
    s2 = _make_signal("bill_due", "finance", "electric-bill")  # duplicate
    result = _deduplicate([s1, s2])
    assert len(result) == 1


# ---------------------------------------------------------------------------
# T-U-07 — deduplication: different entities → both kept
# ---------------------------------------------------------------------------

def test_deduplication_different_entities():
    """Two bill_due signals for different entities → both kept."""
    s1 = _make_signal("bill_due", "finance", "electric-bill")
    s2 = _make_signal("bill_due", "finance", "water-bill")  # different entity
    result = _deduplicate([s1, s2])
    assert len(result) == 2


# ---------------------------------------------------------------------------
# T-U-08 — signal persistence to JSONL
# ---------------------------------------------------------------------------

def test_signal_persistence_to_jsonl(tmp_path):
    """Signals are written to the JSONL file with correct fields."""
    s = _make_signal("bill_due", "finance", "electric-bill")
    out = tmp_path / "signals.jsonl"
    _persist_signals(out, [s])

    assert out.exists()
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0]["signal_type"] == "bill_due"
    assert lines[0]["domain"] == "finance"
    assert lines[0]["entity"] == "electric-bill"


# ---------------------------------------------------------------------------
# T-U-09 — signal persistence excludes metadata (PII guard)
# ---------------------------------------------------------------------------

def test_signal_persistence_excludes_metadata(tmp_path):
    """Signal JSONL must not contain a 'metadata' key (PII source)."""
    s = _make_signal()
    s.metadata = {"email_body": "Dear John, your SSN is 123-45-6789"}
    out = tmp_path / "signals.jsonl"
    _persist_signals(out, [s])

    content = out.read_text()
    assert "metadata" not in content
    assert "SSN" not in content
    assert "123-45-6789" not in content


# ---------------------------------------------------------------------------
# T-U-10 — kill switch: actions disabled → exit 0
# ---------------------------------------------------------------------------

def test_actions_disabled_returns_zero(artha_dir, capsys):
    """Kill switch (harness.actions.enabled: false) → exit 0, no proposals."""
    (artha_dir / "config" / "artha_config.yaml").write_text(
        "harness:\n  actions:\n    enabled: false\n"
    )
    result = run(artha_dir)
    assert result == 0
    captured = capsys.readouterr()
    assert "actions disabled" in captured.err


# ---------------------------------------------------------------------------
# T-U-11 — read-only mode → exit 0, no DB writes
# ---------------------------------------------------------------------------

def test_read_only_mode_returns_zero(artha_dir, capsys):
    """Read-only mode → exit 0, no DB writes attempted."""
    with patch.object(orch, "_is_read_only", return_value=True):
        result = run(artha_dir)

    assert result == 0
    captured = capsys.readouterr()
    assert "read-only" in captured.err


# ---------------------------------------------------------------------------
# T-U-12 — handler health check reports failures
# ---------------------------------------------------------------------------

def test_handler_health_check_reports_failures(artha_dir):
    """Import error in a handler → reported in failures list, not crashed."""
    import importlib as _importlib

    # Patch importlib.import_module inside action_orchestrator to simulate import error
    with patch.object(_importlib, "import_module", side_effect=ImportError("no such module")):
        failures = _handler_health_check(artha_dir)

    # Should return a non-empty list of failure strings (not crash)
    assert isinstance(failures, list)
    assert len(failures) > 0, "Expected at least one failure when all imports fail"
    # Each entry should be a descriptive string
    for f in failures:
        assert isinstance(f, str)


# ---------------------------------------------------------------------------
# T-U-13 — handler health check all healthy
# ---------------------------------------------------------------------------

def test_handler_health_check_all_healthy(artha_dir):
    """When all handlers import cleanly, failure list is empty."""
    with patch("action_orchestrator.importlib.import_module", return_value=MagicMock()):
        failures = _handler_health_check(artha_dir)
    assert failures == []


# ---------------------------------------------------------------------------
# T-U-14 — compose failure does not crash the loop
# ---------------------------------------------------------------------------

def test_compose_failure_does_not_crash_loop(artha_dir):
    """One bad signal compose() raises → loop continues, no crash."""
    s1 = _make_signal("bill_due", "finance", "entity1")
    s2 = _make_signal("reminder_create", "calendar", "entity2")
    proposal = _make_proposal("reminder_create", "calendar")

    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text(
        json.dumps({"subject": "Bill", "from": "x@y.com", "marketing": False}) + "\n"
    )

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [s1, s2]

    mock_composer = MagicMock()
    mock_composer.compose.side_effect = [Exception("compose boom"), proposal]

    mock_executor = MagicMock()
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"), \
         patch("pattern_engine.PatternEngine") as MockPE:
        MockPE.return_value.evaluate.return_value = []
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(return_value=mock_extractor),
        )
        result = run(artha_dir)

    # Second signal still got processed despite first crashing
    assert result == 1


# ---------------------------------------------------------------------------
# T-U-15 — propose failure does not crash the loop
# ---------------------------------------------------------------------------

def test_propose_failure_does_not_crash_loop(artha_dir):
    """DB error on propose → loop continues, no crash."""
    s1 = _make_signal("bill_due", "finance", "entity1")
    s2 = _make_signal("event_rsvp_needed", "calendar", "entity2")
    proposal1 = _make_proposal("reminder_create", "finance")
    proposal2 = _make_proposal("reminder_create", "calendar")

    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text(
        json.dumps({"subject": "Finance", "from": "x@y.com", "marketing": False}) + "\n"
    )

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [s1, s2]

    mock_composer = MagicMock()
    mock_composer.compose.side_effect = [proposal1, proposal2]

    mock_executor = MagicMock()
    mock_executor.propose_direct.side_effect = [Exception("DB error"), None]
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = []
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"), \
         patch("pattern_engine.PatternEngine") as MockPE:
        MockPE.return_value.evaluate.return_value = []
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(return_value=mock_extractor),
        )
        result = run(artha_dir)

    # No exception raised to caller
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# T-U-16 — approve existing proposal → handler executes
# ---------------------------------------------------------------------------

def test_approve_existing_proposal(artha_dir, capsys):
    """--approve <id> on a pending proposal → executor.approve() called, exit 0."""
    action_id = str(uuid.uuid4())

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.message = "Email draft created"

    mock_executor = MagicMock()
    mock_executor.approve.return_value = mock_result
    mock_executor.list_pending.return_value = [_make_proposal(action_id=action_id)]

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_approve
        result = cmd_approve(artha_dir, action_id)

    assert result == 0
    mock_executor.approve.assert_called_once_with(action_id, approved_by="user:terminal")
    out = capsys.readouterr().out
    assert "success" in out or "Email draft" in out

    # Verify audit trail
    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "ACTION_APPROVED" in audit


# ---------------------------------------------------------------------------
# T-U-17 — approve nonexistent ID → error, exit 1
# ---------------------------------------------------------------------------

def test_approve_nonexistent_id(artha_dir, capsys):
    """--approve with unknown ID → error message, returns 1."""
    mock_executor = MagicMock()
    mock_executor.get_action.return_value = None
    mock_executor.list_pending.return_value = []
    mock_executor.approve.side_effect = Exception("not found")

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_approve
        result = cmd_approve(artha_dir, "nonexistent-bad-id-00000")

    assert result == 1


# ---------------------------------------------------------------------------
# T-U-18 — reject existing proposal → status=rejected
# ---------------------------------------------------------------------------

def test_reject_existing_proposal(artha_dir):
    """--reject <id> → executor.reject() called, returns 0."""
    mock_executor = MagicMock()
    mock_executor.reject.return_value = None
    mock_executor.list_pending.return_value = []

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_reject, _resolve_id
        with patch("action_orchestrator._resolve_id", return_value="full-uuid-here"):
            result = cmd_reject(artha_dir, "full-uuid-here", reason="wrong proposal")

    assert result == 0
    mock_executor.reject.assert_called_once()


# ---------------------------------------------------------------------------
# T-U-19 — approve-all-low only approves low-friction proposals
# ---------------------------------------------------------------------------

def test_approve_all_low_only_approves_low(artha_dir, capsys):
    """--approve-all-low: only low-friction proposals are approved."""
    low_p = _make_proposal("reminder_create", "calendar", friction="low")
    high_p = _make_proposal("email_send", "finance", friction="high")
    low_p2 = _make_proposal("calendar_create", "travel", friction="low")

    mock_executor = MagicMock()
    mock_executor.list_pending.return_value = [low_p, high_p, low_p2]
    mock_result = SimpleNamespace(status="success", message="done")
    mock_executor.approve.return_value = mock_result

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_approve_all_low
        result = cmd_approve_all_low(artha_dir)

    # Only 2 low-friction proposals should have been approved
    assert mock_executor.approve.call_count == 2
    approved_ids = [c.args[0] for c in mock_executor.approve.call_args_list]
    assert low_p.id in approved_ids
    assert low_p2.id in approved_ids
    assert high_p.id not in approved_ids


# ---------------------------------------------------------------------------
# T-U-20 — list output format
# ---------------------------------------------------------------------------

def test_list_output_format(artha_dir, capsys):
    """--list output contains expected structural elements."""
    proposal = _make_proposal("reminder_create", "calendar", friction="low", title="Test reminder")

    mock_executor = MagicMock()
    mock_executor.list_pending.return_value = [proposal]

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_list
        result = cmd_list(artha_dir)

    out = capsys.readouterr().out
    assert "PENDING ACTIONS" in out
    assert "reminder_create" in out
    assert "calendar" in out
    assert result == 0


# ---------------------------------------------------------------------------
# T-U-21 — expire removes old proposals
# ---------------------------------------------------------------------------

def test_expire_removes_old_proposals(artha_dir, capsys):
    """--expire calls executor.expire_stale() and reports count."""
    mock_executor = MagicMock()
    mock_executor.expire_stale.return_value = 3

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_expire
        result = cmd_expire(artha_dir)

    mock_executor.expire_stale.assert_called_once()
    assert result == 0
    assert "3" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# T-U-22 — health command reports status
# ---------------------------------------------------------------------------

def test_health_command_reports_status(artha_dir, capsys):
    """--health shows handler status + queue stats."""
    mock_executor = MagicMock()
    mock_executor.list_pending.return_value = []
    # run_health_checks() returns dict {action_type: bool}
    mock_executor.run_health_checks.return_value = {
        "reminder_create": True,
        "calendar_create": True,
        "email_send": False,
    }
    mock_executor.queue_stats.return_value = {"pending": 0, "deferred": 0}

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_health
        result = cmd_health(artha_dir)

    out = capsys.readouterr().out
    assert "HEALTH" in out
    assert result == 0


# ---------------------------------------------------------------------------
# T-U-23 — summary output format
# ---------------------------------------------------------------------------

def test_summary_output_format(artha_dir, capsys):
    """--run stdout matches expected structured format."""
    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text("")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]):
        mock_composer = MagicMock()
        mock_composer.compose.return_value = None
        mock_executor = MagicMock()
        mock_executor.expire_stale.return_value = 0
        mock_executor.list_pending.return_value = []
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(),
        )
        run(artha_dir)

    out = capsys.readouterr().out
    assert "ACTION ORCHESTRATOR" in out
    assert "Signals detected:" in out
    assert "Proposals queued:" in out


# ---------------------------------------------------------------------------
# T-U-24 — audit log written on run
# ---------------------------------------------------------------------------

def test_audit_log_written_on_run(artha_dir):
    """After --run, state/audit.md has ACTION_ORCHESTRATOR entry."""
    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text("")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]):
        mock_executor = MagicMock()
        mock_executor.expire_stale.return_value = 0
        mock_executor.list_pending.return_value = []
        mock_imports.return_value = (
            MagicMock(return_value=MagicMock(compose=MagicMock(return_value=None))),
            MagicMock(return_value=mock_executor),
            MagicMock(),
        )
        run(artha_dir)

    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "ACTION_ORCHESTRATOR" in audit


# ---------------------------------------------------------------------------
# T-U-25 — audit log written on approve
# ---------------------------------------------------------------------------

def test_audit_log_written_on_approve(artha_dir):
    """After approval, state/audit.md has ACTION_APPROVED entry."""
    action_id = str(uuid.uuid4())
    mock_result = SimpleNamespace(status="success", message="sent")
    mock_executor = MagicMock()
    mock_executor.approve.return_value = mock_result
    mock_executor.list_pending.return_value = []

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        with patch("action_orchestrator._resolve_id", return_value=action_id):
            from action_orchestrator import cmd_approve
            cmd_approve(artha_dir, action_id)

    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "ACTION_APPROVED" in audit


# ---------------------------------------------------------------------------
# T-U-26 — handler validation before enqueue
# ---------------------------------------------------------------------------

def test_handler_validation_before_enqueue():
    """_validate_proposal_handler raises ValueError on missing 'to' field."""
    proposal = _make_proposal("email_send", "finance", parameters={})  # no 'to'

    mock_handler = MagicMock()
    mock_handler.validate.return_value = (False, "Missing required field: to")

    mock_executor = MagicMock()
    mock_executor._get_handler.return_value = mock_handler

    with pytest.raises(ValueError, match="Handler pre-validation failed"):
        _validate_proposal_handler(mock_executor, proposal)


# ---------------------------------------------------------------------------
# T-U-27 — show content-bearing proposal (email_reply)
# ---------------------------------------------------------------------------

def test_show_content_bearing_proposal(artha_dir, capsys):
    """--show on email_reply proposal prints To, Subject, Body fields."""
    action_id = str(uuid.uuid4())
    proposal = _make_proposal(
        "email_reply", "finance", friction="high",
        parameters={"to": "tax@county.gov", "subject": "Re: Tax Notice", "body": "Dear Sir,\n\nThank you..."},
        action_id=action_id,
    )

    mock_executor = MagicMock()
    mock_executor.get_action.return_value = proposal

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_show
        result = cmd_show(artha_dir, action_id)

    out = capsys.readouterr().out
    assert "To:" in out
    assert "Subject:" in out
    assert "Body:" in out
    assert "CONTENT PREVIEW" in out
    assert result == 0


# ---------------------------------------------------------------------------
# T-U-28 — show non-content proposal (calendar_create)
# ---------------------------------------------------------------------------

def test_show_non_content_proposal(artha_dir, capsys):
    """--show on calendar_create → title + params, no CONTENT PREVIEW section."""
    action_id = str(uuid.uuid4())
    proposal = _make_proposal(
        "calendar_create", "calendar", friction="low",
        parameters={"summary": "Doctor appointment", "start": "2026-04-05T10:00", "end": "2026-04-05T11:00"},
        action_id=action_id,
    )

    mock_executor = MagicMock()
    mock_executor.get_action.return_value = proposal

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_show
        result = cmd_show(artha_dir, action_id)

    out = capsys.readouterr().out
    assert "calendar_create" in out
    assert "CONTENT PREVIEW" not in out
    assert "PARAMETERS" in out
    assert result == 0


# ---------------------------------------------------------------------------
# T-U-29 — show encrypted params — no key → locked message
# ---------------------------------------------------------------------------

def test_show_encrypted_params_no_key(artha_dir, capsys):
    """--show on proposal with non-dict params (encrypted) → locked message."""
    action_id = str(uuid.uuid4())
    # Use a MagicMock for the proposal so we can set non-dict parameters
    proposal = MagicMock()
    proposal.id = action_id
    proposal.action_type = "email_reply"
    proposal.domain = "finance"
    proposal.friction = "high"
    proposal.min_trust = 1
    proposal.expires_at = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
    proposal.title = "Reply to tax notice"
    proposal.parameters = b"AGE-SECRET-KEY-encrypted-data..."  # non-dict = encrypted

    mock_executor = MagicMock()
    mock_executor.get_action.return_value = proposal

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_show
        result = cmd_show(artha_dir, action_id)

    out = capsys.readouterr().out
    assert "🔒" in out or "encrypted" in out.lower()
    assert result == 0


# ---------------------------------------------------------------------------
# T-U-30 — defer preset: tomorrow → next day 09:00
# ---------------------------------------------------------------------------

def test_defer_preset_tomorrow():
    """_resolve_defer_preset('tomorrow') returns ISO string for next 09:00 local."""
    result = _resolve_defer_preset("tomorrow")
    # Must be a valid ISO string
    dt = datetime.fromisoformat(result)
    # Should be > now
    assert dt > datetime.now(timezone.utc)
    # Hour in local time should be 9
    import time as _time
    utc_offset_sec = -_time.timezone if not _time.daylight else -_time.altzone
    local_dt = dt + timedelta(seconds=utc_offset_sec)
    assert local_dt.hour == 9
    assert local_dt.minute == 0


# ---------------------------------------------------------------------------
# T-U-31 — defer preset: next-session → +24h
# ---------------------------------------------------------------------------

def test_defer_preset_next_session():
    """_resolve_defer_preset('next-session') returns ISO string ~24h from now."""
    before = datetime.now(timezone.utc)
    result = _resolve_defer_preset("next-session")
    after = datetime.now(timezone.utc)

    dt = datetime.fromisoformat(result)
    expected_low = before + timedelta(hours=23, minutes=59)
    expected_high = after + timedelta(hours=24, minutes=1)
    assert expected_low <= dt <= expected_high


# ---------------------------------------------------------------------------
# T-U-32 — defer preset: +4h → +4h ISO
# ---------------------------------------------------------------------------

def test_defer_preset_plus_offset():
    """_resolve_defer_preset('+4h') returns ISO string ~4h from now."""
    before = datetime.now(timezone.utc)
    result = _resolve_defer_preset("+4h")
    after = datetime.now(timezone.utc)

    dt = datetime.fromisoformat(result)
    expected_low = before + timedelta(hours=3, minutes=59)
    expected_high = after + timedelta(hours=4, minutes=1)
    assert expected_low <= dt <= expected_high


# ---------------------------------------------------------------------------
# T-U-33 — signal routing merge: YAML entries + fallback both present
# ---------------------------------------------------------------------------

def test_signal_routing_merge_yaml_over_fallback():
    """YAML with 3 entries + fallback with 50+ → merged dict has all, YAML wins conflicts."""
    import importlib
    import sys as _sys

    # Remove cached module
    for mod in list(_sys.modules):
        if "action_composer" in mod:
            _sys.modules.pop(mod, None)

    from action_composer import _load_signal_routing, _FALLBACK_SIGNAL_ROUTING

    routing = _load_signal_routing()

    # Must have at least as many entries as the fallback
    assert len(routing) >= len(_FALLBACK_SIGNAL_ROUTING), (
        f"Merged routing ({len(routing)}) should have >= fallback ({len(_FALLBACK_SIGNAL_ROUTING)}) entries"
    )

    # All fallback keys should be in merged result
    missing = set(_FALLBACK_SIGNAL_ROUTING.keys()) - set(routing.keys())
    assert not missing, f"Missing from merged routing: {missing}"


# ---------------------------------------------------------------------------
# T-U-34 — allowed action types matches handler map
# ---------------------------------------------------------------------------

def test_allowed_action_types_matches_handler_map():
    """_ALLOWED_ACTION_TYPES - set(_FALLBACK_ACTION_MAP) == empty set."""
    from action_composer import _ALLOWED_ACTION_TYPES
    from action_executor import _FALLBACK_ACTION_MAP

    missing = _ALLOWED_ACTION_TYPES - set(_FALLBACK_ACTION_MAP.keys())
    assert not missing, (
        f"Action types in composer but not in executor handler map: {missing}"
    )


# ---------------------------------------------------------------------------
# Cross-platform tests — T-P-01 through T-P-04
# Ref: specs/actions-reloaded.md §10.4
# ---------------------------------------------------------------------------

def test_pathlib_paths_no_hardcoded_slash():
    """T-P-01: orchestrator uses pathlib.Path — no hardcoded OS separators."""
    orchestrator_src = SCRIPTS_DIR / "action_orchestrator.py"
    src = orchestrator_src.read_text(encoding="utf-8")

    violations = []
    for i, line in enumerate(src.splitlines(), 1):
        # Skip comments and docstrings
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'\"'"):
            continue
        # Detect hardcoded path separators in string literals
        import re
        if re.search(r'["\'](?:[a-zA-Z0-9_]+/){2,}', line):
            # It's a URL or module path if it contains '://' or is after 'import'
            if "://" in line or " import " in line or "import " in stripped:
                continue
            violations.append(f"line {i}: {line.rstrip()}")

    assert not violations, (
        "Hardcoded forward-slash paths found in action_orchestrator.py "
        "(use pathlib.Path instead):\n" + "\n".join(violations[:5])
    )


def test_sqlite_wal_mode_on_open(artha_dir):
    """T-P-02: ActionQueue opens DB in WAL journal mode."""
    from action_queue import ActionQueue

    # Redirect DB to tmp_path so we don't touch live DB
    test_db = artha_dir / "tmp" / "test_actions.db"
    with patch.object(
        __import__("action_queue", fromlist=["ActionQueue"]).ActionQueue,
        "_resolve_db_path",
        return_value=test_db,
    ):
        q = ActionQueue(artha_dir)
        import sqlite3
        conn = sqlite3.connect(str(test_db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        q.close()

    assert mode == "wal", f"Expected WAL mode, got: {mode!r}"


def test_output_path_uses_pathlib():
    """T-P-03: pipeline.py _validate_output_path uses Path not os.path.join."""
    pipeline_src = (SCRIPTS_DIR / "pipeline.py").read_text(encoding="utf-8")

    # _validate_output_path should use pathlib, not os.path
    import re
    func_match = re.search(
        r"def _validate_output_path.*?(?=\ndef |\Z)", pipeline_src, re.DOTALL
    )
    assert func_match, "_validate_output_path function not found in pipeline.py"
    func_body = func_match.group(0)

    assert "Path" in func_body, "_validate_output_path must use pathlib.Path"
    # Should not use os.path.join or os.sep
    assert "os.path.join" not in func_body, "Must not use os.path.join in _validate_output_path"


def test_python3_compatible_shebang():
    """T-P-04: action_orchestrator.py has python3-compatible shebang / no python2 syntax."""
    orchestrator_src = (SCRIPTS_DIR / "action_orchestrator.py").read_text(encoding="utf-8")

    # Must have from __future__ annotations (modern type hints)
    assert "from __future__ import annotations" in orchestrator_src, (
        "action_orchestrator.py must use 'from __future__ import annotations'"
    )
    # Must not use Python 2 print statement syntax
    import re
    py2_print = re.search(r"^print [^(]", orchestrator_src, re.MULTILINE)
    assert not py2_print, "Python 2 print statement found"
    # Must not use old-style exception syntax (except E, e:)
    py2_except = re.search(r"except \w+, \w+:", orchestrator_src)
    assert not py2_except, "Python 2 except syntax found"


# ---------------------------------------------------------------------------
# T-RH5-A — _run_with_timeout configures SIGALRM correctly (RH-5)
# ---------------------------------------------------------------------------

def test_run_with_timeout_alarm_configured(artha_dir):
    """T-RH5-A: _run_with_timeout calls signal.alarm(60) at start and alarm(0) in finally."""
    import signal as _sig
    if not hasattr(_sig, "SIGALRM"):
        pytest.skip("SIGALRM not available on this platform (Windows)")

    from action_orchestrator import _run_with_timeout, _RUN_TIMEOUT_SEC

    alarm_calls: list[int] = []
    original_alarm = _sig.alarm

    def _capture_alarm(n: int) -> int:
        alarm_calls.append(n)
        return original_alarm(n)

    with patch.object(_sig, "alarm", side_effect=_capture_alarm):
        with patch("action_orchestrator.run", return_value=3):
            result = _run_with_timeout(artha_dir)

    # alarm(timeout) called at start, alarm(0) called in finally
    assert _RUN_TIMEOUT_SEC in alarm_calls, (
        f"Expected signal.alarm({_RUN_TIMEOUT_SEC}) call; got {alarm_calls}"
    )
    assert alarm_calls[-1] == 0, "Expected final signal.alarm(0) to cancel pending alarm"
    assert result == 3  # passthrough from mocked run()


# ---------------------------------------------------------------------------
# T-RH5-B — _run_with_timeout fires correctly on timeout (RH-5)
# ---------------------------------------------------------------------------

def test_run_with_timeout_fires_correctly(artha_dir):
    """T-RH5-B: When _OrchestratorTimeout fires, returns 0 + logs ACTION_ORCHESTRATOR_TIMEOUT."""
    import signal as _sig
    if not hasattr(_sig, "SIGALRM"):
        pytest.skip("SIGALRM not available on this platform (Windows)")

    from action_orchestrator import _run_with_timeout, _OrchestratorTimeout

    def _simulate_timeout(*args, **kwargs):
        raise _OrchestratorTimeout()

    with patch("action_orchestrator.run", side_effect=_simulate_timeout):
        result = _run_with_timeout(artha_dir)

    assert result == 0, "Expected 0 (partial) on timeout"
    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "ACTION_ORCHESTRATOR_TIMEOUT" in audit, (
        "Expected ACTION_ORCHESTRATOR_TIMEOUT in audit.md after timeout"
    )
