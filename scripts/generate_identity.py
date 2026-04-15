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
_MIN_PATH = _ARTHA_DIR / "config" / "Artha.min.md"  # DEBT-PROMPT-004: Tier 0 compact output
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
    if not _PROFILE_PATH.exists():
        _error(
            f"Profile not found at {_PROFILE_PATH}\n"
            "  Copy config/user_profile.example.yaml → config/user_profile.yaml\n"
            "  Edit with your family data, then run this script again."
        )
        sys.exit(1)

    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        return load_config("user_profile", str(_PROFILE_PATH.parent))
    except Exception:
        return {}


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


# DEBT-016: YAML injection sanitizer for user-supplied profile values
_MAX_PROFILE_VALUE_LEN = 200

# DEBT-PROMPT-002: Unicode bidi/direction-override characters that can spoof
# section boundaries in rendered output — strip these from all profile values.
_BIDI_CHARS = frozenset([
    '\u202A', '\u202B', '\u202C', '\u202D', '\u202E',  # LRE, RLE, PDF, LRO, RLO
    '\u2066', '\u2067', '\u2068', '\u2069',             # LRI, RLI, FSI, PDI
    '\u200F', '\u200E',                                  # RLM, LRM
])


def _sanitize_profile_value(value: str) -> str:
    """Sanitize a user-supplied profile value before interpolation into the prompt.

    Strips YAML/Markdown structural tokens that could break the prompt context or
    allow a user to inject false framing into the identity block.

    Mitigates: line-starting `#` headings, `---` document separators,
    backtick code-fence injection, Unicode bidi/direction overrides (DEBT-PROMPT-002),
    Artha structural markers (§), inline `---` sequences, and oversized values.
    """
    if not isinstance(value, str):
        return str(value) if value is not None else ""
    # Strip YAML document separators (could end the YAML doc mid-prompt)
    lines = [ln for ln in value.splitlines() if not ln.strip().startswith("---")]
    value = "\n".join(lines)
    # Strip Markdown heading tokens at line start (prompt-injection vector)
    import re as _re
    value = _re.sub(r"(?m)^#+\s*", "", value)
    # Escape backticks (prevent code-fence injection)
    value = value.replace("`", "\u2019")  # replace with right single quotation mark
    # DEBT-PROMPT-002: strip Unicode bidi override characters
    value = "".join(c for c in value if c not in _BIDI_CHARS)
    # DEBT-PROMPT-002: strip Artha structural markers
    value = value.replace("\u00a7", "")  # §
    # DEBT-PROMPT-002: normalize inline --- sequences (replace with em-dash)
    value = _re.sub(r"-{3,}", "\u2014", value)
    # Truncate to safety cap
    if len(value) > _MAX_PROFILE_VALUE_LEN:
        value = value[:_MAX_PROFILE_VALUE_LEN].rstrip() + "…"
    return value.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Validation (blocking errors)
# ─────────────────────────────────────────────────────────────────────────────

def _validate(profile: dict) -> list[str]:
    """Return list of error strings. Empty list = valid."""
    errors: list[str] = []

    if not _get(profile, "family.primary_user.name"):
        errors.append("ERROR: family.primary_user.name is required")

    emails = _get(profile, "family.primary_user.emails", {})
    if not emails or not any(emails.values()):
        errors.append("ERROR: family.primary_user.emails must have at least one address")

    _PLACEHOLDER_NAMES = {"Alex Smith", "Alex"}
    _PLACEHOLDER_EMAILS = {"alex.smith@gmail.com"}
    p_name = _get(profile, "family.primary_user.name", "")
    if p_name in _PLACEHOLDER_NAMES:
        errors.append(
            "ERROR: family.primary_user.name still has the example value "
            f"'{p_name}' — edit config/user_profile.yaml with your real name"
        )
    p_gmail = _get(profile, "family.primary_user.emails.gmail", "")
    if p_gmail in _PLACEHOLDER_EMAILS:
        errors.append(
            "ERROR: family.primary_user.emails.gmail still has the example value "
            f"'{p_gmail}' — edit config/user_profile.yaml with your real Gmail address"
        )

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
# Advisory warnings (non-blocking — placeholder data the user may not have noticed)
# ─────────────────────────────────────────────────────────────────────────────

