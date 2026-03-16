#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/session_summarizer.py — Session context summarization for Artha.

After a major command completes (catch-up, domain deep-dive, bootstrap),
compresses the command's conversation context into a structured summary
and writes the full history to tmp/ for recovery.

Phase 3 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 3).

Summary schema (Pydantic model ``SessionSummary``):
    session_intent   — What the user asked for
    command_executed — Which command ran
    key_findings     — Top 5 findings/alerts (max 50 tokens each)
    state_mutations  — Which state files were modified
    open_threads     — Unresolved items needing follow-up
    next_suggested   — Recommended next command
    timestamp        — ISO-8601

Triggering rules (applied in Artha.core.md Session Summarization Protocol):
- After /catch-up (any format)
- After /domain <X> deep-dive
- After /bootstrap or /bootstrap <domain>
- Proactive: when estimated context usage reaches threshold_pct (default 70%)
- Never during active Step 7 domain processing or Step 8 cross-domain reasoning

Config flag: harness.session_summarization.enabled (default: true)
Config key:  harness.session_summarization.threshold_pct (default: 70)

Ref: specs/deep-agents.md Phase 3, Artha.core.md Session Summarization Protocol
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.common import ARTHA_DIR, TMP_DIR
from context_offloader import load_harness_flag

try:
    from pydantic import BaseModel, Field
    _PYDANTIC_AVAILABLE = True
except ImportError:
    _PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment,misc]

    class Field:  # type: ignore[no-redef]
        """Minimal stub when pydantic is unavailable."""
        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            return kwargs.get("default", None)

# Model context window size used for proactive triggering (chars)
# Conservative estimate: 200K tokens × 4 chars/token
_MODEL_CONTEXT_CHARS = 200_000 * 4

# Commands that trigger post-command summarization
SUMMARIZE_AFTER_COMMANDS: frozenset[str] = frozenset({
    "/catch-up",
    "catch me up",
    "morning briefing",
    "sitrep",
    "run catch-up",
    "/catch-up flash",
    "/catch-up deep",
    "/catch-up standard",
    "/domain",
    "/bootstrap",
})


if _PYDANTIC_AVAILABLE:
    from pydantic import BaseModel, Field as PydanticField

    class SessionSummary(BaseModel):
        """Structured summary of a completed Artha command session.

        Written to tmp/session_history_{N}.md to enable context recovery
        in multi-command sessions.
        """

        session_intent: str = PydanticField(
            description="What the user asked for (e.g. 'morning catch-up')",
            max_length=200,
        )
        command_executed: str = PydanticField(
            description="Which command ran (e.g. '/catch-up', '/domain finance')",
            max_length=100,
        )
        key_findings: list[str] = PydanticField(
            default_factory=list,
            description="Top findings/alerts from this session (max 5, each ≤50 tokens)",
            max_length=5,
        )
        state_mutations: list[str] = PydanticField(
            default_factory=list,
            description="State files modified this session (e.g. 'state/finance.md')",
        )
        open_threads: list[str] = PydanticField(
            default_factory=list,
            description="Unresolved items needing follow-up in a future command",
        )
        next_suggested: str = PydanticField(
            default="",
            description="Recommended next command for the user",
            max_length=200,
        )
        timestamp: str = PydanticField(
            default_factory=lambda: datetime.now(tz=timezone.utc).isoformat(),
            description="ISO-8601 timestamp of summarization",
        )
        context_before_pct: float = PydanticField(
            default=0.0,
            description="Estimated context usage % before summarization",
            ge=0.0,
            le=100.0,
        )
        context_after_pct: float = PydanticField(
            default=0.0,
            description="Estimated context usage % after summarization",
            ge=0.0,
            le=100.0,
        )
        trigger_reason: str = PydanticField(
            default="post_command",
            description="Why summarization was triggered: post_command or proactive_threshold",
        )
        pre_flush_facts_persisted: int = PydanticField(
            default=0,
            description="Number of facts flushed to memory.md before this compression (AR-3)",
            ge=0,
        )

        def to_markdown(self) -> str:
            """Render the summary as a Markdown document for tmp/ storage."""
            lines = [
                f"# Session Summary — {self.timestamp[:19]}Z",
                "",
                f"**Intent:** {self.session_intent}",
                f"**Command:** `{self.command_executed}`",
                f"**Trigger:** {self.trigger_reason}",
                f"**Context usage before/after:** {self.context_before_pct:.1f}% → {self.context_after_pct:.1f}%",
                "",
                "## Key Findings",
            ]
            for i, finding in enumerate(self.key_findings, 1):
                lines.append(f"{i}. {finding}")
            lines += [
                "",
                "## State Mutations",
            ]
            for mut in self.state_mutations:
                lines.append(f"- {mut}")
            lines += [
                "",
                "## Open Threads",
            ]
            for thread in self.open_threads:
                lines.append(f"- {thread}")
            lines += [
                "",
                f"**Next suggested command:** `{self.next_suggested}`" if self.next_suggested else "",
            ]
            return "\n".join(lines).strip()

