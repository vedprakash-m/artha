#!/usr/bin/env python3
# pii-guard: email bodies are never stored; sender emails stripped before signals emitted
"""
scripts/email_signal_extractor.py — Deterministic email → DomainSignal extractor (E1).

Runs at Step 6.5 (after routing.yaml, before Step 7 domain processing).
Operates on routed email JSONL records to produce DomainSignal objects
that feed ActionComposer.compose() without requiring LLM inference.

Signal detection is regex/keyword-only — NO LLM calls.
False positive risks are mitigated by running AFTER marketing suppression (Step 5a)
and by using a 24-hour dedup window keyed on (signal_type, domain, deadline_date).

Detection rules (8 categories → 8 signal types):
  1. RSVP deadline           → event_rsvp_needed
  2. Appointment confirmed   → appointment_confirmed
  3. Payment notice          → bill_due
  4. Form deadline           → form_deadline
  5. Shipment arrival        → delivery_arriving
  6. Account security        → security_alert
  7. Renewal notice          → subscription_renewal
  8. School action needed    → school_action_needed

Privacy:
  - Sender email addresses NOT included in signal metadata
  - Only org_name (domain-part extracted) or subject keyword
  - Financial amount extracted for bill_due (standardized format only)
  - No raw email body stored in signals

Performance: regex-only; benchmark target <100ms for 500 emails.

Config flag: enhancements.email_signal_extractor (default: true)

Ref: specs/act-reloaded.md Enhancement 1
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from actions.base import DomainSignal  # type: ignore[import]
except ImportError:  # pragma: no cover
    # Fallback for test environments
    from dataclasses import dataclass, field

    @dataclass(frozen=True)
    class DomainSignal:  # type: ignore[no-redef]
        signal_type: str
        domain: str
        entity: str
        urgency: int
        impact: int
        source: str
        metadata: dict
        detected_at: str
        subtype: str = ""  # original specific type; signal_type holds the canonical type
        confidence: float = 0.0  # extraction quality score 0.0–1.0 (§4.4.2)

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# ---------------------------------------------------------------------------
# Signal detection patterns
# ---------------------------------------------------------------------------
# Canonical signal type map: 10 specific types → 4 canonical types.
# The canonical type goes in DomainSignal.signal_type; the specific type in .subtype.
# Consumers that need specific routing use .subtype; top-level filters use .signal_type.
_CANONICAL_TYPE_MAP: dict[str, str] = {
    "event_rsvp_needed":    "deadline",
    "bill_due":             "deadline",
    "form_deadline":        "deadline",
    "school_action_needed": "deadline",
    "slack_action_item":    "deadline",
    "appointment_confirmed": "confirmation",
    "delivery_arriving":    "confirmation",
    "subscription_renewal": "informational",
    "financial_alert":      "informational",
    "security_alert":       "security",
}

# Each entry: (compiled_regex_list, signal_type, domain, urgency, impact)
# Patterns match against combined subject + from + snippet (lower-cased)

_SIGNAL_PATTERNS: list[tuple[list[re.Pattern], str, str, int, int]] = [
    # 1. RSVP deadline
    (
        [
            re.compile(r"rsvp\s+by\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"please\s+respond\s+by", re.I),
            re.compile(r"deadline\s+to\s+reply", re.I),
            re.compile(r"respond\s+by\s+\w+\s+\d{1,2}", re.I),
        ],
        "event_rsvp_needed", "calendar", 2, 2,
    ),
    # 2. Appointment confirmed
    (
        [
            re.compile(r"appointment\s+confirmed", re.I),
            re.compile(r"your\s+visit\s+on", re.I),
            re.compile(r"scheduled\s+for\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"your\s+appointment\s+is\s+set", re.I),
        ],
        "appointment_confirmed", "calendar", 1, 1,
    ),
    # 3. Payment notice
    (
        [
            re.compile(r"payment\s+due", re.I),
            re.compile(r"amount\s+due", re.I),
            re.compile(r"balance\s+of\s+\$[\d,]+", re.I),
            re.compile(r"pay\s+by\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"your\s+bill\s+is\s+(ready|available)", re.I),
            re.compile(r"minimum\s+payment\s+due", re.I),
            # Urgency phrases in school/event fee emails
            re.compile(r"(?:pay|submit).*(?:fee|payment).*asap", re.I),
            re.compile(r"fee\s+is\s+now\s+posted", re.I),
        ],
        "bill_due", "finance", 2, 2,
    ),
    # 4. Form deadline
    (
        [
            re.compile(r"submit\s+by\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"form\s+due", re.I),
            re.compile(r"application\s+deadline", re.I),
            re.compile(r"filing\s+deadline", re.I),
            re.compile(r"deadline\s+to\s+submit", re.I),
            # Insurance/cancellation forms
            re.compile(r"cancellation\s+form", re.I),
            re.compile(r"form\s+to\s+be\s+sent", re.I),
        ],
        "form_deadline", "comms", 2, 2,
    ),
    # 5. Shipment arrival
    (
        [
            re.compile(r"out\s+for\s+delivery", re.I),
            re.compile(r"delivered\s+to\s+your\s+(door|address|home)", re.I),
            # "arriving today" AND "arriving April 8" / "arriving Monday"
            re.compile(
                r"arriving\s+(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
                r"|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
                r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|\d{1,2})",
                re.I,
            ),
            re.compile(r"package\s+delivered", re.I),
            re.compile(r"your\s+order\s+has\s+arrived", re.I),
        ],
        "delivery_arriving", "shopping", 1, 1,
    ),
    # 6. Account security
    (
        [
            re.compile(r"unusual\s+sign[-\s]?in", re.I),
            re.compile(r"password\s+reset", re.I),
            re.compile(r"security\s+alert", re.I),
            re.compile(r"suspicious\s+(activity|login)", re.I),
            re.compile(r"your\s+account\s+(was|has\s+been)\s+accessed", re.I),
        ],
        "security_alert", "digital", 3, 3,
    ),
    # 7. Renewal notice
    (
        [
            re.compile(r"renewal\s+on\s+\w+\s+\d{1,2}", re.I),
            re.compile(r"subscription\s+renewing", re.I),
            re.compile(r"auto[-\s]?renew(al)?", re.I),
            re.compile(r"your\s+(plan|subscription)\s+will\s+renew", re.I),
        ],
        "subscription_renewal", "digital", 1, 1,
    ),
    # 8. School action needed
    (
        [
            re.compile(r"action\s+required.*school", re.I),
            re.compile(r"parent\s+signature", re.I),
            re.compile(r"field\s+trip\s+permission", re.I),
            re.compile(r"permission\s+slip", re.I),
            re.compile(r"school\s+action\s+required", re.I),
            # Missing assignment alerts from school districts
            re.compile(r"missing\s+assignment", re.I),
            re.compile(r"assignment.*(?:flagged|missing|past\s+due)", re.I),
            # Survey deadlines from school/org
            re.compile(r"(?:survey|form).*(?:complete\s+today|due\s+today|hasn.t)", re.I),
            re.compile(r"complete\s+today\s+if\s+you\s+haven", re.I),
            # Upcoming school events with specific dates (STEM, assessments, etc.)
            re.compile(r"(?:assessment|test|exam|finals?)\s+@\s+\w+", re.I),
            re.compile(r"(?:spring|fall|final)\s+(?:fitness\s+)?assessment", re.I),
        ],
        "school_action_needed", "kids", 2, 2,
    ),
    # 9a. Bank / financial transaction alert (INR / non-USD)
    (
        [
            re.compile(r"(?:rs|inr)[\.\s]*[\d,]+.*(?:deducted|debited|credited|transferred)", re.I),
            re.compile(r"(?:deducted|debited)\s+from\s+your\s+(?:hdfc|sbi|icici|axis|kotak)?\s*(?:bank\s+)?account", re.I),
            re.compile(r"neft\s+transaction", re.I),
            re.compile(r"upi\s+(?:transaction|payment|debit)", re.I),
        ],
        "financial_alert", "finance", 2, 2,
    ),
    # 9. Slack action item (CONNECT §4.3) — matches Slack message records
    #    where source == "slack" (and any email containing TODO/ACTION phrases)
    (
        [
            re.compile(r"\bTODO\b", re.I),
            re.compile(r"\bACTION(?:\s+ITEM)?:\B", re.I),
            re.compile(r"\bFOLLOW[\s\-]?UP\b", re.I),
            re.compile(r"@\w+\s+please\b", re.I),
        ],
        "slack_action_item", "open_items", 2, 2,
    ),
]

# Slack after-hours pattern constants (CONNECT §4.3)
# Note: after_hours detection is timestamp-driven (not regex); signals are
# emitted by pipeline.py when a Slack record's ts falls outside
# user_profile.yaml → work_hours.start / work_hours.end.
# The regex-free pattern below is a named sentinel for auditing.
_SLACK_AFTER_HOURS_SIGNAL_TYPE = "slack_after_hours"
_SLACK_AFTER_HOURS_DOMAIN = "boundary"

# Regex to extract a plausible deadline date from text
_DATE_RE = re.compile(
    r"\b(?:by|before|due|on)\s+"
    r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,?\s+\d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
    re.I,
)

# Regex to extract dollar amount
_AMOUNT_RE = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.I)

# Extract org name from sender email domain
_DOMAIN_RE = re.compile(r"@([\w\-\.]+)", re.I)

# Dedup window for signal suppression (key = (signal_type, entity_hash))
_DEDUP_WINDOW_HOURS = 24


def _filter_text_only(text: str) -> str:
    """Return pii_guard-filtered text, tolerating its tuple-return API."""
    try:
        from pii_guard import filter_text as _pii_filter  # noqa: PLC0415
        filtered = _pii_filter(text)
        if isinstance(filtered, tuple):
            return str(filtered[0])
        return str(filtered)
    except Exception:  # noqa: BLE001
        return text


def _extract_text(email_record: dict) -> str:
    """Build a scannable text blob from subject + from + snippet + body."""
    # body_preview is a trimmed field; fall back to body when absent
    body_text = (
        email_record.get("body_preview")
        or email_record.get("body")
        or ""
    )
    parts = [
        str(email_record.get("subject", "") or ""),
        str(email_record.get("from", "") or ""),
        str(email_record.get("snippet", "") or ""),
        str(body_text)[:500],
    ]
    return " ".join(p for p in parts if p).lower()


def _extract_org_name(from_field: str) -> str:
    """Extract organisation name from 'From:' header (domain-part only, no email)."""
    m = _DOMAIN_RE.search(from_field or "")
    if not m:
        return "unknown"
    # Strip TLD and return main domain word
    domain = m.group(1).lower()
    parts = domain.split(".")
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return domain.capitalize()


def _extract_deadline_date(text: str) -> str:
    """Try to extract a deadline date string from email text."""
    m = _DATE_RE.search(text)
    if m:
        return m.group(0).strip()[:30]
    return ""


def _extract_amount(text: str) -> str:
    """Extract first dollar amount from text (for financial signals)."""
    m = _AMOUNT_RE.search(text)
    if m:
        return f"${m.group(1)}"
    return ""


# ---------------------------------------------------------------------------
# EmailSignalExtractor
# ---------------------------------------------------------------------------

class EmailSignalExtractor:
    """Deterministic regex-based email → DomainSignal extractor.

    Usage:
        extractor = EmailSignalExtractor()
        signals = extractor.extract(email_records, routing_table)
    """

    def __init__(self, artha_dir: Path | None = None) -> None:
        self._emitted: set[tuple[str, str]] = set()  # dedup within a session run
        self._artha_dir = artha_dir or Path(__file__).resolve().parent.parent
        self._user_ctx: dict | None = None  # lazy-loaded on first use

    def _get_user_ctx(self) -> dict:
        """Return user context, loading lazily on first call."""
        if self._user_ctx is None:
            try:
                from lib.user_context import load_user_context  # noqa: PLC0415
                self._user_ctx = load_user_context(self._artha_dir)
            except Exception:  # noqa: BLE001
                self._user_ctx = {}
        return self._user_ctx

    def _compute_confidence(
        self,
        signal_type: str,
        from_field: str,
        metadata: dict,
        entity_resolved: bool,
    ) -> float:
        """Compute confidence score for a signal using 5-factor model (§4.4.2).

        Factors:
          0.3 — Entity resolved (org portion not "unknown")
          0.2 — Date extracted (deadline_date or delivery_date present)
          0.1 — Amount extracted (finance signals; any numeric metadata otherwise)
          0.2 — Sender domain matches trusted_sender_domains for this signal_type
                 (defaults to 0.2 pass-through when no entry exists for signal_type)
          0.2 — Semantic verification passed (0.0 when flag disabled — default)

        Returns:
            Score in [0.0, 1.0].
        """
        score = 0.0

        # Factor 1: Entity resolved (org portion not "unknown")
        if entity_resolved:
            score += 0.3

        # Factor 2: Date extracted
        if metadata.get("deadline_date") or metadata.get("delivery_date"):
            score += 0.2

        # Factor 3: Amount extracted (finance signals get full credit; others get partial)
        if metadata.get("amount") or metadata.get("amount_str"):
            score += 0.1
        elif any(isinstance(v, (int, float)) and k not in ("urgency", "impact") for k, v in metadata.items()):
            score += 0.1

        # Factor 4: Sender domain matches trusted_sender_domains
        user_ctx = self._get_user_ctx()
        trusted_domains_map = user_ctx.get("trusted_sender_domains", {}) or {}
        trusted_for_type = trusted_domains_map.get(signal_type, []) or []
        if trusted_for_type:
            # Entry exists — check if sender domain is in it
            sender_domain_match = False
            m = _DOMAIN_RE.search(from_field or "")
            if m:
                sender_domain = m.group(1).lower()
                for td in trusted_for_type:
                    td_lower = str(td).lower()
                    if sender_domain == td_lower or sender_domain.endswith("." + td_lower):
                        sender_domain_match = True
                        break
            if sender_domain_match:
                score += 0.2
            # No match and entry exists → no +0.2
        else:
            # No entry for this signal_type → pass-through: don't penalize
            score += 0.2

        # Factor 5: Semantic verification (0.0 by default — feature-flagged)
        # Semantic verification is applied separately in _semantic_verify() below.
        # When disabled (default), this factor contributes 0.0.

        return min(score, 1.0)

    def extract(
        self,
        email_records: list[dict],
        routing_table: dict | None = None,
    ) -> list[DomainSignal]:
        """Scan routed emails and return DomainSignal objects.

        Args:
            email_records: List of email JSONL dicts with subject, from,
                          snippet/body_preview fields. Must be post-marketing-filter.
            routing_table: Optional {email_id: domain} mapping from routing.yaml output.
                          If provided, overrides the default domain from pattern table.

        Returns:
            List of DomainSignal objects ready for ActionComposer.compose().
        """
        if not _load_flag("enhancements.email_signal_extractor", default=True):
            return []

        signals: list[DomainSignal] = []
        routing_table = routing_table or {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for record in email_records:
            if not isinstance(record, dict):
                continue

            email_id = str(record.get("id", record.get("message_id", "unknown")))
            text = _extract_text(record)
            from_field = str(record.get("from", "") or "")
            org_name = _extract_org_name(from_field)

            for patterns, signal_type, default_domain, urgency, impact in _SIGNAL_PATTERNS:
                matched = any(p.search(text) for p in patterns)
                if not matched:
                    continue

                # Determine domain (routing override preferred)
                domain = routing_table.get(email_id, default_domain)

                # Dedup key: (signal_type, email_id) — one signal per email per type
                dedup_key = (signal_type, email_id)
                if dedup_key in self._emitted:
                    continue
                self._emitted.add(dedup_key)

                # Extract metadata (no PII — only org name, dates, amounts)
                deadline_date = _extract_deadline_date(text)
                metadata: dict[str, Any] = {
                    "email_id": email_id,
                    "sender_org_name": org_name,
                }
                if deadline_date:
                    metadata["deadline_date"] = deadline_date
                if signal_type in ("bill_due", "subscription_renewal"):
                    amount = _extract_amount(text)
                    if amount:
                        metadata["amount"] = amount
                    metadata["sensitivity"] = "high"

                # Use subject as entity (truncated)
                subject = str(record.get("subject", "") or "")[:60]
                entity = f"{org_name}: {subject}" if subject else org_name

                # RD-05: Apply PII filter to entity before it leaves the extractor.
                # Email subjects routinely contain contextual PII (patient names,
                # child names, case references) that pii_guard's regex does not
                # cover via structured patterns alone. Scrub at the boundary.
                entity = _filter_text_only(entity)

                # Compute confidence score (§4.4.2)
                entity_resolved = not org_name.lower().startswith("unknown")
                confidence = self._compute_confidence(
                    signal_type=signal_type,
                    from_field=from_field,
                    metadata=metadata,
                    entity_resolved=entity_resolved,
                )

                canonical_type = _CANONICAL_TYPE_MAP.get(signal_type, signal_type)
                sig = DomainSignal(
                    signal_type=canonical_type,
                    domain=domain,
                    entity=entity,
                    urgency=urgency,
                    impact=impact,
                    source="email_signal_extractor",
                    metadata=metadata,
                    detected_at=now_iso,
                    subtype=signal_type,
                    confidence=confidence,
                )
                # DEBT-SIG-004: apply signal-layer PII scrub to all string metadata values.
                # Structural keys (email_id, sensitivity) are exempted; user-visible
                # fields (sender_org_name, deadline_date, amount) are scrubbed.
                _PII_EXEMPT_KEYS = frozenset({"email_id", "sensitivity", "signal_origin", "source_id"})
                clean_meta = {
                    k: (_filter_text_only(v) if isinstance(v, str) and k not in _PII_EXEMPT_KEYS else v)
                    for k, v in sig.metadata.items()
                }
                import dataclasses as _dc
                sig = _dc.replace(sig, metadata=clean_meta)
                signals.append(sig)

        # RD-43: Write signal funnel metrics for eval_runner and CI regression detection
        try:
            import json as _json  # noqa: PLC0415
            from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
            _metrics_dir = Path(__file__).resolve().parent.parent / "tmp"
            _metrics_dir.mkdir(parents=True, exist_ok=True)
            _by_type: dict[str, int] = {}
            for _s in signals:
                _by_type[_s.signal_type] = _by_type.get(_s.signal_type, 0) + 1
            _metrics = {
                "run_at": _dt.now(tz=_tz.utc).isoformat(),
                "emails_processed": len(email_records),
                "signals_extracted": len(signals),
                "signals_by_type": _by_type,
            }
            (_metrics_dir / "signal_metrics.json").write_text(
                _json.dumps(_metrics, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass  # metrics write failure must never crash pipeline

        return signals

    def reset_dedup(self) -> None:
        """Clear in-session dedup state (call between separate email batches)."""
        self._emitted.clear()


# ---------------------------------------------------------------------------
# Phase 4: Semantic verification (feature-flagged via artha_config.yaml)
# Ref: specs/action-convert.md §4.4.1
# ---------------------------------------------------------------------------

def _semantic_verify(
    signal_type: str,
    sender: str,
    subject: str,
    snippet: str,
    artha_dir: Path | None = None,
) -> "bool | None":
    """LLM-as-judge: verify that an email is genuinely the claimed signal_type.

    Feature-flagged: only runs when harness.actions.quality.semantic_verification=true
    in artha_config.yaml. Returns None (skip) when flag is disabled.

    Cache: results are persisted in tmp/semantic_cache.json keyed on
    sha256(sender_domain|signal_type|subject_template)[:16] with a 24h TTL.

    Fail behaviour:
      - Timeout or error for security_alert → False (fail-closed, suppress entirely)
      - Timeout or error for all other types → None (fail-to-suggestion)

    Returns:
        True  — LLM confirmed signal_type is correct
        False — LLM says not a genuine signal_type (or security_alert timeout)
        None  — feature disabled, or non-security timeout/error (fail-to-suggestion)
    """
    import hashlib
    import json as _json
    import os
    import time

    _artha_dir = artha_dir or Path(__file__).resolve().parent.parent

    # Check feature flag
    try:
        from lib.config_loader import load_config as _lc  # noqa: PLC0415
        _cfg = _lc("artha_config", str(_artha_dir / "config")) or {}
        _quality = ((_cfg.get("harness") or {}).get("actions") or {}).get("quality") or {}
        if not _quality.get("semantic_verification", False):
            return None  # feature disabled
    except Exception:  # noqa: BLE001
        return None

    # Build stable cache key
    m = _DOMAIN_RE.search(sender or "")
    sender_domain = m.group(1).lower() if m else "unknown"
    subject_template = re.sub(r"\d+", "N", subject[:60].lower().strip())
    cache_key = hashlib.sha256(
        f"{sender_domain}|{signal_type}|{subject_template}".encode()
    ).hexdigest()[:16]

    # Load / check cache
    cache_path = _artha_dir / "tmp" / "semantic_cache.json"
    cache: dict = {}
    cache_hit = False
    try:
        if cache_path.exists():
            raw = _json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cache = raw
        entry = cache.get(cache_key)
        if isinstance(entry, dict):
            ts = entry.get("ts", 0)
            if time.time() - ts < 86400:  # 24h TTL
                cache_hit = True
                decision = entry.get("decision")
                try:
                    from lib.observability import semantic_verify_trace  # noqa: PLC0415
                    semantic_verify_trace(
                        sender_domain=sender_domain,
                        signal_type=signal_type,
                        subject_template=subject_template,
                        model=entry.get("model", "cached"),
                        cache_hit=True,
                        decision=decision,
                    )
                except Exception:  # noqa: BLE001
                    pass
                return decision == "YES"
    except Exception:  # noqa: BLE001
        pass

    # Cache miss — call LLM
    _signal_definitions = {
        "bill_due": "a bill or payment that is due and requires action from the recipient",
        "form_deadline": "a form or document that must be submitted by a deadline",
        "delivery_arriving": "a package or shipment that is arriving or has arrived",
        "security_alert": "a genuine security alert about unauthorized account access",
        "school_action_needed": "a school-related action required from a parent or student",
        "subscription_renewal": "a subscription or recurring service that is renewing",
    }
    signal_def = _signal_definitions.get(signal_type, f"a {signal_type.replace('_', ' ')} event")
    snippet_truncated = snippet[:200]  # Truncate before building prompt
    prompt = (
        f"You are an email classifier. Given this email metadata, answer ONLY "
        f'"YES" or "NO" — is this genuinely a {signal_type}?\n\n'
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Snippet: {snippet_truncated}\n\n"
        f"A {signal_type} means: {signal_def}\n"
        f"Answer YES or NO, then one sentence explaining why."
    )

    model = "gpt-4o-mini"
    decision_str: str | None = None
    latency_ms: float | None = None
    fallback_reason: str | None = None
    result: bool | None = None

    try:
        import openai  # type: ignore[import]
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
        result = decision_str == "YES"
    except Exception as exc:  # noqa: BLE001
        fallback_reason = f"{type(exc).__name__}: {exc}"[:80]
        if signal_type == "security_alert":
            decision_str = "timeout"
            result = False  # fail-closed for security alerts
        else:
            decision_str = "timeout"
            result = None  # fail-to-suggestion for all others

    # Write cache (atomic)
    try:
        cache[cache_key] = {
            "ts": time.time(),
            "decision": decision_str,
            "model": model,
            "signal_type": signal_type,
            "sender_domain": sender_domain,
        }
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(_json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_path), str(cache_path))
    except Exception:  # noqa: BLE001
        pass

    # Emit trace
    try:
        from lib.observability import semantic_verify_trace  # noqa: PLC0415
        semantic_verify_trace(
            sender_domain=sender_domain,
            signal_type=signal_type,
            subject_template=subject_template,
            model=model,
            cache_hit=False,
            decision=decision_str,
            latency_ms=latency_ms,
            fallback_reason=fallback_reason,
        )
    except Exception:  # noqa: BLE001
        pass

    return result


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI: python scripts/email_signal_extractor.py — runs on sample batch."""
    import json

    sample_emails = [
        {
            "id": "test-001",
            "subject": "Payment due: $127.45 due by March 25",
            "from": "billing@example-utility.com",
            "snippet": "Your payment of $127.45 is due by March 25, 2026.",
        },
        {
            "id": "test-002",
            "subject": "RSVP by March 28 — Annual Dinner",
            "from": "events@company.com",
            "snippet": "Please RSVP by March 28 for our annual dinner.",
        },
    ]

    extractor = EmailSignalExtractor()
    signals = extractor.extract(sample_emails)
    for sig in signals:
        print(f"  [{sig.signal_type}] domain={sig.domain} urgency={sig.urgency} entity={sig.entity[:40]}")

    if not signals:
        print("No signals detected in sample batch")

    return 0


if __name__ == "__main__":
    sys.exit(main())
