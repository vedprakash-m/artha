"""
scripts/schemas/bridge_schemas.py — Bridge artifact JSON schemas and validators.

Implements §14.4 (Bridge Artifact Schemas) and §9.8 (Enforcement & Test Matrix).
Bridge artifacts are the ONLY cross-surface communication channel between
the work OS and the personal OS. Schema validation is mandatory on every write.

Schema version: v1
Schemas:
  PERSONAL_SCHEDULE_MASK_SCHEMA  — personal → work bridge (§9.3)
  WORK_LOAD_PULSE_SCHEMA         — work → personal bridge (§9.3)

Usage:
  from scripts.schemas.bridge_schemas import validate_bridge_artifact, BridgeValidationError
  validate_bridge_artifact("personal_schedule_mask", artifact_dict)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# § 9.3 + § 14.4: Allowed field sets — zero tolerance for prohibited fields
# ---------------------------------------------------------------------------

#: Fields explicitly PROHIBITED from appearing in any bridge artifact.
#: These are the "toxic" fields that would enable cross-surface data leakage.
PROHIBITED_FIELDS = frozenset({
    # Meeting / event content
    "title", "subject", "summary", "body", "description", "notes",
    # People
    "attendees", "organizer", "sender", "recipient", "people", "person",
    "name", "email", "alias",
    # Work-specific content that must never reach personal surface
    "meeting_names", "project", "projects", "messages", "message",
    "channel", "thread", "comment",
    # Location / travel
    "location",
})

# ---------------------------------------------------------------------------
# personal_schedule_mask.json — personal → work bridge
# ---------------------------------------------------------------------------

PERSONAL_SCHEDULE_MASK_SCHEMA: dict[str, Any] = {
    "$schema": "artha/bridge/personal_schedule_mask/v1",
    "type": "object",
    "required": ["$schema", "generated_at", "date", "blocks"],
    "additionalProperties": False,
    "properties": {
        "$schema": {"type": "string", "const": "artha/bridge/personal_schedule_mask/v1"},
        "generated_at": {"type": "string"},   # ISO-8601 datetime
        "date": {"type": "string"},            # YYYY-MM-DD
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["busy_start", "busy_end", "type"],
                "additionalProperties": False,
                "properties": {
                    "busy_start": {"type": "string"},  # HH:MM
                    "busy_end":   {"type": "string"},  # HH:MM
                    "type": {"type": "string", "enum": ["hard", "soft"]},
                },
            },
        },
    },
}

# ---------------------------------------------------------------------------
# work_load_pulse.json — work → personal bridge
# v1.0 fields: numeric metrics only
# v1.1 fields (optional): phase, advisory — semantic context, PII-sanitized
# ---------------------------------------------------------------------------

#: Valid phase values for the work_load_pulse bridge (§9.3, v1.1)
WORK_LOAD_PULSE_PHASES = frozenset({
    "normal",
    "sprint_deadline",
    "connect_submission",
    "promo_season",
    "offboarding_prep",
})

#: Max length for the advisory field — enforced at write time (§9.3, v1.1)
_ADVISORY_MAX_LEN = 100

WORK_LOAD_PULSE_SCHEMA: dict[str, Any] = {
    "$schema": "artha/bridge/work_load_pulse/v1",
    "type": "object",
    "required": ["$schema", "generated_at", "date",
                 "total_meeting_hours", "after_hours_count",
                 "boundary_score", "focus_availability_score"],
    "additionalProperties": False,
    "properties": {
        "$schema":  {"type": "string", "const": "artha/bridge/work_load_pulse/v1"},
        "generated_at": {"type": "string"},
        "date": {"type": "string"},
        "total_meeting_hours":     {"type": "number", "minimum": 0},
        "after_hours_count":       {"type": "integer", "minimum": 0},
        "boundary_score":          {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "focus_availability_score":{"type": "number", "minimum": 0.0, "maximum": 1.0},
        # v1.1 optional semantic fields — PII-sanitized before write; max 100 chars
        "phase":    {"type": "string"},   # enum enforced programmatically (not in schema)
        "advisory": {"type": "string"},   # max 100 chars, validated in write_bridge_artifact
    },
}

_SCHEMAS: dict[str, dict[str, Any]] = {
    "personal_schedule_mask": PERSONAL_SCHEDULE_MASK_SCHEMA,
    "work_load_pulse": WORK_LOAD_PULSE_SCHEMA,
}

_SCHEMA_ROOTS: dict[str, str] = {
    "personal_schedule_mask": "artha/bridge/personal_schedule_mask/v1",
    "work_load_pulse": "artha/bridge/work_load_pulse/v1",
}

# ---------------------------------------------------------------------------
# §9.3 v1.1 — Advisory field PII sanitizer
# ---------------------------------------------------------------------------

#: Patterns that must never appear in the advisory field.
#: The advisory is written by Work OS and read by personal OS — it must
#: carry no PII, no project names, no meeting titles, no people names.
_ADVISORY_PII_PATTERNS = [
    re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"),        # likely full name (TitleCase TitleCase)
    re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]+\b", re.IGNORECASE),  # email address
]


def sanitize_advisory(text: str, redact_keywords: list[str] | None = None) -> str:
    """Sanitize the advisory string before writing to the bridge artifact.

    Enforces:
    - Max length (100 chars, hard truncate)
    - No email addresses
    - No strings matching redact_keywords (project names, people aliases)
    - No pattern that looks like a full name (TitleCase TitleCase)

    Returns the sanitized advisory string, or an empty string if the input
    is empty or cannot be safely sanitized.

    This is a best-effort sanitizer, not a guarantee. The advisory must be
    authored by automated code (not LLM output) to have meaningful PII safety.
    """
    if not text:
        return ""
    out = text.strip()
    # Strip email-like patterns
    for pat in _ADVISORY_PII_PATTERNS:
        out = pat.sub("[redacted]", out)
    # Strip redact_keywords from profile
    if redact_keywords:
        for kw in redact_keywords:
            if kw and len(kw) > 2:
                out = re.sub(re.escape(kw), "[redacted]", out, flags=re.IGNORECASE)
    # Hard truncate to 100 chars
    if len(out) > _ADVISORY_MAX_LEN:
        out = out[:_ADVISORY_MAX_LEN - 1] + "…"
    return out

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class BridgeValidationError(ValueError):
    """Raised when a bridge artifact fails schema validation."""


class ProhibitedFieldError(BridgeValidationError):
    """Raised when a bridge artifact contains a prohibited field."""


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _check_prohibited_fields(obj: Any, path: str = "") -> None:
    """Recursively scan obj for any prohibited field names."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            fq = f"{path}.{key}" if path else key
            if key.lower() in PROHIBITED_FIELDS:
                raise ProhibitedFieldError(
                    f"Prohibited field '{fq}' found in bridge artifact. "
                    "Bridge artifacts must never contain meeting titles, attendees, "
                    "names, projects, or message content. See §9.3."
                )
            _check_prohibited_fields(value, fq)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _check_prohibited_fields(item, f"{path}[{i}]")