else:
    # Fallback dataclass-style implementation when pydantic is not installed
    class SessionSummary:  # type: ignore[no-redef]
        """Fallback SessionSummary for environments without pydantic."""

        def __init__(
            self,
            session_intent: str = "",
            command_executed: str = "",
            key_findings: list[str] | None = None,
            state_mutations: list[str] | None = None,
            open_threads: list[str] | None = None,
            next_suggested: str = "",
            timestamp: str = "",
            context_before_pct: float = 0.0,
            context_after_pct: float = 0.0,
            trigger_reason: str = "post_command",
            pre_flush_facts_persisted: int = 0,
        ) -> None:
            self.session_intent = session_intent
            self.command_executed = command_executed
            self.key_findings = key_findings or []
            self.state_mutations = state_mutations or []
            self.open_threads = open_threads or []
            self.next_suggested = next_suggested
            self.timestamp = timestamp or datetime.now(tz=timezone.utc).isoformat()
            self.context_before_pct = context_before_pct
            self.context_after_pct = context_after_pct
            self.trigger_reason = trigger_reason
            self.pre_flush_facts_persisted = pre_flush_facts_persisted

        def model_dump(self) -> dict:
            return self.__dict__.copy()

        def to_markdown(self) -> str:
            lines = [
                f"# Session Summary — {self.timestamp[:19]}Z",
                "",
                f"**Intent:** {self.session_intent}",
                f"**Command:** `{self.command_executed}`",
                f"**Trigger:** {self.trigger_reason}",
                "",
                "## Key Findings",
            ]
            for i, finding in enumerate(self.key_findings, 1):
                lines.append(f"{i}. {finding}")
            return "\n".join(lines).strip()


def estimate_context_pct(
    text: str,
    model_limit_chars: int = _MODEL_CONTEXT_CHARS,
) -> float:
    """Estimate what percentage of the model context window ``text`` occupies.

    Uses the 1 token ≈ 4 chars heuristic and a default 200K-token model limit.

    Args:
        text: The text whose size to estimate.
        model_limit_chars: Total model context size in characters
            (default 200K tokens × 4 = 800K chars).

    Returns:
        float: Estimated usage as a percentage (0–100).
    """
    if model_limit_chars <= 0:
        return 0.0
    return min((len(text) / model_limit_chars) * 100.0, 100.0)


def load_threshold_pct() -> float:
    """Read harness.session_summarization.threshold_pct from artha_config.yaml.

    Returns the configured percentage (0–100); defaults to 70.
    """
    try:
        import yaml  # noqa: PLC0415

        cfg_path = ARTHA_DIR / "config" / "artha_config.yaml"
        if not cfg_path.exists():
            return 70.0
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        val = (
            cfg.get("harness", {})
            .get("session_summarization", {})
            .get("threshold_pct", 70)
        )
        f_val = float(val)
        return max(0.0, min(f_val, 100.0))
    except Exception:  # noqa: BLE001
        return 70.0


def should_summarize_now(context_text: str, command: str | None = None) -> bool:
    """Decide whether session summarization should trigger.

    Summarization triggers when:
    - A major command just completed (command is in SUMMARIZE_AFTER_COMMANDS), OR
    - Context usage has reached the configured threshold percentage.

    Args:
        context_text: Current accumulated context as a string, used
            for size estimation.
        command: The command that just completed (optional).

    Returns:
        True if summarization should be triggered.
    """
    if not load_harness_flag("session_summarization.enabled"):
        return False

    # Post-command trigger
    if command:
        cmd_lower = command.lower().strip()
        for trigger in SUMMARIZE_AFTER_COMMANDS:
            if cmd_lower.startswith(trigger):
                return True

    # Proactive threshold trigger
    pct = estimate_context_pct(context_text)
    threshold = load_threshold_pct()
    return pct >= threshold


def should_flush_memory(context_text: str) -> bool:
    """Decide whether to flush in-context facts to memory.md before compression (AR-3).

    Pre-eviction flush should trigger whenever summarization is about to
    compress the context — ensuring no accumulated facts are lost in the
    compression window.

    A flush is warranted when:
    - The config flag harness.agentic.pre_eviction_flush.enabled is true, AND
    - Context usage has reached ≥50% of the model window (earlier flush is safer).

    Args:
        context_text: Current accumulated context string (for size estimation).

    Returns:
        True if facts should be flushed to memory.md before compression.
    """
    if not load_harness_flag("agentic.pre_eviction_flush.enabled"):
        return False
    # Flush at half the summarization threshold (conservative — fail safe)
    pct = estimate_context_pct(context_text)
    trigger_pct = load_threshold_pct() / 2.0
    return pct >= trigger_pct


