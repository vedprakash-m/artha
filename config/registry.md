---
schema_version: "1.0"
component: registry
last_updated: 2026-04-25T00:00:00
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
| `scripts/vault.py` | Cross-platform encrypt/decrypt sensitive state files using `age` + keyring | ✅ Operational | 2026-03-13 |
| `scripts/pii_guard.py` | Layer 1 PII detection and redaction (pure Python, cross-platform) | ✅ Operational | 2026-03-13 |
| `scripts/safe_cli.py` | Outbound PII wrapper for Gemini/Copilot CLI | ✅ Operational | 2026-03-13 |
| `scripts/com.artha.vault-watchdog.plist` | LaunchAgent: auto-encrypt on session crash (macOS) | ✅ Loaded (`com.artha.vault-watchdog`) | 2026-03-07 |
| `scripts/requirements.txt` | Python package dependencies (mirrors pyproject.toml optional-dependencies) | ✅ Created | 2026-03-13 |
| `scripts/google_auth.py` | Shared Google OAuth helper — keyring-backed, auto-refresh | ✅ Operational | 2026-03-08 |
| `scripts/pipeline.py` | Unified data pipeline — runs all connectors from `connectors.yaml` | ✅ Operational | 2026-03-13 |
| `scripts/gmail_send.py` | Send email via Gmail API; markdown→HTML, dual MIME | ✅ Operational | 2026-03-08 |
| `scripts/setup_google_oauth.py` | One-time OAuth setup wizard — stores creds in keyring | ✅ Operational | 2026-03-08 |
| `scripts/setup_msgraph_oauth.py` | Microsoft Graph OAuth setup — PKCE flow | ✅ Operational | 2026-03-08 |
| `scripts/setup_icloud_auth.py` | iCloud credentials setup (IMAP + CalDAV) | ✅ Operational | 2026-03-08 |
| `scripts/preflight.py` | Pre-catch-up health gate — P0 blocks, P1 warns | ✅ Operational | 2026-03-13 |
| `scripts/todo_sync.py` | Bidirectional sync: open_items.md ↔ Microsoft To Do | ✅ Operational | 2026-03-08 |
| `scripts/skill_runner.py` | Data fidelity skill orchestrator (USCIS, weather, tax, recalls) | ✅ Operational | 2026-03-13 |
| `scripts/mcp_server.py` | MCP server adapter for Artha tools | ✅ Operational | 2026-03-13 |
| `scripts/lib/workiq_circuit_breaker.py` | WorkIQ circuit-breaker state machine (§38) | ✅ Operational | 2026-04-25 |
| `scripts/lib/mcp_formatter.py` | MCP → connector record normalization (F31) | ✅ Operational | 2026-04-25 |
| `scripts/lib/work_connector_router.py` | Policy-driven Work OS connector routing (§38) | ✅ Operational | 2026-04-25 |
| `scripts/lib/enghub_manager.py` | EngHub MCP subprocess lifecycle manager (§38) | ✅ Operational | 2026-04-25 |
| `scripts/schemas/briefing_block.py` | BriefingBlock schema for F20 success predicate | ✅ Operational | 2026-04-25 |
| `scripts/generate_identity.py` | Assembles Artha.md from core + identity | ✅ Operational | 2026-03-13 |
| `scripts/demo_catchup.py` | Demo briefing with fictional data for onboarding | ✅ Operational | 2026-03-13 |
| `scripts/upgrade.py` | Non-destructive version upgrade detector | ✅ Operational | 2026-03-13 |
| `scripts/migrate.py` | Best-effort migration from settings.md → user_profile.yaml | ✅ Operational | 2026-03-13 |
| `scripts/profile_loader.py` | Cached singleton accessor for user_profile.yaml | ✅ Operational | 2026-03-13 |

### Script Health Checks
```bash
# pii_guard.py
python scripts/pii_guard.py test 2>/dev/null | tail -1
# Expected: "Results: 19 passed, 0 failed"

# vault.py
python scripts/vault.py health
# Expected: exit 0

# pipeline health check
python scripts/pipeline.py --health

# LaunchAgent (macOS only)
ls ~/Library/LaunchAgents/com.artha.vault-watchdog.plist 2>/dev/null && echo "LaunchAgent: installed" || echo "LaunchAgent: NOT INSTALLED"
```

---

## Configuration Files

| File | Purpose | Sensitivity | Encrypted |
|------|---------|-------------|-----------|
| `config/CLAUDE.md` | Auto-loaded by Claude Code — delegates to config/Artha.md | none | no |
| `config/Artha.md` | Full operating instructions (assembled from core + identity) | internal | no |
| `config/user_profile.yaml` | Personal configuration — gitignored, never committed | high | no |
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

### `state/goals.md` Goals schema (v2.0)

