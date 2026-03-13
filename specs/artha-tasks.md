# Artha — Implementation Plan

> **Version**: 2.2 | **Date**: March 2026 (v2.2: WorkIQ Calendar Integration — adds Group 24 tasks for Microsoft Work Calendar via WorkIQ MCP; includes preflight detection, partial redaction, field-merge dedup, cross-domain conflict scoring, duration-based density, meeting-triggered OIs, Teams Join actions, graceful Mac degradation, and 13-week rolling window; risks R-11 through R-14 added) (v2.1: Intelligence Amplification workstreams T–X from deep expert review added to Phase 2A — Goal Sprint, Fastest Next Action, Calendar Intelligence, Privacy Hardening, Observability & Coaching; Phase 2B adds Canvas LMS API, Apple Health, Tax Automation, Subscription ROI, College Countdown; Phase 3 adds WhatsApp send, Wallet Card, India TZ; open decisions TD-26 through TD-30 added) (v2.0: Supercharge workstreams K–S — 18 enhancement items from expert review added to Phase 2A; comprehensive task breakdown with P0 Data Integrity Guard and Bootstrap Command, P1 intelligence engines, briefing compression, system resilience, and life scorecard; Phase 2A dependency map updated; success criteria expanded; open decisions TD-20 through TD-25 added)
> **Author**: Vedprakash Mishra | **Classification**: Personal & Confidential
> **Implements**: PRD v4.1, Tech Spec v2.2, UX Spec v1.5

---

## How to Use This Plan

Each task has: a unique ID (`T-{phase}.{group}.{seq}`), a title, a description, dependencies, acceptance criteria, estimated effort, and security notes where relevant. Tasks are ordered within each phase by dependency — complete them top-to-bottom unless parallelism is noted.

**Status tracking convention:**
- `[ ]` — Not started
- `[~]` — In progress
- `[x]` — Complete
- `[!]` — Blocked (note blocker in task)
- `[-]` — Deferred / descoped

**Priority key:** P0 = must-have for phase gate, P1 = high value, P2 = nice-to-have.

**Cross-references:** PRD = `artha-prd.md`, TS = `artha-tech-spec.md`, UX = `artha-ux-spec.md`.

---

## Dependency Map — Phase 1A Critical Path

```
Directory Structure ──────────────────────────────────────────────────────────┐
  │                                                                           │
  ├── age Install + Keypair ──► vault.sh ──► Claude Code Hooks               │
  │                                          (PreToolUse/Stop)                │
  ├── pii_guard.sh ────────────────────────────────────────────────────┐      │
  │                                                                    │      │
  ├── safe_cli.sh ─────────────────────────────────────────────────┐   │      │
  │                                                                 │   │      │
  ├── Google Cloud OAuth ──┬── Gmail MCP ──────────────────┐       │   │      │
  │                        └── Calendar MCP ───────────┐   │       │   │      │
  │                                                    │   │       │   │      │
  ├── Gemini CLI Verify ───────────────────────────┐   │   │       │   │      │
  │                                                 │   │   │       │   │      │
  ├── Copilot CLI Verify ──────────────────────┐   │   │   │       │   │      │
  │                                             │   │   │   │       │   │      │
  ├── Artha.md + CLAUDE.md ◄── (needs MCP +    │   │   │   │       │   │      │
  │     │                       multi-LLM +     │   │   │   │       │   │      │
  │     │                       hooks +         │   │   │   │       │   │      │
  │     │                       slash cmds)     │   │   │   │       │   │      │
  │     │                                       │   │   │   │       │   │      │
  │     ├── Domain Prompts (imm, fin, kids, comms)  │   │   │       │   │      │
  │     │                                       │   │   │   │       │   │      │
  │     ├── Slash Commands ─────────────────────┘   │   │   │       │   │      │
  │     │                                           │   │   │       │   │      │
  │     └── Multi-LLM Routing Rules ────────────────┘   │   │       │   │      │
  │                                                      │   │       │   │      │
  ├── State File Bootstrap ◄── (needs encryption)────────┘   │       │   │      │
  │                                                          │       │   │      │
  ├── Config Files (settings, contacts, occasions, registry) │       │   │      │
  │                                                          │       │   │      │
  ├── Email Delivery Setup ──────────────────────────────────┘       │   │      │
  │                                                                   │   │      │
  └── FIRST CATCH-UP ◄── ALL ABOVE ──────────────────────────────────┘───┘──────┘
        │
        └── Iterate on Artha.md + prompts based on results
```

---

## Phase 1A — Core Foundation (Weeks 1–2)

> **Objective**: Stand up the complete Artha runtime — security, MCP integrations, instruction files, P0 domain prompts, and first successful catch-up — with zero custom code beyond `vault.sh`, `pii_guard.sh`, and `safe_cli.sh`.
> **Estimated total effort**: 21–25 hours
> **Success gate**: First end-to-end catch-up completes, briefing is emailed, state files are encrypted at rest, PII filter passes.

---

### Group 1: Security Foundation

> Security is built FIRST. Nothing processes personal data until encryption, PII filtering, and outbound protection are operational. (PRD P5, TS §8, UX §14.1)

#### T-1A.1.1 — Install `age` encryption tool