_PLACEHOLDER_CHILD_NAMES = {"Child1", "Child2", "ChildName", "Child"}
_PLACEHOLDER_CITIES = {"Springfield", "Anytown", "Your City", "Exampleville"}


def _collect_warnings(profile: dict) -> list[str]:
    """
    Return non-blocking advisory warnings about placeholder data.

    These are NOT validation errors — they don't block identity generation.
    They surface fields that look like unedited example values so the user
    can update them before their first real catch-up.
    """
    warnings: list[str] = []

    children = _get(profile, "family.children", []) or []
    for i, child in enumerate(children):
        name = child.get("name", "")
        if name in _PLACEHOLDER_CHILD_NAMES:
            warnings.append(
                f"family.children[{i}].name is still placeholder '{name}' — "
                "update config/user_profile.yaml with your child's real name"
            )

    city = _get(profile, "location.city", "")
    if city in _PLACEHOLDER_CITIES:
        warnings.append(
            f"location.city is still placeholder '{city}' — "
            "update config/user_profile.yaml with your real city"
        )

    return warnings


def _print_validate_summary(profile: dict) -> None:
    """Print a concise preview of what the generated identity will contain."""
    name = _get(profile, "family.primary_user.name", "")
    emails = _get(profile, "family.primary_user.emails", {}) or {}
    email_display = next((v for v in emails.values() if v), "no email set")

    city  = _get(profile, "location.city", "")
    state = _get(profile, "location.state", "")
    tz    = _get(profile, "location.timezone", "")
    location = ", ".join(p for p in [city, state] if p) or tz or "not set"

    household = _get(profile, "household.type", "")
    children  = _get(profile, "family.children", []) or []
    domains   = _get(profile, "domains", {}) or {}
    enabled   = sorted(d for d, v in domains.items()
                       if isinstance(v, dict) and v.get("enabled", False))

    print("  Identity preview:")
    # Mask for privacy: t***@example.com
    masked = email_display if "@" not in email_display else f"{email_display[0]}***@{email_display.split('@')[-1]}"
    print(f"    Name:      {name}  ({masked})")
    print(f"    Location:  {location}")
    if household:
        c_note = f", {len(children)} child(ren)" if children else ""
        print(f"    Household: {household}{c_note}")
    if enabled:
        print(f"    Domains:   {', '.join(enabled)}")
    print()



# ─────────────────────────────────────────────────────────────────────────────
# Identity block generation
# ─────────────────────────────────────────────────────────────────────────────

