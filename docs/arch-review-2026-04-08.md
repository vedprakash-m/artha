# Artha — Lead Principal Architectural Review
**Date:** 2026-04-08  
**Reviewer:** Lead Principal AI System Architect (read-only engagement)  
**Scope:** Full system — specs/kb-graph.md, artha-prd.md, artha-tech-spec.md, artha-ux-spec.md + codebase (knowledge_graph.py, kb_bootstrap.py, mcp_server.py)  
**Constraint:** Observations only. No code or file modifications.

---

## Executive Summary

Artha is architecturally sound at its foundation — Markdown-first, local-first, SQLite-enhancement, human-gated — and these four invariants are well-enforced across design decisions. The specs are unusually rigorous for a personal intelligence system, the risk register is honest, and the phased plan is realistic.

However, there are **two critical architectural failures that exist right now** in the live system, not as future risks: the confidence model is entirely non-functional (1,890 entities with `source_type=NULL`), and the knowledge graph has no lifecycle filtering, meaning cancelled and archived entities will contaminate results the moment Phase 0 ships. Both are fixable in Phase 0. Everything else in this review is consequential but secondary.

---

## Phase A — Conceptual Integrity

### A1. PATH A→B Bridge: The Central Architectural Debt

**Finding:** Every signal processed by Artha's connector infrastructure (WorkIQ, ADO, email, calendar) terminates in ephemeral `state/work/*.md`. The durable knowledge graph (`knowledge/*.md` + SQLite) grows only when a human edits a markdown file or Phase 1's stub contract enforcement ships. No connector, no meeting transcript, no design review ever automatically promotes into the KB.

