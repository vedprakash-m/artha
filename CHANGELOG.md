# Changelog

All notable changes to Artha are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [6.1.0] — 2026-03-15

### Fixed / Hardened
- **`scripts/skill_runner.py`** — Agentic CLI hardening (PRD F15.124):
  - Added `if __name__ == "__main__": main()` entrypoint (was inert when run as script from CLI agents like Gemini/Claude).
  - Restructured imports: stdlib → path setup → `reexec_in_venv()` → third-party (`yaml`). Eliminates `ImportError: PyYAML not installed` when run outside venv.
  - `importlib.util` moved to module scope (was inside `run_skill()`, causing potential `UnboundLocalError` on user-plugin path).

- **`scripts/pipeline.py`** — Venv bootstrap + unambiguous health output (PRD F15.125):
  - Added `reexec_in_venv()` call (same pattern as `skill_runner.py`). Prevents silent `ImportError` on bare Python.
  - `run_health_checks()` now always prints `[health] ✓ name` per connector and `All N connectors healthy.` summary — previously these were gated on `--verbose`, making automated health gates (preflight `check_script_health()`) see an empty stderr that fell back to a generic `OK ✓` note.

- **`scripts/skills/noaa_weather.py`** — Unconfigured coordinates guard (PRD F15.126):
  - `get_skill()` raises `ValueError` when `lat==lon==0.0` (placeholder defaults). Previously the skill silently issued a request to `api.weather.gov/points/0.0,0.0` which returns HTTP 404 from the mid-Atlantic Ocean, appearing as an API failure rather than a configuration problem.

- **`scripts/skills/uscis_status.py`** — Actionable 403 IP-block message (PRD F15.127):
  - HTTP 403 now returns `{"blocked": True, "error": "USCIS API is blocking requests from this IP address or network (common on cloud/VPN). Check status manually at https://egov.uscis.gov/casestatus/..."}`. Previous generic `{"error": "HTTP 403", "text": <large HTML>}` was not actionable.
  - Other non-200 responses truncate `response.text` to 500 chars (prevents log bloat).

- **`scripts/preflight.py`** — CI fix: cold-start profile check now uses `ARTHA_DIR` constant instead of `__file__`-relative path. `config/user_profile.yaml` is gitignored so CI never has it; the `__file__`-relative path bypassed the test mock, causing 3 `TestAdvisoryJsonOutput` tests to hit exit 3 (cold start) in CI.

### Tests
- **+12 new tests** (816 total, 5 skipped, 20 xfailed):
  - `tests/unit/test_pipeline.py`: `test_healthy_connector_always_printed_without_verbose`, `test_summary_line_always_printed_on_success`
  - `tests/unit/test_skill_runner.py`: `test_main_block_executes_without_error`, `test_importlib_util_accessible_at_module_scope`
  - `tests/unit/test_skills.py` *(new file)*: `TestNOAAUnconfiguredCoordinates` (2 tests), `TestUSCIS403ErrorMessage` (6 tests)

### Specs
- PRD v6.0 → **v6.1**, features F15.124–F15.127 added
- Tech Spec v3.7 → **v3.8**, §8.9 updated for skill_runner, noaa_weather, uscis_status changes
- UX Spec v2.1 → **v2.2**, agentic CLI hardening UX patterns added

### Deferred (spec entry required before implementation)
- `pipeline.py` exit 1 when `--source` filter matches no configured connector (changes observable exit contract)
- Vault lock PID-aware auto-clear (requires `vault.py` lock file format change)

---

## [6.0.0] — 2026-03-15

### Added
- **Cowork VM & Operational Hardening** (PRD v6.0, Tech Spec v3.7, specs/vm-hardening.md, F15.119–F15.123): addresses 15 failures found during March 15, 2026 Cowork VM diagnostic + 1 silent token expiry failure identified in post-mortem.

- **`scripts/detect_environment.py`** (Phase 1): multi-signal runtime environment detection. 7 probes: cowork marker (`/var/cowork` dir + `$COWORK_SESSION_ID` env var), filesystem writability, `age` installation, keyring functionality, TCP to Google/Microsoft/Apple. Returns `EnvironmentManifest` with `environment` (cowork_vm | local_mac | local_linux | local_windows | unknown), `capabilities` dict, `degradations` list. 5-minute TTL cache in `tmp/.env_manifest.json`. `--debug` flag for raw probe output.

