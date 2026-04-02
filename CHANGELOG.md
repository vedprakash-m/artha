# Changelog

All notable changes to Artha are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added — Eval Spec v1.4.0 (specs/eval.md)
- DD-17: Schema versioning policy — all eval artifacts carry `schema_version` with semver, validated by MetricStore on load
- DD-18: Dynamic domain expectations — `_load_expected_domains()` reads `config/domain_registry.yaml` instead of hardcoded list
- §7.5: Eval Data Privacy Classification — artifact-level PII/sensitivity table, `.gitignore` audit checklist
- EV-0d: Eval dashboard — `print_summary()` function with `--summary` / `--summary --json` CLI flags
- Reading guide at top of TOC for audience-specific navigation
- Risks R21 (schema version drift across devices) and R22 (domain registry out of sync)
- Assumptions A-23 (`domain_registry.yaml` exists) and A-24 (no existing `schema_version` fields)
- `schema_version: '1.0.0'` added to all Appendix B schema examples (B.1–B.4)
- 10 new tests (3 EV-0d + 4 DD-17 + 3 DD-18) — total 127 tests across 26 files

### Added — Action Layer v1.4: Orchestrator + Signal Extraction (specs/actions-reloaded.md)
- `scripts/action_orchestrator.py`: new top-level CLI combining email signal extraction, pattern engine, AI signals, dedup, compose/propose, approve/reject/defer/expire lifecycle, and audit log into one command (`--run`, `--list`, `--show`, `--approve`, `--reject`, `--defer`, `--approve-all-low`, `--expire`, `--health`)
- `scripts/email_signal_extractor.py`: expanded from 1-pattern to 9-category signal coverage — RSVP deadlines, appointment confirmed, bill due, form deadline (+ cancellation forms), delivery arrival (including "arriving [date]" variants), security alerts, subscription renewals, school action needed (missing assignments, surveys, events), financial alerts (INR/NEFT/UPI transactions)
- `scripts/action_queue.py`: `find_by_prefix()` method — resolves 8-char ID prefix across all non-terminal statuses, enabling `--reject` and `--show` on deferred items; `deferred → rejected` added to state machine transitions (§2.4)
- `scripts/action_composer.py`: `school_action_needed` remapped `email_reply → reminder_create` (eliminates spurious `thread_id` handler validation failure); added `bill_due`, `financial_alert`, `subscription_renewal` entries to `_FALLBACK_SIGNAL_ROUTING`
- `config/signal_routing.yaml`: `school_action_needed` updated to `reminder_create`, `bill_due` friction lowered to `standard`, `financial_alert` entry added
- `scripts/email_signal_extractor.py`: `_extract_text()` now scans `body` field in addition to `body_preview` (captures Amazon order "Arriving April 8" and other body-only content)
- E2E validated on 7-day real pipeline data (750 records: WhatsApp, Gmail, Outlook, OneNote, HomeAssistant, RSS): 14 signals extracted, 8 unique after dedup, 4 proposals queued
- `tests/unit/test_action_orchestrator.py`, `tests/unit/test_action_orchestrator_security.py`, `tests/integration/test_action_orchestrator.py`: full test coverage for new orchestrator (4,014 tests total, all passing)
- `specs/actions-reloaded.md`: canonical spec for Action Layer v1.4 — now committed to repo

### Fixed — Action Layer
- `_resolve_id()` previously returned raw 8-char prefix when action was deferred, causing `transition()` to raise "Action not found"; now uses `find_by_prefix()` to search all active statuses
- `cmd_show()` had separate duplicate prefix-matching logic; consolidated to use `_resolve_id()` — deferred items now visible via `--show`
- State machine blocked `deferred → rejected`; adding `rejected` to allowed transitions from `deferred` enables users to discard deferred items without un-deferring first
- Pipeline `--output` write timing issue investigated and resolved: full multi-connector run (750 records, 135s) confirmed to write atomically

### Added — Kusto Live Data Pipeline (Tier 1)
- `scripts/kusto_runner.py`: `run_refresh_set()` batch API — runs curated golden queries (GQ-001, 002, 010, 012, 050, 051) and returns structured results for pipeline consumption
- `scripts/work_loop.py`: Kusto provider integration — `_check_kusto_available()` in preflight, `_run_kusto_metrics()` in fetch stage, wired into `_write_domain_files_from_connector_data()`
- `scripts/work_domain_writers.py`: `write_kusto_metrics_state()` — in-place regex update of xpf-program-structure.md metric values (M01 fleet size, M04/M05 velocity, M06 throughput, M20 incidents) with Kusto-live timestamps
- `ProviderAvailability.kusto` field added to work loop data model

