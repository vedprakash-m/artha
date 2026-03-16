# Changelog

All notable changes to Artha are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [7.0.5] — 2026-03-14

### Added — Utilization Uplift (specs/util.md U-1–U-9)

**U-5: Privacy Hardening**
- **`config/connectors.yaml`** — All 8 personal-data connectors (`google_gmail`, `google_calendar`, `microsoft_outlook`, `microsoft_todo`, `apple_icloud_calendar`, `apple_reminders`, `workiq`, `health_kit`) now have `prefer_mcp: false` with comment `# PRIVACY: no official MCP — use direct API only`. Previously all 8 were `prefer_mcp: true`, routing personal data through unapproved 3rd-party MCP proxies.

**U-6: Profile Scaffold**
- **`config/user_profile.yaml`** — Rebuilt from a 4-line stub to a 130+ line comprehensive scaffold with all known values: full family members (primary user, spouse, 2 children with school info), Washington state location, household info, 17 enabled domains, integration preferences, encryption key preserved. 8 `# FILL:` markers left for user to supply (Gmail address, iCloud, city, parcel ID, Canvas URLs, VINs, briefing email, Telegram IDs). Passes `generate_identity.py --validate` with 17 domains enabled.

**U-7: Routing Generation**
- **`config/routing.yaml`** — Generated from enriched user profile via `generate_identity.py --with-routing`. Was `key: value` (completely broken). Now contains `system_routes` (immigration/USCIS, finance institutions, estate, kids/school, marketing suppression) and `user_routes` (school domains, finance institutions list).

**U-8: Pipeline Step 21**
- **`config/Artha.core.md`** — Step 21 added after Step 20 (channel push): runs `fact_extractor.py` on `tmp/session_history_*.json` to extract 6 fact types (correction, pattern, preference, contact, schedule, threshold) and persist to `state/memory.md`. Non-blocking (failure never breaks catch-up). Gated on `harness.agentic.fact_extraction.enabled`.

**U-2: Occasions Enrichment**
- **`state/occasions.md`** — Schema bumped to v1.1. Added "Extended Family — India Birthdays" table (11 contacts with DOBs extracted from `state/contacts.md`). Added "Cultural & Religious Occasions (2026–2027)" table with 16 Indian festivals (Holi, Diwali, Navratri, Ganesh Chaturthi, etc.). Added "US Public Holidays (2026)" table with 12 holidays. Fixed all existing entries: ISO date format, corrected ages, standardized tables.

**U-1: Circles Schema**
- **`state/contacts.md`** — YAML frontmatter replaced with full circles classification (schema v1.1): 6 circles defined — `core_family` (nudge: false), `extended_family_india` (14-day cadence), `best_friends` (30-day cadence), `us_friends` (30-day cadence), `spiritual` (weekly), `professional` (as-needed). Each circle has label, members list, cadence, nudge flag, signal_sources, and description.

