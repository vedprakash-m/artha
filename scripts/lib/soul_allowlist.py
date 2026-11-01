# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/soul_allowlist.py — SOUL principle injection allowlist (EAR-9, R-8).

SOUL principles are injected into the composed delegation prompt to give agents
rich identity and behavioural constraints.  Before injection, each principle
string is scanned by injection_detector.  This creates false-positive risk:
legitimate SOUL instructions like "stop if you cannot cite sources" contain
phrase fragments ("stop if", "do not") that match the injection keyword list.

This module provides:
  1. A frozenset of safe phrase prefixes that, when matched, skip injection
     scanning for that principle (allowlist short-circuit).
  2. scan_principle() — per-principle scan that honours the allowlist.
  3. Audit logging integration (fire-and-forget).

Design:
  - Allowlist is conservative: only exact phrase-level prefixes, not substrings.
  - Principles matching the allowlist are NOT scanned (they pass unconditionally).
  - Principles NOT on the allowlist are scanned by InjectionDetector.scan().
  - A BLOCKED principle is excluded individually — remaining principles and the
    full composed prompt are still dispatched.  Silently dropping "do not
    fabricate data" due to a false positive is a security regression.

Ref: specs/ext-agent-reloaded.md §EAR-9, Sonnet v2 R-8
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from lib.injection_detector import InjectionDetector, ScanResult

_log = logging.getLogger("artha.soul_allowlist")

# ---------------------------------------------------------------------------
# Allowlisted phrase prefixes — these SOUL instruction patterns are known-safe
# and would otherwise produce injection false positives.
# Lowercase, stripped.  Match is prefix (startswith) after lower+strip.
# ---------------------------------------------------------------------------

SOUL_SAFE_PREFIXES: frozenset[str] = frozenset({
    "stop if",
    "refuse if",
    "do not",
    "i must not",
    "never fabricate",
    "only if",
    "always cite",
    "if you cannot",
    "do not guess",
    "if unsure",
    "flag when",
    "halt if",
    "report if",
    "note if",
    "skip if",
    "warn if",
    "say so explicitly",
    "state that",
    "acknowledge when",
    "must not",
    "should not",
    "never claim",
    "never produce",
})

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class PrincipleScanResult:
    principle: str
    """Original principle text."""

    allowed: bool
    """True if principle should be injected (either allowlisted or clean scan)."""

    allowlisted: bool
    """True if skipped due to allowlist (not scanned)."""

    scan_result: ScanResult | None
    """Full scan result if the principle was scanned; None if allowlisted."""

    principle_hash: str
    """SHA-256[:8] of principle for audit log correlation."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_detector = InjectionDetector()


def scan_principle(principle: str) -> PrincipleScanResult:
    """Scan a single SOUL principle for injection risk.

    Returns PrincipleScanResult indicating whether the principle is safe to
    inject.  Allowlisted prefixes bypass scanning and always pass.

    Never raises.
    """
    try:
        p_lower = principle.lower().strip()
        h = hashlib.sha256(principle.encode("utf-8", errors="replace")).hexdigest()[:8]

        # Allowlist short-circuit: any matching prefix → skip scan, pass through
        for prefix in SOUL_SAFE_PREFIXES:
            if p_lower.startswith(prefix):
                return PrincipleScanResult(
                    principle=principle,
                    allowed=True,
                    allowlisted=True,
                    scan_result=None,
                    principle_hash=h,
                )

        # Full injection scan
        scan = _detector.scan(principle)

        if scan.injection_detected:
            _log.warning(
                "SOUL principle blocked: hash=%s signals=%s",
                h,
                [s.signal_type for s in scan.signals],
            )
            return PrincipleScanResult(
                principle=principle,
                allowed=False,
                allowlisted=False,
                scan_result=scan,
                principle_hash=h,
            )

        return PrincipleScanResult(
            principle=principle,
            allowed=True,
            allowlisted=False,
            scan_result=scan,
            principle_hash=h,
        )

    except Exception as exc:  # noqa: BLE001
        # Fail-safe: any unexpected error → block the principle
        _log.error("soul_allowlist.scan_principle error: %s", exc)
        h = hashlib.sha256(principle.encode("utf-8", errors="replace")).hexdigest()[:8]
        return PrincipleScanResult(
            principle=principle,
            allowed=False,
            allowlisted=False,
            scan_result=None,
            principle_hash=h,
        )


def filter_principles(principles: list[str]) -> tuple[list[str], list[PrincipleScanResult]]:
    """Scan all principles, returning (allowed_list, all_scan_results).

    allowed_list: principles that passed (allowlisted or clean scan).
    all_scan_results: full audit record for every principle.
    """
    allowed: list[str] = []
    results: list[PrincipleScanResult] = []
    for p in principles:
        r = scan_principle(p)
        results.append(r)
        if r.allowed:
            allowed.append(p)
    return allowed, results