### Added — PF Join Pattern & Testing (Tier 3)
- PF cross-cluster join pattern for 12 Env_Machines golden queries (GQ-021, 024, 041, 060, 062–066, 068, 091, 121) — `let PF_Clusters = cluster('xdeployment...').database('Deployment').TenantCatalogSnapshot | where IsPF == true | distinct ClusterName` then `| where AutoGen_Cluster in (PF_Clusters)`
- 55 new tests: `test_kusto_metrics_writer` (19), `test_program_metrics` (10), `test_pulse_health` (5), `test_meeting_context` (12), `test_kusto_refresh_set` (9) — total suite now 1,060 passing
- `/work notes [meeting-id]` command wired into `work_reader.py` dispatch — reads `work-notes.md`, supports meeting-id search, suggests WorkIQ for missing notes
- XPF staleness advisory in data freshness footer — `_xpf_staleness_hours()` and `_xpf_staleness_advisory()` in `scripts/work/helpers.py`; surfaces when `xpf-program-structure.md` is >18h stale (PULL > PUSH pattern)
- WorkIQ lookup hint in `/work people` when person not found locally

### Changed — Program-Aware Narrative Output (Tier 2)
- `scripts/narrative_engine.py`: `_load_program_metrics()` parser — extracts signal summary, risk posture, per-workstream signals, and red metrics from xpf-program-structure.md
- `scripts/narrative_engine.py`: `generate_newsletter()` — new `program_metrics` section with per-WS status, top red risks, risk posture; enriched `executive_summary` with program posture
- `scripts/narrative_engine.py`: `generate_deck()` — exec summary shows program risk, `_section_metrics` renders per-WS status, `_section_risks` surfaces red metrics
- `scripts/narrative_engine.py`: `generate_connect_summary()` — new "Quantitative Evidence (Kusto-validated)" section with green metrics as wins
- `scripts/narrative_engine.py`: `_extract_ws_metrics()` helper for per-workstream metric extraction by signal filter
- `scripts/work/briefing.py`: `cmd_pulse()` — new program health one-liner: `MEDIUM (R:7 Y:24 G:7)`
- `scripts/work/meetings.py`: `cmd_prep()` — XPF program context injection for meetings matching XPF keywords (risk posture + workstream metrics)
- Freshness footer now includes xpf-program-structure.md timestamp across all narrative outputs

### Fixed
- `.vscode/mcp.json`: deduplicated `kusto-xdataanalytics` key (renamed second to `kusto-xdataanalytics-cogs`)
- `scripts/work_domain_writers.py`: cross-platform date formatting (replaced `%-m/%-d` with `f"{now.month}/{now.day}"` for Windows compatibility)
- 12 golden queries: `IsPF_TODO_NEEDS_JOIN` → cross-cluster PF join via ClusterName (validated live — GQ-060 confirmed)
- 12 golden queries: `TIMESTAMP` → `AutoGen_TimeStamp` for all Env_Machines table queries

### Updated — Specs & Status
- `config/implementation_status.yaml`: added `work_os_kusto_integration` entry; updated `work_os_domain_writers` from partial→implemented (7 writers + 3 appenders); updated notes for pulse/prep/newsletter/deck/connect-prep commands to reflect program metrics integration

---

## [8.5.0] — 2026-03-27

### Changed — PAY-DEBT v1.0: Structural Integrity Plan (specs/pay-debt.md)

Eliminated three 1,800–3,500-line god files, added structured observability, hardened registries, schemas, and the PII guard pipeline. All 2,886 baseline tests preserved; ~328 new tests added.

**Phase 1 — Foundation Hardening (TD-5, TD-10, TD-11)**
- `foundation.py`: added `get_config()` accessor — safe alternative to module-level aliases frozen at import time
- `scripts/health_check_updater.py` (new): extracted `update_channel_health_md()` from `lib/common.py`; deprecated stub re-export kept for backward compatibility
- `middleware/audit_middleware.py`: replaced silent `except OSError: pass` with `print("[WARN] audit write failed: ...", file=sys.stderr)` — audit trail gaps now visible

**Phase 2 — Observability Layer (TD-4)**
- `scripts/lib/logger.py` (new, ~120 LOC): structured JSONL logger with daily rotation, 30-day pruning, PII-safe design, and trace_id/correlation_id support
- Integrated into `pipeline.py` (connector.fetch events), `action_executor.py` (action.executed events), and `channel_listener.py` (command.received/completed events)
- Log sink: `~/.artha-local/logs/artha.YYYY-MM-DD.log.jsonl` (machine-local, never cloud-synced)

**Phase 3 — Work OS Restructure (TD-2) ★ HIGHEST PRIORITY**
- `scripts/work_reader.py`: reduced from 3,146 LOC to ~313 LOC facade + `main()`
- Decomposed into 8 focused submodules under `scripts/work/`: `helpers`, `briefing`, `meetings`, `health`, `decisions`, `discovery`, `career`, `narrative`
- All 284 existing work reader tests preserved via strangler-fig facade
- `scripts/narrative_engine.py`: removed 6 duplicate helper functions; now imports from `work.helpers`