**U-9: New Relationship & Life Skills**
- **`scripts/skills/relationship_pulse.py`** (new) — Reads circle definitions from `state/contacts.md` YAML frontmatter; extracts last-contact ISO dates from table rows; computes `days_since_contact` vs `_CADENCE_DAYS` threshold per circle; returns top-10 most overdue contacts sorted by `overdue_by`. Only processes circles with `nudge: true`.
- **`scripts/skills/occasion_tracker.py`** (new) — Parses `state/occasions.md` for birthdays, festivals, anniversaries, and holidays. `_this_year_occurrence()` handles Feb-29 leap-year edge case; `_parse_date_flexible()` handles ISO-8601. Alert windows: birthday 🔴 ≤3d / 🟠 ≤7d / 🟡 ≤14d; festival/holiday 7d window. Generates contextual WhatsApp greeting suggestions per event type.
- **`scripts/skills/bill_due_tracker.py`** (new) — Extracts bill rows from `state/occasions.md` Financial/Legal/Deadline sections. `_parse_bill_date()` handles 4 formats: ISO, "Month Day, Year", "Monthly (Nth)", "Semi-annual (Mo & Mo)". Alert severities: 🔴 ≤1d, 🟠 ≤3d, 🟡 ≤7d; also detects overdue items.
- **`scripts/skills/credit_monitor.py`** (new) — Scans `state/digital.md` and `state/finance.md` for credit monitoring signals using 4 regex patterns: `_FRAUD_PATTERN` (🔴), `_INQUIRY_PATTERN` (🟠), `_NEW_ACCOUNT_PATTERN` (🟠), `_SCORE_CHANGE_PATTERN` (🟡). Deduplicates by excerpt key; sorts fraud first; gracefully degrades when vault-encrypted files unavailable.
- **`scripts/skills/school_calendar.py`** (new) — Scans `state/calendar.md`, `state/occasions.md`, and `state/kids.md` for LWSD school events. `_SCHOOL_KEYWORDS` regex covers PTC, parent-teacher, LWSD, Tesla STEM, Inglewood, no-school days, breaks, graduation. `_GRADE_PATTERN` detects failing/incomplete/missing assignments. Deduplicates events by `(date, event[:40])` key.
- **`config/skills.yaml`** — 5 new skills registered: `relationship_pulse`, `occasion_tracker`, `bill_due_tracker`, `credit_monitor`, `school_calendar`. Total: 13 skills (was 8).

**U-2.4: Occasion-Aware Social Prompt**
- **`prompts/social.md`** — Added "Occasion-Aware Intelligence" section (schema v1.1): 3-day priority lane (🔴 URGENT block for imminent birthdays/festivals/anniversaries), circle cross-reference protocol (channel + tone per circle type), 8 WhatsApp message templates (birthday peer, birthday elder, birthday child, Diwali, Holi, Raksha Bandhan, Eid, reconnect), and structured briefing output format with 🔴/🟠/🟡 windows.

### Fixed
- **`tests/conftest.py`** — Added `_PROJECT_ROOT` (project root directory) to `sys.path` alongside the existing `_SCRIPTS_DIR`. Previously, tests using `from scripts.skills.*` imports only worked when run as part of the full suite (order-dependent via other imports); isolated runs (`pytest tests/unit/test_util_skills.py`) failed with `ModuleNotFoundError: No module named 'scripts.skills'`. Both `from skills.X import Y` and `from scripts.skills.X import Y` import styles now work in all invocation modes.

### Tests
- Added **27** new tests in `tests/unit/test_util_skills.py`:
  - `TestRelationshipPulse` (6 tests) — stale contact detection, fresh contact exclusion, `nudge: false` circle skip, never-contacted contact inclusion, missing file graceful degradation, `to_dict()` summary format.
  - `TestOccasionTracker` (7 tests) — upcoming birthday detection, past birthday exclusion, festival window detection, anniversary 30-day window, empty occasions graceful return, 🔴 severity within 3 days, `to_dict()` summary.
  - `TestBillDueTracker` (5 tests) — 🟠 severity 3-day bill, 🔴 severity 1-day bill, 30-day bill not surfaced, `_parse_bill_date()` monthly format, missing occasions file graceful return.
  - `TestCreditMonitor` (4 tests) — hard inquiry detection, fraud alert prioritization, clean content no alerts, missing state files empty return.
  - `TestSchoolCalendar` (5 tests) — school keyword event detection, non-school event exclusion, grade alert detection from kids.md, missing files empty return, `to_dict()` summary.

**Total test count: 1069 (+27 from baseline 1042)**

---

## [7.0.4] — 2026-03-15

### Added — Catch-Up Quality Hardening (P0/P1/P2)

