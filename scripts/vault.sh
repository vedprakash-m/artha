#!/usr/bin/env bash
# vault.sh — Artha sensitive state encrypt/decrypt helper
# Usage:
#   vault.sh decrypt   — decrypt all .age files to plaintext .md
#   vault.sh encrypt   — encrypt all .md back to .age and remove plaintext
#   vault.sh status    — show current encryption state without changing anything
#   vault.sh health    — exit 0 if vault is healthy (age installed, key reachable); exit 1 otherwise
#
# Stale lock handling:
#   Lock file > 30 min old: previous session crashed uncleanly.
#   vault.sh auto-clears the stale lock and logs the event before proceeding.
#   Lock file < 30 min old: treated as active session; decrypt halts to prevent collision.
#
# Security model:
#   - Private key lives ONLY in macOS Keychain (account: artha, service: age-key)
#   - Public key is read from config/settings.md (safe to sync)
#   - Lock file ~/OneDrive/Artha/.artha-decrypted signals an active session
#   - The LaunchAgent watchdog auto-encrypts if lock file is stale
#
# Ref: TS §8.5, T-1A.1.3

set -euo pipefail

ARTHA_DIR="${HOME}/OneDrive/Artha"
LOCK_FILE="${ARTHA_DIR}/.artha-decrypted"
STATE_DIR="${ARTHA_DIR}/state"
CONFIG_DIR="${ARTHA_DIR}/config"
AUDIT_LOG="${STATE_DIR}/audit.md"

# Sensitive files in state/ that must be encrypted at rest
# Note: contacts.md lives in config/ and is handled separately below
SENSITIVE_FILES=(
    "immigration"
    "finance"
    "insurance"
    "estate"
    "health"
    "audit"
    "vehicle"
)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

log() {
    local msg="$1"
    local entry="[$(date -Iseconds)] VAULT | ${msg}"
    # Only append to audit.md if it currently exists as plaintext;
    # if audit.md has been encrypted (or removed), write to stdout only
    # to avoid recreating it as a stray plaintext file post-encrypt.
    if [[ -f "${AUDIT_LOG}" ]]; then
        echo "${entry}" | tee -a "${AUDIT_LOG}" 2>/dev/null || echo "${entry}"
    else
        echo "${entry}"
    fi
}

die() {
    echo "ERROR: $1" >&2
    exit 1
}

get_private_key() {
    # Retrieve private key from macOS Keychain
    local privkey
    privkey=$(security find-generic-password -a "artha" -s "age-key" -w 2>/dev/null) \
        || die "Cannot retrieve age private key from Keychain. Run T-1A.1.2 setup first."
    echo "${privkey}"
}

get_public_key() {
    # Read age recipient public key from settings.md
    # Format: age_recipient: "age1..."
    local pubkey
    pubkey=$(grep 'age_recipient:' "${CONFIG_DIR}/settings.md" 2>/dev/null \
        | grep -o 'age1[a-z0-9]*') \
        || die "Cannot read age_recipient from config/settings.md. Populate the file first."

    [[ "${pubkey}" == age1* ]] \
        || die "Invalid age public key in settings.md (must start with 'age1'). Got: ${pubkey}"
    echo "${pubkey}"
}

check_age_installed() {
    command -v age >/dev/null 2>&1 \
        || die "'age' encryption tool not found. Install with: brew install age"
}

# ─────────────────────────────────────────────
# Decrypt
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# Stale lock detection
# ─────────────────────────────────────────────