**Phase 4 — Channel Listener Restructure (TD-1)**
- `scripts/channel_listener.py`: reduced from 3,543 LOC to ~592 LOC entry point
- Decomposed into 8 focused submodules under `scripts/channel/`: `router`, `handlers`, `catchup`, `llm_bridge`, `security`, `formatters`, `state_readers`, `stage`
- Old `test_channel_listener.py` (24 tests) retained in parallel until behavioral parity confirmed

**Phase 5 — Preflight Restructure (TD-1)**
- `scripts/preflight.py` (1,848 LOC) → `scripts/preflight/` package
- Decomposed into 5 category modules: `vault_checks`, `oauth_checks`, `api_checks`, `state_checks`, `integration_checks`
- `config/Artha.core.md` Step 0 updated from `python3 scripts/preflight.py` to `python3 -m preflight`
- ⚠️ **Breaking change:** If you call `python3 scripts/preflight.py` directly in custom scripts, update to `python3 -m preflight`

**Phase 6 — Registry Consolidation (TD-3)**
- `pipeline.py`: `_HANDLER_MAP` now derived from `connectors.yaml` at startup via `_derive_handler_map()`; `_FALLBACK_HANDLER_MAP` + `_ALLOWED_MODULES` frozenset provide fail-degraded safety + security gate
- `action_executor.py`: same pattern for `_derive_action_map(connectors.yaml)`
- `action_composer.py`: added `_validate_routing_table()` — catches unknown action_type at import time, not signal-fire time
- `connectors.rss_feed` added to `_ALLOWED_MODULES` (feed connector was deployed but missing from security allowlist)
- Handler map derivation fixed: reads `fetch.handler` path stem instead of falling back to YAML connector name (resolves gmail → google_email mismatch)

**Phase 7 — Hardening (TD-7, TD-8)**
- `scripts/lib/state_schema.py` (new, ~80 LOC): lightweight required-field validator for state file frontmatter
- `middleware/write_guard.py`: `before_write()` now calls `validate_frontmatter()` — blocks writes missing required fields in registered schemas
- `action_executor.py`: PII check now calls `pii_guard.scan()` in-process instead of `subprocess.Popen`; saves ~100ms per action invocation

### Fixed
- `_derive_handler_map` false SECURITY warnings for legitimate connectors (7 → 0) with real `connectors.yaml`
- `connectors.rss_feed` was enabled in `connectors.yaml` and the script existed, but absent from `_ALLOWED_MODULES` — would have caused runtime rejection
- Unused `from pathlib import Path` import removed from `health_check_updater.py`



### Added — Memory Pipeline Activation (MEM v1.3.0, specs/mem.md)

Fixed 4 independent breaks that left Artha's 9-script memory subsystem (3,274 lines) fully coded but operationally inert — `state/memory.md` had `facts: []`, `state/self_model.md` was template-only, and `state/learned_procedures/` was empty after 10+ catch-ups.

**Break 1 — Deterministic pipeline trigger (`scripts/post_catchup_memory.py`, new ~240 LOC):**
- `run(briefing_path, artha_dir, dry_run) -> dict` — 5-step pipeline invoked deterministically from Step 11c
- Extracts facts DIRECTLY from briefing (bypasses lossy `SessionSummary.to_markdown()` round-trip)
- Steps: write session history (search/recovery) → extract facts → persist → update self-model → log to `state/memory_pipeline_runs.jsonl`
- CLI: `--briefing PATH` (required), `--dry-run`, `--discover` (bootstrap/manual only), `--rebuild-self-model`
- Config kill switch: `harness.agentic.post_catchup_memory.enabled` (default: true)

**Break 2 — Dual-path briefing parser (`scripts/fact_extractor.py` extended):**
- `_parse_briefing_md(content)` handles both Telegram (`━━` box-drawing delimiters) and Markdown (`## CRITICAL/URGENT/BY DOMAIN` headings)
- `extract_facts_from_summary()` renamed `summary_path→input_path`; 3-branch format auto-detection
- Previously returned 0 facts on any briefing file

**Break 3 — Health-check run parser (`scripts/self_model_writer.py` extended):**
- `_parse_catchup_runs_from_markdown(content)` reads `## Catch-Up Run History` freeform section
- `SelfModelWriter.update()` tries YAML frontmatter first, falls back to markdown parser
- Previously: `catch_up_runs = []` → `len < 5` gate → `update()` always returned `False`

**Break 4 — Briefing-aware extraction signals (Phase 1b, `scripts/fact_extractor.py`):**
- 4 new `_EXTRACTION_SIGNALS`: deadline (30d TTL), decision-pending (60d), `$N/mo` threshold (90d, finance), OI-NNN pattern (90d)
- Previously: 0 `preference`-type facts across all briefings; Effective Strategies section permanently empty

