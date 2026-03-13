#!/usr/bin/env python3
"""
generate_identity.py — Artha identity section generator

Reads config/user_profile.yaml and generates:
  1. config/Artha.identity.md  — §1 Identity block (personal data, NEVER committed)
  2. config/Artha.md           — Assembled = Artha.identity.md + Artha.core.md

Usage:
    python scripts/generate_identity.py               # Normal generation
    python scripts/generate_identity.py --validate    # Validate profile only (dry run)
    python scripts/generate_identity.py --with-routing  # Also generate routing.yaml

Exit codes:
    0 — success (files written)
    1 — validation error (files NOT written — existing Artha.md preserved)

Design:
    Artha.core.md  = §2–§14 + generic §1 behavior (distributable, version-controlled)
    Artha.identity.md = §1 Identity block, generated from profile (in .gitignore)
    Artha.md       = identity.md + core.md  (build output; this is what AI CLIs read)

Ref: standardization.md §6.5.1, T-1A.2.x
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_ARTHA_DIR = Path(__file__).resolve().parent.parent
_PROFILE_PATH = _ARTHA_DIR / "config" / "user_profile.yaml"
_CORE_PATH = _ARTHA_DIR / "config" / "Artha.core.md"
_IDENTITY_PATH = _ARTHA_DIR / "config" / "Artha.identity.md"
_ASSEMBLED_PATH = _ARTHA_DIR / "config" / "Artha.md"
_ROUTING_EXAMPLE_PATH = _ARTHA_DIR / "config" / "routing.example.yaml"
_ROUTING_PATH = _ARTHA_DIR / "config" / "routing.yaml"

# Valid IANA timezones subset (fast check — not exhaustive; full check needs pytz/zoneinfo)
_KNOWN_TZ_PREFIXES = (
    "America/", "Europe/", "Asia/", "Africa/", "Pacific/", "Atlantic/",
    "Australia/", "Indian/", "Arctic/", "Antarctica/", "UTC", "GMT",
)


# ─────────────────────────────────────────────────────────────────────────────
# Profile loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_profile() -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        _error("PyYAML not installed. Run: pip install pyyaml")
        sys.exit(1)

    if not _PROFILE_PATH.exists():
        _error(
            f"Profile not found at {_PROFILE_PATH}\n"
            "  Copy config/user_profile.example.yaml → config/user_profile.yaml\n"
            "  Edit with your family data, then run this script again."
        )
        sys.exit(1)

    with _PROFILE_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get(profile: dict, key_path: str, default: Any = None) -> Any:
    """Dot-notation nested access: _get(p, 'family.name', 'Family')."""
    parts = key_path.split(".")
    node = profile
    for part in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(part, default)
        if node is None:
            return default
    return node


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate(profile: dict) -> list[str]:
    """Return list of error strings. Empty list = valid."""
    errors: list[str] = []

    if not _get(profile, "family.primary_user.name"):
        errors.append("ERROR: family.primary_user.name is required")

    emails = _get(profile, "family.primary_user.emails", {})
    if not emails or not any(emails.values()):
        errors.append("ERROR: family.primary_user.emails must have at least one address")

    tz = _get(profile, "location.timezone", "")
    if not tz:
        errors.append("ERROR: location.timezone is required")
    elif not any(tz.startswith(p) for p in _KNOWN_TZ_PREFIXES):
        errors.append(f"ERROR: location.timezone '{tz}' does not look like a valid IANA timezone")

    domains = _get(profile, "domains", {})
    enabled = [d for d, v in (domains.items() if isinstance(domains, dict) else []) if
               isinstance(v, dict) and v.get("enabled", False)]
    if not enabled:
        print("WARNING: no domains are enabled — system works but does nothing useful")

    if not _CORE_PATH.exists():
        errors.append(f"ERROR: {_CORE_PATH} not found — nothing to assemble with")

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Identity block generation
# ─────────────────────────────────────────────────────────────────────────────

def _build_identity_block(profile: dict) -> str:
    """Generate the §1 Identity & Context markdown block from profile."""
    lines: list[str] = []

    primary = _get(profile, "family.primary_user", {}) or {}
    p_name = primary.get("name", "User")
    p_nick = primary.get("nickname", "")
    p_emails = primary.get("emails", {}) or {}
    p_gmail = p_emails.get("gmail", "")

    spouse = _get(profile, "family.spouse", {}) or {}
    sp_name = spouse.get("name", "")
    sp_filtered = spouse.get("filtered_briefing", True)

    children: list[dict] = _get(profile, "family.children", []) or []

    location = _get(profile, "location", {}) or {}
    city = location.get("city", "")
    state = location.get("state", "")
    county = location.get("county", "")
    timezone = location.get("timezone", "")

    cultural_ctx = _get(profile, "family.cultural_context", "")
    # Resolve cultural preset if the value is a preset name
    if cultural_ctx and not cultural_ctx.strip().startswith((".", "/", "\n")):
        preset_path = _ARTHA_DIR / "config" / "presets" / "cultural" / f"{cultural_ctx.strip()}.yaml"
        if preset_path.exists():
            try:
                preset = yaml.safe_load(preset_path.read_text(encoding="utf-8"))
                cultural_ctx = preset.get("description", cultural_ctx)
            except Exception:
                pass  # Fall back to raw value

    domains = _get(profile, "domains", {}) or {}
    enabled_domains = [d for d, v in domains.items() if isinstance(v, dict) and v.get("enabled", False)]

    imm_enabled = _get(profile, "domains.immigration.enabled", False)
    imm_context = _get(profile, "domains.immigration.context", "")
    imm_path = _get(profile, "domains.immigration.path", "")
    imm_origin = _get(profile, "domains.immigration.origin_country", "")

    # ── Header ──────────────────────────────────────────────────────────────
    lines.append("## §1 — Identity & Context")
    lines.append("")
    name_clause = f"**{p_name}**"
    if p_nick:
        name_clause += f' ("{p_nick}")'
    lines.append(f"You are **Artha**, the personal intelligence system for {name_clause}'s family.")
    lines.append("You are **not a chatbot** — you are an operating system for personal life management.")
    lines.append("")

    # ── Family ───────────────────────────────────────────────────────────────
    lines.append("### Family")
    primary_entry = f"- **{p_name}**"
    if p_nick:
        primary_entry += f" ({p_nick})"
    primary_entry += " — primary user"
    if p_gmail:
        primary_entry += f". Email: {p_gmail}"
    lines.append(primary_entry)

    if sp_name:
        sp_entry = f"- **{sp_name}** — spouse"
        if sp_filtered:
            sp_entry += ". Filtered briefing: enabled"
        lines.append(sp_entry)

    for child in children:
        c_name = child.get("name", "Child")
        c_age = child.get("age", "")
        c_grade = child.get("grade", "")
        school = child.get("school", {}) or {}
        s_name = school.get("name", "")
        s_district = school.get("district", "")
        milestones = child.get("milestones", {}) or {}
        class_of = milestones.get("class_of", "")
        college_prep = milestones.get("college_prep", False)
        new_driver = milestones.get("new_driver", False)

        c_entry = f"- **{c_name}**"
        parts = []
        if c_age:
            parts.append(f"age {c_age}")
        if c_grade:
            parts.append(c_grade)
        if s_name:
            school_ref = s_name
            if s_district:
                school_ref += f" ({s_district})"
            parts.append(f"at {school_ref}")
        if class_of:
            parts.append(f"Class of {class_of}")
        if parts:
            c_entry += " — " + ", ".join(parts) + "."
        if college_prep:
            c_entry += " College prep active."
        if new_driver:
            c_entry += " New driver — insurance monitoring active."
        lines.append(c_entry)

    lines.append("")

    # ── Location ─────────────────────────────────────────────────────────────
    lines.append("### Location")
    loc_parts = []
    city_state = ", ".join(p for p in [city, state] if p)
    if city_state:
        loc_parts.append(city_state)
    if county:
        loc_parts.append(f"({county} County)")
    loc_line = " ".join(loc_parts)
    if timezone:
        loc_line += f". Timezone: {timezone}"
    lines.append(loc_line + ".")
    lines.append("")

    # ── Cultural Context (optional) ───────────────────────────────────────────
    if cultural_ctx:
        lines.append("### Cultural Context")
        lines.append(cultural_ctx)
        lines.append("")

    # ── Active Domains ────────────────────────────────────────────────────────
    if enabled_domains:
        lines.append("### Active Domains")
        for domain in sorted(enabled_domains):
            d_config = domains.get(domain, {}) or {}
            d_label = domain.replace("_", " ").title()
            d_note = ""
            # Domain-specific context snippets
            if domain == "immigration" and imm_path:
                d_note = f" — {imm_path}"
                if imm_origin:
                    d_note += f", {imm_origin} national"
            elif domain == "kids" and children:
                d_note = f" — {', '.join(c.get('name', '') for c in children)}"
            description = d_config.get("description", "")
            if description:
                d_note = f" — {description}"
            lines.append(f"- **{d_label}**{d_note}")
        lines.append("")

    # ── Immigration Context (if enabled) ──────────────────────────────────────
    if imm_enabled and (imm_context or imm_path):
        lines.append("### Immigration Context")
        if imm_context:
            lines.append(imm_context)
        elif imm_path:
            ctx_parts = [f"Family is on employment-based immigration path ({imm_path})"]
            if imm_origin:
                ctx_parts.append(f"country of birth: {imm_origin}")
            lines.append(". ".join(ctx_parts) + ".")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────

def _write_identity(identity_block: str) -> None:
    _IDENTITY_PATH.write_text(identity_block, encoding="utf-8")
    print(f"  Written: {_IDENTITY_PATH.relative_to(_ARTHA_DIR)}")


def _assemble_artha_md(identity_block: str) -> None:
    core_text = _CORE_PATH.read_text(encoding="utf-8")

    # Strip the generic §1 placeholder from core (first line until the first ## §2)
    # The core.md may start with the file header comment and then the generic §1
    # We inject our generated §1 at the top, replacing the generic identity placeholder.
    # The core.md §1 starts with "## §1 Identity & Core Behavior" — we keep the rest.
    # But the first 2-3 lines of core.md are the title + generic identity, so we
    # insert the generated §1 block before the content of core.md.
    # Design choice: identity.md IS prepended directly; core.md starts with §2+ behavior.
    # Since core.md currently starts with "# Artha — Personal Intelligence System" header
    # and then the §1 block, we need to clip that.

    # Find where §2 starts in core.md to avoid duplicating the system behavior in §1
    # The core.md §1 contains only system behavior (cross-platform, directives) — no personal data.
    # We prepend the generated identity, then append core.md from its §2 marker.
    # BUT: the core.md's §1 system behavior is still useful. So the assembly is:
    #   generated identity (§1a personal context) + full core.md (§1b system + §2-§14)
    # We just prepend; the generated §1 replaces the personal lines we stripped from core.md.

    assembled = identity_block + "\n\n---\n\n" + core_text
    _ASSEMBLED_PATH.write_text(assembled, encoding="utf-8")
    print(f"  Written: {_ASSEMBLED_PATH.relative_to(_ARTHA_DIR)}")


def _generate_routing_yaml(profile: dict) -> None:
    """Generate config/routing.yaml from profile + routing.example.yaml template."""
    if not _ROUTING_EXAMPLE_PATH.exists():
        print(f"  WARNING: {_ROUTING_EXAMPLE_PATH} not found — skipping routing generation")
        return

    try:
        import yaml  # type: ignore
    except ImportError:
        print("  WARNING: PyYAML not found — skipping routing generation")
        return

    with _ROUTING_EXAMPLE_PATH.open("r", encoding="utf-8") as f:
        routing = yaml.safe_load(f) or {}

    # Inject school domains from profile children
    children: list[dict] = _get(profile, "family.children", []) or []
    school_domains: list[str] = []
    for child in children:
        school = child.get("school", {}) or {}
        domain = school.get("email_domain", "")
        if domain and domain not in school_domains:
            school_domains.append(domain)

    if school_domains and "user_routes" in routing:
        routing["user_routes"]["school_domains"] = school_domains

    # Inject finance institutions from profile
    finance_institutions = _get(profile, "domains.finance.institutions", []) or []
    if finance_institutions and "user_routes" in routing:
        routing["user_routes"]["finance_institutions"] = finance_institutions

    with _ROUTING_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(routing, f, default_flow_style=False, allow_unicode=True)

    print(f"  Written: {_ROUTING_PATH.relative_to(_ARTHA_DIR)}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Artha identity section from user_profile.yaml"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate profile only; do NOT write any files",
    )
    parser.add_argument(
        "--with-routing",
        action="store_true",
        help="Also generate config/routing.yaml from profile + routing.example.yaml",
    )
    args = parser.parse_args(argv)

    print("Artha identity generator")
    print(f"  Profile: {_PROFILE_PATH.relative_to(_ARTHA_DIR)}")
    print(f"  Core:    {_CORE_PATH.relative_to(_ARTHA_DIR)}")
    print()

    # Load
    profile = _load_profile()

    # Validate
    errors = _validate(profile)
    if errors:
        print("Validation failed:")
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        print()
        print("Existing Artha.md NOT modified.")
        return 1

    if args.validate:
        print("Validation passed. (--validate mode: no files written)")
        return 0

    # Generate
    print("Generating identity block...")
    identity_block = _build_identity_block(profile)

    _write_identity(identity_block)
    _assemble_artha_md(identity_block)

    if args.with_routing:
        print("Generating routing.yaml...")
        _generate_routing_yaml(profile)

    print()
    p_name = _get(profile, "family.primary_user.name", "User")
    nick = _get(profile, "family.primary_user.nickname", "")
    children: list[dict] = _get(profile, "family.children", []) or []
    child_names = [c.get("name", "") for c in children]
    domains = _get(profile, "domains", {}) or {}
    enabled_count = sum(1 for v in domains.values() if isinstance(v, dict) and v.get("enabled", False))

    print("Identity generation complete.")
    print(f"  Primary user: {p_name}" + (f" ({nick})" if nick else ""))
    if child_names:
        print(f"  Children:     {', '.join(child_names)}")
    print(f"  Domains:      {enabled_count} enabled")
    print()
    print("Regenerate after profile changes:")
    print("  python scripts/generate_identity.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
