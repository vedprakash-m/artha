#!/usr/bin/env bash
# DEPRECATED: This script is superseded by scripts/safe_cli.py.
# It will be removed in Artha v5.1. Ref: standardization.md §7.6.4
>&2 echo "WARNING: safe_cli.sh is deprecated. Use: python scripts/safe_cli.py"

# safe_cli.sh — Artha outbound PII wrapper for external CLI calls
#
# Usage:
#   safe_cli.sh gemini "What is the current EB-2 India priority date?"
#   safe_cli.sh copilot "Review this script for security issues"
#   safe_cli.sh <any-cli> "<query>"
#
# Safety model:
#   - Pipes query through pii_guard.sh scan BEFORE forwarding to external CLI
#   - If PII detected: blocks the call, logs to audit.md, exits 1
#   - If clean: executes the CLI call, logs query length (not content) to audit.md
#   - Exemption: Gemini Imagen calls (--image flag) bypass check — prompts are descriptive only
#
# CLAUDE.md instructs: "Never call gemini or copilot directly with user data.
# Always use ./scripts/safe_cli.sh."
#
# Ref: TS §8.7, §3.7.7, T-1A.1.6

set -euo pipefail

ARTHA_DIR="${HOME}/OneDrive/Artha"
AUDIT_LOG="${ARTHA_DIR}/state/audit.md"
PII_GUARD="${ARTHA_DIR}/scripts/pii_guard.sh"

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

log_to_audit() {
    local entry="$1"
    echo "${entry}" >> "${AUDIT_LOG}" 2>/dev/null || true
    echo "${entry}" >&2
}

die() {
    echo "ERROR: $1" >&2
    exit 1
}

# ─────────────────────────────────────────────
# Argument validation
# ─────────────────────────────────────────────

[[ $# -lt 2 ]] && {
    echo "Usage: safe_cli.sh <cli> <query> [additional_args...]" >&2
    echo "" >&2
    echo "  cli    — CLI to invoke (gemini, copilot, gh, etc.)" >&2
    echo "  query  — The query string to send (will be PII-scanned before sending)" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo '  safe_cli.sh gemini "What is the current EB-2 India priority date?"' >&2
    echo '  safe_cli.sh copilot "Review this script for security issues"' >&2
    exit 1
}

CLI="$1"
QUERY="$2"
shift 2
EXTRA_ARGS=("$@")   # Any additional arguments to pass to the CLI

# ─────────────────────────────────────────────
# Verify CLI is available
# ─────────────────────────────────────────────

command -v "${CLI}" >/dev/null 2>&1 || {
    log_to_audit "[$(date -Iseconds)] CLI_UNAVAILABLE | cli: ${CLI} | query_length: ${#QUERY}"
    die "CLI '${CLI}' not found in PATH. Check installation."
}

# ─────────────────────────────────────────────
# Verify pii_guard.sh is available and executable
# ─────────────────────────────────────────────

[[ -f "${PII_GUARD}" ]] || die "pii_guard.sh not found at ${PII_GUARD}. Run T-1A.1.5 setup."
[[ -x "${PII_GUARD}" ]] || die "pii_guard.sh is not executable. Run: chmod +x ${PII_GUARD}"

# ─────────────────────────────────────────────
# PII scan
# ─────────────────────────────────────────────

PII_SCAN_OUTPUT=""
PII_SCAN_EXIT=0
PII_SCAN_OUTPUT=$(echo "${QUERY}" | "${PII_GUARD}" scan 2>&1) || PII_SCAN_EXIT=$?

if (( PII_SCAN_EXIT != 0 )); then
    # PII detected — extract the type(s) from output
    PII_TYPES=$(echo "${PII_SCAN_OUTPUT}" | grep -o 'PII_FOUND:.*' | head -1 || echo "unknown")

    log_to_audit "[$(date -Iseconds)] OUTBOUND_PII_BLOCK | cli: ${CLI} | pii_types: ${PII_TYPES} | reason: PII detected in outbound query"
    echo "" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "⛔ BLOCKED: PII detected in outbound query" >&2
    echo "   CLI: ${CLI}" >&2
    echo "   Found: ${PII_TYPES}" >&2
    echo "   Action: Query blocked. Logged to audit.md." >&2
    echo "   Fix: Reformulate query without PII (use redacted references)." >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    exit 1
fi

# ─────────────────────────────────────────────
# Log the approved outbound call (no PII present)
# ─────────────────────────────────────────────

log_to_audit "[$(date -Iseconds)] CLI_CALL | cli: ${CLI} | query_length: ${#QUERY} | status: approved"

# ─────────────────────────────────────────────
# Execute the CLI
# ─────────────────────────────────────────────

# Map CLI aliases to actual commands
case "${CLI}" in
    gemini)
        # Gemini CLI uses -p/--prompt for non-interactive mode
        "${CLI}" -p "${QUERY}" "${EXTRA_ARGS[@]}"
        ;;
    copilot)
        # GitHub Copilot CLI: gh copilot suggest <query>
        gh copilot suggest "${QUERY}" "${EXTRA_ARGS[@]}"
        ;;
    gh)
        # Pass-through for gh CLI commands
        gh "${QUERY}" "${EXTRA_ARGS[@]}"
        ;;
    *)
        # Generic pass-through: cli "query" [extra_args]
        "${CLI}" "${QUERY}" "${EXTRA_ARGS[@]}"
        ;;
esac

# Log success
EXIT_CODE=$?
log_to_audit "[$(date -Iseconds)] CLI_RESULT | cli: ${CLI} | exit_code: ${EXIT_CODE}"
exit ${EXIT_CODE}