**Domain sensitivity tiering:**
- `_HIGH_STAKES_DOMAINS = frozenset({"finance", "health", "immigration", "insurance", "legal"})`
- `_apply_domain_sensitivity_ttl()` — caps TTL at 90d for non-correction/preference high-stakes facts

**Bootstrap (`scripts/bootstrap_memory.py` + `scripts/bootstrap_seeds.yaml`, new):**
- Batch-seeds `state/memory.md` from all historical briefings (single `persist_facts()` call — order-independent)
- `bootstrap_seeds.yaml` — declarative procedure manifest; initial seed: `digital-onenote-parallel-fetch`

**Wiring:** `config/workflow/finalize.md` Step 11c rewritten to deterministic script invocation; `config/artha_config.yaml` updated.

**Validated state:** `state/memory.md` populated, `state/self_model.md` live, procedure seeded, 18/18 E2E checks pass, 1546 tests passing.

---

## [8.3.0] — 2026-03-21

### Added — ACT-RELOADED: Sense–Reason–Act–Learn (E1–E16, specs/act-reloaded.md v1.3.0)

Evolved Artha from a **read–reason–act** system to a **sense–reason–act–learn** system with 16 enhancements across 3 waves.

**Wave 1 — New signal pipelines:**
- **`scripts/email_signal_extractor.py`** — Deterministic Step 6.5 signal extraction from email body: 8 categories (RSVP, appointment, payment notice, form deadline, shipment arrival, security alert, renewal notice, school action needed). 6 new `_SIGNAL_ROUTING` entries in `action_composer.py`.
- **`scripts/attachment_router.py`** — Filename-pattern-based attachment routing to domains (finance, health, immigration, kids, insurance, employment, home). `AttachmentSignal` dataclass. PII scrubbing from filenames. `to_domain_signals()` converter.
- **`scripts/pattern_engine.py`** + **`config/patterns.yaml`** — Deterministic cross-domain pattern engine with 8 named patterns and 6 operators (`days_until_lte`, `lt/gt/eq`, `exists`, `has_item_within_days`, `contains`, `stale_days`). Replaces prose rules in Step 8. Cooldown-state isolation per `root_dir`.

**Wave 2 — Intelligence loop:**
- **`scripts/briefing_adapter.py`** — Adaptive briefing with `BriefingConfig` dataclass; 6 rules (R1–R6); 10-run cold-start gate; transparency footer.
- **`scripts/nudge_daemon.py`** — Vault-watchdog bridge for proactive notifications: 5 nudge types; 3/day cap; marker-file dedup at `tmp/nudge_*.marker`; no vault access when session inactive.
- **`channel_listener.py`** `/remember` command — Async knowledge capture: PII-guarded writes to `state/inbox.md`; 5/hour write rate limit; full-scope only; `CHANNEL_REMEMBER` audit event.

**Wave 3 — Activation enhancements:**
- **`scripts/self_model_writer.py`** — Memory→self-model pipeline; 3000-char bounded `state/self_model.md`.
- **`scripts/decision_tracker.py`** — `capture_from_command()`, `persist_proposal()`, `_load_decisions()`.
- **`scripts/relationship_pulse_view.py`** + **`state/relationships.md`** — View backing the existing `/relationships` command.
- **`scripts/power_half_hour_view.py`** — Powers the `/power` command: top OI by impact×urgency, today's calendar, intention prompt.
- **`scripts/retrospective_view.py`** — Monthly retrospective generator reading `summaries/` + state files.
- **`scripts/coaching_engine.py`** — `select_nudge(goals, memory_facts, health_history, preferences)`; 4 strategy types; moved from Step 19 → Step 8.
- **`scripts/skills/subscription_monitor.py`** extended — 4 new lifecycle detectors (trial_ending, cancellation_window, annual_review, duplicate_subscription); 5 new signal types.
- **`scripts/actions/whatsapp_cloud_send.py`** — WhatsApp Cloud API Phase 2: `SUPPORTED_TEMPLATES` frozenset; `urllib`-based HTTP; `validate()` → `tuple[bool, str]`.
- **`channel_push.py`** `_build_family_flash()` — Condensed family-scope Telegram digest.
- **`scripts/cost_tracker.py`** + `/cost` command — Per-session API cost estimation.
- **`state/inbox.md`** + **`state/relationships.md`** — New state files.
- **`config/artha_config.yaml`** `enhancements.*` flags — Feature flag namespace for all 16 enhancements.