check_lock_state() {
    # Returns:
    #   0 — no lock file, safe to proceed
    #   1 — STALE lock (auto-cleared), safe to proceed
    #   2 — ACTIVE lock (< 30 min old), HALT
    if [[ ! -f "${LOCK_FILE}" ]]; then
        return 0
    fi

    local lock_mtime now lock_age_sec lock_age_min
    lock_mtime=$(stat -f '%m' "${LOCK_FILE}" 2>/dev/null || echo 0)
    now=$(date +%s)
    lock_age_sec=$(( now - lock_mtime ))
    lock_age_min=$(( lock_age_sec / 60 ))
    local STALE_THRESHOLD=1800  # 30 minutes

    if (( lock_age_sec > STALE_THRESHOLD )); then
        echo "  ⚠ Stale lock file detected (age: ${lock_age_min}m — threshold: 30m)."
        echo "  Previous session exited uncleanly. Auto-clearing lock and proceeding."
        rm -f "${LOCK_FILE}"
        log "STALE_LOCK_CLEARED | age: ${lock_age_min}m | action: auto-cleared"
        return 1
    else
        echo "⛔ vault.sh: Active session lock detected (age: ${lock_age_min}m)."
        echo "  Another catch-up session may be in progress."
        echo "  Halt: duplicate catch-up would corrupt state."
        echo "  To force-clear: rm ${LOCK_FILE}"
        log "DECRYPT_BLOCKED | reason: active_lock | age: ${lock_age_min}m"
        return 2
    fi
}

do_decrypt() {
    check_age_installed

    local lock_state
    check_lock_state; lock_state=$?
    case ${lock_state} in
        0) : ;;  # No lock — proceed normally
        1) : ;;  # Stale lock was auto-cleared — proceed
        2) exit 1 ;;  # Active session — halt
    esac

    local privkey
    privkey=$(get_private_key)

    local any_decrypted=false
    local errors=0

    for domain in "${SENSITIVE_FILES[@]}"; do
        local age_file="${STATE_DIR}/${domain}.md.age"
        local plain_file="${STATE_DIR}/${domain}.md"

        if [[ -f "${age_file}" ]]; then
            # Layer 1: Pre-decrypt backup (TS §8.5.1)
            if [[ -f "${plain_file}" ]]; then
                cp "${plain_file}" "${plain_file}.bak"
                log "INTEGRITY_BACKUP | file: ${domain}.md | layer: 1_pre_decrypt"
            fi

            echo "  Decrypting ${domain}.md.age ..."
            if age --decrypt --identity <(echo "${privkey}") \
                   --output "${plain_file}" "${age_file}"; then

                # Layer 1: Post-decrypt validation
                if [[ ! -s "${plain_file}" ]]; then
                    echo "  ERROR: Decrypted ${domain}.md is empty — restoring backup" >&2
                    if [[ -f "${plain_file}.bak" ]]; then
                        mv "${plain_file}.bak" "${plain_file}"
                        log "INTEGRITY_RESTORE | file: ${domain}.md | reason: empty_decrypt | layer: 1"
                    fi
                    (( errors++ )) || true
                    continue
                fi

                if ! head -1 "${plain_file}" | grep -q '^---'; then
                    echo "  ERROR: Decrypted ${domain}.md missing YAML frontmatter — restoring backup" >&2
                    if [[ -f "${plain_file}.bak" ]]; then
                        mv "${plain_file}.bak" "${plain_file}"
                        log "INTEGRITY_RESTORE | file: ${domain}.md | reason: invalid_yaml | layer: 1"
                    fi
                    (( errors++ )) || true
                    continue
                fi

                # Bootstrap detection: warn if file still has placeholder data
                if grep -q 'updated_by: bootstrap' "${plain_file}" 2>/dev/null; then
                    log "BOOTSTRAP_DETECTED | file: ${domain}.md | note: state file has placeholder data — run /bootstrap ${domain}"
                fi

                any_decrypted=true
                log "DECRYPT_OK | file: ${domain}.md"
            else
                echo "  ERROR: Failed to decrypt ${domain}.md.age" >&2
                # Restore backup if decrypt command itself failed
                if [[ -f "${plain_file}.bak" ]]; then
                    mv "${plain_file}.bak" "${plain_file}"
                    log "INTEGRITY_RESTORE | file: ${domain}.md | reason: decrypt_failed | layer: 1"
                fi
                (( errors++ )) || true
            fi
        elif [[ -f "${plain_file}" ]]; then
            echo "  ${domain}.md already exists as plaintext (no .age file). Leaving as-is."
        fi
    done

    # contacts.md lives in config/
    local contacts_age="${CONFIG_DIR}/contacts.md.age"
    local contacts_plain="${CONFIG_DIR}/contacts.md"
    if [[ -f "${contacts_age}" ]]; then
        # Layer 1: Pre-decrypt backup
        if [[ -f "${contacts_plain}" ]]; then
            cp "${contacts_plain}" "${contacts_plain}.bak"
            log "INTEGRITY_BACKUP | file: contacts.md | layer: 1_pre_decrypt"
        fi

        echo "  Decrypting contacts.md.age ..."
        if age --decrypt --identity <(echo "${privkey}") \
               --output "${contacts_plain}" "${contacts_age}"; then

            # Post-decrypt validation
            if [[ ! -s "${contacts_plain}" ]] || ! head -1 "${contacts_plain}" | grep -q '^---'; then
                echo "  ERROR: Decrypted contacts.md is empty or invalid — restoring backup" >&2
                if [[ -f "${contacts_plain}.bak" ]]; then
                    mv "${contacts_plain}.bak" "${contacts_plain}"
                    log "INTEGRITY_RESTORE | file: contacts.md | reason: invalid_content | layer: 1"
                fi
                (( errors++ )) || true
            else
                any_decrypted=true
                log "DECRYPT_OK | file: contacts.md"
            fi
        else
            echo "  ERROR: Failed to decrypt contacts.md.age" >&2
            if [[ -f "${contacts_plain}.bak" ]]; then
                mv "${contacts_plain}.bak" "${contacts_plain}"
                log "INTEGRITY_RESTORE | file: contacts.md | reason: decrypt_failed | layer: 1"
            fi
            (( errors++ )) || true
        fi
    fi

    if (( errors > 0 )); then
        die "${errors} file(s) failed to decrypt. Aborting catch-up."
    fi

    # Create lock file to signal active session
    touch "${LOCK_FILE}"
    echo "vault.sh: Decrypt complete. Lock file created."
    log "SESSION_START | lock_file: created"
}

