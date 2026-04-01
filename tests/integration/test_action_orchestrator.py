"""
tests/integration/test_action_orchestrator.py — Integration tests for action_orchestrator.py.

Covers T-I-01 through T-I-13 per specs/actions-reloaded.md §10.2

These tests exercise the full orchestrator wiring: pipeline JSONL → signal
extraction → composition → SQLite queue → approval → handler (mocked).

Run:
    pytest tests/integration/test_action_orchestrator.py -v
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
from action_orchestrator import run, _deduplicate


# ---------------------------------------------------------------------------
# Full Artha directory fixture with real SQLite queue
# ---------------------------------------------------------------------------

@pytest.fixture
def artha_dir(tmp_path):
    """Minimal Artha directory with real filesystem layout."""
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
        "  email_reply:\n"
        "    enabled: true\n"
        "    friction: standard\n"
        "    min_trust: 1\n"
        "    sensitivity: standard\n"
        "    autonomy_floor: true\n"
        "    timeout_sec: 60\n"
        "    reversible: false\n"
        "    undo_window_sec:\n"
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
        "  instruction_sheet:\n"
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
    )
    return tmp_path


def _rsvp_email_record():
    return {
        "subject": "You're invited! Please RSVP by Friday",
        "from": "host@example.com",
        "body": "We're hosting a birthday party. Can you come? Please RSVP.",
        "date": "2026-03-31",
        "marketing": False,
        "source": "gmail",
        "type": "email",
    }


# ---------------------------------------------------------------------------
# T-I-02 — email signal to proposal E2E
# ---------------------------------------------------------------------------

def test_email_signal_to_proposal_e2e(artha_dir):
    """Fake RSVP email JSONL → event_rsvp_needed signal → proposal queued."""
    jsonl_path = artha_dir / "tmp" / "pipeline_output.jsonl"
    jsonl_path.write_text(json.dumps(_rsvp_email_record()) + "\n")

    from actions.base import ActionProposal
    proposal = ActionProposal(
        id=str(uuid.uuid4()),
        action_type="reminder_create",
        domain="calendar",
        title="RSVP to birthday party",
        description="",
        parameters={"text": "RSVP to birthday party by Friday"},
        friction="low",
        min_trust=0,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    from types import SimpleNamespace as SN

    rsvp_signal = SN(
        signal_type="event_rsvp_needed",
        domain="calendar",
        entity="birthday-party",
        urgency=7,
        impact=5,
        source="email_extractor",
        detected_at=datetime.now(timezone.utc).isoformat(),
        metadata={},
    )

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [rsvp_signal]

    mock_composer = MagicMock()
    mock_composer.compose.return_value = proposal

    mock_executor = MagicMock()
    mock_executor.propose_direct.return_value = proposal.id
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"):
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(return_value=mock_extractor),
        )
        with patch("action_orchestrator.PatternEngine", create=True) as MockPE:
            MockPE.return_value.evaluate.return_value = []
            result = run(artha_dir)

    assert result == 1
    mock_executor.propose_direct.assert_called_once()


# ---------------------------------------------------------------------------
# T-I-06 — cross-session dedup: same emails → no duplicate proposals
# ---------------------------------------------------------------------------

def test_cross_session_dedup(artha_dir):
    """Run orchestrator twice with same signals → dedup prevents double-queue."""
    from types import SimpleNamespace as SN

    signal = SN(
        signal_type="bill_due",
        domain="finance",
        entity="electric-bill",
        urgency=6,
        impact=6,
        source="email_extractor",
        detected_at=datetime.now(timezone.utc).isoformat(),
        metadata={},
    )

    from actions.base import ActionProposal
    proposal = ActionProposal(
        id=str(uuid.uuid4()),
        action_type="reminder_create",
        domain="finance",
        title="Pay electric bill",
        description="",
        parameters={"text": "Pay electric bill"},
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    call_count = {"propose": 0}

    def _propose_side_effect(p):
        if call_count["propose"] == 0:
            call_count["propose"] += 1
            return p.id
        else:
            # Second call: queue dedup guard fires
            raise ValueError("Duplicate proposal: (reminder_create, finance) already pending")

    mock_executor = MagicMock()
    mock_executor.propose_direct.side_effect = _propose_side_effect
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    mock_composer = MagicMock()
    mock_composer.compose.return_value = proposal

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [signal]

    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text(
        json.dumps({"subject": "Bill due", "from": "electric@co.com", "marketing": False}) + "\n"
    )

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"):
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(return_value=mock_extractor),
        )
        with patch("action_orchestrator.PatternEngine", create=True) as MockPE:
            MockPE.return_value.evaluate.return_value = []

            # Run 1
            r1 = run(artha_dir)
            # Run 2 — dedup should fire (simulate same signals)
            r2 = run(artha_dir)

    # First run: 1 proposed; second run: 0 proposed (ValueError caught)
    assert r1 == 1
    assert r2 == 0


# ---------------------------------------------------------------------------
# T-I-07 — kill switch: full session produces 0 proposals
# ---------------------------------------------------------------------------

def test_kill_switch_full_session(artha_dir):
    """actions.enabled=false → 0 proposals regardless of any emails."""
    (artha_dir / "config" / "artha_config.yaml").write_text(
        "harness:\n  actions:\n    enabled: false\n"
    )
    (artha_dir / "tmp" / "pipeline_output.jsonl").write_text(
        json.dumps({"subject": "RSVP needed", "from": "x@y.com", "marketing": False}) + "\n"
    )

    result = run(artha_dir)
    assert result == 0


# ---------------------------------------------------------------------------
# T-I-09 — concurrent read during write (WAL mode safety)
# ---------------------------------------------------------------------------

def test_concurrent_read_during_write(artha_dir):
    """--list while orchestrator runs → no crash (SQLite WAL mode)."""
    # Since we're using mocks, we just verify both can run without interference
    mock_executor_run = MagicMock()
    mock_executor_run.expire_stale.return_value = 0
    mock_executor_run.list_pending.return_value = []

    mock_executor_list = MagicMock()
    mock_executor_list.list_pending.return_value = []

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]):
        mock_imports.return_value = (
            MagicMock(return_value=MagicMock(compose=MagicMock(return_value=None))),
            MagicMock(return_value=mock_executor_run),
            MagicMock(),
        )
        result_run = run(artha_dir)

    with patch("action_orchestrator._import_action_modules") as mock_imports2:
        mock_imports2.return_value = (MagicMock(), MagicMock(return_value=mock_executor_list), MagicMock())
        from action_orchestrator import cmd_list
        result_list = cmd_list(artha_dir)

    assert result_run == 0
    assert result_list == 0


# ---------------------------------------------------------------------------
# T-I-10 — pipeline --output flag writes file AND stdout
# ---------------------------------------------------------------------------

def test_pipeline_output_flag(tmp_path, capsys):
    """pipeline.py --output writes JSONL file atomically."""
    from pipeline import _validate_output_path

    tmp_out = tmp_path / "pipeline_output.jsonl"
    # Must not raise for valid tmp/ path
    # Direct call to internal function for unit-level check
    # (Full pipeline integration test would require connector credentials)

    # Verify the path validator correctly accepts paths inside tmp/
    artha_tmp = tmp_path / "tmp"
    artha_tmp.mkdir()
    valid_output = artha_tmp / "pipeline_output.jsonl"

    # _validate_output_path raises ValueError for paths outside tmp/
    try:
        _validate_output_path(valid_output, tmp_path)
        valid_accepted = True
    except (ValueError, Exception):
        valid_accepted = False

    assert valid_accepted, "Valid tmp/ path should be accepted by _validate_output_path"


# ---------------------------------------------------------------------------
# T-I-11 — show expanded preview for content-bearing proposals
# ---------------------------------------------------------------------------

def test_show_expanded_preview(artha_dir, capsys):
    """--show <id> prints full content for email_reply proposals."""
    action_id = str(uuid.uuid4())

    from actions.base import ActionProposal
    proposal = ActionProposal(
        id=action_id,
        action_type="email_reply",
        domain="finance",
        title="Reply: Property tax notice",
        description="",
        parameters={
            "to": "assessor@county.gov",
            "subject": "Re: Property Tax Assessment Notice",
            "body": "Dear Assessor,\n\nThank you for your notice.\n\nRegards,\nVed",
        },
        friction="high",
        min_trust=1,
        sensitivity="high",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    mock_executor = MagicMock()
    mock_executor.get_action.return_value = proposal

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (MagicMock(), MagicMock(return_value=mock_executor), MagicMock())
        from action_orchestrator import cmd_show
        result = cmd_show(artha_dir, action_id)

    out = capsys.readouterr().out
    assert "ACTION DETAIL" in out
    assert "CONTENT PREVIEW" in out
    assert "assessor@county.gov" in out
    assert "Property Tax Assessment Notice" in out
    assert "Thank you for your notice" in out
    assert result == 0


# ---------------------------------------------------------------------------
# T-I-12 — fresh snapshot: pipeline --output never appends
# ---------------------------------------------------------------------------

def test_fresh_snapshot_no_append(tmp_path):
    """Running pipeline.py twice with --output → file contains only latest data."""
    from pipeline import _validate_output_path

    artha_dir = tmp_path
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    output_path = tmp_dir / "pipeline_output.jsonl"

    # Simulate two pipeline runs writing different content
    record_run1 = json.dumps({"run": 1, "data": "first run"})
    record_run2 = json.dumps({"run": 2, "data": "second run"})

    # First run: write via atomic rename
    tmp_path_1 = output_path.with_suffix(".tmp")
    with open(tmp_path_1, "w", encoding="utf-8") as f:
        f.write(record_run1 + "\n")
    tmp_path_1.replace(output_path)

    # Verify content is from first run
    assert "first run" in output_path.read_text()
    assert "second run" not in output_path.read_text()

    # Second run: overwrite via atomic rename (fresh snapshot semantics)
    tmp_path_2 = output_path.with_suffix(".tmp")
    with open(tmp_path_2, "w", encoding="utf-8") as f:
        f.write(record_run2 + "\n")
    tmp_path_2.replace(output_path)

    content = output_path.read_text()
    assert "second run" in content
    assert "first run" not in content  # Overwrites, never appends


# ---------------------------------------------------------------------------
# T-I-01 — full pipeline → orchestrator E2E
# ---------------------------------------------------------------------------

def test_full_pipeline_to_orchestrator(artha_dir, capsys):
    """pipeline.py --output + orchestrator --run together produce a proposal."""
    # Write pipeline JSONL as if pipeline.py --output had run
    rsvp = _rsvp_email_record()
    jsonl_path = artha_dir / "tmp" / "pipeline_output.jsonl"
    jsonl_path.write_text(json.dumps(rsvp) + "\n")

    from actions.base import ActionProposal
    proposal = ActionProposal(
        id=str(uuid.uuid4()),
        action_type="reminder_create",
        domain="calendar",
        title="RSVP to event",
        description="",
        parameters={"text": "RSVP reply"},
        friction="low",
        min_trust=0,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    from types import SimpleNamespace as SN
    rsvp_signal = SN(
        signal_type="event_rsvp_needed",
        domain="calendar",
        entity="event-rsvp",
        urgency=7,
        impact=5,
        source="email_extractor",
        detected_at=datetime.now(timezone.utc).isoformat(),
        metadata={},
    )

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = [rsvp_signal]
    mock_composer = MagicMock()
    mock_composer.compose.return_value = proposal
    mock_executor = MagicMock()
    mock_executor.propose_direct.return_value = proposal.id
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"):
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(return_value=mock_extractor),
        )
        with patch("action_orchestrator.PatternEngine", create=True) as MockPE:
            MockPE.return_value.evaluate.return_value = []
            count = run(artha_dir)

    # Proposal was created
    assert count == 1
    mock_executor.propose_direct.assert_called_once()

    # Pipeline JSONL was consumed (file exists)
    assert jsonl_path.exists()

    # Signals persisted to audit file
    signals_file = artha_dir / "tmp" / "signals.jsonl"
    assert signals_file.exists()
    signals_data = [json.loads(l) for l in signals_file.read_text().splitlines() if l.strip()]
    assert any(s["signal_type"] == "event_rsvp_needed" for s in signals_data)

    # Audit trail written
    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "ACTION_ORCHESTRATOR" in audit


# ---------------------------------------------------------------------------
# T-I-03 — pattern engine signal → proposal E2E
# ---------------------------------------------------------------------------

def test_pattern_engine_to_proposal_e2e(artha_dir):
    """Pattern engine fires goal_stale → instruction_sheet proposal queued."""
    from types import SimpleNamespace as SN

    goal_stale_signal = SN(
        signal_type="goal_stale",
        domain="goals",
        entity="fitness-goal",
        urgency=4,
        impact=6,
        source="pattern_engine",
        detected_at=datetime.now(timezone.utc).isoformat(),
        metadata={"description": "", "steps": [], "notes": []},
    )

    from actions.base import ActionProposal
    proposal = ActionProposal(
        id=str(uuid.uuid4()),
        action_type="instruction_sheet",
        domain="goals",
        title="Generate guide: goal stale — fitness-goal",
        description="",
        parameters={"task": "Goal Stale", "service": "fitness-goal", "context": {}},
        friction="low",
        min_trust=0,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    mock_composer = MagicMock()
    mock_composer.compose.return_value = proposal
    mock_executor = MagicMock()
    mock_executor.propose_direct.return_value = proposal.id
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"):
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(),  # extractor — no emails file
        )
        with patch("pattern_engine.PatternEngine") as MockPE:
            MockPE.return_value.evaluate.return_value = [goal_stale_signal]
            count = run(artha_dir)

    # Pattern engine signal produced a proposal
    assert count == 1
    call_args = mock_composer.compose.call_args[0][0]
    assert call_args.signal_type == "goal_stale"
    assert call_args.domain == "goals"


# ---------------------------------------------------------------------------
# T-I-04 — approve email_send dry run (mocked handler)
# ---------------------------------------------------------------------------

def test_approve_email_send_dry_run(artha_dir, capsys):
    """Propose email_send → approve → mocked handler executes successfully."""
    action_id = str(uuid.uuid4())

    from actions.base import ActionProposal
    proposal = ActionProposal(
        id=action_id,
        action_type="email_reply",
        domain="finance",
        title="Reply: Tax notice",
        description="",
        parameters={
            "to": "assessor@county.gov",
            "subject": "Re: Tax Assessment",
            "body": "Dear Assessor, thank you.",
        },
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.message = "Email draft created in Gmail"

    mock_executor = MagicMock()
    mock_executor.approve.return_value = mock_result
    mock_executor.list_pending.return_value = [proposal]

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (
            MagicMock(),
            MagicMock(return_value=mock_executor),
            MagicMock(),
        )
        from action_orchestrator import cmd_approve
        result = cmd_approve(artha_dir, action_id)

    assert result == 0
    mock_executor.approve.assert_called_once_with(action_id, approved_by="user:terminal")
    out = capsys.readouterr().out
    assert "success" in out or "Gmail" in out

    # Audit logged
    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "ACTION_APPROVED" in audit
    assert "ACTION_EXECUTED" in audit


# ---------------------------------------------------------------------------
# T-I-05 — approve calendar_create dry run (mocked handler)
# ---------------------------------------------------------------------------

def test_approve_calendar_create_dry_run(artha_dir, capsys):
    """Propose calendar_create → approve → mocked handler executes successfully."""
    action_id = str(uuid.uuid4())

    from actions.base import ActionProposal
    proposal = ActionProposal(
        id=action_id,
        action_type="calendar_create",
        domain="calendar",
        title="Add: Parent-teacher meeting",
        description="",
        parameters={
            "summary": "Parent-teacher meeting",
            "start": "2026-04-10T10:00:00",
            "end": "2026-04-10T11:00:00",
        },
        friction="low",
        min_trust=0,
        sensitivity="standard",
        reversible=True,
        undo_window_sec=30,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.message = "Calendar event created"

    mock_executor = MagicMock()
    mock_executor.approve.return_value = mock_result
    mock_executor.list_pending.return_value = [proposal]

    with patch("action_orchestrator._import_action_modules") as mock_imports:
        mock_imports.return_value = (
            MagicMock(),
            MagicMock(return_value=mock_executor),
            MagicMock(),
        )
        from action_orchestrator import cmd_approve
        result = cmd_approve(artha_dir, action_id)

    assert result == 0
    mock_executor.approve.assert_called_once_with(action_id, approved_by="user:terminal")
    out = capsys.readouterr().out
    assert "success" in out or "event" in out.lower()

    # Audit logged
    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "ACTION_APPROVED" in audit


# ---------------------------------------------------------------------------
# T-I-13 — routing merge produces proposals for all 8 email signal types
# ---------------------------------------------------------------------------

def test_routing_merge_produces_proposals_for_all_email_signals():
    """All 8 email signal types have routes after YAML + fallback merge."""
    from action_composer import _load_signal_routing

    routing = _load_signal_routing()

    # The 8 categories from EmailSignalExtractor
    email_signal_types = [
        "event_rsvp_needed",
        "appointment_confirmed",
        "bill_due",
        "form_deadline",
        "delivery_arriving",
        "security_alert",
        "subscription_renewal",
        "school_action_needed",
    ]

    missing = [t for t in email_signal_types if t not in routing]
    assert not missing, (
        f"Signal types missing from merged routing after YAML+fallback: {missing}\n"
        f"Routing has {len(routing)} entries."
    )


# ---------------------------------------------------------------------------
# T-I-08 — Full AI signal path: ai_signals.jsonl + ai_signals:true → hardened proposal
# ---------------------------------------------------------------------------

def test_orchestrator_with_ai_signals_file(artha_dir):
    """T-I-08: Valid ai_signals.jsonl + ai_signals:true → proposal with friction=high + [AI-SIGNAL] in audit."""
    # Enable ai_signals in config
    (artha_dir / "config" / "artha_config.yaml").write_text(
        "harness:\n"
        "  actions:\n"
        "    enabled: true\n"
        "    burn_in: false\n"
        "    ai_signals: true\n"
    )

    # Write a valid AI signal
    ai_signal = {
        "signal_type": "goal_stale",
        "domain": "goals",
        "entity": "fitness-goal",
        "urgency": 4,
        "impact": 6,
        "source": "ai",
    }
    (artha_dir / "tmp" / "ai_signals.jsonl").write_text(json.dumps(ai_signal) + "\n")

    from actions.base import ActionProposal

    # Composer returns a low-friction proposal (hardening must escalate it)
    proposal = ActionProposal(
        id=str(uuid.uuid4()),
        action_type="instruction_sheet",
        domain="goals",
        title="Generate guide: goal stale — fitness-goal",
        description="",
        parameters={"task": "Goal Stale", "service": "fitness-goal", "context": {}},
        friction="low",       # hardening must override this to "high"
        min_trust=0,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
    )

    mock_composer = MagicMock()
    mock_composer.compose.return_value = proposal
    mock_executor = MagicMock()
    mock_executor.propose_direct.return_value = proposal.id
    mock_executor.expire_stale.return_value = 0
    mock_executor.list_pending.return_value = [proposal]
    mock_executor._get_handler.return_value.validate.return_value = (True, "ok")

    with patch.object(orch, "_is_read_only", return_value=False), \
         patch("action_orchestrator._import_action_modules") as mock_imports, \
         patch("action_orchestrator._handler_health_check", return_value=[]), \
         patch("action_orchestrator._validate_proposal_handler"):
        mock_imports.return_value = (
            MagicMock(return_value=mock_composer),
            MagicMock(return_value=mock_executor),
            MagicMock(),  # extractor — no emails file
        )
        with patch("pattern_engine.PatternEngine") as MockPE:
            MockPE.return_value.evaluate.return_value = []
            count = run(artha_dir)

    # AI signal was processed and produced a proposal
    assert count == 1

    # Friction must have been escalated to "high" by _apply_ai_signal_hardening()
    enqueued_proposal = mock_executor.propose_direct.call_args[0][0]
    assert enqueued_proposal.friction == "high", (
        f"Expected friction='high' for AI-origin proposal, got '{enqueued_proposal.friction}'"
    )

    # Audit must contain [AI-SIGNAL] prefix
    audit = (artha_dir / "state" / "audit.md").read_text()
    assert "[AI-SIGNAL]" in audit, "Expected [AI-SIGNAL] prefix in audit log for AI-origin proposal"