### Fixed
- **`scripts/pattern_engine.py`** `_load_yaml_file()` — Switched from `yaml.safe_load()` to `next(yaml.safe_load_all(), None)` to handle Markdown files with YAML frontmatter without raising `ComposerError`.
- **`scripts/power_half_hour_view.py`** `_parse_open_items()` — Correct YAML list item extraction with `yaml.safe_load(block)[0]` instead of broken `re.sub` stripping.
- **`scripts/cost_tracker.py`** `_load_pipeline_metrics()` — Handle list-formatted JSON output (wraps list in `{"runs": [...]}` dict).

**Tests:** 1520 passed, 0 failures (5 skipped, 20 xfailed). +151 new tests across 16 new test files.

---

## [8.2.0] — 2026-03-21

### Added — Home Assistant IoT Integration (ARTHA-IOT, PRD F7.4 + F12.5)

Full Wave 1 + Wave 2 implementation of the IoT/Home Assistant integration spec (`specs/iot.md` v1.4.0).

**Wave 1 — Connector (read-only data ingestion):**
- **`scripts/connectors/homeassistant.py`** (new, ~450 LOC) — HA REST API connector. Calls `GET /api/states`, applies hard-floor privacy filtering (`_EXCLUDED_DOMAINS`: camera, media_player, tts, stt, conversation, persistent_notification, update), strips PII attributes (ip_address, mac_address, entity_picture, access_token), sanitizes device_tracker entities to `home`/`not_home`/`unknown`. LAN self-gating via RFC 1918 IP detection + TCP reachability probe — raises `ConnectorOffLAN` (silent skip) when not on home network. Atomic cache write to `tmp/ha_entities.json` via `tempfile.mkstemp` + `os.replace()`.
- **`scripts/setup_ha_token.py`** (new, ~340 LOC) — Interactive 7-step setup wizard: URL validation → token input (masked) → connectivity test → keyring storage → `connectors.yaml` update → `.nosync` creation → success summary. Atomic YAML write. Rollback instructions printed on every failure.
- **`config/connectors.yaml`** — Added `homeassistant` connector block (`enabled: false`, `auth.method: api_key`, `auth.credential_key: artha-ha-token`, `requires_lan: true`).
- **`scripts/preflight.py`** — Added `check_ha_connectivity()` P1 non-blocking preflight check. Handles off-LAN gracefully.
- **`prompts/home.md`** — Added "Smart Home / IoT" section: activation conditions, briefing formats for CRITICAL/MONITORED/energy signals, signal-to-action routing table, privacy notes.

**Wave 2 — Skill (deterministic alerting):**
- **`scripts/skills/home_device_monitor.py`** (new, ~560 LOC) — Deterministic device health monitoring skill. Reads `tmp/ha_entities.json`, applies threshold checks: device offline >2h (`security_device_offline` for Ring/lock/alarm, `device_offline` for monitored), printer supply <20% (`supply_low`), swim spa temp variance >5°F (`spa_maintenance`), power spike >30% above weekly average (`energy_anomaly`). Constructs `DomainSignal` objects directly — bypasses LLM mediation for deterministic signals. Serializes via `dataclasses.asdict()`. Atomic `state/home_iot.md` write.

**Framework wiring:**
- **`scripts/pipeline.py`** — `"connectors.homeassistant"` added to `_HANDLER_MAP`.
- **`scripts/skill_runner.py`** — `"home_device_monitor"` added to `_ALLOWED_SKILLS` frozenset.
- **`scripts/action_composer.py`** — 6 IoT signal routing entries: `device_offline`, `security_device_offline`, `energy_anomaly`, `supply_low`, `spa_maintenance`, `security_travel_conflict`.
- **`config/skills.yaml`** — `home_device_monitor` entry (`enabled: false`, `priority: P1`, `cadence: every_run`).
- **`config/user_profile.yaml`** — `integrations.homeassistant` consent/preference flags section.

**Auth fix (G1):**
- **`scripts/lib/auth.py`** — Fixed `api_key` branch in `load_auth_context()`: now loads actual credential from keyring when `credential_key` is present. Canvas LMS (per-child key pattern) is unaffected.

**Tests (126 new, 1369 total):**
- `tests/unit/test_homeassistant_connector.py` — 36 tests (LAN detection, privacy, fetch, health_check, cache write, G1 regression)
- `tests/unit/test_home_device_monitor.py` — 67 tests (classification, offline detection, all thresholds, serialization, state write, DomainSignal round-trip)
- `tests/unit/test_setup_ha_token.py` — 23 tests (URL validation, connectivity, token storage, .nosync)

**New spec:** `specs/iot.md` v1.4.0 — all gap resolutions documented (G1 auth, G2 ha_url ownership).

> Feature gated behind `enabled: false` — no runtime impact until activated via `python scripts/setup_ha_token.py`.

---

## [8.1.2] — 2026-03-21

### Fixed — Linux CI: `SystemExit` from keyring not caught in action key helpers