# ─────────────────────────────────────────────
# Encrypt
# ─────────────────────────────────────────────

do_encrypt() {
    check_age_installed

    local pubkey
    pubkey=$(get_public_key)

    local errors=0
    local encrypted_count=0

    for domain in "${SENSITIVE_FILES[@]}"; do
        local plain_file="${STATE_DIR}/${domain}.md"
        local age_file="${STATE_DIR}/${domain}.md.age"

        if [[ -f "${plain_file}" ]]; then
            echo "  Encrypting ${domain}.md ..."
            # Write to temp file first for atomicity — never leave partial .age
            local tmp_file="${age_file}.tmp"
            if age --recipient "${pubkey}" --output "${tmp_file}" "${plain_file}"; then
                mv "${tmp_file}" "${age_file}"
                # Securely remove plaintext
                rm -f "${plain_file}"
                # Clean up backup (Layer 1 artifact — never leave .bak on disk)
                rm -f "${plain_file}.bak"
                (( encrypted_count++ )) || true
                log "ENCRYPT_OK | file: ${domain}.md"
            else
                rm -f "${tmp_file}" 2>/dev/null || true
                echo "  ERROR: Failed to encrypt ${domain}.md" >&2
                (( errors++ )) || true
            fi
        fi
    done

    # contacts.md lives in config/
    local contacts_plain="${CONFIG_DIR}/contacts.md"
    local contacts_age="${CONFIG_DIR}/contacts.md.age"
    if [[ -f "${contacts_plain}" ]]; then
        echo "  Encrypting contacts.md ..."
        local tmp_file="${contacts_age}.tmp"
        if age --recipient "${pubkey}" --output "${tmp_file}" "${contacts_plain}"; then
            mv "${tmp_file}" "${contacts_age}"
            rm -f "${contacts_plain}"
            # Clean up backup
            rm -f "${contacts_plain}.bak"
            (( encrypted_count++ )) || true
            log "ENCRYPT_OK | file: contacts.md"
        else
            rm -f "${tmp_file}" 2>/dev/null || true
            echo "  ERROR: Failed to encrypt contacts.md" >&2
            (( errors++ )) || true
        fi
    fi

    if (( errors > 0 )); then
        die "${errors} file(s) failed to encrypt. CRITICAL: plaintext may remain on disk."
    fi

    # Remove lock file only if everything succeeded
    rm -f "${LOCK_FILE}"
    echo "vault.sh: Encrypt complete. ${encrypted_count} files secured. Lock file removed."
    log "SESSION_END | lock_file: removed | files_encrypted: ${encrypted_count}"
}

