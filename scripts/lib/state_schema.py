"""scripts/lib/state_schema.py — Lightweight YAML frontmatter validator for state files.

Does NOT enforce types or formats — the LLM handles flexible parsing.
Only checks that required fields exist, preventing silent data loss
when a state writer accidentally omits a critical field.

Ref: specs/pay-debt.md §11.1
"""
from __future__ import annotations

# Schema registry: {filename: {required: [...], version: N}}
_SCHEMAS: dict[str, dict] = {
    "health-check.md": {
        "required": ["schema_version", "domain", "last_updated"],
        "version": 1,
    },
    "goals.md": {
        "required": ["schema_version", "domain", "last_updated"],
        "version": 1,
    },
    "open_items.md": {
        "required": ["schema_version", "domain"],
        "version": 1,
    },
    "profile.md": {
        "required": ["schema_version", "domain", "last_updated"],
        "version": 1,
    },
    "finance.md": {
        "required": ["schema_version", "domain", "last_updated"],
        "version": 1,
    },
    "health.md": {
        "required": ["schema_version", "domain", "last_updated"],
        "version": 1,
    },
}


def validate_frontmatter(filename: str, frontmatter: dict) -> list[str]:
    """Return list of missing required fields.  Empty list = valid.

    Args:
        filename: Basename of the state file (e.g. "health-check.md").
        frontmatter: Parsed YAML frontmatter as a dict.

    Returns:
        List of field names that are required but absent from frontmatter.
        Returns an empty list if no schema is registered for the filename
        (conservative default: unknown files are accepted without validation).
    """
    schema = _SCHEMAS.get(filename)
    if schema is None:
        return []  # No schema = no validation (conservative)
    return [f for f in schema["required"] if f not in frontmatter]