def _validate_required_fields(obj: dict, schema: dict, path: str = "") -> None:
    """Validate required fields and types (lightweight — no jsonschema dependency)."""
    required = schema.get("required", [])
    for field in required:
        if field not in obj:
            raise BridgeValidationError(
                f"Required field '{path}.{field}' missing from bridge artifact."
            )

    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties", {}).keys())
        for key in obj:
            if key not in allowed:
                raise BridgeValidationError(
                    f"Unexpected field '{path}.{key}' in bridge artifact. "
                    f"Allowed fields: {sorted(allowed)}"
                )

    for field, sub_schema in schema.get("properties", {}).items():
        if field not in obj:
            continue
        val = obj[field]
        expected_type = sub_schema.get("type")
        # type check
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "array": list,
            "object": dict,
            "boolean": bool,
        }
        if expected_type and expected_type in type_map:
            if not isinstance(val, type_map[expected_type]):
                raise BridgeValidationError(
                    f"Field '{path}.{field}' has wrong type. "
                    f"Expected {expected_type}, got {type(val).__name__}."
                )
        # const check
        if "const" in sub_schema and val != sub_schema["const"]:
            raise BridgeValidationError(
                f"Field '{path}.{field}' must equal '{sub_schema['const']}', got '{val}'."
            )
        # enum check
        if "enum" in sub_schema and val not in sub_schema["enum"]:
            raise BridgeValidationError(
                f"Field '{path}.{field}' must be one of {sub_schema['enum']}, got '{val}'."
            )
        # range checks
        if "minimum" in sub_schema and isinstance(val, (int, float)):
            if val < sub_schema["minimum"]:
                raise BridgeValidationError(
                    f"Field '{path}.{field}' = {val} is below minimum {sub_schema['minimum']}."
                )
        if "maximum" in sub_schema and isinstance(val, (int, float)):
            if val > sub_schema["maximum"]:
                raise BridgeValidationError(
                    f"Field '{path}.{field}' = {val} exceeds maximum {sub_schema['maximum']}."
                )
        # recurse into arrays and objects
        if expected_type == "array" and "items" in sub_schema:
            for i, item in enumerate(val):
                _validate_required_fields(item, sub_schema["items"], f"{path}.{field}[{i}]")
        elif expected_type == "object":
            _validate_required_fields(val, sub_schema, f"{path}.{field}")