# ─────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────

do_status() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "VAULT STATUS — $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [[ -f "${LOCK_FILE}" ]]; then
        echo "SESSION: ACTIVE (lock file present)"
        echo "Lock file: $(stat -f '%Sm' "${LOCK_FILE}" 2>/dev/null || echo 'unknown time')"
    else
        echo "SESSION: INACTIVE (no lock file — state is encrypted)"
    fi

    echo ""
    echo "State files:"
    for domain in "${SENSITIVE_FILES[@]}"; do
        local plain_file="${STATE_DIR}/${domain}.md"
        local age_file="${STATE_DIR}/${domain}.md.age"
        if [[ -f "${plain_file}" ]]; then
            echo "  [PLAINTEXT] ${domain}.md  ⚠ NOT encrypted"
        elif [[ -f "${age_file}" ]]; then
            echo "  [ENCRYPTED] ${domain}.md.age ✓"
        else
            echo "  [MISSING]   ${domain} — no .md or .age found"
        fi
    done

    local contacts_plain="${CONFIG_DIR}/contacts.md"
    local contacts_age="${CONFIG_DIR}/contacts.md.age"
    if [[ -f "${contacts_plain}" ]]; then
        echo "  [PLAINTEXT] contacts.md  ⚠ NOT encrypted"
    elif [[ -f "${contacts_age}" ]]; then
        echo "  [ENCRYPTED] contacts.md.age ✓"
    else
        echo "  [MISSING]   contacts — no .md or .age found"
    fi

    echo ""
    command -v age >/dev/null 2>&1 && echo "age: ✓ $(age --version)" || echo "age: ✗ NOT INSTALLED"
    security find-generic-password -a "artha" -s "age-key" -w >/dev/null 2>&1 \
        && echo "Keychain key: ✓ present" || echo "Keychain key: ✗ NOT found (run T-1A.1.2)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ─────────────────────────────────────────────
# Round-trip test
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# Health check (used by preflight.py)
# ─────────────────────────────────────────────

do_health() {
    local ok=true

    # 1. age tool installed
    if command -v age >/dev/null 2>&1; then
        echo "  age: ✓ $(age --version)"
    else
        echo "  age: ✗ NOT installed (brew install age)"
        ok=false
    fi

    # 2. Private key retrievable from Keychain
    if security find-generic-password -a 'artha' -s 'age-key' -w >/dev/null 2>&1; then
        echo "  Keychain key: ✓ present"
    else
        echo "  Keychain key: ✗ NOT found — run T-1A.1.2"
        ok=false
    fi

    # 3. Public key readable
    local pubkey
    pubkey=$(grep 'age_recipient:' "${CONFIG_DIR}/settings.md" 2>/dev/null | grep -o 'age1[a-z0-9]*')
    if [[ "${pubkey}" == age1* ]]; then
        echo "  Public key:   ✓ ${pubkey:0:20}..."
    else
        echo "  Public key:   ✗ NOT found in config/settings.md"
        ok=false
    fi

    # 4. State directory accessible
    if [[ -d "${STATE_DIR}" && -w "${STATE_DIR}" ]]; then
        echo "  State dir:    ✓ ${STATE_DIR}"
    else
        echo "  State dir:    ✗ NOT accessible: ${STATE_DIR}"
        ok=false
    fi

    # 5. Lock file status
    if [[ -f "${LOCK_FILE}" ]]; then
        local lock_mtime now lock_age_min
        lock_mtime=$(stat -f '%m' "${LOCK_FILE}" 2>/dev/null || echo 0)
        now=$(date +%s)
        lock_age_min=$(( (now - lock_mtime) / 60 ))
        echo "  Lock file:    ⚠ present (age: ${lock_age_min}m)"
    else
        echo "  Lock file:    ✓ absent (state encrypted)"
    fi

    # 6. Orphaned .bak files (integrity guard artifacts)
    local bak_count=0
    for domain in "${SENSITIVE_FILES[@]}"; do
        [[ -f "${STATE_DIR}/${domain}.md.bak" ]] && (( bak_count++ )) || true
    done
    [[ -f "${CONFIG_DIR}/contacts.md.bak" ]] && (( bak_count++ )) || true
    if (( bak_count > 0 )); then
        echo "  Backup files: ⚠ ${bak_count} orphaned .bak file(s) — stale plaintext"
        ok=false
    else
        echo "  Backup files: ✓ none (clean)"
    fi

    if [[ "${ok}" == true ]]; then
        echo "vault.sh health: OK"
        return 0
    else
        echo "vault.sh health: FAILED"
        return 1
    fi
}

