# Artha — Personal Intelligence OS
<!-- pii-guard: ignore-file -->
## Product Requirements Document · v7.4.0

**Author:** [Author]
**Date:** March 28, 2026
**Status:** Active Development
**Classification:** Personal & Confidential

> **⚠ Note on Example Data:** All personal names, schools, account numbers,
> and addresses in this document are **fictional examples** used to illustrate
> Artha's capabilities for a representative family (the "Patel" family:
> Raj, Priya, Arjun, Ananya). They do not represent real individuals.
> Your actual family data is configured in `config/user_profile.yaml`.

| Version | Date | Summary |
|---------|------|----------|
| v7.4.0 | 2026-03 | **FW-19 Reflection Loop v1.5.0 — Spec Consolidation:** Reflection Loop spec (specs/reflection-loop.md v1.5.0) fully reviewed through 6 architectural iterations (v1.0→v1.5). Key features consolidated into canonical specs: 8-step pipeline (sweep→extract→score→reconcile→synthesize→draft→persist), three-tier progressive summarization (live/archive/compacted), `CompactionManifest` for idempotent multi-file compaction, `ReflectionKey` for crash-safe retry, `reconcile.py` two-pass deterministic+LLM strategy, `ReflectReader` typed facade for downstream integration, `write_state_atomic()` with `os.replace()` cross-platform contract, UUID+timestamp concurrency guard, additive scoring model (eliminates Deep Work escape hatch), carry-forward hard-decay policy (3-carry parking lot), 82-week backfill from work-scrape corpus, audit JSONL with sequence numbers and truncation guard. New modules: `scripts/work/reflect.py`, `scripts/work/sweep.py`, `scripts/work/scoring.py`, `scripts/work/reconcile.py`, `scripts/work/reflect_reader.py`, `scripts/work/compaction_manifest.py`, `scripts/work/reflection_key.py`, `scripts/backfill/`. 1,375 work tests passing. PRD FW-19 updated. Tech Spec §19.11 Reflection Loop technical architecture added. UX Spec §23.14 Reflection Loop UX added. `specs/reflection-loop.md` archived to `.archive/specs/reflection-loop.md`. |
| v7.3.0 | 2026-03 | **Spec Consolidation — Work OS FW-18 Product Knowledge Domain:** Full FW-18 specification (`specs/work-products.md`) incorporated into canonical specs. PRD FR-19 FW-18 row updated (archived-spec reference removed). Tech Spec §19.10 Product Knowledge Domain technical architecture added. UX Spec §23.13 Product Knowledge Domain UX added. `specs/work-products.md` archived to `.archive/work-products.md`. |
| v7.2.0 | 2026-03 | **PAY-DEBT-RELOADED v2.0 — Infrastructure Hardening (WS-1–WS-9-B):** Zero user-visible feature changes; pure infrastructure and test-quality hardening across 9 workstreams. **WS-1 (`work_loop.py` DAG fix):** Silent short-circuit on empty connector result eliminated — 7-stage DAG stages 3–7 now execute unconditionally even when a connector returns `[]`; previously work enrichment never ran when any connector returned an empty list. **WS-2 (centralized config loader):** `scripts/lib/config_loader.py` — `load_config(name, _config_dir=None)` LRU-cached YAML loader; `_config_dir` param is a secondary cache key enabling per-test temp-dir isolation without monkeypatching; `invalidate()` clears all cached entries; `_CONFIG_FILES` registry maps 10 canonical config names to YAML filenames; all 92 direct `yaml.safe_load()` call sites in Work OS scripts replaced with `load_config()`; `tests/conftest.py` autouse `_clear_config_cache(tmp_path)` fixture. **WS-3 (narrative decomposition):** `scripts/narrative_engine.py` (1,766-LOC god-file) decomposed into `scripts/narrative/` bounded-context package — `__init__.py` thin backward-compatible shim, `templates.py` (template registry), `renderer.py` (Jinja2 + markdown render pipeline), `context.py` (context assembly + section builders); all 10 template names re-exported from `__init__.py`; original import path preserved for all callers. **WS-4 (architectural boundary rules):** `tests/unit/test_architectural_boundaries.py` — 7 enforced rules (AST-level import scanner): no cross-domain imports, no direct connector imports from non-connector modules, no connector upward/cross imports, no direct `yaml.safe_load` of known config filenames, no narrative cross-module coupling, no `threading.Lock` in `async with await` contexts. **WS-5 (test coverage expansion):** 11 new test files/classes; `tests/work/test_post_work_refresh.py` (15 tests covering `_is_enabled`, `run()`, `main()`, `_append_run_log()` — previously 0% coverage); integration tests for config-cache consistency and work-loop DAG paths. **WS-6 (exception observability):** `WorkConnectorError.to_log_dict()` method; connector error catch blocks in `work_loop.py` emit structured dict to `tmp/work_loop_errors.jsonl` JSONL sink — replaces 12 silent `except Exception: pass` swallows. **WS-7 (`signal_routing` externalization):** `config/signal_routing.yaml` — 47-entry signal→action routing table extracted from `scripts/action_composer.py` `_SIGNAL_ROUTING` Python dict; loader uses `load_config("signal_routing")` with in-process dict fallback on parse failure (N2 compliant). **WS-8 (dep pins + type annotations + lint):** `pyproject.toml` — all Work OS dependencies pinned; `mypy --strict` annotations on `work_loop.py`, `post_work_refresh.py`, `work_reader.py`; ruff + pylint clean. **WS-9-A (concurrency audit):** `tmp/ws9-audit.md` documents 4 shared-state race conditions with severity ratings. **WS-9-B (concurrency tests):** `tests/unit/test_channel_security.py` — 2 new tests: concurrent `ingest_proposals()` dedup correctness, `threading.Barrier(2)` write+read under race. **Architectural health:** 7.5/10 → 9.0/10. **Test suite:** 3,429 passed, 0 failed, 13 skipped, 20 xfailed. See Tech Spec v3.14.0, `specs/pay-debt-reloaded.md` (archived). |
| v7.1.5 | 2026-03 | **PAY-DEBT v1.0 — Structural Integrity Plan (TD-1–TD-5) [retroactive]:** Codebase health raised from ~5/10 to 7.5/10 by retiring five categories of technical debt across Tech Spec v3.9.x–v3.12.x releases. **TD-1 (god-file decomposition):** `channel_listener.py` (3,543 LOC, 44 functions, 9 mixed responsibilities) → `scripts/channels/` bounded-context package; `work_reader.py` (3,146 LOC, 54 functions, 22 commands) → `scripts/work/` subpackage with facade; `preflight.py` (1,848 LOC, 30 functions, 40+ checks) → modular checker pipeline. ~8,537 LOC moved from god-files to bounded packages. **TD-2 (Work OS structural fragility):** `work_reader.py` decomposed into domain writers, state readers, and narrative delegation layers — enabling safe incremental test coverage. **TD-3 (hardcoded registry anti-pattern):** `_HANDLER_MAP` in `pipeline.py` and `action_executor.py` externalized to `config/connectors.yaml` and `config/actions.yaml`; `_COMMAND_ALIASES` in `channel_listener.py` externalized to `config/commands.md`. **TD-4 (retrospective-only observability):** Structured JSONL logging added — `tmp/pipeline_metrics.json`, `tmp/connector_health_log.md`; `scripts/health_check_writer.py` + `scripts/audit_compliance.py` replace AI-as-sole-updater pattern. **TD-5 (foundation module-level aliases):** `foundation.py` constant aliases documented as import-time frozen; `_config` dict is the runtime source of truth; test isolation via `_config` dict pattern. Four design laws enforced throughout: N1 (LLM/CLI engine), N2 (PULL>PUSH), N3 (privacy-first), N4 (cloud-folder portable). See Tech Spec v3.9.x–v3.12.x for implementation details; `specs/pay-debt.md` (archived). |
| v7.1.0 | 2026-03 | **Work OS v2.7.0 — Phases 2–5 complete:** `scripts/work_bootstrap.py` (12-question guided setup interview, cold-start path); `scripts/work_notes.py` (post-meeting notes capture, D-NNN/OI-NNN IDs); `scripts/work_reader.py` (25-command read-path CLI, <2s from cached state); `scripts/work_domain_writers.py` (atomic writers for all 11 domain state files); `scripts/narrative_engine.py` (10 narrative templates: weekly_memo, talking_points, boundary_report, connect_summary, newsletter, deck, calibration_brief, connect_evidence, escalation_memo, decision_memo); `scripts/post_work_refresh.py` (post-refresh summarization); `scripts/kusto_runner.py` (KQL bridge, Microsoft Enhanced tier); `scripts/skills/work_background_refresh.py` (pull-triggered background refresh); `.github/workflows/work-tests.yml` (CI matrix Python 3.11/3.12/3.13 + prompt-lint gate); Work OS state directory expanded to 20 files; FW-11 through FW-17 implemented (Post-Meeting Notes, Guided Bootstrap, Narrative Engine, Connect Cycle, Promotion OS, Decision Support, Org Calendar); 883 tests in `tests/work/`. See FR-19, Tech Spec §19, UX Spec §23. |
| v7.0.11 | 2026-03 | **PR-3 AI Trend Radar** (specs/ai-posts.md PR-3 v1.0.6): `scripts/skills/ai_trend_radar.py` — `AITrendRadarSkill` 5-stage pipeline (INGEST→DISTILL→SURFACE→TRY→DRAFT); `AISignal` dataclass with SHA-256 stable IDs; 9-rule relevance scoring (keyword hit +2.0, multi-source bonus +0.15, employer safety gate −5.0 block, title+body hit +0.5, multi-topic boost); `_detect_category()` ordered (model→framework→tool→tutorial→technique→research); `_apply_topic_boost()` max-wins; `_emit_experiment_moments()` GAP-2 guard; warm-start lifecycle via `state/ai_trend_radar.md` frontmatter. `scripts/pr_manager.py` — `ai_experiment_complete` moment type (weight 0.85, NT-1/1.0 + NT-5/0.5 threads, LinkedIn-only); `cultural_festival` platform list excludes `linkedin`. `platform_exclude: list[str]` field on `ContentCard` (pr_stage/domain.py + service.py). `state/pr_manager.md` — `register_b_practitioner` voice sub-register. RSS connector enabled with 7 AI feeds (openai_blog, simon_willison, huggingface_blog, google_ai, anthropic, msresearch, hn_ai). `config/artha_config.yaml` — `enhancements.pr_manager.ai_trend_radar` block (13 newsletter senders, relevance_keywords, try_worthy_threshold: 0.7, surface_threshold: 0.35, max_signals_per_week: 10). PAT-PR-004 stale-radar pattern (stale_days: 14). `config/skills.yaml` — ai_trend_radar P2 weekly. `state/gallery.yaml` — CARD-SEED-RADAR seed (occasion_type: ai_experiment_complete). `scripts/briefing_adapter.py` — `render_radar_section()`. Telegram `/radar`/`/try`/`/skip` commands in `channel_listener.py`. 52 new tests (1974 total passing). See F11.12. |
| v7.0.10 | 2026-03 | **PR-2 Content Stage fixes + vault policy change:** gallery files (`state/gallery.yaml`, `state/gallery_memory.yaml`) removed from vault encryption — public social-media drafts do not require PII-level protection; vault lockout was preventing `/stage list` from reading cards between sessions. `scripts/skills/occasion_tracker.py` `_parse_date_flexible()` expanded to handle `M/D/YYYY`, `MM/DD/YYYY`, `M/D` formats (all dates in `state/occasions.md` use US date notation). `scripts/pr_stage/service.py` `ContentStage._adapt_moment()` bridges PR-1→PR-2 `ScoredMoment` field names. 16 Indian festivals 2026–2027 seeded in `state/occasions.md`. 1922 tests (all passing). See F11.12. |
| v7.0.9 | 2026-03-21 | **MEM v1.3.0** — Memory Pipeline Activation (specs/mem.md): fixed 4 independent breaks that left Artha's 9-script memory subsystem operational-but-silent. **Break 2 — parser mismatch:** `scripts/fact_extractor.py` — `_parse_briefing_md(content)` handles both Telegram (`━━` box-drawing) and Markdown (`## CRITICAL/URGENT/BY DOMAIN`) briefing formats; `extract_facts_from_summary()` renamed `summary_path→input_path`, auto-detects format via 3-branch routing. **Phase 1b — briefing-aware signals:** 4 new `_EXTRACTION_SIGNALS` — deadline (30d TTL), decision-pending (60d TTL), `$N/mo` threshold (90d TTL, finance), OI-NNN open-item pattern (90d TTL). **Domain sensitivity tiering (Design Principle 8):** `_HIGH_STAKES_DOMAINS = {finance, health, immigration, insurance, legal}` + `_apply_domain_sensitivity_ttl()` — caps TTL at 90d for non-correction/preference facts. **Break 3 — health-check format mismatch:** `scripts/self_model_writer.py` — `_parse_catchup_runs_from_markdown(content)` reads `## Catch-Up Run History` freeform section; `update()` tries YAML frontmatter first, falls back to markdown parser. **Break 1 — LLM never fires session summarizer:** `scripts/post_catchup_memory.py` (new, ~240 LOC) — deterministic orchestrator; `run(briefing_path, artha_dir, dry_run)` extracts facts DIRECTLY from briefing (bypasses lossy SessionSummary round-trip), persists via `persist_facts()`, updates `SelfModelWriter`, appends JSON record to `state/memory_pipeline_runs.jsonl`; CLI: `--briefing PATH` (required), `--dry-run`, `--discover`, `--rebuild-self-model`; config flag `harness.agentic.post_catchup_memory.enabled`. **Break 4 — low signal yield:** addressed by Phase 1b signals above. **Bootstrap:** `scripts/bootstrap_memory.py` (new, ~80 LOC) — batch-seeds all historical briefings (collect ALL facts first, single `persist_facts()` call → AR-1 consolidation fires once, order-independent); `scripts/bootstrap_seeds.yaml` — declarative procedure manifest; Step 11c in `config/workflow/finalize.md` rewritten to `python3 scripts/post_catchup_memory.py --briefing briefings/YYYY-MM-DD.md`. Validated outcome: `state/memory.md` populated, `state/self_model.md` live (Domain Confidence + Known Blind Spots), procedure seeded. 18/18 E2E checks pass. 1546 tests (all passing). See F15.136. |
| v7.0.8 | 2026-03 | **DUAL v1.3.0** — Multi-machine action bridge, per-machine connector routing, and nudge daemon host gating (specs/dual-setup.md): `scripts/action_bridge.py` (new, ~800 LOC) — file-based OneDrive-synced bridge for proposing actions on Mac (enricher/proposer role) and executing on Windows (executor role); Fernet symmetric encryption + Argon2 key derivation; atomic `os.replace()` writes; UUID dedup; `BridgeMetrics` counter/latency tracking; TTL-based GC; `ingest_proposals()` / `ingest_results()` / `retry_outbox()` / `write_proposal()` / `write_result()` / `gc()` / `detect_role()` / `detect_conflicts()` / `is_bridge_enabled()` functions; `scripts/action_queue.py` extended: `_resolve_db_path()` (per-machine local SQLite at `~/.artha-local/actions.db` on macOS, `%LOCALAPPDATA%\Artha\actions.db` on Windows), `_migrate_schema_if_needed()` adds `bridge_synced` + `origin` columns, `ingest_remote()` UUID-dedup ingestion, `update_defer_time()`, `apply_remote_result()` (additive-only), `mark_bridge_synced()`, `list_unsynced_results()`; `scripts/action_executor.py` extended: shared `_after_enqueue()` post-enqueue helper, `write_result()` called after approve/reject, `update_defer_time()` in defer; `scripts/channel_listener.py` — `ingest_results()` + `gc()` called at top of `cmd_catchup()` (proposer-only), bridge init block in `run_listener()` poll loop; `scripts/preflight.py` — P1 bridge health check added. **Per-machine connector routing:** `run_on:` field on every connector in `config/connectors.yaml` (`darwin`/`windows`/`all`); `pipeline.py` `_enabled_connectors()` gates on `platform.system().lower()`; `list_connectors()` shows PLATFORM column. **Nudge daemon host gating:** `scripts/nudge_daemon.py` `_verify_nudge_host()` compares `channels.yaml defaults.listener_host` vs `socket.gethostname()` — exits early on wrong machine. 57 new bridge unit tests + 10 platform gating tests. 1586 tests total (all passing). See F15.133–F15.135. |
| v7.0.7 | 2026-03-21 | ACT-RELOADED production hardening and test canonicalization: `scripts/pattern_engine.py` `_load_yaml_file()` rewritten to use `yaml.safe_load_all()` — fixes silent `None` return on Markdown files with YAML frontmatter (`---` separators triggered `ComposerError`; patterns never evaluated against state files); `scripts/power_half_hour_view.py` `_parse_open_items()` — correct YAML list item extraction (was doing broken `re.sub(r"^- ", "")` causing inconsistent indentation); `scripts/cost_tracker.py` `_load_pipeline_metrics()` — handle list-formatted JSON output (production `tmp/pipeline_metrics.json` is a JSON array); test suite finalized: 1520 passed, 0 failures, 5 skipped, 20 xfailed. See §8.15. |
| v7.0.6 | 2026-03-21 | ACT-RELOADED (E1–E16, specs/act-reloaded.md v1.3.0) — Evolved Artha from **read–reason–act** to **sense–reason–act–learn** with 16 enhancements across 3 waves. **Wave 1 — New signal pipelines:** E1 `scripts/email_signal_extractor.py` (8 signal categories — RSVP, appointment, payment, form deadline, shipment, security alert, renewal, school action — Step 6.5 deterministic extraction, 6 new `_SIGNAL_ROUTING` entries in `action_composer.py`); E9 `scripts/attachment_router.py` (7 routing rules mapping filename patterns to domains, PII scrubbing from filenames, `AttachmentSignal` dataclass, `to_domain_signals()` converter); E3 `scripts/pattern_engine.py` + `config/patterns.yaml` (8 named cross-domain patterns, 6 operators: `days_until_lte`, `lt`/`gt`/`eq`, `exists`, `has_item_within_days`, `contains`, `stale_days`; replaces prose rules in Step 8; cooldown-state isolation per `root_dir`). **Wave 2 — Intelligence loop:** E5 `scripts/briefing_adapter.py` (`BriefingConfig` dataclass, 6 adaptive rules R1–R6 — flash override, low signal noise, calibration skip, coaching dismiss, consistent domains, weekend skip — 10-run cold-start gate, transparency footer via `format_footer()`); E4 `scripts/nudge_daemon.py` (vault-watchdog bridge, 5 nudge types, 3/day global cap, marker-file dedup at `tmp/nudge_*.marker`, no vault access when locked); E2 `/remember` command in `channel_listener.py` + `state/inbox.md` schema (PII-guarded async knowledge capture, 5 writes/hour, full-scope only, `CHANNEL_REMEMBER` audit event). **Wave 3 — Activation enhancements:** E11 `scripts/self_model_writer.py` (fact→self-model pipeline, 3000-char bounded model, `state/self_model.md`); E12 `scripts/decision_tracker.py` (`capture_from_command()`, `persist_proposal()`, `_load_decisions()`); E13 `scripts/relationship_pulse_view.py` + `state/relationships.md` (view for existing `/relationships` command); E14 `scripts/power_half_hour_view.py` (Powers the `/power` command — top OI by impact, quick calendar, intention prompt); E15 `scripts/retrospective_view.py` (monthly retrospective generator reading summaries/ + state/); E16 `scripts/coaching_engine.py` (`select_nudge()`, 4 strategy types, deterministic selection based on goals + health history + preferences; moved from Step 19 to Step 8). **Supporting:** E6 `scripts/skills/subscription_monitor.py` (4 new lifecycle detectors: trial_ending, cancellation_window, annual_review, duplicate_subscription; 5 new signal types); E7 `_build_family_flash()` in `channel_push.py` (family-scope Telegram digest); E8 `scripts/cost_tracker.py` + `/cost` command; E10 `scripts/actions/whatsapp_cloud_send.py` (Cloud API Phase 2, `SUPPORTED_TEMPLATES`, `urllib`-based HTTP). Feature flags wired under `enhancements.*` in `config/artha_config.yaml`. 1369 tests baseline (+151 new tests across 16 new test files). See §8.14. |
| v7.0.5 | 2026-03 | Utilization uplift (specs/util.md U-1–U-9): `config/connectors.yaml` all 8 personal-data connectors set `prefer_mcp: false` (privacy hardening — no 3rd-party MCP proxies); `config/user_profile.yaml` rebuilt from stub to 130-line scaffold (17 domains enabled, full family + location + integrations); `config/routing.yaml` generated from profile (was `key: value`); `config/Artha.core.md` Step 21 fact extraction (non-blocking); `state/occasions.md` enriched with 11 India family DOBs, 16 Indian festivals (2026–2027), 12 US holidays (schema v1.1); `state/contacts.md` circles schema added (6 circles: core_family, extended_family_india, best_friends, us_friends, spiritual, professional); `scripts/skills/relationship_pulse.py` (circle-aware stale-contact nudger); `scripts/skills/occasion_tracker.py` (birthday/festival/anniversary 30-day lookahead with greeting suggestions); `scripts/skills/bill_due_tracker.py` (bill due date alerts 7/3/1 days ahead); `scripts/skills/credit_monitor.py` (hard inquiry / fraud alert scanner); `scripts/skills/school_calendar.py` (LWSD school event + grade alert tracker); `config/skills.yaml` 5 new skills registered (13 total); `prompts/social.md` occasion-aware intelligence section (3-day priority lane, circle cross-reference, message templates); `tests/conftest.py` project-root path fix (isolated test runs now work); `tests/unit/test_util_skills.py` 27 tests for new skills. 1069 tests (+27). See §8.13. |
| v7.0.4 | 2026-03 | Catch-up quality hardening (catch-up-quality-report 2026-03-15): `scripts/email_classifier.py` (rule-based marketing tagger, whitelist-first, tags `marketing: bool` + `marketing_category:` on pipeline email records, auto-wired into `pipeline.py`); `scripts/health_check_writer.py` (atomic frontmatter updater for `state/health-check.md`, connector log rotation >7d to `tmp/connector_health_log.md`, lock-safe atomic write); `scripts/calendar_writer.py` (pipeline GCal/Outlook records → `state/calendar.md`, SHA-256 dedup on date+title, bootstrap stub detection); `scripts/migrate_oi.py` (idempotent OI backfill scanner — finds OI-NNN refs in all state files, appends to `open_items.md`); `scripts/preflight.py` token fix (refresh attempted when access token already expired, not just within 300s window) + bootstrap stub detection (`_is_bootstrap_stub()`) in `check_state_templates()` + 48h advance expiry advisory for MS Graph; `scripts/session_summarizer.py` `get_context_card()` auto-invokes `fact_extractor` for catch-up commands; `config/workflow/finalize.md` Step 11c (calendar_writer call) + Step 16 rewritten to call `health_check_writer.py` script; `config/workflow/process.md` Step 5a (email_classifier context) + Step 5d (context_offloader instruction). 1042 tests (+27). See §8.12. |
| v7.0.3 | 2026-03 | Agentic Reloaded (AR-1–AR-8): bounded memory capacity enforcement (`MAX_MEMORY_CHARS=3000`, `MAX_FACTS_COUNT=30`, `_consolidate_facts()`); self-model metacognition template (`state/templates/self_model.md`); pre-eviction memory flush (`should_flush_memory()`); cross-session search (`session_search.py`, grep-based with relevance ranking); procedural memory store (`procedure_index.py`, `state/learned_procedures/`); prompt stability architecture (frozen layer marker in `generate_identity.py`); delegation protocol (`delegation.py`, `DelegationRequest/Result`); root-cause-before-retry in `fetch.md`; 2 new audit checks (`check_memory_capacity`, `check_prompt_stability`); AR-* config flags in `artha_config.yaml`. 1015 tests (+72). |
| v7.0.2 | 2026-03 | Operational safety hardening: vault health 3-exit-code model (exit 2 = soft warnings, never blocks catch-up); stale lock auto-clear without `--fix`; `detect_environment.py` compact JSON when piped; calendar attendee PII redaction; `python3` consistency in AI agent instructions; +7 targeted tests (936→943). |
| v7.0.1 | 2026-03 | Patch: `_ComposedMiddleware.before_write` now accepts and forwards `ctx` to all child middlewares (was silently dropped). Test mocks updated to match `StateMiddleware` Protocol. |
| v7.0 | 2026-03 | Agentic Intelligence — OODA reasoning protocol, tiered context eviction, typed runtime context carrier (ArthaContext), implicit step checkpoints for crash recovery, persistent fact extraction across sessions. New scripts: `artha_context.py`, `checkpoint.py`, `fact_extractor.py`. `context_offloader.py` EvictionTier enum. `audit_compliance.py` OODA check. `state/memory.md` v2.0 schema. `harness.agentic.*` feature flags. 936 tests (+120). (F15.128–F15.132) |
| v6.1 | 2026-03 | Agentic CLI Hardening (Gemini CLI feedback cycle): `skill_runner.py` venv bootstrap + entrypoint + importlib scope fix; `pipeline.py` venv bootstrap + unconditional health output; `noaa_weather.get_skill()` 0.0/0.0 coordinate guard; `uscis_status` actionable 403 IP-block message. Preflight `ARTHA_DIR`-relative cold-start check (CI fix). 816 tests (+12). (F15.124–F15.127) |
| v6.0 | 2026-03 | Cowork VM & Operational Hardening (Phases 1–5): environment detection layer, preflight advisory mode + profile completeness check, MS Graph 60-day token lifecycle, config/Artha.md decomposition into compact mode + config/workflow/ phase files, workflow compliance gates, post-catch-up audit script. New scripts: `detect_environment.py`, `audit_compliance.py`. New state template: `state/templates/health-check.md`. 804 tests (+106). (F15.119–F15.123) |
| v5.9 | 2026-03 | Deep Agents Option B — Core Harness Patterns (Phases 1–5): context offloading, progressive domain disclosure, session summarization, middleware stack, structured output validation. New scripts: `context_offloader.py`, `domain_index.py`, `session_summarizer.py`, `scripts/middleware/` (5 modules), `scripts/schemas/` (4 modules). `config/Artha.md` + `config/Artha.core.md` updated with Steps 4b′, 5, 7, 8h, 11b, Session Protocol, harness_metrics, Step 18a′. Feature flags under `harness:` namespace in `artha_config.yaml`. 698 tests (F15.114–F15.118) |
| v5.8 | 2026-03 | Intelligence expansion + platform parity: financial_resilience skill, 1099-K gig income tracking, purchase interval observation, structured contact profiles, pre-meeting context injection, passive fact extraction, digital estate inventory, instruction-sheet actions, subscription action proposals, setup.ps1 Windows parity, --doctor unified diagnostic, Apple Health connector, longitudinal lab tracking (F15.100–F15.113) |
| v5.7 | 2026-03 | OOBE polish audit: setup.sh step counters + brand mark, AI CLI auto-detection, colorized demo briefing, README compression 624→142 lines, docs/backup.md, specs/README.md, make start (F15.95–F15.99) |
| v5.6 | 2026-03 | Interactive setup wizard + first-run friction fixes: starter profile, wizard do_setup(), no auto-preflight, advisory warnings, --first-run preflight, setup.sh wizard prompt (F15.89–F15.94) |
| v5.5 | 2026-03 | 10-layer defense-in-depth for state data protection: advisory file locking, cloud sync fence, post-encrypt verification, deferred plaintext deletion, encrypt-failure lockdown, auto-lock mtime guard, net-negative override with `.pre-shrink` pin, GFS prune protection, confirm gate for restore/install, pre-restore safety backup, key health monitoring (F15.88) |
| v5.4 | 2026-03 | Novice UX deep audit round 2 (15 issues): demo-first onboarding, placeholder guard, Windows venv, household section, contributor hook, vault --help, preflight expectations table, AI CLI cost transparency, Google OAuth safety explanation, mosaic PII docs (F15.78–F15.86) |
| v5.3 | 2026-03 | Novice UX hardening (15-issue audit): Step 6 restored, age key deletion trap fixed, keyring check, open_items template, path PII masking, Node.js prereq, `<details>` OS blocks, AI CLI guidance, Linux age installs, Google OAuth deep links, catchup aliases, example profile PII neutralized (F15.72–F15.77) |
| v5.2 | 2026-03 | Phase 1b capabilities: domain registry, household types, renter mode, pet reminders, passport skill, subscription monitor, RSS connector, offline/degraded mode, performance telemetry, view scripts (F15.60–F15.71) |
| v5.1 | 2026-03 | ACB v2.1: Multi-LLM Q&A via Telegram, ensemble mode, HCI command redesign, write commands |
| v5.0 | 2026-03 | Channel Bridge (ACB v2.0): Telegram push + interactive listener |
| v4.4 | 2026-03 | Three-module arch: `foundation.py` + `backup.py` standalone CLI; key export/import; `--data-only` restore |
| v4.3 | 2026-03 | ZIP-per-snapshot backup architecture — `backups/` root dir, self-contained ZIPs, `install` command |
| v4.2 | 2026-03 | Comprehensive Backup Registry — all 31 state + 4 config files, full fresh-install restore |
| v4.1 | 2026-03 | WorkIQ work calendar integration |
| v4.0 | 2026-03 | Intelligence amplification: 29 enhancements |
| v3.0 | 2025-10 | Architectural pivot to pull model |

See [CHANGELOG.md](../CHANGELOG.md) for full version history.

---

## Table of Contents

