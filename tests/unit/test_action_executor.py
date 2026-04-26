"""
tests/unit/test_action_executor.py — Integration tests for ActionExecutor.

Tests the core execution engine: propose, approve, reject, defer, undo,
history, health checks, and PII firewall integration.

Ref: specs/act.md §5
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from action_executor import ActionExecutor
from actions.base import ActionProposal, ActionResult
from action_queue import ActionQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def artha_dir(tmp_path):
    """Create a minimal Artha directory structure for executor testing."""
    (tmp_path / "state").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "state" / "audit.md").write_text("# Audit Log\n")

    # Minimal health-check.md with autonomy block
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

    # Minimal actions.yaml — dict-keyed format matching real config/actions.yaml
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
        "      max_per_hour: 10\n"
        "      max_per_day: 20\n"
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
    )

    return tmp_path


@pytest.fixture
def executor(artha_dir):
    return ActionExecutor(artha_dir)


def _make_proposal(
    action_type: str = "email_send",
    domain: str = "test",
    title: str = "Test action",
    sensitivity: str = "standard",
    min_trust: int = 1,
    parameters: dict | None = None,
) -> ActionProposal:
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type=action_type,
        domain=domain,
        title=title,
        description="Test",
        parameters=parameters or {"to": "test@example.com", "subject": "Hi", "body": "Hello"},
        friction="standard",
        min_trust=min_trust,
        sensitivity=sensitivity,
        reversible=True,
        undo_window_sec=30,
        expires_at=None,
        source_step="12.5",
        source_skill="test",
        linked_oi=None,
    )


# ---------------------------------------------------------------------------
# Propose tests
# ---------------------------------------------------------------------------

class TestPropose:
    def test_propose_creates_pending(self, executor):
        with patch.object(executor, "_get_handler") as mock_handler:
            mock_h = MagicMock()
            mock_h.validate.return_value = (True, "")
            mock_handler.return_value = mock_h

            proposal = executor.propose(
                action_type="email_send",
                domain="test",
                title="Send test email",
                parameters={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
            )

        assert proposal.action_type == "email_send"
        assert proposal.id is not None
        # Verify it's in the queue
        pending = executor.pending()
        ids = [p.id for p in pending]
        assert proposal.id in ids

    def test_propose_validation_failure_raises(self, executor):
        with patch.object(executor, "_get_handler") as mock_handler:
            mock_h = MagicMock()
            mock_h.validate.return_value = (False, "Missing required field 'to'")
            mock_handler.return_value = mock_h

            with pytest.raises(ValueError, match="Handler validation failed"):
                executor.propose(
                    action_type="email_send",
                    domain="test",
                    title="Bad action",
                    parameters={"subject": "Hi"},  # missing 'to'
                )

    def test_propose_requires_non_empty_title(self, executor):
        with pytest.raises(ValueError):
            executor.propose(
                action_type="email_send",
                domain="test",
                title="",
                parameters={},
            )


# ---------------------------------------------------------------------------
# Approve + execute tests
# ---------------------------------------------------------------------------

class TestApprove:
    def test_approve_calls_handler_execute(self, executor):
        with patch.object(executor, "_get_handler") as mock_get:
            mock_h = MagicMock()
            mock_h.validate.return_value = (True, "")
            mock_h.execute.return_value = ActionResult(
                status="success",
                message="Email sent",
                data={"message_id": "msg123"},
                reversible=True,
                reverse_action=None,
            )
            mock_get.return_value = mock_h

            proposal = executor.propose(
                action_type="email_send",
                domain="test",
                title="Send email",
                parameters={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
            )

        # Reset mock after propose
        with patch.object(executor, "_get_handler") as mock_get2:
            mock_h2 = MagicMock()
            mock_h2.execute.return_value = ActionResult(
                status="success",
                message="Email sent",
                data={"message_id": "msg123"},
                reversible=True,
                reverse_action=None,
            )
            mock_get2.return_value = mock_h2

            result = executor.approve(proposal.id, approved_by="user:test")

        assert result.status == "success"
        assert "sent" in result.message.lower()

    def test_approve_unknown_id_returns_failure(self, executor):
        result = executor.approve("nonexistent-id", approved_by="user:test")
        assert result.status == "failure"
        assert "not found" in result.message.lower()

    def test_approve_autonomy_floor_auto_blocked(self, executor):
        """autonomy_floor=True actions must block auto: approvers."""
        with patch.object(executor, "_get_handler") as mock_get:
            mock_h = MagicMock()
            mock_h.validate.return_value = (True, "")
            mock_get.return_value = mock_h

            proposal = executor.propose(
                action_type="email_send",
                domain="test",
                title="Auto email",
                parameters={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
            )

        result = executor.approve(proposal.id, approved_by="auto:L2")
        # auto: approver should be blocked for autonomy_floor actions
        assert result.status == "failure"
        assert "autonomy" in result.message.lower() or "floor" in result.message.lower()


# ---------------------------------------------------------------------------
# Reject & Defer tests
# ---------------------------------------------------------------------------

class TestRejectDefer:
    def test_reject_moves_to_rejected(self, executor):
        with patch.object(executor, "_get_handler") as mock_get:
            mock_h = MagicMock()
            mock_h.validate.return_value = (True, "")
            mock_get.return_value = mock_h

            proposal = executor.propose(
                action_type="reminder_create",
                domain="test",
                title="Create reminder",
                parameters={"title": "Doctor appointment"},
            )

        executor.reject(proposal.id, reason="not needed")
        row = executor._queue.get_raw(proposal.id)
        assert row["status"] == "rejected"

    def test_reject_records_feedback_reason(self, executor):
        with patch.object(executor, "_get_handler") as mock_get:
            mock_h = MagicMock()
            mock_h.validate.return_value = (True, "")
            mock_get.return_value = mock_h

            proposal = executor.propose(
                action_type="reminder_create",
                domain="test",
                title="Create reminder",
                parameters={"title": "Doctor appointment"},
            )

        executor.reject(proposal.id, reason="not useful")
        row = executor._queue._conn.execute(  # noqa: SLF001 - test-only DB inspection
            "SELECT feedback FROM trust_metrics ORDER BY proposed_at DESC LIMIT 1"
        ).fetchone()
        assert row["feedback"] == "not useful"

    def test_defer_moves_to_deferred(self, executor):
        with patch.object(executor, "_get_handler") as mock_get:
            mock_h = MagicMock()
            mock_h.validate.return_value = (True, "")
            mock_get.return_value = mock_h

            proposal = executor.propose(
                action_type="reminder_create",
                domain="test",
                title="Create reminder",
                parameters={"title": "Doctor appointment"},
            )

        executor.defer(proposal.id, until="+24h")
        row = executor._queue.get_raw(proposal.id)
        assert row["status"] == "deferred"


# ---------------------------------------------------------------------------
# History & pending
# ---------------------------------------------------------------------------

class TestHistory:
    def test_pending_empty_initially(self, executor):
        assert executor.pending() == []

    def test_history_returns_list(self, executor):
        history = executor.history()
        assert isinstance(history, list)


# ---------------------------------------------------------------------------
# Proposal quality gate
# ---------------------------------------------------------------------------

class TestProposalQualityGate:
    def test_empty_instruction_sheet_rejected_before_enqueue(self, executor):
        proposal = _make_proposal(
            action_type="instruction_sheet",
            domain="goals",
            title="Generate guide: empty",
            min_trust=0,
            parameters={"task": "Empty", "service": "Test", "context": {}},
        )

        with pytest.raises(ValueError, match="instruction_sheet_empty_context"):
            executor.propose_direct(proposal)

        assert executor.pending() == []

    def test_notes_only_instruction_sheet_rejected_before_enqueue(self, executor):
        proposal = _make_proposal(
            action_type="instruction_sheet",
            domain="social",
            title="Generate guide: notes only",
            min_trust=0,
            parameters={
                "task": "Content Moment",
                "service": "Social",
                "context": {"description": "", "steps": [], "notes": ["Signal detected"]},
            },
        )

        with pytest.raises(ValueError, match="instruction_sheet_empty_context"):
            executor.propose_direct(proposal)

        assert executor.pending() == []

    def test_low_quality_pending_sweep_expires_legacy_proposal(self, executor):
        proposal = _make_proposal(
            action_type="instruction_sheet",
            domain="social",
            title="Generate guide: legacy notes only",
            min_trust=0,
            parameters={
                "task": "Content Moment",
                "service": "Social",
                "context": {"description": "", "steps": [], "notes": ["Signal detected"]},
            },
        )
        executor._queue.propose(proposal)  # noqa: SLF001 - bypass gate to model legacy data

        assert executor.expire_low_quality_pending() == 1
        assert executor._queue.get_raw(proposal.id)["status"] == "expired"  # noqa: SLF001

    def test_low_quality_pending_sweep_releases_idempotency_reservation(self, executor, artha_dir):
        from lib.idempotency import CompositeKey, IdempotencyStore

        proposal = _make_proposal(
            action_type="instruction_sheet",
            domain="social",
            title="Generate guide: legacy notes only",
            min_trust=0,
            parameters={
                "task": "Content Moment",
                "service": "Social",
                "context": {"description": "", "steps": [], "notes": ["Signal detected"]},
            },
        )
        key = CompositeKey.compute(
            "social",
            proposal.title.strip().lower()[:80],
            proposal.action_type,
            signal_type="",
        )
        store = IdempotencyStore(artha_dir / "state" / "idempotency_keys.json")
        assert store.check_or_reserve(key, proposal.action_type) == "ok"
        executor._queue.propose(proposal)  # noqa: SLF001 - bypass gate to model legacy data

        assert executor.expire_low_quality_pending() == 1
        assert store.check_or_reserve(key, proposal.action_type) == "ok"

    def test_low_quality_sweep_releases_existing_expired_reservation(self, executor, artha_dir):
        from lib.idempotency import CompositeKey, IdempotencyStore

        proposal = _make_proposal(
            action_type="instruction_sheet",
            domain="social",
            title="Generate guide: already expired notes only",
            min_trust=0,
            parameters={
                "task": "Content Moment",
                "service": "Social",
                "context": {"description": "", "steps": [], "notes": ["Signal detected"]},
            },
        )
        key = CompositeKey.compute(
            "social",
            proposal.title.strip().lower()[:80],
            proposal.action_type,
            signal_type="",
        )
        store = IdempotencyStore(artha_dir / "state" / "idempotency_keys.json")
        assert store.check_or_reserve(key, proposal.action_type) == "ok"
        executor._queue.propose(proposal)  # noqa: SLF001 - bypass gate to model legacy data
        executor._queue.transition(proposal.id, "expired", actor="system:expiry")  # noqa: SLF001

        assert executor.expire_low_quality_pending() == 0
        assert store.check_or_reserve(key, proposal.action_type) == "ok"

    def test_failed_direct_enqueue_releases_idempotency_reservation(self, executor, artha_dir):
        from lib.idempotency import CompositeKey, IdempotencyStore

        proposal = _make_proposal(
            action_type="instruction_sheet",
            domain="social",
            title="Generate guide: enqueue failure",
            min_trust=0,
            parameters={
                "task": "Content Moment",
                "service": "Social",
                "context": {"description": "Useful plan", "steps": ["Review it"]},
                "signal_type": "content_moment_missed",
            },
        )
        key = CompositeKey.compute(
            "social",
            proposal.title.strip().lower()[:80],
            proposal.action_type,
            signal_type="content_moment_missed",
        )
        store = IdempotencyStore(artha_dir / "state" / "idempotency_keys.json")

        with patch.object(executor._queue, "propose", side_effect=ValueError("queue duplicate")):  # noqa: SLF001
            with pytest.raises(ValueError, match="queue duplicate"):
                executor.propose_direct(proposal)

        assert store.check_or_reserve(key, proposal.action_type) == "ok"

    def test_direct_enqueue_recovers_orphan_pending_idempotency_key(self, executor, artha_dir):
        from lib.idempotency import CompositeKey, IdempotencyStore

        proposal = _make_proposal(
            action_type="instruction_sheet",
            domain="social",
            title="Generate guide: orphan idempotency",
            min_trust=0,
            parameters={
                "task": "Content Moment",
                "service": "Social",
                "context": {"description": "Useful plan", "steps": ["Review it"]},
                "signal_type": "content_moment_missed",
            },
        )
        key = CompositeKey.compute(
            "social",
            proposal.title.strip().lower()[:80],
            proposal.action_type,
            signal_type="content_moment_missed",
        )
        store = IdempotencyStore(artha_dir / "state" / "idempotency_keys.json")
        assert store.check_or_reserve(key, proposal.action_type) == "ok"

        executor.propose_direct(proposal)

        assert executor._queue.get_raw(proposal.id)["status"] == "pending"  # noqa: SLF001

    def test_stale_delivery_reminder_rejected_before_enqueue(self, executor):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        title = f"USPS Expected Delivery on {past.strftime('%A, %B %d, %Y')} arriving"
        proposal = _make_proposal(
            action_type="reminder_create",
            domain="shopping",
            title=f"Create reminder: {title}",
            min_trust=0,
            parameters={"title": title, "due_date": "", "body": ""},
        )

        with pytest.raises(ValueError, match="stale_delivery_reminder"):
            executor.propose_direct(proposal)

        assert executor.pending() == []


# ---------------------------------------------------------------------------
# Handler loading
# ---------------------------------------------------------------------------

class TestHandlerLoading:
    def test_unknown_action_type_raises(self, executor):
        with pytest.raises((ValueError, KeyError, ImportError)):
            executor._get_handler("nonexistent_action_type_xyz")

    def test_handler_map_restricts_loading(self, executor):
        """Handler map is an allowlist — arbitrary module paths are blocked."""
        from actions import _HANDLER_MAP
        assert "email_send" in _HANDLER_MAP
        assert "calendar_create" in _HANDLER_MAP
        # No path traversal possible
        for k, v in _HANDLER_MAP.items():
            assert ".." not in v
            assert v.startswith("actions.")