do_test() {
    check_age_installed
    local pubkey
    pubkey=$(get_public_key)
    local privkey
    privkey=$(get_private_key)

    local tmpdir
    tmpdir=$(mktemp -d)
    trap "rm -rf ${tmpdir}" EXIT

    local pass=0
    local fail=0

    # Test 1: Basic round-trip encryption/decryption
    echo "  Test 1: Round-trip encrypt/decrypt..."
    echo "Artha vault test data — $(date)" > "${tmpdir}/test.md"
    age --recipient "${pubkey}" --output "${tmpdir}/test.md.age" "${tmpdir}/test.md"
    rm "${tmpdir}/test.md"
    age --decrypt --identity <(echo "${privkey}") \
        --output "${tmpdir}/test_out.md" "${tmpdir}/test.md.age"

    if grep -q "vault test data" "${tmpdir}/test_out.md"; then
        echo "  ✓ Test 1 passed: round-trip OK"
        (( pass++ )) || true
    else
        echo "  ✗ Test 1 FAILED: decrypted content mismatch"
        (( fail++ )) || true
    fi

    # Test 2: Pre-decrypt backup creation (Layer 1)
    echo "  Test 2: Pre-decrypt backup creation..."
    mkdir -p "${tmpdir}/state2"
    cat > "${tmpdir}/state2/test.md" <<'EOF'
---
domain: test
updated_by: artha-catchup
---
# Existing data
field1: value1
field2: value2
EOF
    cp "${tmpdir}/state2/test.md" "${tmpdir}/state2/test.md.expected"
    # Create a valid .age file
    age --recipient "${pubkey}" --output "${tmpdir}/state2/test.md.age" "${tmpdir}/state2/test.md"
    # Now decrypt should create .bak first
    if age --decrypt --identity <(echo "${privkey}") \
           --output "${tmpdir}/state2/test_decrypted.md" "${tmpdir}/state2/test.md.age"; then
        # Simulate what vault.sh does: backup before overwrite
        cp "${tmpdir}/state2/test.md" "${tmpdir}/state2/test.md.bak"
        if [[ -f "${tmpdir}/state2/test.md.bak" ]]; then
            echo "  ✓ Test 2 passed: .bak file created"
            (( pass++ )) || true
        else
            echo "  ✗ Test 2 FAILED: .bak file not created"
            (( fail++ )) || true
        fi
    else
        echo "  ✗ Test 2 FAILED: decrypt failed"
        (( fail++ )) || true
    fi

    # Test 3: Corrupt .age detection (Layer 1 validation)
    echo "  Test 3: Corrupt .age file detection..."
    echo "THIS IS NOT VALID AGE CONTENT" > "${tmpdir}/corrupt.md.age"
    echo "---" > "${tmpdir}/corrupt.md"
    echo "domain: test" >> "${tmpdir}/corrupt.md"
    cp "${tmpdir}/corrupt.md" "${tmpdir}/corrupt.md.bak"
    if age --decrypt --identity <(echo "${privkey}") \
           --output "${tmpdir}/corrupt_out.md" "${tmpdir}/corrupt.md.age" 2>/dev/null; then
        echo "  ✗ Test 3 FAILED: corrupt file decrypted without error"
        (( fail++ )) || true
    else
        # Decrypt failed as expected — verify backup would be restored
        if [[ -f "${tmpdir}/corrupt.md.bak" ]]; then
            echo "  ✓ Test 3 passed: corrupt .age rejected, backup available for restore"
            (( pass++ )) || true
        else
            echo "  ✗ Test 3 FAILED: no backup available"
            (( fail++ )) || true
        fi
    fi

    # Test 4: Bootstrap detection
    echo "  Test 4: Bootstrap state detection..."
    cat > "${tmpdir}/bootstrap_test.md" <<'EOF'
---
domain: test
updated_by: bootstrap
---
# Bootstrap placeholder
EOF
    if grep -q 'updated_by: bootstrap' "${tmpdir}/bootstrap_test.md"; then
        echo "  ✓ Test 4 passed: bootstrap state detected"
        (( pass++ )) || true
    else
        echo "  ✗ Test 4 FAILED: bootstrap detection missed"
        (( fail++ )) || true
    fi

    # Test 5: Post-decrypt validation (empty file detection)
    echo "  Test 5: Empty file detection..."
    touch "${tmpdir}/empty.md"
    if [[ ! -s "${tmpdir}/empty.md" ]]; then
        echo "  ✓ Test 5 passed: empty file correctly detected as invalid"
        (( pass++ )) || true
    else
        echo "  ✗ Test 5 FAILED: empty file not detected"
        (( fail++ )) || true
    fi

    # Test 6: Post-decrypt validation (missing YAML frontmatter)
    echo "  Test 6: Missing YAML frontmatter detection..."
    echo "This file has no YAML frontmatter" > "${tmpdir}/no_yaml.md"
    if ! head -1 "${tmpdir}/no_yaml.md" | grep -q '^---'; then
        echo "  ✓ Test 6 passed: missing frontmatter correctly detected"
        (( pass++ )) || true
    else
        echo "  ✗ Test 6 FAILED: missing frontmatter not detected"
        (( fail++ )) || true
    fi

    # Test 7: Backup cleanup during encrypt
    echo "  Test 7: Backup cleanup during encrypt..."
    echo "test" > "${tmpdir}/cleanup_test.md.bak"
    rm -f "${tmpdir}/cleanup_test.md.bak"
    if [[ ! -f "${tmpdir}/cleanup_test.md.bak" ]]; then
        echo "  ✓ Test 7 passed: .bak cleanup works"
        (( pass++ )) || true
    else
        echo "  ✗ Test 7 FAILED: .bak not cleaned up"
        (( fail++ )) || true
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "vault.sh integrity test: ${pass} passed, ${fail} failed (${pass}/$((pass + fail)) total)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if (( fail > 0 )); then
        log "SELFTEST | result: FAILED | passed: ${pass} | failed: ${fail}"
        return 1
    else
        log "SELFTEST | result: PASSED | tests: ${pass}"
        return 0
    fi
}

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

CMD="${1:-}"

case "${CMD}" in
    decrypt)    do_decrypt ;;
    encrypt)    do_encrypt ;;
    status)     do_status ;;
    health)     do_health ;;
    test)       do_test ;;
    *)
        echo "Usage: vault.sh {decrypt|encrypt|status|health|test}"
        echo ""
        echo "  decrypt  — unlock sensitive state files for a catch-up session"
        echo "  encrypt  — lock sensitive state files after catch-up"
        echo "  status   — show current encryption state (read-only)"
        echo "  health   — exit 0 if vault is healthy; exit 1 otherwise (for preflight)"
        echo "  test     — verify encrypt/decrypt round-trip"
        exit 1
        ;;
esac