- **`scripts/action_executor.py`** — `_get_privkey()` and `_get_pubkey()` now catch `(Exception, SystemExit)` instead of `Exception` alone. On Ubuntu CI (no D-Bus / no SecretService backend), `keyring.get_password()` raises `NoKeyringError`; `foundation.get_private_key()` caught that as `Exception` then called `die()` → `sys.exit(1)` → `SystemExit(1)`. Because `SystemExit` is a `BaseException` subclass (not an `Exception`), it escaped the existing guard and surfaced in pytest as 8 test failures with `"SystemExit: 1"`. Fixed by widening the guard.
- **`scripts/action_queue.py`** — Same `(Exception, SystemExit)` guard applied to `_get_age_pubkey()`.
- **`scripts/action_executor.py`** — `ActionExecutor.__init__`: `detect()` now called with `skip_network=True` to avoid 3 × 3 s TCP probes per fixture instantiation in tests. Fixed `env_info.get("filesystem_writable")` → `env_info.capabilities.get("filesystem_writable")` (`EnvironmentManifest` is a dataclass, not a dict).

**CI impact**: `pytest (3.11)` and `pytest (3.12)` CI jobs had been failing since the Action Layer commit `0900fcc` (2026-03-19). All 4 intermediate commits (`0900fcc`, `fc0b5a5`, `d08f9ac`, `5aad9dd`) had red CI. All 8 CI jobs green as of `5a3ccf6`.

---

## [8.1.1] — 2026-03-20

### Fixed — Action Handler Module Paths + Mock-patch Compatibility

- **`scripts/actions/__init__.py` + `scripts/action_executor.py`** — `_HANDLER_MAP` paths corrected from `scripts.actions.*` to `actions.*`. Because `scripts/` is on `sys.path`, `importlib.import_module("scripts.actions.email_send")` loaded a *separate* module object from the `actions.email_send` the tests imported. This broke all `patch("actions.email_send.build_service", ...)` mocks and the security allowlist assertion (`v.startswith("actions.")`). Fixed. `specs/act.md §4.3` updated accordingly.
- **`scripts/actions/calendar_create.py`, `calendar_modify.py`, `email_send.py`** — `build_service` and `check_stored_credentials` promoted from lazy (deferred `from google_auth import ...` inside functions) to module-level `try/except ImportError` imports. Required for test mock-patching to work correctly via `patch("actions.X.build_service", ...)`.
- **`scripts/actions/calendar_modify.py`** — `validate()` now returns `"empty updates dict"` instead of the generic missing-parameter message when `updates={}`, matching test expectations (`assert "empty" in reason.lower()`).
- **`tests/unit/test_action_executor.py`** — `test_propose_validation_failure_raises` match string updated from `"Validation"` to `"Handler validation failed"` to match the actual error message raised by `ActionExecutor.propose()`.

---

## [8.1.0] — 2026-03-20

### Added — Messaging Connectors (WhatsApp + iMessage)

Artha's catch-up pipeline now includes local messaging data from WhatsApp Desktop and iMessage without any API keys or network access.

**WhatsApp connector (`scripts/connectors/whatsapp_local.py`)**
- **macOS**: reads `ChatStorage.sqlite` (full message text via CoreData timestamps)
- **Windows**: reads Chromium IndexedDB (LevelDB) via `ccl_chromium_reader`. Message body is AES-encrypted at rest; connector surfaces metadata — sender, group, direction, message type, timestamp
- Dual `_find_db()` paths, automatic platform detection
- Status-broadcast filtering, deduplication by `rowId`, sorted by timestamp descending
- Contact/group name resolution from IndexedDB stores (contacts, chats, group-metadata)
- `health_check()` returns `True` on both platforms when local DB is reachable

**iMessage connector (`scripts/connectors/imessage_local.py`)**
- macOS-only; reads `~/Library/Messages/chat.db`
- Handles nanosecond + second timestamp formats (Apple clock epoch conversion)
- Graceful skip (0 records, no error) on non-macOS platforms
- Requires Full Disk Access for the terminal app (System Settings → Privacy & Security)

**Other changes**
- `scripts/skills/whatsapp_last_contact.py` — added missing `get_skill()` factory (was causing `skill_runner.py` AttributeError)
- `scripts/skills/uscis_status.py` — `get_skill()` now skips approved/closed receipt numbers using 7-line context window + `_TERMINAL_STATUSES` regex; avoids polling USCIS for resolved cases
- `scripts/detect_environment.py` — filesystem write probe: `unlink()` is now best-effort (FUSE mounts on some VMs allow write but not delete)
- `config/connectors.yaml` — `whatsapp_local` + `imessage_local` registrations
- `config/routing.yaml` — `source_routes` section routing messaging sources to comms domain
- `prompts/comms.md` — messaging extraction rules and briefing format template
- `pyproject.toml` — new `messaging` optional dependency group (`ccl_chromium_reader`)
- `.gitignore` — `state/*.db` (actions.db etc.) and `test-write.txt` excluded