**P0: Critical Reliability Fixes**
- **`scripts/preflight.py`** — `check_msgraph_token()` now attempts proactive refresh when the token is already expired (`secs_left < 0`), not just within 5 minutes; previously an expired token silently skipped the refresh branch and caused the entire catch-up to fail with 401 errors.
- **`scripts/preflight.py`** — Added `_is_bootstrap_stub(path)` helper that detects un-populated YAML template stubs (files containing `# Content\nsome: value` fingerprint). `check_state_templates()` now reports and, with `--fix`, replaces stubs — in addition to creating missing files. Summary line now reads "created N + replaced M bootstrap stubs".
- **`scripts/migrate_oi.py`** (new) — Idempotent one-time backfill scanner: reads all `state/*.md` files, extracts `OI-NNN` references, and appends any missing IDs to `state/open_items.md` as `-[ ] OI-NNN (backfilled)` entries. Reports the highest OI-NNN seen so users can update the next-ID counter in `state/memory.md`. Supports `--dry-run`.

**P1: Workflow Script Coverage**
- **`scripts/health_check_writer.py`** (new) — Atomic writer for `state/health-check.md` frontmatter. Non-blocking 3-second file lock (`state/.health-check.lock`); detects and replaces bootstrap stubs; upserts individual YAML keys while preserving unknown fields; rotates connector log blocks older than 7 days to `tmp/connector_health_log.md`; writes via `os.replace()` for atomicity. CLI: `python3 scripts/health_check_writer.py --last-catch-up ISO --email-count N --domains-processed a,b,c --mode normal`.
- **`config/workflow/finalize.md`** — Step 16 rewritten: replaces the AI-manual YAML instruction with a direct `health_check_writer.py` CLI call, eliminating frontmatter corruption from hand-edited YAML.
- **`config/workflow/finalize.md`** — Step 11c updated to include `python3 scripts/calendar_writer.py --input tmp/pipeline_output.jsonl` for automatic calendar state persistence.
- **`scripts/preflight.py`** — Added 48-hour advance advisory for MS Graph token expiry (`TOKEN_ADVANCE_WARN_SECONDS = 172800`). When `0 < secs_left < 172800`, emits a P1 advisory "expires in ~Nh; run --reauth before your next session" so users act proactively rather than hitting an expired token mid-catch-up.
- **`scripts/calendar_writer.py`** (new) — Reads pipeline JSONL output (stdin, `--input PATH`, or auto-detects `tmp/pipeline_output.jsonl`), filters for calendar/event records, deduplicates events via SHA-256 fingerprint (`<!-- dedup:KEY -->`), and appends new events to `state/calendar.md`. Recognises `google_calendar`, `gcal`, `outlook_calendar`, `msgraph_calendar`, `caldav_calendar`, and `workiq_calendar` connector output. Detects and replaces bootstrap stubs with a proper calendar YAML schema.

**P2: Noise Reduction & Context Quality**
- **`scripts/email_classifier.py`** (new) — Rule-based marketing email tagger. Whitelist-first: `_IMPORTANT_SENDER_DOMAINS` (USCIS, HDFC, banks, Microsoft, Fragomen, etc.) and `_IMPORTANT_SUBJECT_PATTERNS` (order/shipment, security alerts, immigration, tax) always override marketing signals. Classifies remaining emails by sender patterns and subject keywords into `marketing`, `newsletter`, `promotional`, `social`, or `transactional` categories; sets `marketing: true` on records that are noise. Configurable custom domain whitelist via `artha_config.yaml` `email_classifier.whitelist_domains`.
- **`scripts/pipeline.py`** — Added `_classify_email_lines()` helper that is invoked post-fetch per connector. Falls back silently if `email_classifier` is not importable (safe for fresh installs). Eliminates ~52 % context noise from routine marketing emails.
- **`scripts/session_summarizer.py`** — `get_context_card()` now auto-invokes `fact_extractor.extract_facts_from_summary()` + `persist_facts()` for catch-up commands via `_auto_extract_facts_if_catchup()`. Appends "Facts persisted to memory: N" to the context card when facts are saved. Gated on `harness.agentic.fact_extraction.enabled` config flag; fails silently if `fact_extractor` is unavailable.
- **`config/workflow/process.md`** — Step 5a updated: documents that `pipeline.py` now auto-calls `email_classifier.py`; lists trusted-domain override rules for AI fallback. New Step 5d: context offloading instruction using `context_offloader.offload_artifact('pipeline_output', ...)` referencing `tmp/pipeline_output.jsonl`.