- **`scripts/preflight.py` hardening** (Phase 2):
  - `--advisory` flag: P0 failures become `⚠️ [ADVISORY]` (non-blocking, exit always 0). For use only in sandboxed/VM environments. JSON output includes `advisory_mode: true` and `degradation_list`.
  - `check_profile_completeness()` (P1 check): fires when profile has ≤10 YAML keys; validates `family.primary_user.name`, emails, timezone, ≥1 enabled domain.
  - `check_msgraph_token()` rewrite — 3-layer fix: proactive refresh when near expiry; 60-day cliff warning reading `_last_refresh_success` timestamp; dual-failure message when token expired AND network blocked.
  - `check_state_templates()` — health-check.md only seeded when absent or `last_catch_up:` not present (never overwrites real data).

- **`scripts/setup_msgraph_oauth.py`**: writes `_last_refresh_success` ISO-8601 timestamp to token file after every successful silent refresh. Feeds the 60-day cliff warning in `check_msgraph_token()`.

- **`state/templates/health-check.md`**: new template with `schema_version: '1.1'`, `last_catch_up: never`, `catch_up_count: 0`. Auto-seeded by preflight on first run.

- **`config/Artha.core.md`**: "Read-Only Environment Protocol" block added — 8-step procedure for VM/sandboxed runs, token+network dual-failure subsection.

- **`scripts/generate_identity.py` compact mode** (Phase 3):
  - Default output: ~15KB `config/Artha.md` — extracts §1/§4/§5/§6/§7 from `Artha.core.md` + injects §R command router table (`_COMMAND_ROUTER_TABLE` constant) pointing to `config/workflow/*.md`.
  - `--no-compact` flag: legacy ~78KB full-core output for rollback.
  - `_extract_sections()` parser, `_COMMAND_ROUTER_TABLE` constant.