- [x] **Priority**: P0
- **Status Note**: COMPLETE — age v1.3.1 installed via brew.
- **Description**: Install `age` (https://age-encryption.org/) via Homebrew. This provides the encryption layer for all sensitive state files at rest and during OneDrive sync.
- **Dependencies**: None
- **Steps**:
  1. `brew install age`
  2. Verify: `age --version`
- **Acceptance Criteria**:
  - `age` command is available in PATH
  - Version is current stable release
- **Effort**: 5 minutes
- **Ref**: TS §8.5

---

#### T-1A.1.2 — Generate `age` keypair and store in macOS Keychain

- [x] **Priority**: P0
- **Status Note**: COMPLETE — Keypair generated. Private key stored in Keychain (account: artha, service: age-key). Public key (age1asdktxzk9kmuhw9s259stfg26qmqqkrag60h32jwwfg54l2ksd4qq426g8) written to config/settings.md.
- **Description**: Generate an `age` identity (private key) and recipient (public key). Store the private key in macOS Keychain — never on OneDrive, never in plaintext files. The public key will be stored in `settings.md` (safe to sync).
- **Dependencies**: T-1A.1.1
- **Steps**:
  1. Generate keypair: `age-keygen -o /dev/stdout`
  2. Extract public key (line starting with `age1...`)
  3. Extract private key (line NOT starting with `#`)
  4. Store private key in Keychain:
     ```bash
     echo "$AGE_PRIVKEY" | security add-generic-password -a artha -s age-key -w
     ```
  5. Record public key for later use in `settings.md`
  6. Verify retrieval: `security find-generic-password -a artha -s age-key -w`
- **Acceptance Criteria**:
  - Private key stored in macOS Keychain under account `artha`, service `age-key`
  - Private key retrievable via `security` CLI
  - Public key recorded for `settings.md` (T-1A.2.5)
  - No key material exists in plaintext files anywhere
- **Effort**: 15 minutes
- **Security**: Key material exists ONLY in Keychain. If Mac is lost, keys must be re-provisioned.
- **Ref**: TS §8.5

---

#### T-1A.1.3 — Create `vault.sh` encrypt/decrypt script

- [x] **Priority**: P0
- **Status Note**: COMPLETE — vault.sh created and round-trip tested. 8/8 sensitive files encrypted. vault.sh test passes.
- **Description**: Create `~/OneDrive/Artha/scripts/vault.sh` — the helper script that encrypts sensitive state files after each catch-up and decrypts them at the start. This is the gatekeeper for OneDrive sync security.
- **Dependencies**: T-1A.1.2, T-1A.2.1 (directory structure)
- **Implementation spec**:
  - ~30 lines bash
  - Two modes: `vault.sh decrypt` and `vault.sh encrypt`
  - Sensitive files list: `immigration`, `finance`, `insurance`, `estate`, `health`, `audit`, `contacts`
  - Reads `age` private key from Keychain via `security find-generic-password`
  - Reads `age` public key from `config/settings.md`
  - `decrypt`: For each sensitive file, if `.age` exists, decrypt to `.md`; create `.artha-decrypted` lock file
  - `encrypt`: For each sensitive file, if `.md` exists, encrypt to `.age`, remove `.md`, remove lock file
  - `set -euo pipefail` for strict error handling
- **Acceptance Criteria**:
  - `vault.sh decrypt` decrypts all `.age` files to `.md` plaintext
  - `vault.sh encrypt` encrypts all `.md` back to `.age` and removes plaintext
  - Lock file `.artha-decrypted` created on decrypt, removed on encrypt
  - Script exits non-zero on any error (Keychain not found, file permission issue)
  - Test round-trip: encrypt → decrypt → diff confirms no data loss
- **Effort**: 45 minutes
- **Ref**: TS §8.5, §11.1

---

#### T-1A.1.4 — Configure vault.sh crash recovery

- [x] **Priority**: P0
- **Status Note**: COMPLETE — LaunchAgent installed and loaded: launchctl list shows com.artha.vault-watchdog.
- **Description**: If a catch-up session crashes before `vault.sh encrypt` runs, plaintext sensitive files could briefly sync to OneDrive. Two mitigations: (1) configure OneDrive selective sync to exclude `state/*.md` for files that also have `.age` equivalents, and (2) create a macOS LaunchAgent watchdog that auto-encrypts stale decrypted files.
- **Dependencies**: T-1A.1.3
- **Steps**:
  1. Configure OneDrive selective sync: exclude plaintext versions of sensitive state files from sync. Only `.age` files sync.
  2. Create LaunchAgent plist (`~/Library/LaunchAgents/com.artha.vault-watchdog.plist`) that runs every 5 minutes:
     - Check if `.artha-decrypted` lock file exists
     - Check if any `claude` process is running
     - If lock exists AND no claude process → run `vault.sh encrypt`
  3. Load the LaunchAgent: `launchctl load ~/Library/LaunchAgents/com.artha.vault-watchdog.plist`
- **Acceptance Criteria**:
  - OneDrive does NOT sync plaintext sensitive `.md` files (only `.age`)
  - LaunchAgent fires within 5 minutes of a stale lock file
  - `vault.sh encrypt` runs automatically if session appears dead
  - Manual test: create lock file, verify encrypt fires within 5 min
- **Effort**: 45 minutes
- **Ref**: TS §8.5 (crash recovery section)

---

#### T-1A.1.5 — Create `pii_guard.sh` pre-persist PII filter

- [x] **Priority**: P0
- **Status Note**: COMPLETE — 19/19 tests passing. Uses Perl temp-file pattern to avoid stdin conflict.
- **Description**: Create `~/OneDrive/Artha/scripts/pii_guard.sh` — the device-local PII filter that validates all extracted data before it is written to state files. This is Layer 1 of defense-in-depth (Layer 2 is Claude's semantic redaction per TS §8.2).
- **Dependencies**: T-1A.2.1 (directory structure)
- **Implementation spec**:
  - ~80 lines bash + `grep -P` (Perl-compatible regex)
  - Two modes: `scan` (detect only, exit 1 if PII found) and `filter` (detect and replace on stdout)
  - Detection patterns (from TS §8.6):
    - SSN: `\b\d{3}-\d{2}-\d{4}\b` → `[PII-FILTERED-SSN]`
    - SSN (no dashes): `\b\d{9}\b` near context words → `[PII-FILTERED-SSN]`
    - Credit card (Visa/MC/Amex/Discover): patterns per TS §8.6 → `[PII-FILTERED-CC]`
    - Bank routing: `\b\d{9}\b` near "routing"/"ABA" → `[PII-FILTERED-ROUTING]`
    - Bank account: `\b\d{8,17}\b` near "account" → `[PII-FILTERED-ACCT]`
    - US Passport: `\b[A-Z]\d{8}\b` near "passport" → `[PII-FILTERED-PASSPORT]`
    - A-number: `\bA\d{8,9}\b` → `[PII-FILTERED-ANUM]`
    - ITIN: `\b9\d{2}-[7-9]\d-\d{4}\b` → `[PII-FILTERED-ITIN]`
    - Driver's license (WA): `WDL[A-Z0-9]{9}` → `[PII-FILTERED-DL]`
  - Allowlist support: reads `## PII Allowlist` section from domain prompt files and exempts matching patterns
  - Audit output: logs each detection to stdout (type, source, action) for appending to `audit.md`
- **Acceptance Criteria**:
  - `scan` mode: detects synthetic SSN `123-45-6789` → exits 1
  - `scan` mode: detects synthetic CC `4111-1111-1111-1111` → exits 1
  - `filter` mode: replaces SSN with `[PII-FILTERED-SSN]` in output
  - `filter` mode: replaces CC with `[PII-FILTERED-CC]` in output
  - Allowlist: USCIS receipt numbers (`IOE-\d{10}`, `SRC\d{10}`, `LIN\d{10}`) are NOT flagged
  - Allowlist: Amazon order numbers (`\d{3}-\d{7}-\d{7}`) are NOT flagged
  - Non-zero exit halts catch-up (enforced by Artha.md instruction)
  - Audit log entries match format: `[timestamp] PII_FILTER | email_id: X | type: Y | action: filtered`
- **Effort**: 1.5 hours
- **Security**: This is mandatory — catch-up HALTS if this script fails or is missing.
- **Ref**: TS §8.6

---

#### T-1A.1.6 — Create `safe_cli.sh` outbound PII wrapper

- [x] **Priority**: P0
- **Status Note**: COMPLETE — Script created and verified.
- **Description**: Create `~/OneDrive/Artha/scripts/safe_cli.sh` — the outbound wrapper that prevents PII from leaking to external CLIs (Gemini, Copilot). Scans queries before sending them to non-Claude models.
- **Dependencies**: T-1A.1.5 (reuses `pii_guard.sh` scan logic)
- **Implementation spec**:
  - ~30 lines bash
  - Usage: `safe_cli.sh <cli> "<query>"`
  - Pipes query through `pii_guard.sh scan`
  - If PII detected: block the call, log to `audit.md`, exit 1
  - If clean: execute the CLI call, log query length to `audit.md`
- **Acceptance Criteria**:
  - `safe_cli.sh gemini "What is the EB-2 India priority date?"` → succeeds
  - `safe_cli.sh gemini "My SSN is 123-45-6789"` → blocked with error
  - Blocked calls logged to `audit.md` with PII type and CLI name
  - Successful calls logged with CLI name and query length (no query content)
- **Effort**: 30 minutes
- **Ref**: TS §8.7

---

#### T-1A.1.7 — Test PII filter with synthetic data

- [x] **Priority**: P0
- **Status Note**: COMPLETE — 19-case test suite embedded in pii_guard.sh test mode. All pass.
- **Description**: Create a set of synthetic test emails containing each PII pattern and verify `pii_guard.sh` catches them all. Also verify allowlisted patterns (USCIS receipt numbers, Amazon orders) pass through.
- **Dependencies**: T-1A.1.5
- **Steps**:
  1. Create `~/OneDrive/Artha/tests/pii_test_data.txt` with synthetic PII patterns
  2. Run `pii_guard.sh scan < pii_test_data.txt` — verify exit 1
  3. Run `pii_guard.sh filter < pii_test_data.txt` — verify all replacements
  4. Create test data with allowlisted patterns only — verify exit 0
  5. Create mixed test data — verify PII caught, allowlisted passed
- **Acceptance Criteria**:
  - 100% detection rate for all 10 PII pattern types
  - 0% false positive rate on allowlisted patterns
  - Filter output contains only `[PII-FILTERED-*]` tokens, no raw PII
- **Effort**: 30 minutes
- **Ref**: TS §8.6

---

### Group 2: Infrastructure & Directory Setup

#### T-1A.2.1 — Create Artha directory structure

- [x] **Priority**: P0
- **Status Note**: COMPLETE — All 8 directories created under ~/OneDrive/Artha/.
- **Description**: Create the full `~/OneDrive/Artha/` directory tree that holds all Artha files. This is the foundation everything else builds on.
- **Dependencies**: None
- **Steps**:
  ```bash
  mkdir -p ~/OneDrive/Artha/{prompts,state,briefings,summaries,config,scripts,visuals,tests}
  ```
- **Acceptance Criteria**:
  - All 8 directories exist under `~/OneDrive/Artha/`
  - OneDrive recognizes and begins syncing the folder
- **Effort**: 5 minutes
- **Ref**: TS §11.1, PRD §9.3

---

#### T-1A.2.2 — Create CLAUDE.md thin loader

- [x] **Priority**: P0
- **Status Note**: COMPLETE — Created at ~/OneDrive/Artha/CLAUDE.md. Points to config/Artha.md.
- **Description**: Create the 3-line `CLAUDE.md` file that Claude Code auto-reads on session start. It delegates to `Artha.md` for all instructions, keeping `CLAUDE.md` minimal for clean project separation.
- **Dependencies**: T-1A.2.1
- **Content**:
  ```markdown
  # Artha Loader
  Read and follow ALL instructions in Artha.md in this directory.
  Do not proceed without reading Artha.md first.
  ```
- **Acceptance Criteria**:
  - Claude Code reads `CLAUDE.md` on `cd ~/OneDrive/Artha && claude`
  - Claude then reads `Artha.md` as instructed
- **Effort**: 5 minutes
- **Ref**: TS §2.1, §11.1

---

#### T-1A.2.3 — Create initial state file templates

- [x] **Priority**: P0
- **Status Note**: COMPLETE — All 20 state file templates created with YAML frontmatter (added memory.md, boundary.md, learning.md, social.md, digital.md this session).
- **Description**: Create empty state files for all domains with proper YAML frontmatter. Standard-sensitivity files are plaintext. High/critical files will be encrypted after bootstrapping (T-1A.7.x).
- **Dependencies**: T-1A.2.1
- **Files to create** (with sensitivity classification):
  - Standard (plaintext sync): `calendar.md`, `kids.md`, `shopping.md`, `goals.md`, `memory.md`, `health-check.md`, `boundary.md`, `learning.md`, `social.md`, `digital.md`
  - High (encrypted): `finance.md`, `health.md`, `insurance.md`, `vehicle.md`
  - Critical (encrypted): `immigration.md`, `estate.md`, `audit.md`
- **Each file includes**:
  ```yaml
  ---
  domain: [name]
  last_updated: 1970-01-01T00:00:00-08:00
  last_catch_up: 1970-01-01T00:00:00-08:00
  alert_level: none
  sensitivity: [standard|high|critical]
  access_scope: [full|catch-up-only|terminal-only]
  version: 1
  ---
  ## Current Status
  Not yet initialized. Run first catch-up to populate.
  ## Recent Activity
  (none)
  ```
- **Acceptance Criteria**:
  - All 17 domain state files created with valid YAML frontmatter
  - Sensitivity and access_scope correctly classified per TS §4.2 table
  - `health-check.md` includes empty autonomy tracking fields per TS §12.11
- **Effort**: 30 minutes
- **Ref**: TS §4, §11.1

---

#### T-1A.2.4 — Create `contacts.md` config file

- [x] **Priority**: P0
- **Status Note**: COMPLETE — Created at config/contacts.md with family/professional sections.
- **Description**: Create `~/OneDrive/Artha/config/contacts.md` with contact groups for messaging (Diwali greetings, birthday list, etc.) and individual contacts with phone numbers for WhatsApp. This file is encrypted (contains phone numbers = PII).
- **Dependencies**: T-1A.2.1
- **Content structure**:
  ```markdown
  ---
  sensitivity: high
  last_updated: 2026-03-XX
  ---
  ## Contact Groups
  ### Diwali Greetings
  - family_india: [emails]
  - friends_us: [emails]
  - colleagues: [emails]
  ### Birthday Reminders
  - [name]: [email] | [phone] | [birthday]
  ## Individual Contacts
  [To be populated]
  ```
- **Acceptance Criteria**:
  - File created with proper structure
  - Added to `SENSITIVE_FILES` array in `vault.sh` (contacts)
  - Encrypts correctly to `.age` on `vault.sh encrypt`
- **Effort**: 30 minutes
- **Note**: Populate with actual contacts in T-1B.7.1
- **Ref**: TS §7.4.3, §8.5

---

#### T-1A.2.5 — Create `settings.md` config file

- [x] **Priority**: P0
- **Status Note**: COMPLETE — Created at config/settings.md with all required sections. age_recipient needs updating after keypair generation.
- **Description**: Create `~/OneDrive/Artha/config/settings.md` with global configuration — briefing email targets, work hours, timezone, sync settings, and the `age` public key.
- **Dependencies**: T-1A.1.2 (age public key), T-1A.2.1
- **Content**: Per TS §11.1 — includes briefing email address, alert email, work hours (8 AM – 6 PM per OQ-4), timezone (America/Los_Angeles), OneDrive sync config, `age` recipient public key.
- **Acceptance Criteria**:
  - All required settings populated
  - `age_recipient` field contains the public key from T-1A.1.2
  - Work hours reflect 8 AM – 6 PM (not 7 PM) per OQ-4
  - Email accounts section lists primary Gmail
- **Effort**: 15 minutes
- **Ref**: TS §11.1, PRD OQ-4

---

#### T-1A.2.6 — Create `occasions.md` config file

- [x] **Priority**: P1
- **Status Note**: COMPLETE — Created at config/occasions.md.
- **Description**: Create `~/OneDrive/Artha/config/occasions.md` with festival/occasion calendar and visual style preferences for greeting card generation.
- **Dependencies**: T-1A.2.1
- **Content**: Per TS §7.4.6 — Diwali, Holi, Christmas, New Year, birthdays, anniversaries with associated date sources and visual styles.
- **Acceptance Criteria**:
  - All major occasions listed with date sources
  - Visual style preferences defined per occasion
- **Effort**: 15 minutes
- **Ref**: TS §7.4.6

---

#### T-1A.2.7 — Verify OneDrive sync

- [~] **Priority**: P0
- **Status Note**: Partial — OneDrive folder exists and syncs. Selective sync exclusion of plaintext sensitive .md files needs manual verification on iPhone/Windows.
- **Description**: Confirm that the `~/OneDrive/Artha/` folder syncs correctly to iPhone and Windows, and that selective sync exclusions work as configured.
- **Dependencies**: T-1A.2.1, T-1A.1.4 (selective sync)
- **Steps**:
  1. Create a test file in `~/OneDrive/Artha/state/`
  2. Verify it appears on iPhone OneDrive app within 5 minutes
  3. Verify it appears on Windows OneDrive within 5 minutes
  4. Verify that files in the selective sync exclusion list do NOT sync
  5. Clean up test file
- **Acceptance Criteria**:
  - Standard state files sync to all devices within 5 minutes
  - Excluded plaintext sensitive files do NOT sync
  - `.age` files DO sync
- **Effort**: 15 minutes
- **Ref**: PRD §9.3

---

### Group 3: MCP Integration Setup

#### T-1A.3.1 — Create Google Cloud project and enable APIs

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-08 — Google Cloud project created, Gmail API + Calendar API enabled, OAuth 2.0 Desktop client configured. Credentials stored in macOS Keychain via setup_google_oauth.py.
- **Description**: Set up a Google Cloud project for Artha's OAuth credentials. Enable both Gmail API and Google Calendar API.
- **Dependencies**: None (can be done in parallel with Group 1)
- **Steps**:
  1. Go to https://console.cloud.google.com/
  2. Create project: "Artha Personal"
  3. Enable Gmail API
  4. Enable Google Calendar API
  5. Create OAuth 2.0 credentials (Desktop application type)
  6. Download client credentials JSON
  7. Note: OAuth scopes needed: `gmail.readonly` + `gmail.send` + `calendar.events` (read + write)
- **Acceptance Criteria**:
  - Project created with both APIs enabled
  - OAuth 2.0 client ID and secret available
  - Client credentials JSON downloaded
- **Effort**: 30 minutes
- **Ref**: TS §11.2

---

#### T-1A.3.2 — Configure Gmail API integration (Python, not MCP)

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-08 — OAuth flow complete for configured-gmail@example.com (37,924 messages). Token stored in Keychain. Scripts: google_auth.py, gmail_fetch.py, gmail_send.py. Venv at ~/OneDrive/Artha/.venv with google-api-python-client.
- **Description**: Install and configure Gmail access using official Google API Python client. This is the single most critical integration — ~80% of Artha's data comes through Gmail. **Budget 3–5 hours** for OAuth validation and MCP troubleshooting (community-maintained MCP connectors may have version-specific issues).
- **Dependencies**: T-1A.3.1
- **Steps**:
  1. Identify the Gmail MCP server to use (resolve TD-1: `@anthropic/gmail-mcp` vs community)
  2. Run OAuth flow to get refresh token
  3. Store credentials in macOS Keychain:
     ```bash
     security add-generic-password -a "artha" -s "gmail-client-id" -w "$CLIENT_ID"
     security add-generic-password -a "artha" -s "gmail-client-secret" -w "$CLIENT_SECRET"
     security add-generic-password -a "artha" -s "gmail-refresh-token" -w "$REFRESH_TOKEN"
     ```
  4. Configure MCP in Claude Code config (`~/.claude/mcp.json` or project-level):
     - Test if MCP config supports shell expansion `$(security ...)` for env vars (TD-4)
     - If not, create a wrapper script that reads from Keychain and sets env vars
  5. Validate: start Claude Code session, confirm Gmail MCP tools are available
  6. Test operations: `gmail_search` (fetch recent emails), `gmail_read` (read email body), `gmail_send` (send test email to self)
- **Acceptance Criteria**:
  - Gmail MCP connects successfully in Claude Code session
  - `gmail.readonly` scope: search and read operations work
  - `gmail.send` scope: can send a test email
  - OAuth tokens stored in Keychain, never in plaintext
  - MCP reconnects across sessions without manual intervention
- **Effort**: 3–5 hours (includes troubleshooting time)
- **Resolves**: TD-1 (Gmail MCP selection), TD-4 (Keychain integration)
- **Fallback**: If Gmail MCP proves unreliable, prepare Python SMTP script per TS §9.2
- **Ref**: TS §3.1, §11.2

---

#### T-1A.3.3 — Configure Calendar API integration (Python, not MCP)

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-08 — OAuth flow complete. 3 calendars visible: configured-gmail@example.com, Family, Holidays in United States. Token stored in Keychain. Script: gcal_fetch.py.
- **Description**: Install and configure Google Calendar access using official Google API Python client.
- **Dependencies**: T-1A.3.1
- **Steps**:
  1. Install Calendar MCP server (same provider pattern as Gmail MCP)
  2. Configure OAuth with `calendar.events` scope (read + write) — resolve TD-15 (start with primary personal calendar only)
  3. Store credentials in Keychain
  4. Configure MCP in Claude Code config
  5. Validate: `list_events` (today + 7 days), create test event, delete test event
- **Acceptance Criteria**:
  - Calendar MCP connects successfully
  - Can list events for today + next 7 days
  - Can create a calendar event (write scope)
  - OAuth tokens in Keychain
- **Effort**: 30–60 minutes
- **Resolves**: TD-15 (calendar scope — start with single primary calendar)
- **Ref**: TS §3.2, §7.4.5

---

#### T-1A.3.4 — Configure Claude Code hooks for auto-encrypt/decrypt

- [x] **Priority**: P0
- **Status Note**: COMPLETE — .claude/settings.json created with PreToolUse hook (auto-decrypt on Read/Write/Edit/Bash) and PostToolUse hook (stray plaintext file detection).
- **Description**: Set up Claude Code hooks so that `vault.sh decrypt` runs automatically at session start (PreToolUse on state file access) and `vault.sh encrypt` runs on session stop (Stop hook). This eliminates manual encrypt/decrypt steps.
- **Dependencies**: T-1A.1.3 (vault.sh), T-1A.3.2 or T-1A.3.3 (MCP working)
- **Steps**:
  1. Configure PreToolUse hook: intercepts filesystem reads of `state/*` → runs `vault.sh decrypt` if not already decrypted
  2. Configure Stop hook: runs `vault.sh encrypt` on session end
  3. Test: start session → verify decrypt fires → end session → verify encrypt fires
  4. Verify lock file lifecycle: created on decrypt, removed on encrypt
  5. Fallback: if hooks are not supported in current Claude Code version, add explicit instructions in Artha.md
- **Acceptance Criteria**:
  - Session start auto-decrypts sensitive state files
  - Session end auto-encrypts and removes plaintext
  - Hook failures fall back to Artha.md instruction ("Run vault.sh decrypt first")
  - Feature flag in Artha.md: `hooks: enabled` (fallback: `disabled`)
- **Effort**: 30 minutes
- **Ref**: TS §3.6.2, §12.9

---

### Group 4: Instruction File Authoring

#### T-1A.4.1 — Author Artha.md — Identity & Behavior Section

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §1 in config/Artha.md (466 lines total).
- **Description**: Write the core identity section of Artha.md — who Artha is, the Mishra family, behavioral rules (silence by default, family-aware language, first names always). This is the "soul" of the system.
- **Dependencies**: None (conceptual — does not depend on infrastructure)
- **Content** (per TS §2.2):
  - Identity block: "You are Artha — Vedprakash Mishra's personal intelligence system"
  - Family: Vedprakash, Archana (wife), Parth (17, 11th grade, Tesla STEM), Trisha (12, 7th grade, Inglewood MS)
  - Behavior: "You do not greet. You do not pad output. You speak when you have something worth saying." (UX §2.4)
  - Tone: warm but concise, factual, data-driven
- **Acceptance Criteria**:
  - Identity section matches spec personality
  - All four family members named with correct details
  - Behavioral rules explicitly stated
- **Effort**: 30 minutes
- **Ref**: TS §2.2, UX §1.1 (UX-1 through UX-8), UX §2.4

---

#### T-1A.4.2 — Author Artha.md — Catch-Up Workflow Section

- [x] **Priority**: P0
- **Status Note**: COMPLETE (v3.7 updated 2026-03-08 Session 2) — §2 in config/Artha.md expanded to 20-step workflow: added Step 0 (pre-flight gate), Step 1b (ToDo pull), Step 7b (open_items update with OI-NNN deduplication), Step 11 (overdue items prepended to briefing), Step 15 (ToDo push), Step 16 (structured YAML to health-check.md). Fixed step refs: weekly trigger→Step 10; redaction→§4. Added family calendar guard. /items slash command added to §5.
- **Description**: Write the catch-up workflow instructions in Artha.md — the step-by-step sequence that Artha follows when the user says "catch me up". This is the primary operational procedure.
- **Dependencies**: T-1A.1.3 (vault.sh), T-1A.1.5 (pii_guard.sh), T-1A.3.2 (Gmail MCP), T-1A.3.3 (Calendar MCP)
- **Content** (per TS §7.1, PRD §9.4 — 18-step workflow):
  1. Decrypt sensitive state (`vault.sh decrypt`)
  2. Read health-check.md for last run timestamp
  3. Fetch emails + calendar IN PARALLEL
  4. Pre-flight PII filter (`pii_guard.sh filter`) — HALT if fails
  5. Email content pre-processing (strip HTML, collapse threads, truncate)
  6. Route each item to domain prompt
  7. Per domain: extract → redact (Layer 2) → deduplicate → update state → evaluate alerts
  8. Web research via Gemini CLI for external data needs
  9. Cross-domain reasoning
  10. Ensemble reasoning (if triggered for high-stakes)
  11. Synthesize briefing
  12. Surface alerts
  13. Propose actions
  14. Email briefing
  15. Update health-check.md
  16. Archive briefing
  17. Log PII filter stats
  18. Encrypt sensitive state (`vault.sh encrypt`)
- **Acceptance Criteria**:
  - All 18 steps documented with explicit instructions
  - PII filter halt instruction is unambiguous
  - Parallel fetch instruction for Gmail + Calendar
  - Error handling per TS §7.2 included
  - Idempotency: second run processes only NEW emails
- **Effort**: 1 hour
- **Ref**: TS §7.1, PRD §9.4

---

#### T-1A.4.3 — Author Artha.md — Domain Routing Table

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §3 in config/Artha.md with 14 routing rules.
- **Description**: Write the routing rules that map email senders, subjects, and content patterns to domain prompt files. This is how Artha knows which domain handles which email.
- **Dependencies**: None (conceptual)
- **Content** (per TS §2.2.3):
  - Pattern → domain mappings for all known senders
  - Immigration: `*@fragomen.com`, `*@uscis.gov`, `*@travel.state.gov`, subject "visa bulletin"
  - Finance: `*@chase.com`, `*@fidelity.com`, `*@wellsfargo.com`, `*@vanguard.com`, `*@pse.com`
  - Kids: `*@parentSquare.com`, `*@lwsd.org`, `*@instructure.com`
  - Communications: catch-all for unmatched emails
  - Plus all other domain sender patterns
- **Acceptance Criteria**:
  - Every known sender has a routing rule
  - Ambiguous senders have a priority order
  - Unmatched emails route to Communications (catch-all)
- **Effort**: 30 minutes
- **Ref**: TS §2.2.3

---

#### T-1A.4.4 — Author Artha.md — Privacy & Redaction Rules

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §4 in config/Artha.md.
- **Description**: Encode the Layer 2 redaction rules in Artha.md — what Claude must redact when writing to state files. This complements `pii_guard.sh` (Layer 1).
- **Dependencies**: None
- **Content** (per TS §8.2):
  - SSN: NEVER stored anywhere — `[REDACTED]`
  - Passport numbers: `[REDACTED-PASSPORT-{name}]`
  - A-numbers: `[REDACTED-ANUM-{name}]`
  - Bank accounts: last 4 digits only `****1234`
  - Credit cards: last 4 digits only `****5678`
  - Bank routing numbers: `[REDACTED]`
  - Trust IDs: `[REDACTED-TRUST]`
  - Domain-specific rules per TS §8.2.1–§8.2.5
  - Emailed briefing filter: `sensitivity: high/critical` → summary only
- **Acceptance Criteria**:
  - Every PII type has explicit handling instructions
  - Sensitivity filter for email briefing explicitly stated
  - Extract-and-discard policy for documents (Phase 2) stated
- **Effort**: 30 minutes
- **Ref**: TS §8.2, PRD §12

---

#### T-1A.4.5 — Author Artha.md — Slash Commands

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §5 in config/Artha.md (/catch-up, /status, /goals, /domain, /cost, /health).
- **Description**: Define the six slash commands in Artha.md with their expected behavior and output format.
- **Dependencies**: None
- **Commands**:
  - `/catch-up` — Full catch-up workflow (T-1A.4.2)
  - `/status` — Health check, last run, stale domains, MCP status, cost (UX §10.2)
  - `/goals` — Goal scorecard with progress bars (UX §10.4)
  - `/domain <name>` — Deep-dive into one domain (UX §10.3)
  - `/cost` — Monthly API cost vs budget (UX §10.1)
  - `/health` — System integrity: file checks, encryption, CLI health (UX §10.5)
- **Acceptance Criteria**:
  - Each command defined with trigger, behavior, and output format
  - Output formats match UX spec templates
- **Effort**: 30 minutes
- **Ref**: TS §3.6.1, UX §10

---

#### T-1A.4.6 — Author Artha.md — Multi-LLM Routing Rules

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §6 in config/Artha.md.
- **Description**: Add cost-aware routing rules to Artha.md that direct tasks to the appropriate LLM (Claude, Gemini CLI, Copilot CLI).
- **Dependencies**: T-1A.5.1, T-1A.5.2 (CLI verification)
- **Content** (per TS §3.7.2):
  - Web research (Visa Bulletin, property values, recalls, prices) → Gemini CLI via `safe_cli.sh`
  - URL summarization → Gemini CLI via `safe_cli.sh`
  - Script/config validation → Copilot CLI via `safe_cli.sh`
  - Visual generation → Gemini Imagen (no `safe_cli.sh` needed — descriptive text only)
  - All reasoning, state management, MCP tools → Claude
  - High-stakes decisions → Ensemble (all 3 LLMs, Claude synthesizes)
  - Ensemble trigger criteria: immigration timeline, finance >$5K, estate, conflicting signals
- **Acceptance Criteria**:
  - Routing rules explicitly state when to use each CLI
  - `safe_cli.sh` wrapper required for all external CLI calls with user data
  - Gemini Imagen exempt from PII wrapper (descriptive prompts only)
  - Fallback chain: if Gemini/Copilot unavailable → Claude handles
- **Effort**: 30 minutes
- **Ref**: TS §3.7.2, §3.7.5

---

#### T-1A.4.7 — Author Artha.md — Capabilities Feature Flags

- [x] **Priority**: P1
- **Status Note**: COMPLETE — §7 in config/Artha.md (11 flags).
- **Description**: Add the capabilities section to Artha.md that acts as feature flags for Claude Code features, enabling graceful degradation.
- **Dependencies**: None
- **Content** (per TS §12.9):
  ```markdown
  ## Capabilities (Feature Flags)
  parallel_tool_invocation: enabled
  hooks: enabled
  sub_agents: disabled          # Phase 2
  built_in_memory: enabled
  extended_thinking: enabled
  ```
- **Acceptance Criteria**:
  - Claude reads flags and adapts behavior accordingly
  - Disabled features have documented fallback behavior
- **Effort**: 15 minutes
- **Ref**: TS §12.9

---

#### T-1A.4.8 — Author Artha.md — Briefing Output Format

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §8 in config/Artha.md (standard, quiet, crisis, email templates).
- **Description**: Encode the briefing output format in Artha.md so Claude generates consistent, well-structured briefings matching the UX spec templates.
- **Dependencies**: None
- **Content**:
  - Full briefing template (UX §4.1): header → critical → urgent → today → by domain → goal pulse → one thing → footer
  - Quiet day template (UX §4.3): header → "no alerts, no action items" → today → goals → footer
  - Crisis day template (UX §4.4): header → count → numbered critical items → standard format
  - Design rules (UX §4.2): empty sections stated not hidden, domain items in prose, goal pulse fixed-width, ONE THING always specific, footer shows signal-to-noise ratio
  - Sensitivity filter for email version
  - Email subject line convention (UX §12.2)
- **Acceptance Criteria**:
  - All three briefing variants documented
  - Design rules explicitly stated
  - Email subject line format defined
- **Effort**: 30 minutes
- **Ref**: UX §4, §5, §12

---

#### T-1A.4.9 — Author Artha.md — Action Proposal Format

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §9 in config/Artha.md.
- **Description**: Define the action proposal schema in Artha.md per the Action Execution Framework.
- **Dependencies**: None
- **Content** (per TS §7.4.1):
  - Proposal display format with Type, Recipient, Channel, Content Preview, Trust Required, Current Trust, Sensitivity
  - Approval options: `[approve]` `[edit]` `[skip]` `[skip all]`
  - Batch approval for low-risk items (UX §9.2)
  - Proposal sequencing: critical first → communications → calendar → informational (UX §9.3)
  - Autonomy floor rules: NEVER auto-execute financial, communication, immigration, or cross-person actions
  - Trust level processing rules (Level 0: recommend only, Level 1: propose with approval, Level 2: pre-approved types auto-execute)
  - Audit logging for all actions (approved, modified, rejected)
- **Acceptance Criteria**:
  - Proposal format matches TS §7.4.1 schema
  - Autonomy floor rules explicitly stated
  - Trust level behavior explicitly defined
- **Effort**: 30 minutes
- **Ref**: TS §7.4, UX §9, PRD §10

---

#### T-1A.4.10 — Author Artha.md — Versioning & Changelog

- [x] **Priority**: P0
- **Status Note**: COMPLETE — §10/§11 in config/Artha.md.
- **Description**: Add versioning header and changelog to Artha.md per governance requirements.
- **Dependencies**: None
- **Content**:
  ```yaml
  ---
  artha_md_version: 1.0
  last_modified: 2026-03-XX
  changelog:
    - v1.0: Initial authoring — identity, workflow, routing, privacy, slash commands, multi-LLM routing
  ---
  ```
- **Acceptance Criteria**:
  - Version field present in frontmatter
  - Changelog tracks initial creation
- **Effort**: 5 minutes
- **Ref**: TS §12.2

---

### Group 5: Multi-LLM Setup

#### T-1A.5.1 — Verify Gemini CLI operational

- [x] **Priority**: P0
- **Status Note**: COMPLETE — v0.32.1 at /opt/homebrew/bin/gemini. Use `-p` flag: `gemini -p "<query>"`
- **Description**: Confirm that the Gemini CLI is installed, authenticated, and can perform web search queries and image generation.
- **Dependencies**: None
- **Steps**:
  1. Verify installation: `gemini --version` (or equivalent)
  2. Test web search: `gemini "What is the current EB-2 India priority date?"`
  3. Test Imagen: `gemini "Generate a test image of a sunset"`
  4. Verify output saved to expected location
  5. If API key needed: store in Keychain, export via env var
- **Acceptance Criteria**:
  - Gemini CLI responds to web search queries
  - Gemini Imagen generates an image successfully
  - Free tier quota is sufficient for expected usage (~30 calls/month)
- **Effort**: 15 minutes
- **Ref**: TS §3.7.1

---

#### T-1A.5.2 — Verify Copilot CLI operational

- [x] **Priority**: P1
- **Status Note**: COMPLETE — v1.0.2. Use: `gh copilot suggest "<query>"`
- **Description**: Confirm that the GitHub Copilot CLI is installed, authenticated, and can answer validation queries.
- **Dependencies**: None
- **Steps**:
  1. Verify: `gh copilot --version`
  2. Test: `gh copilot suggest "Review this bash script for errors: vault.sh"`
  3. Verify authentication via `gh auth status`
- **Acceptance Criteria**:
  - Copilot CLI responds to queries
  - Authentication is via GitHub CLI token (managed by `gh auth`)
- **Effort**: 15 minutes
- **Ref**: TS §3.7.1

---

### Group 6: Domain Prompts (P0 Domains)

#### T-1A.6.1 — Author Immigration domain prompt (FR-2, P0)

- [x] **Priority**: P0
- **Status Note**: COMPLETE — prompts/immigration.md created with full extraction rules, alerts, dedup, PII allowlist.
- **Description**: Create `~/OneDrive/Artha/prompts/immigration.md` — the most critical domain prompt. Handles the Mishra family's immigration cases, deadlines, Visa Bulletin monitoring, and CSPA age-out tracking.
- **Dependencies**: T-1A.2.1
- **Content** (per TS §6.2 example — full production version):
  - Purpose: Track immigration for all 4 family members
  - Extraction rules: case_type, receipt_number, deadline, action_required, attorney_items, priority_date, status_change
  - Alert thresholds: 🔴 <30d deadline/status change/RFE, 🟠 <90d/VB within 6mo, 🟡 <180d/attorney mail, 🔵 VB published
  - CSPA age-out monitoring for Parth and Trisha (highest-stakes derived deadline)
  - State file update rules with `last_email_processed_id`
  - Extraction verification rules (date verification, receipt number format check)
  - Briefing contribution format
  - Sensitivity: critical, access_scope: catch-up-only
  - Known senders: `*@fragomen.com`, `*@uscis.gov`, `*@travel.state.gov`
  - Deduplication rules: match on receipt number, case type, update in-place
  - Visa Bulletin parsing: EB-2 India extraction, trailing average, movement detection
  - PII allowlist: USCIS receipt numbers (IOE-*, SRC*, LIN*), case numbers
- **Acceptance Criteria**:
  - All sections from TS §6.1 template are present
  - CSPA calculation rules explicitly documented
  - Extraction verification rules included (append `[VERIFY]` when confidence low)
  - PII allowlist section present with USCIS patterns
  - Matches the example in TS §6.2 with production refinements
- **Effort**: 1 hour
- **Ref**: TS §6.2, PRD FR-2

---

#### T-1A.6.2 — Author Finance domain prompt (FR-3, P0)

- [x] **Priority**: P0
- **Status Note**: COMPLETE — prompts/finance.md created.
- **Description**: Create `~/OneDrive/Artha/prompts/finance.md` — tracks bills, spending, account balances, and financial health.
- **Dependencies**: T-1A.2.1
- **Content** (per TS §6.3 example):
  - Purpose: Track bills, spending, account balances for Mishra household
  - Extraction rules: bill_type, amount, due_date, account, auto_pay, statement_period, balance, unusual_flag
  - Alert thresholds: 🔴 overdue/fraud/credit drop, 🟠 <3d non-auto-pay/unusual >$500, 🟡 <7d/above budget, 🔵 statement/balance
  - Extraction verification (dollar amount regex, date inference noting)
  - Cross-domain triggers: travel booking → credit card benefits (F3.12), large expense → budget check
  - Sensitivity: high, access_scope: catch-up-only
  - Known senders: Chase, Fidelity, Wells Fargo, Vanguard, PSE, Sammamish, Equifax
  - Deduplication: match on provider + billing period, payment confirmation updates existing entry
  - PII allowlist: order confirmation numbers, statement reference numbers
- **Acceptance Criteria**:
  - All sections present per TS §6.1 template
  - Cross-domain trigger for travel/credit card benefits documented
  - Unusual spend threshold configurable
- **Effort**: 1 hour
- **Ref**: TS §6.3, PRD FR-3

---

#### T-1A.6.3 — Author Kids domain prompt (FR-4, P0)

- [x] **Priority**: P0
- **Status Note**: COMPLETE — prompts/kids.md created with Parth + Trisha sections.
- **Description**: Create `~/OneDrive/Artha/prompts/kids.md` — tracks both children's academics, activities, and school communications.
- **Dependencies**: T-1A.2.1
- **Content**:
  - Purpose: Track school, grades, activities for Parth (11th, Tesla STEM) and Trisha (7th, Inglewood MS)
  - Extraction rules: student_name, grade_type, score, course, assignment, attendance, activity, event_date
  - Alert thresholds: 🔴 absence (unplanned), 🟠 low grade/missing assignment, 🟡 event upcoming, 🔵 newsletter
  - Per-child sections in state file (PRD FR-4: Parth profile + Trisha profile)
  - School noise filter: Spirit Week, fundraisers, lunch menus → summarize, don't alert
  - SAT tracking for Parth (college prep — per OQ-5)
  - Sensitivity: standard
  - Known senders: `*@parentSquare.com`, `*@lwsd.org`, `*@instructure.com`
  - Deduplication: match on student + event/assignment + date
- **Acceptance Criteria**:
  - Both children's profiles defined with grade levels and schools
  - School noise reduction rules explicit (target: 90-100 emails/month → ≤10 digests)
  - Parth SAT/college prep tracking included
- **Effort**: 45 minutes
- **Ref**: PRD FR-4, §7 feature table

---

#### T-1A.6.4 — Author Communications domain prompt (FR-1, P0)

- [x] **Priority**: P0
- **Status Note**: COMPLETE — prompts/comms.md created.
- **Description**: Create `~/OneDrive/Artha/prompts/comms.md` — the catch-all domain for emails that don't route to a specific domain. Handles school digest consolidation and action item extraction.
- **Dependencies**: T-1A.2.1
- **Content**:
  - Purpose: Catch-all for general communications, school digest consolidation (F1.1), action item extraction (F1.2)
  - Extraction rules: sender, subject, action_required (boolean), deadline, priority_assessment
  - Alert thresholds: only if action required with deadline
  - Sensitivity: standard
  - Known senders: catch-all (everything not matching other domains)
- **Acceptance Criteria**:
  - Catch-all routing works for unmatched emails
  - School emails consolidated into digest format
  - Action items extracted with deadlines
- **Effort**: 30 minutes
- **Ref**: PRD FR-1

---

#### T-1A.6.5 — Define PII allowlists in domain prompts

- [x] **Priority**: P0
- **Status Note**: COMPLETE — PII Allowlist sections included in immigration.md and finance.md.
- **Description**: Add `## PII Allowlist` sections to each domain prompt, listing patterns that look like PII but are domain-legitimate (USCIS receipt numbers, Amazon order numbers, etc.).
- **Dependencies**: T-1A.6.1–T-1A.6.4
- **Steps**:
  1. Immigration: USCIS receipt numbers, case numbers
  2. Finance: Order confirmation numbers, statement reference numbers, account last-4
  3. Kids: Student ID numbers (if applicable)
  4. Communications: No specific allowlist needed
- **Acceptance Criteria**:
  - Each domain prompt with known PII-like legitimate patterns has an allowlist
  - `pii_guard.sh` reads and honors these allowlists
- **Effort**: 15 minutes
- **Ref**: TS §8.6

---

### Group 7: State File Bootstrap & Email Delivery

#### T-1A.7.1 — Bootstrap immigration state file with known data

- [~] **Priority**: P0
- **Status Note**: PARTIAL 2026-03-08 — Framework populated: H-1B (Ved) + H-4 (Archana, Parth, Trisha), no GC filed. School data added. All specific dates/receipt numbers marked TODO for first catch-up to extract from Fragomen emails. File encrypted.
- **Description**: Manually populate `~/OneDrive/Artha/state/immigration.md` with current known immigration data for all four family members. This is the highest-stakes domain — accuracy here is critical.
- **Dependencies**: T-1A.2.3
- **Data to capture**:
  - Vedprakash: H-1B status, expiry, I-140 approval, priority date, receipt numbers
  - Archana: H-4 status, EAD status and expiry
  - Parth & Trisha: H-4 status, CSPA age-out calculations
  - Active deadlines: EAD renewal, H-1B extension
  - Attorney contact info (Fragomen)
  - Visa Bulletin history (most recent months)
- **Acceptance Criteria**:
  - All four family members have correct current status
  - All active deadlines are listed with correct dates
  - Priority date is recorded (resolves OQ-6)
  - CSPA calculations are started for both children
  - File encrypts correctly to `.age`
- **Effort**: 1–2 hours (requires gathering data from Fragomen emails/docs)
- **Security**: Passport numbers stored as `[REDACTED-PASSPORT-{name}]`, A-numbers as `[REDACTED-ANUM-{name}]`, SSN never stored
- **Ref**: PRD FR-2, OQ-6

---

#### T-1A.7.2 — Bootstrap finance state file with known data

- [~] **Priority**: P0
- **Status Note**: PARTIAL 2026-03-08 — Account framework populated: Chase (checking/savings/CC), Wells Fargo (mortgage/savings), Fidelity, Vanguard, Morgan Stanley, E*Trade, Discover, HDFC NRI. Bill schedule structure in place. Specific balances/amounts/last-4s marked TODO for first catch-up to extract from bank emails. File encrypted.
- **Description**: Manually populate `~/OneDrive/Artha/state/finance.md` with current account inventory, bill schedule, and budget targets.
- **Dependencies**: T-1A.2.3
- **Data to capture** (per PRD FR-3 account inventory):
  - Chase (checking, credit cards)
  - Fidelity (investment)
  - Vanguard (investment)
  - Morgan Stanley (investment)
  - E*Trade (investment)
  - Wells Fargo (mortgage + savings)
  - Discover (credit card)
  - HDFC NRI (India accounts)
  - Recurring bills: mortgage, utilities (PSE, water), insurance, subscriptions
  - Auto-pay status for each bill
  - Monthly budget targets
- **Acceptance Criteria**:
  - All known accounts listed with last-4 masking for account numbers
  - Bill calendar populated with due dates and auto-pay status
  - Monthly budget categories defined
  - Resolve OQ-7: confirm whether Archana holds independent accounts
  - File encrypts correctly to `.age`
- **Effort**: 1–2 hours
- **Ref**: PRD FR-3, OQ-7

---

#### T-1A.7.3 — Bootstrap kids state file with known data

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-08 — Fully populated by first catch-up. Parth: Tesla STEM HS grade 11, SAT 3/13 at Eastlake HS, AP Lang, TSA, absence 3/6. Trisha: Inglewood MS grade 7, IMS Concert 3/10, missing assignment, soccer/piano/UIL. Teacher contacts added.
- **Description**: Populate `~/OneDrive/Artha/state/kids.md` with current academic data for both children.
- **Dependencies**: T-1A.2.3
- **Data to capture**:
  - Parth: current grades by course, GPA, SAT prep status, activities (Econ Club, etc.), college prep timeline
  - Trisha: current grades by course, activities (soccer, etc.)
- **Acceptance Criteria**:
  - Both children's sections populated with current data
  - Parth's SAT date and college prep tracked as milestone goal
- **Effort**: 30 minutes
- **Ref**: PRD FR-4, OQ-5

---

#### T-1A.7.4 — Configure email delivery for briefings

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-08 — gmail_send.py tested successfully. Test email sent and delivered to configured-gmail@example.com (message_id: 19cccb36717007b3). Markdown→HTML conversion active, dual MIME working.
- **Description**: Set up the mechanism for emailing catch-up briefings to the configured address. Resolve TD-2 (email sending mechanism).
- **Dependencies**: T-1A.3.2 (Gmail MCP)
- **Options** (per TS §3.4):
  1. Gmail MCP `gmail.send` (preferred — already configured)
  2. Python SMTP script (`send_briefing.py`) — fallback
  3. Apple Mail via `osascript` — last resort
- **Steps**:
  1. Test Gmail MCP send with a formatted briefing
  2. Verify HTML rendering in email client (iPhone Mail, Outlook)
  3. Verify subject line convention works (UX §12.2)
  4. Test sensitivity filter: high/critical domains show summary only in email
- **Acceptance Criteria**:
  - Briefing email sends successfully via chosen mechanism
  - Email renders correctly on iPhone and Windows (both plaintext and HTML)
  - Subject line follows `Artha · [date] — [severity] · [count] items` convention
  - Sensitivity filter: immigration/finance show only "X items processed, no new alerts" in email
- **Effort**: 1 hour
- **Resolves**: TD-2
- **Ref**: TS §3.4, UX §12

---

#### T-1A.7.5 — Resolve Archana's email access (TD-18)

- [ ] **Priority**: P0
- **Description**: Determine how to handle Archana's personal email for domains relevant to Artha (immigration, health, school). Options: forward to Ved's Gmail, add separate MCP scope, or shared inbox.
- **Dependencies**: T-1A.3.2
- **Steps**:
  1. Identify which of Archana's accounts receive immigration (Fragomen, USCIS), health (provider), or school (ParentSquare) emails
  2. If she receives relevant emails: set up auto-forward to Ved's Gmail (simplest)
  3. Update domain routing rules in Artha.md for forwarded sender patterns
  4. Test: verify forwarded emails are correctly routed
- **Acceptance Criteria**:
  - Decision on Archana's email integration documented
  - If forwarding: auto-forward configured and tested
  - Routing rules updated for new sender patterns
- **Effort**: 30 minutes
- **Resolves**: TD-18
- **Ref**: PRD OQ-2, TS TD-18

---

### Group 8: Action Framework & Visual Generation Setup

#### T-1A.8.1 — Test WhatsApp URL scheme on Mac

- [ ] **Priority**: P1
- **Description**: Verify that the `open "https://wa.me/..."` URL scheme opens WhatsApp on Mac with a pre-filled message.
- **Dependencies**: None
- **Steps**:
  1. Test: `open "https://wa.me/1XXXXXXXXXX?text=Hello%20test%20message"`
  2. Verify WhatsApp opens with pre-filled text
  3. Verify user must manually tap Send (human gate)
  4. Test with no recipient: `open "https://wa.me/?text=Hello%20test"`
- **Acceptance Criteria**:
  - WhatsApp opens with pre-filled message
  - User must tap Send (cannot auto-send)
  - URL encoding handles special characters and emoji
- **Effort**: 15 minutes
- **Ref**: TS §7.4.4

---

#### T-1A.8.2 — Test Gemini Imagen visual generation

- [ ] **Priority**: P1
- **Description**: Generate a test image via Gemini Imagen and verify the output pipeline.
- **Dependencies**: T-1A.5.1
- **Steps**:
  1. Generate: `gemini "Generate a Diwali greeting card with diyas and rangoli"`
  2. Verify output saved to `~/OneDrive/Artha/visuals/`
  3. Verify output is valid image (open in Preview)
  4. Test with different styles per TS §7.4.6 occasions table
- **Acceptance Criteria**:
  - Image generated successfully
  - Saved to `visuals/` directory
  - Image syncs to iPhone via OneDrive
- **Effort**: 15 minutes
- **Resolves**: TD-14 (PNG vs JPEG — use whichever Imagen outputs)
- **Ref**: TS §3.7.5, §7.4.6

---

### Group 9: Governance Baseline

#### T-1A.9.1 — Create `registry.md` component manifest

- [x] **Priority**: P0
- **Status Note**: COMPLETE — config/registry.md created with all components, status, and setup checklist.
- **Description**: Create `~/OneDrive/Artha/config/registry.md` — the single source of truth for all Artha components: prompts, state files, MCP servers, hooks, scripts, slash commands, CLIs, action channels, and config files.
- **Dependencies**: Depends on most previous tasks (document what's been built)
- **Content**: Per TS §12.1 — full component registry with tables for each component type
- **Acceptance Criteria**:
  - All deployed components listed with correct status, version, and metadata
  - `next_review` date set to 3 months from creation
  - Registry is accurate and complete at Phase 1A completion
- **Effort**: 30 minutes
- **Ref**: TS §12.1

---

#### T-1A.9.2 — Initialize audit.md with governance baseline

- [x] **Priority**: P0
- **Status Note**: COMPLETE — Governance baseline written 2026-03-08. Documents: system initialization, security foundation, pii_guard verification, LaunchAgent, Claude Code hooks, Google API security decision, 17 domain prompts, 20 state files, governance rules. File encrypted as audit.md.age.
- **Description**: Create initial entries in `audit.md` documenting the initial system setup: encryption decisions, PII patterns, routing rules, and component deployment.
- **Dependencies**: T-1A.2.3
- **Content**:
  - System creation timestamp
  - Encryption tier decisions (which files are encrypted and why)
  - PII filter pattern list (initial set)
  - Initial component list deployed
- **Acceptance Criteria**:
  - `audit.md` has a clean governance baseline entry
  - File encrypts correctly (it's in the sensitive files list)
- **Effort**: 15 minutes
- **Ref**: TS §12.2

---

### Group 10: Integration Testing & First Catch-Up

#### T-1A.10.1 — First end-to-end catch-up run

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-08 — First catch-up ran successfully. 50 emails scanned (14-day window), 8 actionable items identified, signal:noise 8:42. Briefing displayed in terminal and emailed to configured-gmail@example.com.
  - Kids domain: fully populated (Parth SAT 3/13, TSA forms, AP Lang alert; Trisha IMS Concert 3/10, missing assignment)
  - Security: Google new sign-in alert from Windows device on 3/6 — needs manual verification
  - Comms: Aavya birthday party RSVP needed; Samskrit Family Camp
  - Finance: Discover FICO update (Archana)
  - **Known gaps found**:
    - Calendar fetched primary only (0 events) — Family calendar not checked; needs Artha.md fix to include all calendars
    - Goals.md not bootstrapped — T-1A.10.3 iteration needed
    - 6 domains still empty: health, home, travel, estate, insurance + detailed finance/immigration
- **Dependencies**: ALL previous Phase 1A tasks
- **Steps**:
  1. `cd ~/OneDrive/Artha && claude`
  2. Verify Claude reads CLAUDE.md → Artha.md
  3. Say "catch me up"
  4. Observe: decrypt fires, emails fetched, processed, briefing generated
  5. Verify: briefing displayed in terminal
  6. Verify: briefing emailed to configured address
  7. Verify: state files updated with extracted data
  8. Verify: health-check.md updated with run timestamp
  9. Verify: audit.md has action log entries
  10. Verify: PII filter stats logged
  11. Verify: encrypt fires on session end
  12. Note issues for iteration in T-1A.10.3
- **Acceptance Criteria**:
  - Catch-up completes without errors
  - Briefing format matches UX spec template
  - At least one email correctly routed to a domain
  - State file updated with extracted data
  - Email briefing received with correct subject line and sensitivity filter
  - Encrypted state files exist after session ends
  - Total catch-up time < 5 minutes (first run may be slower)
- **Effort**: 1–2 hours
- **Ref**: TS §14.1

---

#### T-1A.10.2 — Second catch-up run (idempotency validation)

- [ ] **Priority**: P0
- **Description**: Run a second catch-up immediately after the first to verify idempotency — only NEW emails should be processed, no duplicates.
- **Dependencies**: T-1A.10.1
- **Steps**:
  1. Run "catch me up" again
  2. Verify: ONLY new emails since last run are processed
  3. Verify: state files are not duplicated (dedup rules work)
  4. Verify: `last_email_processed_id` tracking works
  5. If duplicates appear: diagnose whether issue is in Gmail query, state tracking, or dedup logic
- **Acceptance Criteria**:
  - Zero duplicate entries in state files
  - Only new emails processed (timestamp-based or historyId-based)
  - Briefing shows only new items
- **Effort**: 30 minutes
- **Resolves**: TD-5 (idempotency strategy confirmed)
- **Ref**: TS TD-5, §7.1

---

#### T-1A.10.3 — Iterate on Artha.md and domain prompts

- [x] **Priority**: P0
- **Status Note**: SUBSTANTIALLY COMPLETE 2026-03-08 Session 2 — Major v3.7 iteration: 20-step workflow (from 18), pre-flight gate, open_items integration, ToDo sync hooks, /items command, family calendar guard, HTML pre-processing explicit in Step 5, structured YAML health-check output (Step 16). settings.md: calendar IDs, email coverage matrix, MS Graph section, enhanced capability flags, setup checklist. health-check.md: restructured to schema_version 2.0 with machine-parseable YAML blocks. goals.md: added schema_version 2.0 frontmatter + goals_index YAML block. open_items.md: bootstrapped with OI-001/002/003 from first catch-up. gcal_fetch.py: --calendars default updated to include all 3 calendar IDs. Remaining: user accuracy review after 3+ catch-ups (per acceptance criteria ≥90%).
- **Description**: Based on the first 2–3 catch-up runs, refine Artha.md instructions and domain prompts to improve accuracy, fix routing errors, and tune alert thresholds.
- **Dependencies**: T-1A.10.1, T-1A.10.2
- **Steps**:
  1. Review each briefing for accuracy — note misrouted emails, missed extractions, incorrect alert levels
  2. Log corrections in `memory.md`
  3. Update domain prompt extraction rules or routing table as needed
  4. Re-run catch-up and verify improvements
  5. Repeat 3–5 times until accuracy stabilizes at ≥90%
- **Acceptance Criteria**:
  - Briefing accuracy rated ≥90% by user
  - Domain routing correctly handles all observed email patterns
  - Alert thresholds tuned to avoid false positives
  - CLAUDE.md changelog updated with iterations
- **Effort**: 2–3 hours (spread over 3–5 sessions)
- **Ref**: TS §12.8

---

#### T-1A.10.4 — Test PII filter in live catch-up

- [ ] **Priority**: P0
- **Description**: Verify that `pii_guard.sh` correctly filters PII during actual email processing, not just synthetic test data.
- **Dependencies**: T-1A.10.1
- **Steps**:
  1. During a catch-up, monitor PII filter stats in audit.md
  2. Inspect state files for any raw PII that leaked through
  3. Verify allowlisted patterns (USCIS receipt numbers) are NOT filtered
  4. If PII leaks through: update patterns in `pii_guard.sh`
- **Acceptance Criteria**:
  - No raw SSN, CC, routing numbers, passport numbers in state files
  - USCIS receipt numbers pass through correctly
  - PII filter audit entries show in `audit.md`
- **Effort**: 30 minutes
- **Ref**: TS §8.6

---

#### T-1A.10.5 — Test multi-LLM routing in live catch-up

- [ ] **Priority**: P1
- **Description**: Verify that web research queries go to Gemini CLI and validation queries go to Copilot CLI during a catch-up, rather than consuming Claude API tokens.
- **Dependencies**: T-1A.10.1, T-1A.5.1, T-1A.5.2
- **Steps**:
  1. During catch-up, trigger a Visa Bulletin lookup → verify Gemini CLI handles it
  2. Check `safe_cli.sh` logs in `audit.md` → verify outbound PII wrapper engaged
  3. Verify cost: no Claude API tokens consumed for research queries
- **Acceptance Criteria**:
  - Research queries routed to Gemini CLI
  - `safe_cli.sh` wrapper intercepted all external CLI calls
  - No PII in outbound queries
- **Effort**: 30 minutes
- **Ref**: TS §3.7

---

#### T-1A.10.6 — Test TD-19: PreToolUse hook PII interception

- [ ] **Priority**: P2
- **Description**: Test whether Claude Code `PreToolUse` hooks can intercept Gmail MCP responses and pipe through `pii_guard.sh` before Claude processes the content. If this works, it upgrades the PII filter from pre-persist to true pre-flight.
- **Dependencies**: T-1A.10.1
- **Steps**:
  1. Configure a PreToolUse hook on Gmail MCP responses
  2. Test: does the hook receive the MCP response data?
  3. Test: can the hook modify the response (pipe through pii_guard.sh)?
  4. If yes: upgrade pii_guard.sh integration to true pre-flight
  5. If no: document finding, keep current pre-persist approach
- **Acceptance Criteria**:
  - Finding documented: can/cannot intercept MCP responses
  - If can: PII filter upgraded to true pre-flight
  - If cannot: current pre-persist approach confirmed as acceptable
- **Effort**: 1 hour
- **Resolves**: TD-19
- **Ref**: TS TD-19, §8.6

---

#### T-1A.10.7 — Run Phase 1A validation checklist

- [ ] **Priority**: P0
- **Description**: Execute the complete Phase 1A validation checklist from TS §14.1 — 35+ items covering all components.
- **Dependencies**: ALL Phase 1A tasks
- **Checklist** (from TS §14.1):
  - [ ] `claude` opens in `~/OneDrive/Artha/` and reads CLAUDE.md
  - [ ] "catch me up" triggers full workflow
  - [ ] `vault.sh decrypt` works
  - [ ] Gmail MCP connects and fetches
  - [ ] Calendar MCP connects and fetches
  - [ ] At least one email correctly routed
  - [ ] At least one state file correctly updated
  - [ ] `vault.sh encrypt` works
  - [ ] `pii_guard.sh scan` detects synthetic SSN
  - [ ] `pii_guard.sh scan` detects synthetic CC
  - [ ] `pii_guard.sh filter` replaces PII with tokens
  - [ ] PII allowlist exempts USCIS receipt numbers
  - [ ] Catch-up halts on pii_guard.sh failure
  - [ ] PII filter audit entries in audit.md
  - [ ] OneDrive syncs within 5 minutes
  - [ ] Standard state files readable on iPhone
  - [ ] `.age` files visible but unreadable on iPhone
  - [ ] Briefing synthesized and displayed in terminal
  - [ ] Briefing emailed to configured address
  - [ ] health-check.md updated with run timestamp
  - [ ] audit.md logs all actions
  - [ ] Second catch-up processes only NEW emails (no duplicates)
  - [ ] On-demand query answers from state files
  - [ ] Total catch-up time < 3 minutes
  - [ ] No custom code beyond vault.sh + pii_guard.sh + safe_cli.sh
  - [ ] `registry.md` accurately reflects all components
  - [ ] Artha.md has version field and changelog
  - [ ] Gemini CLI responds to web search
  - [ ] Copilot CLI responds to validation
  - [ ] Multi-LLM routing in Artha.md works
  - [ ] CLI health status tracked
  - [ ] WhatsApp URL scheme opens WhatsApp with pre-filled message
  - [ ] Gmail MCP can send composed emails
  - [ ] Action proposals display correctly
  - [ ] Action approval/rejection logged to audit.md
  - [ ] contacts.md and occasions.md created
  - [ ] Gemini Imagen generates test visual
  - [ ] Visuals saved and synced
- **Acceptance Criteria**:
  - ALL items checked off
  - Any failures documented with mitigation plan
- **Effort**: 1 hour
- **Ref**: TS §14.1

---

### Group 11: Operational Robustness (Post-Catch-Up Enhancements)

> These tasks were identified from operational experience after the first two live catch-ups. They close reliability gaps not visible during design, and lay the foundation for idempotent, self-healing operation.

#### T-1A.11.1 — Implement persistent open items tracking (open_items.md)

- [x] **Priority**: P0
- **Status**: ✅ Complete — `state/open_items.md` created with YAML frontmatter, Open Items and Resolved sections, full field schema in comments. File lives at `~/OneDrive/Artha/state/open_items.md`.
- **Description**: Create `~/OneDrive/Artha/state/open_items.md` — a running list of action items extracted from catch-ups that persists across sessions. Items are added by Artha during catch-up, managed by the user in Microsoft To Do (T-1B.6.x), and re-surfaced in subsequent briefings until closed. This is the missing bridge between one-time briefings and durable task awareness.
- **Dependencies**: T-1A.10.1 (first catch-up complete)
- **Schema** (per item):
  ```yaml
  - id: OI-001
    date_added: 2026-03-08
    source_domain: kids
    description: "Parth SAT 3/13 — arrange transport to Eastlake HS by 8:30am"
    deadline: 2026-03-13
    priority: P0
    status: open  # open | in-progress | done | dismissed
    todo_id: ""   # Microsoft To Do item ID (populated in T-1B.6.3)
  ```
- **Artha.md additions**:
  - On catch-up: for each actionable item, check `open_items.md` by description hash — add only if not already present
  - On briefing: render open/overdue items above domain sections
  - On resolution: user can say "mark OI-001 done" or Artha detects resolution from email evidence
  - Deduplication: fuzzy match on description + deadline prevents re-adding the same item across catch-ups
- **Acceptance Criteria**:
  - `open_items.md` created with schema above
  - Second catch-up does NOT re-add items already in the list
  - Overdue open items (deadline < today, status: open) appear in a `🔴 OVERDUE` header in the briefing
  - Items with `status: done` are archived to a `## Resolved` section, not deleted
  - File is standard sensitivity (not encrypted) — task descriptions only, no raw PII
- **Effort**: 1 hour (Artha.md update + state file creation)
- **Ref**: Enhancement from catch-up #1 and #2 operational experience

---

#### T-1A.11.2 — Implement briefing archive pipeline

- [x] **Priority**: P0
- **Status**: ✅ Complete — `archive_briefing()` function added to `gmail_send.py`; `--archive` flag saves briefing to `briefings/YYYY-MM-DD.md` with YAML frontmatter; appends with separator on same-day re-run.
- **Description**: At the end of each catch-up, save the full briefing to `~/OneDrive/Artha/briefings/YYYY-MM-DD.md`. This enables historical reference, accuracy measurement over time, and weekly summary generation (T-1C.3.1). Without an archive, there is no way to answer "what did last Tuesday's briefing say about Parth's SAT?" or measure signal quality across sessions.
- **Dependencies**: T-1A.10.1
- **Steps**:
  1. Add step 16b to the catch-up workflow in Artha.md: after emailing briefing, write full text to `briefings/YYYY-MM-DD.md`
  2. Create `briefings/` directory in the Artha project structure
  3. Filename convention: ISO date, e.g., `briefings/2026-03-08.md`
  4. If two briefings in one day: `briefings/2026-03-08-2.md`
  5. File uses the sensitivity-filtered email version (same content as what was sent)
- **Acceptance Criteria**:
  - After each catch-up, a dated briefing file exists in `briefings/`
  - Briefings sync to OneDrive and are readable on iPhone
  - Weekly summary (T-1C.3.1) reads from `briefings/` to detect cross-week patterns
  - `registry.md` updated to include `briefings/` directory
- **Effort**: 30 minutes (Artha.md addition + directory creation)
- **Ref**: Pre-requisite for T-1C.3.1 (weekly summary)

---

#### T-1A.11.3 — Add pre-catch-up go/no-go health gate

- [x] **Priority**: P0
- **Status**: ✅ Complete — `scripts/preflight.py` (330+ lines): 9 P0 hard-block checks + 4 P1 warnings. Flags: `--quiet`, `--fix`, `--json`. Exits 0/1. All 13 checks pass. Found and fixed a real bug (gmail_send.py --health broken) on first run.
- **Description**: Before every catch-up begins (before fetching any data), run a silent go/no-go check across all critical integrations. If any check fails, surface a specific error and halt — never produce a briefing that silently omits a data source. Catch-up #1 silently showed 0 calendar events because the Family calendar wasn't configured; this gate prevents that entire class of silent failures.
- **Dependencies**: T-1A.11.1 (open_items.md), T-1A.3.2, T-1A.3.3
- **Gate checks** (in order):
  1. OAuth token files exist at `~/.artha-tokens/` (both gmail + gcal tokens present)
  2. `python3 scripts/gmail_fetch.py --health` exits 0
  3. `python3 scripts/gcal_fetch.py --health` exits 0
  4. Vault lock file absent OR older than 30 minutes (stale — cleared per T-1A.11.5)
  5. At least one `.age` state file present (vault provisioned)
  6. `open_items.md` readable (once created)
- **Artha.md addition**: `## Pre-Flight Gate` section added before step 1 of the catch-up workflow. On any gate failure: display `⛔ Pre-flight failed: [check] — [error]` and halt. Gate results logged to `health-check.md`.
- **Acceptance Criteria**:
  - Catch-up halts with clear error message on any gate failure
  - Partial-data briefings are blocked (no silent omissions)
  - Gate runs in < 15 seconds total
  - Gate pass/fail logged to `health-check.md` per run
- **Effort**: 30 minutes (Artha.md addition only; scripts already have --health flags)
- **Ref**: Enhancement from catch-up #1 silent calendar gap

---

#### T-1A.11.4 — Validate OAuth token auto-refresh end-to-end

- [x] **Priority**: P0
- **Status**: ✅ Complete — `google_auth.py` updated: `_save_token` now persists `expiry` as UTC ISO string; `_load_token` restores `creds.expiry`; new public `validate_token_freshness()` proactively refreshes on near/past expiry, returning `{ok, message, expires_in_sec, refreshed}`. `preflight.py` now calls this function for accurate freshness reporting.
- **Description**: Google OAuth access tokens expire after 1 hour. The `google-api-python-client` library auto-refreshes using the `refresh_token` in the stored token file, but this has never been tested under actual expiry conditions. Silent expiry → 0 emails fetched with successful exit code → briefing looks complete but is empty. This is the most dangerous silent failure mode.
- **Dependencies**: T-1A.3.2, T-1A.3.3
- **Test steps**:
  1. Manually simulate expiry: edit `~/.artha-tokens/gmail-oauth-token.json`, set `expiry` to a past timestamp
  2. Run `python3 scripts/gmail_fetch.py --health` — verify auto-refresh succeeds
  3. Verify token file on disk is updated with new access token after refresh
  4. Repeat for `~/.artha-tokens/gcal-oauth-token.json`
  5. Failure mode test: remove `refresh_token` field entirely → verify a clear auth error is raised, not silent 0 results
- **Acceptance Criteria**:
  - Expired access token auto-refreshes without user intervention
  - Token file on disk updated after each refresh
  - Missing `refresh_token` raises a visible auth error (not silent 0 results)
  - Token refresh events logged to `health-check.md`
- **Effort**: 30 minutes
- **Ref**: Reliability gap identified from operational analysis

---

#### T-1A.11.5 — Stale lock file detection and auto-cleanup

- [x] **Priority**: P1
- **Status**: ✅ Complete — `vault.sh` updated: `check_lock_state()` inspects lock file mtime vs 1800s threshold (returns 0=no lock, 1=stale/cleared, 2=active/halt); `do_health()` runs 5 checks with exit 0/1; `health` subcommand added. `preflight.py` calls both.
- **Description**: If Artha crashes mid-catch-up, the vault lock file remains on disk and the next catch-up refuses to run or behaves incorrectly. Add stale-lock detection: if a lock file is older than 30 minutes, treat it as a crash remnant, log a warning, clear it, and proceed.
- **Dependencies**: T-1A.1.3 (vault.sh)
- **Steps**:
  1. Add stale lock check to `vault.sh` (or the catch-up pre-flight gate in T-1A.11.3): inspect `mtime` of lock file
  2. If lock age > 30 minutes: log `⚠ Stale lock detected (age: Xm) — clearing` to health-check.md and remove lock
  3. If lock age < 30 minutes: assume active session — halt with `⛔ Active session detected. Run 'rm /tmp/artha.lock' if this is an error.`
  4. Test crash recovery: manually create a stale lock, verify cleanup path fires on next catch-up
- **Acceptance Criteria**:
  - Stale lock (>30 min) auto-cleared with logged warning
  - Fresh lock (<30 min) blocks new catch-up with clear user-actionable error message
  - No manual intervention required after a crash followed by a normal retry
- **Effort**: 30 minutes (vault.sh edit + test)
- **Ref**: Reliability gap identified from operational analysis

---

#### T-1A.11.6 — Add rate limit and quota guard to Gmail/Calendar scripts

- [x] **Priority**: P1
- **Status**: ✅ Complete — `_with_retry()` helper added to both `gmail_fetch.py` and `gcal_fetch.py`: 3 retries, 1→2→4s backoff (max 30s), retryable on HTTP 429/5xx or quota/rate-limit error strings. Quota exhaustion on list calls → `sys.exit(2)` hard halt. Per-message/per-calendar failures → log warning + continue.
- **Description**: Gmail API and Google Calendar API enforce daily read quota limits. High-volume catch-ups (clearing a large backlog) can silently hit the quota, returning partial results with a success exit code. Add exponential backoff on HTTP 429/503 responses and a configurable `--max-results` guard.
- **Dependencies**: T-1A.3.2, T-1A.3.3
- **Steps**:
  1. Add `googleapiclient.errors.HttpError` exception handling to both `gmail_fetch.py` and `gcal_fetch.py`
  2. On HTTP 429 or 503: retry with exponential backoff (1s → 2s → 4s, max 3 retries)
  3. On retry exhaustion: fail loudly — `⛔ Gmail API quota exceeded — partial data, aborting catch-up`
  4. Add `--max-results` flag to `gmail_fetch.py` (default: 200, configurable via Artha.md setting)
  5. Log quota events and retry counts to `health-check.md`
- **Acceptance Criteria**:
  - 429/503 HTTP errors are retried with backoff before failing
  - Quota exhaustion fails loudly, never silently returns partial data
  - `--max-results` cap prevents runaway backlog fetches
  - Retry and quota events visible in health-check.md
- **Effort**: 45 minutes (Python script edits)
- **Ref**: Reliability gap from operational analysis

---

#### T-1A.11.7 — Add --dry-run mode to catch-up email delivery

- [x] **Priority**: P1
- **Status**: ✅ Complete — `gmail_send.py` updated: `--dry-run` validates auth via `getProfile()` then returns `{status: dry_run, to, from, body_length, archived}` without sending. `--to`/`--subject` not required in dry-run mode. Exit code 0 for sent or dry_run, 1 otherwise.
- **Description**: Add a `--dry-run` flag to `gmail_send.py` that prints the formatted email (subject + full HTML/plaintext body) to stdout without making any API call. Enables safe testing of briefing formatting, subject line changes, and sensitivity filter behavior without cluttering the inbox. Also the correct tool for debugging email rendering issues.
- **Dependencies**: T-1A.7.4 (gmail_send.py)
- **Steps**:
  1. Add `--dry-run` to `gmail_send.py` argparse
  2. When set: print `[DRY RUN] Subject: ...` followed by full body to stdout; skip `service.users().messages().send()` call
  3. Both MIME parts (HTML + plaintext) printed
  4. Add to Artha.md: "To preview briefing email without sending: `python3 scripts/gmail_send.py --dry-run`"
  5. Artha may use `--dry-run` when user says "show me what the briefing email looks like"
- **Acceptance Criteria**:
  - `--dry-run` prints full email content to stdout
  - No API call made in dry-run mode
  - Both HTML and plaintext MIME parts printed
  - Works independently of all other arguments
- **Effort**: 20 minutes
- **Ref**: Enhancement from operational experience

---

---

### Group 12: Automated Testing Framework

#### T-1A.12.1 — Setup pytest framework and test directory structure

- [ ] **Priority**: P0
- **Description**: Create the root `tests/` directory and core configuration files for `pytest`. Establish the structure for unit, integration, and extraction (Golden File) tests. Update `scripts/requirements.txt` with testing dependencies.
- **Dependencies**: None
- **Steps**:
  1. Create `tests/`, `tests/unit/`, `tests/integration/`, `tests/extraction/`, and `tests/fixtures/`
  2. Create `tests/conftest.py` with shared fixtures (e.g., temporary directory setup)
  3. Add `pytest`, `pytest-mock`, and `datadiﬀ` to `scripts/requirements.txt`
  4. Create a "Hello World" test in `tests/unit/test_health.py` to verify the environment
- **Acceptance Criteria**:
  - `pytest` runs and passes the health test
  - `tests/` directory structure exists
  - Requirements updated and installed in venv
- **Effort**: 30 minutes

---

#### T-1A.12.2 — Implement PII Guard unit tests

- [ ] **Priority**: P0
- **Description**: Create `tests/unit/test_pii_guard.py` to exhaustively test `pii_guard.sh`. Validate detection and redaction of all 8+ PII categories and ensure no "over-redaction" of allowlisted patterns.
- **Dependencies**: T-1A.12.1, T-1A.1.5 (`pii_guard.sh`)
- **Steps**:
  1. Write positive tests for each PII category (SSN, CC, ITIN, etc.)
  2. Write negative tests for allowlisted patterns (USCIS receipts, Amazon orders)
  3. Write boundary tests (PII at start/end of input, weird spacing)
  4. Execute via `subprocess.run(["bash", "scripts/pii_guard.sh", "filter"], ...)`
- **Acceptance Criteria**:
  - 100% pass rate for all PII patterns
  - Cross-platform check (handles missing bash gracefully)
- **Effort**: 1 hour

---

#### T-1A.12.3 — Implement Vault & Integrity tests

- [ ] **Priority**: P0
- **Description**: Create `tests/unit/test_vault.py` to validate `vault.py` logic and the "Net-Negative Write Guard."
- **Dependencies**: T-1A.12.1, T-1A.1.3 (`vault.py`)
- **Steps**:
  1. Mock `keyring` and `age` CLI calls
  2. Test `encrypt` and `decrypt` logic (state transitions, file naming)
  3. Test "Stale Lock" handling (auto-clear at 31m, block at 5m)
  4. Test "Net-Negative Write Guard": attempt to write a file with >20% field loss and verify it is blocked
- **Acceptance Criteria**:
  - Encryption/decryption state machine validated
  - Net-negative guard prevents data loss in simulated failure
- **Effort**: 1.5 hours

---

#### T-1A.12.4 — Implement Golden File extraction tests

- [ ] **Priority**: P1
- **Description**: Create `tests/extraction/test_extraction.py` to validate domain prompt accuracy using snapshots.
- **Dependencies**: T-1A.12.1, Group 6 (Domain Prompts)
- **Steps**:
  1. Create `tests/fixtures/mock_emails.jsonl` with sample data
  2. Create `tests/fixtures/expected_immigration.md` (manually verified)
  3. Write test to run extraction engine on mock input and compare output to reference
  4. Use `datadiﬀ` to provide clear failure reports on extraction drift
- **Acceptance Criteria**:
  - Tests pass for at least 2 core domains (Immigration, Finance)
  - Diff output is readable and actionable
- **Effort**: 2 hours

---

---

### Group 13: Data Fidelity Skills

#### T-1A.13.1 — Create Skill Registry and Base Class

- [x] **Priority**: P0
- **Description**: Establish the foundational infrastructure for modular skills. Create the `config/skills.yaml` registry and the `scripts/skills/base_skill.py` abstract base class.
- **Dependencies**: None
- **Steps**:
  1. Create `config/skills.yaml` with placeholder entries for `uscis_status` and `king_county_tax`
  2. Create `scripts/skills/` directory
  3. Implement `scripts/skills/base_skill.py` with `pull()`, `parse()`, and `to_dict()` interfaces
- **Acceptance Criteria**:
  - `BaseSkill` class defined
  - `skills.yaml` is valid YAML
- **Effort**: 30 minutes

---

#### T-1A.13.2 — Build USCIS Status Skill (Phase 1)

- [x] **Priority**: P0
- **Description**: Implement zero-latency immigration status check.
- **Dependencies**: T-1A.13.1, `state/immigration.md` (must have receipt numbers)
- **Steps**:
  1. Create `scripts/skills/uscis_status.py`
  2. Implement `pull()` to fetch status from `egov.uscis.gov` via `requests`
  3. Implement `parse()` using `BeautifulSoup` to extract the status text
  4. Integrate with `state/immigration.md` to retrieve receipt numbers
- **Acceptance Criteria**:
  - Script returns current USCIS status for all receipts
  - Handles network timeouts gracefully (warn and continue)
- **Effort**: 1.5 hours

---

#### T-1A.13.3 — Build King County Tax Skill (Phase 1)

- [x] **Priority**: P1
- **Description**: Monitor property tax due dates and amounts for the April 30 deadline.
- **Dependencies**: T-1A.13.1, `config/artha_config.yaml` (must have parcel ID)
- **Steps**:
  1. Create `scripts/skills/king_county_tax.py`
  2. Implement `pull()` to fetch tax status from King County Assessor
  3. Implement `parse()` using `BeautifulSoup` to extract due date and amount
- **Acceptance Criteria**:
  - Script correctly identifies the $8,250 amount and April 30 deadline
- **Effort**: 1 hour

---

#### T-1A.13.4 — Build Skill Runner Orchestrator

- [x] **Priority**: P0
- **Description**: Central orchestrator to run all enabled skills in parallel and aggregate results.
- **Dependencies**: T-1A.13.1
- **Steps**:
  1. Create `scripts/skill_runner.py`
  2. Read `config/skills.yaml` to identify enabled skills
  3. Execute skills in parallel
  4. Write aggregate JSON to `tmp/skills_cache.json`
  5. Enforce failure logic: exit 1 on P0 failure, 0 on others
- **Acceptance Criteria**:
  - Running `skill_runner.py` produces valid JSON in `tmp/`
- **Effort**: 1 hour

---

#### T-1A.13.5 — Integrate into catch-up pipeline

- [x] **Priority**: P0
- **Description**: Wire the skill runner into the main Artha catch-up workflow.
- **Dependencies**: T-1A.13.4
- **Steps**:
  1. Update `scripts/preflight.py` to verify `requests` and `beautifulsoup4` dependencies
  2. Update `Artha.md` Step 4 to call `skill_runner.py`
  3. Update Step 5 to ingest `tmp/skills_cache.json`
- **Acceptance Criteria**:
  - Catch-up run successfully includes skill data in LLM context
- **Effort**: 45 minutes

---

---

### Group 14: Advanced Intelligence Skills

#### T-1A.14.1 — Implement Intelligence Foundation (Orchestrator v2)

- [x] **Priority**: P0
- **Description**: Upgrade `skill_runner.py` with dynamic loading, per-skill cadence control, and generic change detection logic.
- **Dependencies**: T-1A.13.4
- **Steps**:
  1. Refactor `skill_runner.py` to use `importlib` for dynamic skill discovery
  2. Implement `compare_fields` delta tracking in the runner logic
  3. Implement `last_run` cadence enforcement using `state/skills_cache.json`
  4. Ensure cache file is encrypted via `vault.py` (verify `SENSITIVE_FILES` update)
- **Acceptance Criteria**:
  - Skills only run when cadence requires it
  - Runner detects and tags changed fields in the aggregate JSON
- **Effort**: 1.5 hours

---

#### T-1A.14.2 — Implement Visa Bulletin Skill (P0)

- [x] **Priority**: P0
- **Description**: Replace Gemini web search with deterministic monthly bulletin parser.
- **Dependencies**: T-1A.14.1, `state/immigration.md` (must have priority date and category)
- **Steps**:
  1. Create `scripts/skills/visa_bulletin.py`
  2. Parse Table A and B from `travel.state.gov`
  3. Parse authorized chart from `uscis.gov` (AOS Filing Charts URL)
  4. Validate results with regex `(\d{2}[A-Z]{3}\d{2}|C|U)`
- **Acceptance Criteria**:
  - Script extracts both tables and current month's chart authorization
  - No Gemini fallback on parse error (warn + stale marker instead)
- **Effort**: 2 hours

---

#### T-1A.14.3 — Implement NHTSA Recall & Assessor Extension

- [x] **Priority**: P1
- **Description**: Safety-critical recall monitoring and finance-critical home value tracking.
- **Dependencies**: T-1A.14.1
- **Steps**:
  1. Create `scripts/skills/nhtsa_recalls.py` (VIN-based API call)
  2. Update `scripts/skills/king_county_tax.py` to extract `assessed_value` and `last_sale_price`
  3. Update `state/vehicle.md` with Mazda CX-50 VIN and year
- **Acceptance Criteria**:
  - Recall status detected for both vehicles
  - Home value extracted and written to cache
- **Effort**: 1.5 hours

---

#### T-1A.14.4 — Implement NOAA Weather Concierge

- [x] **Priority**: P1
- **Description**: Proactive unblocking of outdoor goals.
- **Dependencies**: T-1A.14.1
- **Steps**:
  1. Create `scripts/skills/noaa_weather.py`
  2. Implement keyword trigger logic (description search)
  3. Implement two-step API call with `owner_email` User-Agent
- **Acceptance Criteria**:
  - Weather info only appears if "hike/summit/etc" keywords are found in open items
  - Sammamish weekend preview appears on Friday catch-ups
- **Effort**: 1.5 hours

---

## Phase 1B — High-Value Domains (Weeks 3–5)

> **Objective**: Build `msgraph_fetch.py` and `msgraph_calendar_fetch.py` for direct Outlook email and calendar fetch via the live MS Graph token, expand remaining domain prompts, test ensemble reasoning, and prepare contact infrastructure for visual greetings.
> **Success gate**: Immigration, Finance, Kids, Communications, Travel, Health, Home domains all operational with real data flowing through catch-ups.

---

### Group 1: Microsoft Graph Email & Calendar Integration

#### T-1B.1.1 — Build msgraph_fetch.py: direct Outlook email fetch

- [x] **Priority**: P0
- **Description**: Build `scripts/msgraph_fetch.py` — reads Outlook/Hotmail inbox via MS Graph `Mail.Read` scope using the existing `~/.artha-tokens/msgraph-token.json`. Output format matches `gmail_fetch.py` JSONL schema with an added `"source": "outlook"` field, making it a drop-in parallel data source for the catch-up pipeline. Runs in parallel with `gmail_fetch.py` at Step 3. Replaces the forwarding approach entirely.
- **Dependencies**: T-1B.6.1 (✅ MS Graph OAuth live as `configured-outlook@example.com`)
- **Steps**:
  1. Create `scripts/msgraph_fetch.py` mirroring `gmail_fetch.py` interface:
     - `--since YYYY-MM-DDTHH:MM:SS` — fetch after timestamp (idempotency)
     - `--max-results N` — cap per run (default 200)
     - `--folder FOLDER` — `inbox` (default), `sentItems`, `archive`
     - `--health` — connectivity check only, no fetch
     - `--dry-run` — print count without writing
  2. Reuse `ensure_valid_token()` from `setup_msgraph_oauth.py` for auto-refresh
  3. HTML stripping and thread truncation to 3000 chars — same as `gmail_fetch.py`
  4. Exponential backoff on 429/503 (same `_with_retry` pattern)
  5. Output JSONL to stdout: `{id, subject, sender, date, body_text, thread_id, source: "outlook"}`
  6. Add to `preflight.py` as P1 check: `python3 scripts/msgraph_fetch.py --health`
  7. Update `Artha.md` Step 3 to run `gmail_fetch.py` and `msgraph_fetch.py` in parallel
- **Acceptance Criteria**:
  - `msgraph_fetch.py --health` exits 0
  - Fetches last 7 days of Outlook inbox and outputs valid JSONL
  - `--since` flag is idempotent (same timestamp → same result)
  - Output schema compatible with `gmail_fetch.py` output (catch-up pipeline ingests both transparently)
  - `preflight.py` P1 check passes
- **Effort**: 1.5 hours
- **Ref**: TS §3.8

---

#### T-1B.1.2 — ~~Set up Apple ID (iCloud) email forwarding to Gmail~~ → SUPERSEDED

- [x] **Priority**: P1 (superseded)
- **Description**: **SUPERSEDED by T-1B.1.8 (direct IMAP+CalDAV).** Forwarding approach retired in Session 4. Scripts `icloud_mail_fetch.py` and `icloud_calendar_fetch.py` provide direct API access without forwarding. T-1B.1.2 is closed — no forwarding setup needed.
- **Ref**: Superseded by T-1B.1.8, T-1B.1.9, T-1B.1.10

---

#### T-1B.1.3 — Evaluate and configure Yahoo email forwarding

- [ ] **Priority**: P2
- **Description**: Assess whether the Yahoo account holds actionable email for Artha. Yahoo accounts are often legacy (pre-dating Gmail) and may carry old bank notifications, social network mail, or dormant subscriptions. Forward only if ≥3 actionable emails arrive per month.
- **Dependencies**: None
- **Steps**:
  1. Log into Yahoo Mail, review last 30 days of email
  2. Categorize: spam, dormant newsletter, active bank/service notifications, actionable items
  3. Decision threshold: if ≥3 actionable emails in 30 days → configure forwarding
  4. If forwarding: Yahoo Settings → More Settings → Mailboxes → forward to configured-gmail@example.com
  5. In Gmail: create filter for Yahoo-forwarded senders → apply label `from-yahoo`
  6. If not forwarding: document in `health-check.md` as `yahoo: excluded (dormant)` with date reviewed
- **Acceptance Criteria**:
  - Decision documented in `health-check.md` with rationale and review date
  - If forwarding: Gmail label created and routing rules added
  - If excluded: acknowledged gap entered in email coverage matrix (T-1B.1.5)
- **Effort**: 20 minutes
- **Ref**: Email coverage expansion decision

---

#### T-1B.1.4 — Document Proton Mail integration decision

- [ ] **Priority**: P2
- **Description**: Proton Mail uses end-to-end encryption by design, which blocks standard IMAP forwarding. Proton Bridge (desktop app) is the only supported integration path. Assess based on volume of actionable mail arriving at Proton. If it is primarily privacy-sensitive personal communication, it should be intentionally excluded from Artha — that is a correct boundary, not a gap.
- **Dependencies**: None
- **Integration paths**:
  - **Proton Bridge** (if integration desired): Install Proton Bridge → configure local IMAP → create Gmail forwarding rule. Adds a persistent desktop daemon. Viable for Phase 2.
  - **Excluded by design**: if Proton holds private personal correspondence that should not be aggregated — document as an intentional boundary.
- **Steps**:
  1. Review Proton Mail: what categories of email arrive there?
  2. If contains immigration, finance, health, or school email → plan Proton Bridge integration for Phase 2
  3. If primarily personal/private correspondence → document as excluded boundary
  4. Add entry to email coverage matrix in `health-check.md`
- **Acceptance Criteria**:
  - Decision documented in `health-check.md`: integrate via Bridge (Phase 2 plan) OR exclude with boundary rationale
  - If Bridge: setup steps noted for Phase 2 (T-2.x placeholder created)
  - No ambiguity — this is a deliberate decision, not an oversight
- **Effort**: 20 minutes (assessment only; Proton Bridge setup is Phase 2 if chosen)
- **Ref**: Proton Bridge: https://proton.me/mail/bridge

---

#### T-1B.1.5 — Email coverage matrix and gap acknowledgment

- [ ] **Priority**: P1
- **Description**: After completing T-1B.1.1–1.4, document Artha's complete email coverage in `health-check.md`. For each account: status, forward target, Gmail label, primary domain routing, and any acknowledged gaps. This becomes the authoritative record of Artha's email coverage boundary — visible via `/health` command.
- **Dependencies**: T-1B.1.1, T-1B.1.2, T-1B.1.3, T-1B.1.4
- **Schema to add to health-check.md**:
  ```yaml
  email_coverage:
    gmail_primary:
      status: connected
      account: configured-gmail@example.com
      domains: all
    outlook:
      status: direct_api        # MS Graph Mail.Read — no forwarding needed
      account: configured-outlook@example.com
      script: msgraph_fetch.py  # T-1B.1.1
      domains: immigration, finance, comms
    apple:
      status: forwarding        # Apple has no public Mail API
      gmail_label: from-apple
      domains: finance, digital_life
    yahoo:
      status: excluded|forwarding
      rationale: "dormant, reviewed 2026-03-XX"
    proton:
      status: excluded|bridge_phase2
      rationale: "personal comms boundary" OR "Bridge planned Phase 2"
  ```
- **Acceptance Criteria**:
  - `health-check.md` has complete `email_coverage` block
  - Every email account has a documented status (connected, forwarding, or excluded with rationale)
  - Gaps are acknowledged, not ignored
  - `/health` slash command surfaces coverage matrix summary
- **Effort**: 15 minutes
- **Ref**: Operational visibility requirement

---

#### T-1B.1.6 — Build msgraph_calendar_fetch.py: direct Outlook Calendar fetch

- [x] **Priority**: P1
- **Description**: Build `scripts/msgraph_calendar_fetch.py` — fetches Outlook Calendar events via MS Graph `/me/calendarView` endpoint using the existing token. Output matches `gcal_fetch.py` JSONL schema with `"source": "outlook_calendar"`. Covers calendar events in Outlook that aren’t in Google Calendar (Microsoft meeting invites, Teams meetings, Outlook-only personal events).
- **Dependencies**: T-1B.6.1 (✅ OAuth live), T-1B.1.1 (msgraph_fetch.py pattern established)
- **Steps**:
  1. Create `scripts/msgraph_calendar_fetch.py` mirroring `gcal_fetch.py` interface:
     - `--from`, `--to` date range flags
     - `--today-plus-days N` shorthand
     - `--health` flag
     - `--calendars` flag (comma-separated Outlook calendar IDs; default: all)
  2. Enumerate all Outlook calendars: `GET /me/calendars`
  3. Fetch events per calendar: `/me/calendars/{id}/calendarView?startDateTime=&endDateTime=`
  4. Parse: subject, start/end, location, organizer, attendees
  5. Output JSONL: same schema as `gcal_fetch.py` with `"source": "outlook_calendar"`
  6. Deduplication note in Artha.md: if same event in Google Calendar and Outlook Calendar (title + start time match) → keep one, note `source: both`
  7. Update Artha.md Step 3 to include `msgraph_calendar_fetch.py` in parallel fetch block
- **Acceptance Criteria**:
  - `msgraph_calendar_fetch.py --health` exits 0
  - Outlook Calendar events appear in catch-up briefing calendar section
  - No duplicate events if same event is in both Google and Outlook calendars
- **Effort**: 1 hour
- **Ref**: TS §3.8

---

#### T-1B.1.7 — Build msgraph_onenote_fetch.py + add Notes.Read scope

- [x] **Priority**: P1
- **Description**: Build `scripts/msgraph_onenote_fetch.py` to read OneNote notebooks as structured plain-text JSONL. Ved uses OneNote extensively for planning (finance, immigration checklists, kids activities, home/vehicle logs) — this content is highly relevant for enriching the Artha state layer beyond what email/calendar alone provide. Requires adding `Notes.Read` to OAuth scopes and running `setup_msgraph_oauth.py --reauth`.
- **Dependencies**: T-1B.6.1 (✅ OAuth live), T-1B.1.1 (msgraph pattern established)
- **Steps**:
  1. Add `"Notes.Read"` to `_SCOPES` in `scripts/setup_msgraph_oauth.py`
  2. Run `python3 scripts/setup_msgraph_oauth.py --reauth` to get updated token
  3. Create `scripts/msgraph_onenote_fetch.py`:
     - `--notebook NAME` — restrict to specific notebook (partial name match)
     - `--section NAME` — restrict to specific section
     - `--modified-since TIMESTAMP` — only pages modified after this date (idempotency)
     - `--health` — connectivity check
     - `--list-notebooks` — print all notebook names and IDs
  4. API calls:
     - `GET /me/onenote/notebooks?$select=id,displayName,lastModifiedDateTime`
     - `GET /me/onenote/notebooks/{id}/sections?$select=id,displayName`
     - `GET /me/onenote/sections/{id}/pages?$select=id,title,lastModifiedDateTime&$filter=lastModifiedDateTime ge {since}`
     - `GET /me/onenote/pages/{id}/content` — returns HTML; strip to plain text
  5. Output JSONL: `{notebook, section, page_title, last_modified, content_text, source: "onenote"}`
  6. Cap content at 3000 chars per page (same truncation pattern as `msgraph_fetch.py`)
  7. Add `--notebook` filter default: skip notebooks named "Personal Notebook" (usually empty)
  8. Update `Artha.md` Step 4 to add `msgraph_onenote_fetch.py --modified-since {last_run}` as optional P1 parallel fetch
  9. Add P1 preflight check for `msgraph_onenote_fetch.py --health` (after scope added)
- **Acceptance Criteria**:
  - `msgraph_onenote_fetch.py --health` exits 0
  - `--list-notebooks` shows all notebook names
  - JSONL output includes key notebooks (Finance, Immigration, Home, Kids at minimum)
  - `--modified-since` is idempotent
  - Catch-up briefing reflects OneNote content in relevant domain sections
- **Effort**: 2 hours
- **Ref**: TS §3.8

---

#### T-1B.1.8 — Set up Apple iCloud credentials (IMAP + CalDAV)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — 2026-03-08. `setup_icloud_auth.py` run successfully. IMAP and CalDAV both live. 1764 inbox messages; 6 calendars found.
- **Dependencies**: `scripts/setup_icloud_auth.py` (✅ built), `scripts/icloud_mail_fetch.py` (✅ built), `scripts/icloud_calendar_fetch.py` (✅ built, requires `caldav` ✅ installed)
- **Steps**:
  1. Go to `account.apple.com` → Sign-In and Security → App-Specific Passwords → Generate
  2. Label the password "Artha" to identify it for future revocation
  3. Run: `python3 scripts/setup_icloud_auth.py`
  4. Enter Apple ID (e.g. name@icloud.com) and paste the generated xxxx-xxxx-xxxx-xxxx password
  5. Verify: `python3 scripts/setup_icloud_auth.py --health`
  6. Verify mail: `python3 scripts/icloud_mail_fetch.py --health`
  7. Verify calendar: `python3 scripts/icloud_calendar_fetch.py --health`
  8. Verify calendar list: `python3 scripts/icloud_calendar_fetch.py --list-calendars`
  9. Update `config/settings.md`: set `icloud.setup_complete: true`
  10. Update `state/health-check.md`: `icloud_mail: true`, `icloud_calendar: true`
  11. Run `python3 scripts/preflight.py` — should show 17+/X with iCloud P1 checks passing
- **Acceptance Criteria**:
  - `setup_icloud_auth.py --health` exits 0 (IMAP + CalDAV both OK)
  - `icloud_mail_fetch.py --health` exits 0 (inbox message count visible)
  - `icloud_calendar_fetch.py --health` exits 0 (calendar list shown)
  - Preflight P1 checks for iCloud pass
  - Next catch-up includes iCloud email + calendar in Step 4
- **Effort**: 10 minutes
- **Ref**: TS §3.9, supersedes T-1B.1.2

---

### Group 2: Additional Domain Prompts

#### T-1B.2.1 — Refine immigration prompt with real email data

- [ ] **Priority**: P0
- **Description**: After 2–3 weeks of real catch-ups, review immigration domain performance and refine extraction rules, alert thresholds, and sender patterns based on actual email content.
- **Dependencies**: Phase 1A complete, 2+ weeks of catch-ups
- **Acceptance Criteria**:
  - Immigration domain accuracy ≥95% over 7-day window
  - Zero false positives in critical alerts
  - Visa Bulletin parsing accurate
  - CSPA calculations verified against manual calculation
- **Effort**: 1 hour
- **Ref**: TS §12.8

---

#### T-1B.2.2 — Author Travel domain prompt (FR-5)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/travel.md created with immigration AP cross-check alert.
- **Description**: Create `~/OneDrive/Artha/prompts/travel.md` — tracks trip planning, bookings, loyalty programs (Alaska Airlines, Marriott Bonvoy).
- **Dependencies**: T-1A.2.1
- **Content**: Trip dashboard (F5.1), loyalty points aggregator (F5.2), travel document checker (F5.3), flight alert (F5.4), India trip planner (F5.6, P2)
- **Acceptance Criteria**: Travel emails correctly extracted, upcoming trips tracked
- **Effort**: 30 minutes
- **Ref**: PRD FR-5

---

#### T-1B.2.3 — Author Health domain prompt (FR-6)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/health.md created with per-person tracking and FSA alerts.
- **Description**: Create `~/OneDrive/Artha/prompts/health.md` — tracks family appointments, Rx refills, HSA balance.
- **Dependencies**: T-1A.2.1
- **Content**: Family appointment calendar (F6.1), HSA balance & utilization (F6.2), annual preventive care tracker (F6.3), Rx refill tracker (F6.5), wellness goal integration (F6.6)
- **Sensitivity**: high (encrypted)
- **Acceptance Criteria**: Health appointment emails parsed, Rx refill reminders surface, preventive care schedule tracked
- **Effort**: 45 minutes
- **Ref**: PRD FR-6

---

#### T-1B.2.4 — Author Home domain prompt (FR-7)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/home.md created with quarterly Zillow/Redfin check.
- **Description**: Create `~/OneDrive/Artha/prompts/home.md` — tracks utilities, mortgage, maintenance, property.
- **Dependencies**: T-1A.2.1
- **Content**: Utility bill calendar (F7.1), mortgage tracker (F7.2), maintenance schedule (F7.3), telecom & internet tracker (F7.8)
- **Known senders**: PSE Energy, Sammamish Water, Wells Fargo (mortgage), Republic Services, ISP
- **Acceptance Criteria**: Utility bills tracked, maintenance schedule populated
- **Effort**: 30 minutes
- **Ref**: PRD FR-7

---

### Group 3: Ensemble Reasoning Validation

#### T-1B.3.1 — Test ensemble reasoning on real immigration question

- [ ] **Priority**: P1
- **Description**: Run the first ensemble query (all 3 LLMs) on a real immigration question. Evaluate whether the synthesized answer improves over Claude-only reasoning.
- **Dependencies**: T-1A.5.1, T-1A.5.2, T-1A.1.6
- **Steps**:
  1. Select a real immigration question (e.g., "What is the recommended timing for our EAD renewal?")
  2. Get Claude's answer (state-based reasoning)
  3. Get Gemini's answer (web-augmented via safe_cli.sh)
  4. Get Copilot's answer (via safe_cli.sh)
  5. Claude synthesizes the best answer
  6. Evaluate: did the ensemble add value?
- **Acceptance Criteria**:
  - All 3 LLMs respond without PII leakage
  - Synthesized answer is at least as good as Claude-only
  - Process documented for future ensemble triggers
- **Effort**: 30 minutes
- **Resolves**: TD-11 (ensemble voting — start with Claude-as-synthesizer)
- **Ref**: TS §3.7.3

---

### Group 4: Contact & Visual Infrastructure

#### T-1B.4.1 — Populate contacts.md with family and friends

- [ ] **Priority**: P1
- **Description**: Add actual contact entries to `contacts.md` — email addresses and phone numbers for all contact groups (festival greetings, birthday reminders).
- **Dependencies**: T-1A.2.4
- **Acceptance Criteria**:
  - At least 3 contact groups defined (family India, friends US, colleagues)
  - Birthday reminders list populated
  - File encrypted in `.age`
- **Effort**: 30 minutes (manual data entry)
- **Ref**: TS §7.4.3

---

#### T-1B.4.2 — End-to-end visual greeting workflow test

- [ ] **Priority**: P1
- **Description**: Test the complete visual greeting pipeline: generate card → compose email → send to test recipient.
- **Dependencies**: T-1A.8.2, T-1A.7.4, T-1B.4.1
- **Steps**:
  1. Generate a festival card via Gemini Imagen
  2. Compose a personalized email with card attached
  3. Send to a test recipient (self)
  4. Verify email arrives with image attachment
  5. Test WhatsApp flow: open WhatsApp with pre-filled greeting
- **Acceptance Criteria**:
  - Visual generated and saved to `visuals/`
  - Email composed and sent successfully with attachment
  - WhatsApp opens with pre-filled text
  - Full pipeline works end-to-end
- **Effort**: 30 minutes
- **Ref**: TS §7.4.6, UX §18

---

### Group 5: Mobile Access

#### T-1B.5.1 — Set up Claude.ai Project for iPhone

- [ ] **Priority**: P1
- **Description**: Create a Claude.ai Project named "Artha" on the iOS app for mobile read-only queries.
- **Dependencies**: Phase 1A complete (state files populated)
- **Steps**:
  1. Create Claude.ai Project named "Artha"
  2. Upload simplified system instructions (read-only query support)
  3. Configure to read from OneDrive state files (standard sensitivity only)
  4. Test: "What's my immigration status?" → answers from state
  5. Verify sensitivity boundary: high/critical queries → "Only available during Mac catch-up session"
- **Acceptance Criteria**:
  - iPhone queries answered from state files
  - Sensitivity filter enforced (encrypted files not accessible)
  - Response format matches UX §7.1 guidelines
- **Effort**: 30 minutes
- **Ref**: TS §13.3, PRD §9.3

---

### Group 6: Microsoft To Do Integration

> **Objective**: Connect `open_items.md` (persistent action tracking from T-1A.11.1) to Microsoft To Do via Graph API, turning Artha's briefing items into a prioritized, notification-driven task list on iPhone/Windows. Single Graph OAuth token covers both To Do and Outlook.

#### T-1B.6.1 — Set up Microsoft Graph API OAuth for To Do + Outlook

- [x] **Priority**: P1
- **Status**: ✅ LIVE 2026-03-08 — Azure app registered ("Artha Personal Assistant", personal accounts only, redirect http://localhost:8400). OAuth flow complete: authenticated as configured-outlook@example.com. Token at `~/.artha-tokens/msgraph-token.json`. Fixed 3 bugs during setup: `offline_access` reserved scope conflict, `redirect_uri`/`port` MSAL parameter conflict, `datetime` NameError in preflight.py. Preflight now passes 13/13 checks, 0 warnings.
- **Description**: Register an Azure AD app and complete OAuth 2.0 PKCE flow to obtain a long-lived Graph API token. This single token covers both `Tasks.ReadWrite` (Microsoft To Do) and `Mail.Read` (Outlook), eliminating a separate Outlook OAuth flow. Token stored at `~/.artha-tokens/msgraph-token.json` following the same pattern as Google OAuth.
- **Dependencies**: T-1A.11.1, T-1A.11.4 (OAuth auto-refresh validation)
- **Steps**:
  1. Register app in Azure AD (portal.azure.com → App Registrations)
  2. Grant scopes: `Tasks.ReadWrite`, `Tasks.ReadWrite.Shared`, `Mail.Read`, `offline_access`
  3. Set redirect URI: `http://localhost:8400/callback`
  4. Write `scripts/setup_msgraph_oauth.py` — one-time PKCE auth flow
  5. Store token at `~/.artha-tokens/msgraph-token.json`
  6. Test: `python3 scripts/setup_msgraph_oauth.py` → token acquired
  7. Test: `python3 -c "import msal; ..."` → `Tasks.ReadWrite` confirmed
  8. Add auto-refresh logic (same pattern as Gmail: check expiry, refresh before use)
  9. Add `msgraph_token` to pre-flight gate check (T-1A.11.3)
- **Acceptance Criteria**:
  - Token file exists and is valid
  - Token auto-refreshes when expiring (pre-flight gate verifies this)
  - `Mail.Read` scope confirmed (to be used in T-1B.1.1 Outlook integration)
- **Effort**: 1.5 hours
- **Ref**: PRD §11, TS §3.1, UX §10.5

---

#### T-1B.6.2 — Create domain-tagged Microsoft To Do lists

- [x] **Priority**: P1
- **Status**: ✅ LIVE 2026-03-08 — 7 lists created: Artha · Kids, Immigration, Finance, Health, Home, Comms, General. List IDs saved to `config/artha_config.yaml`. 31 pre-existing lists found in account (skipped). `todo_sync: true` set in settings.md and health-check.md.
- **Description**: Create 7 named To Do lists in the user's Microsoft To Do account via Graph API — one per Artha domain. These lists are the user-facing task manager; `open_items.md` is Artha's bridge file. Artha never creates tasks in the default "Tasks" list.
- **Dependencies**: T-1B.6.1
- **Steps**:
  1. Write `scripts/setup_todo_lists.py` — idempotent list creation (skip if already exists)
  2. Create lists: `Artha · Kids`, `Artha · Immigration`, `Artha · Finance`, `Artha · Health`, `Artha · Home`, `Artha · Comms`, `Artha · General`
  3. Store list IDs in `config/artha_config.yaml` (new `todo_lists:` section)
  4. Test: lists visible in Microsoft To Do on iPhone and Windows
- **Config schema**:
  ```yaml
  todo_lists:
    kids: "AAMkAGI2..."
    immigration: "AAMkABB3..."
    finance: "AAMkACW4..."
    health: "AAMkADX5..."
    home: "AAMkAEY6..."
    comms: "AAMkAFZ7..."
    general: "AAMkAGA8..."
  ```
- **Acceptance Criteria**:
  - 7 lists exist in To Do account
  - List IDs stored in `artha_config.yaml`
  - Script is idempotent (safe to re-run)
- **Effort**: 20 minutes
- **Ref**: TS §1.3 (config description)

---

#### T-1B.6.3 — Build todo_sync.py: push open items to Microsoft To Do

- [x] **Priority**: P1
- **Status**: ✅ Complete — `scripts/todo_sync.py` created: push mode reads `open_items.md`, pushes status=open+todo_id="" items to correct domain list, writes returned todo_id back to file; exponential backoff retry; `--dry-run`; non-blocking on failure (logs to audit.md, leaves todo_id="" for retry).
- **Description**: Write `scripts/todo_sync.py` — reads `state/open_items.md`, pushes items with `status: open` and `todo_id: ""` to the appropriate domain-tagged To Do list, then writes the returned `todo_id` back into `open_items.md`. Called as the final step of catch-up (Step 8f in TS §7.1).
- **Dependencies**: T-1B.6.1, T-1B.6.2, T-1A.11.1
- **Steps**:
  1. Parse `open_items.md` for items with `status: open` AND `todo_id: ""`
  2. For each: call `POST /me/todo/lists/{listId}/tasks` with title (description), dueDateTime (deadline), importance (P0→high, P1→normal, P2→low)
  3. Write returned task ID back to `open_items.md` as `todo_id`
  4. Handle To Do API rate limits (same exponential backoff as T-1A.11.6)
  5. On failure: log warning to `audit.md`, leave `todo_id: ""` for retry next run (non-blocking)
  6. Add `--dry-run` flag: print tasks that would be pushed without calling API
- **Acceptance Criteria**:
  - New items from catch-up appear in correct domain list in To Do within ~1 minute
  - `todo_id` populated in `open_items.md` after successful push
  - Failure is non-blocking — catch-up completes even if To Do sync fails
  - `--dry-run` works correctly
- **Effort**: 1.5 hours
- **Ref**: TS §7.1 Step 8f, TS §7.2 (To Do sync failure row), UX §10.5

---

#### T-1B.6.4 — Pull To Do completion status back to open_items.md

- [x] **Priority**: P1
- **Status**: ✅ Complete — `todo_sync.py --pull` fetches completion status for all tracked items, marks `status: done` + `date_resolved: today` in `open_items.md`, logs to audit.md. `todo_sync.py --status` prints sync summary without API calls.
- **Description**: At the start of each catch-up (Step 0b in TS §7.1), call `todo_sync.py --pull` to fetch completion status from To Do for all items with a known `todo_id`. Items the user marked done in To Do are updated to `status: done` in `open_items.md` and moved to the `## Resolved` section. Prevents Artha from re-surfacing tasks the user already completed.
- **Dependencies**: T-1B.6.3
- **Steps**:
  1. Add `--pull` flag to `todo_sync.py`
  2. For each `open_items.md` entry with non-empty `todo_id`: call `GET /me/todo/lists/{listId}/tasks/{taskId}`
  3. If `status: completed` → update `open_items.md`: `status: done`, `date_resolved: today`
  4. Move resolved items to `## Resolved` section
  5. Log completion pull summary to `audit.md`
- **Acceptance Criteria**:
  - Tasks completed in To Do on iPhone → reflected as `status: done` in `open_items.md` at next catch-up
  - Done items no longer surfaced in briefing or `/items` output
  - Bidirectional sync loop verified: Artha pushes → user completes in To Do → Artha pulls completion
- **Effort**: 45 minutes
- **Ref**: TS §7.1 Step 0b

---

#### T-1B.6.5 — Support manual task addition to open_items.md without a catch-up

- [ ] **Priority**: P2
- **Description**: Allow the user to add an action item to `open_items.md` (and immediately push to To Do) mid-session without running a full catch-up. Useful for tasks that arise in conversation ("remind me to call Fragomen about the I-797").
- **Dependencies**: T-1B.6.3
- **Steps**:
  1. Author a CLAUDE.md instruction or slash command handler for "add item: [description]"
  2. Artha asks: domain? deadline? priority? (with defaults)
  3. Append new entry to `open_items.md` with next available id
  4. Call `todo_sync.py` immediately to push to To Do
  5. Confirm: "Added OI-NNN to open_items.md and pushed to Artha · [Domain] in Microsoft To Do."
- **Acceptance Criteria**:
  - User can add a task in ≤3 conversational turns
  - Item appears in correct To Do list within 30 seconds
  - `open_items.md` updated with correct id, date, domain, todo_id
- **Effort**: 30 minutes
- **Ref**: UX §10.1 (/items command)

---

## Phase 1C — Goal Engine + Finance Expansion (Weeks 6–8)

> **Objective**: Launch the Goal Intelligence Engine (FR-13, P0), expand finance coverage, set up conversation memory, and generate first weekly summary. The Goal Engine is Artha's most distinctive feature.
> **Success gate**: ≥5 active goals with automatic metric collection, first weekly summary generated, conversation memory functional.

---

### Group 1: Goal Intelligence Engine

#### T-1C.1.1 — Author Goals domain prompt (FR-13, P0)

- [x] **Priority**: P0
- **Status Note**: COMPLETE — prompts/goals.md created with progress bar format and goal pulse briefing.
- **Description**: Create `~/OneDrive/Artha/prompts/goals.md` — the most complex domain prompt. Implements conversational goal creation, automatic metric wiring, scorecard generation, conflict detection.
- **Content** (per PRD §8):
  - Goal model schema: name, type (outcome/habit/milestone), metric, metric_source, target_value, target_date, current_value, cadence, status
  - Three goal types with distinct tracking patterns
  - Auto-metric wiring table: goal → source state file → metric field → cadence
  - Weekly scorecard format (Unicode progress bars)
  - Recommendation engine (threshold-based suggestions)
  - Conversational creation flow (UX §8.1)
  - Goal conflict detection logic (UX §8.4)
- **Acceptance Criteria**:
  - Goal model schema supports all 3 types
  - Auto-metric wiring maps at least 5 metrics to state files
  - Scorecard generates with progress bars and trend indicators
  - Conflict detection identifies when two goals are in tension
- **Effort**: 1.5 hours
- **Ref**: PRD §8, UX §8

---

#### T-1C.1.2 — Define initial 5 goals in state file (OQ-3)

- [~] **Priority**: P0
- **Status Note**: PARTIAL 2026-03-08 Session 2 — goals.md bootstrapped with 7 goals derived from first catch-up analysis (immigration, estate planning, insurance, Trisha academic recovery, career growth, financial health, health & wellness). Schema upgraded to v2.0 with goals_index YAML block containing type/metric/target_value fields. Remaining: formal 5-goal conversational definition per UX-5 spec (T-1C.1.2 requires conversation, not file editing). Spec goals (net worth trajectory, immigration readiness, Parth GPA ≥3.8, family time ≥10h/week, learning consistency) partially overlap but differ from current bootstrapped goals — schedule /goals conversation to formalize.
- **Description**: Create the first 5 goals through conversational creation (not manual YAML editing — UX-5).
- **Goals** (per OQ-3):
  1. Net worth / savings trajectory (outcome goal)
  2. Immigration readiness — all documents current, deadlines known ≥90 days out (milestone goal)
  3. Parth GPA ≥ 3.8 (outcome goal)
  4. Protected family time ≥ 10h/week (habit goal)
  5. Learning consistency — X hrs/month (habit goal)
- **Acceptance Criteria**:
  - All 5 goals created via conversation, not file editing
  - Each goal has metric, source, target, cadence defined
  - Auto-metric wiring confirmed for at least 3 goals
  - Goals visible in `/goals` command output
- **Effort**: 30 minutes
- **Ref**: PRD OQ-3, UX §8.1

---

#### T-1C.1.3 — Implement goal scorecard in /goals command

- [ ] **Priority**: P0
- **Description**: Ensure the `/goals` slash command generates a properly formatted scorecard with progress bars, status indicators, and grouping by category.
- **Acceptance Criteria**:
  - Scorecard matches UX §10.4 format
  - Progress bars use Unicode block characters (██████░░░░)
  - Status indicators: `→ On Track`, `⚠ At Risk`, `🔴 Behind`, `✓ Achieved`
  - Goals grouped by category (Financial, Family, Learning, Immigration, etc.)
- **Effort**: Included in Artha.md instructions (T-1A.4.5)
- **Ref**: UX §10.4, PRD §8.4

---

#### T-1C.1.4 — Implement goal conflict detection

- [ ] **Priority**: P1
- **Description**: Enable Artha to detect when two active goals are in tension (e.g., savings goal vs. travel goal) and surface the trade-off explicitly.
- **Acceptance Criteria**:
  - Artha detects resource conflicts between goals (time, money)
  - Conflict surfaced per UX §8.4 format with numbered options
  - Conflict detection runs during weekly summary generation
- **Effort**: 30 minutes (instruction refinement in goals prompt)
- **Ref**: PRD §8.9, UX §8.4

---

### Group 2: Finance Expansion

#### T-1C.2.1 — Expand finance prompt with budget tracking

- [x] **Priority**: P0
- **Description**: Enhance the finance domain prompt with predictive spend forecasting (F3.9), monthly budget tracking, and anomaly detection.
- **Dependencies**: T-1A.6.2, 4+ weeks of finance data
- **Acceptance Criteria**:
  - Monthly spending tracked against budget categories
  - Anomaly alerts fire for >20% deviation from typical
  - Predictive forecasting enabled (projects month-end spend from MTD)
- **Effort**: 30 minutes
- **Ref**: PRD F3.9

---

### Group 3: Weekly Summary & Memory

#### T-1C.3.1 — Implement weekly summary generation

- [ ] **Priority**: P0
- **Description**: Enable the weekly summary that generates on Sunday catch-up (or first post-Sunday catch-up per TS §5.2 trigger logic). Format per UX §5.
- **Acceptance Criteria**:
  - Weekly summary generates on the correct trigger (Sunday or first after Sunday 8 PM)
  - Format matches UX §5.1 template: week at a glance → kids → finance → immigration → goal scorecard → coming up → Artha observations
  - Artha Observations use extended thinking for deep cross-domain analysis
  - Summary archived to `~/OneDrive/Artha/summaries/YYYY-WXX.md`
  - Separate email from daily briefing (UX-OD-5)
- **Effort**: 30 minutes (Artha.md instruction refinement)
- **Ref**: UX §5, TS §5.2

---

#### T-1C.3.2 — Establish conversation memory in memory.md

- [x] **Priority**: P0
- **Description**: Start recording user preferences, corrections, decisions, and patterns in `~/OneDrive/Artha/state/memory.md`. This is how Artha learns and personalizes over time.
- **Content sections**:
  - Preferences (e.g., "morning briefings concise", "immigration deep-dives detailed")
  - Decisions (e.g., "dismissed Spirit Week alerts permanently")
  - Corrections (e.g., "wrong child attributed to grade alert — fixed")
  - Patterns Learned (e.g., "user checks goals first on Mondays")
- **Acceptance Criteria**:
  - `memory.md` structured with 4 sections
  - Corrections from first 6 weeks logged
  - Artha reads `memory.md` and adapts behavior based on stored preferences
- **Effort**: 15 minutes (structure creation; content accumulates organically)
- **Ref**: TS §4.8, §12.8

---

#### T-1C.3.3 — Cost validation — actual vs projected

- [ ] **Priority**: P0
- **Description**: After 6–8 weeks of operation, validate actual Claude API costs against the projected $25–35/month target. Adjust if needed.
- **Acceptance Criteria**:
  - Actual monthly cost documented
  - Multi-LLM routing savings quantified
  - If over budget: identify top cost drivers and optimize
  - health-check.md cost tracking operational
- **Effort**: 30 minutes
- **Ref**: TS §10, PRD §15.7

---

### Group 4: Boundary & Additional Prompts

#### T-1C.4.1 — Author Boundary domain prompt (FR-14)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/boundary.md created with after-hours detection (F14.1), personal time protection (F14.2), weekly metrics tracking, state/boundary.md template.
- **Content**: Work hours 8 AM – 6 PM (OQ-4), after-hours work signal detection (F14.1), personal time protection (F14.2)
- **Acceptance Criteria**: Late-night work patterns detected and surfaced in weekly summary
- **Effort**: 30 minutes
- **Ref**: PRD FR-14, OQ-4


## Phase 2A — Intelligence Workstreams (Weeks 9–12)

> **Objective**: Implement all intelligence workstreams from expert review synthesis (v1.4 workstreams A–J + v2.0 workstreams K–S + v2.1 workstreams T–X). P0 workstreams (Data Integrity Guard, Bootstrap Command) execute first to protect state file integrity and enable systematic population. P1 workstreams deepen reasoning, context efficiency, self-monitoring, coaching, and forecasting capabilities. All workstreams live within existing architecture (prompts + state files + Artha.md — no new infrastructure).
> **Success gate**: All workstreams operational, data integrity guard protecting all writes, bootstrap command populating state files, relationship graph populated, leading indicators in Goal Pulse, digest mode auto-triggering, accuracy pulse in weekly summary, tiered context ≥30% token savings, life scorecard generating weekly scores, goal sprints active, calibration questions in briefings, PII footer in briefings, /diff command operational.
> **Implements**: PRD v4.0 Phase 2A, Tech Spec v2.1, UX Spec v1.4

---

### Dependency Map — Phase 2A

```
── P0 CRITICAL PATH (execute first) ─────────────────────────

Workstream K (Data Integrity Guard) ──┐
  │                                      │
Workstream L (Bootstrap Command) ────┤  (K before L: integrity guard protects writes)
  │                                      │
── P1 INTELLIGENCE WORKSTREAMS ─────────────────────────────

Workstream A (Relationship Intelligence) ─────────────────────────┐
  │                                                             │
Workstream E (Email Pre-Processing) ─────────────────────────────┐
  │                                                             │
Workstream F (Tiered Context) ───┬─────────────────────────────────┤
  │                             │                               │
Workstream B (Leading Indicators) ─────────────────────────────┤
  │                                                             │
Workstream G (ONE THING Scoring) ──────────────────────────────┤
  │                                                             │
Workstream H (Digest Mode) ───────────────────────────────────┤
  │                                                             │
Workstream C (Decision Graphs) ────────────────────────────────┤
  │                                                             │
Workstream D (Life Scenarios) ─────────────────────────────────┤
  │                                                             │
Workstream I (Accuracy Pulse) ─────────────────────────────────┤
  │                                                             │
Workstream J (Privacy Surface) + #10 (Action Friction) ─────┘
                                                             │
── P1 SUPERCHARGE WORKSTREAMS (v2.0) ─────────────────────
                                                             │
Workstream M (Pattern of Life) ───────────────────┤
  │                                                       │
Workstream N (Signal:Noise) ──────────────────────┤
  │                                                       │
Workstream O (Briefing Compression) ─────────────────┤
  │                                                       │
Workstream P (Context Pressure) ────────────────────┤
  │                                                       │
Workstream Q (OAuth Resilience) ────────────────────┤
  │                                                       │
Workstream R (Email Volume Scaling) ─────────────────┤
  │                                                       │
Workstream S (Life Scorecard) ───────────────────────┘
  │
Compound Signals + Consequence Forecasting ─────────┘
  │
Coaching Engine + Dashboard + Scorecard ────────────┘

── P1 INTELLIGENCE AMPLIFICATION WORKSTREAMS (v2.1) ──────

Workstream T (Goal Sprint) ─────────────────────┤
  │                                                       │
Workstream U (Fastest Next Action) ──────────────┤
  │                                                       │
Workstream V (Calendar Intelligence) ────────────┤
  │                                                       │
Workstream W (Privacy Hardening) ───────────────┤
  │                                                       │
Workstream X (Observability & Coaching) ─────────┘

Parallel tracks (no ordering dependency):
  • K must complete before L (integrity guard protects bootstrap writes)
  • K, L (P0) must complete before any P1 workstream
  • A, E, F can start simultaneously (after K, L done)
  • B requires F (tiered context loads leading indicators)
  • G, H require existing briefing workflow (Phase 1A)
  • C, D require cross-domain reasoning (Phase 1C)
  • I requires action framework (Phase 1A Group 8)
  • J, #10 are standalone additions
  • M, N, O, P, Q, R, S can start in parallel (after K, L)
  • Compound Signals + Forecasting require Phase 1C
  • Coaching Engine requires Phase 1C Goal Engine
  • Dashboard + Scorecard require bootstrap + goal data
  • T requires Goal Engine (Phase 1C) *(v2.1)*
  • U requires ONE THING scoring (Workstream G) + Decision Graphs (Workstream C) *(v2.1)*
  • V requires Calendar integration (Phase 1B) *(v2.1)*
  • W requires pii_guard.sh (Phase 1A) *(v2.1)*
  • X can start in parallel with T–W (mostly briefing + prompt additions) *(v2.1)*
```

---

### Group 1: Relationship Intelligence (Workstream A)

#### T-2A.1.1 — Expand social.md state schema for relationship graph

- [x] **Priority**: P1
- **Dependencies**: T-3.1.2 (social prompt — already complete)
- **Description**: Expand `state/social.md` from basic birthday/occasion tracking to full relationship graph model per TS §4.9. Add: `tier` (close_family, close_friend, extended_family, acquaintance), `last_contact`, `contact_frequency_target`, `preferred_channel`, `cultural_protocol` array, `timezone`, `life_events` log. Add Group Health table and Communication Patterns section. Populate with initial entries from `contacts.md`.
- **Acceptance Criteria**: social.md has ≥5 relationship entries with all fields populated; tiers assigned; cultural protocol entries for ≥2 contacts
- **Effort**: 45 minutes | **Ref**: PRD FR-11 (F11.1–F11.10), TS §4.9, UX §10.8

#### T-2A.1.2 — Update social.md prompt for relationship intelligence

- [x] **Priority**: P1
- **Dependencies**: T-2A.1.1
- **Description**: Update `prompts/social.md` to support new FR-11 features: contact frequency monitoring (F11.5), communication pattern analysis (F11.6), reciprocity tracking (F11.7), cultural protocol engine (F11.8), group dynamics (F11.9), life event tracking (F11.10). Add extraction rules for relationship signals in email (e.g., event invitations, reply patterns, group threads). Add reconnect queue generation logic with threshold alerts.
- **Acceptance Criteria**: Prompt extracts relationship signals from email batch; reconnect queue generates ≥1 reconnection suggestion; reciprocity tracking shows outbound vs inbound
- **Effort**: 45 minutes | **Ref**: PRD FR-11, TS §4.9

#### T-2A.1.3 — Add Relationship Pulse to briefing template in Artha.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Relationship Pulse section confirmed in §8.1 standard briefing template. /relationships command in §5.
- **Dependencies**: T-2A.1.2
- **Description**: Add “🤝 Relationship Pulse” section to Artha.md catch-up briefing template per UX §4.1. Position: after BY DOMAIN, before GOAL PULSE. Contents: reconnect alerts, upcoming occasions/events, cultural calendar items. Add `/relationships` slash command to Artha.md command table.
- **Acceptance Criteria**: Catch-up briefing includes Relationship Pulse section; `/relationships` command shows relationship summary per UX §10.8
- **Effort**: 30 minutes | **Ref**: UX §4.1, §10.8

#### T-2A.1.4 — Add Relationship Health to weekly summary

- [x] **Priority**: P2
- **Status Note**: COMPLETE 2026-03-10 — Relationship Health section confirmed in §8.6 weekly summary template.
- **Dependencies**: T-2A.1.3
- **Description**: Add “🤝 Relationship Health” section to weekly summary template per UX §5.1. Shows: close family/friend contact health, reciprocity alerts, upcoming occasions, group activity.
- **Acceptance Criteria**: Weekly summary includes relationship health section with ≥2 data points
- **Effort**: 20 minutes | **Ref**: UX §5.1

---

### Group 2: Email Pre-Processing Enhancement (Workstream E)

#### T-2A.2.1 — Implement marketing sender suppression

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Marketing suppression logic in Step 5a with sender domain list, subject patterns, List-Unsubscribe header check, trusted domain allowlist.
- **Dependencies**: Phase 1A complete (email pipeline operational)
- **Description**: Add marketing suppression logic to Artha.md catch-up workflow step 3b per TS §7.1. Define marketing sender list (newsletters, promotions, notifications from noreply@ addresses). For marketing emails: extract subject line only, skip body. Log suppression counts to health-check.md email pre-processing stats. Include sender allowlist for legitimate notifications (e.g., school newsletters, bank alerts).
- **Acceptance Criteria**: Marketing emails produce subject-line-only extraction; non-marketing emails unaffected; suppression count visible in health-check; allowlist overrides suppression
- **Effort**: 30 minutes | **Ref**: PRD F15.20 (enhanced), TS §7.1 step 3b

#### T-2A.2.2 — Implement 1,500-token per-email cap

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — 1,500-token per-email cap in Step 5c(4) with truncation suffix and count logging.
- **Dependencies**: T-2A.2.1
- **Description**: Add per-email token budget of 1,500 tokens to Artha.md email pre-processing instructions per TS §7.1 step 3b. Emails exceeding the cap are truncated with `[truncated — original: ~N tokens]` suffix. Log truncation count to health-check.md.
- **Acceptance Criteria**: No email exceeds 1,500 tokens after pre-processing; truncated emails retain key content (subject, sender, opening); truncation count in health-check
- **Effort**: 15 minutes | **Ref**: TS §7.1 step 3b(e)

#### T-2A.2.3 — Implement batch summarization for large email volumes

- [x] **Priority**: P2
- **Status Note**: COMPLETE 2026-03-10 — Batch summarization in Step 5c(5): >50 emails or digest_mode triggers grouping by sender domain in batches of 20.
- **Dependencies**: T-2A.2.2
- **Description**: When >50 emails arrive in a single batch, group by sender pattern and produce cluster summaries for low-priority groups (e.g., “12 Amazon order updates: 8 shipped, 4 delivered”). Only Critical/Urgent emails are exempt from clustering. Add batch_summarized flag and threshold to health-check.
- **Acceptance Criteria**: Batch of >50 emails triggers grouping; cluster summary readable in briefing; individual critical emails preserved
- **Effort**: 20 minutes | **Ref**: TS §7.1 step 3b(f)

---

### Group 3: Tiered Context Architecture (Workstream F)

#### T-2A.3.1 — Add `last_activity` timestamp to all state files

- [x] **Priority**: P0
- **Dependencies**: Phase 1A complete (state files exist)
- **Description**: Add `last_activity: <ISO 8601 timestamp>` field to all state file YAML frontmatter per TS §4.1. This timestamp updates whenever a meaningful state change occurs (new data extracted, alert level change, manual update). Differs from `last_updated` which tracks any write. The `last_activity` field drives tier classification.
- **Acceptance Criteria**: All state files have `last_activity` field; field updates on meaningful changes during catch-up; field does NOT update on no-change catch-ups
- **Effort**: 30 minutes | **Ref**: TS §4.1, PRD §9.8

#### T-2A.3.2 — Implement tiered context loading in Artha.md

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — Tiered context loading in Step 4b with always/active/reference/archive tiers, override rule for new data, stale state detection.
- **Dependencies**: T-2A.3.1
- **Description**: Add step 4b (TIERED CONTEXT LOADING) to Artha.md catch-up workflow per TS §7.1. Classification logic:
  - **Always tier**: `health-check.md`, `open_items.md`, `memory.md`, `goals.md` — always load full content
  - **Active tier**: domains with new emails/data in this batch — load full content
  - **Reference tier**: domains referenced by Active tier domains but no new data — load YAML frontmatter + alerts only
  - **Archive tier**: domains with `last_activity` >30 days ago and no new data — skip entirely
  Log tier assignments and token counts to health-check.md Tiered Context Stats section.
- **Acceptance Criteria**: Catch-up loads ≤30 files at full content; inactive domains correctly classified as Archive; token savings logged; no information loss for Active domains
- **Effort**: 45 minutes | **Ref**: TS §7.1 step 4b, PRD §9.8

#### T-2A.3.3 — Validate tiered context token savings

- [ ] **Priority**: P1
- **Dependencies**: T-2A.3.2
- **Description**: Run 3 catch-ups with tiered context enabled. Compare token usage to pre-tiered baseline (from health-check.md Run History). Target: 30–40% token savings. If savings <20%, investigate tier thresholds and adjust. Document findings in health-check.md.
- **Acceptance Criteria**: ≥3 catch-up runs with tier stats; documented token savings percentage; tier thresholds tuned if needed
- **Effort**: 30 minutes (across 3 sessions) | **Ref**: PRD §9.8 (NFR-P3)

---

### Group 4: Goal Engine — Leading Indicators (Workstream B)

#### T-2A.4.1 — Add leading indicators to domain prompts

- [x] **Priority**: P1
- **Dependencies**: Phase 1C Group 1 (Goal Engine operational)
- **Description**: Add `## Leading Indicators` extraction block to each domain prompt per TS §6.1. For each goal connected to a domain, define: indicator name, how to extract it, threshold for divergence alert. Examples:
  - Finance: savings rate trend (leading for net worth goal)
  - Kids: assignment completion rate (leading for GPA goal)
  - Immigration: attorney response time (leading for readiness goal)
  Start with the 3 domains above; expand to others in Phase 2B.
- **Acceptance Criteria**: ≥3 domain prompts have leading indicators block; indicators extractable from available email/state data; threshold defined for each
- **Effort**: 30 minutes | **Ref**: PRD §8.11, TS §6.1

#### T-2A.4.2 — Add leading indicator column to Goal Pulse

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Leading indicator column added to Goal Pulse in §8.1 briefing and §8.6 weekly summary templates.
- **Dependencies**: T-2A.4.1
- **Description**: Update Goal Pulse section in Artha.md briefing template and weekly summary to include a “Leading” column per UX §4.1. Shows compact leading indicator value alongside status and trend. Add divergence alert logic: if lagging says “on track” but leading says “warning,” generate an alert explanation.
- **Acceptance Criteria**: Goal Pulse shows leading indicator column; divergence between lagging and leading generates explanatory alert in briefing
- **Effort**: 20 minutes | **Ref**: UX §4.1, §8.5

#### T-2A.4.3 — Add `/goals leading` command

- [x] **Priority**: P2
- **Status Note**: COMPLETE 2026-03-10 — /goals leading command added to §5.
- **Dependencies**: T-2A.4.2
- **Description**: Add `/goals leading` slash command to Artha.md per UX §8.5. Shows detailed leading indicator view: lagging-first, then leading for each goal, with divergence alerts at bottom.
- **Acceptance Criteria**: `/goals leading` produces output matching UX §8.5 template; divergence alerts appear when present
- **Effort**: 15 minutes | **Ref**: UX §8.5

---

### Group 5: ONE THING Scoring (Workstream G) & Digest Mode (Workstream H)

#### T-2A.5.1 — Implement URGENCY×IMPACT×AGENCY scoring

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — U×I×A scoring in Step 8a with 0-3 scale per factor, tie-breaking rule, mini scoring display in ONE THING.
- **Dependencies**: Phase 1A complete (cross-domain reasoning in catch-up)
- **Description**: Update Artha.md cross-domain reasoning step (step 6) to use URGENCY×IMPACT×AGENCY scoring per TS §7.1 step 6. Each factor scored 0–5:
  - URGENCY: time pressure (5 = due today, 0 = no deadline)
  - IMPACT: consequence of inaction (5 = legal/financial loss, 0 = minor convenience)
  - AGENCY: can the family act on it now? (5 = clear action, 0 = waiting on others)
  Show scoring chain in ONE THING section: “Chosen because: URGENCY X × IMPACT Y × AGENCY Z = score”
- **Acceptance Criteria**: ONE THING shows scoring chain; highest-scored item wins; scoring feels non-arbitrary to user
- **Effort**: 30 minutes | **Ref**: PRD F15.11, TS §7.1 step 6, UX §4.1

#### T-2A.5.2 — Implement digest mode detection and briefing

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Digest mode detection in Step 2b: >48h gap triggers digest format, <4h gap triggers flash format.
- **Dependencies**: T-2A.5.1
- **Description**: Add step 1b (DIGEST MODE CHECK) to Artha.md catch-up workflow per TS §7.1. When >48 hours since last catch-up: set digest_mode flag, use §5.1.1 digest briefing format (temporal ordering, priority-tier filtering, consolidated actions). ONE THING picks from entire gap period. Footer includes cadence nudge.
- **Acceptance Criteria**: >48hr gap triggers digest format automatically; items grouped by day; FYI items counted not listed; standard format used for <48hr gaps
- **Effort**: 30 minutes | **Ref**: PRD F15.26, TS §5.1.1, §7.1 step 1b, UX §4.5

#### T-2A.5.3 — Add `--standard` override for digest mode

- [x] **Priority**: P2
- **Status Note**: COMPLETE 2026-03-10 — /catch-up standard override added to Step 2b routing logic.
- **Dependencies**: T-2A.5.2
- **Description**: Add `/catch-up --standard` flag to Artha.md that overrides digest mode and produces standard briefing format even after a >48hr gap. Preference stored in memory.md if user consistently overrides.
- **Acceptance Criteria**: `--standard` flag produces standard briefing after gap; override count tracked in memory.md
- **Effort**: 10 minutes | **Ref**: UX §UX-OD-11

---

### Group 6: Decision Graphs (Workstream C) & Life Scenarios (Workstream D)

#### T-2A.6.1 — Create state/decisions.md schema and bootstrap

- [x] **Priority**: P1
- **Dependencies**: Phase 1C complete (cross-domain reasoning operational)
- **Description**: Create `state/decisions.md` per TS §4.10. Schema: decision ID (DEC-NNN), date, summary, context (why this decision was made), domains_affected, alternatives_considered, review_trigger, status (active|resolved). Populate with 1–2 initial decisions from known context (e.g., refinance timing, school selection).
- **Acceptance Criteria**: decisions.md exists with valid schema; ≥1 active decision populated; review_trigger defined
- **Effort**: 20 minutes | **Ref**: PRD F15.24, TS §4.10

#### T-2A.6.2 — Add decision detection to cross-domain reasoning

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Decision detection added to Step 8d: auto-create DEC-NNN entry when trade-offs across 2+ domains detected, user confirmation required.
- **Dependencies**: T-2A.6.1
- **Description**: Update Artha.md step 6 (Cross-Domain Reasoning) to check if a decision point emerges during reasoning per TS §7.1 step 6. When detected: auto-create entry in decisions.md, surface “New decision logged: DEC-NNN” in briefing, offer `/decisions DEC-NNN` for detail.
- **Acceptance Criteria**: Cross-domain reasoning creates decision entries when decision points detected; entry appears in decisions.md; surfaced in briefing
- **Effort**: 20 minutes | **Ref**: TS §7.1 step 6

#### T-2A.6.3 — Add `/decisions` slash command

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — /decisions command in §5 with list and detail views.
- **Dependencies**: T-2A.6.2
- **Description**: Add `/decisions` command to Artha.md per UX §10.6. Shows active decisions with status, domains affected, review trigger. `/decisions DEC-NNN` shows full alternatives analysis. Add to slash command table.
- **Acceptance Criteria**: `/decisions` produces output matching UX §10.6; detail view shows alternatives and review trigger
- **Effort**: 15 minutes | **Ref**: UX §10.6

#### T-2A.6.4 — Create state/scenarios.md schema and templates

- [x] **Priority**: P1
- **Dependencies**: Phase 1C complete
- **Description**: Create `state/scenarios.md` per TS §4.11. Schema: scenario ID (SCN-NNN), trigger, question, impacts (per domain with branches), last_evaluated, status. Include Templates section with 3–4 pre-built templates: refinance_analysis, college_cost, immigration_timeline, job_change.
- **Acceptance Criteria**: scenarios.md exists with valid schema; ≥1 active scenario; ≥3 templates defined
- **Effort**: 25 minutes | **Ref**: PRD F15.25, TS §4.11

#### T-2A.6.5 — Add scenario trigger to cross-domain reasoning

- [x] **Priority**: P2
- **Status Note**: COMPLETE 2026-03-10 — Scenario trigger detection in Step 8e: checks scenarios.md for status: watching, promotes to active on trigger match.
- **Dependencies**: T-2A.6.4
- **Description**: Update Artha.md step 6 to check if incoming data triggers a scenario template per TS §7.1 step 6. When a high-stakes goal has new data, check if a template applies and offer: “New data on immigration timeline. Run what-if analysis? [yes/no]”
- **Acceptance Criteria**: Scenario suggestions appear when relevant data arrives; user can accept or dismiss; accepted scenarios populate scenarios.md
- **Effort**: 20 minutes | **Ref**: TS §7.1 step 6

#### T-2A.6.6 — Add `/scenarios` slash command

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — /scenarios command in §5 with list, detail, and interactive editing views.
- **Dependencies**: T-2A.6.5
- **Description**: Add `/scenarios` command to Artha.md per UX §10.7. List view shows active scenarios and templates. Detail view (`/scenarios SCN-NNN`) shows side-by-side impact comparison with recommendation. Support interactive editing: user can modify parameters in conversation and Artha re-runs analysis.
- **Acceptance Criteria**: `/scenarios` produces output matching UX §10.7; detail view shows side-by-side comparison; interactive editing works
- **Effort**: 20 minutes | **Ref**: UX §10.7

---

### Group 7: Accuracy Pulse (Workstream I) & Action Friction (#10)

#### T-2A.7.1 — Add accuracy tracking fields to health-check.md

- [x] **Priority**: P1
- **Dependencies**: Phase 1A Group 8 (action framework operational)
- **Description**: Add “Accuracy Pulse Data” section to `state/health-check.md` per TS §4.5. Track per catch-up: actions_proposed, actions_accepted, actions_declined, actions_deferred, corrections_logged, alerts_dismissed. Track rolling 7-day aggregate with acceptance_rate and per-domain accuracy.
- **Acceptance Criteria**: health-check.md has Accuracy Pulse section; fields update after each catch-up; rolling 7-day aggregate calculated
- **Effort**: 25 minutes | **Ref**: TS §4.5, PRD F15.27

#### T-2A.7.2 — Add Accuracy Pulse to weekly summary

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Accuracy Pulse section in §8.6 weekly summary template.
- **Dependencies**: T-2A.7.1
- **Description**: Add “📊 Accuracy Pulse” section to weekly summary template in Artha.md per UX §5.1. Shows: actions proposed/accepted/declined/deferred with acceptance rate, corrections count, alerts dismissed, per-domain accuracy. Include Notable line for significant accuracy events (e.g., “Finance accuracy dropped due to PSE amount parsing”).
- **Acceptance Criteria**: Weekly summary includes Accuracy Pulse; data sourced from health-check.md; Notable line generated for significant events
- **Effort**: 20 minutes | **Ref**: UX §5.1, PRD F15.27

#### T-2A.7.3 — Add “Anything I got wrong?” prompt to catch-up close

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Calibration prompt in Step 19 with corrections logging to memory.md and health-check.md.
- **Dependencies**: T-2A.7.1
- **Description**: Add “Anything I got wrong today?” question at end of each catch-up in Artha.md. User corrections logged to health-check.md Accuracy Pulse section with domain tag. Corrections feed into weekly accuracy reporting and domain prompt improvement.
- **Acceptance Criteria**: Question appears at catch-up close; user corrections update health-check.md; corrections visible in weekly Accuracy Pulse
- **Effort**: 15 minutes | **Ref**: TS §12.8, PRD F15.27

#### T-2A.7.4 — Add friction field to action proposal format

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Friction field in §9 Action Proposal format with green/yellow/red visual indicators and classification table.
- **Dependencies**: Phase 1A Group 8 (action framework)
- **Description**: Add `Friction: [low | standard | high]` field to action proposal display in Artha.md per TS §7.4.1. Classification:
  - **Low**: calendar adds, email archiving, visual generation → batch-approvable
  - **Standard**: email composition, WhatsApp greetings, reminders → individual review
  - **High**: financial actions, immigration correspondence, actions affecting others → cannot be pre-approved even at Trust Level 2
  Visual indicators: 🟢 Low, 🟠 Standard, 🔴 High.
- **Acceptance Criteria**: All action proposals show friction level; low-friction batch approval works; high-friction actions require individual confirmation regardless of trust level
- **Effort**: 20 minutes | **Ref**: TS §7.4.1, UX §9.1

---

### Group 8: Privacy Surface (Workstream J) & Integration

#### T-2A.8.1 — Add Privacy Surface section to Artha.md

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — §4.3 Privacy Surface enhanced with data flow table (7 data types × destination × PII filtering × retention), Anthropic API retention clarified.
- **Dependencies**: None (standalone)
- **Description**: Add §4.3 Privacy Surface section to Artha.md per PRD §12.6 and TS §8.8. Content: “All data processed during catch-up sessions is sent to the Anthropic Claude API for ephemeral processing. Anthropic does not retain API inputs/outputs for model training. External CLIs (Gemini, Copilot) receive sanitized queries only (PII stripped by safe_cli.sh). See tech spec §8.8 for full privacy surface documentation.” Include data flow table from TS §8.8.
- **Acceptance Criteria**: Artha.md §4.3 exists with accurate privacy surface disclosure; cross-references tech spec; no false claims about data retention
- **Effort**: 15 minutes | **Ref**: PRD §12.6, TS §8.8

#### T-2A.8.2 — Add email pre-processing stats to health-check.md

- [x] **Priority**: P1
- **Dependencies**: T-2A.2.1 (marketing suppression)
- **Description**: Add “Email Pre-Processing Stats” section to health-check.md per TS §4.5. Track per catch-up: emails_received, marketing_suppressed, avg_tokens_per_email, truncated_emails, batch_summarized flag.
- **Acceptance Criteria**: Stats section populates after catch-up; marketing suppression count visible; truncation count visible
- **Effort**: 15 minutes | **Ref**: TS §4.5

#### T-2A.8.3 — Add tiered context stats to health-check.md

- [x] **Priority**: P1
- **Dependencies**: T-2A.3.2 (tiered context loading)
- **Description**: Add “Tiered Context Stats” section to health-check.md per TS §4.5. Track: always_tier files, active_tier files, reference_tier files, archive_tier files, tokens_loaded (actual vs. all-loaded comparison for savings percentage).
- **Acceptance Criteria**: Tier stats populate after catch-up; token savings percentage calculated and logged; cumulative average tracked
- **Effort**: 15 minutes | **Ref**: TS §4.5

#### T-2A.8.4 — Integration test: Full catch-up with all Phase 2A features

- [ ] **Priority**: P0
- **Dependencies**: All T-2A.*.* tasks complete
- **Description**: Run a complete catch-up with all Phase 2A features enabled. Verify:
  1. Tiered context loads correctly (check tier assignments)
  2. Marketing emails suppressed, token cap enforced
  3. Leading indicators appear in Goal Pulse
  4. Relationship Pulse section present
  5. ONE THING shows scoring chain
  6. Digest mode triggers if >48hr gap (test with stale timestamp)
  7. Action proposals show friction level
  8. Accuracy Pulse data captured in health-check
  9. Privacy Surface section in Artha.md accurate
  10. `/decisions`, `/scenarios`, `/relationships` commands functional
- **Acceptance Criteria**: All 10 checks pass; no regression in existing functionality; briefing quality maintained or improved
- **Effort**: 45 minutes | **Ref**: All Phase 2A specs

---

### Group 9: Data Integrity Guard (Workstream K) — P0

> The Data Integrity Guard is P0 because it prevents the state data loss that was observed during initial deployment (immigration.md, finance.md never populated; vault.sh overwrite vulnerability). Must be implemented before any other Phase 2A work. (PRD F15.28, TS §8.5.1, UX §14.1)

#### T-2A.9.1 — Implement pre-decrypt backup in vault.sh (Layer 1)

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — vault.sh do_decrypt() enhanced: pre-decrypt .bak backup, post-decrypt validation (non-empty + YAML frontmatter check), auto-restore on failure, bootstrap detection logging.
- **Dependencies**: Phase 1A complete (vault.sh operational)
- **Description**: Modify `vault.sh decrypt` to create a `.md.bak` backup before overwriting the `.md` file with decrypted content per TS §8.5.1 Layer 1. Before decrypt: `cp state/FILE.md state/FILE.md.bak`. After successful decrypt: verify the output is valid (non-empty, starts with `---`). If decrypt produces invalid output, restore from `.bak` and abort. Add bootstrap detection: if `.md` file has `updated_by: bootstrap` in frontmatter, log a warning to `state/audit.md`.
- **Acceptance Criteria**: `.md.bak` created before every decrypt; corrupt decrypt auto-restores from backup; bootstrap-state files logged; backup removed after successful encrypt
- **Security Notes**: Backup file contains plaintext — must be cleaned up in encrypt step. Never leave `.bak` files on disk after session ends.
- **Effort**: 1 hour | **Ref**: PRD F15.28, TS §8.5.1 Layer 1

#### T-2A.9.2 — Implement post-write YAML/size verification (Layer 2)

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — Artha.md Step 8c post-write verification: file existence, size >100 bytes, YAML frontmatter, domain field match, last_updated timestamp validation. Failed files excluded from encryption.
- **Dependencies**: T-2A.9.1
- **Description**: Add post-write verification to Artha.md catch-up workflow Step 8 per TS §8.5.1 Layer 2. After every state file write, verify: (1) file starts with `---` (valid YAML frontmatter), (2) file size > 100 bytes (not truncated/empty), (3) `domain:` header field present and matches expected domain. If any check fails, log to `state/audit.md` with details and do NOT encrypt the corrupted file.
- **Acceptance Criteria**: Every state file write is followed by verification; failed verification prevents encryption; verification failures logged to audit.md
- **Effort**: 1 hour | **Ref**: TS §8.5.1 Layer 2

#### T-2A.9.3 — Implement net-negative write guard (Layer 3)

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — Artha.md Step 8b net-negative write guard: >20% field loss triggers user confirmation dialog (show diff / write anyway / skip domain). Bootstrap files exempt. Events logged to audit.md.
- **Dependencies**: T-2A.9.2
- **Description**: Add Step 8b (NET-NEGATIVE WRITE GUARD) to Artha.md catch-up workflow per TS §7.1. Before writing each state file: compare field count in proposed write vs. current file. If proposed write removes >20% of data fields, halt the write and surface a user confirmation dialog per UX §14.1 (net-negative write warning). Options: `[show full diff]`, `[write anyway]`, `[skip domain this session]`. Log all net-negative events to `state/audit.md`.
- **Acceptance Criteria**: >20% field loss triggers user confirmation; user can approve, skip, or view diff; skip preserves existing data; all events logged
- **Effort**: 2 hours | **Ref**: TS §7.1 Step 8b, TS §8.5.1 Layer 3, UX §14.1

#### T-2A.9.4 — Backup retention and audit trail

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — vault.sh do_encrypt() cleans up .bak files; do_health() detects orphaned .bak files; audit trail format in health-check.md integrity_events section.
- **Dependencies**: T-2A.9.1
- **Description**: Add cleanup logic: `.md.bak` files auto-removed after successful encrypt. Add audit trail format to `state/audit.md` per TS §8.5.1: timestamp, layer triggered, file affected, action taken, outcome. Ensure `.bak` files are included in LaunchAgent watchdog cleanup (never left in plaintext after session).
- **Acceptance Criteria**: No `.bak` files survive past encrypt; audit trail populated for all integrity events; watchdog catches orphaned `.bak` files
- **Effort**: 30 minutes | **Ref**: TS §8.5.1

#### T-2A.9.5 — Test data integrity guard with simulated scenarios

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — 7/7 tests passing: round-trip, backup creation, corrupt .age detection, bootstrap detection, empty file detection, missing YAML detection, backup cleanup.
- **Dependencies**: T-2A.9.3, T-2A.9.4
- **Description**: Create test scenarios: (1) simulate corrupt `.age` file (produces empty/garbage on decrypt), (2) simulate catch-up that would remove >20% of fields, (3) simulate crash mid-session leaving `.bak` files, (4) simulate bootstrap-state file during catch-up. Verify all three layers respond correctly. Log results.
- **Acceptance Criteria**: All 4 scenarios handled correctly; no data loss; audit trail matches expected output
- **Effort**: 1 hour | **Ref**: All TS §8.5.1

---

### Group 10: Bootstrap Command (Workstream L) — P0

> Bootstrap Command enables systematic population of state files that are currently empty (bootstrap placeholders). Depends on Data Integrity Guard (Group 9) to protect writes. (PRD F15.33, TS §7.5, UX §10.9, §15.1)

#### T-2A.10.1 — Add `/bootstrap` slash command to Artha.md

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — /bootstrap and /bootstrap <domain> added to Artha.md §5 with guided interview workflow, population status table, progress tracking, and post-write verification.
- **Dependencies**: T-2A.9.3 (integrity guard protects bootstrap writes)
- **Description**: Add `/bootstrap` and `/bootstrap <domain>` to Artha.md slash command table per TS §3.6.1. Route to guided interview workflow per TS §7.5. Domain selection: if no domain specified, show all domains with population status (populated/empty/partial). If domain specified, jump directly to that domain's interview.
- **Acceptance Criteria**: `/bootstrap` shows domain selector; `/bootstrap finance` jumps to finance interview; command registered in slash command table
- **Effort**: 30 minutes | **Ref**: TS §3.6.1, §7.5, UX §10.9

#### T-2A.10.2 — Implement domain interview question generation

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — Domain interview question generation specified in /bootstrap command: schema-derived questions, one at a time, input validation, redaction reminders, confirmation before write, resumable progress.
- **Dependencies**: T-2A.10.1
- **Description**: For each domain, derive interview questions from the state file schema (TS §4.2–§4.11). Questions asked one at a time per TS §7.5 design rules. Include: validation (e.g., dates must be ISO 8601, amounts must be numeric), redaction reminders for sensitive fields (e.g., "I'll store this encrypted — enter your case number"), confirmation before writing ("Here's what I'll save. Correct? [yes/edit/skip]"). Progress saved per domain — user can exit and resume.
- **Acceptance Criteria**: Questions derive from schema (not hardcoded); one question at a time; validation catches format errors; user confirms before write; progress resumable
- **Effort**: 2 hours | **Ref**: TS §7.5, UX §10.9

#### T-2A.10.3 — Implement answer writing with integrity verification

- [x] **Priority**: P0
- **Status Note**: COMPLETE 2026-03-10 — Answer writing with Layer 2 verification integrated into /bootstrap workflow: updated_by changed from bootstrap to user_interview, encrypted domain handling specified.
- **Dependencies**: T-2A.10.2, T-2A.9.2 (post-write verification)
- **Description**: After user confirms data for a domain: write state file, run Layer 2 post-write verification (T-2A.9.2), update `updated_by` from `bootstrap` to `user_interview`, update `last_updated` timestamp. Show completion summary per UX §10.9 (field counts, verification status). If writing to encrypted domain, handle decrypt → write → verify → encrypt cycle.
- **Acceptance Criteria**: State file updated with user-provided data; `updated_by` field changed from `bootstrap`; post-write verification passes; encrypted files handled correctly
- **Effort**: 2 hours | **Ref**: TS §7.5, UX §10.9

#### T-2A.10.4 — Add bootstrap detection to catch-up workflow

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Bootstrap detection added to catch-up Step 4c: checks updated_by: bootstrap in frontmatter, surfaces warning with /bootstrap suggestion, ≥3 stale domains triggers bulk suggestion.
- **Dependencies**: T-2A.10.1
- **Description**: Add bootstrap detection to catch-up Step 4 per TS §7.1. When processing state files, check for `updated_by: bootstrap` in frontmatter. If found, surface warning per UX §14.1 (bootstrap state detection): "⚠ UNPOPULATED STATE FILE — [domain].md. This file still contains bootstrap placeholder data. For complete data: Run /bootstrap [domain] after this catch-up." Proceed with best-effort email extraction.
- **Acceptance Criteria**: Bootstrap-state files generate warning in catch-up briefing; warning includes `/bootstrap` suggestion; catch-up continues with best-effort extraction
- **Effort**: 30 minutes | **Ref**: TS §7.1, UX §14.1

#### T-2A.10.5 — Create state/dashboard.md template

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — state/dashboard.md created with Life Pulse (17 domains), Active Alerts (U×I×A ranked), Open Items Summary, Life Scorecard (7 dimensions), System Health sections.
- **Dependencies**: T-2A.10.1
- **Description**: Create `state/dashboard.md` per TS §4.12. Include: Life Pulse table (17 domains with status/alert/last-updated), Active Alerts ranked by URGENCY×IMPACT×AGENCY, Open Items Summary, Life Scorecard placeholder (7 dimensions), System Health section. Update rules: rebuilt each catch-up, not persisted across sessions.
- **Acceptance Criteria**: dashboard.md created with all sections from TS §4.12; populated during catch-up; `/dashboard` command shows formatted output per UX §10.10
- **Effort**: 30 minutes | **Ref**: TS §4.12, UX §10.10

---

### Group 11: Intelligence Engines — Compound Signals, Forecasting, Pre-Decision

> Cross-domain intelligence engines that generate non-obvious insights and proactive alerts. These are prompt-only implementations — no new scripts or infrastructure. (PRD F15.30, F15.37, F15.38, TS §7.6, §7.10, §7.11)

#### T-2A.11.1 — Implement compound signal detection in Artha.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Compound signal detection added to Step 8f with 6 correlation rules, ephemeral (not persisted), max 3 per briefing.
- **Dependencies**: Phase 1C complete (cross-domain reasoning in catch-up)
- **Description**: Add compound signal detection to catch-up Step 6 (Cross-Domain Reasoning) per TS §7.6. Implement 6 correlation rules:
  1. Travel booking + credit card with travel benefits → benefit reminder
  2. Immigration deadline + no calendar block → calendar suggestion
  3. School event + work conflict → scheduling alert
  4. Bill due + seasonal spending pattern → budget warning
  5. Health appointment + insurance deductible status → cost alert
  6. Goal deadline approaching + behavioral trend declining → intervention
  Compound signals are ephemeral (TD-25) — surfaced in briefing only, not persisted to state files.
- **Acceptance Criteria**: ≥2 correlation rules fire on realistic data; compound signals appear in briefing with clear reasoning chain; no false-positive spam
- **Effort**: 3 hours | **Ref**: PRD F15.30, TS §7.6

#### T-2A.11.2 — Implement consequence forecasting engine

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Consequence forecasting in Step 8g: IF YOU DON'T chains for Critical/Urgent items, confidence >70% gate, max 3 per briefing.
- **Dependencies**: Phase 1C complete (cross-domain reasoning)
- **Description**: Add consequence forecasting to catch-up Step 6 per TS §7.10. For each Critical/Urgent alert, generate "IF YOU DON'T" consequence chain: inaction → timeline → first-order consequence → cascade effect. Only surface when confidence >70%. Display per UX §4.7: standard format (2–3 items with impact chains), flash format (1 item, max 2 lines). Maximum 3 consequence forecasts per briefing.
- **Acceptance Criteria**: Consequence forecasts appear for critical/urgent items; reasoning chain is logical and specific; ≤3 per briefing; confidence gate prevents speculative alerts
- **Effort**: 2 hours | **Ref**: PRD F15.37, TS §7.10, UX §4.7

#### T-2A.11.3 — Implement pre-decision intelligence packets

- [ ] **Priority**: P1
- **Dependencies**: T-2A.6.1 (decisions.md exists)
- **Description**: Add `/research <topic>` command or integrate with `/decisions` per TS §7.11. When user faces a decision, generate structured packet: Context (what triggered this), Key Data Points (from state files), External Research (via Gemini CLI), Options Analysis (pros/cons/risks), Recommendation, What Artha Doesn't Know. Output uses safe_cli.sh for external queries. Packet saved to decisions.md for reference.
- **Acceptance Criteria**: Research packet covers all 6 sections; external research uses safe_cli.sh (PII-stripped); packet saved to decisions.md; user finds output useful for real decisions
- **Effort**: 3 hours | **Ref**: PRD F15.38, TS §7.11

---

### Group 12: Pattern of Life & Coaching Engine

> Behavioral intelligence — learning the user's patterns, detecting anomalies, and providing goal-oriented coaching. (PRD F15.34, F13.14–F13.16, TS §7.7, §4.7, UX §8.6)

#### T-2A.12.1 — Add behavioral baselines to memory.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Behavioral Baselines section added to memory.md with 6 metrics, provisional flag, 30-day moving averages.
- **Dependencies**: Phase 1A complete (memory.md exists, ≥10 catch-ups completed)
- **Description**: Add "Behavioral Baselines" section to `state/memory.md` per TS §4.7. Track 30-day moving averages for: catch-up frequency, typical email volume per catch-up, communication response patterns, spending patterns, calendar density. Collect data passively during each catch-up — no user prompting. Mark baselines as provisional until ≥10 catch-ups (TD-21).
- **Acceptance Criteria**: Behavioral Baselines section exists in memory.md; baselines update after each catch-up; provisional flag shows until 10 catch-ups reached
- **Effort**: 30 minutes | **Ref**: TS §4.7, §7.7

#### T-2A.12.2 — Implement pattern anomaly detection

- [ ] **Priority**: P1
- **Dependencies**: T-2A.12.1 (baselines data collected)
- **Description**: Add anomaly detection to catch-up Step 5 per TS §7.7. Detect 5 anomaly types: (1) unusual gap in catch-up frequency, (2) email volume spike (>2x baseline), (3) spending deviation (>30% from 30-day avg), (4) communication drop (no contact with close family/friends >2x typical gap), (5) routine disruption (missed regular events — gym, school pickup, etc.). Only activate after ≥10 catch-ups to avoid false positives.
- **Acceptance Criteria**: Anomaly detection fires on at least 1 type with realistic data; anomalies surfaced in briefing with clear reasoning; no anomaly alerts before 10 catch-ups
- **Effort**: 2 hours | **Ref**: TS §7.7

#### T-2A.12.3 — Add coaching preferences to memory.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Coaching Preferences section added to memory.md with style, accountability, frequency, obstacle anticipation, celebration threshold settings.
- **Dependencies**: T-2A.12.1
- **Description**: Add "Coaching Preferences" section to `state/memory.md` per TS §4.7. Fields: `coaching_style` (question/direct/cheerleader), `accountability_level` (light/standard/strict), `goal_check_frequency` (daily/weekly), `preferred_nudge_format`, `obstacle_anticipation` (on/off), `celebration_threshold` (milestone/any-progress), `example_nudges` array. Defaults: question style, standard accountability, weekly goal checks.
- **Acceptance Criteria**: Coaching Preferences section exists; defaults populated; user can modify via conversation
- **Effort**: 20 minutes | **Ref**: TS §4.7

#### T-2A.12.4 — Implement coaching engine in Artha.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Coaching engine added to Step 19b: accountability nudge (question style), obstacle anticipation, celebration; max 1 per catch-up; dismissal cooldown.
- **Dependencies**: T-2A.12.3, Phase 1C Group 1 (Goal Engine)
- **Description**: Add coaching engine to catch-up close per UX §8.6. Three components:
  1. **Accountability nudge**: Question-style by default (e.g., "You mentioned wanting to exercise 3x/week. This week you logged 1. What got in the way?"). Max 1 per catch-up, after briefing.
  2. **Obstacle anticipation** (opt-in): When a goal has an upcoming milestone and behavioral patterns suggest risk, surface proactive alert (e.g., "Your savings goal target is March 31. Based on current deposit pattern, you'll be ~$400 short. Increase this month's transfer?").
  3. **Celebration**: When a milestone is hit, acknowledge it (e.g., "🎯 Parth's GPA hit 3.85 — above your 3.8 target. Nice work."). Milestone-level celebrations only (not every minor progress).
  All coaching is configurable via Coaching Preferences. Dismissal available for each item.
- **Acceptance Criteria**: Coaching nudge appears at catch-up close (max 1); obstacle anticipation triggers on realistic risk; celebrations fire on milestone achievement; all respects preference settings
- **Effort**: 3 hours | **Ref**: PRD F13.14–F13.16, TS §4.7, UX §8.6

---

### Group 13: Briefing Compression & Session Quick-Start

> Adaptive briefing formats that match the user's available time and gap duration. (PRD F15.39, F15.40, TS §7.12, §7.13, §5.1.2, UX §4.6)

#### T-2A.13.1 — Implement flash briefing format in Artha.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Flash briefing format added as §8.8: max 8 lines, ≤30s read time, Critical/Urgent only, 'say more' footer.
- **Dependencies**: Phase 1A complete (standard briefing operational)
- **Description**: Add flash briefing format per TS §5.1.2 and UX §4.6. Maximum 8 lines, ≤30 seconds reading time. Content: since-last line, critical/urgent alerts only (≤3), single calendar line, one consequence forecast line (if applicable), "say 'more' for full briefing" footer. No domain sections, no greetings, no goals in flash mode. Design rules per UX §4.6.
- **Acceptance Criteria**: Flash briefing ≤8 lines; reading time ≤30 seconds; contains only critical/urgent items; "more" expands to standard; no information loss for actionable items
- **Effort**: 1 hour | **Ref**: TS §5.1.2, UX §4.6

#### T-2A.13.2 — Implement deep briefing format

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Deep briefing format added as §8.9: extends standard with trend analysis, scenario implications, coaching, compound signals, consequence forecasts.
- **Dependencies**: Phase 1A complete (standard briefing operational)
- **Description**: Add deep briefing format per TS §7.13. Extends standard briefing with: cross-domain trend analysis, scenario implications, extended coaching section, compound signal analysis, full consequence forecasts. Intended for Sunday catch-ups or user-requested deep dives.
- **Acceptance Criteria**: Deep briefing includes all standard content plus analysis sections; user can trigger with `/catch-up deep`; content is genuinely deeper (not just longer)
- **Effort**: 1.5 hours | **Ref**: TS §7.13

#### T-2A.13.3 — Implement session quick-start routing

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Session quick-start routing in Step 2b: <4h→flash, 4-48h→standard, >48h→digest, /catch-up flash|deep|standard overrides.
- **Dependencies**: T-2A.13.1 (flash format exists), T-2A.5.2 (digest mode exists)
- **Description**: Add session routing logic to catch-up Step 1 per TS §7.12. Routing rules:
  - <4h gap → flash briefing (auto-select, user can override)
  - 4–48h gap → standard briefing
  - >48h gap → digest mode (existing T-2A.5.2)
  - Bootstrap state files detected → surface `/bootstrap` suggestion regardless of gap
  - Stale state (any domain >7 days without update) → alert in briefing header
  Add `/catch-up flash` and `/catch-up deep` explicit commands per UX §10.1.
- **Acceptance Criteria**: Routing selects correct format based on gap; user can override with explicit commands; stale state detection works; bootstrap detection works
- **Effort**: 1 hour | **Ref**: TS §7.12, UX §10.1

#### T-2A.13.4 — Implement stale state detection

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Stale state detection in Step 4b: >7 day threshold, ≥3 stale domains triggers /bootstrap suggestion.
- **Dependencies**: T-2A.3.1 (last_activity timestamp exists)
- **Description**: Add stale state detection per TS §7.12. When any domain's `last_activity` is >7 days ago and no new data arrives, surface alert in briefing header: "⚠ [domain] has no activity in [N] days — data may be outdated." Check during catch-up Step 4. If ≥3 domains are stale, suggest `/bootstrap` for state refresh.
- **Acceptance Criteria**: Stale domains surfaced in briefing header; >7 day threshold triggers alert; ≥3 stale domains triggers bootstrap suggestion
- **Effort**: 30 minutes | **Ref**: PRD F15.36, TS §7.12

---

### Group 14: Signal:Noise Optimization & Email Volume Scaling

> System self-tuning — tracking information quality ratios and scaling processing strategies for email volume. (PRD F15.35, F15.43, TS §7.8, §4.5 Signal:Noise)

#### T-2A.14.1 — Add signal:noise tracking to health-check.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Signal:Noise Tracking section added to health-check.md with per-catch-up metrics, 30-day rolling average, <30% alert threshold.
- **Dependencies**: Phase 1A complete (health-check.md exists)
- **Description**: Add "Signal:Noise Tracking" section to `state/health-check.md` per TS §4.5. Track per catch-up: `total_items` (everything extracted), `actionable_items` (user needs to act), `informational_items` (awareness only), `suppressed_items` (filtered noise), `signal_ratio` (actionable/total). Track 30-day rolling average. If signal ratio drops below 30%, flag for prompt tuning.
- **Acceptance Criteria**: Signal:Noise section populated after each catch-up; 30-day rolling average calculated; <30% ratio generates prompt tuning alert
- **Effort**: 30 minutes | **Ref**: TS §4.5

#### T-2A.14.2 — Implement email volume tier detection and scaling

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Email volume tier detection in Step 4d: standard/medium/high/extreme tiers with progressive processing strategies.
- **Dependencies**: T-2A.2.1 (email pre-processing)
- **Description**: Add email volume tier detection to catch-up Step 3 per TS §7.8. Tiers:
  - **Standard** (≤50 emails): Process all normally
  - **Medium** (51–200): Aggressive marketing suppression + 1,000-token cap (reduced from 1,500)
  - **High** (201–500): Two-pass processing — P0 domains first (immigration, finance, health), then remaining with summary extraction
  - **Extreme** (500+): Three-pass — P0 full, P1 summary, P2 count-only
  Log tier triggered to health-check.md.
- **Acceptance Criteria**: Tier selected based on email count; processing strategy scales appropriately; high/extreme tiers prioritize critical domains; tier logged
- **Effort**: 2 hours | **Ref**: TS §7.8

#### T-2A.14.3 — Add noise source analysis to weekly summary

- [x] **Priority**: P2
- **Status Note**: COMPLETE 2026-03-10 — Noise source analysis added to §8.6 weekly summary: top 3 noise sources with unsubscribe suggestions.
- **Dependencies**: T-2A.14.1
- **Description**: Add noise source breakdown to weekly summary. Show top 3 noise sources (senders/domains generating most suppressed items) with suggestion: "Consider unsubscribing from [sender] — 23 emails this week, 0 actionable." Helps user proactively reduce noise.
- **Acceptance Criteria**: Weekly summary includes noise source ranking; actionable unsubscribe suggestion included; only suggests for senders with >5 suppressed emails/week
- **Effort**: 30 minutes | **Ref**: TS §4.5

---

### Group 15: System Health & Resilience

> Context window management, OAuth resilience, and Life Dashboard + Scorecard. (PRD F15.29, F15.41, F15.42, F15.44, TS §7.14, §7.15, §7.9, §4.12)

#### T-2A.15.1 — Add context window pressure tracking to health-check.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Context Window Pressure section added to health-check.md with per-step breakdown, pressure levels, mitigation tracking.
- **Dependencies**: Phase 1A complete (health-check.md exists)
- **Description**: Add "Context Window Pressure" section to `state/health-check.md` per TS §4.5. Track per catch-up: estimated tokens used at each workflow step (pre-flight, email fetch, state load, domain analysis, cross-domain, briefing synthesis, action proposals, write-back), peak usage, pressure level (green <50%, yellow 50–70%, red 70–85%, critical >85%). Use approximate heuristic for token estimation (TD-23).
- **Acceptance Criteria**: Pressure section populated; per-step breakdown visible; pressure level classified correctly; critical events logged
- **Effort**: 1.5 hours | **Ref**: TS §4.5, §7.14

#### T-2A.15.2 — Implement pressure-level mitigations in Artha.md

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — Pressure-level mitigations in Step 16: green/yellow/red/critical thresholds with progressive degradation strategy.
- **Dependencies**: T-2A.15.1
- **Description**: Add pressure response logic per TS §7.14:
  - **Green** (<50%): Standard processing
  - **Yellow** (50–70%): Switch to flash compression, skip FYI items
  - **Red** (70–85%): Process P0 domains only, skip Reference/Archive tiers entirely
  - **Critical** (>85%): Emergency mode — process only Critical/Urgent alerts, skip trend analysis
  Log pressure events and mitigations to health-check.md. Show pressure indicator in `/health` output.
- **Acceptance Criteria**: Pressure mitigations activate at correct thresholds; briefing quality degrades gracefully; pressure events logged; `/health` shows current pressure
- **Effort**: 2 hours | **Ref**: TS §7.14

#### T-2A.15.3 — Implement OAuth token resilience

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — OAuth Token Health section added to health-check.md; proactive token health checking added to Step 0 pre-flight.
- **Dependencies**: Phase 1A complete (OAuth tokens configured)
- **Description**: Add OAuth Token Health section to health-check.md per TS §4.5. Track per provider: last_success, last_failure, consecutive_failures, token_expiry_estimate. Enhance pre-flight Step 0 per TS §7.15: lightweight API call per provider (e.g., Gmail userinfo, MS Graph /me), proactive expiry warnings (MS Graph <7 days, Gmail based on failure patterns). If refresh fails: log details, retry once with backoff, surface error per UX §14.1 (proactive OAuth warning).
- **Acceptance Criteria**: Token health tracked per provider; proactive warnings surface before expiry; refresh failures logged with details; pre-flight catches expired tokens
- **Effort**: 1.5 hours | **Ref**: TS §7.15, UX §14.1

#### T-2A.15.4 — Implement Life Scorecard

- [ ] **Priority**: P1
- **Dependencies**: Phase 1C (Goal Engine), ≥4 weeks of catch-up data
- **Description**: Implement Life Scorecard per TS §7.9. Define 7 dimensions with 1–10 scoring rubrics: Physical Health, Financial Health, Career & Growth, Family & Relationships, Immigration & Legal, Home & Environment, Personal Development. Calculate composite score (sum/7 with equal weights per TD-20). Generate scorecard during Sunday catch-up. Add week-over-week trend detection (↑/↓/→). Add `/scorecard` command per UX §10.11. Store in dashboard.md Life Scorecard section.
- **Acceptance Criteria**: 7-dimension scores calculated from state data; composite score generated; week-over-week trends shown; `/scorecard` produces output per UX §10.11; scores feel calibrated (not arbitrary)
- **Effort**: 3 hours | **Ref**: PRD F15.44, TS §7.9, UX §10.11

#### T-2A.15.5 — Implement `/dashboard` command

- [x] **Priority**: P1
- **Status Note**: COMPLETE 2026-03-10 — /dashboard command added to §5 with Life Pulse, Active Alerts, Open Items Summary, System Health sections.
- **Dependencies**: T-2A.10.5 (dashboard.md template), T-2A.15.4 (scorecard)
- **Description**: Add `/dashboard` command to Artha.md per UX §10.10. Shows: Life Pulse table (all 17 domains with status/alert/last-updated), Active Alerts ranked by URGENCY×IMPACT×AGENCY score, Open Items Summary (count by domain with top 5 items), System Health line (context pressure, token usage, OAuth status). Dashboard regenerated each catch-up (Step 8g per TS §7.1).
- **Acceptance Criteria**: `/dashboard` produces formatted output per UX §10.10; Life Pulse shows all domains; alerts ranked by composite score; system health accurate
- **Effort**: 1 hour | **Ref**: PRD F15.29, TS §4.12, UX §10.10

#### T-2A.15.6 — Implement proactive calendar intelligence

- [ ] **Priority**: P1
- **Dependencies**: Phase 1B Group 1 (calendar integration)
- **Description**: Add proactive calendar alerts to catch-up per PRD F15.31. Detect: (1) double-bookings with conflict severity, (2) events requiring preparation (e.g., meetings with prep docs, travel requiring booking), (3) unblocked deadlines (known deadline without calendar block), (4) travel time conflicts (back-to-back events in different locations). Surface alerts in briefing with specific action suggestions.
- **Acceptance Criteria**: Calendar alerts detect at least double-bookings and unblocked deadlines; alerts include actionable suggestions; false positive rate acceptable
- **Effort**: 1.5 hours | **Ref**: PRD F15.31

---

### Group 16: Phase 2A v2.0 Integration Test

#### T-2A.16.1 — Full catch-up with all Phase 2A v2.0 features *(see also T-2A.23.1 for v2.1)*

- [ ] **Priority**: P0
- **Dependencies**: All T-2A.9–15.* tasks complete
- **Description**: Run a complete catch-up with all Phase 2A features (v1.4 A–J + v2.0 K–S) enabled. Verify:
  1. Data integrity guard: pre-decrypt backup created, post-write verification passes
  2. Bootstrap detection: unpopulated files generate warnings
  3. All v1.4 checks (tiered context, marketing suppression, leading indicators, relationship pulse, ONE THING scoring, digest mode, friction levels, accuracy tracking, privacy surface, slash commands)
  4. Compound signals fire on cross-domain data
  5. Consequence forecasts appear for critical items
  6. Coaching nudge appears at catch-up close
  7. Flash/standard/deep compression routes correctly
  8. Context pressure tracking operates
  9. OAuth health checked in pre-flight
  10. Signal:noise ratio tracked
  11. `/dashboard`, `/scorecard`, `/bootstrap` commands functional
  12. Life Scorecard generates (if ≥4 weeks of data)
- **Acceptance Criteria**: All 12 checks pass; no regression in existing functionality; briefing quality maintained or improved; total catch-up time still <5 minutes
- **Effort**: 1 hour | **Ref**: All Phase 2A specs

### Group 17: Goal Sprint Engine (Workstream T) *(v2.1)*

#### T-2A.17.1 — Implement Goal Sprint creation

- [x] **Priority**: P1
- **Dependencies**: T-1C.4.1 (Goal Engine)
- **Description**: Add 90-day sprint creation to Goal Engine. User says "start a sprint for [goal]" → Artha creates sprint entry in `state/goals.md` with: sprint name, target metric, start/end dates, weekly checkpoints, success criterion. Sprint appears in Goal Pulse section of every briefing with progress bar and days remaining.
- **Acceptance Criteria**: Sprint created via natural language; appears in Goal Pulse; progress tracked weekly; completion/failure detected automatically
- **Effort**: 45 minutes | **Ref**: PRD F13.17, TS §7.1, UX §8.7

#### T-2A.17.2 — Implement target calibration questions

- [x] **Priority**: P1
- **Dependencies**: T-2A.17.1
- **Description**: After 2 weeks of sprint data, Artha asks calibration question: "Your [metric] is at [X]. Your target is [Y] by [date]. Should we adjust the target, change the approach, or stay the course?" Log response in sprint metadata. Adjust target or add note about strategy change.
- **Acceptance Criteria**: Calibration question fires at 2-week mark; user response logged; target adjustable
- **Effort**: 30 minutes | **Ref**: PRD F13.17, UX §8.7

#### T-2A.17.3 — Implement goal auto-detection

- [x] **Priority**: P2
- **Dependencies**: T-2A.17.1, T-2A.12.1 (Pattern of Life)
- **Description**: After 30+ days of data, detect implicit goals from behavior patterns (e.g., consistent gym emails → fitness goal, regular Duolingo → language goal). Surface as suggestion: "It looks like you’re working toward [X]. Want me to track this as a goal?"
- **Acceptance Criteria**: At least 1 auto-detected goal suggestion after 30 days of data; suggestion is skippable
- **Effort**: 30 minutes | **Ref**: PRD F13.18

---

### Group 18: Fastest Next Action + Decision Deadlines (Workstream U) *(v2.1)*

#### T-2A.18.1 — Implement Fastest Next Action field

- [x] **Priority**: P1
- **Dependencies**: T-2A.5.1 (ONE THING scoring)
- **Description**: In Mode 3 (Quick Alert), add a "Fastest Next Action" field that identifies the single most impactful action the user can take right now, considering calendar availability and current context. Field appears after ONE THING in alert briefings. Uses scoring: (impact × urgency × agency) / estimated_effort.
- **Acceptance Criteria**: Fastest Next Action appears in Mode 3 alerts; action is specific and immediately executable; effort estimate included
- **Effort**: 30 minutes | **Ref**: PRD Mode 3, UX §4.1

#### T-2A.18.2 — Implement Decision Deadline tracking

- [x] **Priority**: P1
- **Dependencies**: T-2A.6.1 (Decision Graphs)
- **Description**: Enhance `state/decisions.md` schema with `deadline` and `deadline_source` fields per TS §4.10. During catch-up, scan for decisions with deadlines within 14 days → surface in URGENT section. Decisions with expired deadlines → surface in CRITICAL with "Decision deadline passed" warning. Add Step 7c to workflow: check decisions approaching deadline.
- **Acceptance Criteria**: Decisions with deadlines tracked; 14-day warnings appear; expired deadlines flagged as critical
- **Effort**: 30 minutes | **Ref**: PRD F15.24, TS §4.10, TS §7.1 Step 7c

---

### Group 19: Calendar Intelligence (Workstream V) *(v2.1)*

#### T-2A.19.1 — Implement Week Ahead preview

- [x] **Priority**: P1
- **Dependencies**: T-2.1.1 (Calendar prompt)
- **Description**: On Monday catch-ups (or on request), add WEEK AHEAD section after TODAY in briefing. Table format: Day | Events | Deadlines. Pulls from all calendar sources (Google, iCloud, Outlook). Highlights days with ≥3 events as "busy" and days with 0 events as "open."
- **Acceptance Criteria**: Week Ahead appears automatically on Mondays; available on request other days; shows all 7 upcoming days
- **Effort**: 30 minutes | **Ref**: PRD Mode 1, TS §5.1, UX §4.1

#### T-2A.19.2 — Implement Weekend Planner

- [x] **Priority**: P2
- **Dependencies**: T-2A.19.1
- **Description**: On Friday catch-ups (or via `/weekend` command), generate weekend plan: Saturday/Sunday events from calendar + suggested family activities based on weather (if available) + pending errands that could be batched + any Monday prep needed. Output per UX §4.8.
- **Acceptance Criteria**: Weekend plan generated on Friday; includes calendar events + suggestions; respects existing commitments
- **Effort**: 30 minutes | **Ref**: PRD F8.7, TS §7.17, UX §4.8

#### T-2A.19.3 — Implement calendar-aware action scheduling

- [x] **Priority**: P2
- **Dependencies**: T-2A.19.1
- **Description**: When proposing actions, check user’s calendar for availability. Don’t suggest "call Chase today" if calendar shows back-to-back meetings. Instead: "Call Chase — your Thursday afternoon is open." Add calendar awareness to action proposal workflow.
- **Acceptance Criteria**: Action proposals consider calendar availability; suggest specific time slots when possible
- **Effort**: 30 minutes | **Ref**: PRD F8.6

---

### Group 20: Privacy Hardening (Workstream W) *(v2.1)*

#### T-2A.20.1 — Implement PII detection footer

- [x] **Priority**: P1
- **Dependencies**: T-1A.10.4 (pii_guard.sh)
- **Description**: Add PII GUARD section to briefing footer showing: emails scanned, state files scanned, redactions applied, false positive count. Data comes from pii_guard.sh execution stats. Builds user trust in privacy protection. Format per UX §4.1.
- **Acceptance Criteria**: PII Guard footer appears in every briefing; counts are accurate; zero false positives tracked
- **Effort**: 20 minutes | **Ref**: PRD F15.47, TS §5.1, UX §4.1

#### T-2A.20.2 — Implement privacy audit command

- [x] **Priority**: P2
- **Dependencies**: T-2A.20.1, T-2A.8.1 (Privacy Surface)
- **Description**: Add `/privacy` slash command that shows: all encrypted files and their last-decrypted timestamp, PII detection accuracy over last 30 days, any unencrypted files that contain sensitivity markers, external API data flows. Output per TS §3.6.1.
- **Acceptance Criteria**: `/privacy` command outputs full privacy surface report; flags any unprotected sensitive data
- **Effort**: 30 minutes | **Ref**: PRD F15.49

---

### Group 21: Observability & Coaching (Workstream X, Part 1) *(v2.1)*

#### T-2A.21.1 — Implement post-briefing calibration questions

- [x] **Priority**: P1
- **Dependencies**: T-1A.6.1 (Briefing workflow)
- **Description**: After briefing output, append 2 calibration questions: (1) "Was anything surprising or wrong?" (2) "Anything on your mind Artha didn’t surface?" Silent skip — if user doesn’t respond, move on without nagging. Log responses in `state/health-check.md` for accuracy tracking. See TS §7.1 Step 7b.
- **Acceptance Criteria**: Calibration block appears after every briefing; responses logged; skip is silent (no acknowledgment needed)
- **Effort**: 20 minutes | **Ref**: PRD F15.46, TS §7.1 Step 7b, UX §4.1

#### T-2A.21.2 — Implement /diff command

- [x] **Priority**: P1
- **Dependencies**: T-1A.2.1 (State files)
- **Description**: Add `/diff [period]` slash command. Reads current state files and compares against previous period (default: 7 days). Shows changes with markers: + (new), Δ (changed), − (removed). Groups by domain. Uses git history or timestamped state snapshots for comparison. Output per UX §10.12.
- **Acceptance Criteria**: `/diff` shows meaningful state changes; default 7-day window; custom periods work (e.g., `/diff 30d`)
- **Effort**: 45 minutes | **Ref**: PRD F15.51, TS §7.16, UX §10.12

#### T-2A.21.3 — Implement Monthly Retrospective

- [x] **Priority**: P1
- **Dependencies**: T-2A.7.1 (Accuracy Pulse)
- **Description**: On first catch-up of each month, generate retrospective: accuracy trend (30-day), goal progress summary, domain activity heatmap, top 3 wins, top 3 areas needing attention, system health summary. Format per TS §5.3 and UX §10.13.
- **Acceptance Criteria**: Monthly retro auto-generates on 1st of month; covers all specified sections; skippable
- **Effort**: 30 minutes | **Ref**: PRD F15.50, TS §5.3, UX §10.13

#### T-2A.21.4 — Implement Power Half Hour

- [x] **Priority**: P2
- **Dependencies**: T-2A.18.1 (Fastest Next Action)
- **Description**: When user has 30 minutes available (detected from calendar gap or explicit request), generate a Power Half Hour plan: 3-5 quick actions sorted by impact, each with estimated time. Total ≤30 minutes. Output per UX §10.14.
- **Acceptance Criteria**: Power Half Hour generates on request; actions are specific and time-estimated; total time ≤30 min
- **Effort**: 20 minutes | **Ref**: PRD F15.48, UX §10.14

---

### Group 22: Observability & Coaching (Workstream X, Part 2) *(v2.1)*

#### T-2A.22.1 — Implement Teach Me interaction

- [x] **Priority**: P2
- **Dependencies**: None (standalone)
- **Description**: Add `/teach [topic]` command. Artha explains a domain-relevant concept using the user’s own data as context. Example: `/teach credit utilization` → explains concept using user’s actual credit data. Domain-aware: immigration topics use user’s visa category, finance topics use user’s accounts. Output per UX §10.15.
- **Acceptance Criteria**: `/teach` generates domain-aware explanations; uses real user data for examples; covers at least 5 domains
- **Effort**: 30 minutes | **Ref**: PRD F15.54, UX §10.15

#### T-2A.22.2 — Implement Ask Archana suggestions

- [x] **Priority**: P2
- **Dependencies**: T-2.4.1 (Archana briefing)
- **Description**: When Artha encounters a decision that benefits from Archana’s input (household, kids activities, social commitments, travel plans), add note: "→ Consider asking Archana about [topic]." Triggered by domains tagged `shared` in domain prompts. Max 1 per briefing to avoid noise.
- **Acceptance Criteria**: Ask Archana suggestions appear for shared-domain decisions; max 1 per briefing; respectful framing
- **Effort**: 15 minutes | **Ref**: PRD F15.52

#### T-2A.22.3 — Implement 5-Minute Tasks

- [x] **Priority**: P2
- **Dependencies**: T-2A.18.1 (Fastest Next Action)
- **Description**: Maintain a rolling list of tasks that take ≤5 minutes and can be done on a phone (quick approvals, brief replies, simple lookups). Surface when user has micro-gaps in schedule or asks "anything quick I can knock out?"
- **Acceptance Criteria**: 5-min task list maintained; surfaced on request or during detected short gaps
- **Effort**: 20 minutes | **Ref**: PRD F15.53

#### T-2A.22.4 — Implement Natural Language queries

- [x] **Priority**: P2
- **Dependencies**: T-1A.2.1 (State files)
- **Description**: Support free-form questions like "When is PSE bill due?" or "How is Parth doing in math?" by searching across all state files and returning a concise answer. This is inherent to Claude’s capability but needs prompt guidance to search all state files systematically for factual queries.
- **Acceptance Criteria**: Factual queries answered accurately from state file data; response time within normal catch-up speed
- **Effort**: 15 minutes | **Ref**: PRD F15.55

#### T-2A.22.5 — Enhance Leading Indicator Auto-Discovery

- [x] **Priority**: P2
- **Dependencies**: T-2A.4.1 (Leading Indicators)
- **Description**: Enhance leading indicator engine to auto-discover new indicators from cross-domain data. After 30+ days of data, suggest new leading indicators: "Parth’s assignment completion rate seems to predict quiz scores — want me to track this?" Add to Goal Engine configuration if user approves.
- **Acceptance Criteria**: At least 1 auto-discovered indicator suggested after 30 days; user can approve/reject
- **Effort**: 20 minutes | **Ref**: PRD §8.11

---

### Group 23: Phase 2A v2.1 Integration Test *(v2.1)*

#### T-2A.23.1 — Full catch-up with all Phase 2A v2.1 features

- [ ] **Priority**: P0
- **Dependencies**: All T-2A.17–22.* tasks complete
- **Description**: Run a complete catch-up with all Phase 2A features (v1.4 A–J + v2.0 K–S + v2.1 T–X) enabled. Verify:
  1. All v2.0 checks pass (T-2A.16.1 items 1–12)
  2. Goal Sprint: sprint active with progress bar in Goal Pulse
  3. Calibration questions appear after briefing (skippable silently)
  4. PII Guard footer shows scan stats
  5. `/diff 7d` shows meaningful state changes
  6. Week Ahead appears (if Monday) or on request
  7. Fastest Next Action appears in alert-style briefings
  8. Decision deadlines surface for items within 14 days
  9. Monthly retrospective generates (if 1st of month)
  10. `/teach` command returns domain-aware explanation
- **Acceptance Criteria**: All 10 checks pass; no regression in v2.0 features; total catch-up time still <5 minutes
- **Effort**: 1 hour | **Ref**: All Phase 2A specs

---

### Group 24: WorkIQ Calendar Integration *(v2.2 — Workstream Y)*

> **Workstream Y — Employment Domain Activation**: Adds Microsoft corporate work calendar as 7th data source via WorkIQ MCP. Calendar-only (no email/chat). Windows-only with graceful Mac degradation. Metadata-only persistence. Local redaction before LLM transit.
>
> **Dependency Gate**: Microsoft IT/compliance approval confirmed for routing WorkIQ calendar data through Claude API. *(Confirmed by user — proceed with implementation.)*
>
> **PRD**: v4.1 FR-8 (F8.8–F8.13), §12.7 | **Tech Spec**: v2.2 §3.2b, §4.13, §7.1 Steps 0/4/6/8/9, §8.2.6 | **UX Spec**: v1.5 §4.1, §6.1, §9.3, §14.1

#### T-2A.24.1 — WorkIQ preflight detection + combined auth check

- [ ] **Priority**: P0
- **Dependencies**: None (but T-2A.24.0 compliance gate is confirmed)
- **Description**: Update `scripts/preflight.py` to add WorkIQ as item (f) in preflight checks:
  1. Platform detection: `platform.system()` — if not Windows, set `workiq_available=false`, skip silently
  2. Check `tmp/.workiq_cache.json` for cached result <24h old — if fresh, reuse
  3. If cache miss/stale: single combined detection+auth call: `npx -y @microsoft/workiq@1.x ask -q "What is my name?"` (pinned version, NOT @latest)
  4. Parse response: non-error response = both available AND authenticated
  5. Write cache: `{"available": true/false, "auth_valid": true/false, "platform": "Windows", "checked_at": "ISO8601", "user_name": "..."}`
  6. On failure: log to `state/health-check.md` WorkIQ section, set P1 non-blocking, continue catch-up
  7. On auth failure specifically: surface `⚠️ WorkIQ auth expired` in preflight output
- **Acceptance Criteria**: Preflight completes in <5s with cached result; <30s on cold cache; Mac skips silently with no error; auth failure surfaces clear message
- **Effort**: 2 hours | **Ref**: Tech Spec §3.2b, §7.1 Step 0(f)

#### T-2A.24.2 — WorkIQ redaction config in settings.md

- [ ] **Priority**: P0
- **Dependencies**: None
- **Description**: Add `workiq_redaction` section to `config/settings.md`:
  1. `redact_keywords` list: user-configurable sensitive project codes/keywords (e.g., `["Project-Cobalt", "Acquisition"]`)
  2. `redact_replacement`: default `[REDACTED]` — replaces matched substring only (partial redaction), NOT full title
  3. `workiq_version_pin`: default `1.x` — version constraint for npx calls
  4. `workiq_query_variant`: `auto` (uses context pressure) / `7day` / `2day`
  5. Document: "Add keywords that should NEVER be sent to the LLM. Only the matched substring is replaced — meeting type words (Review, Standup, Interview) are preserved for trigger classification."
- **Acceptance Criteria**: Settings section is parseable; redact_keywords list is initially empty (user populates); documentation is clear
- **Effort**: 30 min | **Ref**: Tech Spec §8.2.6

#### T-2A.24.3 — WorkIQ calendar fetch logic (Step 4)

- [ ] **Priority**: P0
- **Dependencies**: T-2A.24.1 (preflight must pass), T-2A.24.2 (redaction config)
- **Description**: Add WorkIQ calendar fetch as item (e) in Artha.md Step 4 parallel fetch:
  1. Skip if `workiq_available=false` from preflight
  2. Build explicit date-range query: `"List all calendar events from {YYYY-MM-DD} through {YYYY-MM-DD+N}. Format each as: DATE | START_TIME | END_TIME | TITLE | ORGANIZER | LOCATION | TEAMS(yes/no)"` where N=6 (7-day) at green/yellow pressure, N=1 (2-day) at red/critical
  3. Invoke `ask_work_iq` with the query
  4. Parse response: split by newlines, split by `|`, extract fields. Handle: extra whitespace, missing fields (default empty), header rows (skip), conversational prose (retry once with more explicit prompt)
  5. Apply partial redaction: for each event title, replace matched keywords from `redact_keywords` with `[REDACTED]` (substring replacement, preserving meeting type)
  6. Save parsed+redacted events to `tmp/work_calendar.json` (ephemeral — deleted at Step 9)
  7. Failure handling: if 0 events from non-empty response → log warning "format may have changed", retry once; if still 0 → skip with briefing footer note
- **Acceptance Criteria**: Events correctly parsed from pipe-delimited format; redaction verified (keyword replaced, meeting type preserved); tmp file created and well-formed; Mac silently skips
- **Effort**: 3 hours | **Ref**: Tech Spec §7.1 Step 4(e), §8.2.6

#### T-2A.24.4 — Field-merge deduplication (Step 5/6)

- [ ] **Priority**: P1
- **Dependencies**: T-2A.24.3 (work events available)
- **Description**: Implement enrichment-based dedup in cross-domain reasoning (Step 6):
  1. Compare work events against personal calendar events: same day + start time ±5 minutes = candidate match
  2. For matches: keep personal event as primary, merge in work title and Teams link from work event. Set `merged: true` flag.
  3. Merged events are EXCLUDED from cross-domain conflict detection (dedup-excludes-conflict rule)
  4. For non-matched work events: prefix with 💼 emoji, keep as separate entries
  5. For non-matched personal events: no change
- **Acceptance Criteria**: Duplicates correctly merged (not discarded); merged events show personal tag + work title + Teams link; merged events don't trigger false conflicts; non-matched work events show 💼
- **Effort**: 2 hours | **Ref**: Tech Spec §7.1 Step 6, calendar merge logic

#### T-2A.24.5 — Cross-domain conflict detection + duration density (Step 6)

- [ ] **Priority**: P1
- **Dependencies**: T-2A.24.4 (dedup must run first)
- **Description**: Add conflict detection and duration-based density scoring to Step 6:
  1. **Cross-domain conflicts** (work↔personal, ±15 min overlap): Impact=3. Surface as 🔴 alert: "⚠️ CONFLICT: 💼 [work title] ↔ 🏠 [personal title]"
  2. **Internal work conflicts** (work↔work, ±15 min overlap): Impact=1. Surface as ⚠️ info only. Self-resolvable.
  3. **Duration-based density**: calculate `total_meeting_minutes` and `largest_focus_gap`:
     - If total_meeting_minutes > 300 → "📊 Heavy meeting load: [N]h[M]m of meetings today"
     - If largest_focus_gap < 60 → "📊 Context switching fatigue — no focus window >1 hour"
  4. Both metrics go to briefing 📅 TODAY footer
- **Acceptance Criteria**: Cross-domain conflicts score higher than internal; density alerts use duration not count; deduped events excluded from conflict; footer shows meeting stats
- **Effort**: 2 hours | **Ref**: Tech Spec §7.1 Step 6 Rules 7a/7b/8

#### T-2A.24.6 — State persistence: work-calendar.md (Step 8)

- [ ] **Priority**: P1
- **Dependencies**: T-2A.24.5 (density data computed)
- **Description**: Create and maintain `state/work-calendar.md` with metadata-only schema (Step 8):
  1. Create file with YAML frontmatter: `last_fetch`, `last_fetch_platform`, `last_fetch_os`
  2. `today` section: `meeting_count`, `total_minutes`, `focus_gap_minutes`, `teams_count`, `conflicts_cross_domain`, `conflicts_internal`
  3. `density` array: weekly entries `{week_start, meeting_count, total_minutes, avg_daily_minutes, busiest_day, focus_gap_min}` — rolling 13-week window (prune older entries)
  4. NO meeting titles, attendees, organizers, or links persisted. Count + duration only.
  5. Verify: `state/work-calendar.md` is NOT in the encrypted file list (it contains only aggregate metrics, no PII)
  6. Verify: `.gitignore` entry prevents accidental commit of `tmp/.workiq_cache.json`
- **Acceptance Criteria**: Schema matches Tech Spec §4.13; density prunes >13 weeks; no sensitive data in file; git-safe
- **Effort**: 1.5 hours | **Ref**: Tech Spec §4.13

#### T-2A.24.7 — Meeting-triggered Open Items (Step 8)

- [ ] **Priority**: P1
- **Dependencies**: T-2A.24.3 (parsed event data with titles)
- **Description**: Auto-create Employment domain Open Items for critical meeting types (Step 8):
  1. Scan parsed work events for trigger keywords: "Interview", "Performance Review", "Calibration", "Perf Review", "360 Review"
  2. **Temporal filter**: only create OIs for meetings within the next 7 days (future-dated). Past meetings → log to employment.md metrics only, no OI.
  3. OI format: `OI-NNNN: Prepare for [meeting type] on [date] [time]` → Employment domain, Priority P1
  4. Dedup: don't create OI if one already exists for same meeting (match by date + title substring)
  5. In digest mode (>48h gap): explicitly skip past meetings — no stale OIs
- **Acceptance Criteria**: OIs created only for future critical meetings; no stale OIs in digest mode; dedup prevents duplicates; Employment domain routing correct
- **Effort**: 1.5 hours | **Ref**: Tech Spec §7.1 Step 8(i)

#### T-2A.24.8 — Ephemeral data cleanup (Step 9)

- [ ] **Priority**: P0
- **Dependencies**: T-2A.24.6, T-2A.24.7 (state writes complete)
- **Description**: Add explicit `rm tmp/work_calendar.json` to Artha.md Step 9, BEFORE vault encrypt:
  1. Delete `tmp/work_calendar.json` (contains redacted but still corporate meeting data)
  2. Position: after state file writes, before `vault.py encrypt` call
  3. Failure handling: if file doesn't exist (no WorkIQ run), skip silently; if delete fails, log warning but don't halt
  4. Verify: no corporate content persists in `tmp/` after catch-up
- **Acceptance Criteria**: tmp file deleted every run; deletion logged; no corporate data persists; vault encrypt runs after cleanup
- **Effort**: 30 min | **Ref**: Tech Spec §7.1 Step 9

#### T-2A.24.9 — Artha.md catch-up workflow updates

- [ ] **Priority**: P0
- **Dependencies**: T-2A.24.1–24.8 (all logic designed and tested)
- **Description**: Update `config/Artha.md` with WorkIQ integration at exact insertion points:
  1. **Step 0** (preflight): Add item (f) — WorkIQ combined detection+auth check. Non-blocking P1.
  2. **Step 4** (parallel fetch): Add item (e) — WorkIQ calendar fetch with explicit date-range query. Context-pressure-aware query variant.
  3. **Step 6** (cross-domain reasoning): Add Rules 7a (cross-domain conflict), 7b (internal conflict), 8 (duration-based load). Dedup-excludes-conflict.
  4. **Step 8** (deliver+archive): Add items (h) work-calendar.md update and (i) meeting-triggered OIs.
  5. **Step 9** (encrypt): Add `rm tmp/work_calendar.json` before vault encrypt.
  6. Review all adjacent steps for consistency with new data flow.
- **Acceptance Criteria**: All 5 steps updated; step numbering consistent; no broken cross-references; WorkIQ path is clearly marked as optional/non-blocking; existing non-WorkIQ catch-up flow unchanged
- **Effort**: 3 hours | **Ref**: Tech Spec §7.1

#### T-2A.24.10 — Teams "Join" action proposal

- [ ] **Priority**: P2
- **Dependencies**: T-2A.24.3 (parsed events with Teams links)
- **Description**: Surface Teams Join action when meeting starts within 15 minutes:
  1. During briefing generation (Step 7), check: any work event with `teams=yes` starting within ≤15 minutes
  2. If yes, add to action proposals (tier 5 in UX Spec §9.3): "💼 [title] starts in [N] minutes → Join via Teams [open link]"
  3. Friction: 🟢 Low (link open only). Actions: [open] [skip]
  4. If no imminent meetings, no action proposed (silent)
- **Acceptance Criteria**: Join action appears only for imminent Teams meetings; link is functional; non-intrusive when no meetings imminent
- **Effort**: 1 hour | **Ref**: UX Spec v1.5 §9.3

#### T-2A.24.11 — Mac graceful degradation testing

- [ ] **Priority**: P0
- **Dependencies**: T-2A.24.9 (Artha.md updated)
- **Description**: Test full catch-up on Mac to verify zero regression:
  1. WorkIQ preflight: silently skips (platform != Windows). No error, no warning.
  2. Step 4: WorkIQ fetch skipped. All other 6 sources work normally.
  3. Step 6: Cross-domain reasoning works with personal calendar only. No WorkIQ-related errors.
  4. Briefing: If stale `work-calendar.md` exists (<12h, written by Windows session): show "💼 [N] work meetings detected via Windows laptop (titles unavailable on this device)". If stale >12h or nonexistent: no mention.
  5. State files: work-calendar.md is NOT overwritten/cleared on Mac. Windows-written data preserved until next Windows session.
  6. No crash, no hang, no error — catch-up runs identically to pre-WorkIQ Mac experience.
- **Acceptance Criteria**: Full catch-up on Mac passes with zero WorkIQ-related output (except stale metadata note); all existing tests pass; no regression
- **Effort**: 2 hours | **Ref**: Tech Spec §3.2b degradation matrix

#### T-2A.24.12 — Windows end-to-end integration test

- [ ] **Priority**: P0
- **Dependencies**: T-2A.24.9, T-2A.24.11
- **Description**: Test full catch-up on Windows with WorkIQ enabled:
  1. Preflight: WorkIQ detected and authenticated (<30s cold, <5s cached)
  2. Fetch: Events retrieved for current week. Pipe-delimited parsing successful.
  3. Redaction: Sensitive keywords replaced (verify with test keyword). Meeting types preserved.
  4. Dedup: Personal/work duplicate correctly merged (field-merge, not discard).
  5. Conflicts: Cross-domain conflict surfaced as 🔴 (Impact=3). Internal conflicts as ⚠️ (Impact=1).
  6. Density: Duration-based metrics in 📅 TODAY footer. Count + minutes shown.
  7. State: work-calendar.md updated with counts only. No titles/attendees.
  8. Cleanup: `tmp/work_calendar.json` deleted after catch-up.
  9. Briefing: Work events show with 💼 prefix. Footer includes work meeting stats.
  10. OIs: Future critical meeting generates Employment OI. Past meeting does not.
- **Acceptance Criteria**: All 10 checks pass; total catch-up time <5 minutes; no corporate data in state files
- **Effort**: 2 hours | **Ref**: All WorkIQ specs

#### T-2A.24.13 — Full catch-up with all Phase 2A v2.2 features

- [ ] **Priority**: P0
- **Dependencies**: All T-2A.24.1–24.12 tasks complete
- **Description**: Run a complete catch-up with ALL Phase 2A features (v1.4 A–J + v2.0 K–S + v2.1 T–X + v2.2 Y) enabled. Verify:
  1. All v2.1 checks pass (T-2A.23.1 items 1–10)
  2. WorkIQ: Windows shows merged calendar view with 💼 prefix
  3. WorkIQ: Mac gracefully degrades with stale metadata note (or silent)
  4. Cross-domain conflicts surface at correct severity levels
  5. Duration-based density in footer (not count-based)
  6. Partial redaction: keyword replaced, meeting type preserved
  7. No corporate data in state/ (only counts/duration in work-calendar.md)
  8. tmp/ cleaned after catch-up (no work_calendar.json)
  9. Meeting-triggered OIs for future critical meetings only
  10. Teams Join action for imminent meetings (if applicable)
- **Acceptance Criteria**: All 10 checks pass; no regression in v2.0/v2.1 features; total catch-up time <5 min (Windows) / unchanged (Mac)
- **Effort**: 1 hour | **Ref**: All Phase 2A specs

---

### Phase 2A Success Criteria

| Metric | Target | Measurement |
|---|---|---|
| Relationship graph entries | ≥5 with full fields | state/social.md |
| Leading indicator goals | ≥3 goals with leading indicators | prompts/*.md |
| Tiered context token savings | ≥30% vs. all-loaded baseline | health-check.md |
| Marketing email suppression | ≥70% of marketing emails subject-only | health-check.md |
| Digest mode triggers | Auto-triggers on >48hr gaps | health-check.md |
| ONE THING scoring chain | Appears in 100% of briefings | Briefing output |
| Action acceptance rate tracked | Data captured every catch-up | health-check.md |
| Privacy Surface documented | §4.3 exists in Artha.md | Artha.md |
| New slash commands operational | `/decisions`, `/scenarios`, `/relationships` | Manual test |
| Decision/scenario entries | ≥1 each in state files | state/decisions.md, state/scenarios.md |
| Data integrity guard active *(v2.0)* | All 3 layers protecting writes | vault.sh + Artha.md |
| Bootstrap command functional *(v2.0)* | `/bootstrap` populates state files | Manual test |
| Net-negative write events *(v2.0)* | Zero unprotected data loss | state/audit.md |
| Signal:noise ratio *(v2.0)* | ≥30% actionable items | health-check.md |
| Flash briefing *(v2.0)* | ≤8 lines, ≤30 sec reading | Briefing output |
| Context pressure tracked *(v2.0)* | Pressure level logged every catch-up | health-check.md |
| OAuth token health *(v2.0)* | Per-provider status tracked | health-check.md |
| Life Scorecard *(v2.0)* | 7 dimensions scored weekly | state/dashboard.md |
| Consequence forecasts *(v2.0)* | Appear for critical items (>70% confidence) | Briefing output |
| Coaching nudge *(v2.0)* | Max 1 per catch-up, configurable | Catch-up close |
| `/dashboard` operational *(v2.0)* | Shows life pulse + alerts + system health | Manual test |
| `/scorecard` operational *(v2.0)* | Shows 7-dimension table + composite | Manual test |
| WorkIQ preflight *(v2.2)* | Detects+authenticates in <30s (cold) / <5s (cached) | health-check.md |
| WorkIQ Mac degradation *(v2.2)* | Silent skip, stale metadata note if <12h | Mac catch-up test |
| Work calendar merge *(v2.2)* | Field-merge dedup, 💼 prefix, no corporate data in state/ | Manual test |
| Cross-domain conflict scoring *(v2.2)* | Impact=3 for work↔personal, Impact=1 for work↔work | Briefing output |
| Duration-based density *(v2.2)* | Minutes-based alerts, not count-based | Briefing footer |
| Partial redaction *(v2.2)* | Keywords replaced, meeting types preserved | Redaction audit |
| Ephemeral cleanup *(v2.2)* | tmp/work_calendar.json deleted every run | Post-catch-up check |

---

## Phase 2B — Intelligence Expansion (Months 3–5)

> **Objective**: Expand to all 17+ domains, deepen intelligence (insight engine, proactive check-ins), add financial API integrations, implement family access model, and introduce helper scripts only where Claude proves insufficient.
> **Success gate**: All domains covered, ≥10 active goals with auto-metrics and conflict detection, Archana has active access, all insurance/vehicle domains operational.

---

### Domain Prompts — Phase 2

#### T-2.1.1 — Author Calendar domain prompt (FR-8)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/calendar.md created. MCP fetch instructions and cross-domain awareness included.
- **Content**: Unified calendar view (F8.1), conflict detector (F8.2), time budget awareness (F8.3), important date vault (F8.4), upcoming week briefing contribution (F8.5)
- **Effort**: 45 minutes | **Ref**: PRD FR-8

#### T-2.1.2 — Author Learning domain prompt (FR-10)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/learning.md created with goal tracker (F10.1), newsletter digest (F10.2), course progress (F10.4), mid-month alert threshold.
- **Content**: Learning goal tracker (F10.1), newsletter digest (F10.2), Obsidian vault signals (F10.3), course progress tracker (F10.4)
- **Effort**: 30 minutes | **Ref**: PRD FR-10

#### T-2.1.3 — Author Insurance domain prompt (FR-16)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/insurance.md created.
- **Content**: Policy registry (F16.1), premium tracker (F16.2), renewal calendar (F16.3), coverage adequacy review (F16.4), teen driver prep for Parth (F16.6), Microsoft benefits optimizer (F16.8)
- **Sensitivity**: high (encrypted)
- **Effort**: 45 minutes | **Ref**: PRD FR-16

#### T-2.1.4 — Author Vehicle domain prompt (FR-17)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/vehicle.md created with monthly NHTSA recall check via Gemini.
- **Content**: Vehicle registry (F17.1), registration renewal (F17.2), maintenance schedule (F17.3), service history (F17.4), warranty tracker (F17.5), teen driver program for Parth (F17.7), recall monitor via Gemini CLI (F17.8)
- **Effort**: 45 minutes | **Ref**: PRD FR-17

#### T-2.1.5 — Author Boundary domain prompt expansion

- [x] **Priority**: P1
- **Status Note**: COMPLETE — Folded into T-1C.4.1. Full work-life boundary prompt created with all F14.x features.
- **Content**: Full after-hours work signal detection (F14.1), personal time protection (F14.2), work-life balance goal integration (F14.3)
- **Effort**: 30 minutes | **Ref**: PRD FR-14

---

### Finance & Data Expansion

#### T-2.2.1 — Finance prompt: Tax preparation manager (F3.10)

- [x] **Priority**: P1
- **Description**: Add tax preparation tracking to finance domain — document collection, filing deadlines, estimated payments.
- **Effort**: 30 minutes | **Ref**: PRD F3.10

#### T-2.2.1b — Finance prompt: Subscription ledger, credit health, insurance premiums

- [x] **Priority**: P1
- **Description**: Expand finance domain with subscription ledger (F3.4), credit health monitor (F3.5 — Equifax alerts, score tracking), tax document tracker (F3.6), and insurance premium aggregator (F3.11).
- **Effort**: 45 minutes | **Ref**: PRD F3.4, F3.5, F3.6, F3.11

#### T-2.2.2 — Finance prompt: Credit card benefit optimizer (F3.12)

- [x] **Priority**: P1
- **Description**: Add cross-domain trigger: travel bookings → credit card benefit suggestions (lounge access, travel credit, trip insurance).
- **Effort**: 30 minutes | **Ref**: PRD F3.12

#### T-2.2.3 — Kids prompt: Paid enrichment tracker (F4.8) + activity costs (F4.9)

- [x] **Priority**: P1
- **Description**: Track enrichment activity costs and generate semester cost summaries.
- **Effort**: 30 minutes | **Ref**: PRD F4.8, F4.9

#### T-2.2.3b — Health prompt expansion (F6.4, F6.7, F6.8)

- [x] **Priority**: P1
- **Description**: Expand health domain with EOB monitor (F6.4), open enrollment decision support (F6.7), employer benefits inventory (F6.8).
- **Effort**: 30 minutes | **Ref**: PRD F6.4, F6.7, F6.8

#### T-2.2.3c — Insurance prompt: P0 renewal calendar (F16.3)

- [ ] **Priority**: P0
- **Description**: The insurance renewal calendar (F16.3) is P0 within FR-16. Ensure it's prioritized in insurance prompt authoring (T-2.1.3). Auto-pay and home insurance renewals are high-stakes deadlines.
- **Effort**: Included in T-2.1.3 | **Ref**: PRD F16.3


#### T-2.2.5 — Implement Canvas LMS API fetch *(v2.1)*

- [x] **Priority**: P1
- **Dependencies**: T-1A.3.2 (API integration pattern)
- **Description**: Create `scripts/canvas_fetch.py` to pull Parth’s and Trisha’s assignment/grade data from Issaquah School District Canvas instance. Endpoints: `/courses`, `/assignments`, `/submissions`, `/grades`. Auth via Canvas API token stored in Keychain. Output to `state/kids.md` grades section. Run during catch-up pre-flight. See TS §7.18.
- **Acceptance Criteria**: Canvas data populated in kids.md; assignment grades current; GPA calculated; runs in <10s
- **Effort**: 2 hours | **Ref**: PRD F4.10, TS §3.5, TS §7.18

#### T-2.2.6 — Implement Apple Health XML import *(v2.1)*

- [ ] **Priority**: P2
- **Dependencies**: T-1B.2.1 (Health prompt)
- **Description**: Create `scripts/parse_apple_health.py` to import Apple Health XML export. Parse: steps, active calories, exercise minutes, resting heart rate, sleep duration, weight. Write weekly aggregates to `state/health-metrics.md`. Manual export workflow: Health app → Export → save to OneDrive → Artha parses on next catch-up. See TS §7.19.
- **Acceptance Criteria**: Apple Health export parsed; metrics in health-metrics.md; weekly trends calculated
- **Effort**: 1.5 hours | **Ref**: PRD F6.9, TS §3.5, TS §7.19

#### T-2.2.7 — Finance: Tax automation enhancement *(v2.1)*

- [ ] **Priority**: P1
- **Dependencies**: T-2.2.1 (Tax prep manager)
- **Description**: Enhance tax preparation tracking with: automated document collection checklist (W-2, 1099s, mortgage interest, property tax, charitable donations), filing deadline countdown, estimated payment schedule with amount calculation, carry-forward tracker for items spanning tax years.
- **Acceptance Criteria**: Tax checklist auto-generated; filing deadlines tracked; estimated payments scheduled
- **Effort**: 30 minutes | **Ref**: PRD F3.13

#### T-2.2.8 — Digital: Subscription ROI scoring *(v2.1)*

- [x] **Priority**: P2
- **Dependencies**: T-3.1.3 (Digital Life prompt)
- **Description**: Add ROI scoring to subscription tracker in `state/digital.md`. Score each subscription: (usage_frequency × value_rating) / monthly_cost. Surface subscriptions with ROI < threshold in monthly review. Flag unused subscriptions (no usage signal in 30 days) for cancellation review.
- **Acceptance Criteria**: Subscriptions scored; low-ROI flagged monthly; unused subscriptions detected
- **Effort**: 30 minutes | **Ref**: PRD F12.6

#### T-2.2.9 — Kids: College Application Countdown *(v2.1)*

- [x] **Priority**: P1
- **Dependencies**: T-2.2.3 (Kids prompt expansion)
- **Description**: Add college application countdown to `state/kids.md` for Parth (Class of 2027). Track 7 milestones per TS §4.4: PSAT (Oct 2025), SAT (Spring 2026), campus visits (Summer 2026), essay drafts (Aug 2026), early apps (Nov 2026), regular apps (Jan 2027), decisions (Apr 2027). Color-code by status: green (done), amber (upcoming ≤60d), red (overdue). Surface in briefing during relevant windows.
- **Acceptance Criteria**: Countdown milestones tracked; color-coded status in Kid Intelligence section; auto-surfaces when milestones are within 60 days
- **Effort**: 30 minutes | **Ref**: PRD F4.11, TS §4.4, UX §8.8

#### T-2.2.4 — Evaluate Plaid API integration (read-only) *(deferred — PRD v4.0: evaluate FDX/Section 1033 first)*

- [ ] **Priority**: P2 *(deprioritized v2.1)*
- **Description**: Evaluate Plaid for real-time balance and transaction data from Chase, Fidelity, Vanguard, Wells Fargo. If justified: implement as first helper script.
- **Cost**: ~$0.30/account/month (4-8 accounts = ~$1.20-$2.40/month)
- **Decision criteria**: Is email parsing ≥30% unreliable for any institution?
- **Effort**: 2-3 hours | **Ref**: PRD §11, TS §12.4

---

### Intelligence Features

#### T-2.3.1 — Implement Insight Engine (F15.11)

- [x] **Priority**: P0
- **Description**: Enable extended thinking for weekly deep reasoning across all domain state. Artha should generate 3–5 non-obvious cross-domain insights per week.
- **Acceptance Criteria**: At least 1 non-obvious insight per week surfaced in weekly summary
- **Effort**: 1 hour | **Ref**: PRD F15.11

#### T-2.3.2 — Implement Proactive Check-in (Mode 6)

- [x] **Priority**: P1
- **Description**: Integrate check-ins into catch-up flow per UX §11. Trigger conditions: 2+ goals at risk, work-life behind 2+ weeks, no exercise/learning in 5 days, spending >20% over budget.
- **Acceptance Criteria**: Check-ins appear at end of briefing when triggered, max 3 items, deferral works, 14-day suppression after 2 dismissals
- **Effort**: 30 minutes | **Ref**: UX §11, PRD §6 Mode 6

#### T-2.3.3 — Goal Engine expansion

- [x] **Priority**: P1
- **Content**: Goal cascade view (F13.5), goal-linked alerts (F13.6), recommendation engine (F13.7), trajectory forecasting (F13.10), behavioral nudge engine (F13.11), dynamic replanning (F13.12)
- **Effort**: 2 hours | **Ref**: PRD §8.7–§8.11

---

### Family Access

#### T-2.4.1 — Implement Archana's filtered briefing

- [x] **Priority**: P1
- **Description**: Generate a separate briefing for Archana — shared domains only (Finance summary, Immigration, Kids, Home, Travel, Health, Calendar). Subject line includes `(Family)` tag. Separate Claude.ai Project for her on-demand queries.
- **Acceptance Criteria**: Archana receives filtered briefing email, sensitivity filter applied, her Project answers within domain boundaries
- **Effort**: 1 hour | **Ref**: UX §13.2, PRD OQ-2

#### T-2.4.2 — Implement Parth's academic view (Phase 2)

- [ ] **Priority**: P2
- **Description**: Create a Claude.ai Project for Parth with his academic data, SAT prep, college prep timeline, and personal goals. Excludes: family finances, immigration details.
- **Effort**: 30 minutes | **Ref**: UX §13.3

---

### Infrastructure & Operations

#### T-2.5.1 — State volume check

- [ ] **Priority**: P1
- **Description**: Assess total state file token count. If exceeding 150K tokens: introduce SQLite for historical data, keep active state in Markdown.
- **Effort**: 30 minutes | **Ref**: PRD §9.7

#### T-2.5.2 — Implement per-domain accuracy tracking

- [ ] **Priority**: P1
- **Description**: Add accuracy metrics to health-check.md per domain. After each catch-up, track correct/incorrect counts. Alert if any domain drops below 90%.
- **Acceptance Criteria**: health-check.md accuracy YAML block populated per TS §12.8
- **Effort**: 30 minutes | **Ref**: TS §12.8

#### T-2.5.3 — Quarterly governance review (first)

- [ ] **Priority**: P1
- **Description**: Execute the first quarterly review per TS §12.9 — scan Claude Code release notes, evaluate new features, update registry.md.
- **Effort**: 1 hour | **Ref**: TS §12.9

#### T-2.5.4 — Add /accuracy slash command

- [ ] **Priority**: P2
- **Description**: Add `/accuracy` command showing per-domain accuracy metrics from health-check.md.
- **Effort**: 15 minutes | **Ref**: TS §12.10

---

## Phase 3 — Autonomy & Prediction (Months 6–9)

> **Objective**: Complete all 18 FRs, launch predictive capabilities, voice access, earn execution autonomy, and establish longitudinal pattern recognition.
> **Success gate**: All 18 FRs covered, pre-approved actions operational, first annual retrospective, voice queries functional, estate planning documents inventoried.

---

### Remaining Domain Prompts

#### T-3.1.1 — Author Shopping domain prompt (FR-9)

- [x] **Priority**: P2
- **Status Note**: COMPLETE — prompts/shopping.md created. Low-signal; only surfaces exceptions.
- **Content**: Monthly spend summary (F9.1), delivery tracker (F9.2), return window tracker (F9.3), competitive price check via Gemini (F9.5)
- **Effort**: 30 minutes | **Ref**: PRD FR-9

#### T-3.1.2 — Author Social domain prompt (FR-11)

- [x] **Priority**: P2
- **Status Note**: COMPLETE — prompts/social.md created with birthday engine (F11.1), cultural calendar (F11.2), reconnect radar (F11.3), visual greetings via DALL-E (F11.4), state/social.md template.
- **Content**: Birthday engine (F11.1), cultural calendar (F11.2), reconnect radar (F11.3), occasion-triggered greetings (F11.4)
- **Effort**: 30 minutes | **Ref**: PRD FR-11

#### T-3.1.3 — Author Digital Life domain prompt (FR-12)

- [x] **Priority**: P2
- **Status Note**: COMPLETE — prompts/digital.md created with subscription audit (F12.1), renewal calendar (F12.2), security monitor (F12.3), domain tracker (F12.4), state/digital.md template.
- **Content**: Subscription audit (F12.1), renewal calendar (F12.2), account security monitor (F12.3), domain & hosting tracker (F12.4)
- **Effort**: 30 minutes | **Ref**: PRD FR-12

#### T-3.1.4 — Author Estate domain prompt (FR-18)

- [x] **Priority**: P1
- **Status Note**: COMPLETE — prompts/estate.md created.
- **Content**: Document registry (F18.1), beneficiary audit (F18.2), document review cycle (F18.3), life event triggers (F18.4), emergency access guide (F18.5), guardianship planning (F18.7)
- **Sensitivity**: critical (encrypted)
- **Effort**: 45 minutes | **Ref**: PRD FR-18

#### T-3.1.5 — Home prompt expansion

- [x] **Priority**: P2
- **Content**: Home Assistant integration (F7.4), energy usage tracker (F7.5), home value signal (F7.6), service provider rolodex (F7.7), waste & recycling (F7.9), HOA/community dues (F7.10), lawn & landscaping (F7.11), property tax (F7.12), emergency preparedness (F7.13)
- **Effort**: 30 minutes | **Ref**: PRD FR-7

#### T-3.1.6 — Insurance prompt expansion

- [x] **Priority**: P2
- **Content**: Life event coverage triggers (F16.5), claims history log (F16.7)
- **Effort**: 15 minutes | **Ref**: PRD FR-16

#### T-3.1.6b — Communications prompt expansion (F1.3, F1.5, F1.6)

- [ ] **Priority**: P2
- **Content**: Sender intelligence (F1.3), subscription radar (F1.5), USPS Informed Delivery integration (F1.6)
- **Effort**: 30 minutes | **Ref**: PRD FR-1

#### T-3.1.6c — Immigration prompt expansion (F2.4, F2.5)

- [ ] **Priority**: P1
- **Content**: Document vault index (F2.4), attorney correspondence log (F2.5)
- **Effort**: 30 minutes | **Ref**: PRD FR-2


#### T-3.1.7b — Communications: WhatsApp Business API send *(v2.1)*

- [ ] **Priority**: P2
- **Dependencies**: T-3.1.6b (Comms expansion)
- **Description**: Integrate WhatsApp Business API for sending messages to India-based family contacts. Use case: birthday/festival greetings, quick status updates to parents. Read-only first (receive notifications), then controlled send. Requires WhatsApp Business account setup and API approval.
- **Acceptance Criteria**: WhatsApp messages sendable to approved contacts; greetings auto-suggested for occasions; India timezone aware
- **Effort**: 2–3 hours | **Ref**: PRD F1.7

#### T-3.1.7c — Social: India timezone support *(v2.1)*

- [ ] **Priority**: P2
- **Dependencies**: T-3.1.2 (Social prompt)
- **Description**: Add IST (UTC+5:30) awareness to relationship intelligence for India-based contacts. Reconnect radar should consider India business hours for call suggestions. Cultural calendar should include Indian festivals with IST-aware timing. Greeting suggestions should account for time difference.
- **Acceptance Criteria**: India contacts show IST-aware suggestions; festival timing correct for IST; call suggestions respect India business hours
- **Effort**: 20 minutes | **Ref**: PRD F11.11

#### T-3.1.7 — Vehicle prompt expansion

- [ ] **Priority**: P2
- **Content**: Fuel/charging cost tracker (F17.6), lease & lifecycle manager (F17.9), TCO calculator (F17.10)
- **Effort**: 30 minutes | **Ref**: PRD FR-17

---

### Predictive & Advanced Features

#### T-3.2.1 — Implement Predictive Calendar (F15.15)

- [ ] **Priority**: P1
- **Description**: Model recurring events and generate proactive predictions with confidence levels.
- **Effort**: 1 hour | **Ref**: PRD F15.15

#### T-3.2.2 — Goal Engine: Annual retrospective (F13.8)

- [ ] **Priority**: P1
- **Description**: Generate first annual goal retrospective with year-over-year trends.
- **Effort**: 1 hour | **Ref**: PRD F13.8

#### T-3.2.3 — Goal Engine: Seasonal pattern awareness (F13.13)

- [ ] **Priority**: P2
- **Description**: Detect seasonal patterns in goal performance and adjust expectations.
- **Effort**: 30 minutes | **Ref**: PRD §8.10

#### T-3.2.4 — Longitudinal pattern recognition (Artha Memory)

- [ ] **Priority**: P1
- **Description**: Enable cross-domain pattern recognition using 6+ months of data. Surface non-obvious correlations and trends.
- **Effort**: 1 hour | **Ref**: PRD F15.11

---

### Autonomy & Voice

#### T-3.3.1 — Implement autonomy elevation tracking

- [x] **Priority**: P0
- **Description**: Ensure health-check.md tracks all elevation criteria per TS §12.11. Artha surfaces elevation recommendation when criteria met.
- **Acceptance Criteria**: Elevation criteria tracked, recommendation surfaced (UX §19.2), approval logs to audit.md
- **Effort**: 30 minutes | **Ref**: TS §12.11, UX §19

#### T-3.3.2 — Define and enable pre-approved action categories (Level 2)

- [ ] **Priority**: P1
- **Description**: After 60+ days at Level 1, define which action types can be pre-approved for automatic execution.
- **Candidate actions**: auto-add bill due dates to calendar, auto-archive school newsletters, auto-log delivery confirmations, auto-generate greeting card visuals
- **Autonomy floor**: Financial, communication, immigration, deletion actions NEVER pre-approved
- **Effort**: 30 minutes | **Ref**: PRD §10


#### T-3.3.4 — Estate: Wallet Card emergency QR *(v2.1)*

- [ ] **Priority**: P2
- **Dependencies**: T-3.1.4 (Estate prompt)
- **Description**: Generate a physical wallet card with QR code linking to encrypted emergency information. QR decodes to: emergency contacts, medical info (allergies, blood type, medications), insurance policy numbers, attorney contact. Card printable on standard card stock. QR payload encrypted with age; recipient needs shared key. Update quarterly.
- **Acceptance Criteria**: Wallet card generated as printable PDF; QR code links to encrypted payload; quarterly regeneration reminder
- **Effort**: 1 hour | **Ref**: PRD F18.8

#### T-3.3.3 — Voice access via Apple Shortcuts (Phase 3)

- [ ] **Priority**: P2
- **Description**: Create Apple Shortcut "Ask Artha" that captures voice → text → `claude --print` → spoken response.
- **Steps**:
  1. Create Shortcut with voice capture
  2. Pass to `claude --print -p "[query]"` using sub-agent pattern
  3. Return response as spoken text or notification
  4. Test with common queries: "When is the PSE bill due?", "How's Parth doing?"
- **Acceptance Criteria**: Voice queries answered for common factual questions
- **Effort**: 1–2 hours | **Ref**: UX §17.1

---

### State Scaling

#### T-3.4.1 — Evaluate SQLite for historical data

- [ ] **Priority**: P2
- **Description**: If state files exceed 150K tokens total, introduce SQLite for historical entries. Keep active state in Markdown.
- **Effort**: 2 hours if needed | **Ref**: PRD §9.7

#### T-3.4.2 — Evaluate RAG for conversation memory

- [ ] **Priority**: P2
- **Description**: If `memory.md` exceeds useful size, evaluate vector indexing for conversation memory and historical briefing archives.
- **Effort**: 2 hours if needed | **Ref**: PRD §9.7

---

## Risk Register

| # | Risk | Impact | Probability | Mitigation |
|---|---|---|---|---|
| R-1 | Gmail MCP OAuth instability | Catch-up fails — no email data | Medium | Budget 3-5 hrs for setup. Have Python SMTP fallback (TS §9.2). Track `failure_count_30d` in health-check.md. |
| R-2 | PII leakage through state files | Privacy breach | Low (with filters) | Two-layer defense: pii_guard.sh (Layer 1) + Claude redaction (Layer 2). Test with synthetic PII. Monthly audit. |
| R-3 | Context window overflow | Catch-up fails or truncates | Low (106K of 200K) | Email pre-processing reduces token count. Batch large backlogs. Monitor `headroom_pct` in health-check.md. |
| R-4 | Crash during catch-up leaves plaintext on disk | Encrypted state briefly exposed | Medium | OneDrive selective sync + LaunchAgent watchdog (T-1A.1.4). Lock file lifecycle. |
| R-5 | Claude API cost exceeds budget | Cost creep above $50/month | Low | Multi-LLM routing saves $3-6. Prompt caching. Batch processing. Cost tracked in health-check.md. |
| R-6 | Immigration false alert | False sense of security or unnecessary alarm | Very Low | require ≥95% accuracy, zero false positives in critical alerts for Level 1 elevation. Extraction verification (`[VERIFY]` tagging). |
| R-7 | MCP connector version breaking change | Integration fails after update | Medium | Pin to specific MCP version. Source code review for community MCPs. Test after updates. |
| R-8 | Gemini/Copilot free tier quota exhausted | Research queries fall back to Claude | Low | Monitor quota. Fallback to Claude is functional but costs more. |
| R-9 | age key loss (Mac replacement/failure) | Cannot decrypt state files | Low | Document key recovery process. Consider provisioning key on Windows as backup. |
| R-10 | OneDrive sync conflict or corruption | State files corrupted | Very Low | Mac is sole writer (no conflicts). OneDrive 30-day version history. Time Machine backup. |
| R-11 | WorkIQ package deprecated or removed *(v2.2)* | Work calendar integration breaks | Medium | Pin to specific version. Monitor npm registry. Fallback: skip silently, personal calendar only. Document package provenance in Tech Spec §3.2b. |
| R-12 | M365 Copilot license change or WorkIQ API change *(v2.2)* | Auth or query format breaks | Low | 24h cache absorbs transient failures. Preflight detects auth failure. Catch-up continues without work data. |
| R-13 | Corporate compliance policy change *(v2.2)* | Must stop routing calendar data through Claude | Low | Calendar-only (no email/chat), partial redaction, count-only persistence. Integration can be disabled via single config flag without code changes. |
| R-14 | WorkIQ output format change *(v2.2)* | Parser returns 0 events from valid response | Medium | Retry with explicit format request. Log warning. Skip gracefully. Monitor audit.md for format_change_warning pattern. |

---

## Success Criteria Quick Reference

### Phase 1 (Months 1–2)

| Metric | Target | Measurement |
|---|---|---|
| Catch-up briefing accuracy | ≥95% | User feedback on each briefing |
| School email noise reduction | ≥70% | Gmail email count by sender |
| Immigration deadlines tracked | 100% | Immigration state file |
| Active goals with auto-metrics | ≥5 | Goal Engine state |
| Catch-up run time | <3 minutes | health-check.md |
| Monthly Claude API cost | <$50 | API usage dashboard |
| Critical alert false positives | Zero | Immigration + Finance audit log |
| Custom code deployed | vault.sh + pii_guard.sh + safe_cli.sh only | scripts/ directory |

### Phase 2A (Weeks 9–12) *(v2.0)*

| Metric | Target |
|---|---|
| Relationship graph entries | ≥5 with tier, frequency, cultural protocol |
| Leading indicator goals | ≥3 goals with leading + divergence alerts |
| Tiered context token savings | ≥30% vs. all-loaded baseline |
| Marketing suppression rate | ≥70% of marketing emails subject-only |
| Digest mode auto-trigger | Works on >48hr gaps |
| Action acceptance tracking | Data captured every catch-up |
| Decision/scenario entries | ≥1 each in state files |
| New slash commands | `/decisions`, `/scenarios`, `/relationships`, `/dashboard`, `/scorecard`, `/bootstrap` operational |
| Privacy Surface documented | Artha.md §4.3 accurate |
| ONE THING scoring chain | Appears in 100% of briefings |
| Data integrity guard | 3 layers active, zero unprotected data loss |
| Bootstrap command | State files populated via guided interview |
| Signal:noise ratio | ≥30% actionable items |
| Flash briefing | ≤8 lines, auto-selects for <4hr gaps |
| Context pressure tracking | Per-step token estimates, pressure level logged |
| OAuth token health | Per-provider status, proactive expiry warnings |
| Life Scorecard | 7 dimensions scored weekly, composite score |
| Consequence forecasts | Appear for critical items, ≤3 per briefing |
| Coaching engine | Max 1 nudge per catch-up, configurable style |
| Goal Sprint active *(v2.1)* | ≥1 sprint with progress tracking |
| Calibration questions *(v2.1)* | Appear after every briefing; responses logged |
| PII detection footer *(v2.1)* | Scan stats in every briefing footer |
| `/diff` command *(v2.1)* | Shows state changes for custom periods |
| Week Ahead preview *(v2.1)* | Auto on Monday; available on request |
| Fastest Next Action *(v2.1)* | Appears in Mode 3 alerts |
| Decision deadline warnings *(v2.1)* | 14-day warnings + expired flags |
| Monthly retrospective *(v2.1)* | Auto-generates on 1st of month |

### Phase 2B (Months 3–5)

| Metric | Target |
|---|---|
| Domain coverage | All 17 domains with basic prompts |
| Active goals | ≥10 with auto-metrics + conflict detection |
| Financial data via Plaid API | Deferred — evaluate FDX/Section 1033 *(v2.1)* |
| Archana active access | Filtered briefing + Project |
| Helper scripts deployed | ≤3 total (beyond vault + pii + safe) |
| Canvas LMS data *(v2.1)* | Assignment/grade data for both kids |
| Apple Health import *(v2.1)* | Weekly health metrics populated |
| College Countdown *(v2.1)* | 7 milestones tracked for Parth |
| Tax automation *(v2.1)* | Document checklist + deadline tracking |
| Subscription ROI *(v2.1)* | All subscriptions scored; low-ROI flagged |

### Phase 3 (Months 6–9)

| Metric | Target |
|---|---|
| FR coverage | All 18 FRs fully covered |
| Pre-approved actions | ≥3 action types operational |
| Non-obvious insights | ≥1/week from pattern recognition |
| Voice queries | Functional for common questions |
| Estate documents inventoried | All critical docs + review cycle |
| WhatsApp send *(v2.1)* | Messages sendable to approved India contacts |
| Wallet Card *(v2.1)* | Emergency QR card generated + quarterly refresh |
| India TZ awareness *(v2.1)* | IST-aware suggestions for India contacts |
| Predictive calendar accuracy | ≥70% |

---

## Open Decisions Reference

These decisions should be resolved during Phase 1A implementation. Tracked by ID from TS §16.

| ID | Decision | Preferred Resolution | Resolve By |
|---|---|---|---|
| TD-1 | Gmail MCP server selection | **RESOLVED 2026-03-08**: No official Gmail MCP exists. Community npm rejected (security). Using official google-api-python-client. Scripts: gmail_fetch.py, gmail_send.py. | T-1A.3.2 ✓ |
| TD-2 | Email sending mechanism | **RESOLVED 2026-03-08**: `gmail_send.py` — official Gmail API, markdown→HTML, dual MIME. | T-1A.7.4 |
| TD-4 | Keychain integration with MCP env vars | **RESOLVED 2026-03-08**: Python reads Keychain directly via `security find-generic-password`. No env var injection needed. | T-1A.3.2 ✓ |
| TD-5 | Catch-up idempotency | `--since` timestamp in gmail_fetch.py; dedup by message-id | T-1A.10.2 |
| TD-9 | PII filter false positive tuning | Start strict, expand allowlists if >5% FP rate | T-1A.10.4 |
| TD-11 | Ensemble voting threshold | Claude-as-synthesizer (not formal voting) | T-1B.3.1 |
| TD-12 | WhatsApp contact management | `contacts.md` (simple, human-editable) | T-1A.2.4 |
| TD-13 | Gmail MCP write scope | **RESOLVED 2026-03-08**: gmail_send.py has send-only scope. No modify/delete. | T-1A.3.2 ✓ |
| TD-14 | Gemini Imagen output format | Test PNG first; JPEG if size matters | T-1A.8.2 |
| TD-15 | Calendar write scope | **RESOLVED 2026-03-08**: gcal_fetch.py reads primary calendar. Write via calendar prompt guidance. | T-1A.3.3 |
| TD-16 | Gmail send safety mechanism | Direct API send via gmail_send.py with user approval gates | T-1A.4.9 |
| TD-18 | Archana's email access | Forward to Gmail or separate scope | T-1A.7.5 |
| TD-17 | Claude Code portability strategy | Document portability plan; evaluate quarterly | T-2.5.3 |
| TD-19 | pii_guard.sh interception layer | Test PreToolUse hook; keep pre-persist if not | T-1A.10.6 |
| TD-20 | Life Scorecard dimension weighting | Start equal weights; add configurable if user overrides *(v2.0)* | T-2A.15.4 |
| TD-21 | Behavioral baseline minimum sample size | Start with 10 catch-ups; increase to 20 if noisy *(v2.0)* | T-2A.12.1 |
| TD-22 | Net-negative write guard threshold | Start 20%; adjust if >3 false positives in 30 days *(v2.0)* | T-2A.9.3 |
| TD-23 | Context pressure token estimation | Start heuristic; add tiktoken if misclassifications *(v2.0)* | T-2A.15.1 |
| TD-24 | Flash briefing auto-selection threshold | Start <4h gap; make configurable if needed *(v2.0)* | T-2A.13.3 |
| TD-25 | Compound signal persistence | Start ephemeral; add state/signals.md if needed *(v2.0)* | T-2A.11.1 |
| TD-26 | Goal Sprint duration flexibility | Start 90 days fixed; add custom durations if requested *(v2.1)* | T-2A.17.1 |
| TD-27 | Calibration question frequency | Every briefing at first; reduce to weekly if skip rate >80% *(v2.1)* | T-2A.21.1 |
| TD-28 | /diff snapshot storage | Git history vs. timestamped copies; start with git *(v2.1)* | T-2A.21.2 |
| TD-29 | Canvas API token refresh | Manual token rotation vs. OAuth flow; start manual *(v2.1)* | T-2.2.5 |
| TD-30 | WhatsApp Business API vs. personal | Business API (official) vs. personal bridge (unofficial); Business API only *(v2.1)* | T-3.1.7b |

---

*Artha Implementation Plan v2.1 — End of Document*

*"Security first, then signal. Earn trust through reliability, not ambition. Build the foundation so solid that everything above it is just well-written instructions."*
