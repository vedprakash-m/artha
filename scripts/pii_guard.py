#!/usr/bin/env python3
# pii-guard: ignore-file — this source file contains PII pattern strings as code, not PII
"""
pii_guard.py — Artha pre-persist PII filter (Layer 1 of defense-in-depth)

Pure Python port of pii_guard.sh. Cross-platform (macOS, Windows, Linux).
Requires only the Python standard library — no third-party dependencies.

Usage:
    pii_guard.py scan   [file]   — detect only; exit 1 if PII found
    pii_guard.py filter [file]   — replace PII tokens; filtered text to stdout; exit 1 if found
    pii_guard.py test            — run built-in test suite
    pii_guard.py version         — print version and exit 0

stdin mode (no file arg):  reads from stdin
file mode:                 reads from the specified file path

Architecture:
    Layer 1 (this script): device-local Python regex, BEFORE writing to state files.
    Layer 2: AI semantic redaction, applied AFTER extraction.
    Together = defense-in-depth. If this script exits non-zero, catch-up HALTS.

Design: sentinel-based allowlist — protect USCIS/Amazon receipt numbers before
scanning, restore after. Substitution order matters (ITIN before SSN).

Behavioral contract (must be preserved across all versions):
    stderr format: PII_FOUND:<comma-separated types>  (e.g. PII_FOUND:SSN,CC)
    exit 0:        no PII detected
    exit 1:        PII detected — types on stderr, filtered text on stdout (filter mode)
    scan mode:     print PII_FOUND to stderr; stdout unchanged (clean text echoed)
    filter mode:   print filtered text to stdout; PII_FOUND on stderr if found
    test mode:     run 19+ test cases; exit 0 on all pass

Ref: TS §8.6, T-1A.1.5, standardization.md §7.6.1
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"

_ARTHA_DIR = Path(__file__).resolve().parent.parent
_AUDIT_LOG = _ARTHA_DIR / "state" / "audit.md"
_EMAIL_ID = os.environ.get("ARTHA_EMAIL_ID", "unknown")

# ─────────────────────────────────────────────────────────────────────────────
# Allowlist: patterns that must NOT be treated as PII.
# Applied as sentinels BEFORE PII scanning (preserved through substitution).
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r"IOE\d{10}"),          # USCIS receipt number (IOE)
    re.compile(r"SRC\d{10}"),          # USCIS receipt number (SRC)
    re.compile(r"LIN\d{10}"),          # USCIS receipt number (LIN)
    re.compile(r"EAC\d{10}"),          # USCIS receipt number (EAC)
    re.compile(r"WAC\d{10}"),          # USCIS receipt number (WAC)
    re.compile(r"NBC\d{10}"),          # USCIS receipt number (NBC)
    re.compile(r"MSC\d{10}"),          # USCIS receipt number (MSC)
    re.compile(r"ZLA\d{10}"),          # USCIS receipt number (ZLA)
    re.compile(r"\d{3}-\d{7}-\d{7}"),  # Amazon order number
    re.compile(r"\*{4}\d{4}"),         # Masked credit card (already redacted)
]

# ─────────────────────────────────────────────────────────────────────────────
# PII patterns (in substitution order — ITIN before SSN is critical)
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (compiled regex, replacement token, pii_type_label)
# Ordering: ITIN first, then SSN — prevents ITIN being mis-matched as SSN first.
_PII_RULES: list[tuple[re.Pattern, str, str]] = [
    # ITIN: 9XX-[789]X-XXXX (first digit 9, third group starts with 7/8/9)
    (
        re.compile(r"\b9\d{2}-[789]\d-\d{4}\b"),
        "[PII-FILTERED-ITIN]",
        "ITIN",
    ),
    # SSN bare: digits only, NNN-NN-NNNN
    (
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[PII-FILTERED-SSN]",
        "SSN",
    ),
    # SSN in context: "SSN: 123456789" or "social security number 123456789"
    (
        re.compile(
            r"\b(SSN|social[ _]security(?:[ _]number)?|tax[ _]id)\s*[:#]?\s*\d{9}\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-SSN]",
        "SSN",
    ),
    # Visa card: 4XXX XXXX XXXX XXXX
    (
        re.compile(r"\b4\d{3}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        "[PII-FILTERED-CC]",
        "CC",
    ),
    # Mastercard: 5[1-5]XX XXXX XXXX XXXX
    (
        re.compile(r"\b5[1-5]\d{2}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        "[PII-FILTERED-CC]",
        "CC",
    ),
    # Amex: 3[47]XX XXXXXX XXXXX
    (
        re.compile(r"\b3[47]\d{2}[\s\-]?\d{6}[\s\-]?\d{5}\b"),
        "[PII-FILTERED-CC]",
        "CC",
    ),
    # Discover: 6011 or 65XX XXXX XXXX XXXX
    (
        re.compile(r"\b6(?:011|5\d{2})[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        "[PII-FILTERED-CC]",
        "CC",
    ),
    # Bank routing number (9 digits, in context)
    (
        re.compile(
            r"\b(routing|aba|transit)(\s*(?:number|no|#)?)\s*[:#]?\s*(\d{9})\b",
            re.IGNORECASE,
        ),
        r"\1\2:[PII-FILTERED-ROUTING]",
        "ROUTING",
    ),
    # Bank account number (in context)
    (
        re.compile(
            r"\b(account\s*(?:number|no|#))\s*[:#]?\s*(\d{8,17})\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-ACCT]",
        "ACCT",
    ),
    # US Passport number: letter + 8 digits
    (
        re.compile(
            r"\b(passport\s*(?:number|no|#)?)\s*[:#]?\s*([A-Z]\d{8})\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-PASSPORT]",
        "PASSPORT",
    ),
    # USCIS Alien Registration Number: A + 8-9 digits (NOT already in sentinel)
    (
        re.compile(r"\bA(\d{8,9})\b(?!\d)"),
        "[PII-FILTERED-ANUM]",
        "ANUM",
    ),
    # WA state driver's license format: WDL + 9 alphanumeric characters
    (
        re.compile(r"\bWDL[A-Z0-9]{9}\b"),
        "[PII-FILTERED-DL]",
        "DL",
    ),
    # Florida DL: 1 letter + exactly 12 digits — context-gated to avoid
    # false positives on order numbers, tracking IDs, etc.
    # AAMVA DL/ID Card Design Standard §7.4 (FL format).
    (
        re.compile(
            r"(?:driver.?s?\s*licen[sc]e|\bDL\b|\bFL\b|florida|state\s*id)"
            r"[^\n]{0,50}\b([A-Z]\d{12})\b",
            re.IGNORECASE,
        ),
        "[PII-FILTERED-DL]",
        "DL",
    ),
    # Context-gated multi-state DL patterns (AAMVA standard, applied only when
    # preceded by explicit license/ID keyword on the same line/sentence).
    # Covers CA ([A-Z]\d{7}), TX/CO/IL/OH/WI (8 digits), NY (9 digits or
    # 3-letters+6-digits), NJ (1-letter+14-alphanumeric), and other formats.
    # The keyword gate prevents false positives on phone/account numbers.
    (
        re.compile(
            r"\b(driver.?s?\s*licen[sc]e|state\s*id|state\s*identification|"
            r"id\s*(?:card|number|no)|DL\s*(?:number|no|#)?|"
            r"license\s*(?:number|no|#))\s*[:#]?\s*([A-Z]{0,3}\d{6,14}[A-Z0-9]{0,3})\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-DL]",
        "DL",
    ),
    # ── Non-US PII patterns ────────────────────────────────────────────────
    # India PAN (Permanent Account Number): 5 letters + 4 digits + 1 letter
    (
        re.compile(
            r"\b(PAN|permanent\s*account)\s*[:#]?\s*([A-Z]{5}\d{4}[A-Z])\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-PAN]",
        "PAN",
    ),
    # India Aadhaar: 12 digits, typically formatted as XXXX XXXX XXXX
    (
        re.compile(
            r"\b(aadhaar|aadhar|uid)\s*[:#]?\s*(\d{4}[\s-]?\d{4}[\s-]?\d{4})\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-AADHAAR]",
        "AADHAAR",
    ),
    # India Passport: single letter + 7 digits (context-gated)
    (
        re.compile(
            r"\b(passport\s*(?:number|no|#)?)\s*[:#]?\s*([A-Z]\d{7})\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-PASSPORT]",
        "PASSPORT",
    ),
    # UK National Insurance Number: 2 letters + 6 digits + 1 letter
    (
        re.compile(
            r"\b(NI(?:NO)?|national\s*insurance)\s*[:#]?\s*([A-Z]{2}\d{6}[A-Z])\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-NINO]",
        "NINO",
    ),
    # Canada SIN: 9 digits (formatted as XXX-XXX-XXX or XXXXXXXXX)
    (
        re.compile(
            r"\b(SIN|social\s*insurance)\s*[:#]?\s*(\d{3}[\s-]?\d{3}[\s-]?\d{3})\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-SIN]",
        "SIN",
    ),
    # Australia TFN: 8-9 digits (context-gated)
    (
        re.compile(
            r"\b(TFN|tax\s*file\s*number)\s*[:#]?\s*(\d{8,9})\b",
            re.IGNORECASE,
        ),
        r"\1:[PII-FILTERED-TFN]",
        "TFN",
    ),
    # ── Devanagari / Hindi script patterns ─────────────────────────────────
    # Phone: 10 consecutive Devanagari digits (e.g. ९८७६५४३२१०)
    (
        re.compile(r"(?<![\u0966-\u096F])[\u0966-\u096F]{10}(?![\u0966-\u096F])"),
        "[PII-FILTERED-PHONE]",
        "PHONE",
    ),
    # Phone with keyword context (Hindi/English prefix)
    (
        re.compile(
            r"(?:फ़ोन|फोन|मोबाइल|call|tel|ph(?:one)?)\s*[:#]?\s*[+0]?[\u0966-\u096F\s\-]{10,14}",
            re.IGNORECASE,
        ),
        "[PII-FILTERED-PHONE]",
        "PHONE",
    ),
    # Aadhaar in Devanagari digits (context-gated)
    (
        re.compile(
            r"(?:आधार(?:\s*कार्ड)?|aadhar|aadhaar|UID)\s*[:#]?\s*[\u0966-\u096F]{4}[\s\-]?[\u0966-\u096F]{4}[\s\-]?[\u0966-\u096F]{4}",
            re.IGNORECASE,
        ),
        "[PII-FILTERED-AADHAAR]",
        "AADHAAR",
    ),
    # Known family names in Devanagari script — loaded dynamically at runtime
    # from user_profile.yaml (§10.1, P0-3). See _build_deva_name_pattern()
    # below. This placeholder entry is replaced at first scan() call.
    # DO NOT hardcode real names here — user_profile.yaml is gitignored.
    (
        re.compile(r"(?!x)x"),  # Placeholder — never matches; replaced by _build_deva_name_pattern()
        "[PII-FILTERED-NAME]",
        "DEVA_NAME",
    ),
    # Hindi address locality keywords followed by a Devanagari token
    (
        re.compile(
            r"(?:गली|मोहल्ला|नगर|कॉलोनी|कालोनी|सेक्टर|मकान|पता|गाँव|ग्राम)\s+[\u0900-\u097F]+",
            re.IGNORECASE,
        ),
        "[PII-FILTERED-ADDR]",
        "ADDR",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Language fence — flags content in non-Latin scripts pending i18n validation
# (§10.1 Phase 0 safeguard; lifted after Phase 2 validation sign-off)
# ─────────────────────────────────────────────────────────────────────────────

# Matches any Devanagari character (U+0900–U+097F)
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# ─────────────────────────────────────────────────────────────────────────────
# Runtime Devanagari name loader (§10.1, P0-3 — never hardcode real names)
# ─────────────────────────────────────────────────────────────────────────────

_DEVA_NAME_PATTERN: re.Pattern | None = None   # cached after first load
_DEVA_NAME_LOADED: bool = False                # sentinel to skip retry if absent


def _build_deva_name_pattern() -> re.Pattern | None:
    """Build Devanagari name detection regex from user_profile.yaml.

    Reads `pii_guard.known_devanagari_names` list from config/user_profile.yaml.
    Result is cached in _DEVA_NAME_PATTERN for subsequent calls.

    Returns None if the config is absent, empty, or unreadable.
    """
    global _DEVA_NAME_PATTERN, _DEVA_NAME_LOADED  # noqa: PLW0603
    if _DEVA_NAME_LOADED:
        return _DEVA_NAME_PATTERN

    _DEVA_NAME_LOADED = True  # mark attempted even if we fail
    try:
        import yaml  # noqa: PLC0415
        config_dir = Path(__file__).resolve().parent.parent / "config"
        profile_path = config_dir / "user_profile.yaml"
        if not profile_path.exists():
            return None
        with open(profile_path, encoding="utf-8") as fh:
            profile = yaml.safe_load(fh) or {}
        names: list[str] = (profile.get("pii_guard") or {}).get("known_devanagari_names") or []
        if not names:
            return None
        escaped = [re.escape(n.strip()) for n in names if n.strip()]
        if not escaped:
            return None
        _DEVA_NAME_PATTERN = re.compile(r"(?:" + "|".join(escaped) + r")")
    except Exception:  # noqa: BLE001 — degrade gracefully
        pass
    return _DEVA_NAME_PATTERN

# ─────────────────────────────────────────────────────────────────────────────
# Core filter function
# ─────────────────────────────────────────────────────────────────────────────

def _apply_filter(text: str) -> tuple[str, dict[str, int]]:
    """Apply allowlist sentinels, PII substitutions, and restore sentinels.

    Returns:
        (filtered_text, found_types_dict)
        found_types_dict: {pii_type: count} — empty if no PII detected
    """
    original_text = text  # preserved for language-fence check

    # Step 1: protect allowlisted patterns with reversible sentinels
    sentinels: list[str] = []

    def _protect(m: re.Match) -> str:
        idx = len(sentinels)
        sentinels.append(m.group(0))
        return f"__AL{idx}LA__"

    for pattern in _ALLOWLIST_PATTERNS:
        text = pattern.sub(_protect, text)

    # Step 2: apply PII substitutions (order matters — ITIN before SSN)
    found: dict[str, int] = {}
    for compiled_re, replacement, pii_type in _PII_RULES:
        new_text, n = compiled_re.subn(replacement, text)
        if n > 0:
            found[pii_type] = found.get(pii_type, 0) + n
            text = new_text

    # Step 2b: dynamic Devanagari name check (loaded from user_profile.yaml at runtime)
    deva_name_re = _build_deva_name_pattern()
    if deva_name_re is not None:
        new_text, n = deva_name_re.subn("[PII-FILTERED-NAME]", text)
        if n > 0:
            found["DEVA_NAME"] = found.get("DEVA_NAME", 0) + n
            text = new_text

    # Step 3: restore sentinels
    for idx, original in enumerate(sentinels):
        text = text.replace(f"__AL{idx}LA__", original)

    # Step 4: language fence — flag any Devanagari content for human review
    # until full i18n PII coverage is validated (§10.1, Phase 0 safeguard).
    if _DEVANAGARI_RE.search(original_text):
        found["PII_UNVERIFIED_SCRIPT"] = found.get("PII_UNVERIFIED_SCRIPT", 0) + 1

    return text, found


# ─────────────────────────────────────────────────────────────────────────────
# Public API (importable by safe_cli.py and other scripts)
# ─────────────────────────────────────────────────────────────────────────────

def scan(text: str) -> tuple[bool, dict[str, int]]:
    """Scan text for PII patterns without modifying it.

    Returns:
        (pii_found: bool, found_types: dict)
    """
    _, found = _apply_filter(text)
    return bool(found), found


def filter_text(text: str) -> tuple[str, dict[str, int]]:
    """Filter PII from text, replacing with typed placeholders.

    Returns:
        (filtered_text: str, found_types: dict)
    """
    return _apply_filter(text)


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _log_to_audit(pii_types: str, action: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = (
        f"[{timestamp}] PII_FILTER | email_id: {_EMAIL_ID} | "
        f"type: {pii_types} | action: {action}"
    )
    try:
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass  # audit log not writable; don't crash
    print(entry, file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# CLI modes
# ─────────────────────────────────────────────────────────────────────────────

_IGNORE_DIRECTIVE = "pii-guard: ignore-file"


def _is_ignored_file(file_path: Path) -> bool:
    """Return True if the file contains the pii-guard: ignore-file directive in its first 5 lines.

    Use this to mark system source files whose content contains PII-like string
    literals as code/documentation rather than as actual PII.  Example:
        # pii-guard: ignore-file
    """
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if _IGNORE_DIRECTIVE in line:
                    return True
                if i >= 4:  # only scan first 5 lines
                    break
    except OSError:
        pass
    return False


def _read_input(args: list[str]) -> str:
    """Read from file argument or stdin."""
    if args:
        file_path = Path(args[0])
        if not file_path.exists():
            print(f"ERROR: file not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        return file_path.read_text(encoding="utf-8", errors="replace")
    return sys.stdin.read()


def do_scan(args: list[str]) -> None:
    """Scan mode: detect PII, exit 1 if found. Does not modify text."""
    if args and _is_ignored_file(Path(args[0])):
        sys.exit(0)  # file marked as known-safe; skip scanning
    text = _read_input(args)
    pii_found, found_types = scan(text)
    if pii_found:
        types_str = ",".join(sorted(found_types.keys()))
        print(f"PII_FOUND:{types_str}", file=sys.stderr)
        _log_to_audit(types_str, "scan_blocked")
        sys.exit(1)
    sys.exit(0)


def do_filter(args: list[str]) -> None:
    """Filter mode: replace PII tokens, print filtered text to stdout."""
    if args and _is_ignored_file(Path(args[0])):
        # File is marked as known-safe; pass through unchanged, exit 0.
        print(_read_input(args), end="")
        sys.exit(0)
    text = _read_input(args)
    filtered, found_types = filter_text(text)
    print(filtered, end="")
    if found_types:
        types_str = ",".join(sorted(found_types.keys()))
        print(f"PII_FOUND:{types_str}", file=sys.stderr)
        _log_to_audit(types_str, "filtered")
        sys.exit(1)
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in test suite (mirrors pii_guard.sh do_test() exactly)
# ─────────────────────────────────────────────────────────────────────────────

def do_test() -> None:
    """Run built-in test suite. Exit 0 on all pass, exit 1 on any failure."""
    passed = 0
    failed = 0

    def check(description: str, text: str, expect_blocked: bool, expect_token: str = "") -> None:
        nonlocal passed, failed
        _, found = _apply_filter(text)
        pii_found = bool(found)

        if expect_blocked and not pii_found:
            print(f"  ✗ FAIL (no block): {description}")
            print(f"         Input:  {text}")
            failed += 1
            return

        if not expect_blocked and pii_found:
            print(f"  ✗ FAIL (false block): {description}")
            print(f"         Input:  {text}")
            print(f"         Found:  {found}")
            failed += 1
            return

        if expect_blocked and expect_token:
            filtered, _ = _apply_filter(text)
            if expect_token not in filtered:
                print(f"  ✗ FAIL (token missing): {description}")
                print(f"         Expected: {expect_token}")
                print(f"         Input:    {text}")
                print(f"         Output:   {filtered}")
                failed += 1
                return

        print(f"  ✓ PASS: {description}")
        passed += 1

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("pii_guard.py built-in test suite")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    print("")
    print("── Section A: PII Detection ────────────────")
    check("SSN with dashes",      "Your SSN is 123-45-6789",               True,  "[PII-FILTERED-SSN]")
    check("SSN in statement",     "Social Security: 987-65-4321 on file",  True,  "[PII-FILTERED-SSN]")
    check("Visa (spaces)",        "Card: 4111 1111 1111 1111 charged",     True,  "[PII-FILTERED-CC]")
    check("Visa (dashes)",        "Card: 4111-1111-1111-1111 expired",     True,  "[PII-FILTERED-CC]")
    check("Mastercard",           "Card: 5500 0000 0000 0004 approved",    True,  "[PII-FILTERED-CC]")
    check("Amex",                 "Card: 3714 496353 98431 active",        True,  "[PII-FILTERED-CC]")
    check("Discover",             "Card: 6011 1111 1111 1117 active",      True,  "[PII-FILTERED-CC]")
    check("A-number",             "Alien Registration: A123456789 filed",  True,  "[PII-FILTERED-ANUM]")
    check("ITIN",                 "ITIN: 912-78-1234 on record",           True,  "[PII-FILTERED-ITIN]")
    check("Bank routing",         "routing number: 021000021 active",      True,  "[PII-FILTERED-ROUTING]")
    check("US Passport",          "Passport number: A12345678 verified",   True,  "[PII-FILTERED-PASSPORT]")
    check("WA Driver License",    "License: WDLMISH123AB issued",          True,  "[PII-FILTERED-DL]")
    # AAMVA multi-state DL formats (B7/M8)
    check("FL Driver License",    "FL DL: G123456789012 on file",           True,  "[PII-FILTERED-DL]")
    check("CA DL in context",     "Driver's License: A1234567 CA",         True,  "[PII-FILTERED-DL]")
    check("TX DL in context",     "DL number: 12345678",                   True,  "[PII-FILTERED-DL]")
    check("NY DL in context",     "license number: ABC123456",             True,  "[PII-FILTERED-DL]")
    check("State ID in context",  "State ID: B98765432 issued",            True,  "[PII-FILTERED-DL]")
    check("DL no. keyword",       "License No: 98765432 valid",            True,  "[PII-FILTERED-DL]")

    print("")
    print("── Section B: Allowlist (must NOT block) ───")
    check("USCIS IOE receipt",    "Receipt: IOE0915220715 received",       False)
    check("USCIS SRC receipt",    "Case: SRC2190050001 pending",           False)
    check("USCIS LIN receipt",    "Receipt LIN2190050001 approved",        False)
    check("Amazon order",         "Order: 112-3456789-1234567 shipped",    False)
    check("Masked account",       "Account ****1234 charged 47.99",        False)

    print("")
    print("── Section C: Mixed / Edge cases ──────────")
    check("A-number + USCIS receipt", "Receipt SRC2190050001 for A123456789",  True, "[PII-FILTERED-ANUM]")
    check("ITIN distinct from SSN",   "ITIN 912-78-1234 is not an SSN",        True, "[PII-FILTERED-ITIN]")

    print("")
    print("── Section D: Non-US PII Patterns ─────────")
    check("India PAN",             "PAN: ABCDE1234F on file",               True, "[PII-FILTERED-PAN]")
    check("India Aadhaar",         "Aadhaar: 1234 5678 9012 verified",      True, "[PII-FILTERED-AADHAAR]")
    check("India Passport",        "Passport number: J1234567 issued",      True, "[PII-FILTERED-PASSPORT]")
    check("UK NI Number",          "NINO: AB123456C active",                True, "[PII-FILTERED-NINO]")
    check("Canada SIN",            "SIN: 123-456-789 on record",            True, "[PII-FILTERED-SIN]")
    check("Australia TFN",         "TFN: 123456789 registered",             True, "[PII-FILTERED-TFN]")

    print("")
    print(f"Results: {passed} passed, {failed} failed")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    sys.exit(0 if failed == 0 else 1)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Ensure UTF-8 output on Windows (subprocess/pytest may default to cp1252)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print(
            "Usage: pii_guard.py {scan|filter|test|version} [file]\n"
            "\n"
            "  scan    — detect PII from stdin or file; exit 1 if any found\n"
            "  filter  — replace PII in stdin or file; filtered text to stdout; exit 1 if found\n"
            "  test    — run built-in test suite\n"
            "  version — print version\n"
            "\n"
            "  ARTHA_EMAIL_ID=<id>  — set email ID for accurate audit logging\n",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    if cmd == "scan":
        do_scan(args)
    elif cmd == "filter":
        do_filter(args)
    elif cmd == "test":
        do_test()
    elif cmd == "version":
        print(f"pii_guard.py {VERSION}")
        sys.exit(0)
    else:
        print(f"ERROR: unknown command '{cmd}'", file=sys.stderr)
        print("Valid commands: scan, filter, test, version", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