def _build_identity_block(profile: dict) -> str:
    """Generate the §1 Identity & Context markdown block from profile."""
    lines: list[str] = []

    primary = _get(profile, "family.primary_user", {}) or {}
    p_name = _sanitize_profile_value(primary.get("name", "User"))
    p_nick = _sanitize_profile_value(primary.get("nickname", ""))
    p_emails = primary.get("emails", {}) or {}
    p_gmail = p_emails.get("gmail", "")

    spouse = _get(profile, "family.spouse", {}) or {}
    sp_name = spouse.get("name", "")
    sp_filtered = spouse.get("filtered_briefing", True)

    children: list[dict] = _get(profile, "family.children", []) or []

    location = _get(profile, "location", {}) or {}
    city = _sanitize_profile_value(location.get("city", ""))
    state = _sanitize_profile_value(location.get("state", ""))
    county = _sanitize_profile_value(location.get("county", ""))
    timezone = location.get("timezone", "")  # timezone validated structurally — sanitize only display

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
    cultural_ctx = _sanitize_profile_value(cultural_ctx)

    domains = _get(profile, "domains", {}) or {}
    enabled_domains = [d for d, v in domains.items() if isinstance(v, dict) and v.get("enabled", False)]

    imm_enabled = _get(profile, "domains.immigration.enabled", False)
    imm_context = _sanitize_profile_value(_get(profile, "domains.immigration.context", ""))
    imm_path = _sanitize_profile_value(_get(profile, "domains.immigration.path", ""))
    imm_origin = _sanitize_profile_value(_get(profile, "domains.immigration.origin_country", ""))

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
    # AFW-2: When progressive_disclosure.enabled, emit a compact domain menu
    # (Stage 1) instead of the full per-domain bullet list.  The menu is ~1286
    # tokens (A-1 validated) vs. the full list which grows with domain count.
    _pd_enabled = False
    if enabled_domains:
        try:
            from lib.config_loader import load_config as _load_cfg  # noqa: PLC0415
            _acfg = _load_cfg("artha_config", str(_ARTHA_DIR / "config"))
            _pd_enabled = bool(
                isinstance(_acfg, dict)
                and (_acfg.get("harness") or {}).get("progressive_disclosure", {}).get("enabled", False)
            )
        except Exception:  # noqa: BLE001
            _pd_enabled = False

    if _pd_enabled:
        # Stage 1: Compact domain advertisement (AFW-2 progressive loading)
        try:
            from domain_index import build_domain_menu, load_domain_registry  # noqa: PLC0415
            _registry = load_domain_registry()
            if _registry:
                lines.append("### Active Domains")
                lines.append(build_domain_menu(_registry))
                lines.append("")
                _pd_enabled = True  # mark success
            else:
                _pd_enabled = False  # fall through to legacy path
        except Exception:  # noqa: BLE001
            _pd_enabled = False  # fall through to legacy path

    if not _pd_enabled and enabled_domains:
        # Legacy (rollback): full per-domain bullet list
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

    # DEBT-018: Interpolate alert thresholds from user_profile.yaml into identity block
    thresholds = _get(profile, "alert_thresholds", {}) or {}
    if thresholds:
        lines.append("### Alert Thresholds (from config)")
        if "passport_expiry_days" in thresholds:
            tiers = thresholds["passport_expiry_days"]
            lines.append(f"- Passport/Visa expiry tiers: {tiers} days before expiry (escalating urgency)")
        if "sprint_ending_days" in thresholds:
            lines.append(f"- Sprint ending warning: {thresholds['sprint_ending_days']} days before end")
        if "connector_stale_hours" in thresholds:
            lines.append(f"- Connector stale after: {thresholds['connector_stale_hours']}h; "
                         f"critical after: {thresholds.get('connector_critical_hours', 72)}h")
        if "open_item_overdue_days" in thresholds:
            lines.append(f"- Open item overdue: immediate alert after {thresholds['open_item_overdue_days']} day(s)")
        if "goal_no_progress_days" in thresholds:
            lines.append(f"- Goal no-progress nudge: {thresholds['goal_no_progress_days']} days")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Assembly (compact + legacy modes)
# ─────────────────────────────────────────────────────────────────────────────

# §R — Command Router Table (injected into compact Artha.md)
# Tells the AI which workflow files to load for each command.
_COMMAND_ROUTER_TABLE = """\
## §R — Command Router

Load ONLY the files listed for the current command. Do NOT load all workflow
files for light commands that don't invoke the catch-up workflow.

| Command | Load These Files (in order) | State Files |
|---------|-----------------------------|----|
| `/catch-up`, `/catch-up flash`, `/catch-up deep`, `catch me up` | `config/workflow/preflight.md` → `fetch.md` → `process.md` → `reason.md` → `finalize.md` | Per tiered loading in fetch.md |
| `/catch-up standard` | Same as `/catch-up` | Same |
| `/status` | — | `state/health-check.md` |
| `/items` | — | `state/open_items.md` |
| `/items quick` | — | `state/open_items.md`, `state/memory.md` |
| `/goals` | — | `state/goals.md`, `prompts/goals.md` |
| `/domain <X>` | `config/workflow/process.md`, `config/workflow/reason.md` | `state/<X>.md`, `prompts/<X>.md` |
| `/dashboard` | — | `state/dashboard.md` |
| `/bootstrap` | `config/bootstrap-interview.md` | Various |
| `/diff` | — | `state/` (git diff) |
| `/scorecard` | — | All state frontmatter |
| `/health` | — | `state/health-check.md` |
| `/power` | — | `state/open_items.md`, `state/goals.md` |

**CRITICAL for `/catch-up`:**
Load each workflow file BEFORE executing that phase.
Do NOT skip any workflow file.
If a file fails to load, halt and report: "⛔ Cannot load config/workflow/<file>.md — catch-up aborted."
"""