---

## [8.0.0] — 2026-03-19

### Added — Action Layer v1.3 (specs/act.md)

Artha evolves from **read–reason–report** to **read–reason–act**. Every alert now resolves to an executable action with a human-gated approval queue, full audit trail, and Telegram inline keyboards.

**Core Infrastructure (Phase 0)**
- **`scripts/action_queue.py`** — SQLite-backed persistent approval queue (`state/actions.db`). WAL mode, ACID transitions, 10-state machine (`pending → approved → executing → succeeded`), immutable audit log, age encryption for sensitive parameters at rest.
- **`scripts/action_executor.py`** — Core engine: `propose()`, `approve()`, `reject()`, `defer()`, `undo()`, `run_health_checks()`. Validation gates (trust + PII + rate limit) checked **before** any state transition to prevent queue corruption. New methods: `propose_direct()`, `get_action()`, `list_pending()`, `list_history()`, `queue` property.
- **`scripts/trust_enforcer.py`** — Trust level gate (0=observe, 1=propose, 2=pre-approve). Hard-coded autonomy floor: `autonomy_floor: true` actions always require human approval regardless of trust level, configuration, or user override. `evaluate_elevation()` and `apply_demotion()` with optional args.
- **`scripts/action_composer.py`** — DomainSignal → ActionProposal mapping (17 signal types). `ActionComposer(artha_dir=artha_dir)` loads config automatically. `compose()` and `compose_workflow()` for multi-step scenarios (address change, tax prep).
- **`scripts/action_rate_limiter.py`** — Per-action-type sliding-window rate limiting from `actions.yaml` config.
- **`scripts/schemas/action.py`** — JSON Schema validation for ActionProposal serialization.
- **`scripts/actions/base.py`** — `ActionHandler` Protocol with 5 methods: `validate`, `dry_run`, `execute`, `health_check`, `build_reverse_proposal`. `ActionProposal`, `ActionResult`, `DomainSignal` dataclasses. State machine constants.
- **`scripts/actions/__init__.py`** — `_HANDLER_MAP` allowlist (8 Phase 1 handlers). No arbitrary module loading.
- **`scripts/foundation.py`** — Added `age_encrypt_string()` and `age_decrypt_string()` wrappers for in-memory string encryption (used by action queue for sensitive parameter storage).
- **`state/health-check.md`** — Added `autonomy:` block with full schema (trust_level, days_at_level, acceptance_rate_90d, critical_false_positives, pre_approved_categories, last_demotion, last_elevation).

**Phase 1 Action Handlers (8 handlers)**
- **`scripts/actions/email_send.py`** — Gmail `drafts.create` / `drafts.send` / `messages.trash`. Draft-first by default. 30-second undo window.
- **`scripts/actions/email_reply.py`** — Reply-to-thread with auto In-Reply-To / References headers. Draft-first.
- **`scripts/actions/calendar_create.py`** — Google Calendar `events.insert`. All-day support, attendees, reminders. 1-hour undo window.
- **`scripts/actions/calendar_modify.py`** — Google Calendar `events.patch` with original-value snapshot for undo.
- **`scripts/actions/reminder_create.py`** — Microsoft Graph To Do `tasks` API. Auto-creates list if not found.
- **`scripts/actions/whatsapp_send.py`** — Phase 1: `wa.me/{phone}?text={encoded}` URL scheme. Phase 2: Cloud API (future).
- **`scripts/actions/todo_sync_action.py`** — Subprocess wrapper for `todo_sync.py --push / --pull / --both`.
- **`scripts/actions/instruction_sheet.py`** — Pure text markdown guide generation, saved to `tmp/instructions/`.

**Workflow Integration**
- **`scripts/preflight.py`** — Added `check_action_handlers()` (P1 check): sweeps expired actions, runs handler health checks at Step 0c.
- **`scripts/channel_listener.py`** — Added `/queue`, `/approve`, `/reject`, `/undo` commands. `_handle_callback_query()` handles `act:APPROVE:{id}`, `act:REJECT:{id}`, `act:DEFER:{id}` inline keyboard responses.
- **`config/actions.yaml`** — Migrated to schema v2.0: all 8 Phase 1 handlers with `min_trust`, `sensitivity`, `timeout_sec`, `retry`, `reversible`, `undo_window_sec`, `rate_limit`, `autonomy_floor`, `pii_allowlist`. Disabled Phase 2 stubs (bill_pay, appointment_book) and run_pipeline (no handler).
- **`config/workflow/finalize.md`** — Added Steps 12.5 (compose proposals from signals), 14.5 (push to Telegram with inline keyboards), 19.5 (action layer summary in audit + briefing footer).