def validate_bridge_artifact(artifact_type: str, artifact: dict) -> None:
    """
    Validate a bridge artifact dict against its schema.

    Raises BridgeValidationError on any violation.
    This function MUST be called before writing any bridge artifact to disk.

    Args:
        artifact_type: "personal_schedule_mask" or "work_load_pulse"
        artifact:      The dict to validate.

    Raises:
        BridgeValidationError: on schema violation or prohibited field.
        ValueError: if artifact_type is unrecognized.
    """
    if artifact_type not in _SCHEMAS:
        raise ValueError(
            f"Unknown bridge artifact type '{artifact_type}'. "
            f"Valid types: {list(_SCHEMAS.keys())}"
        )
    schema = _SCHEMAS[artifact_type]

    # 1. Prohibited field scan (runs first — highest priority check)
    _check_prohibited_fields(artifact)

    # 2. Required fields, types, ranges
    _validate_required_fields(artifact, schema, artifact_type)


def write_bridge_artifact(
    path: Path,
    artifact_type: str,
    artifact: dict,
    phase: str | None = None,
    advisory: str | None = None,
    redact_keywords: list[str] | None = None,
) -> None:
    """
    Atomically write a validated bridge artifact to disk. (§8.7 atomicity rule)

    Validates the artifact first. If validation fails, the existing file is
    preserved unchanged. On success, writes to a temp file then renames.

    v1.1 optional fields: phase and advisory are populated only for
    work_load_pulse artifacts when provided.  advisory is PII-sanitized
    before write using sanitize_advisory().

    Args:
        path:          Destination path (e.g. state/bridge/work_load_pulse.json)
        artifact_type: "personal_schedule_mask" or "work_load_pulse"
        artifact:      The dict to serialize and write.
        phase:         Optional work context phase (v1.1, work_load_pulse only).
                       Must be one of WORK_LOAD_PULSE_PHASES.
        advisory:      Optional human-readable context line (v1.1, max 100 chars).
                       Will be PII-sanitized before inclusion.
        redact_keywords: Keywords from user_profile.yaml to redact from advisory.

    Raises:
        BridgeValidationError: if artifact fails validation.
    """
    # Attach v1.1 optional fields when provided
    if artifact_type == "work_load_pulse":
        if phase is not None:
            if phase not in WORK_LOAD_PULSE_PHASES:
                raise BridgeValidationError(
                    f"Invalid phase '{phase}'. Must be one of {sorted(WORK_LOAD_PULSE_PHASES)}."
                )
            artifact = dict(artifact)  # shallow copy — never mutate caller's dict
            artifact["phase"] = phase
        if advisory is not None:
            artifact = dict(artifact)
            artifact["advisory"] = sanitize_advisory(advisory, redact_keywords)
    validate_bridge_artifact(artifact_type, artifact)

    tmp_path = path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)  # os.replace is atomic and works on Windows
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def read_bridge_artifact(path: Path, artifact_type: str) -> dict:
    """
    Read and validate a bridge artifact from disk.

    Returns the artifact dict. If the file does not exist or fails validation,
    returns a safe empty artifact rather than raising — callers must handle
    empty artifacts as "no data" (§8.4 error protocol).
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        validate_bridge_artifact(artifact_type, data)
        return data
    except (json.JSONDecodeError, BridgeValidationError):
        # Preserve stale-but-valid behaviour: return empty rather than corrupt
        return {}


def make_schedule_mask(date: str, blocks: list[dict]) -> dict:
    """Construct a valid personal_schedule_mask artifact."""
    return {
        "$schema": "artha/bridge/personal_schedule_mask/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date,
        "blocks": blocks,
    }


def make_work_load_pulse(
    date: str,
    total_meeting_hours: float,
    after_hours_count: int,
    boundary_score: float,
    focus_availability_score: float,
    phase: str | None = None,
    advisory: str | None = None,
    redact_keywords: list[str] | None = None,
) -> dict:
    """Construct a valid work_load_pulse artifact.

    v1.1: optional phase and advisory fields may be included.
    advisory is PII-sanitized before inclusion.
    """
    artifact: dict[str, Any] = {
        "$schema": "artha/bridge/work_load_pulse/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date,
        "total_meeting_hours": round(total_meeting_hours, 2),
        "after_hours_count": after_hours_count,
        "boundary_score": round(max(0.0, min(1.0, boundary_score)), 3),
        "focus_availability_score": round(max(0.0, min(1.0, focus_availability_score)), 3),
    }
    if phase is not None:
        if phase not in WORK_LOAD_PULSE_PHASES:
            raise ValueError(
                f"Invalid phase {phase!r}. Must be one of {sorted(WORK_LOAD_PULSE_PHASES)}."
            )
        artifact["phase"] = phase
    if advisory is not None:
        sanitized = sanitize_advisory(advisory, redact_keywords)
        if sanitized:
            artifact["advisory"] = sanitized
    return artifact


# ---------------------------------------------------------------------------
# §9.6 Alert Isolation — cross-surface content enforcement
# ---------------------------------------------------------------------------

#: Fields whose string values are permitted in the work→personal bridge.
#: Phase and advisory are v1.1 additions — permitted as sanitized semantic metadata.
#: All other string fields are forbidden (they could carry alert content or PII).
_ALLOWED_STRING_FIELDS_WORK_TO_PERSONAL = frozenset({"$schema", "generated_at", "date", "phase", "advisory"})


def validate_alert_isolation(artifact: dict, surface: str) -> None:
    """
    §9.6 Alert Isolation: verify that a bridge artifact crossing the
    work↔personal boundary contains ONLY aggregate/numeric data —
    never alert content, meeting titles, people names, or other PII.

    This is a semantic check that runs ON TOP OF the schema validation.
    It must be called before any bridge artifact is written to disk.

    Args:
        artifact: The bridge artifact dict to validate.
        surface:  Direction of flow:
                  "work_to_personal" — work_load_pulse written by Work OS,
                                       readable by personal catch-up.
                  "personal_to_work" — personal_schedule_mask written by
                                       personal OS, readable by Work OS.

    Raises:
        BridgeValidationError: if the artifact carries non-aggregate data
            (e.g. string values in disallowed fields, embedded content).
        ValueError: if surface is unrecognized.
    """
    if surface == "work_to_personal":
        # Step 1: semantic isolation FIRST — any string or collection value in a
        # non-metadata field is an alert-content leak regardless of schema compliance.
        for key, value in artifact.items():
            if isinstance(value, str) and key not in _ALLOWED_STRING_FIELDS_WORK_TO_PERSONAL:
                raise BridgeValidationError(
                    f"Alert isolation violation (§9.6): field '{key}' carries string data "
                    "in the work→personal bridge. Only aggregate numeric metrics are "
                    "permitted. Work alert content must never reach the personal surface."
                )
            if isinstance(value, (list, dict)) and value:
                raise BridgeValidationError(
                    f"Alert isolation violation (§9.6): field '{key}' carries structured "
                    "data in the work→personal bridge. Only scalar numeric fields are "
                    "permitted alongside schema/date metadata."
                )
        # Step 2: full schema validation (prohibited fields + required fields + types)
        validate_bridge_artifact("work_load_pulse", artifact)

    elif surface == "personal_to_work":
        # Personal→work bridge: schedule mask contains only opaque time blocks.
        # Full schema validation already enforces prohibited fields.
        validate_bridge_artifact("personal_schedule_mask", artifact)
        # Semantic check: no string fields beyond schema/date/blocks metadata.
        _ALLOWED_PERSONAL_TO_WORK = frozenset({"$schema", "generated_at", "date", "blocks"})
        for key in artifact:
            if key not in _ALLOWED_PERSONAL_TO_WORK:
                raise BridgeValidationError(
                    f"Alert isolation violation (§9.6): unexpected field '{key}' "
                    "in the personal→work bridge artifact."
                )
        # Block items: only busy_start, busy_end, type allowed (enforced by schema)

    else:
        raise ValueError(
            f"Unknown surface '{surface}'. "
            "Expected 'work_to_personal' or 'personal_to_work'."
        )