def _extract_sections(core_text: str) -> dict[str, str]:
    """
    Extract named sections from Artha.core.md by §N markers.

    Returns keys: behavior, privacy, commands, routing_table, capabilities
    These are the sections retained in compact Artha.md.
    §2 (catch-up workflow) is moved OUT to config/workflow/ files.
    §3 (routing table) is already in config/routing.yaml.
    """
    import re

    # Split on top-level ## §N markers
    # We keep: §1 (behavior), §4 (privacy), §5 (commands), §6 (routing), §7 (capabilities)
    # We exclude: §2-§3 (workflow + routing table — in separate files), §8-§14 (meta, in core.md only)
    sections: dict[str, str] = {}

    # Find §1 (behavior/directives) — from start to before §2
    m_s1 = re.search(r"^## §1 ", core_text, re.MULTILINE)
    m_s2 = re.search(r"^## §2 ", core_text, re.MULTILINE)
    if m_s1 and m_s2:
        sections["behavior"] = core_text[m_s1.start():m_s2.start()].rstrip()

    # §4 Privacy & Redaction Rules
    m_s4 = re.search(r"^## §4 ", core_text, re.MULTILINE)
    m_s5 = re.search(r"^## §5 ", core_text, re.MULTILINE)
    if m_s4 and m_s5:
        sections["privacy"] = core_text[m_s4.start():m_s5.start()].rstrip()
    elif m_s4:
        sections["privacy"] = core_text[m_s4.start():].rstrip()

    # §5 Slash Commands
    m_s5 = re.search(r"^## §5 ", core_text, re.MULTILINE)
    m_s6 = re.search(r"^## §6 ", core_text, re.MULTILINE)
    if m_s5 and m_s6:
        sections["commands"] = core_text[m_s5.start():m_s6.start()].rstrip()
    elif m_s5:
        sections["commands"] = core_text[m_s5.start():].rstrip()

    # §6 Multi-LLM Routing
    m_s6 = re.search(r"^## §6 ", core_text, re.MULTILINE)
    m_s7 = re.search(r"^## §7 ", core_text, re.MULTILINE)
    if m_s6 and m_s7:
        sections["routing_table"] = core_text[m_s6.start():m_s7.start()].rstrip()
    elif m_s6:
        sections["routing_table"] = core_text[m_s6.start():].rstrip()

    # §7 Capabilities
    m_s7 = re.search(r"^## §7 ", core_text, re.MULTILINE)
    m_s8 = re.search(r"^## §8 ", core_text, re.MULTILINE)
    if m_s7 and m_s8:
        sections["capabilities"] = core_text[m_s7.start():m_s8.start()].rstrip()
    elif m_s7:
        sections["capabilities"] = core_text[m_s7.start():].rstrip()

    return sections


def _write_identity(identity_block: str) -> None:
    _IDENTITY_PATH.write_text(identity_block, encoding="utf-8")
    print(f"  Written: {_IDENTITY_PATH.relative_to(_ARTHA_DIR)}")