### Tests
- Added 27 new tests in `tests/unit/test_catchup_quality_fixes.py`:
  - `TestEmailClassifier` (10 tests) — whitelist overrides, marketing sender/subject/header detection, category labels, `classify_records` batch method.
  - `TestHealthCheckWriter` (3 tests) — file creation from template, key upsert with existing file, stub detection.
  - `TestCalendarWriter` (5 tests) — calendar record detection, dedup key generation, dedup key scan, writer integration, non-calendar record passthrough.
  - `TestMigrateOI` (4 tests) — OI reference extraction, skip-file list, already-present deduplication, dry-run mode.
  - `TestPreflightBootstrapStub` (5 tests) — stub fingerprint detection, non-stub passthrough, check_state_templates stub reporting, auto-fix stub replacement, fix-count summary line format.
- Total: **1042 tests** (+27 from 1015).

### Specs
- **`specs/artha-prd.md`** → v7.0.4: version table row added.
- **`specs/artha-tech-spec.md`** → v3.9.4: version table row added; §8.12 "Catch-Up Quality Hardening" added (§8.12.1 Email Classifier, §8.12.2 Health Check Writer, §8.12.3 Calendar Writer, §8.12.4 OI Migration, §8.12.5 Preflight Fixes).
- **`specs/artha-ux-spec.md`** → v2.6: version table row added.

---

## [7.0.3] — 2026-03-15

### Added — Agentic Reloaded (specs/agentic-reloaded.md AR-1–AR-8)

**AR-1: Bounded Memory & Consolidation Discipline**
- **`scripts/fact_extractor.py`** — Added `MAX_MEMORY_CHARS = 3_000` and `MAX_FACTS_COUNT = 30` constants; `_load_harness_config()` reads capacity from `artha_config.yaml`; `_consolidate_facts()` enforces dual limits via TTL expiry followed by lowest-confidence eviction; `correction` and `preference` fact types are protected and never evicted.
- **`config/workflow/finalize.md`** — Step 11c expanded with Memory Capacity Check (AR-1) instructions.
- **`config/artha_config.yaml`** — Added `harness.agentic.memory_capacity` config block (`enabled`, `max_chars`, `max_facts`).

**AR-2: Self-Model (AI Metacognition)**
- **`state/templates/self_model.md`** — New template for agent self-model state file (domain confidence, effective strategies, blind spots, interaction patterns; capped at 1500 chars).
- **`config/Artha.core.md`** — Added AR-2 Self-Model protocol section (frozen-layer loading, session-boundary update discipline, 1500-char cap).
- **`config/artha_config.yaml`** — Added `harness.agentic.self_model` config block.

**AR-3: Pre-Eviction Memory Flush**
- **`scripts/session_summarizer.py`** — Added `should_flush_memory(context_text)` function (AR-3 pre-compression flush trigger at `threshold_pct/2`); added `pre_flush_facts_persisted: int = 0` field to both Pydantic and fallback `SessionSummary` classes; `create_session_summary()` accepts `pre_flush_facts_persisted` parameter.
- **`config/Artha.core.md`** — Added AR-3 Pre-Compression Memory Flush protocol section.
- **`config/artha_config.yaml`** — Added `harness.agentic.pre_eviction_flush` config block.

