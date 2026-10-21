from __future__ import annotations

import json
import re
import sys
from typing import Any


def _parse_prompt(prompt: str) -> tuple[list[str], list[str], str]:
    """Parse allowed subjects, predicates, and untrusted message from prompt."""
    subjects: list[str] = []
    predicates: list[str] = []
    message = ""

    # Parse subjects
    subj_m = re.search(r"Allowed subjects:\s*([^\n]+)", prompt)
    if subj_m:
        subjects = [s.strip() for s in subj_m.group(1).split(",") if s.strip()]

    # Parse predicates
    preds_m = re.search(r"Allowed predicates[^\n]*\n(.*?)(?=\nSchema:|\n<untrusted>)", prompt, re.DOTALL)
    if preds_m:
        for raw_line in preds_m.group(1).splitlines():
            line = raw_line.strip()
            if line.startswith("-"):
                pred_name = line[1:].split(":")[0].strip()
                if pred_name:
                    predicates.append(pred_name)

    # Parse untrusted message block
    msg_m = re.findall(r"<untrusted>\s*\n(.*?)\n\s*</untrusted>", prompt, re.DOTALL)
    if msg_m:
        message = msg_m[-1]

    return subjects, predicates, message


def _extract_datetimes(message: str, default_subj: str, predicates: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    dt_pred = None
    for p in ("appointment_time", "doctor_appointment", "biometrics_appointment"):
        if p in predicates or not predicates:
            dt_pred = p
            break
    if dt_pred:
        for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})\b", message):
            quote = m.group(1)
            candidates.append({
                "predicate": dt_pred,
                "value": quote,
                "subject_id": default_subj,
                "quote": quote,
                "span": [m.start(), m.end()],
                "confidence": 1.0,
            })
    return candidates


_DATE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("bill", "statement", "payment due", "amount due"), "bill_due_date"),
    (("mortgage", "loan payment"), "mortgage_due_date"),
    (("property tax", "county tax"), "property_tax_due"),
    (("refill", "prescription", "pharmacy"), "prescription_refill_date"),
    (("rfe", "evidence requested"), "rfe_deadline"),
    (("ead", "employment auth"), "ead_expiry"),
    (("i-94", "i94"), "i94_expiry"),
    (("registration", "register", "deadline", "closes", "due"), "exam_registration_deadline"),
    (("test", "exam", "sat", "act", "psat", "ap"), "test_date"),
)


def _has_word(context: str, keyword: str) -> bool:
    """Whole-word match: 'act' must not match 'contact', 'ead' must not match 'deadline'."""
    return re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", context) is not None


def _match_date_predicate(pre_ctx: str, predicates: list[str]) -> str | None:
    """First rule whose keyword appears as a word *and* whose predicate the pack declares."""
    for keywords, pred in _DATE_RULES:
        if (pred in predicates or not predicates) and any(_has_word(pre_ctx, w) for w in keywords):
            return pred
    return None


def _extract_dates(message: str, default_subj: str, predicates: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", message):
        date_str = m.group(1)
        pre_ctx = message[max(0, m.start() - 60):m.start()].lower()
        pred = _match_date_predicate(pre_ctx, predicates)
        if pred:
            candidates.append({
                "predicate": pred,
                "value": date_str,
                "subject_id": default_subj,
                "quote": date_str,
                "span": [m.start(), m.end()],
                "confidence": 0.95,
            })
    return candidates


def _extract_enums(message: str, default_subj: str, predicates: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    enum_configs = [
        ("accommodation_status", r"\baccommodation(?: status)?[:\s]+({val})\b",
         ("approved", "requested", "expired", "none")),
        ("case_status", r"\b(?:case|petition|application)(?: status)?[:\s]+({val})\b",
         ("rfe_issued", "approved", "denied", "pending", "received")),
        ("autopay_status", r"\bautopay(?: status)?[:\s]+({val})\b",
         ("enrolled", "failed", "paused", "inactive")),
        ("lab_result_status", r"\blab(?: result)?(?: status)?[:\s]+({val})\b",
         ("abnormal", "normal", "pending")),
    ]
    for pred, pattern, values in enum_configs:
        if pred in predicates or not predicates:
            for val in values:
                regex_val = val.replace("_", " ")
                m = re.search(pattern.format(val=regex_val), message, re.IGNORECASE)
                if m:
                    candidates.append({
                        "predicate": pred,
                        "value": val,
                        "subject_id": default_subj,
                        "quote": m.group(0),
                        "span": [m.start(), m.end()],
                        "confidence": 0.98,
                    })
    return candidates


def _extract_rule_based(message: str, subjects: list[str], predicates: list[str]) -> list[dict[str, Any]]:
    """Deterministic extractor for ISO datetimes, dates, and common household predicates."""
    named = [subject for subject in subjects if re.search(r"\b" + re.escape(subject) + r"\b", message)]
    if len(named) == 1:
        default_subj = named[0]
    elif len(subjects) == 1:
        default_subj = subjects[0]
    else:
        return []
    return (
        _extract_datetimes(message, default_subj, predicates)
        + _extract_dates(message, default_subj, predicates)
        + _extract_enums(message, default_subj, predicates)
    )


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.stdout.write(json.dumps({"candidates": []}))
        return 0

    subjects, predicates, message = _parse_prompt(prompt)

    cands = _extract_rule_based(message, subjects, predicates)
    sys.stdout.write(json.dumps({"candidates": cands, "complete": False,
        "reason": "bounded pattern extraction needs assistant review of the remaining text"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