def _assemble_artha_md(identity_block: str, compact: bool = True) -> None:
    """
    Assemble config/Artha.md from identity block + core.

    compact=True (default):
        Produces ≤20KB output: identity + §1 behavior + §R router + §4–§7.
        The 21-step catch-up workflow (§2) is referenced via config/workflow/*.md.
        AI must load those files as directed by §R.

    compact=False (legacy, --no-compact):
        Produces original 78KB+ output by prepending identity to full core.md.
        Use for rollback or debugging when workflow files are suspect.
    """
    core_text = _CORE_PATH.read_text(encoding="utf-8")

    header = (
        "<!-- AUTO-GENERATED — DO NOT EDIT.\n"
        "     Modify config/Artha.core.md or config/user_profile.yaml instead,\n"
        "     then run: python scripts/generate_identity.py -->\n"
        "<!-- PROMPT STABILITY: This file is the frozen system prompt layer.\n"
        "     Do NOT modify mid-session. Changes take effect on next session start.\n"
        "     See config/Artha.core.md § AR-6: Prompt Stability Architecture. -->\n\n"
    )

    if not compact:
        # Legacy mode: full core.md, no changes
        assembled = header + identity_block + "\n\n---\n\n" + core_text
        print("  [legacy --no-compact mode: full core.md included]")
    else:
        # Compact mode: extract retained sections
        sections = _extract_sections(core_text)
        missing = [k for k in ("behavior", "privacy", "commands", "capabilities") if k not in sections]
        if missing:
            print(f"  WARNING: Could not extract sections {missing} — falling back to legacy mode")
            assembled = header + identity_block + "\n\n---\n\n" + core_text
        else:
            compact_body = "\n\n".join(filter(None, [
                sections.get("behavior", ""),
                _COMMAND_ROUTER_TABLE,
                sections.get("privacy", ""),
                sections.get("commands", ""),
                sections.get("routing_table", ""),
                sections.get("capabilities", ""),
            ]))
            workflow_ref = (
                "\n\n---\n\n"
                "## §2 — Catch-Up Workflow\n\n"
                "The catch-up workflow is in `config/workflow/` files.\n"
                "Load them as directed by §R above.\n"
                "Full canonical step definitions are in `config/Artha.core.md`.\n"
            )
            assembled = header + identity_block + "\n\n---\n\n" + compact_body + workflow_ref

    _ASSEMBLED_PATH.write_text(assembled, encoding="utf-8")
    size_kb = len(assembled.encode("utf-8")) / 1024
    print(f"  Written: {_ASSEMBLED_PATH.relative_to(_ARTHA_DIR)} ({size_kb:.1f} KB)")



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


# ---------------------------------------------------------------------------
# DEBT-PROMPT-004: Tier stripping helpers for compact prompt generation
# ---------------------------------------------------------------------------

import re as _re

# Size gate for Tier 0 output (15 KB)
_TIER0_SIZE_LIMIT_BYTES = 15_360


def _strip_html_comments(text: str) -> str:
    """Remove all HTML comments (<!-- ... -->) from *text*.

    Handles multi-line comments.  Used by both Tier 0 and Tier 1 modes.
    """
    return _re.sub(r"<!--.*?-->", "", text, flags=_re.DOTALL)


def _strip_tier1_only_sections(text: str) -> str:
    """Remove blocks delimited by <!-- tier:1-only --> ... <!-- /tier:1-only -->.

    Tier 0 mode strips these sections entirely.  Tier 1 keeps them (after
    comment removal).  The delimiter comments themselves are also removed by
    _strip_html_comments which runs first.
    """
    # Pattern: the opening tag, content, and closing tag (including surrounding
    # blank lines to avoid leaving whitespace gaps).
    return _re.sub(
        r"<!--\s*tier:1-only\s*-->.*?<!--\s*/tier:1-only\s*-->",
        "",
        text,
        flags=_re.DOTALL,
    )


def _strip_example_blocks(text: str) -> str:
    """Remove markdown fenced code blocks labelled as examples (Tier 0).

    Strips blocks of the form:
        ```example
        ...content...
        ```
    or preceded by a ``<!-- example -->`` HTML comment.  This keeps the
    output under 15 KB by removing illustrative but non-instructional content.
    """
    # Remove fenced blocks with an 'example' language tag
    text = _re.sub(
        r"```example\n.*?```",
        "",
        text,
        flags=_re.DOTALL,
    )
    # Remove code blocks immediately preceded by <!-- example -->
    text = _re.sub(
        r"<!--\s*example\s*-->\s*```[^\n]*\n.*?```",
        "",
        text,
        flags=_re.DOTALL,
    )
    return text


