---
schema_version: "1.0"
component: registry
last_updated: 2026-03-07T22:59:52
description: Component manifest for the Artha Personal Intelligence System
---
# Artha Component Registry

This file catalogs every component of the Artha system: scripts, prompts, state files,
and config files — with their purpose, sensitivity, and current status.
Run `/health` to validate each component is present and passes its self-check.

---

## Scripts

| File | Purpose | Status | Last Verified |
|------|---------|--------|---------------|
| `scripts/vault.sh` | Encrypt/decrypt sensitive state files using `age` | ✅ Operational — age v1.3.1, Keychain-backed | 2026-03-07 |
| `scripts/pii_guard.sh` | Layer 1 PII detection and redaction | ✅ Operational — 19/19 tests passing | 2026-03-07 |
| `scripts/safe_cli.sh` | Outbound PII wrapper for Gemini/Copilot CLI | ✅ Created | 2026-03-07 |
| `scripts/com.artha.vault-watchdog.plist` | LaunchAgent: auto-encrypt on session crash | ✅ Loaded (`com.artha.vault-watchdog`) | 2026-03-07 |
| `scripts/requirements.txt` | Python package dependencies for Google API scripts | ✅ Created | 2026-03-08 |
| `scripts/google_auth.py` | Shared Google OAuth helper — Keychain-backed, auto-refresh | ✅ Created | 2026-03-08 |
| `scripts/gmail_fetch.py` | Fetch Gmail messages → JSONL output; HTML strip, thread truncate | ✅ Created | 2026-03-08 |
| `scripts/gmail_send.py` | Send email via Gmail API; markdown→HTML, dual MIME | ✅ Created | 2026-03-08 |
| `scripts/gcal_fetch.py` | Fetch Google Calendar events → JSONL output | ✅ Created | 2026-03-08 |
| `scripts/setup_google_oauth.py` | One-time OAuth setup wizard — stores creds in Keychain | ✅ Created | 2026-03-08 |

### Script Health Checks
```bash
# pii_guard.sh
bash scripts/pii_guard.sh test 2>/dev/null | tail -1
# Expected: "Results: 19 passed, 0 failed"

# vault.sh (requires age installed)
age --version && echo "age: OK" || echo "age: NOT INSTALLED"

# safe_cli.sh
bash -n scripts/safe_cli.sh && echo "safe_cli.sh: OK"

# LaunchAgent
ls ~/Library/LaunchAgents/com.artha.vault-watchdog.plist 2>/dev/null && echo "LaunchAgent: installed" || echo "LaunchAgent: NOT INSTALLED"
```

---

## Configuration Files

| File | Purpose | Sensitivity | Encrypted |
|------|---------|-------------|-----------|
| `CLAUDE.md` | Auto-loaded by Claude Code — delegates to config/Artha.md | none | no |
| `config/Artha.md` | Full operating instructions (466 lines, 11 sections) | internal | no |
| `config/settings.md` | Capabilities, feature flags, budget, encryption | internal | no |
| `config/contacts.md` | Family/professional contacts with phone numbers | high | yes (.age) |
| `config/occasions.md` | Annual occasion calendar (MOVED to state/) | high | yes (.age) |
| `config/registry.md` | This file — component manifest | internal | no |

---

## Prompt Files

| Domain | File | Priority | Status |
|--------|------|----------|--------|
| Immigration | `prompts/immigration.md` | P0 | ✅ Created |
| Finance | `prompts/finance.md` | P0 | ✅ Created |
| Kids | `prompts/kids.md` | P0 | ✅ Created |
| Communications | `prompts/comms.md` | P0 | ✅ Created |
| Calendar | `prompts/calendar.md` | P0 | ✅ Created |
| Travel | `prompts/travel.md` | P1 | ✅ Created |
| Health | `prompts/health.md` | P1 | ✅ Created |
| Home | `prompts/home.md` | P1 | ✅ Created |
| Shopping | `prompts/shopping.md` | P1 | ✅ Created |
| Goals | `prompts/goals.md` | P1 | ✅ Created |
| Vehicle | `prompts/vehicle.md` | P1 | ✅ Created |
| Estate | `prompts/estate.md` | P1 | ✅ Created |
| Insurance | `prompts/insurance.md` | P1 | ✅ Created |
| Boundary | `prompts/boundary.md` | P1 | ✅ Created |
| Learning | `prompts/learning.md` | P1 | ✅ Created |
| Social | `prompts/social.md` | P1 | ✅ Created |
| Digital | `prompts/digital.md` | P1 | ✅ Created |

---

## State Files