**AR-4: Cross-Session Search & Recall**
- **`scripts/session_search.py`** (new) — Grep-based full-text search over `briefings/` and `summaries/`; `SearchResult` dataclass; relevance scoring (`match_count / sqrt(line_count)`); PII-safe excerpts; `format_results_for_context()` renderer; CLI entry point.
- **`scripts/artha_context.py`** — Added `session_recall_available: bool = False` field to `ArthaContext`.
- **`config/workflow/reason.md`** — Added Pre-OODA Cross-Session Recall block with `session_search.py` instructions.
- **`config/artha_config.yaml`** — Added `harness.agentic.session_search` config block.

**AR-5: Procedural Memory**
- **`scripts/procedure_index.py`** (new) — Scan `state/learned_procedures/*.md` frontmatter; `ProcedureMatch` dataclass; `_decay_confidence()` with 90-day decay interval and 0.5 floor; `find_matching_procedures()` with min_confidence/min_relevance filters; `format_procedures_for_context()`; CLI entry point.
- **`state/learned_procedures/README.md`** (new) — Directory for agent-discovered reusable procedures (git-tracked, not backup-tracked).
- **`config/workflow/finalize.md`** — Step 11c Procedure Extraction (AR-5) instructions.
- **`config/workflow/reason.md`** — Pre-OODA Procedure Lookup (AR-5) instructions.
- **`config/artha_config.yaml`** — Added `harness.agentic.procedural_memory` config block.

**AR-6: Prompt Stability Architecture**
- **`scripts/generate_identity.py`** — Added `PROMPT STABILITY` frozen-layer comment to generated `config/Artha.md` header.
- **`config/Artha.core.md`** — Added AR-6 Prompt Stability Architecture section (frozen vs ephemeral layer classification, 4 usage rules).

**AR-7: Delegation Protocol**
- **`scripts/delegation.py`** (new) — `DelegationRequest` / `DelegationResult` dataclasses; `should_delegate()` (step threshold ≥5, parallel, isolated); `compose_handoff()` with context compression to ≤500 chars; `evaluate_for_procedure()` to surface delegation patterns as AR-5 candidates; `is_delegation_enabled()`; CLI smoke-test.
- **`config/Artha.core.md`** — Added Delegation Protocol (AR-7) section.
- **`config/artha_config.yaml`** — Added `harness.agentic.delegation` config block (`default_budget`, `max_budget`, `fallback_mode`).

**AR-8: Root-Cause Before Retry**
- **`config/Artha.core.md`** — Added Root-Cause Before Retry section (4-step diagnosis, anti-pattern contrast, AR-5 evaluation trigger).
- **`config/workflow/fetch.md`** — Added AR-8 Connector Failure Root-Cause Protocol (6-step classify/retry/log).

**Audit & Observability**
- **`scripts/audit_compliance.py`** — Added `_check_memory_capacity()` (weight 5, verifies `state/memory.md` ≤30 facts AND ≤3000 chars; advisory pass when file absent) and `_check_prompt_stability()` (weight 5, verifies `PROMPT STABILITY` marker in `config/Artha.md`; advisory pass when file absent); both checks added to `audit_latest_briefing()`.

### Tests
- Added 72 new tests across 4 test files:
  - `tests/unit/test_fact_extractor.py` — 8 AR-1 tests for `_consolidate_facts()` and `persist_facts()` capacity enforcement (protects `correction`/`preference` types, evicts expired + lowest-confidence).
  - `tests/unit/test_session_search.py` (new) — 14 tests for `session_search.py` (match, no-match, empty query, ranking, excerpts, multi-term, disabled flag, `format_results_for_context`).
  - `tests/unit/test_procedure_index.py` (new) — 19 tests for `procedure_index.py` (`list_procedures`, `_decay_confidence`, `find_matching_procedures`, `format_procedures_for_context`).
  - `tests/unit/test_delegation.py` (new) — 31 tests for `delegation.py` (`should_delegate`, `compose_handoff`, `DelegationRequest.to_prompt`, `evaluate_for_procedure`, `_compress_context`, `is_delegation_enabled`).
