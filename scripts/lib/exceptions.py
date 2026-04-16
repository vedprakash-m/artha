"""
exceptions.py — Artha typed exception hierarchy
=================================================
Typed exceptions allow callers to distinguish recoverable failure modes from
programming bugs. All exceptions here are non-fatal by design — each maps to
a defined degradation level in the Formal Degradation Hierarchy.

RD-51: LLMUnavailableError (Level 3 — LLM-Unavailable degradation)
"""

from __future__ import annotations


class ArthaError(RuntimeError):
    """Base class for all typed Artha runtime errors."""


class LLMUnavailableError(ArthaError):
    """Raised when the LLM subprocess is unavailable or returns a fatal error.

    Maps to Formal Degradation Hierarchy Level 3 (LLM-Unavailable).
    Callers must catch this and return a deterministic partial response rather
    than allowing a Python traceback to surface to the user.

    Attributes:
        reason: Short causal string (e.g. "subprocess_timeout",
                "claude_binary_not_found", "non_zero_exit").
        last_exit_code: Process exit code, or -1 for non-process errors.
    """

    def __init__(self, reason: str, last_exit_code: int = -1) -> None:
        super().__init__(
            f"LLM unavailable: {reason} (exit {last_exit_code})"
        )
        self.reason = reason
        self.last_exit_code = last_exit_code


class VaultAccessRequired(ArthaError):
    """Raised when a vault-protected domain is accessed without decryption.

    Maps to Formal Degradation Hierarchy Level 1 (Vault-Locked).
    """

    def __init__(self, domain: str) -> None:
        super().__init__(
            f"Domain '{domain}' requires vault decryption. "
            "Run: python scripts/vault.py decrypt"
        )
        self.domain = domain


class IsolationViolation(ArthaError):
    """Raised when an agent writes outside its permitted path scope.

    Used by the Work OS isolation check (RD-36).
    """
