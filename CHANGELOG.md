# Changelog

All notable changes to Artha are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
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