- Total: **1015 tests** (+72 from 943).

---

## [7.0.2] — 2026-03-15

### Fixed
- **`scripts/vault.py`** — `do_health()` now uses a 3-exit-code model: exit 0 (fully healthy), exit 1 (hard failure — age binary, key, or state dir), exit 2 (soft warnings only — orphaned `.bak` files, GFS never validated, key never exported). Previously `.bak` files set `ok=False` → exit 1, which blocked catch-up entirely.
- **`scripts/preflight.py`** — `check_vault_health()` handles vault exit 2 as a P1 advisory (non-blocking). Extracts the `⚠ .bak` warning line for accurate display; fix hint now correctly reads `python3 scripts/vault.py encrypt`.
- **`scripts/preflight.py`** — `check_vault_lock()` auto-clears stale locks unconditionally (no `--fix` required). Checks PID liveness in addition to age threshold. All error messages now show the actual lock file path instead of the generic `~/.artha-decrypted`.
- **`scripts/detect_environment.py`** — `detect_json()` emits compact single-line JSON when stdout is not a TTY (pipeline-safe), and indented JSON when printing to a terminal. Added `--pretty` CLI flag for explicit formatting.
- **`scripts/connectors/google_calendar.py`** — `_parse_event()` no longer includes raw attendee email addresses in JSONL output. Attendees are now `{name, self}` only; display name falls back to the username portion of the email address.
- **`config/Artha.core.md` + `config/Artha.md`** — All `python scripts/` references updated to `python3 scripts/` for macOS/Linux consistency; added prescriptive `alias python=python3` note in §1; vault §1 note updated for cross-platform; Step 0 clarified: stale locks and orphaned `.bak` files are crash-state that auto-resolves and never constitutes a blocking P0.

### Tests
- Added 7 targeted regression/contract tests: `test_vault_health_bak_files_exit_2_not_1`, `TestVaultHealthExitCodes` (3 tests covering exit 0/1/2 → P0/P1 mapping), `TestVaultLockAutoClean` (3 tests covering stale-by-age, stale-by-dead-PID, no-lock). Total: 943 tests (+7).

---

## [7.0.1] — 2026-03-15

### Fixed
- **`scripts/middleware/__init__.py`** — `_ComposedMiddleware.before_write` now accepts and forwards the `ctx` optional parameter to all child middlewares. Previously `ctx` was silently dropped, causing `TypeError` when callers passed `ctx=` to a composed chain.
- **`tests/unit/test_middleware.py`** — Updated all 5 test-mock `before_write` signatures to include `ctx=None`, matching the `StateMiddleware` Protocol contract.

---

## [7.0.0] — 2026-03-15

### Added — Agentic Intelligence (specs/agentic-improve.md Phases 1–5)

- **Phase 1 — OODA Reasoning Protocol** (`config/workflow/reason.md` Step 8, `scripts/audit_compliance.py`):
  - Step 8 rewritten as structured Boyd OODA loop: **8-O OBSERVE** → **8-Or ORIENT** → **8-D DECIDE** → **8-A ACT**.
  - OBSERVE reads `state/memory.md` correction/pattern/threshold facts from prior sessions.
  - ORIENT builds 8-domain cross-connection matrix + compound-signal detection.
  - DECIDE applies U×I×A scoring (1–3 scale) to rank every item; selects ONE THING.
  - ACT includes consequence forecasting (8-A-2), FNA pipeline (8-A-3), dashboard rebuild (8-A-4), PII stats (8-A-5).
  - `audit_compliance.py`: added `_check_ooda_protocol()` (weight=10); briefings must contain ≥3/4 OODA phase markers to pass. 6 new audit tests.