def _apply_tier_stripping(text: str, tier: int) -> str:
    """Apply tier-appropriate stripping to *text*.

    Tier 0: strip HTML comments + tier:1-only sections + example blocks.
    Tier 1: strip HTML comments only.
    Other values: return text unchanged.
    """
    if tier == 1:
        return _strip_html_comments(text)
    if tier == 0:
        text = _strip_tier1_only_sections(text)  # must run before comment strip
        text = _strip_html_comments(text)
        text = _strip_example_blocks(text)
        # Collapse runs of 3+ blank lines that stripping leaves behind
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    return text


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
        default=True,
        help="Also generate config/routing.yaml from profile + routing.example.yaml (default: on)",
    )
    parser.add_argument(
        "--no-routing",
        action="store_true",
        help="Skip routing.yaml generation",
    )
    parser.add_argument(
        "--no-compact",
        action="store_true",
        help=(
            "Legacy mode: prepend identity to the full Artha.core.md (produces 78KB+ output). "
            "Use for rollback when config/workflow/ files are suspect. "
            "Default is compact mode (~15KB) with workflow steps in config/workflow/."
        ),
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=[0, 1],
        default=None,
        metavar="TIER",
        help=(
            "DEBT-PROMPT-004: Generate a tier-stripped output in addition to the standard build. "
            "0 = strip HTML comments + tier:1-only sections + example blocks → config/Artha.min.md (≤15KB). "
            "1 = strip HTML comments only → config/Artha.min.md. "
            "Does not affect config/Artha.md (standard output)."
        ),
    )
    args = parser.parse_args(argv)

    print("Artha identity generator")
    print(f"  Profile: {_PROFILE_PATH.relative_to(_ARTHA_DIR)}")
    print(f"  Core:    {_CORE_PATH.relative_to(_ARTHA_DIR)}")
    print()

    # Load
    profile = _load_profile()

    # Blocking validation errors
    errors = _validate(profile)
    if errors:
        print("Validation failed:")
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        print()
        print("Existing Artha.md NOT modified.")
        return 1

    # Non-blocking advisory warnings (placeholder data the user may not have noticed)
    warnings = _collect_warnings(profile)
    if warnings:
        print("⚠  Advisory warnings (non-blocking):")
        for w in warnings:
            print(f"     {w}")
        print()

    if args.validate:
        _print_validate_summary(profile)
        print("Validation passed. (--validate mode: no files written)")
        if warnings:
            print(f"  ⚠  {len(warnings)} advisory warning(s) — review above before generating")
        return 0

    # Generate
    print("Generating identity block...")
    identity_block = _build_identity_block(profile)

    _write_identity(identity_block)
    compact_mode = not getattr(args, "no_compact", False)
    _assemble_artha_md(identity_block, compact=compact_mode)

    # DEBT-PROMPT-004: Tier-stripped build → config/Artha.min.md
    if args.tier is not None:
        standard_text = _ASSEMBLED_PATH.read_text(encoding="utf-8")
        min_text = _apply_tier_stripping(standard_text, args.tier)
        _MIN_PATH.write_text(min_text, encoding="utf-8")
        size_bytes = len(min_text.encode("utf-8"))
        size_kb = size_bytes / 1024
        print(f"  Written: {_MIN_PATH.relative_to(_ARTHA_DIR)} ({size_kb:.1f} KB) [tier {args.tier}]")
        if args.tier == 0 and size_bytes > _TIER0_SIZE_LIMIT_BYTES:
            print(
                f"  WARNING: Tier 0 output exceeds {_TIER0_SIZE_LIMIT_BYTES // 1024}KB size gate "
                f"({size_bytes} bytes > {_TIER0_SIZE_LIMIT_BYTES} bytes). "
                f"Review config/Artha.core.md for additional content to tier-gate."
            )

    if not args.no_routing:
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