**Tests (110 tests, 7 files)**
- `tests/unit/test_action_queue.py` — Queue lifecycle, state machine, dedup, expiry, audit trail
- `tests/unit/test_action_executor.py` — Propose/approve/reject/defer lifecycle, autonomy floor, handler allowlist
- `tests/unit/test_trust_enforcer.py` — Trust gates, autonomy floor, demotion, elevation
- `tests/unit/test_email_send_handler.py` — Gmail API mock tests, draft/send/undo flows
- `tests/unit/test_calendar_handler.py` — Calendar create/modify handler protocol tests
- `tests/unit/test_pii_firewall_actions.py` — SSN/CC detection, block-not-redact contract
- `tests/unit/test_safety_redteam.py` — Adversarial: prompt injection, trust bypass, handler injection, SQL injection, state machine violations, undo deadline, callback validation

### Changed
- **`config/actions.yaml`** — `run_pipeline` disabled (`enabled: false`): invoke `pipeline.py` directly; no handler in action framework.
- **`scripts/trust_enforcer.py`** — `check()` signature updated to `check(proposal, approved_by, action_config)` for ergonomic use; autonomy floor is enforced structurally regardless of `action_config` contents.

### Security
- Autonomy floor is a **hard-coded structural rule** — not bypassable by config, trust level, or `approved_by` value
- PII firewall runs at enqueue AND at execution (double-scan for modified proposals)
- Handler allowlist (`_HANDLER_MAP`) prevents arbitrary module loading
- All sensitive action parameters encrypted at rest with age
- SQL injection resistance: all queue operations use parameterized queries
- State machine enforced server-side: no client can skip states or transition from terminal states



## [7.0.6] — 2026-03-15

### Fixed — Post-audit runtime fixes

**Skill Runtime (get_skill() factories)**
- **`scripts/skills/relationship_pulse.py`** — Added `get_skill(artha_dir=None)` factory function required by `skill_runner.py` for dynamic loading. Was present as a `BaseSkill` subclass but missing the module-level factory, causing `AttributeError` on every `skill_runner` invocation.
- **`scripts/skills/occasion_tracker.py`** — Same fix: added `get_skill()` factory.
- **`scripts/skills/bill_due_tracker.py`** — Same fix: added `get_skill()` factory.
- **`scripts/skills/credit_monitor.py`** — Same fix: added `get_skill()` factory.
- **`scripts/skills/school_calendar.py`** — Same fix: added `get_skill()` factory.

**Skill Runner Allowlist**
- **`scripts/skill_runner.py`** — Added all 5 new skills to `_ALLOWED_SKILLS` frozenset: `relationship_pulse`, `occasion_tracker`, `bill_due_tracker`, `credit_monitor`, `school_calendar`. Previously they were blocked with `"Skill X is not in the allowlist"` ERROR on every run.

**Location Coordinates**
- **`config/user_profile.yaml`** *(gitignored)* — Added `lat: 47.6162` and `lon: -122.0355` (Sammamish, WA) to `location` section. Previously missing, causing NOAA weather skill to raise `ValueError: NOAA weather skill not configured`.

**Briefing Format — Occasions & Wishes (U-2.5)**
- **`config/briefing-formats.md`** — Added dedicated `━━ 🎂 OCCASIONS & WISHES` section with 🔴/🟠/🟡 urgency windows, greeting suggestion format, and source attribution. Previously occasions data had no dedicated briefing placement.

**Finalize Workflow — Skill Output Integration (U-2.5)**
- **`config/workflow/finalize.md`** — Extended Step 12 with explicit skill-to-section mapping: `relationship_pulse` → RELATIONSHIP PULSE, `occasion_tracker` → OCCASIONS & WISHES (with core_family escalation to 🔴), `bill_due_tracker` + `credit_monitor` → Finance section, `school_calendar` → Kids section.

**Channels Config**
- **`config/channels.yaml`** *(gitignored)* — Created from `channels.example.yaml` template with `telegram.enabled: true`, `bot_username: artha_ved_bot`, `push_enabled: false` (pending bot token). Chat IDs left empty pending `@userinfobot` lookup.

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
- **`scripts/email_classifier.py`** (new) — Rule-based marketing email tagger. Whitelist-first: `_IMPORTANT_SENDER_DOMAINS` (USCIS, financial institutions, HR platforms, government agencies, etc.) and `_IMPORTANT_SUBJECT_PATTERNS` (order/shipment, security alerts, immigration, tax) always override marketing signals. Classifies remaining emails by sender patterns and subject keywords into `marketing`, `newsletter`, `promotional`, `social`, or `transactional` categories; sets `marketing: true` on records that are noise. Configurable custom domain whitelist via `artha_config.yaml` `email_classifier.whitelist_domains`.
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