- **Phase 2 — Tiered Context Eviction** (`scripts/context_offloader.py`):
  - `EvictionTier(IntEnum)`: PINNED=0 (never evict), CRITICAL=1 (1.0×threshold), INTERMEDIATE=2 (1.0×threshold), EPHEMERAL=3 (0.4×threshold — aggressive).
  - `_ARTIFACT_TIERS` dict: 8 predefined artifact-to-tier mappings; unknown artifacts → INTERMEDIATE.
  - `offload_artifact()` gains `tier` param; feature-flagged via `harness.agentic.tiered_eviction.enabled`.
  - `.checkpoint.json` added to `OFFLOADED_FILES`.
  - `config/artha_config.yaml`: `harness.agentic:` namespace with 4 sub-flags (all `enabled: true`).
  - 9 new eviction tests.

- **Phase 3 — ArthaContext Typed Runtime Carrier** (`scripts/artha_context.py` — new):
  - `ContextPressure(str, Enum)`: GREEN / YELLOW / RED / CRITICAL.
  - `ConnectorStatus(BaseModel)` + `ArthaContext(BaseModel)`: 9 fields, `connectors_online`/`connectors_offline` properties, `health_summary()` method.
  - `build_context(command, artha_dir, env_manifest, preflight_results) → ArthaContext` builder.
  - `scripts/middleware/__init__.py`: `StateMiddleware.before_write()` gains `ctx: Any | None = None` (backward compatible).
  - Feature flag: `harness.agentic.context.enabled`. 25 new context + middleware tests.

- **Phase 4 — Implicit Step Checkpoints** (`scripts/checkpoint.py` — new):
  - `write_checkpoint()` / `read_checkpoint()` / `clear_checkpoint()` utilities.
  - Writes `tmp/.checkpoint.json` after Steps 4, 7, 8; clears in Step 18 cleanup.
  - 4-hour TTL: stale checkpoints (>4h old) ignored on resume.
  - `config/workflow/preflight.md`: Step 0a "Check for Resumable Session" added — auto-resumes in pipeline mode; interactive prompt otherwise.
  - `config/workflow/finalize.md` Step 18: `.checkpoint.json` added to `rm -f` cleanup + programmatic `clear_checkpoint()` call.
  - Feature flag: `harness.agentic.checkpoints.enabled`. 21 new checkpoint tests.

- **Phase 5 — Persistent Fact Extraction** (`scripts/fact_extractor.py` — new):
  - Extracts 5 fact types (correction, pattern, preference, threshold, schedule) from `tmp/session_history_*.md` summaries via regex signal detectors.
  - PII stripped (phone/email/SSN) before persistence; SHA-256 dedup (last 16 hex chars as ID).
  - Persists to `state/memory.md` v2.0 YAML frontmatter (`schema_version: '2.0'`, `facts: []`).
  - `config/workflow/finalize.md`: Step 11c "Persistent Fact Extraction" added (before Step 12).
  - `state/memory.md`: upgraded from placeholder to v2.0 schema.
  - `state/templates/memory.md`: new template file.
  - Feature flag: `harness.agentic.fact_extraction.enabled`. 38 new fact tests.

- **`config/Artha.core.md`**: `harness_metrics:` block extended with `agentic:` sub-section (eviction_tiers, session, checkpoints, facts, ooda telemetry).

### Tests
- +120 new tests; **936 total** (was 816, 5 skipped, 20 xfailed)
- New test files: `tests/unit/test_artha_context.py`, `tests/unit/test_checkpoint.py`, `tests/unit/test_fact_extractor.py`
- Updated test files: `tests/unit/test_context_offloader.py`, `tests/unit/test_audit_compliance.py`

### Specs
- PRD v6.1 → **v7.0** (F15.128–F15.132 added)
- Tech Spec v3.8 → **v3.9** (§8.10 Agentic Intelligence Modules added)
- UX Spec v2.2 → **v2.3** (Implements PRD v7.0, Tech Spec v3.9)

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
