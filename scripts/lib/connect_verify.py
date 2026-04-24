"""
connect_verify.py — S-06: Claim verification for Connect drafts.

Extracts claims from draft text, verifies them against WorkIQ,
and caches results for the session (in-memory only, never persisted).
Max 10 claims per session. Timeout = NOT VERIFIED.
"""
from __future__ import annotations

import re
import threading
from typing import Callable

# Verdict constants
CONFIRMED = "CONFIRMED"
PARTIAL = "PARTIAL"
NOT_VERIFIED = "NOT VERIFIED"

_MAX_CLAIMS_PER_SESSION = 10

# Sentence-boundary splitter: split on ". ", "! ", "? " and common list bullets
_SENTENCE_RE = re.compile(
    r"(?<=[.!?])\s+|(?:^|\n)\s*[-*•]\s*",
)


def extract_claims(draft_text: str) -> list[str]:
    """
    Extract verifiable claims from a draft text.

    Splits on sentence boundaries and bullet points, returning
    non-empty, deduplicated strings of at least 10 characters.
    """
    if not draft_text:
        return []

    parts = _SENTENCE_RE.split(draft_text)
    seen: set[str] = set()
    claims: list[str] = []
    for part in parts:
        stripped = part.strip().rstrip(".")
        if len(stripped) >= 10 and stripped not in seen:
            seen.add(stripped)
            claims.append(stripped)
    return claims


def verify_claim(
    claim: str,
    workiq_fn: Callable[[str], str],
    timeout_s: int = 30,
) -> str:
    """
    Verify a single claim using workiq_fn with a timeout.

    Returns:
        CONFIRMED   — workiq_fn returned a non-empty, non-partial result
        PARTIAL     — workiq_fn returned a partial/uncertain result
        NOT VERIFIED — timeout, error, or no evidence found
    """
    result_holder: list[str] = []
    exc_holder: list[BaseException] = []

    def _run() -> None:
        try:
            result_holder.append(workiq_fn(claim))
        except Exception as exc:  # noqa: BLE001
            exc_holder.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s if timeout_s > 0 else 0.001)

    if thread.is_alive() or exc_holder:
        return NOT_VERIFIED

    if not result_holder:
        return NOT_VERIFIED

    verdict_raw = (result_holder[0] or "").strip().lower()
    if not verdict_raw:
        return NOT_VERIFIED
    if "partial" in verdict_raw or "uncertain" in verdict_raw or "insufficient" in verdict_raw:
        return PARTIAL
    return CONFIRMED


class ClaimVerifier:
    """
    Session-scoped claim verifier with in-memory cache and claim budget.

    A single instance should be used per Artha session. Results are
    never persisted to disk.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._count: int = 0

    def verify(
        self,
        claim: str,
        workiq_fn: Callable[[str], str],
        timeout_s: int = 30,
    ) -> str:
        """
        Verify a claim, using the in-memory cache to avoid re-verification.

        Returns NOT VERIFIED without calling workiq_fn if:
        - Already at MAX_CLAIMS_PER_SESSION unique verifications
        """
        if claim in self._cache:
            return self._cache[claim]

        if self._count >= _MAX_CLAIMS_PER_SESSION:
            return NOT_VERIFIED

        verdict = verify_claim(claim, workiq_fn, timeout_s=timeout_s)
        self._cache[claim] = verdict
        self._count += 1
        return verdict
