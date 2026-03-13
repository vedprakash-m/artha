#!/usr/bin/env bash
# DEPRECATED: This script is superseded by scripts/pii_guard.py.
# It will be removed in Artha v5.1. Ref: standardization.md §7.6.4
>&2 echo "WARNING: pii_guard.sh is deprecated. Use: python scripts/pii_guard.py"

# pii_guard.sh — Artha pre-persist PII filter (Layer 1 of defense-in-depth)
#
# Usage:
#   pii_guard.sh scan   < input.txt    — detect only; exit 1 if PII found
#   pii_guard.sh filter < input.txt    — replace PII tokens; filtered text to stdout; exit 1 if found
#   pii_guard.sh test                  — run built-in test suite
#
# Architecture:
#   Layer 1 (this script): device-local Perl regex, BEFORE writing to state files.
#   Layer 2: Claude §8.2 semantic redaction, applied AFTER extraction.
#   Together = defense-in-depth. If this script exits non-zero, catch-up HALTS.
#
# Design: Single-pass Perl does all substitutions atomically.
#   Allowlisted patterns pre-protected with sentinels, restored after filtering.
#
# NOTE: macOS uses BSD grep (no -P flag). Perl handles all regex operations.
# Ref: TS §8.6, T-1A.1.5

set -euo pipefail

ARTHA_DIR="${HOME}/OneDrive/Artha"
AUDIT_LOG="${ARTHA_DIR}/state/audit.md"
EMAIL_ID="${ARTHA_EMAIL_ID:-unknown}"

# ─────────────────────────────────────────────
# Core Perl filter program (written to temp file at startup)
#
# Args: $ARGV[0] = mode ("scan" or "filter")  [unused — both modes filter]
# Stdin:  raw text
# Stdout: filtered text (PII tokens replaced)
# Stderr: "PII_FOUND:<types>" if any PII detected
# Exit:   0 = clean, 1 = PII found
# ─────────────────────────────────────────────

PERL_FILTER_SOURCE='use strict;
use warnings;

my $input = do { local $/; <STDIN> };
my %found;