def create_session_summary(
    session_intent: str,
    command_executed: str,
    key_findings: list[str],
    state_mutations: list[str],
    open_threads: list[str],
    next_suggested: str = "",
    context_before_pct: float = 0.0,
    context_after_pct: float = 0.0,
    trigger_reason: str = "post_command",
    pre_flush_facts_persisted: int = 0,
) -> "SessionSummary":
    """Create a validated SessionSummary from the provided fields.

    Enforces the 5-item cap on key_findings and truncates each finding to
    ≤200 characters to keep the summary compact.

    Args:
        session_intent: What the user asked for.
        command_executed: Which Artha command ran.
        key_findings: Top findings; capped at 5.
        state_mutations: List of state file paths modified.
        open_threads: Unresolved items for future commands.
        next_suggested: Recommended next command.
        context_before_pct: Context usage % before summarization.
        context_after_pct: Context usage % after summarization.
        trigger_reason: "post_command" or "proactive_threshold".

    Returns:
        SessionSummary instance.
    """
    # Enforce caps
    truncated_findings = [f[:200] for f in key_findings[:5]]

    kwargs = dict(
        session_intent=session_intent[:200],
        command_executed=command_executed[:100],
        key_findings=truncated_findings,
        state_mutations=state_mutations,
        open_threads=open_threads,
        next_suggested=next_suggested[:200],
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        context_before_pct=context_before_pct,
        context_after_pct=context_after_pct,
        trigger_reason=trigger_reason,
        pre_flush_facts_persisted=max(0, pre_flush_facts_persisted),
    )

    if _PYDANTIC_AVAILABLE:
        return SessionSummary(**kwargs)  # type: ignore[call-arg]
    return SessionSummary(**kwargs)  # fallback


def summarize_to_file(
    summary: "SessionSummary",
    session_n: int,
    artha_dir: Path | None = None,
) -> Path:
    """Write a session summary to tmp/session_history_{N}.md.

    Also writes the JSON representation alongside it for programmatic recovery.

    Args:
        summary: The SessionSummary to persist.
        session_n: Session sequence number (increments within a session).
        artha_dir: Override for Artha project root (used in tests).

    Returns:
        Path to the written Markdown file.
    """
    base_dir = artha_dir if artha_dir is not None else ARTHA_DIR
    tmp_dir = base_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    md_path = tmp_dir / f"session_history_{session_n}.md"
    json_path = tmp_dir / f"session_history_{session_n}.json"

    md_path.write_text(summary.to_markdown(), encoding="utf-8")

    # JSON alongside for programmatic recovery
    data = summary.model_dump() if hasattr(summary, "model_dump") else summary.__dict__
    json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    return md_path


def _auto_extract_facts_if_catchup(summary: "SessionSummary", artha_dir: Path | None = None) -> int:
    """Auto-invoke persistent fact extraction when a catch-up session completes (AR-3).

    Only runs when:
    - The command was a catch-up variant
    - The harness flag ``harness.agentic.fact_extraction.enabled`` is True
    - A session history file exists in tmp/

    Returns the count of new facts persisted (0 if skipped or unavailable).
    """
    from context_offloader import load_harness_flag  # already imported at module level
    if not load_harness_flag("agentic.fact_extraction.enabled"):
        return 0

    cmd = summary.command_executed.lower().strip()
    if not any(kw in cmd for kw in ("catch-up", "catch_up", "catchup", "/catch")):
        return 0

    base_dir = artha_dir if artha_dir is not None else ARTHA_DIR
    tmp_dir = base_dir / TMP_DIR.name if artha_dir is not None else TMP_DIR

    try:
        import glob as _glob
        summaries = sorted(_glob.glob(str(tmp_dir / "session_history_*.md")))
        if not summaries:
            return 0
        from fact_extractor import extract_facts_from_summary, persist_facts  # type: ignore[import]
        facts = extract_facts_from_summary(Path(summaries[-1]), base_dir)
        count = persist_facts(facts, base_dir)
        return count
    except Exception:
        return 0  # Non-fatal — fact extraction is best-effort


def get_context_card(summary: "SessionSummary", artha_dir: Path | None = None) -> str:
    """Return a compact in-context card replacing full conversation history.

    Automatically triggers persistent fact extraction when a catch-up has
    completed (AR-3 pre-eviction flush integration). The fact count is
    embedded in the card footer.

    This replaces the full conversation history after summarization, keeping
    the AI informed about the session state without the full token cost.

    Returns:
        ~500-token string summarizing the completed command.
    """
    # Auto-extract facts for catch-up commands (AR-3 / AR-5 integration)
    facts_persisted = _auto_extract_facts_if_catchup(summary, artha_dir=artha_dir)

    findings_block = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(summary.key_findings))
    mutations_block = ", ".join(summary.state_mutations) if summary.state_mutations else "none"
    threads_block = "\n".join(f"  - {t}" for t in summary.open_threads) if summary.open_threads else "  none"

    facts_note = f"\nFacts persisted to memory: {facts_persisted}" if facts_persisted > 0 else ""
    return (
        f"[SESSION CONTEXT — {summary.timestamp[:19]}Z]\n"
        f"Command: {summary.command_executed}\n"
        f"Intent:  {summary.session_intent}\n"
        f"\nKey findings:\n{findings_block}\n"
        f"\nState files modified: {mutations_block}\n"
        f"\nOpen threads:\n{threads_block}\n"
        + (f"\nNext suggested: {summary.next_suggested}\n" if summary.next_suggested else "")
        + facts_note
        + "\n[END SESSION CONTEXT — full history in tmp/session_history_N.md]"
    )
