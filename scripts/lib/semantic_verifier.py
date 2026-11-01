"""scripts/lib/semantic_verifier.py — LLM judge for signal verification (§40.8).

Isolated here so that signal-path modules (email_signal_extractor.py,
pattern_engine.py, actions/base.py) satisfy the RD-19 / no-LLM-in-signal-path
invariant while still being able to call the verifier via a lazy import.

The openai import lives ONLY in this file — never in the signal path modules.
"""

from __future__ import annotations


def call_llm_verifier(
    prompt: str,
    model: str,
    signal_type: str,
) -> tuple[str | None, float | None, str | None]:
    """Call the LLM and return (decision_str, latency_ms, fallback_reason).

    decision_str: "YES" | "NO" | "timeout" | None
    latency_ms:   elapsed milliseconds, or None on error
    fallback_reason: exception description on error, or None on success
    """
    import time  # noqa: PLC0415

    decision_str: str | None = None
    latency_ms: float | None = None
    fallback_reason: str | None = None

    try:
        import openai  # type: ignore[import]  # noqa: PLC0415

        t0 = time.time()
        resp = openai.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            timeout=5,
        )
        latency_ms = (time.time() - t0) * 1000
        raw_text = resp.choices[0].message.content or ""
        decision_str = "YES" if raw_text.strip().upper().startswith("YES") else "NO"
    except Exception as exc:  # noqa: BLE001
        fallback_reason = f"{type(exc).__name__}: {exc}"[:80]
        decision_str = "timeout"
        # Caller applies fail-closed (security_alert) vs fail-to-suggestion logic.

    return decision_str, latency_ms, fallback_reason
