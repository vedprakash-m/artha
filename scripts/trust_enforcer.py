#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/trust_enforcer.py — Trust level gate enforcement.

Reads the `autonomy:` block from state/health-check.md and enforces:
  1. autonomy_floor actions ALWAYS require explicit human approval —
     NOT bypassable by trust level, configuration, or user override.
  2. Current trust level must be ≥ action's min_trust.
  3. Auto-approval via 'auto:L2' is blocked for friction="high" actions.
  4. Trust elevation and demotion criteria are evaluated here.

AUTONOMY FLOOR CONTRACT (§6.2, specs/act.md):
  Actions with autonomy_floor=true in actions.yaml ALWAYS require
  a human actor.  If approved_by contains "auto:", the check fails
  regardless of trust level.  This is a hard-coded structural rule —
  not a policy — and cannot be overridden by configuration.

Ref: specs/act.md §6
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
_lib_dir = str(Path(__file__).resolve().parent / "lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)
from state_writer import write as _state_write  # noqa: PLC0415


# "auto:" prefix in approved_by = autonomous approval (not human)
_AUTO_APPROVER_PREFIX = "auto:"

# Path to per-domain autonomy tracking file (§7.1)
_DOMAIN_AUTONOMY_PATH = Path(__file__).resolve().parent.parent / "config" / "domain_autonomy_state.yaml"

# Path to artha_config.yaml (wave0.complete source-of-truth)
_ARTHA_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "artha_config.yaml"
# Path to guardrails.yaml (wave0_gate: hard | advisory)
_GUARDRAILS_PATH = Path(__file__).resolve().parent.parent / "config" / "guardrails.yaml"


# ---------------------------------------------------------------------------
# Wave 0 Gate
# ---------------------------------------------------------------------------

class GateBlockedError(RuntimeError):
    """Raised when a trust elevation or action is blocked by the Wave 0 gate.

    Wave 0 must be complete (``harness.wave0.complete: true`` in
    ``config/artha_config.yaml``) before any autonomous elevation can proceed.

    Attributes:
        reason:      Human-readable blocking reason.
        justification: Override justification if --force-wave0 was used
                       (None otherwise).
    """

    def __init__(self, reason: str, justification: str | None = None) -> None:
        self.reason = reason
        self.justification = justification
        super().__init__(reason)


def _load_wave0_complete() -> bool:
    """Read ``harness.wave0.complete`` from config/artha_config.yaml.

    Returns ``True`` if wave0 is complete or if the config file is absent
    (e.g. CI / test environments where artha_config.yaml is gitignored).
    Returns ``False`` only when the file explicitly sets wave0.complete: false.
    """
    try:
        import re as _re
        text = _ARTHA_CONFIG_PATH.read_text(encoding="utf-8")
        # Look for: wave0:\n    complete: true/false
        m = _re.search(r"wave0:\s*\n\s+complete:\s*(true|false)", text)
        if m:
            return m.group(1).strip() == "true"
        # File exists but no wave0 block — treat as complete (not explicitly false).
        return True
    except OSError:
        # File absent (e.g. CI — artha_config.yaml is gitignored).
        # Treat as wave0 complete; only an explicit false should hard-block.
        return True


def _load_wave0_gate_mode() -> str:
    """Read ``wave0_gate`` value from config/guardrails.yaml.

    Returns ``"hard"`` or ``"advisory"`` (defaults to ``"hard"``).
    """
    try:
        text = _GUARDRAILS_PATH.read_text(encoding="utf-8")
        import re as _re
        m = _re.search(r"^wave0_gate:\s*(\S+)", text, _re.MULTILINE)
        if m:
            val = m.group(1).strip().rstrip("#").strip().lower()
            if val in ("hard", "true"):
                return "hard"
            if val in ("advisory", "false"):
                return "advisory"
    except OSError:
        pass
    return "hard"  # safe default


def _check_wave0_gate(justification: str | None = None) -> None:
    """Check Wave 0 gate.  Raises ``GateBlockedError`` if blocked.

    Args:
        justification: If provided, the caller is using --force-wave0 override.
                       The gate is bypassed and the override is logged to
                       telemetry + audit.md.

    Raises:
        :class:`GateBlockedError`: If wave0.complete is false AND gate mode
            is "hard" AND no justification is provided.
    """
    if justification is not None:
        # Override path: bypass gate but log the override
        try:
            from telemetry import emit_wave0_override  # noqa: PLC0415
            emit_wave0_override(justification=justification)
        except Exception:  # noqa: BLE001
            pass
        _append_audit_override(justification)
        return

    wave0_complete = _load_wave0_complete()
    if wave0_complete:
        return  # gate open

    gate_mode = _load_wave0_gate_mode()
    if gate_mode == "hard":
        raise GateBlockedError(
            "Wave 0 incomplete: harness.wave0.complete is false in "
            "config/artha_config.yaml. "
            "Set wave0.complete: true after all lint-state-writes checks pass, "
            "or use --force-wave0 --justification '<reason>' to override."
        )
    else:
        # Advisory mode — emit warning but continue
        import warnings
        warnings.warn(
            "[trust_enforcer] Wave 0 incomplete but gate is advisory — continuing.",
            RuntimeWarning,
            stacklevel=3,
        )


def _append_audit_override(justification: str) -> None:
    """Append a Wave 0 override row to state/audit.md.

    Row format (pipe-delimited):
      timestamp | session_id | domain | action_type | status | payload_summary
    """
    try:
        from telemetry import get_session_id  # noqa: PLC0415
        session_id = get_session_id()
    except Exception:  # noqa: BLE001
        session_id = "unknown"

    ts = datetime.now(timezone.utc).isoformat()
    audit_path = _ARTHA_CONFIG_PATH.parent.parent / "state" / "audit.md"
    row = (
        f"| {ts} | {session_id} | system | wave0_override "
        f"| override | justification: {justification[:120]} |\n"
    )
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(row)
    except OSError:
        pass


def _apply_domain_demotion(domain: str, reason: str) -> None:
    """Demote domain autonomy level by one tier with 14-day cooldown.

    PRD §7.1: Any guardrail HALT at level Ln immediately demotes the
    domain to L(n-1) for a 14-day cooldown period.  Two demotion trips
    during a cooldown window force the domain to L0 pending manual
    re-elevation.

    Non-fatal: if the YAML file is missing or malformed, the caller's
    health-check.md update (the primary trust signal) still proceeds.
    """
    try:
        import yaml  # noqa: PLC0415
        from datetime import timedelta  # noqa: PLC0415

        raw = _DOMAIN_AUTONOMY_PATH.read_text(encoding="utf-8") if _DOMAIN_AUTONOMY_PATH.exists() else "{}"
        data = yaml.safe_load(raw) or {}
        domains = data.setdefault("domains", {})
        entry = domains.setdefault(domain, {
            "current_level": "L0",
            "base_level": "L0",
            "last_promotion_date": None,
            "cooldown_expires": None,
            "trip_history": [],
        })

        current = entry.get("current_level", "L0")
        try:
            n = int(str(current).lstrip("L"))
        except (ValueError, AttributeError):
            n = 0
        new_level = f"L{max(0, n - 1)}"

        today = datetime.now(timezone.utc).date()
        cooldown_expires = (today + timedelta(days=14)).isoformat()

        entry["current_level"] = new_level
        entry["cooldown_expires"] = cooldown_expires
        trip_history = entry.setdefault("trip_history", [])
        trip_history.append({
            "date": today.isoformat(),
            "from_level": current,
            "to_level": new_level,
            "reason": reason,
        })

        _DOMAIN_AUTONOMY_PATH.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except (OSError, Exception):  # noqa: BLE001
        pass  # non-fatal: health-check.md is the primary trust signal


class TrustEnforcer:
    """Enforces trust level gates on action proposal execution.

    Usage:
        enforcer = TrustEnforcer(artha_dir)
        ok, reason = enforcer.check(proposal, action_config, approved_by)
        if not ok:
            raise PermissionError(reason)
    """

    def __init__(self, artha_dir: Path) -> None:
        self._artha_dir = artha_dir
        self._health_check_path = artha_dir / "state" / "health-check.md"
        self._autonomy: dict[str, Any] | None = None

    def _load_autonomy(self, force: bool = False) -> dict[str, Any]:
        """Load the autonomy block from health-check.md.

        Caches the result in-process to avoid repeated file reads.
        Pass force=True to re-read (e.g. after elevation/demotion).
        """
        if self._autonomy is not None and not force:
            return self._autonomy

        defaults: dict[str, Any] = {
            "trust_level": 0,
            "trust_level_since": datetime.now(timezone.utc).date().isoformat(),
            "days_at_level": 0,
            "acceptance_rate_90d": 0.0,
            "critical_false_positives": 0,
            "pre_approved_categories": [],
            "last_demotion": None,
            "last_elevation": None,
        }

        if not self._health_check_path.exists():
            self._autonomy = defaults
            return self._autonomy

        try:
            content = self._health_check_path.read_text(encoding="utf-8")
            block = _extract_autonomy_block(content)
            if block:
                has_since = "trust_level_since" in block
                defaults.update(block)
                # Compute days_at_level dynamically from trust_level_since
                # when the autonomy block explicitly contains that field.
                if has_since:
                    since_str = defaults.get("trust_level_since", "")
                    try:
                        since_date = datetime.strptime(
                            str(since_str), "%Y-%m-%d"
                        ).date()
                        today = datetime.now(timezone.utc).date()
                        defaults["days_at_level"] = max(
                            0, (today - since_date).days
                        )
                    except (ValueError, TypeError):
                        pass  # keep whatever was parsed or default 0
        except Exception:
            pass  # fallback to defaults on any parse error

        self._autonomy = defaults
        return self._autonomy

    @property
    def current_level(self) -> int:
        """Current trust level (0=observe, 1=propose, 2=pre-approve)."""
        return int(self._load_autonomy().get("trust_level", 0))

    def check(
        self,
        proposal: Any,  # ActionProposal — avoids circular import
        approved_by: str,
        action_config: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Evaluate whether execution is permitted.

        Args:
            proposal:     ActionProposal object (reads .min_trust and .friction).
            approved_by:  Actor string: "user:terminal" | "user:telegram" | "auto:L2"
            action_config: Full action config dict from actions.yaml (needs
                           "autonomy_floor" key). Defaults to {} if not provided.

        Returns:
            (True, "")                  if execution is permitted.
            (False, "REASON: details")  if execution is blocked.
        """
        if action_config is None:
            action_config = {}
        proposal_min_trust: int = getattr(proposal, "min_trust", 0)
        proposal_friction: str = getattr(proposal, "friction", "standard")
        autonomy = self._load_autonomy()
        current_trust = int(autonomy.get("trust_level", 0))
        is_auto = approved_by.startswith(_AUTO_APPROVER_PREFIX)

        # Rule 1 — AUTONOMY FLOOR: structural, non-bypassable.
        if action_config.get("autonomy_floor", False) and is_auto:
            return (
                False,
                "AUTONOMY_FLOOR: this action type always requires explicit human "
                "approval. Autonomous approval ('auto:*') is not permitted.",
            )

        # Rule 2 — Trust level gate.
        if current_trust < proposal_min_trust:
            return (
                False,
                f"TRUST_LEVEL: current trust level {current_trust} is below "
                f"the required minimum {proposal_min_trust} for this action.",
            )

        # Rule 3 — High-friction actions cannot be auto-approved even at L2.
        if is_auto and proposal_friction == "high":
            return (
                False,
                "HIGH_FRICTION: friction='high' actions cannot be auto-approved "
                "at any trust level. Explicit human approval required.",
            )

        # Rule 4 — Trust Level 0 is observation-only; no execution allowed.
        if current_trust == 0 and is_auto:
            return (
                False,
                "OBSERVE_MODE: Trust Level 0 is observation-only. "
                "All actions require explicit human approval.",
            )

        # Rule 5 — Wave 0 gate: hard block if wave0.complete is false.
        try:
            _check_wave0_gate()  # raises GateBlockedError if blocked
        except GateBlockedError as exc:
            return (False, f"WAVE0_GATE: {exc.reason}")

        return True, ""

    def elevate(
        self,
        justification: str | None = None,
    ) -> dict[str, Any]:
        """Attempt to elevate trust level.

        Enforces the Wave 0 gate as a hard block BEFORE evaluating
        elevation criteria.  If ``harness.wave0.complete`` is ``false``,
        raises :class:`GateBlockedError` unless ``--force-wave0`` override
        is provided via ``justification``.

        Args:
            justification: Override justification string for ``--force-wave0``.
                           If provided, the Wave 0 gate is bypassed and the
                           override is logged to telemetry + audit.md.

        Returns:
            Result dict from :meth:`evaluate_elevation` with an additional
            ``"elevated"`` key (True if level was incremented).

        Raises:
            :class:`GateBlockedError`: If Wave 0 gate is hard and incomplete.
        """
        # Hard Wave 0 gate — must pass before any elevation evaluation
        _check_wave0_gate(justification)

        result = self.evaluate_elevation()
        if not result.get("eligible", False):
            result["elevated"] = False
            return result

        # Perform the actual elevation
        current = result["current_level"]
        target = result["target_level"]
        now_str = datetime.now(timezone.utc).date().isoformat()

        autonomy = self._load_autonomy()
        autonomy["trust_level"] = target
        autonomy["trust_level_since"] = now_str
        autonomy["days_at_level"] = 0
        autonomy["last_elevation"] = now_str

        content = ""
        if self._health_check_path.exists():
            content = self._health_check_path.read_text(encoding="utf-8")
        updated = _replace_autonomy_block(content, autonomy)
        _state_write(
            self._health_check_path,
            updated,
            domain="health_check",
            source="trust_enforcer.elevate",
            pii_check=False,
        )
        self._autonomy = None  # invalidate cache

        # Emit telemetry
        try:
            from telemetry import emit  # noqa: PLC0415
            emit(
                "trust.elevated",
                extra={
                    "from_level": current,
                    "to_level": target,
                    "force_wave0": justification is not None,
                },
            )
        except Exception:  # noqa: BLE001
            pass

        result["elevated"] = True
        return result

    def evaluate_elevation(
        self,
        metrics_summary: dict[str, Any] | None = None,
        days_at_level: int | None = None,
    ) -> dict[str, Any]:
        """Check if trust level should be elevated.

        Args:
            metrics_summary: Optional dict with acceptance_rate_90d etc.
                             Defaults to reading from autonomy block.
            days_at_level:   Optional override for days at current level.
                             Defaults to reading from autonomy block.
        """
        autonomy = self._load_autonomy()
        current = int(autonomy.get("trust_level", 0))
        critical_fp = int(autonomy.get("critical_false_positives", 0))
        if metrics_summary is None:
            metrics_summary = dict(autonomy)
        if days_at_level is None:
            days_at_level = int(autonomy.get("days_at_level", 0))

        if current == 0:
            # L0 → L1 criteria (§6.3)
            criteria_met = {
                "days_at_level_30": days_at_level >= 30,
                "zero_critical_false_positives": critical_fp == 0,
                "acceptance_rate_na": True,  # not evaluated at L0
            }
            eligible = all(criteria_met.values())
            blocker = None if eligible else (
                _first_unmet(criteria_met, {
                    "days_at_level_30": f"Need 30+ days at Level 0 (current: {days_at_level})",
                    "zero_critical_false_positives": f"Critical false positives must be 0 (current: {critical_fp})",
                })
            )
            return {
                "eligible": eligible,
                "current_level": 0,
                "target_level": 1,
                "criteria_met": criteria_met,
                "blocker": blocker,
            }

        elif current == 1:
            # L1 → L2 criteria (§6.3)
            acceptance_rate = float(metrics_summary.get("acceptance_rate_90d", 0.0))
            pre_approved = autonomy.get("pre_approved_categories", [])
            criteria_met = {
                "days_at_level_60": days_at_level >= 60,
                "acceptance_rate_90": acceptance_rate >= 0.90,
                "pre_approved_categories_set": len(pre_approved) > 0,
                "zero_critical_false_positives": critical_fp == 0,
            }
            eligible = all(criteria_met.values())
            blocker = None if eligible else (
                _first_unmet(criteria_met, {
                    "days_at_level_60": f"Need 60+ days at Level 1 (current: {days_at_level})",
                    "acceptance_rate_90": f"Acceptance rate must be ≥0.90 (current: {acceptance_rate:.2f})",
                    "pre_approved_categories_set": "Must have at least one pre-approved category",
                    "zero_critical_false_positives": f"Critical false positives must be 0 (current: {critical_fp})",
                })
            )
            return {
                "eligible": eligible,
                "current_level": 1,
                "target_level": 2,
                "criteria_met": criteria_met,
                "blocker": blocker,
            }

        else:
            return {
                "eligible": False,
                "current_level": current,
                "target_level": current,
                "criteria_met": {},
                "blocker": "Already at maximum trust level (Level 2)",
            }

    def apply_demotion(self, reason: str = "", domain: str | None = None) -> None:
        """Immediately demote trust to Level 0 and log the incident.

        Called by ActionExecutor when a critical failure occurs (§6.4):
          - Financial loss confirmed by user feedback
          - Wrong recipient on communication
          - Critical false positive (immigration/health)

        Updates the autonomy block in health-check.md atomically.
        When ``domain`` is supplied, also updates per-domain autonomy
        level in ``config/domain_autonomy_state.yaml`` (§7.1 demotion).
        Invalidates internal cache.
        """
        content = ""
        if self._health_check_path.exists():
            content = self._health_check_path.read_text(encoding="utf-8")

        now_str = datetime.now(timezone.utc).date().isoformat()

        new_autonomy = {
            "trust_level": 0,
            "trust_level_since": now_str,
            "days_at_level": 0,
            "acceptance_rate_90d": 0.0,
            "critical_false_positives": int(
                self._load_autonomy().get("critical_false_positives", 0)
            ) + 1,
            "pre_approved_categories": [],
            "last_demotion": now_str,
            "last_elevation": self._load_autonomy().get("last_elevation"),
        }

        updated = _replace_autonomy_block(content, new_autonomy)
        _state_write(
                self._health_check_path,
                updated,
                domain="health_check",
                source="trust_enforcer.reset_trust_level",
                pii_check=False,
            )
        self._autonomy = None  # invalidate cache
        if domain is not None:
            _apply_domain_demotion(domain, reason)

    def update_autonomy_block(self, updates: dict[str, Any]) -> None:
        """Merge updates into the autonomy block in health-check.md.

        Used by ActionExecutor after each execution to update metrics.
        """
        content = ""
        if self._health_check_path.exists():
            content = self._health_check_path.read_text(encoding="utf-8")

        current = self._load_autonomy()
        current.update(updates)

        updated = _replace_autonomy_block(content, current)
        _state_write(
                self._health_check_path,
                updated,
                domain="health_check",
                source="trust_enforcer.update_autonomy_block",
                pii_check=False,
            )
        self._autonomy = None  # invalidate cache


# ---------------------------------------------------------------------------
# Health-check.md autonomy block parser / writer
# ---------------------------------------------------------------------------

_AUTONOMY_YAML_BLOCK_RE = re.compile(
    r"(```yaml\s*\nautonomy:.*?```|^autonomy:\s*\n(?:[ \t]+\S.*\n?)+)",
    re.MULTILINE | re.DOTALL,
)


def _extract_autonomy_block(content: str) -> dict[str, Any] | None:
    """Extract the 'autonomy:' YAML block from health-check.md content."""
    # Look for raw YAML block (not in a code fence)
    pattern = re.compile(
        r"^autonomy:\s*\n((?:[ \t]+.*\n?)*)",
        re.MULTILINE,
    )
    m = pattern.search(content)
    if not m:
        return None

    try:
        import yaml  # PyYAML
        block_text = "autonomy:\n" + m.group(1)
        parsed = yaml.safe_load(block_text)
        if isinstance(parsed, dict) and "autonomy" in parsed:
            return parsed["autonomy"]
    except Exception:
        pass

    return None


def _replace_autonomy_block(content: str, autonomy: dict[str, Any]) -> str:
    """Replace or append the 'autonomy:' block in health-check.md content."""
    try:
        import yaml  # PyYAML
        # Serialise to YAML with 2-space indent
        block_lines = yaml.dump(
            {"autonomy": autonomy},
            default_flow_style=False,
            sort_keys=True,
        )
    except Exception:
        # Fallback: minimal YAML serialisation
        lines = ["autonomy:"]
        for k, v in sorted(autonomy.items()):
            if isinstance(v, list):
                if v:
                    lines.append(f"  {k}:")
                    for item in v:
                        lines.append(f"    - {item}")
                else:
                    lines.append(f"  {k}: []")
            elif v is None:
                lines.append(f"  {k}: null")
            elif isinstance(v, bool):
                lines.append(f"  {k}: {str(v).lower()}")
            else:
                lines.append(f"  {k}: {v}")
        block_lines = "\n".join(lines) + "\n"

    # Check if an autonomy block already exists
    pattern = re.compile(
        r"^autonomy:\s*\n((?:[ \t]+.*\n?)*)",
        re.MULTILINE,
    )
    m = pattern.search(content)
    if m:
        # Replace existing block
        return content[:m.start()] + block_lines + content[m.end():]
    else:
        # Append new block at end
        sep = "\n" if content and not content.endswith("\n\n") else ""
        return content + sep + "\n## Autonomy State\n\n" + block_lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_unmet(criteria_met: dict[str, bool], messages: dict[str, str]) -> str:
    """Return the human-readable message for the first unmet criterion."""
    for key, met in criteria_met.items():
        if not met:
            return messages.get(key, f"Criterion '{key}' not met")
    return "Unknown blocker"