Active goals are tracked in a `goals:` list within `goals.md`. Schema added in Phase 1 of the Goals Reloaded spec (2026-03-28).

```yaml
goals:
  - id: G-001              # Sequential, never reused
    title: "[goal name]"
    type: milestone        # milestone | outcome | habit
    category: "[domain]"   # fitness|health|finance|learning|academic|career|etc.
    status: active         # active | parked | done | dropped
    next_action: "[specific next action — sentence]"
    next_action_date: YYYY-MM-DD
    review_date: YYYY-MM-DD
    last_progress: YYYY-MM-DD   # null for parked/new goals
    created: YYYY-MM-DD
    target_date: YYYY-MM-DD
    leading_indicators: []
    # Optional — outcome goals with a measurable metric:
    metric:
      baseline: 200        # starting value (enables accurate % for direction: down)
      current: 183.0
      target: 160
      unit: lb             # lb|kg|usd|pct|count|etc.
      direction: down      # up | down
    # Optional — parked goals:
    parked_reason: "[reason for deferral]"
    parked_since: YYYY-MM-DD
    # Optional — per-goal sprint tracking (Phase 1: display convenience; Phase 2: replaces top-level sprints:):
    sprint:
      id: SPR-001             # optional cross-reference to top-level sprints: entry
      target: "[specific measurable outcome for this sprint]"
      sprint_start: YYYY-MM-DD
      sprint_end: YYYY-MM-DD
      progress_pct: 0         # 0–100
```

> **Sprint coexistence (v2.0):** The top-level `sprints:` block (§Sprint schema v2.1 above) is preserved
> for backward compatibility — Step 3 sprint calibration reads `state/goals.md → sprints`. Per-goal
> `sprint:` sub-blocks are a display convenience. **Phase 2:** Migrate Step 3 to read per-goal
> `sprint:` sub-blocks and deprecate top-level `sprints:`. Set via `goals_writer.py --update G-NNN`
> (per-goal sprint support is Phase 2 — write the sub-block manually until then).

**`goals_writer.py`** manages all YAML writes to this block. The LLM owns Markdown tables; the script owns YAML frontmatter (dual-layer architecture). Work goals use the identical schema in `state/work/work-goals.md` — accessed via `goals_view.py --scope all` or `--scope work`.

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

## Work OS Connectors

| Connector | Route | Purpose | Autonomy Cap | Status |
|-----------|-------|---------|--------------|--------|
| `workiq_bridge` | WorkIQ (AI read) | WorkIQ bridge — calendar/email/teams narrative fetch; display-only, circuit-breaker gated | L1_permanent | ✅ Registered |
| `m365_identity` | MCP Direct (primary) | Read-only M365 identity/profile data via Graph API | L1_permanent | ✅ Registered |
| `m365_write` | MCP Direct (stub) | M365 write operations — permanently stubbed (OQ-1) | L1_permanent | ⛔ Stub (never implement) |
| `enghub` | EngHub MCP (async) | Engineering Hub MCP search — fire-and-forget enrichment, daemon thread only | L1_permanent | ✅ Registered |

---

## External Dependencies

| Component | Status | Notes |
|-----------|--------|-------|
| `age` encryption | Required | `brew install age` (macOS) · `winget install FiloSottile.age` (Windows) |
| age keypair | Required | Private key in system keychain; public key in `user_profile.yaml → encryption.age_recipient` |
| Gmail API (Python) | Optional | Run `python scripts/setup_google_oauth.py` |
| Google Calendar API | Optional | Same OAuth flow as Gmail |
| `google-api-python-client` | Optional | Run `pip install -r scripts/requirements.txt` |
| AI CLI | Required | Gemini CLI, GitHub Copilot, or Claude Code (one of) |
| LaunchAgent (macOS) | Optional | `com.artha.vault-watchdog` — auto-encrypt on session crash |

---

## Setup Checklist

- [ ] Install `age` encryption tool
- [ ] Generate age keypair; store private key in keychain; set `encryption.age_recipient` in `user_profile.yaml`
- [ ] Run `python scripts/vault.py encrypt` on sensitive state files
- [ ] Enable PII pre-commit hook: `git config core.hooksPath .githooks`
- [ ] Set up Google Cloud project, enable Gmail API + Google Calendar API
- [ ] `pip install -r scripts/requirements.txt`
- [ ] `python scripts/setup_google_oauth.py` (stores OAuth tokens in `.tokens/`)
- [ ] Bootstrap state files with your data (`/bootstrap` in an AI session)
- [ ] Run first `/catch-up` and validate briefing output

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-03-07 | Initial registry — all files created, external deps pending |
| 1.1 | 2026-03-08 | Added 6 Python scripts (Google API), 5 state files, 4 prompt files; replaced MCP with Python approach |
