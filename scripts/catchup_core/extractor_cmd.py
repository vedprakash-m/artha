from __future__ import annotations

import json
import os
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


def _try_gemini(prompt: str) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=os.environ.get("ARTHA_EXTRACTOR_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return str(resp.text)
    except Exception as exc:
        print(f"gemini extraction failed: {exc}", file=sys.stderr)
        return None


def _try_openai(prompt: str) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.environ.get("ARTHA_EXTRACTOR_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content
    except Exception as exc:
        print(f"openai extraction failed: {exc}", file=sys.stderr)
        return None


def _align_candidates(candidates: list[dict[str, Any]], message: str,
                      subjects: list[str], predicates: list[str]) -> list[dict[str, Any]]:
    """Ensure candidate quotes match message text spans and adhere to allowed vocabulary."""
    valid: list[dict[str, Any]] = []
    allowed_subjs = set(subjects)
    allowed_preds = set(predicates)

    for c in candidates:
        if not isinstance(c, dict):
            continue
        pred = str(c.get("predicate", ""))
        subj = str(c.get("subject_id", ""))
        quote = c.get("quote")
        val = c.get("value")

        if not quote or not isinstance(quote, str) or not val:
            continue
        if allowed_preds and pred not in allowed_preds:
            continue
        if allowed_subjs and subj not in allowed_subjs:
            continue

        # Verify or fix quote span in message
        span = c.get("span")
        if isinstance(span, list) and len(span) == 2 and 0 <= span[0] < span[1] <= len(message):
            if message[span[0]:span[1]] != quote:
                idx = message.find(quote)
                if idx >= 0:
                    span = [idx, idx + len(quote)]
                else:
                    continue
        else:
            idx = message.find(quote)
            if idx >= 0:
                span = [idx, idx + len(quote)]
            else:
                continue

        c["span"] = span
        valid.append(c)
    return valid


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


def _match_date_predicate(pre_ctx: str, predicates: list[str]) -> str | None:
    rules = [
        (("bill", "statement", "payment due", "amount due"), "bill_due_date"),
        (("mortgage", "loan payment"), "mortgage_due_date"),
        (("property tax", "county tax"), "property_tax_due"),
        (("refill", "prescription", "pharmacy"), "prescription_refill_date"),
        (("rfe", "evidence requested"), "rfe_deadline"),
        (("ead", "employment auth"), "ead_expiry"),
        (("i-94", "i94"), "i94_expiry"),
        (("registration", "register", "deadline", "closes", "due"), "exam_registration_deadline"),
        (("test", "exam", "sat", "act", "psat", "ap "), "test_date"),
    ]
    for keywords, pred in rules:
        if any(w in pre_ctx for w in keywords):
            return pred if (pred in predicates or not predicates) else None
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
    default_subj = subjects[0] if subjects else "owner"
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

    # 1. Try LLM providers
    raw_llm = _try_gemini(prompt) or _try_openai(prompt)
    if raw_llm:
        try:
            parsed = json.loads(raw_llm)
            cands = parsed.get("candidates") if isinstance(parsed, dict) else None
            if isinstance(cands, list):
                aligned = _align_candidates(cands, message, subjects, predicates)
                sys.stdout.write(json.dumps({"candidates": aligned}))
                return 0
        except Exception as exc:
            print(f"error processing LLM output: {exc}", file=sys.stderr)

    # 2. Rule-based fallback
    cands = _extract_rule_based(message, subjects, predicates)
    sys.stdout.write(json.dumps({"candidates": cands}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
