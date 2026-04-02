#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/artha_context.py — Typed runtime context for Artha workflows.

Carries runtime state through the catch-up workflow. Constructed by
``build_context()`` at the start of a session. Updated by preflight,
pipeline, and middleware as the workflow progresses.

The LLM never sees this object directly.  It informs code-side decisions
(middleware gating, eviction tiers, checkpoint logic).  Consumers inspect
it for ``pressure``, ``is_degraded``, ``preflight_passed``, and
``steps_executed`` — the AI sees only the *effects* of these flags through
workflow file instructions.

Inspired by Pydantic AI's ``RunContextWrapper[T]`` and the OpenAI Agents SDK
``RunContextWrapper`` — a typed context object injected into every tool call,
cleanly separating "what the code knows" from "what the LLM sees".

Phase 3 of the Agentic Intelligence Improvement Plan (specs/agentic-improve.md).

Usage:
    from artha_context import build_context, ArthaContext, ContextPressure

    ctx = build_context(
        command="/catch-up",
        artha_dir=Path("."),
        env_manifest=manifest.to_dict(),
        preflight_results=check_results,
    )

Config flag: harness.agentic.context.enabled (default: true)
When disabled, ``build_context()`` returns a default context with
conservative (safe) defaults.

Ref: specs/agentic-improve.md Phase 3
"""
from __future__ import annotations

import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, Field
    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment,misc]

    class Field:  # type: ignore[no-redef]
        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            return kwargs.get("default", None)


try:
    from context_offloader import load_harness_flag as _load_harness_flag
except ImportError:  # pragma: no cover
    def _load_harness_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContextPressure(str, Enum):
    """Estimated token-pressure level for the current session."""
    GREEN = "green"        # < 50% of 200K context window
    YELLOW = "yellow"      # 50–70% — switch to flash compression
    RED = "red"            # 70–85% — P0 domains only
    CRITICAL = "critical"  # > 85% — emergency eviction mode


# ---------------------------------------------------------------------------
# Pressure estimator
# ---------------------------------------------------------------------------

_CONTEXT_WINDOW = 200_000  # Claude/GPT-4 target window (tokens)


def _estimate_pressure(content_tokens: int | None = None) -> ContextPressure:
    """Map estimated token count to ContextPressure tier.

    Args:
        content_tokens: Estimated token count for the session.  When None,
            returns GREEN (safe default for cold-start / offline sessions).
    """
    if content_tokens is None:
        return ContextPressure.GREEN
    fraction = content_tokens / _CONTEXT_WINDOW
    if fraction >= 0.85:
        return ContextPressure.CRITICAL
    if fraction >= 0.70:
        return ContextPressure.RED
    if fraction >= 0.50:
        return ContextPressure.YELLOW
    return ContextPressure.GREEN


def make_span_id() -> str:
    """Generate a 16-char hex span ID for distributed tracing."""
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ConnectorStatus(BaseModel):
    """Per-connector health snapshot."""
    name: str
    online: bool
    last_error: str | None = None


# ---------------------------------------------------------------------------
# ArthaContext
# ---------------------------------------------------------------------------


class ArthaContext(BaseModel):
    """Typed runtime context for a single Artha session.

    Constructed once per session by ``build_context()``.  Middleware and
    scripts may read from it freely; they should not mutate it directly —
    use ``model_copy(update={...})`` to derive an updated context.

    Fields intentionally limited to what code actually needs to inspect.
    The LLM never receives a serialisation of this object in its context
    window.
    """

    # Workflow identity
    command: str = "unknown"
    artha_dir: str = ""
    session_id: str = ""  # 16-char hex; populated by build_context() (EV-2)

    # Environment
    environment: str = "local_mac"  # from detect_environment.py
    is_degraded: bool = False       # True if filesystem_writable=False or degradations present
    degradations: list[str] = []    # human-readable degradation strings

    # Preflight
    preflight_passed: bool = True

    # Connectors (populated after Step 4)
    connectors: list[ConnectorStatus] = []

    # Context pressure
    pressure: ContextPressure = ContextPressure.GREEN

    # Active domains (populated after Step 6 routing)
    active_domains: list[str] = []

    # Agentic capabilities (AR-4)
    session_recall_available: bool = False  # True when session_search.py found prior context

    # Workflow progress (step numbers appended as each step completes)
    steps_executed: list[int] = []

    # Session timing
    session_start: datetime = None  # type: ignore[assignment]

    if _PYDANTIC_AVAILABLE:
        model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data: Any) -> None:
        if "session_start" not in data or data.get("session_start") is None:
            data["session_start"] = datetime.now(timezone.utc)
        super().__init__(**data)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def connectors_online(self) -> list[str]:
        """Names of connectors that are currently online."""
        return [c.name for c in self.connectors if c.online]

    @property
    def connectors_offline(self) -> list[str]:
        """Names of connectors that are currently offline."""
        return [c.name for c in self.connectors if not c.online]

    def health_summary(self) -> dict[str, Any]:
        """Compact serialisation suitable for inclusion in harness_metrics."""
        return {
            "command": self.command,
            "environment": self.environment,
            "pressure": self.pressure.value if isinstance(self.pressure, ContextPressure) else str(self.pressure),
            "preflight_passed": self.preflight_passed,
            "is_degraded": self.is_degraded,
            "connectors_online": self.connectors_online,
            "connectors_offline": self.connectors_offline,
            "active_domains": self.active_domains,
            "steps_executed": self.steps_executed,
            "session_id": self.session_id,
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_context(
    command: str,
    artha_dir: Path | str | None = None,
    env_manifest: dict[str, Any] | None = None,
    preflight_results: list[Any] | None = None,
) -> ArthaContext:
    """Construct an ``ArthaContext`` from available session data.

    Args:
        command: The Artha command being executed (e.g. ``"/catch-up"``).
        artha_dir: Artha project root (defaults to ``ARTHA_DIR`` constant).
        env_manifest: Output dict from ``detect_environment.py``
            ``EnvironmentManifest.to_dict()``.  Missing keys use safe defaults.
        preflight_results: List of ``CheckResult`` objects from
            ``preflight.py``.  Any P0 failure sets ``preflight_passed=False``.

    Returns:
        ``ArthaContext`` with fields populated from the supplied data.
        All fields have safe defaults when data is absent, ensuring this
        function never raises for partial input.
    """
    # Feature flag check — return conservative defaults when disabled
    if not _load_harness_flag("agentic.context.enabled"):
        return ArthaContext(command=command, artha_dir=str(artha_dir or ""))

    env = env_manifest or {}
    capabilities = env.get("capabilities", {})
    degradations: list[str] = env.get("degradations", [])
    environment: str = env.get("environment", "local_mac")

    is_degraded = (
        not capabilities.get("filesystem_writable", True)
        or len(degradations) > 0
    )

    preflight_passed = True
    if preflight_results:
        for result in preflight_results:
            # CheckResult namedtuple / dataclass: has severity + ok / passed attributes
            severity = getattr(result, "severity", None) or getattr(result, "level", None)
            ok = getattr(result, "ok", None)
            if ok is None:
                ok = getattr(result, "passed", True)
            if severity == "P0" and not ok:
                preflight_passed = False
                break

    return ArthaContext(
        command=command,
        artha_dir=str(artha_dir) if artha_dir else "",
        environment=environment,
        is_degraded=is_degraded,
        degradations=degradations,
        preflight_passed=preflight_passed,
        session_id=uuid.uuid4().hex[:16],
    )


# ---------------------------------------------------------------------------
# EV-11a — Retrospective outcome signal collection
# ---------------------------------------------------------------------------


def _count_corrections_since(artha_dir: Path, prev_ts: str) -> int:
    """Count correction-type facts in state/memory.md added since prev_ts."""
    if not prev_ts:
        return 0
    memory_path = Path(artha_dir) / "state" / "memory.md"
    if not memory_path.exists():
        return 0
    try:
        import yaml  # type: ignore[import]
        content = memory_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return 0
        end = content.find("\n---", 3)
        if end == -1:
            return 0
        fm = yaml.safe_load(content[3:end]) or {}
        facts = fm.get("facts", [])
        if not isinstance(facts, list):
            return 0
        prev_date = prev_ts[:10]
        return sum(
            1 for f in facts
            if isinstance(f, dict)
            and f.get("type") == "correction"
            and str(f.get("date_added") or "") >= prev_date
        )
    except Exception:
        return 0


def _load_current_open_items(artha_dir: Path) -> set:
    """Return set of open item IDs from state/open_items.md."""
    open_items_path = Path(artha_dir) / "state" / "open_items.md"
    if not open_items_path.exists():
        return set()
    try:
        import yaml  # type: ignore[import]
        content = open_items_path.read_text(encoding="utf-8")
        item_ids: set = set()
        item_blocks = re.split(r"\n(?=- id:)", content)
        for block in item_blocks:
            block = block.strip()
            if not block.startswith("- id:"):
                continue
            try:
                # Parse with "- " prefix intact so YAML sees a valid list entry
                item_list = yaml.safe_load(block)
                if isinstance(item_list, list) and item_list:
                    item = item_list[0]
                elif isinstance(item_list, dict):
                    item = item_list
                else:
                    continue
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                status = str(item.get("status") or "open").lower()
                if item_id and status not in (
                    "closed", "done", "resolved", "complete", "completed"
                ):
                    item_ids.add(item_id)
            except Exception:
                pass
        return item_ids
    except Exception:
        return set()


def _count_queries_since(artha_dir: Path, prev_ts: str) -> int:
    """Count ad-hoc query session history files modified after prev_ts."""
    if not prev_ts:
        return 0
    tmp_dir = Path(artha_dir) / "tmp"
    if not tmp_dir.exists():
        return 0
    try:
        cutoff_t = datetime.fromisoformat(prev_ts.replace("Z", "+00:00")).timestamp()
        count = 0
        for f in tmp_dir.glob("session_history_*.md"):
            try:
                if os.path.getmtime(str(f)) > cutoff_t:
                    count += 1
            except OSError:
                pass
        return count
    except Exception:
        return 0


def _briefing_referenced_in_queries(artha_dir: Path, prev_run: dict) -> bool:
    """Return True if any session history since prev_run references briefing content."""
    prev_ts = prev_run.get("timestamp", "")
    if not prev_ts:
        return False
    tmp_dir = Path(artha_dir) / "tmp"
    if not tmp_dir.exists():
        return False
    try:
        cutoff_t = datetime.fromisoformat(prev_ts.replace("Z", "+00:00")).timestamp()
        keywords: set = set()
        briefing_file = prev_run.get("briefing_file", "")
        if briefing_file:
            keywords.add(str(briefing_file).replace(".md", ""))
        for d in (prev_run.get("domains_processed") or []):
            keywords.add(str(d).lower())
        if not keywords:
            return False
        for f in tmp_dir.glob("session_history_*.md"):
            try:
                if os.path.getmtime(str(f)) <= cutoff_t:
                    continue
                text = f.read_text(encoding="utf-8", errors="replace").lower()
                if any(kw in text for kw in keywords):
                    return True
            except OSError:
                pass
        return False
    except Exception:
        return False


def _days_since_session(prev_run: dict, current_state: dict) -> float:
    """Return days elapsed between prev_run timestamp and now."""
    prev_ts = prev_run.get("timestamp", "")
    if not prev_ts:
        return 0.0
    try:
        prev_dt = datetime.fromisoformat(str(prev_ts).replace("Z", "+00:00"))
        now = current_state.get("now") or datetime.now(timezone.utc)
        if isinstance(now, str):
            now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        delta = (now - prev_dt).total_seconds() / 86400
        return max(0.0, delta)
    except Exception:
        return 0.0


def _get_last_run(artha_dir: Path) -> "dict | None":
    """Return most recent entry from state/catch_up_runs.yaml, or None."""
    runs_path = Path(artha_dir) / "state" / "catch_up_runs.yaml"
    if not runs_path.exists():
        return None
    try:
        import yaml  # type: ignore[import]
        data = yaml.safe_load(runs_path.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return data[-1]
    except Exception:
        pass
    return None


def _backfill_run_record(artha_dir: Path, session_id: str, outcomes: dict) -> bool:
    """Backfill outcome fields into the run record matching session_id.

    Reads state/catch_up_runs.yaml, finds the entry with matching session_id,
    updates the outcome fields, and writes back atomically.
    Returns True on success, False if not found or write failed.
    Ref: specs/eval.md EV-11a
    """
    if not session_id or not outcomes:
        return False
    runs_path = Path(artha_dir) / "state" / "catch_up_runs.yaml"
    if not runs_path.exists():
        return False
    try:
        import yaml  # type: ignore[import]
        data = yaml.safe_load(runs_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return False
        found = False
        for run in data:
            if isinstance(run, dict) and run.get("session_id") == session_id:
                run.update(outcomes)
                found = True
                break
        if not found:
            return False
        state_dir = Path(artha_dir) / "state"
        state_dir.mkdir(exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=state_dir, prefix=".catch_up_runs-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(
                    "# state/catch_up_runs.yaml\n"
                    "# Machine-parseable append-only run history.\n"
                    "# Written by health_check_writer.py; read by briefing_adapter.py.\n"
                    "# Field: engagement_rate (float) — NOT 'signal_noise' (compound object).\n"
                    "---\n"
                )
                yaml.dump(data, fh, allow_unicode=True, default_flow_style=False)
            os.replace(tmp_path, runs_path)
            return True
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        return False


def collect_outcome_signals(
    prev_run: dict,
    current_state: dict,
    artha_dir: "Path | str",
) -> dict:
    """Collect retrospective outcome signals for the previous session.

    Called at session start (Step 2b in finalize.md) before new processing.
    Compares previous session's open items, corrections, and query activity
    against current state to measure engagement and resolution rates.

    Config gate: harness.eval.outcome_signals.enabled (default: true)
    Ref: specs/eval.md EV-11a

    Args:
        prev_run: Last run record from state/catch_up_runs.yaml.
        current_state: Optional dict; may contain 'now' key (datetime) for testing.
        artha_dir: Artha project root directory.

    Returns:
        Dict of outcome fields, or empty dict if disabled or prev_run empty.
    """
    if not prev_run:
        return {}
    if not _load_harness_flag("eval.outcome_signals.enabled"):
        return {}

    _dir = Path(artha_dir)
    prev_ts = str(prev_run.get("timestamp") or "")
    outcomes: dict = {}

    # 1. Corrections added since prev session
    outcomes["outcome_corrections_next_session"] = _count_corrections_since(_dir, prev_ts)

    # 2. Open items resolved since last session
    prev_items: set = set(prev_run.get("open_item_ids") or [])
    current_items = _load_current_open_items(_dir)
    outcomes["outcome_items_resolved_24h"] = len(prev_items - current_items)

    # 3. Ad-hoc queries since prev session
    outcomes["outcome_user_queries_since"] = _count_queries_since(_dir, prev_ts)

    # 4. Briefing content referenced in subsequent queries
    outcomes["outcome_briefing_referenced"] = _briefing_referenced_in_queries(_dir, prev_run)

    # 5. Absence flag: None when >3 days gap (signals unreliable for calibration)
    days_gap = _days_since_session(prev_run, current_state)
    outcomes["outcome_user_absence_flag"] = None if days_gap > 3 else False

    return outcomes
