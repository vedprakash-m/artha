# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/evaluator_optimizer.py — EAR-6 evaluator-optimizer retry loop.

When a response falls below quality threshold, this module deterministically
generates dimension-specific feedback and triggers a single retry — WITHOUT
using an LLM call for the feedback itself.

Activation:
  - quality_score < quality_threshold (default 0.6)
  - OR any dimension score < dimension_retry_threshold (default 0.45)

Safety:
  - Max 1 retry per invocation (never a loop at runtime).
  - Weekly budget cap: optimizer_max_retries_per_week: 50 (state in tmp/).
  - Final quality = max(initial_score, retry_score) — never downgrade.
  - Feedback injected as a structured preamble to the retry prompt,
    NOT as conversation history (avoids hallucination bleed).

Feedback style: deterministic, dimension-by-dimension, no LLM in the loop.

Ref: specs/ext-agent-reloaded.md §EAR-6
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_STATE_PATH = _REPO_ROOT / "tmp" / "ext-agent-optimizer-state.json"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_DEFAULT_QUALITY_THRESHOLD = 0.60
_DEFAULT_DIM_THRESHOLD = 0.45
_DEFAULT_WEEKLY_BUDGET = 50

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class OptimizeResult:
    """Returned by EvaluatorOptimizer.maybe_retry()."""
    final_response: str
    final_quality: float
    retried: bool
    retry_reason: str = ""
    budget_remaining: int = 0


# ---------------------------------------------------------------------------
# Feedback generation (deterministic — no LLM)
# ---------------------------------------------------------------------------

_DIM_FEEDBACK_TEMPLATES = {
    "factual": (
        "Your previous answer may contain factual gaps. "
        "Prioritize verifiable statements and cite the agent's knowledge base. "
        "If uncertain, say 'Based on available data…' rather than asserting."
    ),
    "completeness": (
        "Your previous answer was incomplete. "
        "Address all parts of the question and avoid abrupt endings. "
        "If token budget is a concern, prefer shorter but complete sentences."
    ),
    "relevance": (
        "Your previous answer diverged from the question. "
        "Focus strictly on what was asked. Trim context that does not directly support the answer."
    ),
    "clarity": (
        "Your previous answer was unclear or overly dense. "
        "Use plain language, bullet points where appropriate, "
        "and lead with the most important conclusion."
    ),
    "safety": (
        "Your previous answer may have violated SOUL principles. "
        "Review: no exfiltration of private data, no fabricated claims, "
        "refuse speculation beyond the knowledge base."
    ),
}

def _build_feedback_preamble(dim_scores: dict[str, float], threshold: float) -> str:
    """Build a concise feedback block for the retry prompt."""
    weak_dims = [d for d, v in dim_scores.items() if v < threshold]
    if not weak_dims:
        return ""

    lines = ["[QUALITY FEEDBACK — address these areas in your retry response]"]
    for dim in weak_dims:
        tmpl = _DIM_FEEDBACK_TEMPLATES.get(dim, f"Improve your {dim} score.")
        lines.append(f"• {dim.upper()}: {tmpl}")
    lines.append("[END FEEDBACK]")
    return "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------
# Weekly budget state
# ---------------------------------------------------------------------------

def _load_budget_state() -> dict:
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _save_budget_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=_STATE_PATH.parent, prefix=".opt_tmp_", suffix=".json"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp_path, _STATE_PATH)
    except Exception:
        pass


def _current_week() -> str:
    """Return ISO week string, e.g. '2026-W14'."""
    now = datetime.now(timezone.utc)
    return now.strftime("%G-W%V")


def _budget_remaining(state: dict, weekly_cap: int) -> int:
    week = _current_week()
    used = state.get("weeks", {}).get(week, 0)
    return max(0, weekly_cap - used)


def _consume_budget(state: dict, n: int = 1) -> dict:
    week = _current_week()
    weeks = state.setdefault("weeks", {})
    weeks[week] = weeks.get(week, 0) + n
    return state


# ---------------------------------------------------------------------------
# EvaluatorOptimizer
# ---------------------------------------------------------------------------