| Domain | File | Sensitivity | Encrypted | Bootstrap Status |
|--------|------|-------------|-----------|-----------------|
| Immigration | `state/immigration.md` | critical | yes | ⏳ Template — needs real data |
| Finance | `state/finance.md` | high | yes | ⏳ Template — needs real data |
| Health | `state/health.md` | high | yes | ⏳ Template — needs real data |
| Insurance | `state/insurance.md` | high | yes | ⏳ Template — needs real data |
| Estate | `state/estate.md` | high | yes | ⏳ Template — needs real data |
| Vehicle | `state/vehicle.md` | high | yes | ⏳ Template — needs real data |
| Audit | `state/audit.md` | high | yes | ⏳ Template — append-only log |
| Kids | `state/kids.md` | medium | no | ⏳ Template — needs real data |
| Calendar | `state/calendar.md` | medium | no | ⏳ Populated each catch-up via MCP |
| Home | `state/home.md` | medium | no | ⏳ Template — needs real data |
| Travel | `state/travel.md` | medium | no | ⏳ Template — populated on first booking email |
| Shopping | `state/shopping.md` | low | no | ⏳ Template — populated on first order |
| Goals | `state/goals.md` | medium | no | ⏳ Needs user-defined goals |
| Comms | `state/comms.md` | medium | no | ⏳ Template — populated each catch-up |
| Health Check | `state/health-check.md` | low | no | ⏳ Initialized on first catch-up |
| Memory | `state/memory.md` | low | no | ✅ Created — preferences, decisions, corrections |
| Boundary | `state/boundary.md` | low | no | ✅ Created — weekly work-life metrics |
| Learning | `state/learning.md` | low | no | ✅ Created — monthly learning target tracking |
| Social | `state/social.md` | low | no | ✅ Created — birthday/occasion/greeting log |
| Digital | `state/digital.md` | low | no | ✅ Created — subscription ledger, security alerts |
| Occasions | `state/occasions.md` | high | yes | Annual dates, birthdays, anniversaries, deadlines |

---

## Versioning

- State files carry YAML frontmatter: `schema_version:`, `last_updated:`, `updated_by:`
- When updating a state file, increment `last_updated` timestamp; `updated_by: artha-catchup` or `artha-interactive`
- If Artha.md schema_version changes, add migration notes to `audit.md`

### `state/goals.md` Sprint schema (v2.1)
Active sprints are tracked in a `sprints:` list within `goals.md`:
```yaml
sprints:
  - id: SPR-001
    name: "[sprint name]"
    linked_goal: "[goal name from goals list]"
    target: "[specific, measurable outcome]"
    sprint_start: YYYY-MM-DD
    sprint_end: YYYY-MM-DD
    duration_days: 30
    status: active
    progress_pct: 0
    calibrated_at_14d: false
    outcome: ""
```

### `state/decisions.md` Deadline schema (v2.1)
```yaml
- id: DEC-001
  date: YYYY-MM-DD
  summary: "[concise decision description]"
  context: "[background]"
  domains_affected: [domain1, domain2]
  alternatives_considered: "[options weighed]"
  deadline: YYYY-MM-DD
  review_trigger: "[condition for re-evaluation]"
  status: active
```

---

## External Dependencies

| Component | Status | Notes |
|-----------|--------|-------|
| `age` encryption | ✅ Installed (v1.3.1) | `brew install age` — done |
| age keypair | ✅ Generated | Private key in Keychain; public key in settings.md |
| Gmail API (Python) | ⏳ OAuth PENDING | Run `python3 scripts/setup_google_oauth.py` to configure |
| Calendar API (Python) | ⏳ OAuth PENDING | Run `python3 scripts/setup_google_oauth.py` to configure |
| `google-api-python-client` | ⏳ Install pending | Run `pip install -r scripts/requirements.txt` |
| Gemini CLI | ✅ Installed (v0.32.1) | `/opt/homebrew/bin/gemini` — use `-p` flag |
| Copilot CLI | ✅ Installed (v1.0.2) | `gh copilot suggest "<query>"` |
| LaunchAgent | ✅ Loaded | `com.artha.vault-watchdog` running |

---

## Setup Checklist

- [x] `brew install age`
- [x] `age-keygen` → store private key in Keychain, public key in settings.md
- [x] Run `vault.sh encrypt` on all sensitive state and config files
- [x] Install LaunchAgent: `com.artha.vault-watchdog` loaded
- [ ] Set up Google Cloud project, enable Gmail API + Calendar API
- [ ] `pip install -r scripts/requirements.txt`
- [ ] `python3 scripts/setup_google_oauth.py` (stores OAuth tokens in Keychain)
- [ ] Set `briefing_email` in config/user_profile.yaml
- [ ] Bootstrap state files with real family data
- [ ] Run first `/catch-up` and validate briefing output

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-03-07 | Initial registry — all files created, external deps pending |
| 1.1 | 2026-03-08 | Added 6 Python scripts (Google API), 5 state files, 4 prompt files; replaced MCP with Python approach |