# ── Step 1: Protect allowlisted patterns with sentinels ──────────────────
my @sentinels;
my @allowlist_re = (
    qr/IOE\d{10}/,
    qr/SRC\d{10}/,
    qr/LIN\d{10}/,
    qr/EAC\d{10}/,
    qr/WAC\d{10}/,
    qr/NBC\d{10}/,
    qr/MSC\d{10}/,
    qr/ZLA\d{10}/,
    qr/\d{3}-\d{7}-\d{7}/,
    qr/\*{4}\d{4}/,
);
for my $re (@allowlist_re) {
    $input =~ s/($re)/ do { push @sentinels, $1; "__AL" . $#sentinels . "LA__" } /ge;
}

# ── Step 2: PII substitutions (order matters: ITIN before SSN) ──────────

if ($input =~ s/\b9\d{2}-[789]\d-\d{4}\b/[PII-FILTERED-ITIN]/g) { $found{ITIN}++ }
if ($input =~ s/\b\d{3}-\d{2}-\d{4}\b/[PII-FILTERED-SSN]/g)     { $found{SSN}++  }
if ($input =~ s/\b(SSN|social[ _]security(?:[ _]number)?|tax[ _]id)\s*[:#]?\s*\d{9}\b/$1:[PII-FILTERED-SSN]/gi) { $found{SSN}++ }

if ($input =~ s/\b4\d{3}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b/[PII-FILTERED-CC]/g)          { $found{CC}++ }
if ($input =~ s/\b5[1-5]\d{2}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b/[PII-FILTERED-CC]/g)     { $found{CC}++ }
if ($input =~ s/\b3[47]\d{2}[\s\-]?\d{6}[\s\-]?\d{5}\b/[PII-FILTERED-CC]/g)                   { $found{CC}++ }
if ($input =~ s/\b6(?:011|5\d{2})[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b/[PII-FILTERED-CC]/g) { $found{CC}++ }

if ($input =~ s/\b(routing|aba|transit)(\s*(?:number|no|#)?)\s*[:#]?\s*(\d{9})\b/$1$2:[PII-FILTERED-ROUTING]/gi) { $found{ROUTING}++ }

if ($input =~ s/\b(account\s*(?:number|no|#))\s*[:#]?\s*(\d{8,17})\b/$1:[PII-FILTERED-ACCT]/gi) { $found{ACCT}++ }

if ($input =~ s/\b(passport\s*(?:number|no|#)?)\s*[:#]?\s*([A-Z]\d{8})\b/$1:[PII-FILTERED-PASSPORT]/gi) { $found{PASSPORT}++ }

if ($input =~ s/\bA(\d{8,9})\b(?!\d)/[PII-FILTERED-ANUM]/g) { $found{ANUM}++ }

if ($input =~ s/\bWDL[A-Z0-9]{9}\b/[PII-FILTERED-DL]/g) { $found{DL}++ }
if ($input =~ s/\b(driver.?s?\s*licen[sc]e|DL)\s*[:#]?\s*([A-Z0-9]{7,14})\b/$1:[PII-FILTERED-DL]/gi) { $found{DL}++ }

# ── Step 3: Restore sentinels ─────────────────────────────────────────────
for my $i (0 .. $#sentinels) {
    $input =~ s/__AL${i}LA__/$sentinels[$i]/g;
}

# ── Step 4: Output ────────────────────────────────────────────────────────
print $input;

if (%found) {
    my $types = join(",", sort keys %found);
    print STDERR "PII_FOUND:$types\n";
    exit 1;
}
exit 0;
'

# Write Perl source to temp file once (persists for lifetime of this shell process)
_PII_SCRIPT=""
_init_perl_script() {
    if [[ -z "${_PII_SCRIPT}" || ! -f "${_PII_SCRIPT}" ]]; then
        _PII_SCRIPT=$(mktemp /tmp/artha_pii_XXXXXXXX.pl)
        printf '%s' "${PERL_FILTER_SOURCE}" > "${_PII_SCRIPT}"
    fi
}

_cleanup_perl_script() {
    [[ -n "${_PII_SCRIPT:-}" && -f "${_PII_SCRIPT}" ]] && rm -f "${_PII_SCRIPT}"
}
trap _cleanup_perl_script EXIT

# ─────────────────────────────────────────────
# Logging helper
# ─────────────────────────────────────────────

log_to_audit() {
    local pii_types="$1"
    local action="$2"
    local entry
    entry="[$(date -Iseconds)] PII_FILTER | email_id: ${EMAIL_ID} | type: ${pii_types} | action: ${action}"
    echo "${entry}" >> "${AUDIT_LOG}" 2>/dev/null || true
    echo "${entry}" >&2
}

# ─────────────────────────────────────────────
# Scan mode — detect and block, no modification
# ─────────────────────────────────────────────

do_scan() {
    _init_perl_script

    local tmpdir
    tmpdir=$(mktemp -d)

    local in_f="${tmpdir}/in.txt"
    local out_f="${tmpdir}/out.txt"
    local err_f="${tmpdir}/err.txt"

    cat > "${in_f}"

    local perl_exit=0
    perl "${_PII_SCRIPT}" > "${out_f}" 2> "${err_f}" < "${in_f}" || perl_exit=$?
    rm -rf "${tmpdir}"

    if (( perl_exit != 0 )); then
        local pii_types
        pii_types=$(grep '^PII_FOUND:' "${err_f}" 2>/dev/null | sed 's/^PII_FOUND://' || echo "unknown")
        rm -f "${err_f}"
        log_to_audit "${pii_types}" "scan_blocked"
        exit 1
    fi
    rm -f "${err_f}"
    exit 0
}

# ─────────────────────────────────────────────
# Filter mode — replace PII, output filtered content
# ─────────────────────────────────────────────

do_filter() {
    _init_perl_script

    local tmpdir
    tmpdir=$(mktemp -d)

    local in_f="${tmpdir}/in.txt"
    local out_f="${tmpdir}/out.txt"
    local err_f="${tmpdir}/err.txt"

    cat > "${in_f}"

    local perl_exit=0
    perl "${_PII_SCRIPT}" > "${out_f}" 2> "${err_f}" < "${in_f}" || perl_exit=$?

    cat "${out_f}"
    rm -rf "${tmpdir}"

    if (( perl_exit != 0 )); then
        local pii_types
        pii_types=$(grep '^PII_FOUND:' "${err_f}" 2>/dev/null | sed 's/^PII_FOUND://' || echo "unknown")
        rm -f "${err_f}"
        log_to_audit "${pii_types}" "filtered"
        echo "PII FILTERED: ${pii_types}" >&2
        exit 1
    fi
    rm -f "${err_f}"
    exit 0
}

# ─────────────────────────────────────────────
# Built-in test suite
# ─────────────────────────────────────────────

do_test() {
    _init_perl_script

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "pii_guard.sh built-in test suite"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local pass=0
    local fail=0

    # check_test <description> <input> <expect_blocked: y/n> [<expect_token>]
    check_test() {
        local description="$1"
        local input="$2"
        local expect_blocked="$3"
        local expect_token="${4:-}"

        local tmpdir
        tmpdir=$(mktemp -d)
        local in_f="${tmpdir}/in.txt"
        local out_f="${tmpdir}/out.txt"
        local err_f="${tmpdir}/err.txt"
        printf '%s' "${input}" > "${in_f}"

        local perl_exit=0
        perl "${_PII_SCRIPT}" > "${out_f}" 2> "${err_f}" < "${in_f}" || perl_exit=$?
        local output
        output=$(cat "${out_f}")
        rm -rf "${tmpdir}"

        local test_pass=true

        if [[ "${expect_blocked}" == "y" && ${perl_exit} -eq 0 ]]; then
            test_pass=false
            echo "  ✗ FAIL (no block): ${description}"
            echo "         Input:  ${input}"
            echo "         Output: ${output}"
        elif [[ "${expect_blocked}" == "n" && ${perl_exit} -ne 0 ]]; then
            test_pass=false
            echo "  ✗ FAIL (false block): ${description}"
            echo "         Input:  ${input}"
            echo "         Output: ${output}"
        elif [[ "${expect_blocked}" == "y" && -n "${expect_token}" ]]; then
            # Use -F for literal string matching (brackets would be metacharacters otherwise)
            if ! echo "${output}" | grep -qF "${expect_token}"; then
                test_pass=false
                echo "  ✗ FAIL (token missing): ${description}"
                echo "         Expected: ${expect_token}"
                echo "         Input:    ${input}"
                echo "         Output:   ${output}"
            fi
        fi

        if [[ "${test_pass}" == "true" ]]; then
            echo "  ✓ PASS: ${description}"
            (( pass++ )) || true
        else
            (( fail++ )) || true
        fi
    }

    echo ""
    echo "── Section A: PII Detection ────────────────"
    check_test "SSN with dashes"      "Your SSN is 123-45-6789"               y "[PII-FILTERED-SSN]"
    check_test "SSN in statement"     "Social Security: 987-65-4321 on file"  y "[PII-FILTERED-SSN]"
    check_test "Visa (spaces)"        "Card: 4111 1111 1111 1111 charged"     y "[PII-FILTERED-CC]"
    check_test "Visa (dashes)"        "Card: 4111-1111-1111-1111 expired"     y "[PII-FILTERED-CC]"
    check_test "Mastercard"           "Card: 5500 0000 0000 0004 approved"    y "[PII-FILTERED-CC]"
    check_test "Amex"                 "Card: 3714 496353 98431 active"        y "[PII-FILTERED-CC]"
    check_test "Discover"             "Card: 6011 1111 1111 1117 active"      y "[PII-FILTERED-CC]"
    check_test "A-number"             "Alien Registration: A123456789 filed"  y "[PII-FILTERED-ANUM]"
    check_test "ITIN"                 "ITIN: 912-78-1234 on record"           y "[PII-FILTERED-ITIN]"
    check_test "Bank routing"         "routing number: 021000021 active"      y "[PII-FILTERED-ROUTING]"
    check_test "US Passport"          "Passport number: A12345678 verified"   y "[PII-FILTERED-PASSPORT]"
    check_test "WA Driver License"    "License: WDLMISH123AB issued"          y "[PII-FILTERED-DL]"

    echo ""
    echo "── Section B: Allowlist (must NOT block) ───"
    check_test "USCIS IOE receipt"    "Receipt: IOE0915220715 received"        n
    check_test "USCIS SRC receipt"    "Case: SRC2190050001 pending"            n
    check_test "USCIS LIN receipt"    "Receipt LIN2190050001 approved"         n
    check_test "Amazon order"         "Order: 112-3456789-1234567 shipped"     n
    check_test "Masked account"       "Account ****1234 charged 47.99"         n

    echo ""
    echo "── Section C: Mixed / Edge cases ──────────"
    check_test "A-number + USCIS receipt"  "Receipt SRC2190050001 for A123456789"  y "[PII-FILTERED-ANUM]"
    check_test "ITIN distinct from SSN"    "ITIN 912-78-1234 is not an SSN"        y "[PII-FILTERED-ITIN]"

    echo ""
    echo "Results: ${pass} passed, ${fail} failed"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    (( fail == 0 )) && exit 0 || exit 1
}

# ─────────────────────────────────────────────
# Main dispatch
# ─────────────────────────────────────────────

CMD="${1:-}"

case "${CMD}" in
    scan)   do_scan   ;;
    filter) do_filter ;;
    test)   do_test   ;;
    *)
        echo "Usage: pii_guard.sh {scan|filter|test}" >&2
        echo "" >&2
        echo "  scan    — detect PII from stdin; exit 1 if any found (no output modification)" >&2
        echo "  filter  — replace PII in stdin; filtered text to stdout; exit 1 if PII found" >&2
        echo "  test    — run built-in test suite" >&2
        echo "" >&2
        echo "  ARTHA_EMAIL_ID=<id>  — set Gmail message ID for accurate audit logging" >&2
        exit 1
        ;;
esac
