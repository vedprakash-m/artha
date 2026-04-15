"""
scripts/lib/session_preconditions.py — Session-level precondition snapshot
============================================================================
DEBT-ARCH-003: Evaluate Wave 0, vault availability, and preflight health ONCE
at session start and cache the result. All guardrails consult this snapshot
rather than re-evaluating independently, eliminating mid-run state drift and
providing deterministic compound-failure handling.

DEBT-DEGRADE-001: Exposes a bitmask degradation model so the briefing footer
and status output can surface a single coherent degradation level when multiple
components fail simultaneously.

Bitmask bit positions (DEBT-DEGRADE-001):
    VAULT_BIT   = 0b001   bit 0 — vault available
    ACTION_BIT  = 0b010   bit 1 — action layer available
    KG_BIT      = 0b100   bit 2 — KG available

    0b111 = all OK (Level 0 / nominal)
    0b000 = all failed (Level ≥ 3)
    0b110 = vault failed only
    0b101 = action layer failed only
    etc.

Usage::
    prec = SessionPreconditions(artha_dir=Path(...)).evaluate()
    if not prec.results["wave0_complete"]:
        raise RuntimeError("Wave 0 incomplete")
    level = prec.degradation_level()   # 0–4 waterfall
    flags = prec.degradation_flags()   # bitmask for compound failures
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("artha.session_preconditions")

# ---------------------------------------------------------------------------
# Bitmask constants (DEBT-DEGRADE-001)
# ---------------------------------------------------------------------------
VAULT_BIT  = 0b001  # noqa: E221
ACTION_BIT = 0b010
KG_BIT     = 0b100
ALL_OK     = VAULT_BIT | ACTION_BIT | KG_BIT  # 0b111

# ---------------------------------------------------------------------------
# Degradation capability map (DEBT-DEGRADE-001)
# ---------------------------------------------------------------------------
#   key   = degradation_flags() bitmask value
#   value = dict with "level", "description", "capabilities", "user_notice"
DEGRADATION_MAP: dict[int, dict[str, Any]] = {
    ALL_OK: {  # 0b111 — all OK
        "level": 0,
        "description": "All systems operational",
        "capabilities": ["full_brief", "action_proposals", "vault_domains", "kg_context"],
        "user_notice": None,
    },
    # vault failed (bit 0 = 0)
    ACTION_BIT | KG_BIT: {  # 0b110
        "level": 1,
        "description": "Vault unavailable — encrypted domains skipped",
        "capabilities": ["full_brief", "action_proposals", "kg_context"],
        "user_notice": "Briefing excludes encrypted domains (vault unavailable)",
    },
    # action layer failed (bit 1 = 0)
    VAULT_BIT | KG_BIT: {  # 0b101
        "level": 2,
        "description": "Action layer unavailable — read-only mode",
        "capabilities": ["full_brief", "kg_context"],
        "user_notice": "Action proposals disabled (action layer error)",
    },
    # KG failed (bit 2 = 0)
    VAULT_BIT | ACTION_BIT: {  # 0b011
        "level": 3,
        "description": "Knowledge graph unavailable — reduced routing context",
        "capabilities": ["full_brief", "action_proposals"],
        "user_notice": "Routing context reduced (knowledge graph unavailable)",
    },
    # vault + action both failed
    KG_BIT: {  # 0b100
        "level": 3,
        "description": "Vault and action layer unavailable — read-only minimal brief",
        "capabilities": ["kg_context"],
        "user_notice": "Vault and action layer unavailable — minimal read-only mode",
    },
    # vault + KG failed
    ACTION_BIT: {  # 0b010
        "level": 3,
        "description": "Vault and KG unavailable",
        "capabilities": ["action_proposals"],
        "user_notice": "Vault and KG unavailable — action-only mode",
    },
    # action + KG failed
    VAULT_BIT: {  # 0b001
        "level": 3,
        "description": "Action layer and KG unavailable",
        "capabilities": ["full_brief", "vault_domains"],
        "user_notice": "Action proposals and KG unavailable",
    },
    # all failed
    0: {
        "level": 4,
        "description": "Static briefing from last-written state only",
        "capabilities": ["static_brief"],
        "user_notice": "All enhancement layers unavailable — static briefing only",
    },
}


# ---------------------------------------------------------------------------
# SessionPreconditions
# ---------------------------------------------------------------------------

class SessionPreconditions:
    """Single-shot session precondition snapshot.

    Evaluate once at session start. Pass to all guardrails/orchestrators so
    they consult the same stable snapshot rather than re-evaluating independently.

    Example::
        prec = SessionPreconditions(artha_dir=Path("...")).evaluate()
        if not prec.results["wave0_complete"]:
            raise RuntimeError("Wave 0 incomplete — cannot proceed")
        level = prec.degradation_level()
    """

    def __init__(self, artha_dir: Path | None = None) -> None:
        self._artha_dir = artha_dir or _infer_artha_dir()
        self.results: dict[str, bool] = {
            "wave0_complete": True,    # conservative default (fail-open)
            "vault_available": True,
            "preflight_ok": True,
            "kg_available": True,
            "action_layer_available": True,
        }
        self._evaluated = False

    def evaluate(self) -> "SessionPreconditions":
        """Run all checks once and cache results. Returns self for chaining."""
        self.results["wave0_complete"]        = self._check_wave0()
        self.results["vault_available"]       = self._check_vault()
        self.results["preflight_ok"]          = self._check_preflight()
        self.results["kg_available"]          = self._check_kg()
        self.results["action_layer_available"] = self._check_action_layer()
        self._evaluated = True
        log.debug(
            "SessionPreconditions: wave0=%s vault=%s preflight=%s kg=%s action=%s flags=0b%03b",
            self.results["wave0_complete"],
            self.results["vault_available"],
            self.results["preflight_ok"],
            self.results["kg_available"],
            self.results["action_layer_available"],
            self.degradation_flags(),
        )
        return self

    # ------------------------------------------------------------------
    # Degradation level + bitmask (DEBT-DEGRADE-001)
    # ------------------------------------------------------------------

    def degradation_flags(self) -> int:
        """Return bitmask representing available components (DEBT-DEGRADE-001).

        Bits: VAULT_BIT=0b001, ACTION_BIT=0b010, KG_BIT=0b100.
        0b111 = all OK; 0b000 = all failed.
        """
        vault  = int(self.results.get("vault_available", True))
        action = int(self.results.get("action_layer_available", True))
        kg     = int(self.results.get("kg_available", True))
        return (vault * VAULT_BIT) | (action * ACTION_BIT) | (kg * KG_BIT)

    def degradation_level(self) -> int:
        """Return highest-priority waterfall degradation level (0=nominal, 4=static-only).

        0 — nominal: all systems operational
        1 — vault unavailable
        2 — action layer unavailable
        3 — KG unavailable or compound failure
        4 — all failed
        """
        flags = self.degradation_flags()
        entry = DEGRADATION_MAP.get(flags)
        return entry["level"] if entry else 3  # unknown compound → level 3

    def degradation_info(self) -> dict[str, Any]:
        """Return the full degradation map entry for the current flag state."""
        flags = self.degradation_flags()
        return DEGRADATION_MAP.get(flags, {
            "level": 3,
            "description": "Compound failure — partial degradation",
            "capabilities": [],
            "user_notice": "System partially degraded — see preflight for details",
        })

    def user_notice(self) -> str | None:
        """Return a human-readable degradation notice, or None if nominal."""
        return self.degradation_info().get("user_notice")

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_wave0(self) -> bool:
        """Return True if Wave 0 is complete or cannot be determined (fail-open)."""
        # Allow CI/test bypass
        if os.environ.get("ARTHA_WAVE0_OVERRIDE", "").strip():
            return True
        try:
            import context_offloader  # type: ignore[import]
            return bool(context_offloader.load_harness_flag("wave0.complete"))
        except Exception:
            # Fail-open: unknown state treated as complete so as not to block
            return True

    def _check_vault(self) -> bool:
        """Return True if vault is available (vault.py health check passes)."""
        vault_script = self._artha_dir / "scripts" / "vault.py"
        if not vault_script.exists():
            return True  # no vault at all — not required on this setup
        try:
            result = subprocess.run(
                [sys.executable, str(vault_script), "health"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_preflight(self) -> bool:
        """Return True if preflight passes with no P0 failures."""
        preflight_script = self._artha_dir / "scripts" / "preflight.py"
        if not preflight_script.exists():
            return True
        try:
            result = subprocess.run(
                [sys.executable, str(preflight_script), "--quiet"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return True  # fail-open for preflight

    def _check_kg(self) -> bool:
        """Return True if KnowledgeGraph can be opened."""
        try:
            sys.path.insert(0, str(self._artha_dir / "scripts"))
            from lib.knowledge_graph import KnowledgeGraph  # type: ignore[import]
            kg = KnowledgeGraph(artha_dir=self._artha_dir)
            kg.close()
            return True
        except Exception:
            return False

    def _check_action_layer(self) -> bool:
        """Return True if the action orchestrator module can be imported."""
        try:
            sys.path.insert(0, str(self._artha_dir / "scripts"))
            import action_orchestrator  # type: ignore[import] # noqa: F401
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

def _infer_artha_dir() -> Path:
    """Infer artha_dir from this file's location (scripts/lib/ → repo root)."""
    return Path(__file__).resolve().parent.parent.parent