- **5 `config/workflow/*.md` files rewritten** (Phases 3+4): all stub content replaced with canonical step content + compliance gates:
  - `preflight.md`: Steps 0–2b, read-only exceptions per step, dual OAuth failure rule, environment detection Step 0a.
  - `fetch.md`: Steps 3–4e, mandatory Tier A state file loading checklist, MCP retry protocol (3 tries), Google Calendar IDs warning, offline/degraded mode detection.
  - `process.md`: Steps 5–7b, **CRITICAL email body mandate** (snippet-only PROHIBITED; `[snippet — verify]` tagging required), net-negative write guard, post-write verification steps.
  - `reason.md`: Steps 8–11, URGENCY×IMPACT×AGENCY scoring, consequence forecasting (IF YOU DON'T chain), FNA (Fastest Next Action) scoring, required cross-domain pairings.
  - `finalize.md`: Steps 12–19b, read-only skip list (Steps 7/7b/14–19), **mandatory Connector & Token Health table** (every briefing, even all-green, with Impact + Fix Command columns).
  - Each file: YAML frontmatter, `⛩️ PHASE GATE` prerequisite checklist, `✅ Phase Complete → Transition` footer.

- **`scripts/audit_compliance.py`** (Phase 5): post-catch-up compliance auditor.
  - 7 weighted checks: preflight_executed (20pt), connector_health_block_present (25pt), state_files_referenced (15pt), pii_footer_present (15pt), email_bodies_not_snippets (10pt), domain_sections_present (10pt), one_thing_present (5pt).
  - Degraded-mode auto-detection from `## Session Metadata` footer or `READ-ONLY MODE` header.
  - `--threshold N`: exit 1 if score below N (for CI/pipeline gates).
  - `--json`: machine-readable output; default when stdout is non-TTY.
  - Targets: local catch-up ≥80, VM degraded ≥60.

- **New tests** (106 total across 5 files):
  - `tests/unit/test_detect_environment.py` (29 tests)
  - `tests/unit/test_preflight_advisory.py` (17 tests)
  - `tests/unit/test_token_lifecycle.py` (11 tests)
  - `tests/unit/test_audit_compliance.py` (37 tests)
  - `tests/integration/test_vm_degraded.py` (12 tests — IT-4 through IT-8 from spec)

### Changed
- **`config/Artha.md`** now generated in compact mode by default (~15KB vs 78KB previously). Run `python scripts/generate_identity.py` to regenerate. Use `--no-compact` for legacy behavior.

### Total tests: 804 (698 baseline + 106 new), 0 failures

---

### Added (previously unreleased)
  - `scripts/skills/financial_resilience.py` — `FinancialResilienceSkill`: parses `state/finance.md` for monthly burn rate, emergency fund runway, and single-income stress scenario; registered in `config/skills.yaml` (cadence: weekly, requires_vault: true)
  - `config/domain_registry.yaml`: gig income routing keywords (Stripe, PayPal, Venmo, Upwork, Fiverr, Etsy, DoorDash, Uber earnings, 1099-K, 1099-NEC)
  - `prompts/finance.md`: "Gig & Platform Income Tracking (1099-K)" section with alert thresholds (🟡 >$5K, 🟠 >$20K, 🔴 Q4); "Financial Resilience" briefing section
  - `prompts/shopping.md`: "Purchase Interval Observation" section — recurring purchase pattern tracking
  - `prompts/social.md`: structured contact profiles (9-field template), pre-meeting context injection (📅 briefing block), passive fact extraction (date-annotated, high-confidence only)
  - `prompts/estate.md`: complete "Digital Estate Inventory" — 5 tables (legal documents, password/access recovery, beneficiary designations, auto-renewing services, emergency contacts); stale alerts at 6/12 months
  - `config/actions.yaml`: `cancel_subscription` and `dispute_charge` instruction-sheet actions
  - `prompts/digital.md`: "Subscription Action Proposals" section — price increase, trial conversion, and already-converted trial alert formats
  - `setup.ps1` — Windows PowerShell onboarding script: [1/5] prerequisites, [2/5] venv at `$HOME\.artha-venvs\.venv-win`, [3/5] pip install, [4/5] PII hook, [5/5] demo + wizard; `Write-Host -ForegroundColor` (no ANSI)
  - `artha.py --doctor` — `do_doctor()`: 11-point diagnostic (Python ≥3.11, venv active, core packages, age binary, age key in keyring, age_recipient, Gmail token, Outlook token, state dir file count, PII hook, last catch-up recency); `━━ ARTHA DOCTOR ━━` banner; exits 0 for warnings-only, 1 for failures
  - `scripts/connectors/apple_health.py` — local Apple Health export parser: ZIP and bare XML input, `iterparse + elem.clear()` streaming, 16 `HKQuantityTypeIdentifier` types, `since` relative/absolute date filter; `enabled: false` by default (opt-in)
  - `prompts/health.md`: "Longitudinal Lab Results" section — date-keyed table, flag codes (✅🟡🟠🔴), trend arrows (↑↓→), Apple Health mapping
  - **Bug fix**: `passport_expiry` and `subscription_monitor` added to `_ALLOWED_SKILLS` frozenset in `skill_runner.py` (both skills existed but were missing from allowlist)
  - `README.md`: updated Windows section to reference `setup.ps1`; `--doctor` in dev commands; Apple Health + financial_resilience + `--doctor` in "What You Get"
  - 56 new tests (`test_financial_resilience.py`: 21, `test_doctor.py`: 14, `test_apple_health.py`: 21); 541 total, all passing, PII scan clean

- **OOBE polish audit — first-impression redesign** (PRD v5.7, Tech Spec v3.4, F15.95–F15.99):
  - `setup.sh`: branded header `A R T H A  —  Personal Intelligence OS`, `[1/4]`–`[4/4]` step counters, `--disable-pip-version-check` (suppresses internal path leakage in pip upgrade notices)
  - `artha.py`: `_detect_ai_clis()` + `_print_ai_cli_status()` — detects `claude`, `gemini`, `code` (VS Code/Copilot) via `shutil.which`; shows tailored "Your next step:" after wizard and on welcome; shows install URLs if no CLI found
  - `artha.py do_setup()` completion: redesigned bordered success box with privacy assurance (`🔒 Your data stays on this machine. Artha never phones home.`); followed by AI CLI detection block
  - `scripts/demo_catchup.py`: ANSI colorized output (yellow `ACTION:`, green good-news bullets, red alert bullets, bold section headers) gated on `sys.stdout.isatty()`; removed dead "Fast way: bash setup.sh" footer; added privacy line
  - `README.md`: compressed 624 → 142 lines — hero tagline "Your life, organized by AI.", quick start (3 commands), "What You Get" bullet list, docs table; detailed content removed to `docs/`
  - `docs/backup.md`: new file — full Backup & Restore reference (GFS tiers, CLI commands, cold-start rebuild, key backup, validation checks) moved from README
  - `specs/README.md`: new file — disclaimer that all personal names/data in specs/ are fictional examples (Patel family), not real individuals
  - `Makefile`: `start` target added (`@bash setup.sh`); added to `.PHONY`
  - Fixed duplicate `if __name__ == "__main__"` block in `artha.py`
  - 485 tests passing, PII scan clean

- **Interactive setup wizard + first-run friction fixes** (PRD v5.6, Tech Spec v3.3, F15.89–F15.94):
  - `config/user_profile.starter.yaml` — minimal 45-line first-run template (blank name/email forces real data entry; replaces 234-line example as default for new users)
  - `artha.py do_setup()` — interactive wizard collecting name, email, timezone (ET/PT/IST shortcuts), household type, children; writes clean YAML, auto-runs `generate_identity.py`
  - `artha.py --no-wizard` flag — copies starter profile for manual editing
  - Configured path now calls `do_welcome()` only — removed `do_preflight()` auto-call that caused ✅→⛔ cognitive whiplash
  - `generate_identity._collect_warnings()` — non-blocking advisory for placeholder child names and cities
  - `generate_identity._print_validate_summary()` — identity preview on `--validate` success
  - `preflight.py --first-run` flag — Setup Checklist view with `○ not yet configured` for expected OAuth items; exit 0 when only setup steps remain
  - `setup.sh` wizard integration: removed 234-line profile copy; prompts "Run the 2-minute setup wizard now?"; non-interactive CI path silently copies starter profile
  - 11 new tests (`TestCollectWarnings`, `TestPrintValidateSummary`); 485 total, all passing; PII scan clean

- **10-layer defense-in-depth for state data protection** (PRD v5.5, Tech Spec v3.2, F15.88):
  - Advisory file lock (`flock`/`msvcrt`) prevents concurrent encrypt/decrypt
  - Cloud sync fence detects OneDrive/Dropbox/iCloud in flight, waits for quiescence
  - Post-encrypt verification: `.age` output ≥ plaintext size, aborts on truncation
  - Deferred plaintext deletion: `.md` files only removed after all domains succeed
  - Encrypt-failure lockdown: `chmod 000` on remaining plaintext to block cloud sync
  - Auto-lock mtime guard: skips encryption if any `.md` modified within 60 seconds
  - Net-negative override: `ARTHA_FORCE_SHRINK` env var with `.pre-shrink` pin
  - GFS prune protection: sole-carrier ZIPs pinned from deletion
  - Confirm gate: `restore`/`install` require `--confirm` or `--dry-run`
  - Pre-restore safety backup of live files to `backups/pre-restore/`
  - Key health monitoring: validates `AGE-SECRET-KEY-` format and export status
  - `backup.py`: domain checksums stored in manifest for prune-safe rotation
  - 66 new tests (26 vault, 7 backup defense tests + updated existing);
    total 501 tests, all passing

- **Novice UX deep audit round 2 — 15 issues resolved across 3 commits** (PRD v5.4, Tech Spec v3.1, F15.78–F15.87):
  - Quick Start time estimate updated to `~30 minutes` with per-step breakdown
  - Demo mode callout added immediately after `pip install` (before Step 2)
  - `generate_identity._validate()` now rejects example placeholder values
    (`"Alex Smith"`, `"alex.smith@gmail.com"`) with actionable error messages
  - `user_profile.example.yaml` gains explicit `household:` section
    (type, tenure, adults) matching domain registry filter contract
  - `vault.py` now accepts `--help`/`-h`/`help` → prints usage, exits 0;
    unknown command now writes to stderr
  - Step 6 preflight note replaced with expected-results table (4 checks,
    fresh-install state, when-it-resolves)
  - "Which AI CLI?" callout updated with per-tool free/paid tier details
  - Google "app isn't verified" callout expanded with full safety explanation
  - `docs/security.md` §6 Mosaic PII Risk added (cultural_context + immigration
    = demographic fingerprint; guidance for forkers)
  - 17 new tests (2 placeholder guard, 5 vault help/usage, 10 across round-2
    batches); total 435 passed

### Changed
- venv creation wrapped in OS `<details>` block: `python3` (macOS/Linux) and
  `python` (Windows) — fixes hard failure on Windows PATH
- `git config core.hooksPath .githooks` moved from main bash block to
  "Contributors/forkers only" blockquote
- Preflight `NO-GO` caveat callout moved to appear *before* the command
- `_print_usage()` extracted in `vault.py` as shared helper; no-args exits 1,
  help exits 0, unknown command to stderr

---

  - Multi-LLM Q&A via Telegram: free-form questions routed through
    Claude → Gemini → Copilot failover chain with workspace context
  - Ensemble mode (`aa` prefix): all CLIs called in parallel, consolidated
    via Claude Haiku
  - HCI command redesign: 45+ aliases, single-letter shortcuts
    (`s/a/t/q/d/g/?`), slash optional, hyphens optional
  - Write commands: `items add` (with priority/domain/deadline parsing),
    `done OI-NNN` (marks items resolved with audit logging)
  - `/goals` command (aliases: `g`, `goal`, `goals`)
  - `/diff` command: state file changes since last catch-up (supports
    `diff 7d`, `diff 24h`)
  - Domain listing: `d` without args shows categorised domain list
    (direct-read vs. encrypted/AI-routed)
  - Encrypted domain routing: `d finance` routes through LLM with vault
    decrypt/relock cycle
  - Thinking ack: "💭 Thinking…" sent immediately for long-running commands,
    auto-deleted after response arrives
  - `send_message_get_id()` and `delete_message()` methods in Telegram adapter
  - Structured LLM output: numbered lists, Unicode bullets, one-line direct
    answers, no Markdown
  - CLI workspace context: all LLM CLIs invoked from Artha workspace dir
    (access to CLAUDE.md, skills, vault)
  - Vault auto-relock: `_vault_relock_if_needed()` in finally block after
    every CLI call
  - Content quality pipeline: `_extract_section_summaries()`,
    `_filter_noise_bullets()`, `_clean_for_telegram()`
  - CLI priority order benchmarked: Claude (~16.5s) → Gemini (~26.1s) →
    Copilot (~39.1s) with model args
- `scripts/demo_catchup.py` — Tier 1 demo mode using fictional Patel family
  fixtures; no accounts required ([standardization.md §8])
- `scripts/local_mail_bridge.py` — zero-auth local mail reader for Apple Mail
  (`.emlx`) and UNIX mbox; no OAuth required
- `docs/` directory with six reference documents: `quickstart.md`,
  `domains.md`, `skills.md`, `security.md`, `supported-clis.md`,
  `troubleshooting.md`
- `/bootstrap quick`, `/bootstrap validate`, `/bootstrap integration` modes
  documented in `config/Artha.core.md`
- `prompts/README.md` — prompt file contract and schema documentation
- `scripts/skills/README.md` — skill development reference

### Changed
- All scripts now use `_bootstrap.py` instead of ~30-line inline venv boilerplate
  (`setup_google_oauth.py`, `preflight.py`, and 12 others)
- `gcal_fetch.py` — `--calendars` default now reads from
  `user_profile.yaml:integrations.google_calendar.calendar_ids` instead of
  hardcoded personal calendar IDs
- `scripts/skills/noaa_weather.py` — fallback coordinates changed from
  hardcoded personal location to neutral `0.0, 0.0`
- `scripts/pii_guard.sh` and `scripts/safe_cli.sh` — deprecation banners added;
  Python equivalents are now the canonical versions

---

## [5.0.0] — 2026-03-11

### Summary
First public open-source release. Full rewrite for privacy-first, generic
deployment — no personal PII in any tracked file.

### Added
- `config/user_profile.yaml` and `config/user_profile.example.yaml` — all
  personal configuration externalized from code and prompts
- `scripts/profile_loader.py` — dot-notation config accessor with `lru_cache`
- `scripts/_bootstrap.py` — centralized venv re-exec helper
  (`reexec_in_venv(mode)`) replacing ~30 lines of copy-paste boilerplate
- `scripts/generate_identity.py` — generates `config/Artha.identity.md` from
  `user_profile.yaml`
- `scripts/pii_guard.py` — Layer 1 pre-write PII filter (Python rewrite of
  `pii_guard.sh`)
- `scripts/safe_cli.py` — Python rewrite of `safe_cli.sh`
- `config/Artha.core.md` — genericized system prompt (zero PII)
- `config/Artha.identity.md` — generated per-user identity context
- `config/routing.example.yaml` — example email routing rules (no PII)
- `config/settings.example.md` — example settings file (no PII)
- `config/user_profile.example.yaml` — example profile (fictional Patel family)
- All 17 domain prompt files genericized (zero PII grep hits)
- 128 tests passing (`tests/unit/`, `tests/integration/`)

### Changed
- System prompt split into `Artha.core.md` (generic) + `Artha.identity.md`
  (user-generated); `Artha.md` now imports both
- All hardcoded email addresses removed from scripts and prompts
- All hardcoded family names, coordinates, and account IDs removed

### Security
- PII defense documented in `docs/security.md`
- Three-layer defense-in-depth: regex filter → semantic verification → at-rest encryption

---

## [4.x] — 2025 (pre-open-source, personal use only)

v4.x was a functional but PII-embedded personal deployment. Not released publicly.
Migration guide: see `scripts/migrate.py`.

---

## Spec Version History

Detailed per-version changes previously maintained inline in spec headers.
Relocated here during hardening v5.1 to reduce spec context overhead.

### PRD Versions

- **v4.1** (2026-03): WorkIQ Work Calendar Integration — F8.8–F8.13, employment domain activation
- **v4.0** (2026-03): Intelligence Amplification — 29 enhancements (goal sprints, Canvas LMS, Apple Health, `/diff`, coaching engine)
- **v3.9** (2026-02): Supercharge — data integrity guard, dashboard, coaching, bootstrap, pattern detection, consequence forecasting
- **v3.8** (2026-02): Phase 2A — relationship intelligence, tiered context, decision graphs, digest mode, accuracy pulse
- **v3.7** (2026-01): Operational robustness — pre-flight gate, open items, To Do sync, email coverage matrix
- **v3.6** (2026-01): Critical assessment hardening — 18 items from independent review
- **v3.5** (2025-12): Multi-LLM orchestration, action execution framework
- **v3.4** (2025-12): Governance & evolution framework
- **v3.3** (2025-11): Pre-flight PII guardrails, Claude Code capabilities
- **v3.2** (2025-11): OneDrive sync layer for cross-device state
- **v3.1** (2025-10): Data sensitivity classification, document repository model
- **v3.0** (2025-10): Architectural pivot from push daemon to pull model
- **v2.x** (2025): Household coverage audit, expert reviews, daemon architecture

### Tech Spec Versions

- **v2.2** (2026-03): WorkIQ Calendar MCP, work calendar state schema, parallel fetch
- **v2.1** (2026-03): Intelligence amplification — Canvas LMS, `/diff`, monthly retrospective, Apple Health
- **v2.0** (2026-02): Supercharge — data integrity, bootstrap workflow, coaching, email volume scaling
- **v1.9** (2026-02): Phase 2A — relationship graph, decisions, scenarios, tiered context
- **v1.8** (2026-01): MS Graph direct integration replacing hub-and-spoke forwarding
- **v1.7** (2026-01): Pre-flight gate, open items, To Do sync, email coverage
- **v1.6** (2025-12): Critical assessment hardening, safe_cli, contacts encryption
- **v1.5** (2025-12): Multi-LLM orchestration, action framework
- **v1.4** (2025-11): Governance framework
- **v1.3** (2025-11): PII guardrails, Claude Code capabilities
- **v1.2** (2025-10): OneDrive sync layer

### UX Spec Versions

- **v1.5** (2026-03): WorkIQ calendar UX, merged view, Teams join actions
- **v1.4** (2026-03): Intelligence amplification UX — `/diff`, weekend planner, coaching display
- **v1.3** (2026-02): Supercharge UX — flash briefing, bootstrap interview, dashboard, scorecard
- **v1.2** (2026-02): Phase 2A — digest mode, relationship pulse, leading indicators
- **v1.1** (2026-01): Pre-flight gate errors, `/items` command