1. [Vision & Philosophy](#1-vision--philosophy)
2. [The Problem Artha Solves](#2-the-problem-artha-solves)
3. [Design Principles](#3-design-principles)
4. [Life Data Map](#4-life-data-map)
5. [The Six Interaction Modes](#5-the-six-interaction-modes)
6. [Functional Requirements](#6-functional-requirements)
   - FR-1: Communications Intelligence
   - FR-2: Immigration Sentinel
   - FR-3: Financial Command Center
   - FR-4: Kids & School Intelligence
   - FR-5: Travel & Loyalty Management
   - FR-6: Health & Wellness Radar
   - FR-7: Home & Property Management
   - FR-8: Calendar & Time Intelligence
   - FR-9: Shopping & Commerce Intelligence
   - FR-10: Learning & Development Tracker
   - FR-11: Relationships & Social Fabric
   - FR-12: Digital Life Management
   - FR-13: Goal Intelligence Engine
   - FR-14: Work-Life Boundary Guardian
   - FR-15: Artha OS Core
   - FR-16: Insurance & Risk Management
   - FR-17: Vehicle Management
   - FR-18: Estate Planning & Legal Readiness
   - FR-19: Work Intelligence OS
7. [Goal Intelligence Engine — Deep Dive](#7-goal-intelligence-engine--deep-dive)
8. [Architecture](#8-architecture)
9. [Autonomy Framework](#9-autonomy-framework)
10. [Data Sources & Integrations](#10-data-sources--integrations)
11. [Privacy Model](#11-privacy-model)
12. [Phased Roadmap](#12-phased-roadmap)
13. [Success Criteria](#13-success-criteria)
14. [Non-Functional Requirements](#14-non-functional-requirements)
15. [Open Questions — Resolved](#15-open-questions--resolved)

---

## 1. Vision & Philosophy

**Artha** (Sanskrit: अर्थ — *purpose, wealth, meaning*) is your personal intelligence operating system.

In the Purusharthas — the four aims of human life in Sanskrit philosophy — Artha is one of the four pillars: *Dharma* (duty), **Artha** (purpose and material well-being), *Kama* (pleasure and fulfillment), and *Moksha* (liberation). Artha is chosen deliberately: this system serves your material life — finances, family, home, health — while keeping the larger picture of what you're building toward in focus.

Artha manages everything outside of work: your family, finances, health, home, learning, and personal goals. Artha is not a dashboard. It is not a to-do app. It is not a notification router or an email summarizer. It is a **family-aware, privacy-first, goal-centered personal operating system** that continuously converts fragmented life data into prioritized decisions, forward-looking guidance, and friction-reducing actions.

---

## 2. The Problem Artha Solves

Your personal life is fragmented across more than 15 services, 2 email accounts, 5+ financial institutions, 3 school communication channels, immigration attorneys, utility providers, and dozens of subscriptions. There is no single place that knows all of it.

**The three dominant pain patterns, discovered from data:**

### Pattern 1 — High-Volume Noise
ParentSquare generates 90–100 emails per month across three simultaneous streams: Springfield Central High School (Arjun), Lincoln Middle School (Ananya), and Springfield School District. Mixed in are teacher emails, attendance alerts, missing assignment notices, and grade reports. There is no consolidation, no triage, no filter between "Arjun was marked absent" (urgent) and "Spirit Week reminder" (not urgent).

### Pattern 2 — High-Stakes Silence
Life-critical deadlines — document expiry windows, legal filing timelines, insurance renewal dates, and case milestone dates — are tracked nowhere. They live in email threads, PDFs, and attorney correspondence — none of which proactively alerts you when a 90-day action window opens. The cost of missing one of these is measured in months or years.

### Pattern 3 — Fragmented Finance
Bills, subscriptions, investments, loans, and credit monitoring are spread across 8+ institutions: Chase, Fidelity, Vanguard, Morgan Stanley, E*Trade, Wells Fargo, Discover, and International Bank (NRI). Assembling a complete financial picture requires logging into all of them. No single signal tells you whether you are on track toward your goals.

**What this costs today:**
- Time spent triaging 112+ unread emails that span urgent and trivial in the same inbox
- Anxiety from not knowing where key deadlines sit
- Financial opacity across a complex multi-institution portfolio
- No measurement of progress toward any personal goal
- Family coordination happening reactively (Arjun missed? Find out at dinner) rather than proactively

---

## 3. Design Principles

**P1 — Clarity over noise.** Artha defaults to silence. It speaks only when it has something worth saying. The signal-to-noise ratio is a product commitment, not an afterthought. An alert system that cries wolf becomes invisible.

**P2 — Human-gated by default.** Artha reads everything but writes nothing without permission. It observes all your data, synthesizes across domains, and recommends actions — but the humans in this family retain all control over what actually happens.

**P3 — Goals above tasks.** Every feature in Artha ultimately serves a goal you have defined. An energy bill alert matters because it serves a financial goal. A school attendance flag matters because it serves a parenting goal. Features without a goal connection are deprioritized.

**P4 — Family-aware.** Artha understands that you are not alone. Priya, Arjun, and Ananya are first-class citizens in Artha's world model. Artha tracks what matters to each family member, not just to you.

**P5 — Privacy by architecture.** Your data never leaves your devices or trusted cloud storage without your explicit consent. No third-party analytics. No training on your data. Local-first state storage.

**P6 — Earned autonomy.** Artha starts at Trust Level 0 (observe and report) and earns the right to act through demonstrated reliability. It does not rush toward autonomy; it earns it. See Section 9 for the full autonomy framework.

**P7 — AI-native intelligence.** Artha is not a dashboard with an AI add-on. It is an AI-first system that reasons, infers, predicts, and remembers. Every signal passes through a semantic reasoning layer before surfacing. Artha infers intent from communication patterns, reasons across domains to discover non-obvious connections, forecasts future risk before thresholds are crossed, and remembers prior decisions and preferences to improve over time. The difference between a notification system and an intelligence system is reasoning — Artha reasons.

**P8 — Self-improving and extensible.** Artha measures its own accuracy, learns from corrections, and adopts new AI capabilities as they become available. Adding a new data source, domain, or integration follows a documented checklist — not a code rewrite. The system is designed to grow as trust and utility grow: new email accounts, document repositories, MCP servers, and AI features can be absorbed without architectural changes. Governance processes ensure this growth is deliberate, tested, and reversible.

**P9 — Multi-model for cost and capability.** Artha uses the right LLM for the right task at the right cost. Claude handles orchestration, state management, and MCP tool access. Gemini CLI provides free web research, URL summarization, and AI visual generation. Copilot CLI provides free code/config validation. For high-stakes decisions (immigration, finance, estate), all three models generate responses and Claude synthesizes the best answer. No single-vendor lock-in — the multi-LLM layer maximizes capability while minimizing cost.

---

## 4. Life Data Map

*Based on analysis of OneDrive, Gmail (38,671 messages), and Outlook.com.*

| Domain | Key People | Key Services | Friction Level |
|---|---|---|---|
| Communications | All family | Gmail, Outlook, ParentSquare | 🔴 High |
| Immigration | Raj, Priya, Arjun, Ananya | [immigration attorney], USCIS, Employer Immigration team | 🔴 High |
| Finance | Raj, Priya | Chase, Fidelity, Vanguard, Morgan Stanley, E*Trade, Wells Fargo, Discover, International Bank (NRI) | 🔴 High |
| Kids & School | Arjun (11th, Springfield Central High), Ananya (7th, Lincoln Middle) | PPS, ParentSquare, Canvas | 🔴 High |
| Travel | All family | Alaska Airlines, Marriott Bonvoy, Avis, Expedia | 🟡 Medium |
| Health | All family | Regence/BCBS, HSA, Providence | 🟡 Medium |
| Home | Raj, Priya | Wells Fargo (mortgage), Metro Electric, City Water Utility, Sangamon County, Home Assistant, ISP, City Waste Services | 🟡 Medium |
| Calendar | All family | Google Calendar, Outlook Calendar | 🟡 Medium |
| Shopping | All family | Amazon, Costco, local | 🟢 Low |
| Learning | Raj | ByteByteGo, Kaggle, Obsidian, UW (alumni) | 🟡 Medium |
| Social | Raj, Priya | Friends, family, temple | 🟢 Low |
| Digital Life | Raj | 40+ subscriptions, Home Assistant, passwords | 🟡 Medium |
| Insurance | Raj, Priya | Auto insurer, homeowners insurer, umbrella, Work benefits (life/disability) | 🟡 Medium |
| Vehicles | Raj, Priya | WA DOL, service providers, NHTSA recalls | 🟡 Medium |
| Estate Planning | Raj, Priya | Estate attorney, financial account beneficiaries, guardianship docs | 🔴 High |
| Emergency Prep | All family | FEMA, County Emergency Management, New Madrid Seismic Zone readiness | 🟡 Medium |
| Goals | All family | (Artha-native — no existing tool) | 🔴 High |

---

## 5. The Seven Interaction Modes

Artha operates in seven modes simultaneously. They are not separate features — they are seven windows into the same intelligence layer.

---

### Mode 1 — Morning Briefing

**Trigger:** User-initiated via `catch me up` command in Claude Code on Mac, or on first interaction of the day. Output emailed for cross-device access.
**Format:** Structured Markdown brief, delivered to terminal and emailed to configured address
**Duration:** Designed to be read in under 3 minutes

**Briefing structure:**

```
ARTHA · [Day], [Date]

TODAY
  • [Urgent items — bills due, appointments, deadlines]
  • [Immigration: if any window is <90 days]
  • [Kids: attendance flags, tests today, deadlines]

THIS WEEK
  • [Top 3 items that need a decision or action]
  • [Upcoming key dates]

WEEK AHEAD  *(v4.0 — Monday briefings only)*
  • [5 most complex logistics items for the coming week]
  • [Calendar density: N events across M days — light/normal/heavy]
  • [Preparation items needed before Thursday]

GOALS
  • [2-3 goal progress signals — on track / at risk / behind]

ONE THING
  • [The single most important thing Artha wants you to know today]

PII GUARD  *(v4.0 — footer)*
  • [PII filter stats: N items scanned, M detections, 0 leaks]
```

Artha does not pad the briefing. If nothing is urgent, the "Today" section says so. The WEEK AHEAD section appears only on Monday briefings (or the first catch-up of a new week) and surfaces the logistics items that need advance coordination. The PII GUARD footer provides transparency into the pre-flight PII filter's operation.

---

### Mode 2 — On-Demand Chat

**Trigger:** Any time during a Claude Code session on Mac, or via Claude iOS app with cached state in a Claude Project.
**Latency:** <10s for state queries, <30s for email fetch. Answers from local Markdown state files.
Examples: bill due dates, immigration deadlines, spending summaries, goal progress, kids' academics, travel bookings, subscription renewals.

---

### Mode 3 — Batch Alert Review

**Trigger:** Part of each catch-up session (batch, not real-time). Sufficient for personal life timescales (days/weeks/months).

**Alert severity:** 🔴 Critical (deadlines <30d, overdue bills, document expiry), 🟠 Urgent (<90d immigration, bills <3d), 🟡 Heads-up (renewals, goals behind), 🔵 Info (summaries). **Fastest Next Action** *(v4.0)*: every 🔴/🟠 alert includes a concrete next step.

---

### Mode 4 — Weekly Summary

**Trigger:** Generated when the user runs catch-up on Sunday, or during the first catch-up after Sunday 8:00 PM Pacific. If no catch-up occurs over the weekend, the weekly summary is prepended to Monday's first catch-up.
**Format:** Structured Markdown brief, longer than the daily briefing

**Summary structure:**
- **Week in Review:** What happened across domains — highlights only
- **Kids This Week:** Academic, attendance, activity summary per child
- **Finance This Week:** Spending vs. budget, any anomalies, account changes
- **Goals Progress:** Each active goal — this week's movement, trend, status
- **Coming Up:** The 5 most important items in the week ahead
- **Artha Observations:** Patterns Artha noticed that you should know about

---

### Mode 5 — Goal Intelligence Engine

**Trigger:** Always on; powers sections of both briefing and weekly summary
**The most distinctive feature of Artha** — see Section 7 for full specification.

At its core: you define goals. Artha attaches metrics to them. Artha tracks progress automatically from connected data sources. Artha reports weekly and flags when you're off track.

---

### Mode 6 — Proactive Check-in

**Trigger:** Integrated into the catch-up flow — when data suggests intervention would help, Artha surfaces check-in questions at the end of the briefing. The user can also explicitly ask "check in with me" during any Claude Code session.
**Format:** Short conversational micro-interaction (2–3 targeted questions)
**Duration:** Designed to take <2 minutes to respond

*(Check-in details unchanged — see below)*

---

### Mode 7 — Mobile Conversational Bridge *(v5.1)*

**Trigger:** Telegram message to Artha bot — any time, from any device (phone, tablet, desktop).
**Format:** Plain text with Unicode formatting (no Markdown — bullets, numbered lists, emojis).
**Duration:** Read commands <5s; LLM Q&A 15–40s (with "💭 Thinking…" ack); write commands <2s.

This is Artha's **always-on mobile interface** — a persistent Telegram listener that bridges the gap between scheduled catch-ups. Unlike Mode 2 (on-demand chat via Claude Code terminal), Mode 7 requires no terminal, no SSH, and no Mac access. The user sends messages from their phone and gets structured responses.

**Three interaction tiers:**

1. **Read commands** — instant state-file lookups: `s` (status), `a` (alerts), `t` (tasks), `q` (quick tasks), `d` (domain list), `d kids` (domain deep-dive), `g` (goals), `diff` (state changes), `dash` (dashboard).
2. **LLM-powered Q&A** — free-form questions routed through a multi-LLM failover chain (Claude → Gemini → Copilot). Context assembled from domain prompts + state files + open items. Encrypted domains (finance, health, immigration, estate, insurance, vehicle) are automatically decrypted and re-locked after each query. Ensemble mode via `aa` prefix sends query to all CLIs in parallel, consolidated by Haiku.
3. **Write commands** — `items add <description> [P0/P1/P2] [domain] [deadline]` creates open items; `done OI-NNN` marks them resolved. Full audit logging.

**HCI design:** 45+ command aliases with single-letter shortcuts (`s`, `a`, `t`, `q`, `d`, `g`, `?`). Slash optional. Hyphens optional. Spaces OK. "catchup", "catch-up", "catch up", "briefing" all resolve identically. Designed for one-thumb phone operation with minimal cognitive load.

**Security:** Sender whitelist, PII redaction on every outbound byte, vault auto-relock after LLM calls, rate limiting, no PIN-bypass for encrypted data (LLM reads vault in-process and re-encrypts).

Check-ins are interactive micro-conversations surfaced during catch-up sessions when data shows drift from goals or emerging patterns. Because Artha runs interactively (not as a background process), the check-in IS part of the conversation — no separate trigger needed.

**Example check-in:**

```
ARTHA CHECK-IN · Friday 6:00 PM

Hey — quick check-in on your week:

1. No exercise logged this week, and your learning goal is 3 days behind.
   Work hours ran long Tuesday and Thursday (detected late emails both nights).
   Want me to block Saturday morning for a workout and a ByteByteGo session?

2. Arjun has 2 overdue assignments in AP Language.
   Want me to surface this in tomorrow's briefing for Priya too?

3. Your Amazon spend is already at 85% of monthly target with 10 days left.
   Flag before next checkout? [Yes / No / Adjust target]
```

**Check-in intelligence:**
- Only triggered when data shows drift from goals or emerging patterns
- Cross-references calendar availability before suggesting time blocks
- Remembers prior responses (“stop asking about Spirit Week”)
- Adapts timing to your response patterns

---

## 6. Functional Requirements

---

### FR-1 · Communications Intelligence

**Priority:** P0
**Summary:** Reduce inbox noise, surface what requires action, and route messages intelligently.

**The problem:** 112 unread emails in Outlook alone. Gmail has 38,671 messages. ParentSquare generates 90–100/month across 3 streams. Learning newsletters arrive daily. The urgent and trivial arrive in the same place with the same visual weight.

**Data sources:**
- Gmail (configured in user_profile.yaml)
- Outlook.com (configured in user_profile.yaml)
- ParentSquare (via email digest)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F1.1 | **School Digest Consolidator** — Merge all ParentSquare + PPS + Canvas emails into one daily summary per child. Arjun summary. Ananya summary. District summary. Suppress individual delivery emails. | P0 |
| F1.2 | **Action Item Extractor** — Scan all incoming email for deadlines, required actions, registration links, and RSVP requests. Surface in Morning Briefing. | P0 |
| F1.3 | **Sender Intelligence** — Classify senders by domain: immigration, finance, school, utility, shopping, learning, social. Weight alerts by domain priority. | P1 |
| F1.4 | **Newsletter Digest** — Consolidate learning newsletters (ByteByteGo, System Design, Big Technology, TED Recommends) into a weekly reading digest. Suppress individual deliveries. | P1 |
| F1.5 | **Subscription Radar** — Detect renewal notices, price change notifications, and new subscription activations from email. Flag for review. | P1 |
| F1.6 | **USPS Informed Delivery Integration** — Parse daily mail scans. Flag important physical mail (legal documents, checks, government notices). | P2 |
| F1.7 | **Channel Bridge (ACB v2.1)** *(v5.1)* — Platform-agnostic channel bridge with three layers. **Layer 1 (Push):** automated flash briefing push to Telegram after each catch-up. **Layer 2 (Interactive Commands):** 45+ command aliases with single-letter shortcuts (`s/a/t/q/d/g/?`), slash-optional, flexible normaliser. Read commands for all 18 domains (encrypted domains route through LLM). Write commands: `items add`, `done OI-NNN`. New commands: `/goals`, `/diff` (state changes since last catch-up). **Layer 3 (Multi-LLM Q&A):** free-form questions routed through Claude → Gemini → Copilot failover chain with workspace context (prompts + state + vault). Ensemble mode (`aa` prefix) sends to all CLIs in parallel, consolidated via Haiku. CLIs run from Artha workspace directory with auto vault relock. Structured output: numbered lists, Unicode bullets, one-line direct answers. Thinking ack ("💭 Thinking…") shown during LLM calls, auto-deleted on response. PII redaction on every outbound byte. Sender whitelist. Auto-start via Windows Startup. Message splitting for Telegram 4096-char limit. | P0 |

---

### FR-2 · Immigration Sentinel

**Priority:** P0
**Summary:** Track every immigration deadline, document expiry, and case milestone for all four family members — proactively.

**The problem:** Families with immigration processes face a complex web of document expiry dates, visa renewal windows, and case milestones. Members may hold employment-based visas, dependent visas, or be at various stages of a multi-year residency process. Missing a single deadline in this chain can have consequences measured in months or years.

**Data sources:**
- Outlook.com (employer immigration team emails)
- Gmail (immigration attorney correspondence)
- OneDrive (immigration documents folder)
- Manual input (document expiry dates, attorney updates)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F2.1 | **Family Immigration Dashboard** — Single view of all active documents, their expiry dates, and status for all four family members. | P0 |
| F2.2 | **Deadline Alert Engine** — Proactive alerts at 180, 90, 60, 30, and 14 days before any document expiry or filing deadline. Never miss a window. | P0 |
| F2.3 | **Case Timeline Tracker** — Track GC process milestones: PERM filing → PERM approval → I-140 filing → I-140 approval (done) → Priority Date → I-485 filing eligibility. Current priority date vs. Visa Bulletin. | P0 |
| F2.4 | **Document Vault Index** — Index all immigration documents stored in OneDrive. Know what exists, where it is, and when it expires. | P1 |
| F2.5 | **Attorney Correspondence Log** — Parse [immigration attorney] and Immigration emails into a structured log. Summarize latest status in Morning Briefing when anything changes. | P1 |
| F2.6 | **Visa Bulletin Monitor** — Monthly monitoring of the USCIS Visa Bulletin for priority date movements relevant to the user's category and country. Alert when the date advances. | P2 |
| F2.7 | **Dependent Age-Out Sentinel (CSPA)** — Track Child Status Protection Act (CSPA) age calculations for Arjun and Ananya. H-4 dependents "age out" at 21 and lose derivative status unless protected by CSPA. Arjun is approaching this window. Calculate CSPA age = biological age minus time I-140 was pending. Monitor continuously. If CSPA protection is insufficient, trigger F-1 student visa transition planning well before age-out. Alert at 36, 24, 12, and 6 months before projected age-out date. This is the highest-stakes derived deadline in the immigration domain. | P0 |

**Document registry (initial):**

| Document | Holder | Status | Action Window |
|---|---|---|---|
| H-1B | Raj | Active | Track expiry |
| H-4 | Priya | Approved | Track expiry |
| H-4 | Arjun | Approved | Track expiry |
| H-4 | Ananya | Approved | Track expiry |
| I-140 | Raj | Status requires verification — PRD and state file conflict. Run `/bootstrap` to confirm with user. | Monitor priority date |
| Passports | All four | Verify expiry dates | Alert at 6 months |

---

### FR-3 · Financial Command Center

**Priority:** P0
**Summary:** Unified visibility across all financial accounts, proactive bill management, net worth tracking, and goal-linked budget awareness.

**The problem:** Financial life spans 8+ institutions with no unified view. Bills arrive by email with no consolidation. Investment performance requires separate logins per account. Net worth is never known in real time.

**Data sources:**
- Gmail/Outlook (bill notifications, account alerts from all institutions)
- Manual input (account balances, loan amounts)
- Connected APIs where available (Fidelity, Chase — read-only)

**Account inventory:**

| Institution | Type | Email Source |
|---|---|---|
| Chase | Checking / Savings / Credit | alerts@chase.com |
| Fidelity | Investment + Credit Card | Fidelity emails |
| Vanguard | Retirement (401k/IRA) | Vanguard alerts |
| Morgan Stanley | Investment | MS alerts |
| E*Trade | Brokerage | ETrade alerts |
| Wells Fargo | Mortgage + FICO monitoring | alerts@wellsfargo.com |
| Discover | Credit card | Discover alerts |
| International Bank (NRI) | NRI banking (India) | HDFC alerts |
| HSA | Health Savings Account | HSA provider alerts |

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F3.1 | **Bill Calendar** — Parse all bill notification emails (Metro Electric, City Water, Sangamon County, all credit cards, mortgage) into a unified bill calendar. Alert 5 days before due date. Current: Metro Electric $300.63 due 3/26. | P0 |
| F3.2 | **Net Worth Snapshot** — Aggregate estimated net worth across all accounts on demand and in weekly summary. Manual update with auto-prompt from account statements. | P0 |
| F3.3 | **Unusual Spend Alert** — Flag transactions that deviate significantly from category baselines (e.g., Amazon spend 2x monthly average). | P0 |
| F3.4 | **Subscription Ledger** — Maintain a living list of all active subscriptions with amount, renewal date, and category. Detect new subscriptions from email. | P1 |
| F3.5 | **Credit Health Monitor** — Parse Wells Fargo FICO alerts (monthly). Track score trend. Alert on significant drops. | P1 |
| F3.6 | **Tax Document Tracker** — During Jan–April, track which tax documents have arrived per institution (1099, W-2, mortgage interest, etc.). Alert when all expected docs are received. | P1 |
| F3.7 | **Mortgage Tracker** — Track Wells Fargo mortgage balance, monthly payment, and payoff timeline. Annual check-in with banker ([your banker name]) trigger. | P1 |
| F3.8 | **International Bank (NRI) Monitor** — Track International Bank (NRI) account for balance alerts and transaction notifications. Currency conversion aware (USD/INR). | P2 |
| F3.9 | **Predictive Spend Forecasting** — Project monthly and annual spending by category based on historical patterns. Alert when current trajectory will exceed budget. Account for seasonal spikes (holiday spending, back-to-school, tax season). Example: “Amazon spend is 40% above YoY average through February. At this rate, annual discretionary budget will be exceeded by August.” | P1 |
| F3.10 | **Tax Preparation Manager** — Beyond document tracking (F3.6): maintain CPA/tax preparer contact and engagement schedule, track estimated quarterly tax payments (federal + WA has no state income tax — but track if applicable for other states), surface tax optimization prompts (“Max out 401k by year-end: $X remaining of $23,500 limit”, “HSA contribution gap: $Y remaining”, “529 contribution for WA state benefit”), and track filing status and refund/payment outcome. | P1 |
| F3.11 | **Insurance Premium Aggregator** — Pull total annual insurance cost from FR-16 into the financial picture. Surface in net worth and monthly expense views. “Total annual insurance spend: $X (auto: $A, home: $B, umbrella: $C). Up 8% from last year.” | P1 |
| F3.12 | **Credit Card Benefit Optimizer** — Map embedded card benefits (rental car damage waivers, purchase protection, extended warranty, travel insurance, lounge access, price protection) to each card in the account inventory. When a booking or purchase confirmation is detected, proactively surface the best card to use. Example: "You booked an Avis rental. Your Chase Sapphire provides primary rental car damage waiver — use it to decline Avis CDW and save ~$25/day." Also surface quarterly rotating category bonuses and annual benefit deadlines (airline credits, hotel credits). | P1 |
| F3.13 | **Tax Season Automation** *(v4.0)* — Automated tax preparation workflow during Jan–April. Goes beyond document tracking (F3.6) with an active checklist: (1) Track document arrival with expected-vs-received matrix (W-2, all 1099s, mortgage interest, property tax, charitable donations, HSA contributions), (2) Auto-generate CPA submission packet when all docs received, (3) Surface tax optimization actions with deadlines ("Last day for prior-year IRA contribution: April 15"), (4) Track estimated quarterly tax payment schedule and amounts, (5) Monitor filing status and refund/payment outcome. Integrates with F3.10 (Tax Preparation Manager) but adds active workflow automation. | P1 |

---

### FR-4 · Kids & School Intelligence

**Priority:** P0
**Summary:** Real-time awareness of both children's academic standing, attendance, upcoming tests and deadlines, and extracurricular activities — consolidated and actionable.

**The problem:** Arjun (11th grade, Springfield Central High) and Ananya (7th grade, Lincoln Middle) generate a high volume of school communications with no triage. Missing assignments, attendance issues, and test registrations (SAT) get buried in the same stream as routine newsletters.

**Data sources:**
- Outlook.com (ParentSquare emails — all three streams)
- Gmail (Canvas, PPS, teacher emails)
- Manual input (extracurricular schedules)

**Arjun profile (Springfield Central High, 11th grade):**
- SAT scheduled: March 13, 2026
- Extracurriculars: Tesla Economics Club (National Personal Finance Challenge)
- Courses: AP-level curriculum, Economics (teacher: Zebrack-Smith among others)
- Known alerts received: Low assignment score, missing assignment

**Ananya profile (Lincoln Middle, 7th grade):**
- Courses: Science (teacher: Niles), AP Language & Composition (advanced)
- Currently reading: *Just Mercy* (AP Language seminar, chapters 4–9)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F4.1 | **Daily School Brief** — One consolidated morning summary per child: attendance status, assignments due today, upcoming tests, any school alerts. Replaces ParentSquare inbox flood. | P0 |
| F4.2 | **Grade & Assignment Alert** — Immediately flag missing assignments and low scores (both children). Parse PPS automated alert emails. Surface in Morning Briefing. | P0 |
| F4.3 | **Test & Deadline Calendar** — Track standardized tests (SAT, PSAT, AP exams), enrollment deadlines, and school calendar events. Arjun's SAT 3/13 already registered — track as standing item. | P0 |
| F4.4 | **Attendance Tracker** — Log attendance notifications. Alert on absence patterns. Track cumulative absences per school year per child. | P1 |
| F4.5 | **College Prep Tracker (Arjun)** — Track SAT scores, college research milestones, application deadlines, and financial aid timelines as Arjun approaches senior year. | P1 |
| F4.6 | **Extracurricular Tracker** — Track club meetings, competitions, and deadlines for Arjun (Economics Club, National Personal Finance Challenge) and Ananya. | P1 |
| F4.7 | **Teacher Communication Log** — Log emails from specific teachers for each child. Make them searchable and summarizable. | P2 |
| F4.8 | **Paid Enrichment Tracker** — Track paid extracurricular activities, tutoring, sports leagues, music lessons, and summer camps for both children. Include: enrollment dates, costs (linked to FR-3 spend tracking), schedule, provider contact. Alert on registration windows: "Summer camp registration typically opens in March. Last year you enrolled Ananya in [camp]. Register again?" | P1 |
| F4.9 | **Activity Cost Summary** — Aggregate per-child annual cost of all school-adjacent activities (clubs, sports, camps, tutoring, test prep, college counseling for Arjun). Part of FR-3 financial picture: "Total kids enrichment spend YTD: $X (Arjun: $A, Ananya: $B)." | P2 |
| F4.10 | **Canvas LMS API Integration** *(v4.0)* — Direct Canvas (Instructure) API integration for real-time grade and assignment data instead of relying on email parsing. Canvas REST API provides: current grades per course, assignment scores with submission status, missing/late assignment list, upcoming due dates, and teacher comments. This replaces the delayed, incomplete email-parsed school data with structured, real-time academic intelligence. Canvas API uses OAuth2; parents can generate API tokens from their parent portal. Enables: "Arjun has a 94% in AP Physics, 87% in AP Language (down 3% this week), and 2 assignments due tomorrow." | P1 |
| F4.11 | **College Application Countdown Dashboard** *(v4.0)* — Comprehensive countdown tracker for Arjun's college application process (senior year 2026–2027). Structured timeline with reverse-scheduled milestones: SAT scores (track and assess retake need), college list finalization (reach/match/safety by June 2026), campus visits (summer 2026 window), Common App essay drafts (start July, finalize September), letters of recommendation (request by September, track receipt), Early Decision/Early Action deadlines (November 1/15, 2026), Regular Decision deadlines (January 1–15, 2027), FAFSA/CSS Profile (October 2026 opens), financial aid comparison (March–April 2027). Each milestone has: target date, status, dependencies, and Artha-generated preparation prompts. Surface in weekly summary during active application season. | P0 |

---

### FR-5 · Travel & Loyalty Management

**Priority:** P1
**Summary:** Unified view of upcoming travel, loyalty point balances, and trip planning intelligence for the whole family.

**Data sources:**
- Gmail (Alaska Airlines, Expedia, Avis, Marriott Bonvoy booking confirmations)
- Outlook (travel-related emails)
- Manual input (trip plans)

**Known loyalty programs:**
- Alaska Airlines (whole family — MVP/Mileage Plan)
- Marriott Bonvoy
- Avis (car rental)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F5.1 | **Trip Dashboard** — Upcoming trips with all booking details (flights, hotel, car) in one place, parsed from confirmation emails. | P1 |
| F5.2 | **Loyalty Points Aggregator** — Current balance for Alaska, Bonvoy, Avis. Expiry alerts. Opportunity alerts when miles are close to a reward threshold. | P1 |
| F5.3 | **Travel Document Checker** — Before any family trip, verify: passports valid for 6+ months beyond return date (all four), H-4/H-1B status valid, any visa required for destination. | P0 |
| F5.4 | **Flight Alert** — Parse flight confirmation emails. Alert on check-in window, gate changes, and day-of reminders. | P1 |
| F5.5 | **Expedia/Booking History** — Maintain a structured log of past and upcoming bookings. Answer "when did we last go to [destination]?" | P2 |
| F5.6 | **India Trip Planner** — Given the family's ties to India, specific pre-trip checklist: OCI cards, passport validity for all four, airline booking lead time, currency. | P1 |

---

### FR-6 · Health & Wellness Radar

**Priority:** P1
**Summary:** Track health appointments, insurance utilization, HSA balance, and wellness goals for the family.

**Data sources:**
- Gmail/Outlook (appointment confirmations, insurance EOBs, HSA statements)
- Manual input (appointment dates, medications, wellness goals)

**Known providers:**
- Health insurance: Regence BCBS (Employee plan)
- HSA account: Active
- Primary care and specialists: To be indexed from past appointment emails

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F6.1 | **Family Appointment Calendar** — Upcoming medical, dental, and vision appointments for all four family members parsed from confirmation emails. | P1 |
| F6.2 | **HSA Balance & Utilization Tracker** — Current HSA balance. Estimated remaining balance after known upcoming expenses. Annual contribution vs. limit. | P1 |
| F6.3 | **Annual Preventive Care Tracker** — Track whether each family member has completed their annual physical, dental cleaning, and vision check for the current year. Alert if overdue by September. | P1 |
| F6.4 | **Insurance EOB Monitor** — Parse Explanation of Benefits emails from Regence. Flag unexpected charges or claim denials. | P2 |
| F6.5 | **Prescription Refill Tracker** — Alert when a recurring prescription is due for refill based on fill date + days supply. | P2 |
| F6.6 | **Wellness Goal Integration** — Connect to Goal Engine (FR-13). Wellness goals (exercise frequency, sleep, weight) get Artha's tracking and weekly reporting. | P1 |
| F6.7 | **Open Enrollment Decision Support** — During Employer's annual benefits open enrollment window (typically October–November): surface current plan details, prompt review of health plan options (Regence BCBS tiers), compare FSA vs. HSA election, review life insurance and disability coverage adequacy (cross-reference FR-16). Checklist-driven with deadline countdown. | P1 |
| F6.8 | **Employer Benefits Inventory** — Maintain awareness of all Employee benefits beyond health: life insurance (basic + supplemental), short-term and long-term disability, AD&D, legal plan, EAP, employee stock purchase plan (ESPP) enrollment windows, 401k match optimization, and any dependent care FSA. Surface relevant benefits at decision points. | P1 |
| F6.9 | **Apple Health Integration** *(v4.0)* — Import Apple Health data via automated HealthKit export (XML or CSV) to power wellness goals with real biometric data. Data sources: step count, active calories, exercise minutes, resting heart rate, sleep analysis, weight (if tracked). Processing: daily export from iPhone via Shortcuts automation to `~/OneDrive/Artha/health_export/`, parsed by `parse_apple_health.py` during catch-up. Enables wellness goals with real metrics: "Exercise goal: 4x/week → Apple Health shows 3 workout sessions logged this week (Mon run 32min, Wed gym 45min, Fri walk 28min)." Privacy: raw health data processed and discarded; only aggregated daily/weekly metrics stored in `state/health.md`. | P1 |

---

### FR-7 · Home & Property Management

**Priority:** P1
**Summary:** Track utilities, mortgage, maintenance schedules, home value signals, and smart home integration.

**The problem:** The Springfield home generates bills across multiple utilities with no consolidated view. Maintenance tasks are tracked nowhere. The mortgage balance is unknown without logging into Wells Fargo.

**Property profile:**
- Address: Springfield, IL 62704
- Mortgage: Wells Fargo (banker: [your banker name] — annual check-in)
- Utilities: Metro Electric (Account: XXXX-EXAMPLE), City Water Utility, Sangamon County
- Smart home: Home Assistant (local API)
- Internet: TBD (Comcast/Xfinity, Ziply Fiber, or other)
- Mobile: TBD (carrier, family plan)
- Waste: TBD (City Waste Services or Springfield contracted provider)
- Property tax: Sangamon County (semi-annual: April 30, October 31)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F7.1 | **Utility Bill Calendar** — All utility bills (Metro Electric, Water, Sangamon County) parsed from email. Consolidated view, 5-day due date alerts. Metro Electric enrolled in autopay — confirm each month. | P0 |
| F7.2 | **Mortgage Tracker** — Outstanding balance, monthly payment, interest rate, payoff date. Annual refinance check trigger. FICO trend connected. | P1 |
| F7.3 | **Home Maintenance Scheduler** — Annual and seasonal maintenance calendar: HVAC filter (quarterly), gutter cleaning (fall), furnace service (fall), exterior painting (estimate cycle), smoke detectors (annual test). | P1 |
| F7.4 | **Home Assistant Integration** — Read device status, energy usage, and automation logs from Home Assistant local API. Surface anomalies (device offline, unusual energy consumption) in daily briefing. **✅ IMPLEMENTED v8.2.0** — Connector (`scripts/connectors/homeassistant.py`), skill (`scripts/skills/home_device_monitor.py`), 7-step setup wizard (`scripts/setup_ha_token.py`). LAN-only, token in macOS Keychain. Covers 28 devices across 6 ecosystems (Ring, Apple, Amazon, Google, Sonos, Gecko) via single HA REST API. Deterministic alerting: security device offline → 🔴, energy spike >30% → 🟠, supply <20% → 🟡. State stored in `state/home_iot.md`. | P1 |
| F7.5 | **Energy Usage Tracker** — Track Metro Electric bills month-over-month. Alert on unusual spikes. Compare against local seasonal averages. | P2 |
| F7.6 | **Home Value Signal** — Periodic Zillow/Redfin estimate for 62704 comparable sales. Not investment advice — context for net worth calculation in FR-3. | P2 |
| F7.7 | **Service Provider Rolodex** — Maintain a curated list of trusted service providers (plumber, electrician, HVAC, landscaper) with last-used dates and notes. | P2 |
| F7.8 | **Telecom & Internet Tracker** — Track ISP (Comcast/Xfinity, Ziply Fiber, or other), mobile phone plan (carrier, plan, monthly cost for family), and home phone/VoIP if applicable. Parse bill emails for monthly cost. Alert on price increases or contract renewal dates. Surface in subscription ledger (FR-3 F3.4). | P1 |
| F7.9 | **Waste & Recycling Services** — Track trash, recycling, and yard waste service (City Waste Services or the city's contracted provider). Payment schedule, pickup schedule, and any service changes. Holiday schedule adjustments (pickup delayed by 1 day). | P2 |
| F7.10 | **HOA / Community Dues** — If applicable to 62704 property: track HOA dues, payment schedule, assessment notices, and community meeting dates. Parse HOA correspondence from email. | P2 |
| F7.11 | **Lawn & Landscaping Schedule** — Seasonal yard maintenance calendar specific to Pacific Northwest: spring fertilization, summer watering schedule, fall leaf cleanup and aeration, winter moss treatment. Track landscaping service visits and costs if using a service. | P2 |
| F7.12 | **Property Tax Tracker** — Sangamon County property tax is paid semi-annually (April 30 and October 31). Track assessed value, tax amount, payment due dates, and payment confirmation. Alert 30 days before due date. Compare assessed value to market estimate (FR-7 F7.6). "Sangamon County property tax: $X due April 30. Assessed value: $Y vs. Zillow estimate: $Z." | P1 |
| F7.13 | **Emergency Preparedness** — Springfield is in a seismic zone (New Madrid Seismic Zone). Track: earthquake emergency kit contents and expiry dates (water, food, batteries, medications), family emergency plan (meeting point, out-of-area contact), FEMA/County emergency alerts integration, annual family emergency drill reminder. Checklist with annual review prompt. | P1 |

---

### FR-8 · Calendar & Time Intelligence

**Priority:** P1
**Summary:** A unified, intelligent view of the family calendar with conflict detection, context-aware scheduling, and time-budget awareness.

**The problem:** Important dates — Arjun's SAT 3/13, H-4 expiry windows, school events, annual appointments — live in separate systems. The Google Calendar is primarily used for birthday tracking. Outlook Calendar tracks work. Neither is connected to Artha's broader knowledge.

**Data sources:**
- Google Calendar (configured in user_profile.yaml)
- Outlook Calendar (configured in user_profile.yaml — work-life boundary signal only)
- ** Work Calendar via WorkIQ MCP** *(v4.1)* — corporate Teams meetings, 1:1s, standups, org events. Available on Windows work laptop only (M365 Copilot license). Graceful degradation on Mac.
- Artha's internal calendar (built from all FR data sources)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F8.1 | **Artha Unified Calendar** — Merge events from Google Calendar + dates discovered by all Artha agents (immigration deadlines, bill due dates, school tests, travel) into a single queryable calendar. | P1 |
| F8.2 | **Conflict Detector** — Identify scheduling conflicts. Alert when two family members need to be in different places simultaneously with one car, or when a school event conflicts with a planned trip. | P1 |
| F8.3 | **Time Budget Awareness** — Track how Raj's personal time is actually allocated vs. intended. Weekly: how much family time, learning time, and personal time? Surface the gap. | P2 |
| F8.4 | **Important Date Vault** — Store all family important dates (birthdays, anniversaries, citizenship milestones, school milestones) with multi-week advance reminders. | P1 |
| F8.5 | **Upcoming Week Briefing** — Every Sunday, Artha loads the week's calendar and surfaces the 5 most complex logistics items that need coordination in advance. | P1 |
| F8.8 | **Work Calendar Merge** *(v4.1)* — Integrate M365 corporate calendar via WorkIQ MCP as 7th data source. Merge with personal calendars using field-enrichment dedup (summary from personal, Teams link from work). Tag work-only events with 💼 prefix. Platform-gated: Windows only; Mac catch-ups show personal calendar + stale metadata footer. | P1 |
| F8.9 | **Cross-Domain Conflict Detection** *(v4.1)* — Detect work↔personal event overlaps (±15 min). Score cross-domain conflicts at Impact=3 (lifestyle trade-off) vs. internal work conflicts at Impact=1 (self-resolvable). Deduplicated events excluded from conflict detection. | P1 |
| F8.10 | **Duration-Based Meeting Load** *(v4.1)* — Analyze daily meeting burden by total minutes (not count). Triggers: >300 min → "Heavy load"; largest focus gap <60 min → "Context switching fatigue"; <120 min → "Light day, good for deep work." Persist count+duration metadata to `state/work/work-calendar.md` (13-week rolling window). | P1 |
| F8.11 | **Partial Redaction Engine** *(v4.1)* — Before work meeting titles transit to Claude API, redact sensitive codenames locally via configurable keyword list in `config/settings.md`. Only matched substrings are replaced (e.g., "Project Cobalt Review" → "[REDACTED] Review"), preserving meeting-type context for trigger classification. | P0 |
| F8.12 | **Teams Meeting Join Actions** *(v4.1)* — If a Teams meeting starts within 15 minutes of catch-up, surface a low-friction join action: "→ Join [Meeting] (Teams) [Y/n]". Opens Teams link on approval. | P2 |
| F8.13 | **Meeting-Triggered Employment OIs** *(v4.1)* — Critical meeting types (Interview, Performance Review, Calibration) auto-create Employment domain Open Items for prep. Temporal filter: future-dated only (no stale OIs in digest mode). Configurable trigger list in `config/settings.md`. | P1 |

---

### FR-9 · Shopping & Commerce Intelligence

**Priority:** P2
**Summary:** Track spending patterns, subscriptions, and purchase history across major retailers.

**Data sources:**
- Gmail (Amazon, Costco, order confirmation emails)
- Outlook (purchase receipts)
- Credit card alert emails (Chase, Fidelity, Discover)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F9.1 | **Monthly Spend Summary** — By category: Amazon, groceries, restaurants, subscriptions, kids, travel. Trend vs. prior month. Part of weekly summary. | P2 |
| F9.2 | **Amazon Order Tracker** — Parse order confirmation and delivery emails. Answer "when does X arrive?" and "what did I order last month?" | P2 |
| F9.3 | **Return Window Alert** — For major purchases, track return window expiry. Alert 3 days before window closes if item not yet reviewed. | P2 |
| F9.4 | **Costco Membership Renewal** — Track annual membership renewal date. Alert 30 days in advance. | P2 |
| F9.5 | **Price Drop Tracker** — For saved items or recent purchases, monitor for price drops and alert if a significant drop occurs within return window. | P2 |

---

### FR-10 · Learning & Development Tracker

**Priority:** P1
**Summary:** Track learning activity across all channels, measure progress toward learning goals, and surface the right content at the right time.

**The problem:** Learning is happening across many channels (newsletters, courses, Kaggle, Obsidian notes) with no measurement of cumulative progress. There's no way to know if you're on track toward a learning goal.

**Learning inventory (discovered):**
- Newsletters: ByteByteGo, System Design One, Big Technology (Kantrowitz), TED Recommends, Product newsletters
- Active learning: Kaggle (ML/AI), Obsidian vault (personal knowledge base)
- Education: UW (completed Spring 2023 — alumnus)
- Professional: Engineer role generates continuous learning

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F10.1 | **Learning Goal Tracker** — Set explicit learning goals ("complete X course by Q2," "read 12 books this year") and track progress. Connected to Goal Engine. | P1 |
| F10.2 | **Newsletter Digest** — Weekly aggregation of ByteByteGo, System Design, Big Technology, and others. Key insights only, with links to full content. Reduces inbox noise. | P1 |
| F10.3 | **Obsidian Vault Signals** — Monitor Obsidian vault activity (notes created, topics covered). Detect learning streaks. Surface forgotten notes relevant to current context. | P2 |
| F10.4 | **Course Progress Tracker** — For active online courses (Kaggle, Coursera, etc.), track completion percentage and time since last session. Alert on stalled courses. | P1 |
| F10.5 | **Reading Tracker** — Track books started, in progress, and completed. Connect to annual reading goal. | P2 |
| F10.6 | **UW Foster Alumni Tracker** — Monitor relevant UW Foster events, networking opportunities, and State of Economy Forum (noted in Outlook). Relevant to professional development. | P2 |

---

### FR-11 · Relationship Intelligence & Social Fabric

**Priority:** P1 *(elevated from P2 in v3.8 — relationships are a core life domain, not a nice-to-have)*
**Summary:** Build and maintain a relationship graph that tracks communication patterns, reciprocity, cultural protocols, life events, and group dynamics across the family's social network. Surfaces reconnect intelligence, occasion awareness, and relationship health signals in briefings.

**The problem:** Relationships decay silently. There is no system that tracks who you last contacted, whether reciprocity is balanced, which cultural protocols apply to which relationships, or which life events (births, graduations, bereavements) need acknowledgment. The result: missed birthdays, lapsed friendships, unbalanced social investments, and cultural protocol violations — all preventable with structured awareness.

**Data sources:**
- Google Calendar (birthdays, cultural events)
- Gmail/Outlook (personal correspondence patterns, frequency analysis)
- `contacts.md` (relationship groups, cultural protocol metadata)
- `occasions.md` (festival calendar, occasion types)
- Manual input (relationship context, group membership)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F11.1 | **Birthday & Anniversary Engine** — All family birthdays and anniversaries with 2-week advance alerts. Gift suggestion prompt 1 week out. Currently: Google Calendar is the primary birthday tracker — consolidate here. | P1 |
| F11.2 | **Family Cultural Calendar** — Track temple events, religious observances, and cultural milestones. | P1 |
| F11.3 | **Reconnect Radar** — For important relationships where correspondence has gone quiet for more than a configured period, surface a gentle prompt to reconnect. Configurable silence thresholds per relationship tier: close family (14 days), close friends (30 days), extended (90 days). | P1 |
| F11.4 | **Family Connections** — Track correspondence and call patterns with family. Flag when it's been too long since contact with key people. Timezone-aware for suggesting contact windows. | P1 |
| F11.5 | **Relationship Graph Model** — Structured graph of all tracked relationships with attributes: tier (close family / close friend / extended family / colleague / community), last_contact date, contact_frequency target, preferred_channel (email / WhatsApp / phone), cultural_protocol (e.g., "touch feet at Diwali", "Rakhi sender"), and life_events history. Stored in `state/social.md`. | P1 |
| F11.6 | **Communication Pattern Analysis** — Parse email metadata (sender, recipient, frequency, response time) to build communication cadence profiles. Detect: declining frequency (relationship cooling), one-sided communication (you always initiate), sudden silence after regular contact (potential issue). Surface in weekly summary. | P1 |
| F11.7 | **Reciprocity Ledger** — Track directional communication and gesture balance per relationship. "You've sent 5 messages to Rahul since his last reply" is a signal. "Meera has invited your family to 3 events; you've reciprocated once" is actionable. Not a score — a gentle awareness surface. | P2 |
| F11.8 | **Cultural Protocol Intelligence** — For relationships with cultural context (Indian family, temple community), track protocol obligations: Rakhi (sister → brother), Diwali greetings order (elders first), festival-specific gift norms, bereavement protocols (13-day period awareness). Sourced from `contacts.md` cultural metadata. | P1 |
| F11.9 | **Life Event Awareness** — When Artha detects a life event in a contact's sphere (graduation, new job, bereavement, birth, wedding — via email parsing or manual input), surface a prompt: "Priya had a baby last week (detected from email). Send congratulations?" Track acknowledged events to prevent re-prompting. | P1 |
| F11.10 | **Group Dynamics Tracking** — Track relationship groups (Work colleagues, Neighborhood community, Arjun's friends' parents, local neighbors) with group-level health metrics: last group interaction, upcoming group occasions, group communication balance. Enables: "You haven't attended a neighborhood community event in 3 months." | P2 |
| F11.11 | **Time Zone Scheduling** *(v4.0)* — Timezone-aware scheduling intelligence for family communications. Automatically calculates optimal call windows considering Pacific Time ↔ IST conversion (IST = PT + 13.5 hours), Indian family members' typical availability (morning 8–10 AM IST → 6:30–8:30 PM PT previous day), Public holidays and festival dates, and the user's own calendar availability. Surfaces in briefing when a reconnect is overdue: "Mom hasn't been called in 18 days. Best window: tonight 7:30 PM PT (tomorrow 9 AM IST, no Indian holidays)." Also flags when Indian festivals approach: "Holi is in 5 days — schedule family video call? Best window: Saturday 8 PM PT (Sunday 9:30 AM IST)." | P1 |
| F11.12 | **PR Manager — Personal Narrative Engine** *(v1.4, opt-in)* — Extends FR-11 into public/broadcast social media. A moment detection + content composition layer that fuses personal context (goals, occasions, relationships) with cultural calendar awareness to propose platform-specific posts (LinkedIn, Facebook, Instagram, WhatsApp) that are unmistakably the user's voice. 6 narrative threads (NT-1 through NT-6), deterministic convergence scoring (§4.2), 3-gate PII firewall, anti-spam governor, Voice Profile, per-platform adaptation rules. Disabled by default; activate via `/bootstrap pr_manager`. Full spec in `specs/pr-manager.md` (local-only, not committed — contains personal context). Implemented in `scripts/pr_manager.py`, `state/pr_manager.md`, `prompts/social.md`, `config/commands.md` (/pr family). **PR-2 Content Stage** *(Phase 0+1 complete)*: bounded-context `scripts/pr_stage/` package adds persistent card lifecycle (7-state FSM: seed → drafting → staged → approved → posted → archived/dismissed), gallery plaintext YAML (not vaulted — public social-media drafts), Devanagari PII guard i18n baseline, anti-boilerplate DraftPersonalizer context assembly (8-step pipeline, employer/children names loaded from `user_profile.yaml` at runtime). `_adapt_moment()` bridges PR-1→PR-2 ScoredMoment field names. PAT-PR-003 pattern fires when staged cards are unreviewed for 3+ days. Full spec in `specs/pr-stage.md` PR-2 v1.3.0 (Phases 2–5 remaining). **PR-3 AI Trend Radar** *(Phase 1 complete)*: weekly RSS + email scan surfaces AI signals scored by relevance, multi-source boost, and user topic interest graph; try-worthy signals (score ≥ 0.7) auto-trigger `ai_experiment_complete` moment (weight 0.85, LinkedIn-only); `platform_exclude` field prevents cultural-festival posts leaking to LinkedIn; `register_b_practitioner` voice sub-register; Telegram `/radar` / `/try` / `/skip` commands. Full spec in `specs/ai-posts.md` PR-3 v1.0.6. | P2 |

---

### FR-12 · Digital Life Management

**Priority:** P2
**Summary:** Manage the complexity of 40+ digital subscriptions, accounts, and services that form the infrastructure of modern life.

**Data sources:**
- Gmail/Outlook (subscription confirmations, renewal notices, account alerts)
- Manual input (password manager audit)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F12.1 | **Subscription Audit** — Master list of all active subscriptions with monthly cost, renewal date, and last-used-assessed date. Detect unused or duplicate subscriptions. Flag for cancellation review. | P1 |
| F12.2 | **Subscription Cost Dashboard** — Total monthly and annual cost of all subscriptions. Category breakdown. Year-over-year trend. | P2 |
| F12.3 | **Account Security Monitor** — Parse security alert emails (Chase device login, Equifax credit alerts). Flag unusual activity. Equifax credit monitoring is already active — integrate alerts. | P1 |
| F12.4 | **Domain & Hosting Tracker** — Track any personal domains or web hosting (if applicable) for renewal dates. | P2 |
| F12.5 | **Home Assistant Health Monitor** — Track Home Assistant system uptime, device offline alerts, and automation failures. Surface in morning briefing if any critical device is offline. **✅ IMPLEMENTED v8.2.0** — Preflight check (`check_ha_connectivity()` in `scripts/preflight.py`), pipeline health check (`--health --source homeassistant`), `home_device_monitor` skill with deterministic critical-device alerting. | P2 |
| F12.6 | **Subscription ROI Tracker** *(v4.0)* — For each active subscription (from F12.1/F3.4), track usage signals and calculate a value-per-dollar score. Usage signals: email engagement (newsletter open/click patterns), login frequency (detected from authentication emails), content consumption (learning platform progress from F10.x). Categories: High ROI (used frequently, clear value), Medium ROI (periodic use), Low ROI (paying but rarely using), Zero ROI (no usage signals in 60+ days). Quarterly report: "You're paying $167/month across 12 subscriptions. 3 subscriptions ($42/month) show zero usage in 60 days: [list]. Cancel or justify?" Integrated into F3.4 subscription ledger and quarterly life scorecard (F15.44). | P1 |

---

### FR-13 · Goal Intelligence Engine

**Priority:** P0
**Summary:** Define, track, and drive progress on personal goals across all life domains. The most distinctive feature of Artha.

*See Section 7 for the full deep-dive specification.*

**Core features (summary):**

| Feature ID | Feature | Priority |
|---|---|---|
| F13.1 | **Conversational Goal Creation** — Define goals through natural language conversation with Claude. User says "I want to make sure we're saving enough for Arjun's college" — Artha infers the goal type, suggests metrics, identifies data sources, and proposes the structured schema for confirmation. The structured schema (Section 7.1) is the storage format; the creation experience is conversational. | P0 |
| F13.2 | **Automatic Metric Collection** — For each goal, Artha identifies the data source that proves progress and pulls it automatically (e.g., finance goal → Fidelity balance; learning goal → Obsidian notes created). | P0 |
| F13.3 | **Goal Progress in Morning Briefing** — Every morning briefing includes 2–3 goal signals: on track, at risk, or behind. | P0 |
| F13.4 | **Weekly Goal Review** — Every Sunday summary includes a full goal scorecard: all active goals, this week's movement, cumulative progress, and trend. | P0 |
| F13.5 | **Goal Cascade View** — Show how sub-goals support parent goals. Financial savings goal ← Monthly budget goal ← Amazon spend target. | P1 |
| F13.6 | **Goal-Linked Alerts** — When an ambient alert fires (e.g., Fidelity balance drop), it links to the relevant goal and shows impact on goal trajectory. | P1 |
| F13.7 | **Recommendation Engine** — When a goal is at risk or behind, Artha surfaces one specific recommended action. Not generic advice — specific and contextual. | P1 |
| F13.8 | **Annual Goal Retrospective** — At year-end, generate a structured review: goals set, goals achieved, goals missed, what contributed to each outcome. | P2 |
| F13.9 | **Goal Conflict Detection** — Detect when two active goals have metrics moving in opposing directions and surface the trade-off explicitly. Example: "Your savings goal is on track but your family travel goal shows zero progress. These may be in tension — do you want to adjust either target?" Also detect resource conflicts: Arjun's SAT prep time vs. Economics Club commitment, or extra work hours conflicting with protected family time. | P1 |
| F13.10 | **Goal Trajectory Forecasting** — For Outcome goals, project the current trend line forward and compare to the target. When projected outcome deviates by >10% from target, proactively suggest adjustment options with specific numbers: (1) Increase effort, (2) Extend deadline, (3) Revise target. Example: "Your net worth goal assumed 8% returns. YTD returns are 3%. At current trajectory, you'll miss by $X. Options: increase monthly savings by $Y, extend to March 2027, or adjust target." | P1 |
| F13.11 | **Behavioral Nudge Engine** — For each habit goal, suggest implementation intentions (specific time, place, trigger), offer to create supporting calendar blocks, track streaks with positive reinforcement, and proactively reduce friction by cross-referencing calendar availability. Turn "exercise 4x/week" into "best open slot is Tuesday 6 PM after Arjun's pickup; schedule it?" | P1 |
| F13.12 | **Dynamic Goal Replanning** — When a goal is persistently behind, propose structured adjustment options rather than just reporting "Behind." Options: keep target and increase effort, keep effort and extend deadline, or revise target based on updated reality. Prevents the "dead goal" problem where behind-status goals are ignored indefinitely. | P1 |
| F13.13 | **Seasonal Pattern Awareness** — After one full year of data, automatically detect cyclical patterns (school year rhythm, holiday spending spikes, tax season, summer travel, visa bulletin cycles) and incorporate them into goal trajectory forecasting. Example: "Amazon spending typically spikes 50% in Nov–Dec. You need to run under budget by $X/month in Q1–Q3 to absorb the holiday spike." | P2 |
| F13.14 | **Implementation Planning** *(v3.9 — Coaching Engine)* — When a goal is created or falls behind, Artha generates a concrete implementation plan: specific next actions, time blocks needed, resources required, and potential obstacles. Not generic advice — contextual to the user's calendar, current obligations, and behavioral patterns. Example: "To hit your exercise 4x/week goal, here's a plan: Mon 6AM (gym opens early, no school drop-off), Wed 6PM (after Arjun pickup), Fri 6AM, Sat 9AM (family schedule clear). First 2 weeks: 3x/week to build habit, then increase." | P1 |
| F13.15 | **Obstacle Anticipation** *(v3.9 — Coaching Engine)* — For each active goal, identify the most likely obstacles based on historical patterns, upcoming calendar events, and seasonal factors. Surface proactively: "Your learning goal is at risk next week — you have 14 calendar events (highest in a month) and Arjun's SAT prep may consume evening time. Consider front-loading a learning session this weekend." Uses pattern of life data (F15.34) and calendar intelligence (F15.31). | P1 |
| F13.16 | **Accountability Patterns** *(v3.9 — Coaching Engine)* — Learn what accountability style works for the user over time. Track which nudge types lead to action (gentle reminder vs. consequence framing vs. streak tracking vs. commitment device). Adapt: if streak messaging drives exercise compliance but deadline framing drives learning, use the right pattern for each goal. Initial mode: try all styles equally for the first 30 days, then weight toward what works. Stored in `memory.md` under `## Coaching Preferences`. | P1 |
| F13.17 | **Goal Sprint with Real Targets** *(v4.0)* — Enforce that every goal has a concrete, measurable `target_value` — no goals with `target_value: 0` or empty targets are allowed in active status. When a goal is created without a target, Artha prompts for one using calibrated defaults per goal type: financial ("What is your target net worth by end of 2026?"), academic ("What GPA is Arjun targeting? Default: 3.5+"), habit ("How many times per week? Default: 3x"), milestone ("What is the specific milestone and target date?"). For existing goals with missing targets, `/goals` command surfaces them: "⚠️ 2 goals have no target: [list]. Set targets now?" Goals without targets cannot contribute to the weekly scorecard or trajectory forecasting. | P0 |
| F13.18 | **Goal Auto-Detection** *(v4.0)* — Infer implicit goals from email patterns, calendar activity, and user behavior — even when the user hasn't explicitly defined them. Detection signals: recurring calendar blocks suggest habits ("You have 'gym' on your calendar 3x/week — is this a fitness goal?"), repeated email searches suggest tracking interest ("You've checked Zillow 4 times this month — house-related goal?"), spending patterns suggest budget goals ("Amazon spend has been decreasing for 3 months — are you targeting a spending reduction?"). Auto-detected goals are proposed as suggestions, never auto-created: "Artha noticed: [pattern]. Would you like to create a goal for this?" If accepted, Artha creates the goal with appropriate metrics and target. If dismissed, adds to `memory.md` dismissed patterns to prevent re-prompting. | P1 |

---

### FR-14 · Work-Life Boundary Guardian

> **⚠ Superseded by Work OS hard separation model (Tech Spec §19.1)**
>
> FR-14's original design assumed the personal surface would infer boundary
> signals from timestamp patterns in the personal email/calendar sync. The
> Work OS (FR-19, Tech Spec §19) replaces this with an explicit two-artifact
> bridge protocol: the personal surface still never reads raw work data, but
> the work surface now maintains its own isolated state (`state/work/`), a
> separate vault key (`work`), and communicates boundary health only via the
> schema-validated `state/bridge/work_load_pulse.json` artifact. All boundary
> enforcement logic (scoring, after-hours thresholds, alerts) lives in
> `prompts/work-boundary.md` on the work surface. The personal catch-up
> consumes only the scalar `boundary_score` [0.0–1.0] from the bridge pulse —
> no meeting names, no attendees, no project or message content cross the
> surface. The F14.1–F14.3 feature IDs remain valid as implementation targets
> but are owned by the Work OS, not the personal catch-up workflow.

**Priority:** P1
**Summary:** Detect when work is encroaching on personal time and surface that signal to Artha without exposing work content.

**The boundary:** Artha never reads work emails, Teams messages, or ADO items. It receives exactly one signal: a work-health indicator. This is the only work-context signal Artha receives.

**How it works:**
- Artha monitors email timestamps to detect work-hours patterns bleeding into personal time
- Artha detects when personal calendar slots are consumed by work (no specifics — just the signal)
- Artha integrates the signal into weekly summaries and goal tracking for work-life balance goals

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F14.1 | **After-Hours Work Signal** — Detect work-related email activity (Work domains, Outlook work account) occurring outside configured work hours (default: before 8am or after 7pm on weekdays, or on weekends). Surface weekly — not per-incident. | P1 |
| F14.2 | **Personal Time Protection** — If a personal calendar block (family dinner, school event, temple) is overridden by a work meeting, flag this in Artha's weekly summary. | P1 |
| F14.3 | **Work-Life Balance Goal** — Create a default goal in the Goal Engine: "Protected personal time ≥ X hours/week." Artha tracks and reports against it. | P1 |

---

### FR-15 · Artha OS Core

**Priority:** P0
**Summary:** The cross-cutting infrastructure that powers all other FRs — the ambient engine, state management, routing, and Artha's identity as a system.

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F15.1 | **Catch-Up Workflow** — User-triggered pull that fetches all new emails, calendar events, and data source updates since the last run. Claude reads each source via MCP tools, processes in batch, updates local Markdown state files, evaluates alert thresholds, and synthesizes a briefing. The entire workflow is orchestrated by Artha.md instructions (loaded via CLAUDE.md) — no custom orchestration code. On each run: (1) decrypt sensitive state files via `vault.py`, (2) fetch unprocessed emails from Gmail/Outlook via MCP AND calendar events in PARALLEL, (3) run `pii_guard.py` pre-flight filter on extracted data before state writes — halt if filter fails, (4) route each item to the appropriate domain prompt, (5) apply §8.2 redaction rules (Layer 2), (6) update state files, (7) evaluate thresholds and generate alerts, (8) synthesize briefing, (9) email briefing to configured address, (10) encrypt sensitive state files via `vault.py`. | P0 |
| F15.2 | **Local State Files** — Markdown files in `~/OneDrive/Artha/state/` storing Artha's world model: one file per domain (immigration.md, finance.md, education.md, etc.) containing structured frontmatter (YAML) and prose sections. All entities, relationships, and current state are captured. State files contain PII-filtered content only — raw PII is replaced by `[PII-FILTERED-*]` tokens before processing (Layer 1) and redacted by LLM rules before writing (Layer 2). Synced via OneDrive with `age` encryption for sensitive domains. Fits within Claude's 200K context window for single-session reasoning across all domains. | P0 |
| F15.3 | **Domain Prompt Library** — Each FR is backed by a domain prompt file in `~/OneDrive/Artha/prompts/`. Artha.md routes incoming data to the correct prompt based on sender/subject/content patterns. Prompts define extraction rules, alert thresholds, state update patterns, and briefing contribution format. Adding a new domain = adding a new prompt file — no code changes. | P0 |
| F15.4 | **Briefing Synthesizer** — Triggered as part of each catch-up run (not scheduled). Collects signals from all domain state files and synthesizes into the structured briefing format. Output delivered to terminal and emailed for cross-device access. | P0 |
| F15.5 | **Weekly Summary** — Generated when the user requests it or as part of a Sunday/weekend catch-up. Collects the week's state changes across all domains and synthesizes the weekly review. | P0 |
| F15.6 | **Human Gate Layer** — All write operations (sending an email, paying a bill, adding a calendar event) require explicit user approval within the Claude Code conversation. Claude proposes the action with full details; user confirms or modifies before execution. Approval history is logged in `~/OneDrive/Artha/state/audit.md`. At Trust Level 2, pre-approved action categories can execute with post-hoc notification. | P0 |
| F15.7 | **Audit Log** — Every action Artha takes or recommends is logged with timestamp, data source, and outcome. Artha's track record is the basis for autonomy elevation. | P1 |
| F15.8 | **Configuration Interface** — Set alert thresholds, delivery channels, work hours, goal parameters, and data source connections. | P1 |
| F15.9 | **Semantic Reasoning Layer** — All alerts, briefings, and recommendations pass through a Claude-powered reasoning step that considers full context before surfacing. This is the difference between a notification system and an intelligence system. Instead of static severity levels, the LLM dynamically assesses priority based on cross-domain context. Example: a low assignment score is informational if the larger pattern shows stable GPA; it becomes meaningful if it follows three recent weak signals. A Metro Electric bill alert on the day you’re flying to India with passport concerns is lower priority than on a normal Tuesday. | P0 |
| F15.10 | **Conversation Memory** — Artha maintains a structured memory of all interactions, preferences, corrections, and decisions. Remembers: questions asked and their resolutions, preferences expressed (“stop alerting me about Spirit Week”), decisions made and rationale (“we decided to refinance if rates drop below X%”), corrections to understanding (“Arjun’s club meeting is biweekly, not weekly”). Feeds into future briefings, alerts, and recommendations. | P1 |
| F15.11 | **Insight Engine** — Runs weekly (or on significant data changes) using Claude’s extended thinking to reason across all domain data and surface 3–5 non-obvious observations. This powers the “ONE THING” in the morning briefing and the “Artha Observations” in the weekly summary. Examples: “Your H-4 EAD renewal is 5 months out. Based on [immigration attorney]’s average processing time from your last two renewals, initiate attorney contact within 3 weeks.” “Arjun’s grade trajectory in AP Language has declined for 3 consecutive grading periods — the seminar format may need different study strategies.” | P1 |
| F15.12 | **Model Tiering Strategy** — Claude Code handles model selection internally. Artha.md specifies intent: use extended thinking for weekly summaries and cross-domain insight generation, standard processing for email parsing and state updates. Prompt caching (system prompts cached across the session) reduces costs. Target: <$50/month at daily catch-up cadence with ~100 emails/day across all accounts. | P1 |
| F15.13 | **[ELIMINATED — v3.0]** Daemon runtime specification removed. Artha runs as an interactive Claude Code session, not a background process. No LaunchAgent, no crash recovery, no heartbeat file. The user triggers each session explicitly. | — |
| F15.14 | **Self-Health Check** — On request ("Artha, are you healthy?"), Artha reports: last catch-up timestamp, state file freshness per domain, any MCP tool connection failures, estimated API cost for the current billing period, and number of unprocessed items. Logged in `~/OneDrive/Artha/state/health.md`. | P1 |
| F15.15 | **Predictive Calendar** — After 6+ months of data, model recurring events and obligations. Proactively add predictions to the calendar with confidence levels. | P2 |
| F15.16 | **Component Registry** — Machine-readable manifest (`registry.md`) of all deployed components: MCP servers, domain prompts, state files, scripts, hooks, slash commands, CLI tools, and action channels. Enables Artha to reason about its own capabilities and detect configuration drift. | P0 |
| F15.17 | **Self-Assessment Dashboard** — Artha tracks its own per-domain accuracy (≥90% target), false positive rate, and tracks which domains need attention. Surfaced via `/status` slash command or on-demand query. | P1 |
| F15.18 | **Extensibility Wizard** — When the user wants to add a new data source, domain, or integration, Artha walks through the appropriate governance checklist (tech spec §12) and sets up the new component with correct registry entries. | P1 |
| F15.19 | **Multi-LLM Orchestration** — Leverages Gemini CLI (free web search, URL summarization) and Copilot CLI (free code/config validation) alongside Claude for cost-aware task routing. Web research tasks (Visa Bulletin, property values, recall checks) are delegated to Gemini at $0 cost. Script/config validation delegated to Copilot at $0 cost. For high-stakes decisions (immigration, finance, estate), all three models generate independent responses and Claude synthesizes the best answer (ensemble reasoning). CLI health monitored in health-check.md with automatic fallback chain. Implementation: tech spec §3.7. | P0 |
| F15.20 | **Action Execution Framework** — Full lifecycle for actions beyond read-only: email composition (general-purpose, not just briefing delivery), WhatsApp messaging via URL scheme (human-gated by OS — user taps send), calendar event creation, email archiving. Every action follows a structured proposal → review → execute → log lifecycle. Action catalog defines trust levels per action type. Autonomy Floor rules are enforced regardless of trust level. Contacts and occasions managed via config files. **Each proposal includes a `friction` field (`low|standard|high`)** — low-friction actions (calendar add, archive) can batch-approve; high-friction actions (financial, immigration) require individual review regardless of trust level. Implementation: tech spec §7.4. | P0 |
| F15.21 | **Visual Message Generation** — AI-generated images via Gemini Imagen CLI for festival greetings (Diwali, Holi, Christmas, New Year), birthday cards, anniversary wishes, and occasion-specific messages. Generated visuals saved to `~/OneDrive/Artha/visuals/` for cross-device access. Can be attached to emails or manually attached to WhatsApp messages. Occasion calendar and visual styles configured in `occasions.md`. | P1 |
| F15.24 | **Decision Graphs** *(v3.8, enhanced v4.0)* — Track cross-domain decisions with full context: what was decided, when, why, what alternatives were considered, and which domains were affected. Auto-generated during cross-domain reasoning (§9.4 step 10). Stored in `state/decisions.md`. Queryable via `/decisions` slash command. Enables: “When did we decide to refinance?” “What were the alternatives we considered for Arjun’s SAT prep?” Prevents re-deliberation of settled questions. **v4.0 — Decision Deadlines:** Every pending decision gets an explicit `deadline` field with countdown in briefings. Decisions without deadlines get a nudge: “This decision has been open for 14 days with no deadline — set one or mark as resolved.” Expired deadlines auto-escalate to 🔴 alerts. | P1 |
| F15.25 | **Life Scenarios** *(v3.8)* — What-if analysis for high-stakes goals and life decisions. Auto-suggested when Artha detects a major decision point (home purchase, job change, immigration status change). Templates: "What if we refinance at X%?", "What if Arjun attends private university vs. in-state?", "What if I-485 is approved in 6 months vs. 18 months?" Scenarios run through affected domain prompts and surface projected impacts. Stored in `state/scenarios.md`. | P1 |
| F15.26 | **Digest Mode** *(v3.8)* — When >48 hours have elapsed since the last catch-up, Artha automatically switches from standard briefing to digest format: priority-tier grouping (Critical → Warning → Notable → FYI), “What You Missed” header with day-by-day summary, and action item consolidation. Prevents information overload after gaps. Triggered automatically; user can also request: “give me the digest.” | P1 |
| F15.27 | **Accuracy Pulse** *(v3.8)* — Weekly self-assessment in the weekly summary: actions proposed vs. accepted vs. declined vs. deferred, corrections logged by user (via memory.md), alerts dismissed without action, domains where extraction accuracy dropped. Enables the user to see whether Artha is getting smarter or drifting. Not an implicit measurement — explicit metadata tracking. | P1 |
| F15.28 | **Data Integrity Guard** *(v3.9)* — Three-layer protection against state file data loss. **Layer 1 — Pre-decrypt backup:** Before `vault.py decrypt` overwrites a .md file with decrypted .age content, back up the existing .md to `.md.bak` if it exists and is newer than the .age file. Prevents data loss when a session modifies .md but crashes before encrypt (leaving stale .age). **Layer 2 — Write verification:** After any state file write, immediately re-read the file and verify the write contains the expected content (at minimum, more data than what existed before). **Layer 3 — Net-negative write guard:** Before writing a state file, compare new content against existing. If the new version has fewer structured entries (YAML keys, table rows, list items) than the old version, HALT the write, log to audit.md, and surface a warning: "⚠️ State write blocked: [domain].md would lose N entries. Review before proceeding." Override: explicit user confirmation. This guard prevents catch-up sessions from accidentally overwriting populated state with templates. | P0 |
| F15.29 | **Life Dashboard Snapshot** *(v3.9)* — Auto-generated `state/dashboard.md` providing a single-glance family status across all domains. Refreshed at the end of each catch-up (step 15b). Structure: one row per domain showing domain name, alert level (🔴🟠🟡🟢), last activity date, key metric (e.g., "GPA 2.7", "H-1B valid 18mo", "$X net worth"), and next action. Also includes family member status rows (one per person: Raj, Priya, Arjun, Ananya) showing their highest-priority item. Dashboard.md is always-load tier and is the first thing read during session quick-start. Enables rapid "where do things stand?" queries without loading all 18 domain files. | P1 |
| F15.30 | **Compound Signal Detection** *(v3.9)* — Cross-domain signal correlation engine that detects convergent patterns. When signals from 2+ domains converge on the same time window or entity, Artha synthesizes them into a compound alert. Examples: immigration deadline + financial pressure + work stress = "⚠️ Compound: You have 3 high-stress domains active simultaneously — consider deferring non-essential commitments." Arjun SAT week + missing assignments + low sleep signals = "⚠️ Compound: Arjun has converging academic pressure — SAT in 3 days with 2 missing assignments." Implementation: during step 10 (cross-domain insights), run a convergence check across all domains with active alerts. Compounds scored higher than individual signals. | P1 |
| F15.31 | **Proactive Calendar Intelligence** *(v3.9)* — Forward-looking calendar analysis beyond simple conflict detection (F8.2). Three capabilities: (1) **Logistics analysis:** For events requiring travel/coordination, proactively surface: drive time, pickup conflicts for kids, weather impact, concurrent family obligations. "Arjun's SAT is at 8 AM Friday — departure by 7:15. Ananya needs drop-off by 7:45. Both parents needed." (2) **Preparation detection:** For events that need advance action (doctor visit = fasting?, travel = passport check?, school event = volunteer sign-up?), surface preparation items 3–5 days ahead. (3) **Energy/load balancing:** Detect weeks with unusually high calendar density and flag: "Next week has 14 events across 5 days — highest in 30 days. Consider rescheduling non-essential items." | P1 |
| F15.33 | **Bootstrap Command** *(v3.9)* — `/bootstrap` slash command for guided cold-start population of empty or template-only state files. When invoked, Artha scans all state files for those still showing `updated_by: bootstrap` or with >50% TODO fields. For each, initiates a structured interview: "I see immigration.md is mostly empty. Let me ask you the key questions: (1) What is your current visa type? (2) When does it expire? (3) Is I-140 filed/approved? ..." Answers are written directly to the state file with `updated_by: user_bootstrap`. Also prompts for high-value data: "Do you have documents in OneDrive I should scan? Any email threads with [immigration attorney] I should search for?" Prevents the "cold start" problem where state files stay empty because catch-ups have no emails to parse for a domain. | P0 |
| F15.34 | **Pattern of Life Detection** *(v3.9)* — After 30 days of catch-up data, Artha builds behavioral baselines: (1) **Spend patterns:** Average daily/weekly/monthly by category, with day-of-week and seasonal components. Enables: "Your Amazon spending is 2.3x your 30-day average this week." (2) **Communication rhythms:** Response times, initiation patterns, contact frequency by tier. Enables: "You typically respond to [immigration attorney] emails within 2 hours — this one has been pending 3 days." (3) **Calendar density:** Normal vs. overloaded weeks, preferred meeting-free blocks. (4) **Goal behavior:** Time-of-week when goal activities typically happen. Patterns are stored in `state/memory.md` under `## Behavioral Baselines` and used by the coaching engine, compound signal detection, and briefing ONE THING selection. | P1 |
| F15.35 | **Signal:Noise Ratio Tracking** *(v3.9)* — For every catch-up, track: emails processed, items surfaced in briefing, items acted upon by user, items dismissed. Calculate per-domain signal:noise ratio (items acted upon / items surfaced). Store in `health-check.md` under `signal_noise:`. When a domain's ratio drops below 40% over a rolling 14-day window: (1) Log the decline, (2) Suggest suppression rule adjustments, (3) Consider auto-demoting that domain's briefing contribution to summary-only. Prevents briefing fatigue where too many low-value items erode trust in high-value ones. Surfaced in `/status` output and Accuracy Pulse. | P1 |
| F15.36 | **Stale State Detection** *(v3.9)* — Automated monitoring of state file freshness relative to expected data flow. For each domain, maintain an `expected_cadence` (e.g., immigration: monthly, finance: weekly, kids: daily during school). When a state file hasn't been updated for 2x its expected cadence and there IS email/calendar data that should have routed there, flag: "⚠️ finance.md hasn't been updated in 21 days but 8 Chase/Fidelity emails were received. Possible routing failure." Auto-heal attempt: re-process the unrouted emails through the domain prompt. Prevents silent domain death where a routing change or email filter causes a domain to stop receiving updates without anyone noticing. | P1 |
| F15.37 | **Consequence Forecasting** *(v3.9)* — For each alert surfaced in the briefing, add a "consequence of inaction" projection at 7, 30, and 90 days. Example: "Metro Electric bill $300.63 due in 3 days → if ignored: late fee $25 (7d), service disruption warning (30d), credit impact (90d)." "H-1B expires in 180 days → if ignored: 90-day filing window missed (90d), status lapse risk." Not every alert needs all three horizons — only critical and urgent alerts get the full projection. Consequence data sourced from domain prompt knowledge (known fee structures, regulatory timelines). Drives the ONE THING scoring: items with severe 90-day consequences score higher. | P1 |
| F15.38 | **Pre-Decision Intelligence Packets** *(v3.9)* — When Artha detects an upcoming decision point (via email analysis, calendar events, goal milestones, or user query), auto-generate a structured research packet. Triggers: mortgage renewal approaching, insurance renewal, college application timeline, large purchase consideration, job change signals. Packet contents: (1) Current state summary from relevant state files, (2) Options with pros/cons (from Gemini web research at $0), (3) Financial impact projection, (4) Timeline and deadlines, (5) Questions to ask (doctor/lawyer/advisor). Packets saved to `summaries/decision-[topic]-[date].md` and referenced in briefings. Prevents reactive decision-making by preparing you before the deadline pressure hits. | P1 |
| F15.39 | **Session Quick-Start** *(v3.9)* — When a new Claude Code session starts, detect the likely session type from the user's first message and optimize context loading accordingly. Three modes: (1) **Catch-up** ("catch me up", "briefing", "SITREP") — full catch-up workflow. (2) **Query** ("how much do I owe on mortgage?", "when is Arjun's SAT?") — load only the relevant domain state file + dashboard.md, skip email fetch. (3) **Action** ("send birthday wish to Rahul", "add event to calendar") — load contacts.md + occasions.md + relevant domain, skip full catch-up. Quick-start reduces time-to-first-response for non-catch-up sessions from ~60s to <10s by avoiding unnecessary context loading. Auto-detected from first message; overridable with explicit slash commands. | P1 |
| F15.40 | **Briefing Compression Levels** *(v3.9)* — Three briefing modes beyond standard and digest: (1) **Full** — current standard briefing with all sections. Default for first catch-up of the day. (2) **Standard** — current default. (3) **Flash** — ultra-compressed 30-second briefing: only 🔴 Critical and 🟠 Urgent items + ONE THING. No domain sections. For second/third catch-ups in a day or when user says "quick update." Auto-selection: first catch-up = Full, subsequent same-day = Flash, >48hr gap = Digest. User override: "give me the full briefing" / "flash briefing" / "just the critical stuff." | P1 |
| F15.41 | **Context Window Pressure Management** *(v3.9)* — Active monitoring of context window utilization during catch-up with graceful degradation. Thresholds: <50% = green (full processing), 50–70% = yellow (compress email bodies, load reference-tier domains as summary only), 70–85% = orange (batch-summarize remaining emails, skip archive-tier domains entirely, compress briefing), >85% = red (save progress, generate partial briefing, recommend re-running for remaining domains). Pressure level displayed in health-check.md. Session quick-start (F15.39) uses pressure-aware loading: if previous session ended at orange/red, next session pre-loads less aggressively. | P1 |
| F15.42 | **OAuth Token Resilience Framework** *(v3.9)* — Proactive token health monitoring and recovery. Three capabilities: (1) **Pre-expiry refresh:** Check token expiry on every catch-up pre-flight (step 0). If any token expires within 7 days, attempt automatic refresh. Log result. (2) **Graceful degradation path:** If a token fails, the catch-up continues with available sources and clearly labels what's missing in the briefing. Never hard-fail on a single token. (3) **Guided re-auth flow:** When re-authentication is needed, provide step-by-step terminal commands with context: "Gmail token expired. Run: `python3 scripts/setup_google_oauth.py` — this will open a browser for OAuth consent. Takes ~2 minutes." Track token health history in health-check.md to detect patterns (e.g., tokens that expire every 7 days vs. 90 days). | P1 |
| F15.43 | **Email Volume Scaling** *(v3.9)* — Progressive strategies for handling email volume increases without catch-up degradation. Tier 1 (≤100 emails): Current individual processing. Tier 2 (100–300 emails): Batch-summarize by sender domain in groups of 20, expand only flagged items. Tier 3 (300–500 emails): Pre-classify by importance (sender reputation + subject keywords), process top 30% individually, batch-summarize bottom 70%. Tier 4 (>500 emails): Digest-only mode — one paragraph per domain, no individual email processing, flag count of unprocessed. Volume tier detected automatically during email fetch (step 4). Each tier's thresholds are configurable in settings.md. Prevents catch-up failures when returning from vacation or after extended gaps. | P1 |
| F15.44 | **Life Scorecard** *(v3.9)* — Quarterly and annual comprehensive life assessment aggregating data across all domains. Generated at end of each quarter (March, June, September, December) and annually. Sections: (1) **Goal Performance** — each active goal with trend line, (2) **Domain Health Matrix** — 18 domains rated 🟢🟡🔴 with key metric, (3) **Family Well-being** — per-member status summary, (4) **Financial Position** — net worth trajectory, savings rate, debt trajectory, (5) **Time Allocation** — where time went (work vs. family vs. personal vs. learning), (6) **Relationship Health** — contact frequency trends, reconnects overdue, (7) **Risk Dashboard** — immigration timeline, insurance adequacy, estate readiness, (8) **Year-over-Year Comparison** (annual only). Saved to `summaries/scorecard-YYYY-QN.md`. Designed as the "annual physical for your life." | P1 |
| F15.46 | **Post-Briefing Calibration Questions** *(v4.0)* — After each briefing, Artha asks 1–2 targeted calibration questions to improve accuracy and learn preferences. Questions are specific, not generic: "I surfaced 3 ParentSquare items today. Were any of them actually useful, or should I suppress that sender?" "I rated the Metro Electric bill as 🟠 Urgent. Was that the right severity, or is it on autopay and should be 🔵 Info?" "I didn't surface anything from your learning domain this week. Is that because nothing happened, or because I'm missing data?" Questions selected based on: domains with lowest signal:noise ratio, new senders being classified for the first time, alerts that were dismissed in prior sessions, and domains where the user has made corrections. Answers feed directly into `memory.md` corrections and routing rules. Limited to 2 questions per session to avoid fatigue. | P1 |
| F15.47 | **PII Detection Footer** *(v4.0)* — Every briefing includes a footer section showing PII guard statistics for the current catch-up run. Displays: total items scanned, PII patterns detected (by type: SSN, CC, account numbers, passport, etc.), items filtered, zero leaks confirmed. Provides transparency into the pre-flight PII defense layer (Layer 1). Example: "🛡️ PII Guard: 47 emails scanned · 3 detections (2 CC, 1 routing#) · 0 leaks · Layer 2 LLM redaction: clean." If any PII detection occurs, the specific state file and domain are noted (without revealing the PII itself). Builds user trust in the privacy model. | P1 |
| F15.48 | **Effort Estimates & Power Half Hour** *(v4.0)* — Every open item in `open_items.md` gets an estimated effort level: ⚡ Quick (≤5 min), 🔨 Medium (5–30 min), 🏗️ Deep (30+ min). Effort estimated by Artha based on task type and historical completion patterns. Powers the **"Power Half Hour"** concept: when Artha detects a 30-minute calendar gap (or user asks "what can I knock out?"), it assembles a batch of ⚡ Quick items that can be completed in that window. Example: "You have 30 minutes before your 3 PM. Power Half Hour: (1) ⚡ Reply to school fundraiser email [2 min], (2) ⚡ Confirm Arjun's dentist appointment [3 min], (3) ⚡ Review Costco membership renewal notice [5 min], (4) 🔨 Update HSA contribution election [15 min]. Total: ~25 min." Effort data also feeds into calendar-aware task scheduling (F8.6). | P1 |
| F15.49 | **Quarterly Privacy Audit Report** *(v4.0)* — Automated quarterly self-assessment of Artha's privacy posture. Report covers: (1) **Data inventory** — what state files exist, what data types they contain, encryption status, (2) **Access audit** — which OAuth tokens are active, when last refreshed, what scopes granted, (3) **PII filter effectiveness** — rolling stats from F15.47, detection accuracy, false positive rate, (4) **Data minimization check** — any state files storing more raw data than necessary? Any email bodies persisted when they should be extract-and-discard?, (5) **Encryption audit** — all high/critical state files encrypted? Keys in Keychain? No plaintext PII anywhere?, (6) **Third-party surface** — what data flows to Claude API, Gemini CLI, Copilot CLI? What mitigations are active? Saved to `summaries/privacy-audit-YYYY-QN.md`. Surfaced in quarterly life scorecard. | P1 |
| F15.50 | **Monthly Retrospective** *(v4.0)* — Auto-generated monthly summary synthesizing what happened across all domains. Generated during the first catch-up after the 1st of each month. Format: (1) **Month in Review** — 5 most significant events/decisions per domain, (2) **Goals Progress** — each goal's monthly trajectory with delta from prior month, (3) **Financial Summary** — income, expenses, net worth change, budget adherence, (4) **Family Highlights** — per-member notable events, (5) **Artha Performance** — accuracy pulse monthly roll-up, signal:noise trends, domains that improved/degraded, (6) **Next Month Preview** — known deadlines, renewals, and preparation items. Saved to `summaries/retro-YYYY-MM.md`. Shorter and more actionable than the quarterly scorecard; complements the weekly summary by providing a longer-horizon view. | P1 |
| F15.51 | **State Diff Command** *(v4.0)* — `/diff` slash command that shows what changed in state files since the last catch-up. For each modified state file, displays: fields added, fields changed (with before/after), fields removed, and new alerts generated. Enables the user to see exactly what Artha learned during the most recent catch-up without reading full state files. Example output: "Since last catch-up (2h ago): immigration.md: no changes · finance.md: Metro Electric bill added ($300.63 due 3/26) · kids.md: Arjun missing assignment flagged (AP Language) · goals.md: exercise goal moved from 🟢 to 🟡 (missed 2 sessions)." Diff data computed by comparing state files before and after catch-up processing. | P1 |
| F15.52 | **Ask Priya Delegation** *(v4.0)* — When Artha encounters a question or decision that requires Priya's input (shared domain: finance, kids, home, health, social, travel), route the question to Priya with full context via email or WhatsApp. Format: "Hey Priya — Artha has a question for you: [context summary]. [Specific question]. Reply to this message and Artha will incorporate your answer." Triggers: during catch-up when a shared-domain decision needs both spouses' input, during goal reviews for family goals, when a calendar conflict affects both parents. User must approve each delegation (human gate). Response processing: Priya's reply is parsed and incorporated into the relevant state file on the next catch-up. | P1 |
| F15.53 | **If You Have 5 Minutes** *(v4.0)* — Opportunistic micro-task suggestions surfaced when the user has a brief window. Triggered by: (1) User explicitly asks "what can I do in 5 minutes?", (2) Calendar shows a short gap between events, (3) End of a catch-up session with remaining time. Suggestions drawn from: ⚡ Quick open items (F15.48), overdue reconnects that need just a text/WhatsApp (F11.3), quick approvals pending in audit queue, brief calibration questions. Presented as a prioritized list: "5-minute wins: (1) Text happy birthday to Rahul (overdue 2 days), (2) Approve Costco membership auto-renewal, (3) Quick reply to school fundraiser ask. Time: ~4 min total." Different from Power Half Hour (F15.48) which batches for 30 min; this is micro-optimized for 5-minute opportunistic windows. | P1 |
| F15.54 | **Teach Me Mode** *(v4.0)* — When user says "teach me about [domain]" or "explain my [domain] situation", Artha synthesizes an explainer from state data, domain prompts, and prior decisions. Not a generic explanation — personalized to the user's actual data. Examples: "Teach me about my immigration situation" → timeline visualization of the GC process, where the family stands, what's next, what the risks are, explained in plain language. "Teach me about my insurance coverage" → what each policy covers, known gaps, how they interrelate. "Explain my mortgage" → current balance, payment structure, interest vs. principal, payoff timeline, refinance math. Designed for moments when Raj or Priya need to understand a complex domain they don't interact with daily. Uses extended thinking for synthesis. | P1 |
| F15.55 | **Natural Language State Queries** *(v4.0)* — Enhanced conversational access to state files using natural language questions that Artha resolves against structured state data. Goes beyond simple lookups to support: temporal queries ("What happened with immigration last month?"), comparison queries ("How does this month's spending compare to January?"), aggregation queries ("Total we've spent on kids' activities this year"), conditional queries ("What bills are due if I'm traveling next week?"), and cross-domain queries ("Show me everything related to Arjun right now"). Artha decomposes the query, identifies relevant state files, extracts the data, and synthesizes a coherent answer. Replaces the need for users to know which state file contains which data. | P1 |
| F15.56 | **GFS Vault Backup** *(v4.1 — P0)* — Grandfather-Father-Son rotating backup of all encrypted vault files (`*.md.age`). Triggered automatically at the end of every successful `vault.py encrypt` cycle. **Hierarchy:** daily (last 7 per domain), weekly/Sunday (last 4), monthly/month-end (last 12), yearly/Dec-31 (unlimited). **Storage:** `state/backups/{tier}/{domain}-YYYY-MM-DD.md.age` — all files are encrypted at rest, sync via OneDrive automatically. **Manifest:** `state/backups/manifest.json` stores SHA-256 checksum, size, domain, tier, date, `source_type`, and `restore_path` for every backup file; updated atomically on each snapshot. **Restore validation (monthly mandatory):** `vault.py validate-backup` decrypts the newest backup per domain into a temp directory (never touches live state) and checks: SHA-256 matches manifest, decrypt succeeds, non-empty, YAML frontmatter present, word count ≥ 30. Logs `BACKUP_VALIDATE_OK`/`FAIL` to `audit.md`. `vault.py health` warns if last validation is > 35 days ago. **CLI:** `backup.py status`, `backup.py validate [--domain X] [--date D]` — `vault.py backup-status` and `vault.py validate-backup` forward to these for backward compatibility. Provides 3-2-1 coverage. | P0 |
| F15.57 | **Comprehensive Backup Registry** *(v4.2 — P0)* — Extends GFS backup from encrypted state only to **all 31 state files + 4 config files**, enabling a full fresh-install restore from backup alone. **Registry:** declared in `config/user_profile.yaml → backup` section — authoritative, user-editable (users without certain domains simply remove entries). **Three source types:** (1) `state_encrypted` — `.age` files copied directly; (2) `state_plain` — plain `.md` files encrypted on-the-fly by `age_encrypt` before storing; (3) `config` — config files encrypted on-the-fly. **Config files protected by default:** `user_profile.yaml`, `routing.yaml`, `connectors.yaml`, `artha_config.yaml`. **Fresh-install restore:** `backup.py restore --date YYYY-MM-DD [--dry-run]` (or `vault.py restore` — forwards to `backup.py` for backward compatibility) — decrypts and writes all backup files back to their original locations; SHA-256 verified before each write; `--dry-run` previews without writing. **New manifest fields:** `source_type` and `restore_path` per entry. | P0 |
| F15.58 | **ZIP-per-snapshot Backup Architecture** *(v4.3 — P0)* — Upgrades GFS storage from individual `.age` files to a single **ZIP archive per tier-day** containing all registered files. **Location:** `backups/` at project root (moved from `state/backups/`). **Format:** `backups/{tier}/YYYY-MM-DD.zip` — self-contained with an internal `manifest.json` (SHA-256, source_type, restore_path per file). **Portability:** a single ZIP is the complete snapshot for that date — easy to transfer between machines. **Two-tier manifests:** outer `backups/manifest.json` catalogs ZIPs; internal `manifest.json` inside each ZIP enables restore without catalog access. **New command:** `vault.py install <zipfile> [--dry-run]` — restores all files from an explicit ZIP path for cold-start on a new machine, reading the internal manifest directly. **Module separation (v4.4):** GFS engine extracted to `backup.py` (standalone CLI); shared primitives to `foundation.py`; `vault.py` retains session lifecycle only. See §8.5.2 for three-module architecture. | P0 |
| F15.59 | **Key Export/Import for Cold-Start Recovery** *(v4.4 — P0)* — Enables disaster recovery on a new machine by providing a secure workflow to export and import the age private key. **Export:** `python scripts/backup.py export-key` — prints the raw AGE-SECRET-KEY-… to stdout for storage in a password manager or fire-safe printout. **Import:** `python scripts/backup.py import-key` — reads key from stdin (paste + Ctrl-D), stores it in the system keychain (macOS Keychain / Windows Credential Manager). **Cold-start workflow:** install age → run `backup.py import-key` → run `backup.py preflight` → run `backup.py install <zip>`. **`foundation.py`:** shared leaf module (`_config` dict, path constants, `log()`/`die()`, key management, crypto primitives) extracted from `vault.py` to enable single-point test patching via `monkeypatch.setitem(foundation._config, …)`. | P0 |
| F15.60 | **Domain Registry + Lazy Loading** *(v5.2 — P0)* — `config/domain_registry.yaml` is the authoritative manifest for all 24 domains. Each entry declares sensitivity, `always_load` flag, phase, prompt/state file paths, `requires_vault`, `household_types` filter, routing keywords, and alert thresholds. **Lazy loading:** 6 always-load domains (finance, immigration, health, calendar, comms, goals) load every session; 18+ remaining domains load only if an email routes to them — reducing context by >30% on quiet days. `profile_loader.py` exposes `domain_registry()`, `available_domains(household_type)`, and `toggle_domain(name, enabled)`. | P0 |
| F15.61 | **State Schema Migration System** *(v5.2 — P1)* — `scripts/migrate_state.py` provides a migration DSL for YAML front-matter in state files. Operations: `AddField`, `RenameField`, `DeprecateField`. Migration registry maps `(from_version, to_version)` tuples to operation lists. `apply_migrations()` is called automatically by `upgrade.py`. CLI supports `--dry-run`, `--check`, `--verbose`. | P1 |
| F15.62 | **Household Type + Single-Person Mode** *(v5.2 — P1)* — `user_profile.schema.json` adds a `household` section: `type` enum (single/couple/family/multi_gen/roommates), `tenure` (owner/renter/other), `adults` count, `single_person_mode` boolean. When `type=single`, briefings suppress family/spouse/kids language. Each domain in the registry declares `household_types` to filter irrelevant domains. | P1 |
| F15.63 | **Renter Mode** *(v5.2 — P1)* — When `household.tenure=renter`, the home domain `## Renter-Overlay` section in `prompts/home.md` activates. Suppresses: mortgage, property tax, HOA. Adds: rent tracking (alert ≤5 days), lease expiry alerts (120/90/60/30 days), renter's insurance, maintenance requests. Renter-specific YAML state schema and briefing format included. | P1 |
| F15.64 | **`/domains` Command** *(v5.2 — P1)* — Lists all 24 domains with enabled/disabled status, sensitivity, and household filter. Sub-commands: `/domains enable <name>`, `/domains disable <name>` (no YAML editing required), `/domains info <name>`. Changes take effect on next catch-up. Backed by `profile_loader.available_domains()` and `toggle_domain()`. | P1 |
| F15.65 | **Pet Reminders — Phase 1** *(v5.2 — P1)* — `prompts/pets.md` delivers date-driven pet care reminders from profile fields every catch-up: vaccination due, parasite prevention refill, annual wellness exam, license renewal. Alert tiers: 🔴 overdue / 🟠 within 7d / 🟡 within 30d. Multi-pet YAML state template at `state/templates/pets.md`. Full email-routing domain deferred to Phase 2. | P1 |
| F15.66 | **Passport Expiry Skill** *(v5.2 — P1)* — `scripts/skills/passport_expiry.py` reads decrypted `state/immigration.md`, regex-parses passport expiry dates for all family members, and fires alerts at 180/90/60 days. `requires_vault: true`. Alert levels: ok / standard / urgent / critical. | P1 |
| F15.67 | **Subscription Price Watcher** *(v5.2 — P1)* — `scripts/skills/subscription_monitor.py` reads `state/digital.md`, compares against a session cache, and alerts on price increases >1% and trial-to-paid conversions within 7 days. Extends F1.5 and F12.1 with proactive detection. | P1 |
| F15.68 | **RSS Feed Connector** *(v5.2 — P2)* — `scripts/connectors/rss_feed.py` adds RSS 2.0 / Atom 1.0 support (stdlib only). Configured via `connectors.yaml::rss_feed.fetch.feeds`. Useful for regulatory feeds (USCIS, TSA). Disabled by default. | P2 |
| F15.69 | **Offline/Degraded Mode** *(v5.2 — P1)* — `Artha.core.md` Step 4e classifies each session as `normal`, `degraded`, or `offline`. Dedicated briefing templates: §8.10 offline (state-only, date-driven skills still run), §8.11 degraded (data-gaps section + recovery suggestions). Session mode logged to `state/health-check.md`. | P1 |
| F15.70 | **Performance Telemetry + Per-Domain Hit Rate** *(v5.2 — P1)* — `state/health-check.md` run entries extended with: `session_mode`, `domains_loaded`, `domains_skipped`, `domain_hits`, `connector_timing_ms`, `skill_timing_ms`, `domain_hit_rates`. Hit rate <60% after ≥10 catch-ups surfaces as ⚠ in `/health`. Addresses R16 (domain extraction quality degradation under context pressure). | P1 |
| F15.71 | **Script-Backed View Commands** *(v5.2 — P1)* — Four new deterministic view scripts: `status_view.py` (health-check stats + run history), `goals_view.py` (scorecard table with progress bars), `items_view.py` (priority-grouped with `--quick` and `--domain` filters), `scorecard_view.py` (5-dimension weekly scorecard from health-check + goals + open-items). All accept `--format flash|standard|digest`. No vault required (plain state files only). | P1 |
| F15.72 | **Novice UX: Setup Flow Completeness** *(v5.3 — P0)* — README Quick Start restored to a complete, sequential 7-step flow: Step 6 (Run Preflight Check) was absent causing first catch-up failure. Step 4 reordered to prevent age key deletion before public key is pasted: `age-keygen` → ⚠ copy public key callout → paste into profile → store-key → `vault.py status` verification gate → `rm` key file only after confirmation. Node.js 18+ added as an explicit prerequisite (required by Claude Code and Gemini CLI). | P0 |
| F15.73 | **Novice UX: Platform Parity** *(v5.3 — P1)* — Windows and Linux instructions promoted from inline bash comments to `<details>` collapsible blocks with proper syntax-highlighted, copy-pasteable code. Affects: venv activation (Step 1), key file deletion (Step 4), Linux age install commands (Fedora/Arch/Debian/binary fallback). System keyring row added to prerequisites table with headless-Linux `pip install keyrings.alt` guidance. | P1 |
| F15.74 | **Novice UX: AI CLI Guidance** *(v5.3 — P1)* — AI CLI prerequisites row updated with `npm install -g` install commands for each CLI, a `Which AI CLI should I use?` callout recommending Claude Code or Gemini CLI for beginners, and a note that GitHub Copilot requires VS Code + a paid subscription. Command alias note added to Step 7 explaining `catch me up` / `/catch-up` / `catchup` are synonyms. | P1 |
| F15.75 | **Novice UX: Google OAuth Deep Links** *(v5.3 — P1)* — Step 5 Google OAuth instructions replaced with four direct `console.cloud.google.com` deep-links (projectcreate, API library ×2, OAuth consent screen, credentials). New `docs/google-oauth-setup.md` provides a full 5-step illustrated walkthrough with troubleshooting for "This app isn’t verified". | P1 |
| F15.76 | **Novice UX: open_items.md Bootstrap** *(v5.3 — P0)* — `state/templates/open_items.md` created (was the only always-load file without a template). `check_open_items()` in `preflight.py` updated to auto-create from template when `--fix` is passed, removing the broken `T-1A.11.1` spec reference. | P0 |
| F15.77 | **Novice UX: Operational PII Hardening** *(v5.3 — P1)* — Three PII improvements: (1) `preflight.py` `_rel()` helper masks all absolute filesystem paths in console output (replaces OS username + home dir with `$ARTHA_DIR/` prefix). (2) `config/user_profile.example.yaml` location defaults changed from Bellevue/King County WA to Springfield/Sangamon IL with fictional coordinates; immigration context replaced with YYYY-MM-DD placeholder. (3) `demo_catchup.py` footer corrected from dead `python scripts/_bootstrap.py` to `Open your AI CLI and type /bootstrap`. Keyring backend check added as first P0 preflight gate on Linux. | P1 |
| F15.78 | **Novice UX: Setup Timing + Activation Clarity** *(v5.4 — P0)* — Five friction improvements: (1) Quick Start header updated to `~30 minutes` with per-step breakdown. (2) Preflight `NO-GO` caveat callout moved to appear *before* the command. (3) Venv explainer box added. (4) `grep "^age1"` tip added to recover scrolled-away public key. (5) YAML double-quote tip for `age_recipient`. | P0 |
| F15.79 | **Novice UX: Demo-First Onboarding** *(v5.4 — P1)* — Demo mode callout added immediately after `pip install -r scripts/requirements.txt` (before Step 2 heading), letting users see Artha output with fictional data before committing to account setup. | P1 |
| F15.80 | **Novice UX: Placeholder Guard** *(v5.4 — P0)* — `generate_identity.py` `_validate()` now rejects example placeholder data: `family.primary_user.name == "Alex Smith"` and `emails.gmail == "alex.smith@gmail.com"` produce actionable ERROR messages. 2 new `TestValidate` tests. Prevents silent misconfiguration. | P0 |
| F15.81 | **Novice UX: Windows Parity + Contributor Hygiene** *(v5.4 — P1)* — (1) venv creation wrapped in OS `<details>` block with `python3` (macOS/Linux) and `python` (Windows PowerShell) variants. (2) `git config core.hooksPath .githooks` moved to a "Contributors/forkers only" blockquote outside the main flow. | P1 |
| F15.82 | **Novice UX: Profile Completeness** *(v5.4 — P1)* — `config/user_profile.example.yaml` gains a `# ─── Household ───` section (type/tenure/adults) between `children` and `# ─── Location ───`, making household context visible and editable without consulting the schema. | P1 |
| F15.83 | **Novice UX: vault.py Discoverability** *(v5.4 — P1)* — `vault.py` now accepts `--help`/`-h`/`help` → exits 0 + prints usage. `_print_usage(exit_code)` extracted as shared helper. Unknown command prints to stderr. README reference updated. 5 new `TestHelpAndUsage` tests. | P1 |
| F15.84 | **Novice UX: Setup Expectations** *(v5.4 — P0)* — Step 6 preflight note replaced with a 4-row expected-results table (vault/Gmail/pipeline/send, fresh-install state, when-it-resolves). Eliminates confusing "complete Steps 4 and 5 first" text. | P0 |
| F15.85 | **Novice UX: AI CLI Cost Transparency** *(v5.4 — P1)* — "Which AI CLI?" callout rewritten with per-tool tier detail: Gemini (free tier), Claude Code (rate-limited free / Pro $20/mo), Copilot (free in VS Code / Pro $10/mo). Replaces blanket "free to try" claim. | P1 |
| F15.86 | **Novice UX: Google OAuth Safety Explanation** *(v5.4 — P0)* — "This app isn't verified" warning callout expanded with safety rationale (own app, own account, data stays local, no third parties). Click path clarified. Links to `docs/google-oauth-setup.md` screenshot walkthrough. | P0 |
| F15.87 | **Privacy: Mosaic PII Risk Documentation** *(v5.4 — P1)* — `docs/security.md` gains §6 Mosaic PII Risk documenting that `cultural_context + immigration.context` can form a demographic fingerprint (mosaic effect). Guidance for forkers and public config sharers. | P1 |
| F15.88 | **10-Layer Defense-in-Depth** *(v5.5 — P0)* — Comprehensive protection against 10 identified data loss scenarios for state files. **Layer 1 — Advisory file lock:** OS-level `flock` (POSIX) / `msvcrt.locking` (Windows) prevents concurrent encrypt/decrypt via `_with_op_lock` decorator. **Layer 2 — Cloud sync fence:** Detects OneDrive/Dropbox/iCloud workspace, samples `.age` mtimes with 2s delay, waits for quiescence before vault operations. **Layer 3 — Post-encrypt verification:** After each `age -r` call, verifies `.age` output ≥ plaintext size; aborts and lockdowns on truncation. **Layer 4 — Deferred plaintext deletion:** `.md` files only removed after *all* domains encrypt successfully; partial encrypt preserves plaintext. **Layer 5 — Encrypt-failure lockdown:** On encrypt failure, remaining plaintext files set to `chmod 000` preventing cloud sync; restored at next decrypt. **Layer 6 — Auto-lock mtime guard:** `auto-lock` checks if any `.md` was modified within 60s; defers encryption by refreshing lock TTL if active writes detected. **Layer 7 — Net-negative override:** `ARTHA_FORCE_SHRINK=1` (or `=domain`) env var bypasses the 80% size check; old `.age` pinned to `.age.pre-shrink` for recovery. **Layer 8 — GFS prune protection:** Before deleting a ZIP, verifies every domain checksum exists in at least one other retained snapshot; sole-carrier ZIPs are pinned. **Layer 9 — Confirm gate:** `restore` and `install` require `--confirm` flag (or `--dry-run` for preview) before writing; prevents accidental overwrites. Pre-restore backup of live files saved to `backups/pre-restore/`. **Layer 10 — Key health monitoring:** `vault.py health` validates key format (`AGE-SECRET-KEY-` prefix) and warns if key has never been exported (`last_key_export` in manifest). 501 tests, all passing. | P0 |
| F15.89 | **Onboarding: Minimal Starter Profile** *(v5.6 — P0)* — `config/user_profile.starter.yaml` (45 lines) created as the first-run default. Replaces the 234-line `user_profile.example.yaml` for new users. Contains only essential fields: name/email (blank for forced fill-in), household type, timezone, 8 default domains enabled, integration stubs, briefing settings, encryption placeholder. `_validate()` rejects the blank name/email so users must fill in real data. Setup wizard (`artha.py --setup`) and `setup.sh` non-wizard path both use this file as the template. | P0 |
| F15.90 | **Onboarding: Interactive Setup Wizard** *(v5.6 — P0)* — `artha.py do_setup()` provides an interactive wizard that collects: name (required), email (@ validation), timezone (accepts `ET`/`CT`/`MT`/`PT`/`IST`/`UTC` shortcuts, expands to full IANA name), household type (single/couple/family), and up to 6 children (name/age/grade). Writes `config/user_profile.yaml` as a clean formatted YAML (not yaml.dump), auto-runs `generate_identity.py`, and prints a success box. `--no-wizard` flag copies the starter profile for manual editing. Accessible at any time via `python artha.py --setup`. | P0 |
| F15.91 | **Onboarding: No Cognitive Whiplash on Welcome** *(v5.6 — P0)* — `artha.py main()` configured path previously called `do_preflight()` immediately after `do_welcome()`, producing a jarring `✅ Welcome` → `⛔ NO-GO` sequence. Fix: configured path now calls `do_welcome()` and returns 0. Preflight is explicit only (`python artha.py --preflight` or `python scripts/preflight.py`). | P0 |
| F15.92 | **Onboarding: Advisory Warnings for Placeholder Data** *(v5.6 — P1)* — `generate_identity.py` gains `_collect_warnings(profile) -> list[str]` for non-blocking advisory detection: (1) placeholder child names (`Child1`, `Child2`, `ChildName`, `Child`) flagged with indexed path (`family.children[0].name is still placeholder 'Child1'`); (2) placeholder cities (`Springfield`, `Anytown`, `Your City`, `Exampleville`) flagged. Warnings printed as `⚠ Advisory warnings (non-blocking):` section but do NOT prevent generation. `_print_validate_summary(profile)` added to show an identity preview (name, email, location, enabled domains) when `--validate` passes. 11 new tests. | P1 |
| F15.93 | **Onboarding: First-Run Preflight Mode** *(v5.6 — P0)* — `preflight.py` gains `--first-run` CLI flag and `_is_expected_on_first_run(check)` helper. When `--first-run` is active: (1) header becomes `━━ ARTHA SETUP CHECKLIST ━━━`; (2) OAuth/connector failures detected via `fix_hint` content (setup_google_oauth/setup_msgraph_oauth/setup_icloud_auth) display as `○ [P0] Gmail OAuth token: not yet configured` instead of `⛔ NO-GO`; (3) exit 0 when only expected setup steps remain. Truly unexpected P0 failures (broken PII guard, bad state dir) still block. `README.md` updated with `--first-run` usage. | P0 |
| F15.94 | **Onboarding: setup.sh Wizard Integration** *(v5.6 — P0)* — `setup.sh` removes the 234-line `user_profile.example.yaml` copy step. After the demo briefing, detects interactive terminal (`[ -t 0 ] && [ -t 1 ]`) and prompts: `"Run the 2-minute setup wizard now? [yes/no]"`. YES → `python artha.py --setup`; NO → copies `user_profile.starter.yaml` + compact 3-step next-steps card with `python artha.py --setup` reminder. Non-interactive (CI/piped) path silently copies starter. All paths verified with `bash -n setup.sh`. | P0 |
| F15.95 | **OOBE: setup.sh Brand Mark + Step Counters** *(v5.7 — P0)* — `setup.sh` gains a header banner `A R T H A  —  Personal Intelligence OS` in bold ANSI, and explicit progress counters `[1/4] Checking prerequisites...` through `[4/4] Running demo briefing...`, making each phase of setup visible. `pip install` gains `--disable-pip-version-check` to suppress the internal OneDrive path that was leaking in upgrade notices. Venv creation and pip install separated into distinct numbered steps with pass/fail indicators. | P0 |
| F15.96 | **OOBE: AI CLI Auto-Detection** *(v5.7 — P0)* — `artha.py` gains `_AI_CLIS` constant (list of `(cmd, name, url)` tuples), `_detect_ai_clis() -> list[tuple[str, str, bool]]` (uses `shutil.which`, stdlib-only, no new deps), and `_print_ai_cli_status()` which prints a tailored 'Your next step:' block after wizard completion and on every `do_welcome()` call. Detects `claude`, `gemini`, and `code` (VS Code / GitHub Copilot). If at least one is found: lists detected names, shows `→ Run: <cmd>  (then say: catch me up)`. If none found: shows install URLs for all known CLIs. Eliminates the UX dead-end where new users complete setup but don't know which command to run next. | P0 |
| F15.97 | **OOBE: Colorized Demo Briefing** *(v5.7 — P1)* — `scripts/demo_catchup.py` `render_briefing()` gains ANSI color helpers gated on `sys.stdout.isatty()`: `action(text)` (yellow `ACTION:` prefix), `good(text)` (green bullet — positive news), `alert(text)` (red bullet — time-sensitive items), with `BOLD` section headers. Removed dead 'Fast way: bash setup.sh' footer (user already ran it). Added privacy line: `🔒 Your data stays on this machine. Artha never phones home.` before closing separator. Turns monochrome fixture output into a visually scannable briefing that communicates signal vs. noise at a glance. | P1 |
| F15.98 | **OOBE: README Compression + docs/backup.md + specs/README.md** *(v5.7 — P0)* — `README.md` compressed from 624 lines to 142: hero section retains tagline 'Your life, organized by AI.', quick start (3 commands + Windows/Advanced `<details>` blocks), 'What You Get' bullet list, dev commands, docs table. All extended content (Backup & Restore, Architecture, Telegram, Project Structure, Migration) removed from README and the detailed Backup & Restore content moved to `docs/backup.md` (new file). `specs/README.md` created with disclaimer: all personal names/data in specs/ are fictional examples (the Patel family), not real individuals; real data lives in `config/user_profile.yaml`. | P0 |
| F15.99 | **OOBE: Wizard Completion Box + Privacy Line + make start** *(v5.7 — P0)* — `artha.py do_setup()` completion redesigned: bordered box `┌─...─┐` with `✓ Artha knows who you are now.`, `Next: open your AI assistant and say: catch me up`, and `🔒 Your data stays on this machine. Artha never phones home.` — followed by `_print_ai_cli_status()` and optional next-steps (Google OAuth, age setup, /bootstrap). `do_welcome()` similarly shows privacy assurance at bottom. `Makefile` gains `start` target (`@bash setup.sh`) added to `.PHONY` and listed in `make help` output. | P0 |
| F15.100 | **Financial Resilience Skill** *(v5.8 — P1)* — `scripts/skills/financial_resilience.py`: parses `state/finance.md` for monthly expenses, liquid savings, and income sources using regex patterns. Computes burn rate, emergency fund runway in months, and a single-income stress scenario. `compare_fields = ["runway_months", "burn_rate_monthly", "single_income_runway_months"]` drives delta alerts. Registered in `config/skills.yaml` (cadence: weekly, requires_vault: true). Added to `_ALLOWED_SKILLS` in `skill_runner.py`. | P1 |
| F15.101 | **Gig & Platform Income Tracking (1099-K)** *(v5.8 — P1)* — `prompts/finance.md` gains a "Gig & Platform Income Tracking" section with per-platform running totals table and alert thresholds: 🟡 >$5K single platform, 🟠 >$20K cumulative, 🔴 Q4 year-end risk reminder. `config/domain_registry.yaml` gains routing keywords for Stripe, PayPal, Venmo, Upwork, Fiverr, Etsy, DoorDash, Uber earnings, 1099-K, 1099-NEC, payout, earnings summary, direct deposit. | P1 |
| F15.102 | **Purchase Interval Observation** *(v5.8 — P2)* — `prompts/shopping.md` gains a "Purchase Interval Observation" section: tracks recurring purchase patterns and surfaces recommendations like switching to subscriptions or bulk buying with 🔵 note format and observation date. | P2 |
| F15.103 | **Structured Contact Profiles** *(v5.8 — P1)* — `prompts/social.md` gains a 9-field contact profile template (name, relationship, last_contact, next_action, location, birthday, key_facts, shared_history, communication_style). Artha extracts and updates these from email/calendar context. | P1 |
| F15.104 | **Pre-Meeting Relationship Context Injection** *(v5.8 — P1)* — When a calendar event references a known contact (from F15.103 profiles), Artha injects a 📅 briefing block: relationship context, last discussed topics, suggested talking points, and any pending items. Rule: only inject for contacts with ≥3 profile fields populated. | P1 |
| F15.105 | **Passive Fact Extraction** *(v5.8 — P1)* — `prompts/social.md` gains strict passive extraction rules: extract facts only when high-confidence (direct statement in email/calendar), only update existing contacts (no auto-creation), annotate every extracted fact with ISO date, skip uncertain or inferential data. Protects profile integrity. | P1 |
| F15.106 | **Digital Estate Inventory Template** *(v5.8 — P1)* — `prompts/estate.md` gains a complete "Digital Estate Inventory" section with 5 tables: Legal Documents (will, POA, healthcare directive), Password & Access Recovery (critical accounts + recovery methods), Beneficiary Designations (retirement, life insurance, investment accounts), Auto-Renewing Services (annual subscriptions with cancellation steps), Emergency Contacts (medical, legal, financial). Stale alerts at 12 months for legal docs and 6 months for beneficiary designations. | P1 |
| F15.107 | **Instruction-Sheet Action Types** *(v5.8 — P1)* — `config/actions.yaml` gains two `type: instruction_sheet` actions: `cancel_subscription` (step-by-step guide for identifying, tracking, and cancelling subscriptions) and `dispute_charge` (step-by-step guide for disputing credit card charges). Handler is null — these generate guidance prose rather than executing code. | P1 |
| F15.108 | **Subscription Action Proposals** *(v5.8 — P1)* — `prompts/digital.md` gains a "Subscription Action Proposals" section with three alert formats: price increase detected (propose cancel or justify), trial-to-paid conversion upcoming (propose cancel or upgrade decision), trial already converted without decision (flag for immediate review). Integrates with F15.107 action type. | P1 |
| F15.109 | **Windows Setup Script (`setup.ps1`)** *(v5.8 — P0)* — Full PowerShell onboarding script matching `setup.sh` feature parity: [1/5] prerequisites check (Python version via `python`/`py` launcher), [2/5] venv creation at `$HOME\.artha-venvs\.venv-win`, [3/5] pip install, [4/5] PII pre-commit hook (`.bat`-style), [5/5] demo + setup wizard offer. Uses `Write-Host -ForegroundColor` (no ANSI escape codes). No `ExecutionPolicy Bypass` inline. | P0 |
| F15.110 | **`artha.py --doctor` Unified Diagnostic** *(v5.8 — P0)* — `do_doctor()` runs 11 checks with pass/warn/fail classification: Python version ≥3.11, virtual environment active, core packages (PyYAML, keyring, jsonschema), `age` binary version, age encryption key in keyring, age_recipient configured, Gmail OAuth token, Outlook OAuth token, state directory (file count), PII git hook installed, last catch-up recency. Output: `━━ ARTHA DOCTOR ━━` banner, per-check icons (✓ / ⚠ / ✗), summary line `N passed · M warnings`. Exits 0 for warnings-only, 1 for any failures. | P0 |
| F15.111 | **Apple Health Connector** *(v5.8 — P1)* — `scripts/connectors/apple_health.py`: parses Apple Health export ZIPs (and bare XML files) locally — no network required. Uses `xml.etree.ElementTree.iterparse` + `elem.clear()` for memory efficiency on large exports. Tracks 16 HKQuantityTypeIdentifier types (steps, heartRate, bodyMass, sleepAnalysis, bloodPressure, etc.). Supports `since` parameter as relative ("365d") or absolute ISO date. Registered in `config/connectors.yaml` with `enabled: false` (opt-in). Module-level `fetch()` and `health_check()` follow connector duck-type contract. | P1 |
| F15.112 | **Longitudinal Lab Results Tracking** *(v5.8 — P1)* — `prompts/health.md` gains a "Longitudinal Lab Results" section with a date-keyed lab history table, flag codes (✅ normal, 🟡 borderline, 🟠 elevated, 🔴 critical), trend arrows (↑↓→), Apple Health import mapping for compatible metrics, and privacy note. Enables multi-year trend detection for cholesterol, HbA1c, vitamin D, and 10+ other common lab panels. | P1 |
| F15.113 | **`_ALLOWED_SKILLS` Allowlist Completion** *(v5.8 — P0)* — `scripts/skill_runner.py` `_ALLOWED_SKILLS` frozenset was missing `passport_expiry` and `subscription_monitor` despite both skills being fully implemented. All registered skills now present in allowlist: `financial_resilience`, `king_county_tax`, `nhtsa_recalls`, `noaa_weather`, `passport_expiry`, `property_tax`, `subscription_monitor`, `uscis_status`, `visa_bulletin`. | P0 |
| F15.114 | **Context Offloading (Deep Agents Phase 1)** *(v5.9 — P1)* — `scripts/context_offloader.py` offloads large intermediate artifacts to `tmp/` when they exceed a configurable token threshold (default 5,000 tokens). Returns a compact summary card in context. Offloaded artifacts: pipeline JSONL output, processed email batch, per-domain extractions (`tmp/domain_extractions/{domain}.json`), cross-domain scoring (`tmp/cross_domain_analysis.json`). PII guard runs on all content before offloading. Session cleanup in Step 18a′. Config flag: `harness.context_offloading.enabled` (default: true). | P1 |
| F15.115 | **Progressive Domain Disclosure (Deep Agents Phase 2)** *(v5.9 — P1)* — `scripts/domain_index.py` reads YAML frontmatter from all state files before loading any domain prompt, producing a compact domain index card (~600 tokens for 18 domains) listing each domain's status (ACTIVE/STALE/ARCHIVE), last activity date, and alert count. Only prompts required by the current command are loaded. Command-aware hint table: `/status` saves ~15K tokens, `/items` saves ~12K tokens, `/catch-up` with 3 active domains saves ~8K tokens. Config flag: `harness.progressive_disclosure.enabled` (default: true). | P1 |
| F15.116 | **Session Summarization (Deep Agents Phase 3)** *(v5.9 — P1)* — `scripts/session_summarizer.py` creates structured `SessionSummary` objects (Pydantic v2 with graceful fallback) and writes them to `tmp/session_history_{N}.md` + `.json` after catch-up commands. Proactively triggers when estimated context usage reaches a configurable threshold (default: 70%). Triggered after: `/catch-up`, `/domain <X>`, `/bootstrap`. Context card replaces full history; original file path preserved for recovery. Config flag: `harness.session_summarization.enabled` (default: true). | P1 |
| F15.117 | **Middleware Stack (Deep Agents Phase 4)** *(v5.9 — P0)* — `scripts/middleware/` formalizes all state write guards into a composable `StateMiddleware` Protocol with `compose_middleware()` factory. Stack order: PII redaction → WriteGuard (20% field-loss threshold, bootstrap files exempt) → AuditLog → [write] → WriteVerify (post-write YAML integrity + ISO-8601 timestamp check) → AuditLog. Rate limiter applies sliding 60-second windows per API provider (Gmail: 30/min, MS Graph: 20/min, iCloud: 10/min). Config flag: `harness.middleware.enabled` (default: true). | P0 |
| F15.118 | **Structured Output Validation (Deep Agents Phase 5)** *(v5.9 — P1)* — `scripts/schemas/` defines Pydantic v2 schemas for all major AI outputs: `BriefingOutput` (validates one_thing ≤300 chars, AlertItem severity enum, DomainSummary bullet points ≤5, PII footer presence), `FlashBriefingOutput`, `SessionSummarySchema` (key_findings ≤5, truncated gracefully), `DomainIndexCard`. Validated output written to `tmp/briefing_structured.json`. Validation failures log to `state/audit.md` and increment `harness_metrics.structured_output.validation_errors` — never block output. Config flag: `harness.structured_output.enabled` (default: true). | P1 |
| F15.119 | **Environment Detection Layer (VM Hardening Phase 1)** *(v6.0 — P0)* — `scripts/detect_environment.py` produces a JSON capability manifest via 7 probes: cowork marker (`/var/cowork` + `$COWORK_SESSION_ID`), filesystem writability, `age` installation, keyring functionality, and TCP connectivity to Google/Microsoft/Apple services. Returns `EnvironmentManifest` dataclass with `environment` (cowork_vm \| local_mac \| local_linux \| local_windows \| unknown), `capabilities`, `degradations`, and raw detection signals. 5-minute TTL cache in `tmp/.env_manifest.json`. 29 unit tests. | P0 |
| F15.120 | **Preflight Advisory Mode + Profile Completeness Gate (VM Hardening Phase 2)** *(v6.0 — P0)* — `preflight.py` gains `--advisory` flag: P0 failures become ADVISORY (non-blocking, labeled `⚠️ [ADVISORY]`), exit always 0. Use only in sandboxed/VM environments. New `check_profile_completeness()` (P1): fires when profile has ≤10 YAML keys; checks `family.primary_user.name`, emails, timezone, ≥1 enabled domain. New `check_msgraph_token()` 3-layer fix: Layer 1 proactive refresh (calls `ensure_valid_token()` when <TOKEN_EXPIRY_WARN_SECONDS remaining); Layer 2 60-day cliff tracking (reads `_last_refresh_success` timestamp, warns if >60 days toward 90-day expiry wall); Layer 3 dual-failure message (expired token AND network-blocked → two separate actionable messages). `setup_msgraph_oauth.py` writes `_last_refresh_success` timestamp after every successful silent refresh. `state/templates/health-check.md` created (schema_version: 1.1, last_catch_up: never). `config/Artha.core.md` gets "Read-Only Environment Protocol" block. 17 unit tests. | P0 |
| F15.121 | **config/Artha.md Decomposition — Compact Mode (VM Hardening Phase 3)** *(v6.0 — P1)* — `generate_identity.py` gains compact mode (default): assembles `config/Artha.md` at ~15KB by extracting only §1 (behavior), §4 (privacy), §5 (commands), §6 (routing table), §7 (capabilities) from `Artha.core.md` + injects §R command router table pointing to `config/workflow/` phase files. Omits §2 (21-step workflow), §8–§14 (meta). Legacy full-core mode preserved via `--no-compact` flag (~78KB). Reduces LLM context consumption by ~83% at start of catch-up session. | P1 |
| F15.122 | **Workflow Phase Files with Compliance Gates (VM Hardening Phases 3+4)** *(v6.0 — P0)* — All 5 `config/workflow/` stub files rewritten with canonical content: `preflight.md` (Steps 0–2b, read-only mode exceptions, dual OAuth failure rule), `fetch.md` (Steps 3–4e, mandatory Tier A state file loading, MCP retry protocol, calendar IDs warning, degraded mode detection), `process.md` (Steps 5–7b, CRITICAL email body mandate — snippet-only PROHIBITED, net-negative write guard), `reason.md` (Steps 8–11, URGENCY×IMPACT×AGENCY scoring, consequence forecasting, FNA), `finalize.md` (Steps 12–19b, read-only skip list, mandatory Connector & Token Health table in every briefing). Each file has YAML frontmatter, `⛩️ PHASE GATE` checklist, and `✅ Phase Complete → Transition` footer. | P0 |
| F15.123 | **Post-Catch-Up Compliance Audit (VM Hardening Phase 5)** *(v6.0 — P1)* — `scripts/audit_compliance.py` parses any briefing `.md` and returns a `ComplianceReport` with `compliance_score` (0–100) across 7 weighted checks: preflight execution (20pt), Connector & Token Health block (25pt), Tier A state files referenced (15pt), PII footer presence (15pt), full email bodies not snippets (10pt), domain sections (10pt), ONE THING block (5pt). Degraded-mode detection reduces connector_health weight to 15pt. `--threshold N` exits 1 if score below N. `--json` for pipeline output. 37 unit tests + 8 integration scenario tests (IT-4 through IT-8). | P1 |
| F15.124 | **skill_runner.py Agentic CLI Hardening** *(v6.1 — P0)* — `scripts/skill_runner.py` restructured to be directly executable by any CLI agent (Gemini, Claude, shell scripts). Import order: stdlib → path setup → `reexec_in_venv()` → third-party (`yaml`). Added `if __name__ == "__main__": main()` entrypoint (previously inert when invoked as script). `importlib.util` moved to module scope (was inside `run_skill()`, causing potential `UnboundLocalError` on the user-plugin path). 2 new tests (entrypoint + importlib scope). | P0 |
| F15.125 | **pipeline.py Venv Bootstrap + Unconditional Health Output** *(v6.1 — P1)* — `scripts/pipeline.py` adds `reexec_in_venv()` call after path setup (prevents silent `ImportError: PyYAML not installed` when run outside active venv). Health output now always emits `[health] ✓ name` per connector (previously gated on `--verbose`); summary `All N connectors healthy.` always printed on success. Eliminates ambiguity in automated health gates (`check_script_health()` captures stderr as note text — previously silent passes showed only `OK ✓`). 2 new tests. | P1 |
| F15.126 | **noaa_weather Unconfigured Coordinates Guard** *(v6.1 — P1)* — `scripts/skills/noaa_weather.py` `get_skill()` raises `ValueError` when `lat==lon==0.0` (the default placeholder in `user_profile.yaml`). Previously, the skill silently requested `api.weather.gov/points/0.0,0.0` which returns HTTP 404 (mid-Atlantic Ocean), appearing as an API failure rather than a configuration problem. The `ValueError` message includes the fix instruction (`set location.lat/lon in user_profile.yaml`). 2 new tests. | P1 |
| F15.127 | **uscis_status Actionable 403 IP-Block Response** *(v6.1 — P1)* — `scripts/skills/uscis_status.py` `pull()` returns `{"blocked": True, "error": "...IP address or network (common on cloud/VPN)...check manually at https://egov.uscis.gov/..."}` on HTTP 403, replacing the generic `{"error": "HTTP 403", "text": <large HTML>}`. Other non-200 responses truncate `response.text` to 500 chars. `blocked: True` flag enables downstream logic to distinguish IP-blocked from transient errors. 5 new tests. | P1 |
| F15.128 | **OODA Cross-Domain Reasoning Protocol (Agentic Phase 1)** *(v7.0 — P0)* — `config/workflow/reason.md` Step 8 replaced with structured 4-phase OODA loop: **8-O OBSERVE** (gather signals, read `state/memory.md` correction/pattern/threshold facts), **8-Or ORIENT** (8-domain cross-connection matrix, compound signal detection), **8-D DECIDE** (U×I×A priority scoring, ONE THING with fallback), **8-A ACT** (consequence forecasting, FNA, dashboard rebuild, PII stats). `audit_compliance.py` gains `_check_ooda_protocol()` check (weight=10, ≥3/4 OODA markers required). Strategy-level change: single-domain → cross-domain synthesis. 6 new audit tests. | P0 |
| F15.129 | **Tiered Context Eviction (Agentic Phase 2)** *(v7.0 — P1)* — `scripts/context_offloader.py` gains `EvictionTier(IntEnum)` enum (PINNED=0, CRITICAL=1, INTERMEDIATE=2, EPHEMERAL=3) with per-tier thresholds: PINNED=∞ (never offload), CRITICAL/INTERMEDIATE=1.0×configured_threshold (standard), EPHEMERAL=0.4×configured_threshold (aggressive). 8 artifact-to-tier mappings: `session_summary`→PINNED; `alert_list`, `one_thing`, `compound_signals`→CRITICAL; `predictions`, `domain_output`→INTERMEDIATE; `pipeline_output`, `processed_emails`→EPHEMERAL. Feature-flagged via `harness.agentic.tiered_eviction.enabled`. `config/artha_config.yaml` gains `harness.agentic:` namespace with 4 sub-flags. 9 new eviction tests. | P1 |
| F15.130 | **ArthaContext Typed Runtime Context Carrier (Agentic Phase 3)** *(v7.0 — P1)* — `scripts/artha_context.py` (new, ~200 lines): `ContextPressure(str, Enum)` (GREEN/YELLOW/RED/CRITICAL), `ConnectorStatus(BaseModel)`, `ArthaContext(BaseModel)` (9 fields: command, artha_dir, pressure, connectors, degradations, steps_executed, start_time, env_manifest, preflight_results). `build_context()` builder integrates `EnvironmentManifest` + preflight results. `connectors_online`/`connectors_offline` computed properties. `health_summary()` method. `scripts/middleware/__init__.py` `StateMiddleware.before_write()` Protocol gains `ctx: Any | None = None` parameter (backward compatible). Feature-flagged via `harness.agentic.context.enabled`. 21 new context tests + 4 middleware compat tests. | P1 |
| F15.131 | **Implicit Step Checkpoints for Crash Recovery (Agentic Phase 4)** *(v7.0 — P0)* — `scripts/checkpoint.py` (new, ~140 lines): writes `tmp/.checkpoint.json` after each major workflow step (Steps 4, 7, 8, finalize). Schema: `last_step`, `timestamp`, `session_id`, arbitrary metadata. `read_checkpoint()` returns `None` for stale (>4h) or missing files. `clear_checkpoint()` called on clean session completion. `config/workflow/preflight.md` adds Step 0a "Check for Resumable Session" — auto-resumes from last checkpoint in pipeline mode, prompts user interactively. `config/workflow/finalize.md` Step 18 clears `.checkpoint.json`. Feature-flagged via `harness.agentic.checkpoints.enabled`. 21 new checkpoint tests. | P0 |
| F15.132 | **Persistent Fact Extraction Across Sessions (Agentic Phase 5)** *(v7.0 — P1)* — `scripts/fact_extractor.py` (new, ~380 lines): extracts `correction`, `pattern`, `preference`, `threshold`, `schedule` facts from `tmp/session_history_*.md` summaries via 5 signal detectors. PII stripped (phone/email/SSN regex) before persistence. Deduplicates by content hash (last 16 chars of SHA-256). Persists to `state/memory.md` v2.0 frontmatter (`schema_version: '2.0'`, `facts: []` list). `config/workflow/finalize.md` gains Step 11c "Persistent Fact Extraction". `config/workflow/reason.md` Step 8-O OBSERVE reads `memory.md` facts to feed cross-domain orientation. `state/templates/memory.md` template created. Feature-flagged via `harness.agentic.fact_extraction.enabled`. 38 new fact tests. | P1 |

| F15.133 | **Multi-Machine Action Bridge — DUAL v1.3.0** *(v7.0.8 — P1)* — `scripts/action_bridge.py` (new, ~800 LOC): file-based bridge that synchronises action proposals and results between a Mac (proposer/enricher role) and a Windows machine (executor role) via the shared OneDrive folder. **Proposal flow:** `write_proposal()` reads unsynced actions from `ActionQueue.list_unsynced()`, encrypts each with Fernet symmetric encryption (Argon2id KDF from keyring `artha-bridge-key`), and writes an atomic JSON file to `tmp/bridge/proposals/{uuid}.json.enc`. **Result flow:** `ingest_results()` runs on Mac at catch-up startup (before briefing — spec §4.2), reads result files from `tmp/bridge/results/`, decrypts, and calls `queue.apply_remote_result()` (additive-only — never overwrites existing proposal fields). **Executor flow:** Windows `run_listener()` poll loop calls `ingest_proposals()` to read incoming proposals, execute or queue for Telegram approval, then `write_result()` after execution. **Reliability:** `retry_outbox()` retries failed proposals; `gc()` prunes files older than TTL (default 7 days); `detect_conflicts()` globs `state/` for OneDrive machine-suffix conflict copies. **Role detection:** `detect_role(channels_config)` compares `channels.yaml defaults.listener_host` vs `socket.gethostname()` — returns `'executor'` if they match, `'proposer'` otherwise. **DB isolation:** each machine has its own local `ActionQueue` SQLite database (`~/.artha-local/actions.db` on macOS, `%LOCALAPPDATA%\Artha\actions.db` on Windows) so the shared OneDrive DB is never locked by two processes. `_migrate_schema_if_needed()` adds `bridge_synced INTEGER` + `origin TEXT` columns. **Security:** `_get_privkey(artha_dir)` loads key from OS keyring (falls back to env var `ARTHA_BRIDGE_KEY`); all bridge files encrypted at rest; `_write_bridge_file()` uses `tempfile.NamedTemporaryFile` + `os.replace()` for atomic writes. **Metrics:** `BridgeMetrics` class tracks proposal/result/retry/gc counts + latency percentiles, persisted to `tmp/bridge_metrics.json`. `config/artha_config.yaml` gains `multi_machine.bridge_enabled: false` (default off) + `multi_machine.bridge_dir` + `multi_machine.ttl_days`. 57 new bridge unit tests. | P1 |
| F15.134 | **Per-Machine Connector Routing** *(v7.0.8 — P1)* — `run_on:` field added to every connector in `config/connectors.yaml` (`darwin`/`windows`/`all`, default `all`). `scripts/pipeline.py` `_enabled_connectors()` now gates on `platform.system().lower()` vs `run_on` — logs `[pipeline] SKIP {name} (run_on={run_on}, platform={platform})` and silently excludes non-matching connectors from the fetch pipeline. `list_connectors()` gains a PLATFORM column showing the `run_on` value. All 14 connectors default to `run_on: all` in the distributed `connectors.yaml` for backward compatibility — users who operate multi-machine setups override individual connectors locally (e.g., `whatsapp_local: run_on: windows`, `imessage_local: run_on: darwin`). No configuration changes required for single-machine users. +10 platform gating unit tests. | P1 |
| F15.135 | **Nudge Daemon Host Gating** *(v7.0.8 — P1)* — `scripts/nudge_daemon.py` gains `_verify_nudge_host(channels_config) -> bool`: reads `channels_config["defaults"]["listener_host"]`, compares to `socket.gethostname()`, returns `False` (and logs `[nudge] SKIP — not listener host`) if they differ. Returns `True` for empty / missing host (single-machine mode). Called at the top of `run_check_once()` — if `False`, the entire nudge check is skipped silently. Prevents duplicate nudge notifications when the daemon is started on multiple machines (e.g., launchd on Mac AND Task Scheduler on Windows — only the designated listener host fires nudges). No setup required; gating is automatic when `listener_host` is set in `channels.yaml`. | P1 |
| F15.136 | **Memory Pipeline Activation — MEM v1.3.0** *(v7.0.9 — P0)* — Activated Artha's 9-script memory subsystem that was fully coded but operationally inert (all state files empty after 10+ catch-ups). **Deterministic orchestrator:** `scripts/post_catchup_memory.py` — called from Step 11c with `--briefing briefings/YYYY-MM-DD.md`; extracts facts directly from briefing (bypasses lossy `SessionSummary.to_markdown()` round-trip), persists to `state/memory.md` (AR-1: 30 facts / 3,000 chars), updates `state/self_model.md` (AR-2), appends structured record to `state/memory_pipeline_runs.jsonl` (observability + consecutive-zero-fact alerting). **Dual-path parser:** `scripts/fact_extractor.py` extended with `_parse_briefing_md()` handling both Telegram (`━━`) and Markdown (`##`) briefing formats + 4 Phase 1b signals (deadline, decision-pending, `$N/mo` threshold, OI-NNN pattern). **Self-model fix:** `scripts/self_model_writer.py` `_parse_catchup_runs_from_markdown()` — reads freeform `## Catch-Up Run History` when YAML frontmatter `catch_up_runs` is absent. **Domain sensitivity tiering:** `_apply_domain_sensitivity_ttl()` caps finance/health/immigration/insurance/legal facts at 90d TTL (corrections and preferences exempt). **Bootstrap:** `scripts/bootstrap_memory.py` + `scripts/bootstrap_seeds.yaml` seed historical briefings and learned procedures in one idempotent batch. Config kill switch: `harness.agentic.post_catchup_memory.enabled`. | P0 |

---

### FR-16 · Insurance & Risk Management

**Priority:** P1
**Summary:** Unified tracking of all insurance policies (auto, home, umbrella, life, disability), premium costs, renewal dates, coverage adequacy review, and life-event triggered reassessment.

**The problem:** Insurance policies are scattered across multiple carriers with no consolidated view of coverage, cost, or renewal timing. Annual renewal is the highest-leverage moment to review coverage and compare rates — but it slips by without proactive tracking. Critical life events (teen driver, home renovation, asset growth) should trigger coverage reviews but don't.

**Data sources:**
- Gmail/Outlook (policy renewal notices, premium payment confirmations, EOB/claims correspondence)
- Manual input (policy details, coverage limits, deductibles, agent contact info)
- Connected APIs (insurance carrier portals — future)

**Known/expected policies:**

| Policy | Type | Carrier | Notes |
|---|---|---|---|
| Auto Insurance | Vehicle liability + comprehensive | TBD | Covers family vehicles |
| Homeowners Insurance | Property + liability | TBD | Required by Wells Fargo mortgage |
| Umbrella / Liability | Excess liability | TBD | Recommended given asset profile |
| Life Insurance | Term life | Employer benefits (likely) | Verify coverage amount and beneficiaries |
| Long-Term Disability | LTD | Employer benefits (likely) | Verify coverage details |
| Short-Term Disability | STD | Employer benefits (likely) | Verify coverage details |

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F16.1 | **Insurance Policy Registry** — Master list of all active insurance policies with carrier, policy number, coverage limits, deductibles, premium amount, payment frequency, and renewal date. Linked to net worth calculation in FR-3. | P1 |
| F16.2 | **Premium Tracker** — Track total annual insurance cost across all policies. Parse premium payment confirmation emails. Alert on premium increases at renewal. Year-over-year trend. | P1 |
| F16.3 | **Renewal Calendar** — Alert 60 and 30 days before each policy renewal. Prompt: "Auto insurance renews in 30 days. Current premium: $X/6mo. Review coverage or shop rates?" | P0 |
| F16.4 | **Coverage Adequacy Review** — Annual prompted review: Does homeowners coverage match current home value (from FR-7 Zillow estimate)? Does auto coverage reflect current vehicles? Does umbrella policy cover total asset exposure? Surface gaps. | P1 |
| F16.5 | **Life Event Coverage Trigger** — When a life event is detected that should trigger an insurance review, proactively prompt. Triggers: Arjun gets driver’s license (add to auto policy), home renovation (update homeowners), significant asset growth (umbrella review), new vehicle purchase, family member turns 26 (health plan change). | P1 |
| F16.6 | **Teen Driver Prep (Arjun)** — Arjun is approaching driving age. Track: learner’s permit timeline, driver’s ed completion, license eligibility date. Alert on auto insurance impact: "Adding a teen driver typically increases auto premiums 50–100%. Get quotes before Arjun’s license date." | P1 |
| F16.7 | **Claims History Log** — Track all insurance claims filed, status, and outcomes. Maintain a history for rate negotiation context. | P2 |
| F16.8 | **Employer Benefits Optimizer** — During annual open enrollment, surface a benefits review checklist: life insurance coverage vs. needs, disability coverage adequacy, FSA/HSA election optimization, dental/vision plan comparison. Cross-reference with FR-6 (Health). | P1 |

---

### FR-17 · Vehicle Management

**Priority:** P1
**Summary:** Track vehicle registration, maintenance schedules, warranty status, fuel/charging costs, and driving milestones for the family.

**The problem:** Vehicle ownership generates recurring obligations (annual registration, emissions testing, oil changes, tire rotations, warranty expirations) that are tracked nowhere. With Arjun approaching driving age, the complexity will increase (learner’s permit, driver’s ed, license, added vehicle/insurance).

**Data sources:**
- Gmail/Outlook (registration renewal notices from WA DOL, service reminders, warranty correspondence)
- Manual input (vehicle details, mileage, service history)

**Vehicle inventory:**

| Vehicle | Owner | Notes |
|---|---|---|
| TBD — Vehicle 1 | Family | Capture make, model, year, VIN, license plate |
| TBD — Vehicle 2 | Family | Capture make, model, year, VIN, license plate |

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F17.1 | **Vehicle Registry** — All family vehicles with make, model, year, VIN, license plate, purchase date, mileage, and linked insurance policy (FR-16). | P1 |
| F17.2 | **Registration Renewal Tracker** — WA annual registration renewal for each vehicle. Alert 60 and 30 days before expiry. Parse DOL renewal notices from email. Include emissions testing requirement if applicable. | P0 |
| F17.3 | **Maintenance Schedule** — Track manufacturer-recommended maintenance intervals: oil change (every 5K–7.5K miles or 6 months), tire rotation (every 7.5K miles), brake inspection, coolant flush, transmission service, air filter. Alert when due based on mileage or time. | P1 |
| F17.4 | **Service History Log** — Record all vehicle service visits with date, mileage, service performed, cost, and provider. Answer: "When was the last oil change on [vehicle]?" | P1 |
| F17.5 | **Warranty Tracker** — Track manufacturer warranty and any extended warranty coverage. Alert when warranty is approaching expiry: "Factory warranty on [vehicle] expires in 3 months / 2,000 miles. Consider extended warranty?" | P1 |
| F17.6 | **Fuel / Charging Cost Tracker** — Track monthly fuel or EV charging costs per vehicle. Detect anomalies (sudden increase may indicate maintenance need). Part of FR-3 spend tracking by category. | P2 |
| F17.7 | **Teen Driver Program (Arjun)** — Structured milestone tracker for Arjun’s driving journey: WA learner’s permit (age 15.5 eligible), driver’s ed enrollment and completion, required supervised driving hours (50 hours in WA), intermediate license (age 16), full license (age 17). Tied to FR-16 insurance impact. | P1 |
| F17.8 | **Recall Monitor** — Periodically check NHTSA recall database for active recalls on family vehicles by VIN. Alert immediately on safety recalls. | P2 |
| F17.9 | **Lease & Lifecycle Manager** — For leased vehicles, track lease term, residual value, mileage allowance vs. actual, and lease-end date. Reverse-schedule end-of-lease actions: pre-return inspection (90 days out), equity check — purchase vs. return analysis (120 days out), replacement vehicle research window (150 days out), lease-end cleaning and repair. For owned vehicles, track estimated remaining useful life and replacement planning horizon. | P1 |
| F17.10 | **Total Cost of Ownership (TCO) Calculator** — For each vehicle, calculate annualized total cost: lease/loan payment + insurance (from FR-16) + registration + maintenance + fuel/charging + depreciation estimate. Compare across vehicles. Use TCO data when evaluating replacement options: "Current vehicle TCO: $X/year. Comparable new lease: $Y/year. EV alternative: $Z/year (including fuel savings)." | P2 |

---

### FR-18 · Estate Planning & Legal Readiness

**Priority:** P1
**Summary:** Track estate planning documents, beneficiary designations, legal readiness, and ensure the family is protected in the event of an emergency.

**The problem:** Estate planning documents (wills, trusts, powers of attorney, guardianship designations) are critical but easy to neglect. Beneficiary designations across financial accounts may be outdated or inconsistent. As an immigrant family with complex financial and immigration status, legal readiness is especially important.

**Data sources:**
- OneDrive (legal document storage)
- Manual input (document dates, attorney info, beneficiary designations)
- Gmail/Outlook (attorney correspondence)

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| F18.1 | **Estate Document Registry** — Track existence, location, date, and review status of: Will (Raj), Will (Priya), Revocable Living Trust (if applicable), Power of Attorney — Financial (both spouses), Power of Attorney — Healthcare / Advance Directive (both spouses), Guardianship designation for Arjun and Ananya. | P1 |
| F18.2 | **Beneficiary Audit** — Annually prompt a review of beneficiary designations across all financial accounts (Fidelity, Vanguard, Morgan Stanley, E*Trade, Wells Fargo, life insurance, 401k, HSA). Flag inconsistencies: "Your Vanguard IRA lists a beneficiary last updated in 2019. Verify it matches your current wishes." | P1 |
| F18.3 | **Document Expiry & Review Cycle** — Estate documents should be reviewed every 3–5 years or on major life events. Track last review date and prompt: "Your will was last updated 4 years ago. Life changes since then: home purchase, job change, children's ages. Schedule attorney review?" | P1 |
| F18.4 | **Life Event Legal Trigger** — When Artha detects a major life event (home purchase, new job, child turning 18, immigration status change), prompt legal document review. Example: "Arjun turns 18 in [date]. Guardianship designation will no longer apply. Update healthcare POA to include him as adult?" | P1 |
| F18.5 | **Emergency Access Guide** — Maintain a structured "In Case of Emergency" document: location of all critical documents, list of all financial accounts with institution and contact, insurance policies, attorney contact info, key passwords vault reference. Updated automatically as Artha’s knowledge graph grows. Stored encrypted. | P0 |
| F18.6 | **Attorney & Legal Provider Rolodex** — Track estate planning attorney, tax CPA, immigration attorney ([immigration attorney]), and any other legal contacts with last engagement date and notes. | P2 |
| F18.7 | **Guardianship & Minor Children Planning** — Explicitly track: Who is the designated guardian for Arjun and Ananya if both parents are incapacitated? Is this documented? Does the guardian know? When do the children age out (18)? | P1 |
| F18.8 | **Emergency Contact Wallet Card** *(v4.0)* — Generate a printable/digital emergency contact card for each family member. Contents: name, emergency contacts (2–3 prioritized), primary care physician, insurance policy number, blood type (if known), allergies, current medications, immigration status (generic — e.g., "valid work authorization" without sensitive details), attorney contact, and location of the Emergency Access Guide (F18.5). Output formats: PDF (printable wallet card), Apple Wallet pass, or plain text for phone lock screen. Auto-regenerated when any source data changes. Each family member gets a personalized card. | P1 |

---

### FR-19 · Work Intelligence OS

**Priority:** P1 (Windows-first)
**Summary:** A fully isolated, privacy-hardened intelligence layer for professional work — separate vault key, separate state directory, separate surface. Connects MS Graph / ADO / WorkIQ to synthesise daily work briefings, surface commitments, track career evidence, and provide meeting intelligence — under the hard separation model (no raw work data in personal briefing; only the numeric boundary score crosses the surface).

**Data sources:** MS Graph (Calendar, Mail — work account), Azure DevOps (work items), WorkIQ MCP (Windows), Outlook COM bridge (Windows), 81+ weeks of warm-start scrape data, Kusto/ICM (Microsoft Enhanced tier).

**Architecture:** 7-stage processing loop (`scripts/work_loop.py`), canonical object layer (`scripts/schemas/work_objects.py` — 6 dataclasses), connector error protocol (`scripts/schemas/work_connector_protocol.py`), bridge schema validators (`scripts/schemas/bridge_schemas.py`), warm-start processor (`scripts/work_warm_start.py`), guided bootstrap interview (`scripts/work_bootstrap.py`), post-meeting notes capture (`scripts/work_notes.py`), 25-command read-path CLI (`scripts/work_reader.py`), domain state writers (`scripts/work_domain_writers.py`), narrative templates (`scripts/narrative_engine.py`), post-refresh processor (`scripts/post_work_refresh.py`); isolated state in `state/work/` (20 files); bridge output to `state/bridge/` for personal surface consumption; 3 agent tiers (`artha-work.md`, `artha-work-enterprise.md`, `artha-work-msft.md`); 883 tests in `tests/work/`.

**Core features:**

| Feature ID | Feature | Priority |
|---|---|---|
| FW-1 | **Daily Work Briefing** — Morning summary of calendar load, meeting prep readiness, active commitments, and ADO work item deltas. Delivered via `/work` command. Format: `work-summary.md` → briefing synthesis. | P0 |
| FW-2 | **Meeting Intelligence** — Per-meeting prep score (0–100), attendee graph, open threads from prior instances, recurrence detection, and Teams join links. Powered by WorkIQ MCP (Windows) or MS Graph calendar (cross-platform fallback). | P1 |
| FW-3 | **Commitment Tracker** — Automated extraction and tracking of commitments from meeting context and ADO items. Surfaces overdue commitments in daily briefing. | P1 |
| FW-4 | **Career Evidence Log** — Structured capture of high-signal career moments: LT presentations, newsletter sends, program ownership milestones, and recognition events. Maintains `state/work/work-career.md`. | P1 |
| FW-5 | **People Graph** — Relationship map of colleagues, managers, skip-levels, and key stakeholders. Scored by interaction frequency. Warm-started from 81 weeks of scrape history. Stored in `state/work/work-people.md`. | P1 |
| FW-6 | **Warm-Start Import** — One-command population of all work state files from historical work-scrape data. Extracts people graph, project timeline, career evidence, and recurring meetings from 18 months of weekly scrape files. | P0 (one-time) |
| FW-7 | **Boundary Bridge** — Publishes `work_load_pulse.json` to `state/bridge/` — a schema-validated, PII-free summary (boundary score, meeting count, commitment counts). The personal Artha surface consumes only this artifact; raw work data never crosses the surface boundary. | P0 |
| FW-8 | **Work Health & Degraded Mode** — Connector failure protocol: every connector failure has a defined fallback, a user-facing status signal, and a remediation. No single connector failure blocks the workflow. Audit log at `state/work/work-audit.md`. | P0 |
| FW-9 | **Project Tracker** — Active project map with meeting frequency, key stakeholders, status, and timeline signals. Auto-updated from meeting and ADO data. `state/work/work-projects.md`. | P1 |
| FW-10 | **Work Source Catalog** — Registry of key dashboards, Kusto queries, SharePoint sites, and portals — with "what question does this answer" annotations. `state/work/work-sources.md`. | P2 |
| FW-11 | **Post-Meeting Notes Capture** — Structured capture of meeting decisions, open items, and context after any meeting. Generates D-NNN (decision) and OI-NNN (open item) IDs. CLI: `python scripts/work_notes.py`. `state/work/work-notes.md`. | P1 |
| FW-12 | **Guided Bootstrap Interview** — 12-question CLI interview to populate all work domain state files from scratch — no scrape archive required. Covers org structure, stakeholders, projects, goals, and career arc. CLI: `python scripts/work_bootstrap.py`. | P0 (one-time) |
| FW-13 | **Narrative Engine** — 10 narrative templates for status communication: weekly memo, talking points, boundary report, connect summary, newsletter draft, LT deck outline, calibration brief, connect evidence, escalation memo, decision memo. CLI: `python scripts/narrative_engine.py`. | P1 |
| FW-14 | **Connect Cycle Intelligence** — Evidence assembly for performance reviews: keyword matching against career evidence, evidence density ratings (★–★★★★★), GAP analysis, manager-voice calibration brief. CLI: `/work connect-prep` and `/work connect-prep --calibration`. | P1 |
| FW-15 | **Promotion OS** — Evidence-backed promotion readiness assessment and narrative generation. Scope arc analysis, project journey timeline, visibility event inventory, readiness score. CLI: `/work promo-case` (assessment) and `/work promo-case --narrative` (Markdown draft). `state/work/work-project-journeys.md`. | P2 |
| FW-16 | **Decision Support** — Structured decision-making with D-NNN / OI-NNN identifiers. Decision registry, open item tracking, recurrence drift detection. CLI: `/work decide <context>`. `state/work/work-decisions.md`, `state/work/work-open-items.md`. | P1 |
| FW-17 | **Org Calendar** — Tracks key org dates: Connect deadlines, rewards season, fiscal year close, all-hands cadence. 30-day lookahead alerts appear in `/work` briefing. `state/work/work-org-calendar.md`. | P1 |
| FW-18 | **Product Knowledge Base** — Durable product/technology knowledge that persists across projects. Index file (`state/work/work-products.md`) with taxonomy tree + per-product deep files (`state/work/products/*.md`). Captures: architecture, components, dependencies, team ownership, data sources. Trigger-loaded for meeting prep context injection. 6-month staleness (vs 2-week for projects). CLI: `/work products`, `/work products <name>`, `/work products add <name>`. | P2 |
| FW-19 | **Reflection Loop** (v1.5.0) — Multi-horizon planning & review engine (daily/weekly/monthly/quarterly/yearly). 8-step pipeline: detect → sweep → extract → score → reconcile → synthesize → draft → persist. **Sweep**: 5-pass data collection (WorkIQ, state diff, Kusto, calendar, goal/KPI). **Scoring**: additive model `(urgency × importance) + visibility_bonus + goal_alignment_bonus` with org-sensitivity confidence gates. **Reconciliation**: two-pass strategy — deterministic ID match then injectable LLM semantic match (`reconcile.py`). **Persistence**: three-tier progressive summarization (Tier 1 live `reflect-current.md` ≤15 KB, Tier 2 recent archive `reflections/`, Tier 3 compacted `reflect-history.md` with Accomplishment Index never compacted). **Safety**: `write_state_atomic()` via `os.replace()`, `CompactionManifest` for idempotent multi-file compaction, `ReflectionKey` for crash-safe retry, UUID+timestamp concurrency guard, JSONL audit log with sequence numbers. **Carry-forward**: hard-decay at 3 carries → Parking Lot, max 15 active CFs. **Backfill**: 82-week work-scrape corpus (4 format families), 3-phase pipeline (parse → cross-reference → WorkIQ gap-fill), interactive validation. **Integration**: `ReflectReader` typed facade for `/scorecard`, `/goals`, `/radar`, narrative engine consumption. CLI: `/work reflect [daily|weekly|monthly|quarterly]`, `--status`, `--tune`, `--backfill`, `--compact`, `--audit`. See Tech Spec §19.11, UX Spec §23.14. | P1 |

---

## 7. Goal Intelligence Engine — Deep Dive

The Goal Intelligence Engine (FR-13) is the feature that most distinguishes Artha from a monitoring tool. Most personal finance and productivity apps track metrics. Artha connects metrics to meaning.

### 8.1 — Goal Model

Every goal in Artha has a consistent structure:

```
Goal {
  id:           unique identifier
  name:         human-readable label
  domain:       one of the life domains (18 FRs)
  type:         Outcome | Habit | Milestone
  objective:    what you want to achieve (free text)
  metric:       what Artha will measure (quantifiable)
  current:      current measured value
  target:       target value
  deadline:     by when
  cadence:      how often to measure (daily / weekly / monthly)
  data_source:  which Artha agent provides the metric automatically
  status:       On Track | At Risk | Behind | Achieved | Paused
}
```

### 8.2 — Goal Types

**Outcome Goals** — A specific end state you want to reach by a date.
Example: *"Net worth of $X by December 31, 2026"*
Artha measures: Monthly net worth snapshot from FR-3. Reports trajectory. Alerts if trend line will miss target.

**Habit Goals** — A recurring behavior you want to sustain over time.
Example: *"Exercise at least 4 days per week"*
Artha measures: Manual check-in (or future wearable integration). Tracks streak and weekly completion rate. Morning briefing includes habit status.

**Milestone Goals** — A defined event or achievement to reach.
Example: *"Complete ByteByteGo system design course by Q2 2026"*
Artha measures: Course completion percentage (from FR-10). Alerts when pace is insufficient to hit deadline.

### 8.3 — Automatic Metric Wiring

For each goal, Artha identifies the data source that can prove progress without manual entry. This is the key to sustainability — goals that require manual updates die. Goals that update themselves persist.

| Goal example | Auto data source | Artha agent |
|---|---|---|
| Net worth target | Fidelity + Vanguard + Wells Fargo balance emails | FR-3 Finance |
| Learning hours/month | Obsidian vault activity + course logins | FR-10 Learning |
| Amazon spend < $X/month | Amazon order emails + credit card alerts | FR-9 Shopping |
| Arjun's GPA target | Canvas grade emails, PPS alerts | FR-4 Kids |
| Immigration milestone | Case status emails from [immigration attorney]/USCIS | FR-2 Immigration |
| Reading goal | Manual check-in (with Obsidian book note detection) | FR-10 Learning |
| Work-life balance | Work email timestamp analysis | FR-14 Boundary |
| Metro Electric energy bill < $X | Metro Electric bill emails | FR-7 Home |

### 8.4 — The Weekly Goal Scorecard

Every Sunday summary includes a full goal scorecard. Example format:

```
ARTHA GOAL SCORECARD · Week of March 3–9, 2026

FINANCIAL
  Net Worth 2026 Target          ██████░░░░  62%  → On Track
  Monthly Amazon Spend < $X      ████████░░  78%  ⚠ At Risk ($X over budget)

FAMILY
  Arjun GPA ≥ 3.8               █████░░░░░  54%  ⚠ Missing assignments flagged
  Quality family time ≥ 10h/wk  ████████░░  80%  → On Track

LEARNING
  12 books in 2026               ███░░░░░░░  25%  → On Track (3/12)
  ByteByteGo course complete Q2  ██░░░░░░░░  22%  ⚠ Behind — 2h/week needed

IMMIGRATION
  H-4 EAD Renewal Filed         ██████████ 100%  ✓ Achieved

HEALTH
  Exercise 4x/week               ██████░░░░  60%  → On Track (avg 2.4x)

WORK-LIFE
  Protected personal time ≥ 20h  ████░░░░░░  45%  🔴 Behind — 4 late work nights
```

### 8.5 — Recommendation Engine

When a goal is At Risk or Behind, Artha surfaces one specific, contextual recommendation — not generic advice.

| Goal status | Artha recommendation example |
|---|---|
| Amazon spend over budget | "2 pending Amazon orders totaling $X. Review before checkout." |
| Arjun GPA at risk | "3 missing assignments in AP Language. Parent portal login needed." |
| Exercise goal behind | "No workouts logged Wed–Sun. Nearest gap in calendar: Tuesday 6pm." |
| ByteByteGo course behind | "Last session: 12 days ago. You need 2.5h this week to stay on track." |
| Work-life goal behind | "4 late-night work email sessions this week. Tuesday and Thursday after 10pm." |

### 8.6 — Conversational Goal Creation

Goals are defined through natural language conversation, not structured forms. The structured schema (8.1) is the internal storage format; the creation experience is conversational.

**Example interaction:**

> **User:** "I want to make sure we're saving enough for Arjun's college."
> **Artha:** "I can help track that. Based on current Fidelity 529 balance and Arjun's graduation timeline (Spring 2028), I'd suggest tracking:
> - Net 529 contributions per month (Habit goal)
> - Total 529 balance target by enrollment (Outcome goal)
> - Scholarship application milestones (Milestone goal)
>
> Should I set this up? I'll wire the 529 metric to your Fidelity balance emails automatically."

Claude infers the goal type, suggests metrics, identifies data sources, and proposes the structured schema for confirmation. One conversation creates a fully wired goal — no manual schema filling.

### 8.7 — Goal Conflict Detection

Goals can compete for the same resources (time, money, attention). Artha explicitly models goal tension:

| Conflict type | Example | Artha response |
|---|---|---|
| Financial trade-off | Savings goal vs. family travel goal | "Your savings goal is on track but travel goal shows zero progress. These may be in tension — adjust either target?" |
| Time trade-off | Arjun SAT prep vs. Economics Club | "SAT prep target requires 8 hrs/week. Economics Club competition is the same weekend. Prioritize?" |
| Work-life trade-off | Career growth vs. protected family time | "Work hours exceeded boundary 3 of 5 days this week. Protected time goal is at risk." |
| Parent attention split | Arjun college prep vs. Ananya academic support | "Arjun college prep consumed 6 planning hours this week. Ananya's last 2 grade alerts were unaddressed." |

When two active goals have metrics moving in opposing directions, Artha surfaces the trade-off explicitly rather than reporting both as independent items.

### 8.8 — Goal Trajectory Forecasting

For Outcome goals, Artha doesn't just report current status — it projects forward.

**Forecasting model:**
1. Calculate current trend line from historical data points
2. Project to deadline
3. Compare projected outcome to target
4. When deviation exceeds 10%, trigger replanning prompt

**Example forecast:**

```
NET WORTH GOAL · Forecast as of March 7, 2026

Target:        $X by December 31, 2026
Current:       $Y (62% of target)
Trend:         +$Z/month (last 3 months average)
Projected:     $W by December 31 (92% of target)
Gap:           -$V from target

⚠ Projected to miss target by $V
OPTIONS:
  1. Increase monthly savings by $A → closes gap
  2. Extend deadline to March 2027 → current pace sufficient
  3. Adjust target to $W → matches current trajectory
```

### 8.9 — Behavioral Nudge Engine

Informed by behavioral science research (implementation intentions, commitment devices, streak psychology, friction reduction):

- **Implementation intentions:** Not just "exercise 4x/week" but "I will exercise on Mon/Wed/Fri/Sat mornings at 6 AM at the gym"
- **Calendar integration:** "Your exercise goal is behind. Best open slot is Tuesday 6 PM after Arjun's pickup — schedule it?"
- **Streak tracking:** All habit goals track current streak length with positive reinforcement at milestones
- **Friction reduction:** Cross-reference calendar, location, and family logistics to find lowest-friction moments for goal activities
- **Commitment devices:** "Would you like me to schedule a calendar block for your ByteByteGo session this Saturday?"

### 8.10 — Seasonal & Cyclical Awareness

Personal life is deeply cyclical. After one full year of data, Artha automatically detects and models seasonal patterns:

| Cycle | Pattern | Goal impact |
|---|---|---|
| School year | Grades dip in Q3 (AP exam pressure) | Adjust academic goal expectations seasonally |
| Holiday spending | Amazon/retail spikes 50% in Nov–Dec | Budget goals need Q1–Q3 under-spending buffer |
| Tax season | Jan–Apr document tracking, refund income | Financial goal metrics temporarily distorted |
| Summer travel | Jun–Aug spending spike, schedule disruption | Habit goals need adjusted cadence |
| Visa Bulletin | Monthly priority date movements | Immigration milestones shift with bulletin |
| School enrollment | Feb–Mar registration windows | Milestone goals for SAT, AP exams cluster here |

### 8.11 — Leading Indicators *(v3.8)*

Lagging metrics tell you what already happened. Leading indicators tell you what's about to happen. For every goal, Artha identifies and tracks leading indicators alongside the goal's primary metric.

**Principle:** A goal's primary metric (e.g., net worth) is a lagging indicator — it reflects past decisions. Leading indicators (e.g., savings rate, spending trajectory, upcoming large expenses) predict whether the lagging metric will move in the right direction. Artha surfaces both.

**Leading indicator extraction rules:**

| Goal type | Lagging metric (existing) | Leading indicators (new) |
|---|---|---|
| Financial | Net worth, savings balance | Savings rate trend, upcoming bills, discretionary spend trajectory, income changes |
| Academic | GPA, assignment scores | Assignment completion rate, missing assignments, teacher feedback frequency, study hours |
| Health | Weight, A1C, blood pressure | Exercise frequency, appointment adherence, medication compliance, sleep quality signals |
| Immigration | Case status, approval dates | Processing time estimates, Visa Bulletin movements, attorney communication frequency, document expiry proximity |
| Habit | Streak length, completion % | Session frequency trend, time-of-day patterns, skip-day clustering, friction signals |
| Relationship *(v3.8)* | Contact recency, reciprocity balance | Communication frequency trend, response time changes, group participation rate |

**How it works:**
1. Each domain prompt's `leading_indicators` extraction block defines what to track (see tech spec §6.1)
2. During catch-up, leading indicators are extracted alongside standard state updates
3. Goal scorecard shows leading + lagging side by side: "Net worth: $X (↑2% this month). Leading: savings rate 18% (target 20%), no large expenses next 30 days, bonus expected Q2"
4. Weekly summary highlights leading indicator divergence: "Arjun's assignment completion rate dropped 15% this week — GPA impact likely in 2–3 weeks"

**Alert triggers:** When a leading indicator diverges from the trajectory needed to hit the goal target, Artha surfaces an early warning — before the lagging metric moves. This is the difference between "your GPA dropped" (too late) and "your assignment completion rate is declining — GPA risk in 2 weeks" (actionable).

**Leading Indicator Auto-Discovery** *(v4.0)*: In addition to the manually-defined extraction blocks in domain prompts, Artha automatically discovers new leading indicators by analyzing cross-domain correlations. After 30+ days of data, the coaching engine identifies patterns: "When your calendar density exceeds 12 events/week, your exercise goal completion drops 40% the following week." "When Arjun has 3+ assignments due on the same day, next-day scores average 15% lower." Auto-discovered indicators are proposed for confirmation: "Artha discovered: your spend increases ~30% in weeks with no meal-prep calendar block. Track 'meal-prep frequency' as a leading indicator for your food budget goal?" Confirmed indicators are added to the domain prompt's `leading_indicators` block and contribute to goal trajectory forecasting.

---

## 8. Architecture

Artha is a **pull-based personal intelligence system** built on Claude Code as the runtime. There is no custom daemon, no background process, and no always-on infrastructure. The user triggers Artha by opening a Claude Code session and saying "catch me up."

> **v3.0 Architectural Pivot:** The v2.2 architecture assumed an always-on Mac with a macOS LaunchAgent daemon. In practice, the user's Mac is used only some weekday evenings and weekends. Combined with a hard privacy requirement (no cloud VMs, no cloud state storage), a pull-based model was adopted. Personal life obligations operate on days/weeks/months timescales — no personal domain in Artha requires sub-hour alerting. A daily or every-other-day pull cadence is architecturally sufficient for ALL 18 Functional Requirements.

### 9.1 — System Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        ARTHA OS                              │
│          (Claude Code + Artha.md + MCP Tools)                │
│                                                              │
│  ┌──────────────┐    ┌───────────────────────────────────┐  │
│  │  CATCH-UP    │    │     SEMANTIC REASONING LAYER       │  │
│  │  WORKFLOW    │    │  (Claude-native context-aware       │  │
│  │              │    │   prioritization & insight)         │  │
│  │  1. Fetch    │    └───────────────┬───────────────────┘  │
│  │  2. Route    │                    │                       │
│  │  3. Update   │    ┌───────────────▼───────────────────┐  │
│  │  4. Alert    │    │      DOMAIN PROMPT LIBRARY         │  │
│  │  5. Brief    │    │                                   │  │
│  │  6. Email    │◄───│  ~/OneDrive/Artha/prompts/         │  │
│  │              │    │  comms.md · immigration.md         │  │
│  │  Triggered   │    │  finance.md · kids.md              │  │
│  │  by user:    │    │  travel.md · health.md             │  │
│  │  "catch me   │    │  home.md · calendar.md             │  │
│  │   up"        │    │  + 10 more domain prompts          │  │
│  └──────────────┘    └───────────────┬───────────────────┘  │
│                                      │                       │
│  ┌──────────────┐    ┌───────────────▼───────────────────┐  │
│  │ HUMAN GATE   │    │          MCP TOOL LAYER            │  │
│  │              │    │                                   │  │
│  │  All writes  │    │  Gmail MCP (OAuth)                │  │
│  │  proposed in │    │  Google Calendar MCP               │  │
│  │  conversation│    │  Filesystem (read/write state)     │  │
│  │  User        │    │  Email sending (briefing delivery) │  │
│  │  confirms    │    └───────────────┬───────────────────┘  │
│  └──────────────┘                    │                       │
│                      ┌───────────────▼───────────────────┐  │
│  ┌──────────────┐    │      LOCAL STATE FILES             │  │
│  │ CONVERSATION │    │                                   │  │
│  │ MEMORY       │    │  ~/OneDrive/Artha/state/           │  │
│  │              │    │  Markdown files (one per domain)   │  │
│  │  Artha.md  │    │  YAML frontmatter + prose          │  │
│  │  instructions│    │  Goal definitions + progress       │  │
│  │  + state dir │    │  Conversation memory               │  │
│  │  + audit.md  │    │  Audit log                         │  │
│  └──────────────┘    └───────────────────────────────────┘  │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  OUTPUT CHANNELS                       │  │
│  │                                                       │  │
│  │  Terminal (Mac)  ·  Email (iPhone, Windows, any)      │  │
│  │  Claude iOS App (Project with cached state snapshots) │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 9.2 — Domain Prompt Library

Each Functional Requirement is backed by a **domain prompt file** in `~/OneDrive/Artha/prompts/`. These are plain Markdown files containing:
- Domain-specific extraction rules and patterns
- Alert threshold definitions
- State file update instructions
- Briefing contribution format
- Known sender/subject patterns for routing

**Adding a new domain = adding a new prompt file. No code changes required.**

CLAUDE.md (thin loader that delegates to Artha.md, the primary instruction file) references all domain prompts and routes incoming data to the correct one based on sender/subject/content patterns. Claude's native reasoning handles the routing — no hardcoded router logic.

| Domain Prompt | FR | Primary MCP Tool | Briefing Contribution |
|---|---|---|---|
| `comms.md` | FR-1 | Gmail MCP | Actionable emails summary |
| `immigration.md` | FR-2 | Gmail MCP (Outlook via forwarding) | Timeline & deadline status |
| `finance.md` | FR-3 | Gmail MCP | Bills, spending, alerts |
| `kids.md` | FR-4 | Gmail MCP | Grades, activities, deadlines |
| `travel.md` | FR-5 | Gmail MCP, Calendar MCP | Upcoming trips, prep tasks |
| `health.md` | FR-6 | Gmail MCP | Appointments, Rx refills |
| `home.md` | FR-7 | Gmail MCP | Maintenance, utilities |
| `calendar.md` | FR-8 | Google Calendar MCP | Today's schedule, conflicts |
| `shopping.md` | FR-9 | Gmail MCP | Deliveries, price alerts |
| `learning.md` | FR-10 | Gmail MCP | Course progress, reading |
| `social.md` | FR-11 | Calendar MCP | Upcoming events, follow-ups |
| `digital.md` | FR-12 | Gmail MCP | Subscriptions, renewals |
| `goals.md` | FR-13 | All state files | Goal progress, forecasts |
| `boundary.md` | FR-14 | Gmail MCP (Outlook timestamps) | Work-life balance check |
| `insurance.md` | FR-16 | Gmail MCP | Policy status, renewals |
| `vehicle.md` | FR-17 | Gmail MCP | Registration, maintenance |
| `estate.md` | FR-18 | Filesystem | Document inventory |

### 9.3 — OneDrive-Synced State Store

Artha's world model can live in a **OneDrive-synced folder** accessible from Mac, iPhone, and Windows. **Markdown-only initially** — no SQLite, no vector database, no event sourcing.

**OneDrive sync** provides cross-device state with built-in versioning, no custom sync code, and `age` encryption for sensitive files. **Markdown-only** because all 18 domain state files fit within Claude's 200K context (~36K tokens), are human-readable, git-diffable, and directly editable. SQLite/RAG can be added if state outgrows context limits.

**Directory structure:** See `config/registry.md` for the full file manifest and directory layout.

**Write model:** Mac is the **sole writer**. Catch-up runs on Mac → updates state files → OneDrive syncs to all devices. iPhone and Windows only read. This eliminates sync conflicts — there is always exactly one writer.

**Backup:** OneDrive versioning (primary, 30-day history) + Time Machine (secondary, encrypted local). Encryption keys stored in device-local credential stores only.

**Data sensitivity classification:** Each state file declares a `sensitivity` level and `access_scope` that controls how its data flows through output channels:

| Sensitivity | Examples | Emailed Briefing | iPhone Snapshot | Terminal |
|---|---|---|---|---|
| `standard` | calendar, kids school events, shopping | Full detail | Included | Full detail |
| `high` | finance (balances, bills), health | Summary only (e.g., "Finance: 2 items, no alerts") | Excluded | Full detail |
| `critical` | immigration (case numbers), tax data, estate | Summary only | Excluded | Full detail |

**Document repository (Phase 2+):** Extract-and-discard policy — Claude reads documents via filesystem MCP, extracts structured data into state files, never stores raw document content. Sensitive identifiers redacted per domain prompt rules.

**State file format:** See `config/registry.md` for state file schemas, format examples, and the full directory structure. State files use YAML frontmatter (domain, timestamps, alert_level, sensitivity, access_scope) + Markdown prose sections.

### 9.4 — Catch-Up Workflow

The catch-up workflow is Artha's primary operation. It replaces the v2.2 daemon entirely.

**Trigger:** User opens Claude Code in `~/OneDrive/Artha/` and says "catch me up" (or any equivalent — "what did I miss", "morning briefing", "SITREP").

**Workflow steps (orchestrated by Artha.md instructions, no custom code beyond `vault.py` + `pii_guard.py` + `safe_cli.py`):**

0. **⭐ Pre-flight go/no-go gate** — before touching any data, verify: OAuth token files exist at `~/.artha-tokens/`, `gmail_fetch.py --health` exits 0, `gcal_fetch.py --health` exits 0, no active lock file (or stale lock >30 min → auto-clear with warning), vault operational. ⚠ HALT with `⛔ Pre-flight failed: [check] — [error]` if any check fails. Log gate result to `health-check.md`. This prevents silent-omission briefings (e.g., catch-up #1 showed 0 calendar events when Family calendar wasn't configured). See tech spec §7.1 step 0.
1. **Decrypt sensitive state files** via `./scripts/vault.py decrypt` (automated via Claude Code PreToolUse hook)
2. **Read last run timestamp** from `~/OneDrive/Artha/state/health-check.md`
2b. **Digest mode check** *(v3.8 — Workstream H)* — if >48 hours since last catch-up, set `digest_mode: true` for this session. This triggers priority-tier grouping in step 8 and the “What You Missed” briefing variant (see F15.26, tech spec §5.1).
3. **Fetch new emails + calendar events IN PARALLEL** — Gmail MCP (all emails since last run, across configured accounts) + Google Calendar MCP (today + next 7 days) — executed simultaneously via Claude Code parallel tool invocation
4. **⭐ Pre-flight PII filter** — run `./scripts/pii_guard.sh filter` on extracted data before state file writes. Detects SSN, credit card numbers, bank routing/account numbers, passport numbers, A-numbers, ITIN, driver's license patterns. Replaces with `[PII-FILTERED-*]` tokens. ⚠ HALT catch-up if filter fails (non-zero exit). **Note:** Claude sees raw email content via MCP (unavoidable — MCP returns data directly into context). The filter validates extracted data *before* it is persisted to state files. See tech spec §8.6 for data flow details.
5. **Email content pre-processing** *(enhanced v3.8 — Workstream E)* — for each email: strip HTML tags (retain text only), remove footers/disclaimers/unsubscribe blocks, collapse intermediate quoted replies in threads (keep latest reply + original), **suppress marketing/newsletter emails** (apply sender allowlist; unrecognized marketing senders get subject-line-only extraction), enforce **per-email token budget of 1,500 tokens** (hard cap — truncate with "[TRUNCATED]" marker), **batch summarization for >50 emails** (group by sender pattern, summarize groups instead of individual processing). Prevents context window bloat from marketing HTML and thread repetition. Handled by Claude inline; falls back to `email_prefilter.py` script only if >20% of catch-ups hit context window limits (see tech spec §9.2).
5b. **Tiered context loading** *(v3.8 — Workstream F)* — load state files using the tiered context architecture (§9.8): Always tier (health-check, open_items, memory — always loaded), Active tier (domains with new data in this catch-up — full load), Reference tier (domains with no new data but referenced by active domains — summary load), Archive tier (domains inactive >30 days — skip unless explicitly queried). Reduces context window usage by 30–40% on typical catch-ups. See §9.8 for tier definitions.
6. **Route each item** to the appropriate domain prompt based on sender/subject/content
7. **For each domain with new data:**
   - Apply the domain prompt's extraction rules
   - Apply §12 redaction rules (Layer 2 PII defense)
   - **Apply deduplication rules** — before creating new state entries, check for existing entries from the same source for the same item (receipt number, bill ID, event date + source). Update in-place rather than duplicating. See tech spec §6.1 for domain-specific dedup patterns.
   - Update the domain's state file in `~/OneDrive/Artha/state/`
   - Evaluate alert thresholds defined in the domain prompt
7b. **Update open_items.md** — for each actionable item extracted across all domains, check `~/OneDrive/Artha/state/open_items.md` for an existing matching entry (fuzzy match on description + deadline). Add only new items; set `todo_id: ""` for items not yet synced to Microsoft To Do. Re-surface any items with `status: open` and `deadline < today` as overdue. See tech spec §4.7 for schema.
8. **Synthesize briefing** *(enhanced v3.8 — Workstream G)* — aggregate all domain updates into the structured briefing format. **Apply the ONE THING reasoning chain:** score every candidate insight/alert using URGENCY (× time-sensitivity and deadline proximity) × IMPACT (consequence magnitude if ignored) × AGENCY (can the user actually act on this today?). The highest-scoring item becomes the “ONE THING” featured at the top of the briefing. If `digest_mode: true` (step 2b), use priority-tier grouping (Critical → Warning → Notable → FYI) with “What You Missed” header instead of standard format.
9. **Web research via Gemini CLI** — for domains that need external data (Visa Bulletin, property values, recall checks), delegate web research to Gemini CLI at $0 cost instead of consuming Claude API tokens (see tech spec §3.7.4)
10. **Evaluate cross-domain insights** — check for patterns that span domains (e.g., immigration deadline + travel plan conflict). **Auto-generate decision graph entries** *(v3.8 — Workstream C)* when cross-domain reasoning produces a decision point — log to `state/decisions.md` with context, alternatives considered, and affected domains.
11. **Ensemble reasoning (if triggered)** — for high-stakes decisions in critical domains (immigration, finance, estate), generate responses from all three LLMs (Claude, Gemini, Copilot) and synthesize the best answer (see tech spec §3.7.3)
12. **Surface alerts** — present any threshold crossings with severity level
13. **Propose actions** — if any write actions are recommended (send email, add calendar event, WhatsApp message), present as structured Action Proposals for user approval (see tech spec §7.4.1)
14. **Email briefing** — send the briefing to the configured email address for cross-device access
14b. **Sync to Microsoft To Do** (Phase 1B, T-1B.6.3) — run `python3 scripts/todo_sync.py` to push new `open_items.md` entries (those with `todo_id: ""`) to the appropriate domain-tagged Microsoft To Do list. Pull completion status of previously synced tasks to close resolved items. This is the bridge between Artha's catch-up and the user's daily task manager on iPhone.
15. **Update health-check** — write current timestamp + run statistics + CLI health status to `~/OneDrive/Artha/state/health-check.md`
16. **Archive briefing** — save to `~/OneDrive/Artha/briefings/YYYY-MM-DD.md`
17. **Log PII filter stats** — append detection summary to `~/OneDrive/Artha/state/audit.md`
18. **Encrypt sensitive state files** via `./scripts/vault.sh encrypt` (automated via Claude Code Stop hook)

**Progressive fallback:** If Claude proves unreliable at any specific step (e.g., OAuth token refresh, email sending), that step — and only that step — gets extracted into a minimal Python helper script. Target: zero custom code at launch beyond `vault.sh` and `pii_guard.sh`.

### 9.5 — Model Tiering Strategy

Claude Code handles model selection internally. Artha.md specifies **intent** rather than explicit model routing:

- **Standard processing:** Email parsing, state file updates, on-demand chat, calendar review
- **Extended thinking:** Weekly summary synthesis, cross-domain insight generation, immigration timeline reasoning, goal conflict analysis

**Multi-LLM routing (cost optimization):**
- **Web research → Gemini CLI:** $0 (free quota). Visa Bulletin, property values, recalls, price comparisons.
- **Script/config validation → Copilot CLI:** $0 (free quota). vault.sh review, Artha.md validation.
- **Visual generation → Gemini Imagen:** $0 (free quota). Festival cards, birthday greetings.
- **All reasoning, state management, MCP tools → Claude:** Paid API. Core orchestration capability.
- **High-stakes decisions → Ensemble (all 3):** Extra Gemini/Copilot calls at $0; enriches Claude's reasoning.

**Cost optimization:**
- **Prompt caching:** Artha.md system instructions + all state files are cached across the session. Domain prompts loaded on-demand.
- **Batch processing:** All emails processed in a single catch-up session rather than individually, amortizing context loading costs
- **Multi-LLM routing:** ~$3–6/month savings by delegating research tasks to free-quota CLIs
- **Target:** <$50/month at daily catch-up cadence with ~100 emails/day across all accounts
- **Cost validation:** Track actual API cost per catch-up via Claude API usage dashboard + Gemini/Copilot quota usage. Adjust domain prompt verbosity if costs exceed target.

### 9.6 — Integration Adapter Pattern

Domain prompts ARE the adapter layer — each defines extraction schema for its domain. When providers change, update sender patterns in the prompt. Claude handles format variations natively. Geographic relocation = new prompt patterns, no code changes.

### 9.7 — Context Window Management

**Token budget per catch-up:** ~106K of 200K (state ~36K + prompts ~10K + emails ~50K + conversation ~10K). Email pre-filtering (HTML strip, thread collapse, footer removal, 2K cap/email) prevents bloat — raw HTML can be 5-10× larger. **Phase 2+ scaling:** state compression, selective loading, SQLite for history, RAG for memory/briefings.

> **Deep Agents Phase 1 — Context Offloading *(v5.9)*:** `scripts/context_offloader.py` automatically offloads intermediate artifacts exceeding 5,000 tokens to `tmp/`, reducing in-session token pressure. Expected recovery: 10–30K tokens per catch-up (pipeline batch + per-domain extractions + cross-domain scoring). See F15.114.

### 9.8 — Tiered Context Architecture *(v3.8)*

Four loading tiers based on domain activity: **Always** (health-check, open_items, memory, goals — ~8K fixed), **Active** (domains with new data — full load), **Reference** (cross-referenced but no new data — YAML frontmatter only, ~500 tokens), **Archive** (>30 days inactive — skip). On-demand queries bypass tiers. Expected savings: 30-40% token reduction on typical catch-ups (5-7 active domains of 18).

> **Deep Agents Phase 2 — Progressive Disclosure *(v5.9)*:** `scripts/domain_index.py` adds a pre-load domain index card (~600 tokens for 18 domains) that enables command-aware prompt gating on top of the tiered architecture. `/status` and `/items` load zero domain prompts; `/catch-up` loads only routed-domain prompts. Compound savings with Tiered Context: 35–50% token reduction on typical sessions. See F15.115.

### 9.9 — Supported Execution Environments

Artha runs inside an AI CLI — the CLI **is** the runtime. The table below defines the officially supported environments and their capability tiers.

| Environment | OS | Vault Encryption | Connectors | Watchdog | Status |
|---|---|---|---|---|---|
| **Claude Code (local terminal)** | macOS, Windows, Linux | ✅ Full (system keyring) | ✅ All | ✅ LaunchAgent / Task Scheduler | **Full support** |
| **Claude Cowork (sandbox VM)** | Linux | ✅ Env-var fallback (`ARTHA_AGE_KEY`) | ⚠ Gmail + Google Calendar only (MS Graph, iCloud blocked by VM proxy) | ❌ Not needed (ephemeral VM) | **Supported — reduced connectors** |
| **Gemini CLI (local terminal)** | macOS, Windows, Linux | ✅ Full (system keyring) | ✅ All | ✅ LaunchAgent / Task Scheduler | **Full support** |
| **GitHub Copilot (VS Code)** | macOS, Windows, Linux | ✅ Full (system keyring) | ✅ All | ✅ LaunchAgent / Task Scheduler | **Full support** |
| **Telegram channel bridge** | macOS, Windows, Linux | ✅ System keyring + env-var fallback | ✅ All (runs as background service) | N/A (daemon, not LLM session) | **Full support** |

**Not supported:**

| Environment | Reason |
|---|---|
| Docker container | No system keychain, no OneDrive sync, no AI CLI runtime inside container. Cowork VM covers the cloud sandbox use case. |
| Bare SSH (no AI CLI) | Artha requires an AI CLI as its runtime — SSH alone provides no LLM reasoning layer. |

**Cowork VM constraints** are documented in `config/Artha.core.md` (lines 28-33) and handled gracefully: blocked connectors are noted in the briefing footer with "run catch-up from local terminal for full data." The vault uses `ARTHA_AGE_KEY` environment variable as a credential fallback when system keyring is unavailable.

> **Policy:** When adding or changing environment support, update this table, the README "Supported Environments" section, and `docs/supported-clis.md` in the same commit.

---

## 9. Autonomy Framework

Artha uses an earned autonomy model. There is no shortcut to autonomy — it is earned through demonstrated reliability.

### Trust Level 0 — Observer (Default at Launch)

Artha **reads everything, writes nothing**. It observes all connected data sources, builds its world model, generates briefings and summaries, and makes recommendations. All of this happens without taking any action in the world.

**What Artha does at Level 0:**
- Delivers morning briefings and weekly summaries
- Answers on-demand queries
- Fires ambient alerts
- Tracks goal progress
- Queues recommended actions for human review

**What Artha does NOT do at Level 0:**
- Send any email or message on your behalf
- Create calendar events
- Initiate any financial transaction
- File any form or submit any application

**What Artha CAN do at Level 0 (no human gate needed):**
- Generate AI visuals via Gemini Imagen (saved locally — not sent)
- Delegate web research to Gemini CLI (read-only)
- Run script validation via Copilot CLI (read-only)

### Trust Level 1 — Advisor (Earned after 30 days of reliable Level 0)

Artha **proposes specific actions with one-tap approval**. When it detects something that needs action, it drafts the action and presents it for your approval. One tap to execute, one tap to dismiss.

Examples at Level 1:
- "Metro Electric bill of $300.63 due in 3 days. [Pay now] [Dismiss]"
- "Arjun has 2 missing assignments. [Open Parent Portal] [Dismiss]"
- "H-4 EAD window opens in 90 days. [Draft attorney email] [Dismiss]"
- "Today is Rahul's birthday. [Send WhatsApp greeting] [Send email greeting] [Dismiss]"
- "Diwali is in 5 days. [Generate greeting card + compose group email] [Dismiss]"
- "SAT registration deadline in 2 weeks. [Add to calendar] [Dismiss]"

**Criteria for Level 1 elevation:**
- 30 days of Level 0 operation
- Zero false positives in critical domain alerts (Immigration, Finance)
- Morning briefing accuracy ≥ 95% (confirmed by user feedback)
- All recommended actions reviewed (even if dismissed)

### Trust Level 2 — Executor (Earned after 60 days of reliable Level 1)

Artha **acts autonomously on pre-approved action types**, with post-hoc notification. The set of pre-approved actions is defined explicitly by the user — never assumed.

Example pre-approved actions at Level 2:
- Auto-add confirmed bill due dates to calendar
- Auto-archive school newsletters after generating digest
- Auto-log Amazon delivery confirmations to shopping tracker
- Auto-generate visual greeting cards for upcoming occasions (saved locally, not sent)

**Criteria for Level 2 elevation:**
- 60 days of Level 1 operation
- All Level 1 proposed actions accepted ≥ 90% for the specific action type
- Explicit user confirmation of which action types are pre-approved
- Revocable at any time with immediate effect

### Autonomy Floor Rules (Cannot Be Overridden)

Regardless of trust level, the following actions always require explicit human confirmation:
- Any financial transaction or payment
- Any communication sent on your behalf (email, message)
- Any immigration-related document submission or application
- Any deletion of data
- Any action affecting another person's data (Priya, Arjun, Ananya)

### Elevation & Demotion Process

**Elevation:** Artha tracks elevation criteria in `health-check.md` (see tech spec §12.11). When all criteria for the next level are met, Artha surfaces a recommendation during catch-up: "All Level 1 criteria met over the past 30 days. Recommend elevation to Advisor level." The user approves or defers. Elevation is logged to `audit.md`.

**Demotion:** Trust can be revoked at any time. Automatic demotion triggers:
- Any critical false positive (immigration, finance) → immediate demotion to Level 0
- Action acceptance rate drops below 70% at Level 2 → alert + recommend demotion to Level 1
- User command: "Artha, go back to Level 0" → immediate demotion

Demotion resets the elevation clock — criteria must be re-met from scratch. This ensures trust is genuinely earned, not accumulated through inertia.

### Self-Improvement via Trust

As Artha earns higher trust levels, it gains access to self-improvement capabilities:
- **Level 0:** Corrections and preferences are logged to `memory.md` for future reference
- **Level 1:** Artha can propose routing rule and domain prompt improvements (user approves)
- **Level 2:** Artha can auto-fix minor extraction errors (e.g., update a sender pattern) with post-hoc notification

---

## 10. Data Sources & Integrations

Artha uses a **pull-based data source strategy** — all sources are fetched in batch during each catch-up run:
1. **MCP tool connectors** for email and calendar (Gmail MCP, Google Calendar MCP)
2. **LLM-based email parsing** for all other sources (bills, school notifications, financial alerts arrive via email)
3. **Manual input** for edge cases and initial bootstrapping
4. **Microsoft Graph API** for task management (Microsoft To Do sync — Phase 1B)

### Email Coverage — Hub-and-Spoke Model

Gmail is the single Artha integration point for all email accounts. All other accounts forward to Gmail; Artha does not need separate OAuth flows per account. Gmail filters apply labels (`from-outlook`, `from-apple`, etc.) to preserve source identity for routing.

| Account | Integration Method | Status | Primary Domains | Gmail Label |
|---|---|---|---|---|
| Gmail (configured in user_profile.yaml) | Direct — Gmail MCP (OAuth) | ✅ Active | All | — |
| Outlook.com (configured in user_profile.yaml) | Auto-forward → Gmail (T-1B.1.1) | Phase 1B | Immigration, Finance, Comms | `from-outlook` |
| Apple iCloud (icloud.com) | Auto-forward → Gmail (T-1B.1.2) | Phase 1B | Finance, Digital Life | `from-apple` |
| Yahoo | Auto-forward → Gmail (T-1B.1.3, if active) | Phase 1B — evaluate | Finance, Comms | `from-yahoo` |
| Proton Mail | Proton Bridge → IMAP → Gmail (T-1B.1.4) OR excluded | Phase 2 / excluded | Personal (boundary) | `from-proton` |

**Email coverage gap acknowledgment:** Until Outlook and Apple forwarding are configured (Phase 1B), [immigration attorney]/Employer HR immigration emails and Apple receipts may not reach Artha. The `/health` command surfaces the `email_coverage` matrix so gaps are visible, not silent.

### All Data Sources

| Source | Access Method | Data Available | Fetch Pattern |
|---|---|---|---|
| Gmail (primary) | Gmail MCP (OAuth) | All email — read only | Batch pull on catch-up |
| Outlook.com | Forward to Gmail (T-1B.1.1) | All email — read only | Via Gmail batch |
| Apple iCloud | Forward to Gmail (T-1B.1.2) | App Store receipts, Apple account alerts | Via Gmail batch |
| Yahoo | Forward to Gmail (T-1B.1.3, if active) | Legacy email (if active) | Via Gmail batch |
| Proton Mail | Excluded (personal comms boundary) OR Proton Bridge (Phase 2) | Personal comms (by design, excluded) | Phase 2 |
| Google Calendar | Google Calendar MCP | All events — read only | Batch pull on catch-up |
| Microsoft To Do | Microsoft Graph API (T-1B.6.x) | Task lists (read + write) | Sync after catch-up |
| **Microsoft Work Calendar** *(v4.1)* | **WorkIQ MCP** (`@microsoft/workiq` — pinned version) | Work meetings: title, time, duration, organizer, Teams link. Calendar only — no email/chat. | Batch pull on catch-up (Windows only) |
| Home Assistant | Local API (LAN only) | Device status, energy | Pull on catch-up (if Mac on LAN) |
| Fidelity | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Balance, transaction alerts | Via email batch |
| Chase | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Balance, transaction alerts | Via email batch |
| Wells Fargo | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Mortgage, FICO score | Via email batch |
| Vanguard | Email parsing (LLM-based) — *direct financial API deferred (see §13 note)* | Statement alerts, balances | Via email batch |
| Metro Electric | Email parsing (LLM-based) | Bill amount, due date | Via email batch |
| City Water | Email parsing (LLM-based) | Bill amount, due date | Via email batch |
| USCIS / [immigration attorney] | Email parsing (LLM-based) + **USCIS Status Skill** | Case status updates | Via email batch + **direct HTTP lookup** |
| ParentSquare | Email parsing (LLM-based) | School notifications | Via email batch |
| Canvas (Instructure) | Email parsing (LLM-based) → **Phase 2 (Blocked)**: Canvas REST API | Grade, attendance, assignment details | Via email batch → **API pull** |
| USPS Informed Delivery | Email parsing (LLM-based) | Physical mail preview | Via email batch |
| Marriott Bonvoy | Email parsing (LLM-based) | Points balance, bookings | Via email batch |
| Alaska Airlines | Email parsing (LLM-based) | Booking confirmations, miles | Via email batch |
| Equifax | Email parsing (LLM-based) | Credit monitoring signals | Via email batch |
| USCIS Visa Bulletin | Gemini CLI web search (monthly) | Priority date movements | On catch-up (monthly check) |
| Zillow/Redfin | Gemini CLI web search (quarterly) | Comparable sales, home value | On catch-up (quarterly check) |
| Auto Insurance Carrier | Email parsing (LLM-based) | Policy renewal, premium, claims | Via email batch |
| Homeowners Insurance Carrier | Email parsing (LLM-based) | Policy renewal, premium, claims | Via email batch |
| WA DOL (Vehicle Registration) | Email parsing (LLM-based) | Registration renewal notices | Via email batch |
| NHTSA Recall Database | Gemini CLI web search (monthly per VIN) | Active vehicle recalls | On catch-up (monthly check) |
| ISP / Telecom Provider | Email parsing (LLM-based) | Bill amount, service changes | Via email batch |
| City Waste Services (Waste) | Email parsing (LLM-based) | Pickup schedule, billing | Via email batch |
| Sangamon County Assessor | **County Tax Skill** | Property tax assessment, due dates | **Direct HTTP lookup (Phase 1)** |
| Employer Benefits Portal | Manual input (annual open enrollment) | Benefits elections, coverage details | Manual |
| Apple Health (HealthKit) | HealthKit XML export (manual or Shortcuts-automated) | Steps, sleep, heart rate, workouts, weight | Import on catch-up (weekly cadence) |

### 11.x Data Fidelity Skills *(v4.0)*

To enhance data fidelity beyond email parsing, Artha uses targeted **"Skills"** — small, lightweight lookups that query institutional portals or official APIs directly.

**1. Compliance & Stability Philosophy**
- **Public Data Only:** Scrapers are permitted ONLY for public, non-authenticated portals (e.g., USCIS, Sangamon County Tax, NOAA).
- **Authorized APIs Only:** Authenticated access is restricted to documented, provider-supported APIs (e.g., Canvas LMS, OFX, MS Graph, AirNow).
- **No Reverse Engineering:** Unofficial clients or unauthorized scraping of private portals is strictly forbidden to prevent account bans or legal risk.

**2. Fail-Safe Logic**
- **P0 (Immigration):** 
    - **Logical Error/Parse Error:** Halts catch-up (data integrity unknown).
    - **Status Change:** Alerts user immediately (P0); catch-up continues to ensure briefing delivery.
    - **Transient Error (503/timeout):** Warns and continues.
- **P1/P2 (Finance/Tax/Safety):** Skill failures log a warning and continue the catch-up flow.

**3. Intelligence Foundation**
- **Change Detection:** Skills track their own previous state in `state/skills_cache.json`. Alerts only fire when a meaningful field (e.g., USCIS status) changes.
- **Execution Cadence:** Skills support per-run, daily, or weekly cadences to minimize network traffic and rate-limit risk.

**4. Roadmap**
- **Phase 1.1 (Infra):** Centralized state, dynamic loader, and cadence control.
- **Phase 1.2 (Immigration):** USCIS Visa Bulletin parser (EB-2 India, Table A & B, Authorized Chart).
- **Phase 1.3 (Safety/Property):** NHTSA Recall checks (Kia/Mazda) and Sangamon County Assessed Value extension.
- **Phase 1.4 (Concierge):** NOAA Weather unblocking outdoor Open Items.
- **Phase 2.0 (Credentialed):** OFX Bank direct download (Chase) and AirNow AQI (EPA).

**All sources are pull-based.** There are no push notifications, no webhooks, no event-driven triggers. Every data source is queried in batch during each catch-up session. Because all non-API sources arrive via email, Gmail MCP is the single integration point for ~80% of data sources.

**Microsoft To Do integration:** `todo_sync.py` pushes action items extracted by Artha (stored in `open_items.md`) to domain-tagged Microsoft To Do lists. Users manage and complete tasks on iPhone; completion status is pulled back to `open_items.md` on the next catch-up. Microsoft Graph API covers both To Do and Outlook with a single OAuth flow. See tech spec §11.4.

**Parsing strategy:** All email parsing uses LLM-based extraction (send email body to Claude for structured output) rather than regex/template-based parsing. Claude naturally handles format variations from ParentSquare, financial institutions, and other sources.

**Phase 2 data upgrade path:** Financial institutions (Chase, Fidelity, Vanguard, Wells Fargo) will upgrade from email parsing to Plaid API integration (read-only) in Phase 2. This provides real-time balance and transaction data, enabling the "net worth on demand" target.

**Not in scope for personal surface (by design):**
- Work email on the personal surface — work data is handled exclusively by the Work OS (FR-19; see Tech Spec §19)
- WhatsApp inbound messages — no public API; **local DB reading is implemented** for WhatsApp Desktop (macOS + Windows) via `whatsapp_local` connector (metadata only on Windows — message body is encrypted at rest)
- iMessage — **local DB reading is implemented** for macOS via `imessage_local` connector (requires Full Disk Access grant); no remote API access
- Proton Mail (unless Proton Bridge configured in Phase 2) — personal comms boundary; E2E encryption prevents standard forwarding

> **Work OS data sources** (Teams, ADO, MS Graph work calendar, SharePoint) are consumed exclusively by the Work OS (FR-19) via `scripts/work_loop.py`. No work data reaches the personal surface. See Tech Spec §19.4 for the Work OS connector protocol.

---

## 11. Privacy Model

Artha handles deeply personal data across all domains of your life. Privacy is not a feature — it is a foundational constraint.

### 12.1 — Data Residency

All data in OneDrive-synced `~/OneDrive/Artha/`. Sensitive state files `age`-encrypted before sync. Keys in device-local credential stores (macOS Keychain), never on OneDrive. Mac is sole writer. Three PII defense layers: `pii_guard.py` (regex pre-persist), Claude redaction (semantic), `safe_cli.py` (outbound wrapper). Only external flow: Claude API (ephemeral, not used for training). Backup: OneDrive versioning + Time Machine.

### 12.2 — Data Minimization

Extract-and-discard: email content parsed for entities (amounts, dates, names), then discarded. Full email bodies not retained. Three-layer PII defense: Layer 1 (device regex) + Layer 2 (LLM redaction) + Layer 3 (outbound wrapper). See tech spec S8.6-8.7.

### 12.3 — Family Data Governance

Artha handles data about Priya, Arjun, and Ananya. The governing principle: Artha tracks events and statuses that affect the family's well-being (school grades, immigration status, health appointments). It does not monitor personal communications, social activity, or private exchanges.

Specifically:
- ✅ Arjun's grade alerts, SAT dates, club activities — tracked
- ✅ Priya's immigration status, appointment calendar — tracked
- ❌ Arjun's personal messages, friend communications — not tracked
- ❌ Priya's personal email content — not tracked (only bill/immigration/scheduling signals)

### 12.4 — Immigration Data Special Handling

Immigration data is the most sensitive category in Artha. Special handling rules:
- Immigration documents (passport numbers, A-numbers, receipt numbers) are stored encrypted in local state
- Case numbers and document expiry dates are indexed; full document content is not
- Attorney correspondence is parsed for status updates only; legal advice content is not stored

### 12.5 — Audit Rights

The user can at any time:
- View everything Artha has stored (full state dump)
- Delete any data category
- Revoke any data source connection
- Export the full Artha state in portable format

### 12.6 — Privacy Surface Acknowledgment *(v3.8)*

Claude Code sends all context to Anthropic's API (ephemeral — not retained for training). PII defense layers protect *persisted* and *outbound* data, but do not prevent Claude from seeing raw email content via MCP. Mitigated by: three-layer PII defense, email pre-processing to reduce exposure, tiered context loading, full audit trail, revocable data source connections. Artha.md §4.3 documents this in plain language.

### 12.7 WorkIQ Privacy Rules *(v4.1)*

Work calendar data from WorkIQ MCP: meeting titles enter Claude API after local codename redaction (ephemeral, not persisted). Only count+duration metadata persisted to `work-calendar.md` (13-week rolling). No meeting bodies, chat, or email content requested. Windows-only (M365 Copilot license); Mac degrades gracefully. Corporate compliance confirmed for calendar metadata through Claude API.

---

## 12. Phased Roadmap

### Phase 1 — Foundation (Months 1–2)
*Objective: Deliver immediate daily value in the highest-friction domains with zero custom code*

> **v3.0 Note:** Phase 1 is dramatically simplified compared to v2.2. No daemon infrastructure, no SQLite, no event sourcing, no pre-processor pipeline. Just: Artha.md + domain prompts + MCP tools + local Markdown state files. The entire "infrastructure" is a well-written instruction file.

#### Phase 1A — Core Setup (Weeks 1–2)

| Area | Tasks |
|---|---|
| Identity & Config | Artha.md (identity, workflow, routing, multi-LLM), CLAUDE.md loader, directory structure, registry.md, versioning, governance baseline in audit.md |
| Data Sources | Gmail MCP (OAuth, 3-5hr budget), Google Calendar MCP, Outlook forwarding resolution, OneDrive sync verification |
| Security | `age` encryption + Keychain, vault.sh + crash recovery watchdog, `pii_guard.sh` (8+ PII categories), `safe_cli.sh` outbound wrapper, PII allowlists in domain prompts, Claude Code hooks (decrypt/encrypt) |
| Bootstrap | Initial state files (immigration, finance, kids), contacts.md (encrypted), occasions.md, first end-to-end catch-up |
| Actions & Skills | WhatsApp URL scheme, Gmail send, calendar events, visual generation (Gemini Imagen), SMTP briefing delivery, Data Fidelity Skills (USCIS, Property Tax) |
| Multi-LLM | Verify Gemini CLI + Copilot CLI, routing rules, web search + validation tests |

#### Phase 1B — High-Value Domains (Weeks 3–5)
Communications (FR-1: school digest, action items), Immigration (FR-2: dashboard, deadlines, timeline, Visa Bulletin, CSPA age-out), Kids (FR-4: daily brief, grades). Setup: Outlook→Gmail forwarding, ensemble reasoning test, contacts population, visual greeting e2e test.

#### Phase 1C — Goal Engine + Finance (Weeks 6–8)
Goal Engine (FR-13: conversational creation, metric wiring, weekly scorecard, conflict detection). Finance (FR-3: bill calendar, spend alerts, forecasting). Conversation Memory in memory.md. Claude.ai Project for mobile access.

**Phase 1 initial goals (resolved from OQ-3):**
1. Net worth / savings trajectory
2. Immigration readiness (all documents current, deadlines known)
3. Arjun academic trajectory (GPA target)
4. Protected family time (≥ X hours/week)
5. Learning consistency (hours/month target)

**Phase 1 success criteria:** ≥95% briefing accuracy, <3min run time, <$50/mo cost, zero missed Critical alerts, ≥5 active goals, school noise reduced ≥70%, all immigration documents tracked, intelligent alerting (suppress unchanged statuses), zero custom code.

---

### Phase 2A — Intelligence Deepening *(v3.8, expanded v3.9)*
*Objective: Deepen intelligence with relationship awareness, leading indicators, decision tracking, operational improvements — all spec-driven, no new infrastructure*

**Build (24 workstreams):**

| ID | Workstream | Key FRs | Priority |
|---|---|---|---|
| A | Relationship Intelligence | F11.1-F11.10: graph model, reconnect radar, cultural protocols | P1 |
| B | Goal Leading Indicators | S8.11: leading+lagging side-by-side in scorecard | P1 |
| C | Decision Graphs | F15.24: structured decision log, `/decisions` command | P1 |
| D | Life Scenarios | F15.25: what-if analysis for major decisions | P1 |
| E | Email Pre-Processing | S9.4 step 5: 1,500 token cap, batch summarization | P1 |
| F | Tiered Context Architecture | S9.8: Always/Active/Reference/Archive tiers, 30-40% savings | P1 |
| G | ONE THING Reasoning | S9.4 step 8: URGENCY x IMPACT x AGENCY scoring | P1 |
| H | Digest Mode | F15.26: >48hr gap triggers priority-tier grouping | P1 |
| I | Accuracy Pulse | F15.27: action accept/decline tracking, weekly trends | P1 |
| J | Privacy Surface | S12.6: explicit API privacy acknowledgment in Artha.md | P1 |
| #10 | Action Friction Field | low/standard/high friction classification | P1 |
| K | Data Integrity Guard | F15.28: pre-decrypt backup, net-negative write guard | **P0** |
| L | Life Dashboard | F15.29: auto-generated dashboard.md after each catch-up | P1 |
| M | Compound Signals | F15.30: cross-domain convergence detection | P1 |
| N | Calendar Intelligence | F15.31: logistics, preparation, energy/load balancing | P1 |
| O | Coaching Engine | F13.14-16: implementation plans, obstacle anticipation | P1 |
| P | Bootstrap Command | F15.33: guided interview for empty state files | **P0** |
| Q | Pattern of Life | F15.34: 30-day behavioral baselines | P1 |
| R | Resilience Suite | F15.35-43: signal:noise, stale detection, OAuth, scaling | P1 |
| S | Life Scorecard | F15.44: quarterly assessment, YoY comparison | P1 |
| T | Briefing Amplification | F15.46-50: calibration Qs, PII footer, retrospectives | v4.0 |
| U | Task Intelligence | F8.6-7, F15.48, F15.53: scheduling, Power Half Hour | v4.0 |
| V | Goal Expansion | F13.17-18: mandatory targets, auto-detection | v4.0 |
| W | Conversational Intel | F15.51-55: /diff, Ask Priya, Teach Me, NL queries | v4.0 |
| X | Family & Cultural | F11.11, F4.11: India scheduling, college countdown | v4.0 |

**Phase 2A success criteria (key metrics):**
- FR-11 operational with ≥20 tracked relationships
- Leading indicators for ≥5 goals; decision graph with ≥10 entries after 30 days
- Email pre-processing: ≥40% token reduction; tiered context: ≥30% token reduction
- Data integrity guard: zero data-loss incidents
- Bootstrap populates ≥3 empty state files; dashboard refreshed every catch-up
- Coaching engine: plans for ≥5 goals; pattern baselines after 30 days
- Session quick-start: <10s for non-catch-up; context pressure stays below orange
- Goal Sprint targets calibrated for all active goals (v4.0)
- College countdown dashboard active (v4.0); monthly retrospective generated (v4.0)

---

### Phase 2B — Domain Expansion (Months 3–5)
*Objective: Expand domain coverage, deepen intelligence, add helper scripts only where Claude proves insufficient*

**Build:**
- Travel prompt (FR-5): Trip dashboard + travel document checker
- Health prompt (FR-6): Family appointment calendar + HSA tracker + open enrollment support (F6.7, F6.8)
- Home prompt (FR-7): Utility bill calendar + mortgage tracker + telecom tracker (F7.8) + property tax tracker (F7.12) + emergency preparedness (F7.13)
- Calendar prompt (FR-8): Unified calendar + conflict detector
- Learning prompt (FR-10): Newsletter digest + course progress tracker
- Boundary prompt (FR-14): After-hours work signal + personal time protection
- **Insurance prompt (FR-16):** Policy registry (F16.1), premium tracker (F16.2), renewal calendar (F16.3), coverage adequacy review (F16.4), teen driver prep (F16.6), Employer benefits optimizer (F16.8)
- **Vehicle prompt (FR-17):** Vehicle registry (F17.1), registration renewal tracker (F17.2), maintenance schedule (F17.3), service history (F17.4), warranty tracker (F17.5), teen driver program for Arjun (F17.7)
- **Finance prompt expansion:** Tax preparation manager (F3.10), insurance premium aggregator (F3.11), **credit card benefit optimizer (F3.12)**, **tax season automation workflow (F3.13) *(v4.0)***
- **Vehicle prompt expansion:** **Lease & lifecycle manager (F17.9)**
- **Kids prompt expansion:** Paid enrichment tracker (F4.8), activity cost summary (F4.9), **Canvas LMS direct API integration for grades/assignments/analytics (F4.10) *(v4.0)***
- **Health prompt expansion *(v4.0)*:** Apple Health/HealthKit integration — parse XML export for steps, sleep, heart rate, workouts, weight; wire to wellness goals (F6.9)
- **Digital prompt expansion *(v4.0)*:** Subscription ROI tracker — cost vs. usage frequency analysis with cancel/keep recommendations (F12.6)
- **Goal Engine expansion:** Goal cascade view (F13.5), recommendation engine (F13.7), trajectory forecasting (F13.10), behavioral nudge engine (F13.11), dynamic replanning (F13.12)
- **Insight Engine (F15.11):** Extended thinking for weekly deep reasoning across all domain state
- **Proactive Check-in (Mode 6):** Integrated into catch-up flow when data warrants
- *~~Plaid integration~~* — **Deferred.** Direct financial data API integration (FDX/Section 1033 or Plaid) deferred beyond Phase 3. Email-based parsing continues for financial institutions.
- **Family access model:** Tiered access for Priya (shared domains), Arjun (academic view), Ananya (age-appropriate view) — via separate Claude.ai Projects with filtered state
- **State volume check:** If state files exceed 150K tokens total, introduce SQLite for historical data

**Phase 2B success criteria:** All 17 personal domains covered, ≥10 goals with auto-metrics + conflict detection + forecasting, Priya active on shared domains, all insurance/vehicles registered with alerts, ≤3 helper scripts. v4.0 additions: Canvas LMS API, Apple Health integration, subscription ROI, tax season workflow.

---

### Phase 2C — Work Intelligence OS *(v2.7.0 — all phases complete)*
*Objective: Isolated work intelligence layer — daily work briefing, meeting prep, commitment tracking, career evidence, people graph, narrative engine, connect cycle intelligence, promotion OS, and decision support. Hard separation from personal surface.*

**Status: Implemented** (v2.7.0, Phases 1–5 complete). See Tech Spec §19 for full technical architecture; see UX Spec §23 for interaction design.

**Key deliverables (completed):**
- `scripts/work_loop.py` — 7-stage processing loop (fetch → enrich → filter → infer → score → write → bridge); `--mode read` (fast cached reads) and `--mode refresh` (full connector refresh)
- `scripts/work_warm_start.py` — historical import processor (81-week scrape corpus; ScrapeParser + WarmStartAggregator)
- `scripts/work_bootstrap.py` — guided 12-question setup interview for cold-starts (no scrape archive required)
- `scripts/work_notes.py` — post-meeting notes capture with D-NNN / OI-NNN sequenced IDs
- `scripts/work_reader.py` — 25-command read-path CLI (<2s from cached state)
- `scripts/work_domain_writers.py` — atomic writers for all 11 work domain state files
- `scripts/narrative_engine.py` — 10 narrative templates (weekly_memo, talking_points, boundary_report, connect_summary, newsletter, deck, calibration_brief, connect_evidence, escalation_memo, decision_memo)
- `scripts/post_work_refresh.py` — post-refresh summarization (mirrors post_catchup_memory.py pattern)
- `scripts/kusto_runner.py` — KQL query execution bridge (Microsoft Enhanced tier)
- `scripts/schemas/work_objects.py` — 6 canonical dataclasses (WorkMeeting, WorkDecision, WorkCommitment, WorkStakeholder, WorkArtifact, WorkSource)
- `scripts/schemas/work_connector_protocol.py` — connector error protocol with PROTOCOL table and defined fallback per connector
- `scripts/schemas/bridge_schemas.py` — schema-validated bridge artifacts (WorkLoadPulse, BridgeArtifact, BridgeManifest); alert isolation enforcement
- `config/agents/artha-work.md`, `artha-work-enterprise.md`, `artha-work-msft.md` — 3 agent tiers (baseline M365 / corporate ADO / Microsoft Enhanced)
- `state/work/` — 20 domain state files covering all 11 Work OS domains plus bridge artifacts
- `state/bridge/work_load_pulse.json` + `state/bridge/work_context.json` — boundary bridge artifacts
- `.github/workflows/work-tests.yml` — CI matrix (Python 3.11/3.12/3.13) + prompt-lint gate
- 883 tests passing across 12 test suites in `tests/work/`

---

### Phase 3 — Autonomy & Prediction (Months 6–9)
*Objective: Elevate predictive intelligence, voice access, and begin earning execution autonomy*

**Build:**
- Digital prompt (FR-12): Subscription audit + account security monitor
- Shopping prompt (FR-9): Monthly spend summary + return window tracker
- **Estate prompt (FR-18):** Estate document registry (F18.1), beneficiary audit (F18.2), document review cycle (F18.3), life event legal triggers (F18.4), emergency access guide (F18.5), guardianship planning (F18.7), **emergency contact wallet card generator (F18.8) *(v4.0)***
- **WhatsApp Business Bridge (F1.7) *(v4.0)*:** WhatsApp Business API or web bridge for message context ingestion — school groups, family groups, activity coordination. Human-gated send via URL scheme.
- **Home prompt expansion:** Waste & recycling (F7.9), HOA/community dues (F7.10), lawn & landscaping schedule (F7.11)
- **Insurance prompt expansion:** Life event coverage triggers (F16.5), claims history log (F16.7)
- **Vehicle prompt expansion:** Fuel/charging cost tracker (F17.6), recall monitor (F17.8), **TCO calculator (F17.10)**
- Goal Engine: Annual retrospective (F13.8), seasonal pattern awareness (F13.13), full cascade view
- **Predictive Calendar (F15.15):** Model recurring events, proactive predictions with confidence levels
- **Voice interface:** Apple Shortcuts + Whisper/Claude pipeline for voice queries
- Autonomy Layer: Pre-approved action categories execute with post-hoc notification
- Artha Memory: Longitudinal pattern recognition across all domains
- **State scaling:** SQLite for historical data, RAG for conversation memory/briefing archives if needed

**Phase 3 success criteria:** All 18 FRs covered, full goal hierarchy with seasonal awareness, ≥3 pre-approved action types, annual retrospective, ≥1 non-obvious insight/week, voice queries functional, predictive calendar ≥70% accuracy, estate documents inventoried, emergency preparedness complete.

---

## 13. Success Criteria

Artha succeeds when it materially changes how you navigate your life. The following metrics will be tracked by Artha itself — as its own primary goal:

| Metric | Target | Measured via |
|---|---|---|
| Critical alerts missed | Zero | audit.md |
| Catch-up briefing accuracy | >= 95% | User feedback |
| Goal tracking coverage | >= 10 active goals with auto-metrics | Goal Engine state |
| Immigration deadline lead time | 100% known >= 90 days out | immigration.md |
| Monthly AI cost | <$50 | API usage dashboard |
| Catch-up run time | <3 minutes | health-check.md |
| Catch-up reliability | >= 95% complete | health-check.md |
| Per-domain accuracy | >= 90% (30-day rolling) | health-check.md |
| Action acceptance rate | >= 80% | audit.md |
| Domain coverage | All 18 FRs active | Prompt count |
| Data integrity incidents | Zero | audit.md write guard log |
| Signal:noise ratio | >= 60% per domain | health-check.md |
| Session quick-start latency | <10s for non-catch-up | Time-to-first-response |
| Email token savings | >= 40% reduction | health-check.md |
| Context tier savings | >= 30% reduction | health-check.md |
| Relationships tracked | >= 20 with tier + protocol | social.md |
| Bootstrap coverage | <= 2 template-only files after 1st month | State file audit |
| Work calendar merge accuracy | <= 5% dedup errors (v4.1) | Manual audit |

Full version-tagged metrics (v3.8, v3.9, v4.0, v4.1) are tracked in `config/implementation_status.yaml`.

### 14.4 Automated Testing Requirements

To ensure the long-term stability and security of the Artha OS, the following automated testing requirements are mandatory:

**TR-1: Security-Critical Script Validation (P0)**
- All security-critical scripts (`pii_guard.sh`, `vault.py`, `preflight.py`) must have 100% test coverage for their core logic.
- Tests must verify PII detection across all 8+ categories (SSN, CC, ITIN, etc.) and ensure zero leakage.
- Vault tests must verify the "Net-Negative Write Guard" to prevent data loss >20%.

**TR-2: "Golden File" Extraction Regression (P1)**
- Domain extraction prompts must be validated against "Golden File" snapshots.
- A library of mock email inputs (JSONL) and expected state outputs (Markdown) must be maintained.
- Tests must verify that prompt updates do not degrade extraction accuracy for historically handled patterns.

**TR-3: Cross-Platform Consistency (P1)**
- Tests must pass identically on macOS and Windows (or clearly skip OS-specific features with a warning).
- Script health checks (`--health`) must be validated by the testing framework.

---

## 14. Non-Functional Requirements

Artha runs on personal data with no ops team — Raj is the sole operator. NFRs must be self-enforcing.

### 15.1 — Performance

| Metric | Target | Measurement |
|---|---|---|
| On-demand chat (state-file query) | <10 seconds | Query to response time |
| On-demand chat (requires email fetch) | <30 seconds | Query to response time |
| Full catch-up run | <3 minutes | Start to briefing delivery |
| Parallel fetch speedup | Email + calendar fetched simultaneously | Claude Code parallel tool invocation |
| Weekly summary generation | <60 seconds | Within catch-up session |
| Goal scorecard generation | <30 seconds | Within catch-up session |

### 15.2 — Reliability

| Metric | Target | Notes |
|---|---|---|
| Catch-up completion rate | ≥ 95% | Percentage of catch-ups that complete without errors |
| MCP tool connection success | ≥ 99% | Gmail + Calendar OAuth working on each run |
| State file integrity | Zero corruption | Markdown files validated on each write |
| API unavailability handling | Graceful degradation | Note which sources failed, proceed with available data |
| Recovery from partial failure | Resume from last successful step | Health-check file tracks progress |

### 15.3 — Storage & Data

| Metric | Target | Notes |
|---|---|---|
| State file total size (year 1) | <5 MB projected | ~18 Markdown files × ~50 KB each |
| Data retention: parsed email metadata | Indefinite | Needed for seasonal pattern detection |
| Data retention: full email body | Not stored | Parse and discard — privacy by design |
| Data retention: raw document content | Not stored | Extract-and-discard — document stays in repository |
| Sensitive data in state files | Redacted per domain rules | SSN: never stored. Passport, A-number, routing numbers: `[REDACTED]` |
| Briefing email sensitivity filter | High/critical domains: summary only | Prevents sensitive data in transit via email |
| Data retention: briefings/summaries | Indefinite archive | Human-readable Markdown files |
| Backup strategy | OneDrive versioning (primary) + Time Machine (secondary) | OneDrive: 30-day version history. Time Machine: encrypted local. |
| Backup location | OneDrive (synced) + local Mac | Encryption keys in device-local credential stores only |

### 15.4 — Observability

Artha monitors its own health. Accessible via on-demand chat ("Artha, are you healthy?") or by reading `~/OneDrive/Artha/state/health-check.md`:

| Signal | Method | Alert threshold |
|---|---|---|
| Last catch-up timestamp | Timestamp in health-check.md | >48 hours since last run |
| State file freshness per domain | Last-updated timestamp in each state file | >7 days stale for active domain |
| MCP tool connection status | Tested on each catch-up run | Any tool failing to connect |
| LLM API cost (monthly) | Tracked from API usage dashboard | Monthly >$50 |
| Briefing email delivery | Confirmed via email send status | Failed delivery on catch-up |
| Unprocessed email backlog | Count of emails since last run | >500 (suggests catch-up frequency too low) |

### 15.5 — Security

| Requirement | Implementation |
|---|---|
| Immigration data encryption | AES-256 at rest for passport numbers, A-numbers, receipt numbers |
| API credentials | macOS Keychain, never in plaintext config files |
| OAuth tokens | Stored in Keychain, auto-refreshed |
| Audit trail | Immutable append-only log of all Artha actions and recommendations |
| Data export | User can export full state in portable format at any time |
| Data deletion | User can delete any data category immediately |
| Pre-flight PII detection | ≥99% for structured PII (SSN, CC, routing numbers) at device boundary | `pii_guard.sh` regex filter |
| PII defense-in-depth | Two independent layers: regex pre-flight (Layer 1) + LLM redaction (Layer 2) |

### 15.6 — Geographic Portability

No US-centric hardcoding. Provider-agnostic via Integration Adapter Pattern (§9.6). Jurisdiction-aware rules are configuration, not code. Overlapping jurisdiction support during transitions. Fully portable state (OneDrive folder + Keychain re-provisioning).

### 15.7 — Cost

| Component | Target | Control mechanism |
|---|---|---|
| Claude API (monthly) | <$50 | Prompt caching + batch processing + usage dashboard alerts |
| Gmail API | Free tier (15,000 units/day) | Well within limits for personal email volume |
| Google Calendar API | Free tier | Well within limits |
| Gemini CLI | Free tier quota | Web research, URL summarization, Imagen visual generation — $0 |
| Copilot CLI (GitHub) | Free tier | Script/config validation — $0 |
| ~~Plaid API~~ FDX/Financial API | **Deferred** | Direct financial data integration deferred beyond Phase 3 |
| Canvas LMS API | Free tier (institutional) | Covered under school’s Canvas license — $0 |
| Apple Health export | $0 | Local XML export, no API cost |
| Custom infrastructure | $0 | No VMs, no servers — OneDrive is existing subscription |
| OneDrive storage | $0 incremental | Artha state + visuals < 50 MB — negligible within existing OneDrive plan |
| Total | <$55/month | Self-monitored via health-check. Multi-LLM routing saves ~$3-6/month vs. Claude-only |

---

## 15. Open Questions — Resolved

All resolved. Key decisions: **OQ-1** Email delivery (most portable). **OQ-2** Tiered family access (Raj=full, Priya=shared domains, kids=filtered views). **OQ-3** Top 5 goals: net worth, immigration readiness, academics, family time, learning. **OQ-4** Work hours: 8AM-6PM weekdays. **OQ-5** College prep as formal Milestone goal with sub-milestones. **OQ-6** Extract priority date from [immigration attorney] correspondence (critical). **OQ-7** Verify Priya's independent accounts. **OQ-8** Home Assistant: local URL + long-lived token. **OQ-9** Define financial targets via goal creation wizard. **OQ-10** India-specific intelligence at P2 priority.

---

*Artha PRD v4.2 — End of Document*

*"Artha is not about having more. It is about knowing where you stand, so you can decide where to go."*

> Phase 2B features (Insight Engine, Proactive Check-In, Goal Engine Expansion, Spouse Briefing,
> Autonomy Elevation) are specified in `config/implementation_status.yaml`.