This is the most fundamental architectural gap in the system. The entire connectivity infrastructure (FR-19, EAR-3's four agents, the inbox pipeline, the SharePoint connector) feeds a layer that is fully discardable. In practice, an org change detected by WorkIQ today will not appear in a graph traversal of `artha_kb_context` tomorrow — unless the user curates it manually.

**Assessment:** This is a documented gap (G1, R1, Phase 1 critical path), not a surprise. The phased plan addresses it correctly. But the risk is that Phase 2 (inbox pipeline) and Phase 4 (SharePoint) are being designed in parallel with Phase 1 (stub contract). Any new ingestion path that ships before Phase 1 is complete will produce entities in SQLite that **cannot survive a `--force` rebuild**. The `kb_bootstrap.py` rebuild invariant (P6) will silently drop all connector-sourced entities.

**Priority:** Ship Phases 0 and 1 as a single atomic unit. No new ingestion path goes to production until the stub contract is in place and `--force` rebuilds produce identical results.

---

### A2. Confidence Model Is Dead Configuration — Active Failure Today

**Finding:** `_SOURCE_WEIGHT_SEEDS` seeds the `source_weights` table with 10 calibrated entries (weights ranging from 0.90 for `ado_sync` to 0.50 for `llm_extract`). `_determine_source_type()` is specced to classify every entity by its source. Neither is called anywhere in `kb_bootstrap.py`. Every one of the 1,890 live entities has `source_type=NULL`.

This means:
- All KB queries return entities at the same effective confidence (0.5 default)
- `state_md` entities (LLM-synthesized briefing prose) and `kb_file` entities (user-curated facts) are indistinguishable
- The quality gate in `context_for()` (`_DQ_MIN_CONFIDENCE = 0.5`) admits everything because nothing is below threshold
- The domain-aware quality weight system (`_DQ_DOMAIN_WEIGHTS`) is computing scores against garbage input

This is not a latent risk. It is an active architectural failure. Every response from `artha_kb_context` that includes a "confidence" value is a lie.

**Corroboration from code:** `kb_bootstrap.py` sets `source` to `"kb_file:{path.name}"` (string) but never assigns `source_type` (column). `knowledge_graph.py`'s `upsert_entity()` accepts `source_episode_id` but the caller never provides it from bootstrap.

**Priority:** Phase 0's `_determine_source_type()` fix is the single highest-priority unblocked work in the entire codebase. It should be treated as a P0 hotfix, not a phased feature.

---

### A3. Markdown-First Invariant Is Correctly Enforced

**Finding:** The three-layer enforcement of markdown-first is coherent and well-implemented:

1. `_assert_not_cloud_synced()` in `_resolve_db_path()` prevents SQLite from landing on OneDrive — the right guard at the right layer
2. `pipeline.py` isolation (must NOT import `KnowledgeGraph`) is architecturally correct and maintained
3. ADR-2 (SQLite at `%LOCALAPPDATA%`) + ADR-1 (markdown SoT) + ADR-3 (connector dedup in YAML) form a coherent, non-contradictory set

The `_assert_not_cloud_synced()` implementation logs a warning rather than raising an exception. This is defensively correct: it warns without crashing if the path detection heuristic has a false positive (e.g., a path containing "Library" on Windows). The trade-off is acceptable.

**One concern:** The warning fires and silently continues. If a future contributor moves the DB path accidentally into OneDrive and the WAL corrupts, the log warning is easy to miss. Consider promoting to a hard error in non-CI environments where the config is present.

---

### A4. Spec/Implementation Gap Is Large but Bounded

**Finding:** The spec (v2.0) is considerably ahead of the implementation (v1.0). The gap is fully documented in §5 (tested assumptions A1-A12, all confirmed as bugs). Key schema gaps: `lifecycle_stage` and `excerpt_hash` missing from `entities`, `change_source_ref` missing from `entity_history`, six inventory columns missing from `documents`.

What makes this acceptable: the gap is bounded (all gaps are spec'd, no surprises), the phased plan is sequenced correctly (Phase 0 adds schema, Phase 1 adds enforcement), and the `--force` rebuild as recovery mechanism (P6) means no schema migration is ever truly irreversible. Phase 0's ALTER TABLE chain with try/except per column (idempotency pattern already shown in `_migrate_schema_if_needed`) is the right approach.

**One risk:** `_SCHEMA_VERSION = "3"` and `_migrate_schema_if_needed()` currently handles `v1 → v3` only. Phase 0 must both add columns AND bump to `v4`, and the migration path `3 → 4` must be added. Forgetting the version bump is the most likely implementation error.

---

## Phase B — Architectural Trade-off Matrix

### B1. Heuristic Extraction: Right Decision for v1.0, Known Recall Cost

`FileExtractor`'s pattern-based approach (H2/H3 headers, status keywords, bold key-value pairs) with `_MAX_ENTITIES_PER_FILE=100` is the correct v1.0 strategy. Deferring LLM-assisted extraction to v2.1 avoids latency, cost, and non-determinism in the critical bootstrap path.

The **known cost** is documented but not quantified: U6 (untested assumption) estimates ≥60% meeting transcript recall. The pattern-matching approach likely performs well on `knowledge/*.md` (structured author-intent files) but poorly on `state/work/*.md` (prose summaries). Once `source_type` is populated, the confidence gap between these will make the recall difference visible.

No recommendation to change this. Accept the recall limitation consciously.

---

### B2. Leiden Clustering: Correct Upgrade, Currently Premature

Leiden via `graspologic` with Union-Find fallback (§6) is the right long-term architecture. At current scale (1,890 entities, 20 communities, low connectivity), Leiden is likely to produce the same result as Union-Find. The `graspologic` optional dependency (`pip install artha[graph]`) with runtime fallback is the correct mitigation.

**One observation:** The `_rebuild_communities_union_find()` fallback is the active implementation for nearly all users today. The community summaries surfaced in briefings (≤150 tokens via `god_nodes()`) are therefore Union-Find output. This is fine for v1.0. The Leiden upgrade becomes meaningful only when connectivity grows above ~200 entities.

---

### B3. FTS5 Without Vector Embeddings: Correctly Rejected, Gaps Need Mitigation

The non-goal rejection of vector embeddings (§14) is correct for a single-user, local-first, zero-infra system. FTS5 with porter stemmer is fast, reliable, and zero-dependency.

**Two confirmed FTS5 gaps worth flagging:**

1. **Synonym blindness:** "shipped" and "released", "person" and "individual", "XPF" and "Extended Platform Fleet" will not match. The `entity_aliases` table exists and is used in `resolve_entity_candidates()` — this is the correct mitigation. The gap is whether aliases are consistently populated.

2. **FTS query sanitization is present but conservative:** `search()` strips non-alphanumeric characters and produces an implicit AND query. This is safe and good. It means phrase search is disabled, which is correct (phrase search would fail on multi-word entity names). No issue here.

3. **`community_summaries` uses `LIKE '%"entity_id"%'`:** The `_community_context_for_entity()` method queries `WHERE entity_ids LIKE ?` with `f'%"{entity_id}"%'`. At 1,890 entities and 20 communities this is fast. At 10,000 entities it becomes a table scan on a TEXT column. This should be restructured to a junction table before scale becomes a concern.

---

### B4. 950-Token KnowledgeEnricher Budget: Correct Ceiling, Fragile in Practice

The 950-token ceiling (800 entity + 150 god nodes) is specified but the enforcement is upstream of `knowledge_graph.py` — `context_for()` respects a `token_budget` parameter but the 950-token constant is defined in the calling code (KnowledgeEnricher), not enforced by the graph itself.

**Observed behavior in `context_for()`:** The method increments `used_tokens` per item and breaks when budget is exceeded. This is correct greedy enforcement. However, the `token_budget=4000` default in the method signature means any caller that forgets to pass `token_budget` will materialise a 4000-token context bundle, not 950. This is a footgun: the briefing silently becomes 4× larger than spec.

**Context Compression (§4.11) is spec'd but not implemented.** Graph-Markdown notation (`→`, `±0.8`) would deliver 3× token density. Until it ships, each new connector ingestion path increases briefing token pressure. Treat Context Compression as a blocking requirement for Phase 2 (inbox) and Phase 4 (SharePoint).

---

### B5. Domain Agents: Pre-Compute Workers, Silent Failure Gap

EAR-3's four agents (CapitalAgent, LogisticsAgent, ReadinessAgent, TribeAgent) are correctly designed as non-agentic pre-compute workers. Cron-scheduled, stateless, write state files. No ReAct loops. This is appropriate.

**Unaddressed gap:** If a cron agent fails silently — exception swallowed, state file not updated — the briefing surfaces stale data with no indicator. The `state_registry.yaml` tracks agent state, but there is no `last_run_at` freshness check during briefing assembly.

**Minimal mitigation:** Each agent should write a `last_run_at` timestamp to its state file. Briefing assembly should surface a staleness warning if `last_run_at` exceeds 2× the cron interval. This requires no new infrastructure — it's a three-line check in each agent's output writer.

---

### B6. Dedup Gate Timing: ADR-3 Is Correct; SQLite Is Inventory, Not Gate

The spec acknowledges this (R13) and ADR-3 resolves it correctly: the dedup gate lives in connector-level YAML state, not in the SQLite `documents` table. The `documents` table is an inventory (what was processed) not a gate (whether to process). This is the right separation.

**Confirmation from code:** `_save_connector_state_atomic()` per-page atomic saves (§10.12) is the correct pattern. URL normalization to `{driveId}:{itemId}` is the correct deduplification key.

No architectural concern here. The spec has this right.

---

## Phase C — Guardrails and Fallbacks

### C1. NullKnowledgeGraph: Excellent Pattern

`NullKnowledgeGraph` with safe empty returns for all reading methods and no-ops for all writes is textbook graceful degradation. The `get_kb()` factory always returns a valid handler. The briefing continues without KB context when the DB is unavailable. This is correctly implemented and should be replicated wherever similar resilience is needed.

One observation: `NullKnowledgeGraph.backup()` logs a warning but otherwise silently succeeds. This is correct — a backup no-op should not crash a degraded system.

---

### C2. MCP Tool Security: Better Than the Summary Suggested

Reading `mcp_server.py` directly reveals security controls that are **already implemented** and not merely specced:

- **Rate limiting:** Token-bucket implementation (`_RateLimiter`) at 30 read / 10 write calls per 60 seconds. Thread-safe. Correctly applied before audit logging.
- **Write gate:** `_require_approval(approved: bool)` on all write tools (`artha_write_state`, `artha_send_email`, `artha_todo_sync`). Returns error JSON if `approved=False`.
- **Audit logging:** PII-scrubbed audit trail to `state/audit.md`. Sensitive key detection uses both substring (`"key"`, `"token"`, `"secret"`) and exact-field (`"to"`, `"email"`, `"phone"`) matching. This is thoughtfully designed.
- **Path validation:** Handler path validation (only `connectors.*` modules accepted in `artha_fetch_data`).
- **Domain validation:** `artha_write_state` only accepts known `state/` domains.
- **FTS5 injection defense:** `search()` strips non-alphanumeric characters before passing to FTS5 MATCH. This is the correct defense against FTS5 query injection (e.g., `UNION SELECT`-style FTS5 function abuse).

**Corrections to prior analysis:** Rate limiting IS implemented. This is a well-secured server surface.

---

### C3. FTS5 Lifecycle Blindspot: Pre-emptive Fix Required

This is a **future bug guaranteed to materialize the moment Phase 0 ships** `lifecycle_stage`.

The FTS5 triggers (`trg_entity_fts_insert`, `trg_entity_fts_update`) fire on every entity write with no `lifecycle_stage` filter. Cancelled and archived entities will be fully indexed and will appear in FTS5 `kb_search` results. The `lifecycle_stage` column is not projected into the `kb_search` virtual table, so FTS5 callers cannot filter on it after the fact — they would need to JOIN back to `entities`.

The `search()` method today returns `SearchResult` objects with no lifecycle indicator. The caller has no way to know whether a result is active, cancelled, or superseded.

**Required fix before Phase 0 ships:** Either (a) project `lifecycle_stage` into the FTS5 virtual table schema and filter in `search()`, or (b) add a post-FTS5 JOIN in `search()` that filters out non-active entities. Option (b) is simpler but adds a JOIN per FTS result row.

All 7 MCP tools that use search results need this protection before lifecycle stages are populated.

---

### C4. PII Guard Placement: Verification Required

The audit trail and PII scrubbing in `mcp_server.py` is well-implemented for the MCP surface. The concern is the connector pipeline execution order:

- `entities.source_episode_id → episodes.raw_content` stores verbatim extracted text from connector output
- If PII guard fires **after** episode creation but **before** entity extraction, raw email content with PII may be stored in `episodes.raw_content`

The spec says PII guard fires before KB extraction for inbox files. Verification is needed that this ordering holds in the connector pipeline flow — specifically that `pii_guard.py` is called before `add_episode()`, not after. This cannot be confirmed from the files read. Pipeline execution order should be documented explicitly in a comment adjacent to every `add_episode()` call.

---

### C5. Missing: LLM Observability

There is no structured per-call telemetry for LLM invocations, no per-MCP-tool logging of which entity IDs were surfaced, no measurement of KB retrieval precision over time.

The "30-day second brain test" (§13 success metric) is entirely manual. There is no automated measurement of whether KB retrieval quality improves as the graph grows.

**Practical impact today:** When the confidence model is fixed (A2 above) and starts returning different entities based on source_type, there is no way to observe whether the change improved or degraded briefing quality. Without observability, every confidence tuning decision is blind.

**Minimum viable observability:** A structured append-only log line per `artha_kb_search` call, recording `{query, entity_ids_returned, top_confidence, timestamp}`. This requires ~10 lines of Python and no new dependencies. It would enable post-hoc analysis of retrieval quality.

---

### C6. Schema Migration: Correct Recovery Mechanism, One Risk

The `--force` rebuild as recovery for a failed migration is the correct fallback. The try/except per ALTER TABLE (idempotency pattern) is the right implementation approach.

**One risk:** The `_migrate_schema_if_needed()` method's else branch logs "no migration needed" for unknown versions and does nothing. If Phase 0 ships a new `_SCHEMA_VERSION = "4"` but forgets to add the `v3 → v4` case to `_migrate_schema_if_needed()`, existing databases will log "no migration needed" and silently skip the new columns. The code path that seeds `source_weights` and sets `schema_version` in `_ensure_schema()` only runs on a **new** database (when `kb_meta` is empty). Existing databases go through `_migrate_schema_if_needed()` exclusively.

This is the most likely Phase 0 implementation error. The fix: add an assertion in `_migrate_schema_if_needed()` that raises if `current_version` is unrecognized (neither the current expected version nor a known migration source).

---

### C7. Ghost Entity Negative Sync: Well-Designed

The `_negative_sync()` implementation (§4.12) has the right safeguards: force-only mode, archive not delete, history trail with `change_source_ref`. The rationale for archive-not-delete is sound — an entity present in dozens of historical episodes should not be garbage-collected just because it no longer appears in current files.

No architectural concerns here.

---

## Priority Summary

| Priority | Finding | Required Before |
|----------|---------|-----------------|
| 🔴 P0 NOW | `source_type=NULL` for all 1,890 entities — confidence model non-functional | Immediately; hotfix before Phase 1 |
| 🔴 P0 NOW | FTS5 lifecycle blindspot — archived entities will contaminate search | Before `lifecycle_stage` column is added |
| 🔴 P0 PHASE | Phases 0+1 must be atomic — no new ingestion path before stub contract | Before Phase 2 inbox or Phase 4 SharePoint |
| 🟠 HIGH | Context Compression must precede SharePoint/inbox volume increase | Blocking requirement for Phase 2 |
| 🟠 HIGH | Verify PII guard fires before `add_episode()` in connector pipeline | Before Phase 2 inbox ingestion |
| 🟠 HIGH | `_migrate_schema_if_needed()` must raise on unrecognized schema version | Phase 0 implementation |
| 🟡 MEDIUM | Domain agents need `last_run_at` staleness check in briefing assembly | Before EAR-3 Phase 2 |
| 🟡 MEDIUM | `token_budget=4000` default in `context_for()` should match spec's 950 | Before any new ingestion path adds volume |
| 🟡 MEDIUM | `community_summaries` LIKE scan must migrate to junction table | Before scale exceeds ~5K entities |
| 🟢 LOW | Minimum viable LLM observability (append log per `artha_kb_search`) | Before confidence model tuning |
| 🟢 LOW | `_assert_not_cloud_synced()` warning should become hard error in prod | Low risk, defensive hardening |

---

## What Is Working Well

These architectural decisions are correct and should not be changed:

1. **Markdown-first with SQLite-as-index** — the single best architectural decision in the system. It makes the KB portable, human-readable, and git-diffable.
2. **NullKnowledgeGraph graceful degradation** — every MCP tool works without the KB. The briefing never hard-fails.
3. **`pipeline.py` / `KnowledgeGraph` isolation** — the correct separation of streaming ETL from durable state.
4. **`_require_approval` + rate limiting + audit** — the write surface is well-secured.
5. **FTS5 query sanitization** — injection defense is present and correctly positioned.
6. **ADR-3 connector-level dedup gate** — dedup in YAML state, inventory in SQLite. Correct architecture.
7. **`_MAX_ENTITIES_PER_FILE=100` guard** — prevents single-file extraction runaway.
8. **Domain-aware quality weights (`_DQ_DOMAIN_WEIGHTS`)** — calendar/comms get freshness-dominated scoring, decisions get accuracy-dominated scoring. This is correct and uncommon to see at this level of design.
9. **Ghost entity archive-not-delete** — right call. History is a first-class citizen.
10. **Phase-gated implementation plan with explicit dependency graph** — rare and valuable in a personal project.

---

## Closing Assessment

Artha's architecture is coherent, the spec is honest about its own gaps, and the implementation constraints (zero new infra, markdown SoT, human-gated) are the right constraints for a personal intelligence system. The phased plan will close the spec/implementation gap if executed in order.

The two items that need immediate action — populating `source_type` in `kb_bootstrap.py` and adding `lifecycle_stage` filtering to FTS5 search before the column lands — are both bounded, mechanical fixes. Neither requires architectural rethinking. Everything else in this review is improvement work that can wait for its designated phase.

The system is trustworthy enough to use for daily work today, with the understanding that confidence values in KB responses are placeholder values until Phase 0 ships.