class EvaluatorOptimizer:
    """EAR-6 evaluator-optimizer loop — single-retry with deterministic feedback.

    Args:
        quality_threshold: Overall quality floor. Default 0.60.
        dim_threshold: Per-dimension floor. Default 0.45.
        weekly_cap: Max retries consumed per calendar week. Default 50.
    """

    def __init__(
        self,
        quality_threshold: float = _DEFAULT_QUALITY_THRESHOLD,
        dim_threshold: float = _DEFAULT_DIM_THRESHOLD,
        weekly_cap: int = _DEFAULT_WEEKLY_BUDGET,
    ) -> None:
        self.quality_threshold = quality_threshold
        self.dim_threshold = dim_threshold
        self.weekly_cap = weekly_cap
        self._state = _load_budget_state()

    def _should_retry(
        self,
        quality_score: float,
        dim_scores: dict[str, float],
    ) -> tuple[bool, str]:
        """Decide if a retry is warranted.  Returns (should_retry, reason)."""
        if quality_score < self.quality_threshold:
            return True, f"quality {quality_score:.2f} < threshold {self.quality_threshold:.2f}"
        weak = [d for d, v in dim_scores.items() if v < self.dim_threshold]
        if weak:
            return True, f"dimension(s) below threshold: {', '.join(weak)}"
        return False, ""

    def maybe_retry(
        self,
        *,
        agent_name: str,
        query: str,
        initial_response: str,
        initial_quality: float,
        dim_scores: Optional[dict[str, float]] = None,
        invoke_fn: Any = None,  # callable(query: str, feedback: str) -> tuple[str, float]
    ) -> OptimizeResult:
        """Attempt a single retry if quality falls below thresholds.

        Args:
            agent_name: Name of the delegated agent.
            query: Original user query.
            initial_response: Response from first invocation.
            initial_quality: Aggregate quality score (0.0–1.0).
            dim_scores: Per-dimension scores e.g. {"factual": 0.4, "clarity": 0.8}.
            invoke_fn: Callable that invokes the agent with augmented query.
                       Signature: (query: str, feedback_preamble: str) -> (response: str, quality: float)
                       If None, optimization is skipped (dry-run / test mode).

        Returns:
            OptimizeResult with final_response and final_quality.
        """
        dim_scores = dim_scores or {}
        self._state = _load_budget_state()  # re-read in case concurrent tick updated it
        budget = _budget_remaining(self._state, self.weekly_cap)

        should, reason = self._should_retry(initial_quality, dim_scores)

        if not should:
            return OptimizeResult(
                final_response=initial_response,
                final_quality=initial_quality,
                retried=False,
                budget_remaining=budget,
            )

        if budget <= 0:
            # Budget exhausted — return initial response silently
            return OptimizeResult(
                final_response=initial_response,
                final_quality=initial_quality,
                retried=False,
                retry_reason=f"budget exhausted ({self.weekly_cap}/week)",
                budget_remaining=0,
            )

        if invoke_fn is None:
            # No invocation function provided — skip in dry-run / test mode
            return OptimizeResult(
                final_response=initial_response,
                final_quality=initial_quality,
                retried=False,
                retry_reason="invoke_fn not provided",
                budget_remaining=budget,
            )

        # Build feedback preamble and invoke
        feedback = _build_feedback_preamble(dim_scores, self.dim_threshold)

        try:
            retry_response, retry_quality = invoke_fn(query, feedback)
        except Exception as exc:
            # Retry invocation failed — return initial result
            return OptimizeResult(
                final_response=initial_response,
                final_quality=initial_quality,
                retried=False,
                retry_reason=f"retry invocation raised: {exc}",
                budget_remaining=budget,
            )

        # Never downgrade
        final_quality = max(initial_quality, retry_quality)
        final_response = retry_response if retry_quality >= initial_quality else initial_response

        # Consume budget
        self._state = _consume_budget(self._state, n=1)
        _save_budget_state(self._state)
        new_budget = _budget_remaining(self._state, self.weekly_cap)

        return OptimizeResult(
            final_response=final_response,
            final_quality=final_quality,
            retried=True,
            retry_reason=reason,
            budget_remaining=new_budget,
        )
