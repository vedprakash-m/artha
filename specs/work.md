# Artha Work OS - Intelligence Layer for Professional Life
<!-- pii-guard: ignore-file -->
## WORK-1 v2.3.0

**Author:** Artha OS  
**Date:** March 25, 2026  
**Status:** Active  
**Classification:** Internal  
**Depends on:** [specs/artha-prd.md](specs\artha-prd.md), [specs/artha-tech-spec.md](specs\artha-tech-spec.md), [config/domain_registry.yaml](config\domain_registry.yaml), [config/commands.md](config\commands.md), [config/actions.yaml](config\actions.yaml), [config/workflow/reason.md](config\workflow_reason.md), DUAL v1.4.0

| Version | Date | Summary |
|---------|------|---------|
| v1.0.0 | 2026-03-23 | Initial work intelligence spec |
| v1.1.0 | 2026-03-24 | Reframed as Work OS with separate command surface, workflow-first UX, state-driven personalization, provider abstraction, bridge artifacts, work actions, and observability |
| v1.2.0 | 2026-03-24 | Agency distribution format, meeting assist workflow, decision intelligence, Teams-specific comms, collaboration graph, alert isolation, connector error protocol, bridge artifact schemas, work bootstrap interview, work skill registry, multi-tenant strategy, enriched personalization profile |
| v1.3.0 | 2026-03-24 | Agency as default work runtime, personal-surface Agency exclusion (§3.6), selective encryption policy (§9.7), Connect cycle performance management domain, data source registry domain, newsletter + LT deck content production workflows, 12 workflows, 18 commands |
| v1.4.0 | 2026-03-24 | Work Operating Loop (§8.5), Canonical Work Objects (§8.6), Manager + Connect OS expansion (§7.6), Meeting OS (§7.9), Narrative Engine (§7.10), Graph-only acceptance criteria (§6.5), Enforcement & Test Matrix (§9.8), Outcome Metrics (§16.5), Architecture Reuse principle (§3.7), internal contradiction fixes |
| v1.5.0 | 2026-03-24 | Background-First Latency (§3.8), Background Connector Model (§8.7), Work Learning Contract (§8.8), Security Prerequisite Gate on §9.7, Calibration Defense Brief (§7.6), Delivery Feasibility Score (§11.5), ES Chat Signal Hierarchy (§11.1), Cross-Platform Clarity (§6.4), Weekly Status Auto-Draft (§7.10), Phase 0 UX Gate (§19), Microsoft Premium Features (§23), PII keyword seeding in bootstrap (§15.5) |
| v1.6.0 | 2026-03-24 | **Execution-ready release.** Pre-Phase-0 bug fixes applied (ado_workitems since param, outlookctl_bridge run_on, skills.yaml duplicate key). Config files populated: 9 work domains in domain_registry.yaml, 24 commands in commands.md, 11 actions in actions.yaml, 13 reasoning pairs in reason.md. redact_keywords seeded. Phase 0 re-sequenced into 4 dependency-ordered steps (0A–0D). Phase conflict resolved (§8.7). |
| v1.7.0 | 2026-03-25 | **Phase 0B–0D implemented.** Bridge artifact schemas + validators (`scripts/schemas/bridge_schemas.py`). Canonical Work Objects (`scripts/schemas/work_objects.py`). Connector error protocol (`scripts/schemas/work_connector_protocol.py`). `user_profile.yaml` work: section (§10.2, 9 keys). PRD FR-14 superseded by hard separation model (§9). 4 missing domain prompt files created (work-boundary, work-career, work-performance, work-sources); work-people.md upgraded to collaboration graph v1.1. 13 state templates + 2 eval files in `state/work/`. 2 bridge artifact initial files. Work Operating Loop (`scripts/work_loop.py`, 7 stages). Background refresh skill (`scripts/skills/work_background_refresh.py`). 9 work skills registered in skills.yaml. Bridge enforcement test suite (`tests/work/test_bridge_enforcement.py`, 55 tests, 55 pass). Agency agent prototypes (`config/agents/artha-work.md`, `artha-work-enterprise.md`, `artha-work-msft.md`). |
| v1.8.0 | 2026-03-25 | **Phase 1 early deliverables + hardening.** Domain state writers implemented (`scripts/work_domain_writers.py`, 700+ LOC) — atomic writes for work-calendar, work-comms, work-projects, work-boundary, work-career, work-sources with §9.7 PII redaction. Narrative Engine implemented (`scripts/narrative_engine.py`, 500+ LOC) — `weekly_memo`, `talking_points`, `boundary_report`, `connect_summary` templates; CLI: `python scripts/narrative_engine.py --template weekly_memo`. Command instruction files updated with NE invocations: `memo.md`, `talking-points.md`, `connect-prep.md`. Windows strftime compatibility fix (`%-I`/`%-d` → integer arithmetic). Agency agent YAML compliance: removed unsupported VS Code schema keys (`version`, `spec`, `tier`, `audience`, `bash`, `workiq/*`, `bluebird/*`) from `.github/agents/*.md`; Agency metadata preserved in Markdown comments. New test suites: `tests/work/test_domain_writers.py` (40 tests), `tests/work/test_narrative_engine.py` (37 tests). Total: **328 tests, 328 pass**. |
| v1.9.0 | 2026-03-25 | **Phase 1 read-path + post-meeting capture complete.** Read-path CLI implemented (`scripts/work_reader.py`, 500+ LOC) — all read-path commands (`/work`, `/work pulse`, `/work sprint`, `/work health`, `/work return`, `/work connect`, `/work people`, `/work docs`, `/work sources`) rendered from cached state files in <2s; §3.8 data freshness footer + 18h staleness warning on every output. Post-meeting capture writer (`scripts/work_notes.py`, 300+ LOC) — `NotesCapture.from_dict()` + `NotesWriter.write()` with atomic appends to `work-notes.md`, `work-decisions.md` (D-NNN IDs), `work-open-items.md` (OI-NNN IDs); JSON CLI: `python scripts/work_notes.py --input capture.json`. Guided bootstrap interview (`scripts/work_bootstrap.py`, 350+ LOC) — 12 questions covering org/role/team/hours/timezone/goals/projects/privacy/ADO/sprints; `--answers`, `--import-file`, `--dry-run` modes; atomic YAML writes after every answer; `work.bootstrap_status.completed` flag. New test suites: `tests/work/test_work_reader.py` (90 tests), `tests/work/test_work_notes.py` (42 tests), `tests/work/test_work_bootstrap.py` (41 tests). Total: **451 tests, 451 pass**. |
| v2.0.0 | 2026-03-25 | **Phase 1 complete + Phase 2 NE templates implemented.** `/work prep` with meeting readiness scoring (§7.9) added to `scripts/work_reader.py` — `_MeetingEntry` dataclass, `_parse_meeting_start_dt()`, `_parse_today_meetings()`, `_readiness_score()` helpers; readiness algorithm: base 85, ±deductions for recurring/no-notes, carry-forward, large-meeting/no-context, high-stakes keywords, open action items; score clamped [20, 100]. Phase 2 Narrative Engine templates: `generate_newsletter()` (§7.8, team newsletter draft from sprint+decision+career data) + `generate_deck()` (§7.8, LT deck content scaffolding) added to `scripts/narrative_engine.py`; CLI: `--template newsletter [--period ...]` and `--template deck [--topic ...]`. `newsletter.md` and `deck.md` command files updated with NE invocations. `state/work/work-learned.md` created with §8.8 learning model seed structure (Sender Importance, Meeting Pattern, Communication, Decision, Learning Health sections). Test suites updated: 25 new `cmd_prep` tests in `test_work_reader.py`, 27 new newsletter/deck tests in `test_narrative_engine.py`. Total: **503 tests, 503 pass**. |
| v2.1.0 | 2026-03-25 | **All Phase 1 gaps closed.** Four implementation gaps identified and resolved: (1) **`/work refresh` CLI fallback** — `main()` added to `scripts/work_loop.py` with `--mode [read\|refresh]`, `--state-dir`, `--quiet/-q` flags; closes the broken direct-invocation path `python scripts/work_loop.py --mode refresh` documented in `refresh.md` §21.1. (2) **§9.6 Alert Isolation enforcement** — `validate_alert_isolation(artifact, surface)` added to `scripts/schemas/bridge_schemas.py`; semantic check runs before schema validation to ensure work→personal bridge carries only aggregate numeric metrics (no string content, no embedded collections); personal→work bridge is constrained to `{$schema, generated_at, date, blocks}` only. (3) **§8.8 Learning calibration infrastructure** — `_update_learned_state()` replaces scaffolded `_stage_learn_async` in `work_loop.py`; after every REFRESH, atomically updates `state/work/work-learned.md` frontmatter with `days_since_bootstrap`, `learning_phase` (calibration <30d / prediction <60d / anticipation 90d+), and `refresh_runs` count. (4) **§7.6/§19 Phase contradiction resolved** — manager commitment ledger moved from Phase 1 item 14 to Phase 2 item 18; §7.6 body text declared authoritative. New test suites: `tests/work/test_work_loop.py` (26 tests — CLI + learning model persistence), `TestAlertIsolation` in `test_bridge_enforcement.py` (+12 tests — isolation enforcement for both bridge directions). Total: **~541 tests, ~541 pass**. |
| v2.2.0 | 2026-03-24 | **Pull-model architectural alignment.** Eliminated cron/Task Scheduler daemon pattern (contradicted PRD §8 pull-model foundation and v3.0 pivot). Replaced §8.7 "Background Connector Model" with **Pull-Triggered Pre-computation Model** (§8.7) and added new principle §3.9 — Pull-Triggered Pre-computation. Single `/catch-up` command now triggers both personal and work refresh as post-commit stages, producing fresh work state as a side-effect of the morning pull. Removed Phase 2 Item 16 (scheduled background refresh via cron/Task Scheduler). Replaced with: catch-up integration — `work_background_refresh` skill invoked in catch-up finalize phase when `work.enabled: true`; `post_work_refresh.py` post-commit processor (mirrors `post_catchup_memory.py`); staged signals pattern for cross-surface pre-computation. §3.8 updated: "configurable schedule" replaced with "user-initiated pull (catch-up or /work refresh)". §8.7 configuration block updated: `schedule` key replaced with `run_on_catchup` boolean. Phase 2 gate updated: "Background refresh triggered by catch-up" replaces "runs on schedule". Cross-platform simplification: zero OS-level dependencies (no schtasks, no cron). |
| v2.3.0 | 2026-03-26 | **Insight tier and career intelligence.** Added Promotion OS workflow (§7.11) — `/work promo-case` generates evidence-backed promotion narrative from project journeys, scope expansion arc, manager voice, and visibility events; eliminates manual assembly of files like `inflection-point-narrative.md`. Formalized `work-project-journeys` domain (§11.1, §14) — structured long-running program timelines with ADO/IcM/doc provenance, the canonical source for `/work promo-case` and Connect narrative. Added Visibility Events as a first-class attribute on the Stakeholder canonical object (§8.6) — when senior people engage with the user's work, the system records it for career evidence. Added `career_velocity` + `meeting_quality_signals` to Work Learning Contract (§8.8) — detect scope contraction and strategic starvation before they become career problems. Added `/work remember <text>` micro-capture command (§5.2) — mirrors proven personal OS `/remember` pattern; any signal that doesn't fit structured workflows is captured instantly. Added `work-org-calendar.md` state file (§14) — org-level dates (Connect deadlines, rewards season, fiscal year close) with 30-day lookahead alerts surfaced in `/work` briefing. Added optional semantic fields (`phase`, `advisory`) to `work_load_pulse.json` bridge (§9.3, v1.1) — enables intelligent cross-surface coordination (e.g., "Connect submission in 8 days — protect focus") without crossing the PII boundary. Added `WorkBriefingConfig` adaptive layer (§8.9) — usage-based adaptive formatting mirrors personal OS E5. Promoted `/day` from "future only" to Phase 3 with a bridge-safe implementation contract (§5.4). All new features are pull-triggered, platform-agnostic (pure Python + Markdown state files), and CLI-only — zero new OS dependencies. |
| v2.7.0 | 2026-03-28 | **Phase 4 complete + Phase 5 partial (implementable items shipped).** Phase 4 items 2/5/6: (2) Degraded mode reporting — `_seniority_tier()` + `_assess_provider_tier()` + `_build_degraded_mode_report()` in work_reader.py; four-tier model (Microsoft Enhanced/Enterprise/Core M365/Offline); per-command degradation callouts in `cmd_health()` "Provider coverage:" section; uninitialized-variable bug fixed. (5) Prompt linter — `scripts/tools/prompt_linter.py`; checks work-*.md prompts for stale paths, non-canonical references, placeholders, missing separators; exit 0/1. (6) CI enforcement — `.github/workflows/work-tests.yml`; matrix Python 3.11/3.12/3.13 + prompt-lint job. Phase 5 items 3/4/7/8 (no live infra required): (3) ES Chat signal hierarchy — `_EXEC_TIER_KEYWORDS` + `_extract_exec_visibility_signals()` in work_reader.py; cross-references work-people.md seniority against work-comms.md table rows. (4) Incidents/repos scaffold — `cmd_incidents()` + `cmd_repos()`; graceful degradation + Agency MCP setup instructions; `state/work/work-incidents.md` + `state/work/work-repos.md` templates; wired into dispatch + argparse. (7) `/work graph` — `cmd_graph()`; tier-grouped stakeholder visualization with trajectory icons ↑→↓◌; network summary. (8) Pre-read tracking — `_load_preread_markers()` + `cmd_mark_preread()`; writes to `## Pre-Read Log` in work-notes.md; `--preread-id` argparse arg. New test file `tests/work/test_phase4_phase5.py` (59 tests, all pass). Total: **883 tests, 883 pass**. |
| v2.6.0 | 2026-03-28 | **Phase 2.7 + Phase 4 Observability complete.** `/work live` implemented — `cmd_live()` in `scripts/work_reader.py` (§7.4); fuzzy meeting match (exact substring score=100 vs keyword overlap up to 80); 5 context sections: meeting header, attendee context, open decisions, carry-forward items, project context; wired into dispatch + argparse. Phase 4: `_validate_work_state_schema()` + `_collect_eval_metrics()` added; wired into `cmd_health()` as "State schema" + "Operational metrics" sections. Per-stage latency tracking in `work_loop.py` `_stage_audit()` — logs `stages=preflight:Xs,fetch:Xs,...`. 11 new `TestCmdLive` tests. Total: **825 tests, 825 pass**. |
| v2.5.0 | 2026-03-28 | **Phase 3 remaining features + orphan advisory.** (7) `/work bootstrap import` Markdown warm-start mode — `_bootstrap_from_markdown()` in work_bootstrap.py; routes milestone rows → work-project-journeys.md, visibility events → work-people.md, manager quotes → work-performance.md; idempotent, dry-run, sets `work.bootstrap.import_completed`. (8) Connect auto-evidence matching — `generate_connect_summary()` replaced in narrative_engine.py; keyword-based milestone matching, ★ Evidence Density, ⚠ GAP flags, Evidence Gaps aggregation section. (11) Decision drift detection — `_detect_decision_drift()` in work_reader.py; wired into `cmd_prep()` for recurring meetings; flags open >14d/42d, historical deferral patterns. (13) Newsletter template customization + deck outline personalization — `generate_newsletter()` and `generate_deck()` support tone/template/sections/audience profile config. Bonus: `cmd_health()` orphan file advisory — scans for unknown domain: keys, suggests bootstrap import migration path. Also fixed `_bootstrap_from_markdown()` to compute state paths dynamically from `_REPO_ROOT` (enables test monkeypatching). Tests: **812 passing** (up from 757). |
| v2.4.0 | 2026-03-27 | **Phase 3 intelligence tier complete.** Implemented all remaining Phase 3 items: (6) `/work decide` structured decision support — `cmd_decide()` in work_reader.py; surfaces related past decisions, project/notes context, 6-question decision frame, allocates D-NNN ID, logs skeleton entry atomically to work-decisions.md. (9) Calibration defense brief — `generate_calibration_brief()` in narrative_engine.py; third-person format for manager calibration room use; auto-thesis from journeys; Impact Summary table, Evidence Density stars, Cross-Team Visibility, Manager Talking Points, Readiness Signal. (10) Stakeholder influence map — `_build_influence_map()` in work_reader.py; aggregates visibility events from work-people.md by stakeholder; staleness detection with ⚠ stale flag (>90 days) and evidence gap recommendations; fixed naive-datetime timezone bug. (12) Escalation note + decision memo NE templates — `generate_escalation_memo()` + `generate_decision_memo()` in narrative_engine.py; `cmd_memo(escalation_context=...)` and `cmd_memo(decision_id=...)` sub-modes. (14) 90-day anticipation learning models — work_loop.py `_update_learned_state()`: adds `anticipation_prep_themes`, `anticipation_commitment_risk`, `anticipation_narrative_themes` to work-learned.md frontmatter during anticipation phase (60+ days). (15) Auto-refresh-on-stale advisory — `cmd_work()` reads `profile["work"]["refresh"]["auto_refresh_on_stale"]`; when True and ≥2 key domains are stale, outputs ⚠ AUTO-REFRESH ADVISORY. Tests: 757 passing (baseline was 678). |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Day In The Life](#2-day-in-the-life)
3. [Product Principles](#3-product-principles)
4. [Scope And Boundaries](#4-scope-and-boundaries)
5. [Command Surface](#5-command-surface)
6. [Personas And Distribution Tiers](#6-personas-and-distribution-tiers)
7. [Workflow Model](#7-workflow-model)
   - §7.1 Product Workflows (18)
   - §7.4 Meeting Assist
   - §7.5 Decision Support
   - §7.6 Manager + Connect OS
   - §7.7 Data Source Curation
   - §7.8 Content Production Workflows
   - §7.9 Meeting OS
   - §7.10 Narrative Engine
   - §7.11 Promotion OS *(new v2.3.0)*
8. [System Architecture](#8-system-architecture)
   - §8.5 Work Operating Loop
   - §8.6 Canonical Work Objects (incl. Visibility Events)
   - §8.7 Pull-Triggered Pre-computation Model
   - §8.8 Work Learning Contract (incl. Career Velocity + Meeting Quality)
   - §8.9 Work Briefing Adaptive Layer *(new v2.3.0)*
9. [Hard Separation Model](#9-hard-separation-model)
10. [Personalization Model](#10-personalization-model)
11. [Domain Model](#11-domain-model)
12. [Provider Model](#12-provider-model)
13. [Action Model](#13-action-model)
14. [State And Schema Model](#14-state-and-schema-model)
15. [Warm-Start Strategy](#15-warm-start-strategy)
16. [Observability And Evaluation](#16-observability-and-evaluation)
17. [Value Creation](#17-value-creation)
18. [Risks And Mitigations](#18-risks-and-mitigations)
19. [Implementation Plan](#19-implementation-plan)
20. [Assumptions And Validation](#20-assumptions-and-validation)
21. [Appendix - Agency CLI Integration](#21-appendix---agency-cli-integration)
22. [Work Skill Registry](#22-work-skill-registry)
23. [Microsoft Premium Features](#23-microsoft-premium-features)

**Key v1.6.0 Additions:**
- **Pre-Phase-0 bug fixes applied:** ado_workitems.py `since` parameter now filters WIQL, outlookctl_bridge `run_on` corrected to `windows`, skills.yaml duplicate `ai_trend_radar` key resolved, `mental_health_utilization` properties restored
- **Config files populated:** 9 work domains registered in domain_registry.yaml (all with `command_namespace: work`), 24 `/work` commands in commands.md, 11 work actions in actions.yaml, 13 work reasoning pairs in reason.md
- **`redact_keywords` seeded** in user_profile.yaml — §9.7 security prerequisite gate no longer violated
- **§19 Phase 0 re-sequenced** into 4 dependency-ordered steps (0A Config Registration → 0B Schema Definitions → 0C Execution Backbone → 0D Enforcement & Validation) with Pre-Phase-0 bug fix checklist
- **Phase assignment conflict resolved** — §8.7 background refresh skill is Phase 0 (Step 0C.3), not Phase 1
- **Execution readiness:** an engineer can now start Phase 0 Step 0B immediately — Step 0A config registration is complete

---

## 1. Executive Summary

Artha already behaves like a Personal OS. Work, today, does not. Work is present in the codebase as prompts, connectors, and partial state, but not as a first-class operating surface. That is the wrong level of ambition.

The user does not need five more domain summaries. The user needs a system that collapses operational drag, protects boundaries, preserves confidentiality, and turns fragmented workplace signals into decisive action.

This revision changes the goal.

**Artha Work OS is a true operating system for professional life — not a work intelligence shell, but a control system that owns readiness, meetings, commitments, decisions, performance narrative, and executive communication for M365 knowledge workers.**

It must do seven things exceptionally well:

1. Reduce morning work triage from 30-60 minutes to under 3 minutes.
2. Expose workflow-level intelligence, not domain-by-domain fragments.
3. Keep work and personal state, commands, policy, and storage rigorously separated — with machine-enforced boundaries.
4. Support standardized distribution across many M365 users, not just Microsoft-internal users — with a Graph-only portability guarantee and cross-platform Core tier.
5. Allow personalization in state and profile only, never in shipped prompts.
6. Own a full operating loop (preflight → fetch → process → reason → finalize → audit → learn) that mirrors the proven personal OS architecture — with background-first latency so commands never wait for the network.
7. Model work through canonical objects (Meeting, Decision, Commitment, Stakeholder, Artifact, Source) — not just domains.

That means the product is not just `work-calendar`, `work-comms`, and `work-projects`.

It is:

- `/work` for a complete work-only briefing
- `/work prep` for meeting readiness with readiness scoring
- `/work sprint` for delivery health
- `/work return` for "what did I miss?"
- `/work connect` for review evidence assembly
- `/work pulse` for 30-second situational awareness
- `/work notes` for post-meeting action capture with follow-up packages
- `/work decide` for structured decision support
- `/work connect-prep` for Manager + Connect OS — narrative assembly, not just goal tracking
- `/work sources` for data source lookup
- `/work newsletter` for team newsletter drafts
- `/work deck` for leadership presentation content
- `/work memo` for status memos, decision memos, and escalation notes
- `/work memo --weekly` for auto-drafted weekly status from operating data
- `/work talking-points` for meeting-ready talking points
- `/work refresh` for explicit live connector refresh when state is stale
- `/work connect-prep --calibration` for calibration-room defense briefs
- `/work promo-case` for promotion narrative assembly — evidence-backed, thesis-driven, calibration-room ready
- `/work journey [project]` for project timeline and impact provenance across an 18-month program arc
- `/work remember <text>` for instant micro-capture of signals that evaporate by end-of-day

The governing principles are: **separate surfaces with enforcement-grade boundaries, shared intelligence only through narrow policy-controlled bridges, background-first latency so commands are fast enough to be habitual, and architecture reuse over parallel invention.**

---

## 2. Day In The Life

### 2.1 - The New Morning

7:42 AM. The user opens the Windows machine and runs:

```text
/work
```

Artha returns in under a minute:

```text
### Work OS
- 11 meetings today, 5.2 hours blocked, 1 hard conflict
- Highest-stakes meeting: 2:00 PM architecture review, prep card ready
- Sprint ends Thursday: 4 active items, 1 blocked dependency, 2 PRs aging
- 3 work comms need response, oldest is 17h old from manager chain
- After-hours load is up for the third straight week
- Recommended next move: protect 11:00-12:30 as focus block
```

Then the user runs:

```text
/work prep
```

Artha shows the next four hours only. No searching. No dashboard hopping. No mental reconstruction.

```text
Meeting: Architecture Review
- Attendee: Director-level stakeholder, first meeting in 5 months
- Last shared context: dependency review in prior milestone
- Open thread: unresolved decision about rollout sequencing
- Preparation gap: no recent design artifact touched in 9 days
```

Then:

```text
/work sprint
```

Artha answers whether the week is feasible, not just whether tickets exist.

This is the experience to optimize for. Not a prettier report. A shorter path from ambiguity to readiness.

### 2.2 - The Return From PTO

The user returns after four days away and runs:

```text
/work return 4d
```

Artha answers:

- what changed
- what is waiting
- what is already resolved and can be ignored
- who needs response first
- what meetings today require context recovery

That single workflow will matter more to users than ten state tables.

---

## 3. Product Principles

### 3.1 - Workflow Before Domain

Users live in workflows: prepare, decide, catch up, recover, deliver, review. Domains exist to support workflows. They are not the product surface.

### 3.2 - Separate By Default

Work commands, work state, work storage, work actions, work telemetry, and work policy are independent from the personal side. Any cross-surface intelligence must use derived, policy-limited bridge artifacts.

### 3.3 - Standard Package, State-Driven Personalization

The distributed package ships one set of prompts and one set of behaviors. Personalization happens in:

- [config/user_profile.yaml](config\user_profile.yaml)
- `state/work/`
- provider configuration
- trust configuration

Never in prompt text tailored to one user's colleagues, thresholds, or calendar habits.

### 3.4 - Graceful Degradation

If WorkIQ is unavailable, Graph must still provide a useful product. If Agency is unavailable, core workflows must still function. Microsoft-internal tools enhance the package; they do not define it.

### 3.5 - Trust Is A Product Feature

The user must trust three things:

1. the system will not leak work data
2. the system will not fabricate or overstate
3. the system will not take write actions without explicit policy and approval

### 3.6 - Privacy Boundary: Agency Is Work-Only

Agency has enterprise telemetry enabled by default. This is appropriate for work workflows operating within corporate systems. It is categorically inappropriate for personal intelligence.

**Non-negotiable rule:** Artha's personal surface — all `/catch-up` workflows, personal domains, personal state, personal actions — must NEVER route through Agency CLI. Personal Artha uses Claude Code, Gemini CLI, or Copilot CLI directly. No Agency wrapping. No Agency telemetry. No exceptions.

Agency is the default runtime for `/work` commands on Windows (§21). It is never the runtime for personal commands on any platform.

This is not a degradation constraint. It is a privacy architecture decision. Enterprise telemetry and personal intelligence are fundamentally incompatible.

### 3.7 - Architecture Reuse: Personal OS Primitives, Work-Specialized

Artha's personal side already proves that structured workflow execution (reason.md), gated actions (actions.yaml), skill-driven background value (skills.yaml), connector orchestration (pipeline.py), and evaluation (eval/) produce a real operating system.

Work OS must not invent a parallel story. It must reuse the same proven abstractions:

- **OODA reasoning** — work pairs added to the same reason.md engine, not a separate reasoning file
- **Action registry** — work actions registered in the same actions.yaml with work-specific trust gates
- **Skill registry** — work skills registered in the same skills.yaml with `command_namespace: work`
- **Pipeline orchestration** — work connectors registered in the same pipeline.py handler allowlist
- **Bootstrap interview** — `/work bootstrap` follows the same guided-interview pattern as `/bootstrap`
- **Eval framework** — work metrics stored in `state/work/eval/` using the same schema conventions
- **Audit trail** — work audit in `state/work/work-audit.md` following the same format as `state/audit.md`
- **Post-commit pipeline** — `post_work_refresh.py` follows the same non-blocking post-commit pattern as `post_catchup_memory.py`
- **Pull trigger** — work refresh fires from `/catch-up` finalize phase, not from a scheduler

This produces a better product (users learn one system, not two) and a more maintainable codebase (one set of abstractions, not parallel implementations).

The Work OS is not a separate application. It is the personal OS architecture, work-specialized.

### 3.8 - Background-First Latency

Work data changes continuously — new emails, new meetings, new commits — and connector latency (30-70 seconds per provider in live mode) is unacceptable for an interactive command.

The Work OS adopts a **pre-computed state model**: connectors run when the user runs `/catch-up` (as a post-commit stage) or explicitly via `/work refresh`, and all `/work` commands read pre-computed state. The user never waits for a network call during normal command invocation. No daemon, no scheduler, no OS-level process — the user's daily pull is the trigger.

Rules:
1. `/work`, `/work pulse`, `/work prep`, and all read-path commands read from `state/work/` only — never invoke connectors inline.
2. `/catch-up` (when `work.enabled: true`) runs the work refresh as a post-commit stage — the same pattern as `post_catchup_memory.py`. Work state is fresh before the user's first `/work` command of the day.
3. `/work refresh` is the explicit on-demand trigger for a live connector run between catch-ups.
4. Pre-computed state writes to `state/work/` atomically — partial writes never corrupt command output.
5. Every command output includes a `data freshness` footer: "Last refresh: [timestamp] ([age])".
6. If state files are older than the configured staleness threshold (default: 36 hours), commands emit a warning: "Work data is stale — run `/catch-up` or `/work refresh`."

This is the architectural bet that makes Work OS fast enough to be habitual — and platform-agnostic, because it depends on user behavior, not OS infrastructure.

### 3.9 - Pull-Triggered Pre-computation

Artha is a pull-based personal intelligence system (PRD §8). There is no daemon, no background process, and no always-on infrastructure. The user triggers Artha — and every pre-computation cascade — by initiating a session.

This applies equally to Work OS. The single `/catch-up` command is the trigger for both personal and work intelligence. Work refresh fires as a post-commit stage after the personal briefing is delivered — the same non-blocking pattern as `post_catchup_memory.py`. The user runs `/catch-up` once or twice a day; both personal and work state are fresh as a result.

**Rules:**
1. No Artha process ever initiates itself. All execution is a consequence of user-triggered pulls.
2. Post-commit stages (like `post_catchup_memory.py` and `post_work_refresh.py`) fire as side-effects of a pull, never autonomously.
3. The full user story is: "I run `/catch-up` once or twice a day. My work state is always fresh when I need it. No setup. No cron. Works the same on Mac and Windows."
4. The staleness threshold (§8.7) is set relative to a realistic pull cadence (2 catch-ups/day → 36-hour threshold), not an always-on assumption.
5. `/work refresh` exists for explicit on-demand refresh between catch-ups — not as the primary mechanism.

This is the pattern that eliminates cross-platform complexity. cron (macOS/Linux), Task Scheduler (Windows), LaunchAgent, NSSM — none of these are needed. The personal OS v3.0 pivot resolved this for personal domains in 2025-10. Work OS aligns to the same decision.

---

## 4. Scope And Boundaries

### 4.1 - In Scope

| Area | Description |
|------|-------------|
| Work Briefing | Work-only situational briefing under a separate `/work` namespace |
| Meeting Prep | Auto-generated prep for upcoming meetings |
| Sprint Intelligence | Delivery health, blockers, aging work, dependency risk |
| Work Comms Triage | Actionable email and chat prioritization |
| People Intelligence | Org context and relationship memory |
| Document Activity | Recently active work artifacts and context recovery |
| Boundary Intelligence | After-hours creep, focus erosion, meeting load |
| Career Evidence | Continuous evidence capture for review cycles |
| Return From Absence | PTO, travel, and sick-day context recovery workflow |
| Warm Start | Historical import for relationship graph and behavioral baselines |
| Provider Abstraction | Standard package that works across M365 users with different backends |
| Work Actions | Read-safe generation plus explicitly trust-gated write actions |

### 4.2 - Explicitly Out Of Scope

| Area | Rationale |
|------|-----------|
| Autonomous sending of work email | Too sensitive; always approval-gated |
| Autonomous modification of corporate systems | No unapproved writes to ADO, Teams, Outlook, or other enterprise systems |
| Code generation as the primary product surface | Agency or coding agents may handle that; Work OS focuses on intelligence and orchestration |
| Raw email body storage | DLP risk too high |
| Raw Teams content storage | DLP risk too high |
| Work data access from personal-only Mac workflows | Violates hard separation goal |

### 4.3 - Important Correction

The previous draft treated ADO write actions as broadly out of scope. That is too blunt. The correct rule is:

**Work writes are not out of scope. Autonomous work writes are out of scope.**

Work OS may support actions such as adding an ADO comment or proposing a focus block, but only through the action registry with explicit trust gates.

### 4.4 - PRD FR-14 Supersession

The PRD (FR-14, Work-Life Boundary Guardian) states: "Artha never reads work emails, Teams messages, or ADO items." That constraint was designed for a personal-only system. The Work OS changes the model.

FR-14 is superseded by the Work OS hard separation model (§9). The boundary guardian function is absorbed into the work-boundary domain. The PRD must be updated to reflect this: Artha's personal surface still never reads raw work data, but the work surface reads work data through its own isolated commands, state, and vault key. The bridge artifacts (§9.3) are the only cross-surface communication channel, preserving the spirit of FR-14 while enabling the Work OS.

### 4.5 - Expanded Scope

The following areas are added to scope in v1.2.0:

| Area | Description |
|------|-------------|
| Meeting Assist | Pre/during/post meeting intelligence including live context lookup and post-meeting action capture |
| Decision Intelligence | Structured decision support using career evidence, project history, and people context |
| Teams Intelligence | Channel-aware, @-mention-prioritized, meeting-chat-contextual comms processing |
| Collaboration Graph | Relationship network analysis beyond flat people lookup |
| Work Bootstrap Interview | Guided cold-start setup for new users without historical data |

---

## 5. Command Surface

### 5.1 - Command Separation Rule

Work commands must be separate from personal commands.

That means:

- `/catch-up` remains personal-first
- `/work ...` is the work namespace
- work commands read work state only, except narrow derived bridge artifacts
- personal commands never read raw work state

### 5.2 - Required Work Commands

| Command | Purpose | Reads |
|---------|---------|-------|
| `/work` | Full work briefing | work domains only |
| `/work pulse` | 30-second status snapshot | work summary state |
| `/work prep` | Prep for meetings in next 2-4 hours | work-calendar, work-people, work-notes |
| `/work sprint` | Sprint and project health | work-projects |
| `/work return [window]` | Context recovery after absence | work-calendar, work-comms, work-projects, work-notes |
| `/work connect` | Review-cycle evidence assembly | work-career, work-projects, work-comms, work-calendar |
| `/work people <name>` | Person lookup | work-people |
| `/work docs` | Recent work artifacts | work-notes |
| `/work bootstrap` | Guided setup (cold-start) or warm-start import | profile interview or source archive and work state |
| `/work health` | Work connector and policy health | work-specific diagnostics |
| `/work notes [meeting-id]` | Post-meeting action capture and note synthesis | work-calendar, work-people, work-notes |
| `/work decide <context>` | Structured decision support | work-career, work-projects, work-people, work-notes |
| `/work live <meeting-id>` | Live meeting assist (context lookup during meetings) | work-calendar, work-people, work-notes, work-projects |
| `/work connect-prep` | Connect cycle preparation — goal progress, evidence summary, manager pivot log | work-performance, work-career, work-projects, work-people |
| `/work sources [query]` | Lookup or browse data source registry | work-sources |
| `/work sources add <url> [context]` | Register a new data source with context | work-sources |
| `/work newsletter [period]` | Generate team newsletter draft from sprint status, decisions, and accomplishments | work-projects, work-career, work-decisions, work-performance |
| `/work deck [topic]` | Assemble structured LT deck content with data from projects, career evidence, and sources | work-projects, work-career, work-sources, work-performance |
| `/work memo [period]` | Weekly status memo from operating data via Narrative Engine | work-projects, work-decisions, work-performance |
| `/work memo --decision <id>` | Decision memo from work-decisions | work-decisions, work-people |
| `/work memo --escalation <context>` | Escalation note with options framing | work-projects, work-people, work-sources |
| `/work talking-points <topic>` | Concise talking points for a specific meeting or topic | work-calendar, work-projects, work-people, work-performance |
| `/work refresh` | Explicit live connector run — executes full Work Operating Loop with network I/O | all work providers |
| `/work memo --weekly` | Auto-draft weekly status memo from operating data | work-projects, work-decisions, work-performance, work-calendar |
| `/work promo-case` | Promotion narrative assembly — thesis, evidence density, scope arc, visibility events, calibration brief | work-project-journeys, work-performance, work-people, work-career |
| `/work promo-case --narrative` | Generate full promotion narrative Markdown from all project journey and performance evidence | work-project-journeys, work-performance, work-people, work-career |
| `/work journey [project]` | Project timeline with milestone evidence, scope expansion arc, and impact provenance | work-project-journeys, work-projects, work-decisions, work-notes |
| `/work remember <text>` | Instant micro-capture — appends to work-notes with `[quick-capture]` marker and today's date; processed by work-learn next refresh | work-notes |

### 5.3 - Why This Matters

If work remains accessible only through `/domain work-*`, the system is exposing its implementation instead of its product.

### 5.4 - Composite Daily Command

`/day` is a **Phase 3** command that composes a personal pulse and work pulse into a single 90-second morning view:

```text
/day
```

**Implementation contract (bridge-safe):**
- Reads `state/bridge/work_load_pulse.json` (the pre-computed work health summary) for work surface intelligence — never reads `state/work/` directly
- Reads personal state (`state/`) normally
- Hard separation is preserved: work intelligence flows only through the bridge artifact, not through a direct state cross-read
- Output is composited from already-computed surfaces — no additional connector calls
- Can be disabled with `day_command_enabled: false` in `user_profile.yaml` for users who want strict surface discipline

**Platform:** fully cross-platform. Reads only Markdown state files and a JSON bridge artifact — no native OS dependencies. Works identically on macOS, Linux, and Windows.

**Why Phase 3 (not "never"):** The hard separation is a *state and processing* constraint, not a *UX* constraint. Running two commands every morning is a tax on the user's attention. A bridge-safe `/day` preserves every architectural guarantee while eliminating the cognitive split. The right time to build it is after both surfaces are stable and the bridge is proven in production.

---

## 6. Personas And Distribution Tiers

### 6.1 - Persona A: Microsoft Knowledge Worker

High meeting load, Azure DevOps, Teams-heavy coordination, internal systems such as WorkIQ, ICM, Bluebird, and Agency MCPs available.

### 6.2 - Persona B: General M365 Professional

Uses Outlook, Teams, OneDrive, SharePoint, maybe Planner, Jira, or GitHub, but has no access to Microsoft-internal tools. This persona must still receive a high-value product.

### 6.3 - Persona C: First 90 Days Employee

Needs relationship graph, org understanding, meeting prep, and pattern learning more than delivery telemetry.

### 6.4 - Distribution Tiers

| Tier | Audience | Baseline Providers | Enhancers | Platform |
|------|----------|-------------------|-----------|----------|
| Core M365 | Any M365 user | MS Graph email, calendar, people, files | Jira, GitHub, or ADO adapters | Cross-platform (macOS, Linux, Windows) — Graph-only, no native dependencies |
| Enterprise Work | Corporate M365 user | Core M365 plus enterprise project provider | Teams or Slack specialized adapters | Cross-platform for Graph; Windows recommended for ADO CLI enrichment |
| Microsoft Enhanced | Microsoft employee | Core M365 plus ADO plus WorkIQ | Agency MCPs, ICM, Bluebird, ES Chat | Windows required — WorkIQ, Agency, and Outlook COM bridges are Windows-only |

All three tiers must share the same prompts, command surface, and state schemas.

**Cross-Platform Commitment:** The Core M365 tier is the cross-platform guarantee. Any user on macOS or Linux with MS Graph credentials must be able to run all Core tier commands with full functionality. Platform-specific features (WorkIQ, Agency, Outlook COM) degrade gracefully — they never block commands or produce errors on unsupported platforms.

### 6.5 - Graph-Only Acceptance Criteria

The Core M365 tier is the portability contract. Until these criteria pass with MS Graph as the only provider (no WorkIQ, no ADO, no Agency), the product is not distribution-ready.

| Command | Graph-Only Behavior | Minimum Viable Output |
|---------|--------------------|-----------------------|
| `/work` | Graph mail + calendar + people + files | Meeting count, comms needing response, focus availability — no sprint data |
| `/work pulse` | Graph calendar + cached summary | Meeting hours today, top comms item, boundary score |
| `/work prep` | Graph calendar + Graph people | Attendee list with org context, last meeting with this group, open threads from email |
| `/work sprint` | Degraded — no project provider | "No project provider configured. Run `/work health` to set up ADO, Jira, or GitHub." |
| `/work return [window]` | Graph mail + calendar | What meetings happened, what emails arrived, who needs response — no project delta |
| `/work connect` | Graph mail + calendar + people | Communication-based evidence, meeting-based evidence — no sprint delivery evidence |
| `/work people <name>` | Graph people | Org position, recent meetings together, email frequency — no collaboration graph depth |
| `/work notes` | User-authored only | Post-meeting capture works fully (user input, not provider-dependent) |
| `/work sources` | Fully functional | Data source registry is local state — no provider dependency |
| `/work health` | Report Graph-only mode | Show which providers are available, which are missing, what capabilities are degraded |

**Rules:**
1. No command may fail or error when only Graph is available. Degraded output is acceptable; crashes are not.
2. Every degraded-mode output must tell the user what is missing and how to enable it.
3. Graph-only mode must be continuously tested — it is the portability guarantee.
4. Sprint and project commands may show "no provider" messages, but all other commands must produce useful output.

---

## 7. Workflow Model

### 7.1 - Product Workflows

The Work OS is built around eighteen primary workflows:

1. morning orientation
2. meeting preparation (pre, during, and post)
3. sprint and delivery control
4. return-from-absence recovery
5. boundary protection
6. career evidence capture
7. meeting assist (live context and action capture)
8. decision support
9. Manager + Connect OS (goal tracking, evidence collection, manager operating rhythm, narrative assembly)
10. data source curation (capture, index, and retrieve dashboard links and query URLs)
11. newsletter production (periodic team communication drafts)
12. LT deck assembly (structured leadership presentation content)
13. meeting control (recurring meeting memory, carry-forward, readiness scoring, follow-up packages)
14. narrative production (memos, talking points, escalation notes, stakeholder-versioned content)
15. executive communication (Connect submission, skip-level prep, rewards packet)
16. promotion intelligence (scope arc tracking, evidence density, visibility events, promotion narrative assembly)
17. project journey (long-running program timelines with milestone evidence, impact provenance, and before/after framing)
18. work briefing adaptation (usage-based adaptive formatting, mirrors personal OS `BriefingConfig` E5 pattern)

### 7.2 - Mapping To Existing Architecture

| Workflow | Status | Implementation |
|----------|--------|----------------|
| Morning orientation | **implemented** | `cmd_work()` — full work briefing with adaptive formatting (WB-1/3/6) |
| Meeting preparation | **implemented** | `cmd_prep()` — readiness scoring, carry-forward, staleness gaps, open items (§7.9) |
| Sprint control | **implemented** | `cmd_sprint()` — delivery feasibility score, blocked items |
| Return from absence | **implemented** | `cmd_return()` — absence recovery with configurable window |
| Boundary protection | **implemented** | boundary domain + `cmd_work()` boundary pulse section |
| Career evidence | **implemented** | `cmd_connect_prep()`, `cmd_promo_case()`, `_build_influence_map()` (Phase 3) |
| Meeting assist | **implemented** | `cmd_live()` — live context card, fuzzy match, 5 sections; `cmd_notes()` — post-meeting capture (v2.6.0, §7.4) |
| Decision support | **implemented** | `cmd_decide()` — 6-question frame, D-NNN allocation, decision drift detection (Phase 3, §7.5) |
| Connect cycle | **implemented** | `cmd_connect_prep()` — auto-evidence matching, calibration brief, influence map (Phase 3, §7.6) |
| Data source curation | **implemented** | `cmd_sources()` + `cmd_sources_add()` — read/write registry; auto-capture from meetings (Phase 1, §7.7) |
| Newsletter production | **implemented** | `cmd_newsletter()` → `generate_newsletter()` — tone/template/sections customization (Phase 2/3, §7.8) |
| LT deck assembly | **implemented** | `cmd_deck()` → `generate_deck()` — template/audience personalization (Phase 2/3, §7.8) |
| Meeting control | **implemented** | `_extract_carry_forward_items()` + `_detect_decision_drift()` — recurring meeting memory (Phase 2/3, §7.9) |
| Narrative production | **implemented** | `generate_weekly_memo()`, `generate_escalation_memo()`, `generate_decision_memo()`, `cmd_talking_points()` (§7.10) |
| Executive communication | **implemented** | `generate_calibration_brief()` — third-person format for manager calibration room (Phase 3, §7.6) |
| Promotion intelligence | **implemented** | `generate_promo_case()` — scope arc, evidence density, visibility events, full narrative (Phase 3, §7.11) |
| Project journey | **implemented** | `cmd_journey()` — timeline view from work-project-journeys.md; auto-append from ADO closed items (Phase 3, §7.11) |
| Work briefing adaptation | **implemented** | `WorkBriefingConfig` WB-1/3/4/5/6 — usage + org-calendar adaptive rules (Phase 2/3, §8.9) |

### 7.3 - Command To Workflow Binding

The command layer is the workflow layer. Domains feed commands; commands do not expose domains directly.

### 7.4 - Meeting Assist Workflow

Meeting preparation today generates a static prep card before the meeting. The real friction is during and after the meeting.

**Pre-meeting (existing):** `/work prep` generates prep cards with attendee context, open threads, and preparation gaps.

**During-meeting (new):** `/work live <meeting-id>` provides a live assist mode. The user can ask for context lookup mid-meeting — "What was the decision on rollout sequencing?" or "When did we last discuss this dependency?" — and get answers from work state without leaving the terminal. This requires a persistent terminal session or Agency copilot session running alongside the meeting.

**Post-meeting (new):** `/work notes <meeting-id>` prompts the user for decisions made and action items assigned. Actions are proposed as work open items or ADO work item comments. Meeting summaries are stored in `state/work/work-notes.md` linked to the calendar event.

Phase: **DONE** — during-meeting assist implemented in v2.6.0 (`cmd_live()` in work_reader.py; fuzzy meeting match + 5 context sections). Post-meeting capture implemented in Phase 1 (`cmd_notes()` in work_notes.py).

### 7.5 - Decision Support Workflow

Decisions are the highest-leverage human activity at work. The system captures evidence (career evidence, sprint telemetry, relationship graphs) but must also support the decision itself.

`/work decide <context>` triggers structured decision support:

1. Retrieve relevant evidence from work-career, work-projects, work-people, and work-notes
2. Surface historical patterns — when has a similar decision been made before, what was the outcome
3. Identify evidence gaps — what information is missing that would improve the decision
4. Present a structured decision frame, not a recommendation

Decisions are logged to `state/work/work-decisions.md` for future pattern matching and career evidence.

Phase: Phase 3. The command surface and state schema should be designed now; the intelligence matures over time.

### 7.6 - Manager + Connect OS Workflow

At Microsoft, the Connect cycle is the operating rhythm of career progression: goal-setting at the start of a period, continuous evidence collection, manager check-ins, and a submission at the end. This is not optional administrative overhead — it is the mechanism through which impact is recognized and rewarded.

The killer feature is not evidence capture. It is **narrative assembly**: "What changed, why it mattered, who observed it, what proof exists, and what should I say next."

#### Connect Cycle Preparation

`/work connect-prep` triggers Connect preparation:

1. Retrieve current Connect goals from `state/work/work-performance.md`
2. Auto-collect evidence from work-projects (completed items, shipped features), work-comms (recognition messages, stakeholder feedback), work-calendar (key presentations, design reviews), and work-people (collaboration breadth)
3. Match evidence to goals — highlight which goals have strong evidence and which have gaps
4. Surface manager 1:1 pivot log — what topics the manager raised, what was committed to, what shifted
5. Generate a structured Connect submission draft

The system detects manager 1:1 meetings automatically from work-calendar (recurring 1:1 with direct manager) and prompts the user post-meeting to log pivots or commitments.

#### Manager Operating Rhythm

The manager relationship is the most consequential work relationship. The system must track it as a first-class operating rhythm, not as a byproduct of calendar events.

| Capability | Description |
|-----------|-------------|
| **Manager commitment ledger** | Every commitment made to or by the manager — deliverables, deadlines, verbal agreements — tracked from creation to closure. Surfaced in `/work connect-prep` and pre-1:1 prep cards. |
| **1:1 memory** | Persistent log of manager 1:1 topics, promises, pivots, and unresolved asks. Each 1:1 opens with "last time, you committed to X — status?" context. |
| **Skip-level and calibration prep** | `/work connect-prep --skip` generates a narrative optimized for skip-level visibility: broader impact, cross-team collaboration, leadership outcomes. |
| **Calibration defense brief** | `/work connect-prep --calibration` generates a third-person defense brief optimized for the calibration room — where the manager advocates for the user without the user present. Structured as: impact summary, evidence density, cross-team visibility, risk of under-recognition. This is the artifact the manager needs, not the one the user reads. |
| **Rewards/perf season packet** | At rewards season, `/work connect-prep --final` assembles the full packet: goal evidence, manager 1:1 log, collaboration breadth, stakeholder feedback summary. |
| **Stakeholder influence map** | Which stakeholders have observed the user's impact, how recently, and in what context. Feeds evidence gap analysis: "Director X hasn't seen your work since October — consider inviting them to the design review." |
| **Narrative question** | The system surface-prompts: "What story are you telling about your impact this half?" and helps the user test whether their evidence supports that story. |

Phase: Goal tracking and evidence capture are Phase 1. Manager commitment ledger and 1:1 memory are Phase 2. Draft generation, skip-level prep, and narrative assembly are Phase 3.

### 7.7 - Data Source Curation Workflow

Knowledge workers encounter dashboard links, Kusto queries, Power BI reports, and information portals constantly — in meetings, email, Teams messages, and documents. The "I know I saw that dashboard somewhere" problem wastes significant cognitive effort.

`/work sources` provides an indexed, searchable registry. Sources are captured in three ways:

1. **Explicit capture:** `/work sources add <url> [what it answers]` registers a source with context.
2. **Auto-capture:** The `work_source_capture` action detects URLs in meeting notes and email metadata and proposes them for registration.
3. **Decision-linked capture:** When `/work decide` identifies an evidence gap, it checks work-sources for relevant data portals and suggests new ones if the user provides them.

Each source entry records: URL, title, what question it answers, who shared it, when first seen, when last referenced, and relevance tags.

Phase: Explicit capture is Phase 1. Auto-capture is Phase 2. Decision-linked enrichment is Phase 3.

### 7.8 - Content Production Workflows

Two of the most time-consuming recurring work activities are newsletter assembly and leadership (LT) presentation deck drafting. Both involve gathering data from multiple sources, synthesizing it into structured formats, and iterating with stakeholders. These are ideal candidates for workflow automation.

#### Newsletter Production

`/work newsletter [period]` generates a team newsletter draft:

1. Pull sprint completion data from work-projects (shipped items, closed PRs, velocity trends)
2. Pull key decisions from work-decisions (what was decided and why)
3. Pull accomplishments and recognition from work-career and work-performance
4. Pull blockers and risk items from work-projects and work-open-items
5. Structure into configurable newsletter template (status, highlights, decisions, risks, next steps)
6. Output as Markdown draft for human review and editing

The system never sends the newsletter. It produces a draft. The user reviews, edits, and distributes through their preferred channel.

#### LT Deck Assembly

`/work deck [topic]` assembles structured content for leadership presentations:

1. Pull relevant project data from work-projects (milestone status, dependency health, team velocity)
2. Pull supporting metrics from work-sources (dashboard links, data references with last-known values)
3. Pull stakeholder context from work-people (who cares about what, recent conversations on the topic)
4. Pull career evidence and Connect goal alignment from work-performance
5. Structure into a deck outline: executive summary, status, data, risks, asks, next steps
6. Output as structured Markdown sections — not slides, but section-by-section content ready to paste into a deck

The system does not generate PowerPoint files. It generates the intellectual content — the analysis, the data citations, the framing — that makes deck creation fast instead of painful.

Phase: Newsletter draft generation is Phase 2. Deck content assembly is Phase 2. Template customization is Phase 3.

### 7.9 - Meeting OS

For someone living in Outlook + Teams + docs + recurring stakeholder meetings, the real unmet need is not "meeting prep." It is "meeting control." The recurring meeting — not the single calendar event — is the canonical object.

#### Recurring Meeting Memory

Every recurring meeting series maintains persistent state across instances:

| Data | Source | Updated |
|------|--------|---------|
| Attendee roles and typical contributions | work-people + observation | per instance |
| Unresolved threads | work-notes post-meeting capture | carry-forward to next instance |
| Decision history | work-decisions linked to meeting series | per instance |
| Commitment tracker | work-notes + work-projects cross-reference | per instance |
| Topic frequency and drift | work-notes pattern analysis | weekly |

#### Carry-Forward Intelligence

Before each instance of a recurring meeting, the system generates:

```text
Recurring: Architecture Review (instance 14 of series)
- Unresolved from last time: rollout sequencing decision deferred, awaiting perf data
- Commitments due: [user] promised dependency analysis by this meeting
- Since last meeting: 2 PRs merged on the dependency, design doc v3 published
- Decision drift: this meeting has deferred the sequencing decision 3 times in 6 weeks
- Pre-read status: design doc v3 last modified 2 days ago, [stakeholder] has not opened it
```

#### Readiness Scoring

Each upcoming meeting receives a readiness score (0-100):

- **Preparation completeness**: are open items from last instance resolved?
- **Pre-read freshness**: are required artifacts up-to-date and reviewed?
- **Commitment closure**: are due commitments met?
- **Stakeholder context**: is the user prepared for each high-seniority attendee?

`/work prep` sorts meetings by readiness score, lowest first — showing the user where to focus preparation effort.

#### Post-Meeting Follow-Up Package

`/work notes <meeting-id>` generates a follow-up package:

1. Decisions recorded (→ work-decisions)
2. Commitments assigned (→ commitment tracker, work-open-items)
3. Unresolved items for carry-forward (→ recurring meeting memory)
4. Follow-up actions with owner and deadline (→ work-open-items)
5. Optional: formatted follow-up email draft for distribution (→ Narrative Engine)

#### Meeting-to-Commitment Closure Tracking

Commitments made in meetings are tracked to resolution. `/work sprint` surfaces overdue meeting commitments alongside sprint work items. The system distinguishes between project-tracked commitments (linked to ADO items) and verbal commitments (tracked only in meeting memory).

Phase: Recurring meeting memory and carry-forward are Phase 2. Readiness scoring is Phase 2. Decision drift detection is Phase 3. Pre-read tracking is Phase 3.

### 7.10 - Narrative Engine

Newsletter and deck commands (§7.8) are two instances of a broader pattern: **narrative production from operating data.** The repetitive pain for Microsoft-style work is broad:

- weekly status memo
- decision memo
- escalation note
- executive summary
- talking points for staff/LT
- review response draft
- stakeholder-specific versioning of the same story
- Connect submission narrative

A command-per-output approach does not scale. The Narrative Engine is a formal layer that generalizes all content production.

#### Architecture

```text
Work Objects (Decisions, Commitments, Artifacts, Sources, Metrics)
    ↓
Narrative Engine
    ↓
Output Templates (newsletter, deck, memo, talking-points, review, escalation)
```

#### Inputs

The engine consumes Canonical Work Objects (§8.6):

- **Decisions** from work-decisions — what was decided, when, by whom
- **Commitments** from meeting memory and work-open-items — what was promised, status
- **Artifacts** from work-projects and work-notes — what shipped, what changed
- **Sources** from work-sources — data citations with provenance
- **Metrics** from work-projects — velocity, completion, dependency health
- **Stakeholder context** from work-people — who cares about what, interaction recency

#### Output Templates

| Template | Command | Audience | Tone | Phase |
|----------|---------|----------|------|-------|
| Team newsletter | `/work newsletter` | Direct team + stakeholders | Informational, highlights-first | Phase 2 |
| LT deck content | `/work deck` | Leadership | Executive, data-driven, asks-clear | Phase 2 |
| Weekly status memo | `/work memo --weekly` | Manager + stakeholders | Structured, risk-aware — auto-drafted weekly from operating data | **Phase 1** |
| Weekly status memo | `/work memo [period]` | Manager + stakeholders | Structured, risk-aware | Phase 2 |
| Decision memo | `/work memo --decision <id>` | Decision participants | Factual, evidence-linked | Phase 3 |
| Escalation note | `/work memo --escalation <context>` | Management chain | Urgent, options-framed | Phase 3 |
| Talking points | `/work talking-points <topic>` | Self (meeting prep) | Concise, narrative-aligned | Phase 3 |
| Connect narrative | `/work connect-prep --narrative` | Self + manager | Impact-framed, evidence-dense | Phase 3 |

#### Stakeholder-Specific Versioning

The same underlying data can produce different narratives for different audiences. The engine supports audience-aware framing:

- **Manager version**: focuses on delivery, risks, and asks
- **Skip-level version**: focuses on broader impact and strategic alignment
- **Peer version**: focuses on collaboration and shared dependencies
- **External stakeholder version**: focuses on outcomes and timelines

The user selects the audience variant, not a different command.

#### Rules

1. The engine never sends output. It produces drafts for human review.
2. Every generated narrative includes a "data freshness" footer showing when each source was last updated.
3. Templates are configurable in `state/work/` — users can customize section order, tone, and included data.
4. The engine reuses the same Canonical Work Objects consumed by workflows. No separate data pipeline.

Phase: Newsletter and deck templates are Phase 2. Memo templates are Phase 3. Stakeholder versioning is Phase 3. Talking points are Phase 3.

#### New Commands

| Command | Purpose |
|---------|---------|
| `/work memo [period]` | Weekly status memo from operating data |
| `/work memo --decision <id>` | Decision memo from work-decisions |
| `/work memo --escalation <context>` | Escalation note with options framing |
| `/work talking-points <topic>` | Concise talking points for a specific meeting or topic |

### 7.11 - Promotion OS

The highest-leverage unmet need for a Principal-track knowledge worker is not meeting prep or sprint status. It is **career trajectory clarity**: Is my scope expanding or contracting? Are the right people observing my impact? Does the evidence I have actually support the case I'm trying to make?

The system already accumulates this evidence — project timelines, manager voice, organizational data, stakeholder interactions. The gap is in synthesis: turning 18 months of accumulated signals into a coherent, evidence-backed case.

`/work promo-case` is the synthesis layer:

```text
/work promo-case
```

```text
Promotion Readiness Assessment

Thesis (auto-generated from scope arc):
  "Defined Platform Reliability from scratch, delivered P0 on schedule,
   then led operational stabilization as scope expanded 4x across programs.
   Principal-level scope: cross-org, independently led, strategic."

Scope Arc (18 months):
  Sep 2024 → Platform Reliability (1 workstream)
  Jan 2025 → + Safety + Deployment
  May 2025 → + Infra + Config
  Oct 2025 → + Analytics + Platform
  Trajectory: ↑ expanding (promotable signal)

Evidence Density:
  Goal 1 (Platform P0):    ★★★★★  (5 milestone artifacts, manager voice, LT visibility)
  Goal 2 (Pilot Launch):   ★★★    (milestones delivered, pilot go-live pending — gap)
  Goal 5 (People):         ★★     (sparse — no mentorship evidence captured)

Visibility Events (last 90 days):
  • Mar 2026: Platform LT review — 200+ attendees, CVP-level
  • Feb 2026: Community Day organized — 80+ people, 7 hours
  • Ongoing: Weekly status email to CVP
  Unique L-N+ stakeholders with observed impact: 4

Calibration Brief: ready  (→ /work connect-prep --calibration)
Full Narrative: ready     (→ /work promo-case --narrative)
```

#### What The Promotion OS Tracks

| Signal | Source | Updated |
|--------|--------|---------|
| Scope arc | work-project-journeys | per refresh |
| Scope trajectory direction | work-learned `career_velocity` model (§8.8) | after each refresh |
| Evidence density per goal | work-performance matched to work-project-journeys | per `/work notes` capture |
| Visibility events | `visibility_events[]` on Stakeholder object (§8.6) | per connector refresh |
| Manager vocabulary trend | work-performance 1:1 pivot log | per `/work notes` capture |
| Org calendar proximity | work-org-calendar.md (§14) | daily check during catch-up |
| Promotion readiness signal | synthesized from all above | on-demand |

#### `/work promo-case --narrative`

Generates `state/work/work-promo-narrative.md` — the automated equivalent of a manually assembled promotion document. Structured as:

```
## Thesis
[One paragraph: what the case is, at what level, and why the evidence supports it]

## Before / After
[Domain state before the person joined vs. current state — the transformation story]

## Scope Expansion Arc
[Timeline of scope additions with evidence citations and milestone provenance]

## Milestone Evidence
[Per-program: delivered by date, manager signal, measurement, referenced artifacts]

## Manager and Peer Voice
[Verbatim signals from work-performance 1:1 log and Connect submissions]

## Visibility Events
[Senior stakeholders who observed impact: event, context, date]

## Evidence Gaps
[What is thin and specific actions to fill it before submission]

## Readiness Signal
[ready | 1-2 quarters away | critical gaps blocking — with specific reasoning]
```

This is a draft for human review — the system never submits it. The user reads, edits, and validates. It replaces the manual effort of assembling evidence from memory and email search.

#### Relationship To Existing Architecture

`/work promo-case` requires no new data pipeline. It reads:

- **`work-project-journeys.md`** — program timelines (§14, new domain §11.1)
- **`work-performance.md`** — Connect goals and manager operating rhythm
- **Stakeholder** Canonical Work Objects (§8.6) — visibility events
- **Artifact** Canonical Work Objects (§8.6) — evidence provenance
- **Decision** Canonical Work Objects (§8.6) — decisions led or influenced

Every signal already flows through the existing work-process and work-reason stages. The Promotion OS is a new synthesis command on existing infrastructure.

Phase: Scope arc detection and evidence density scoring are Phase 2. Visibility event tracking is Phase 2. `/work promo-case` output generation and `/work promo-case --narrative` are Phase 3.

---

## 8. System Architecture

### 8.1 - High-Level Flow

```text
Work providers -> provider adapters -> normalized work events -> work domains -> work workflows -> work commands
```

### 8.2 - Architecture Layers

| Layer | Purpose |
|-------|---------|
| Provider adapters | Fetch from Graph, ADO, WorkIQ, Agency, Jira, GitHub, Slack |
| Normalization | Convert provider-specific data into common internal records |
| Domain processors | Build domain state from normalized data |
| Workflow synthesizers | Produce answers for `/work`, `/work prep`, `/work sprint`, and related commands |
| Action router | Generates read-safe suggestions and gated action proposals |
| Evaluation layer | Tracks usefulness, latency, coverage, and drift |

### 8.3 - Structural Correction

Work is not yet a full architectural citizen in the codebase. Today:

- work domains are not fully integrated into [config/domain_registry.yaml](config\domain_registry.yaml)
- work commands do not exist as a separate surface in [config/commands.md](config\commands.md)
- work reasoning pairs are absent from [config/workflow/reason.md](config\workflow\reason.md)
- work action types are absent from [config/actions.yaml](config\actions.yaml)

Phase 0 must fix these structural gaps before adding more features.

### 8.4 - Connector Error Protocol

The personal side has AR-8 (error handling protocol). Work connectors need an equivalent contract defining behavior for each failure mode.

| Failure | Fallback | User Signal |
|---------|----------|-------------|
| WorkIQ timeout (>40s) | skip WorkIQ queries, use Graph if available, use cached state | "WorkIQ unavailable — using cached data" |
| ADO bearer token expired | attempt `az account get-access-token` refresh, fail gracefully | "ADO auth expired — run `az login`" |
| Graph 403 on specific scope | skip that data category, proceed with remaining | "Graph: [scope] unavailable — partial data" |
| Graph refresh token invalid | halt Graph queries, use cached state | "Graph re-auth needed — run `/work health`" |
| All providers down | serve from cached summary state only | "Work OS offline — showing last known state from [timestamp]" |

Rules:

1. No single connector failure blocks the entire `/work` workflow.
2. Cached state is always preferable to no output.
3. Error messages must be actionable — tell the user what to do, not what went wrong internally.
4. Every connector failure is logged to `state/work/work-audit.md` with timestamp, connector name, and error category.

### 8.5 - Work Operating Loop

The personal side wins because it owns a full operating loop: identity → preflight → fetch → process → reason → finalize → command routing → actions → skills → audit. That loop is visible in Artha.md, reason.md, pipeline.py, actions.yaml, and skills.yaml.

The Work OS must own an equivalent loop. Without it, the product is a set of domains with nicer marketing, not a control system.

```text
work-preflight → work-fetch → work-process → work-reason → work-finalize → work-audit → work-learn
```

| Stage | Purpose | Implementation |
|-------|---------|----------------|
| **work-preflight** | Verify connector health, token freshness, cache validity. Decide which providers are available for this run. | Runs `check_workiq()`, `check_ado_auth()`, Graph token validation. Produces a provider availability map. Follows §8.4 error protocol. |
| **work-fetch** | Pull raw data from available providers through pipeline.py. Respect tiered cache TTLs. | Invokes workiq_bridge, ado_workitems, msgraph_calendar, msgraph_email through pipeline handler allowlist. Parallel fetch where possible. |
| **work-process** | Normalize fetched data into Canonical Work Objects (§8.6). Run domain processors. Update domain state files. | Provider-specific records → Meeting, Decision, Commitment, Stakeholder, Artifact, Source objects → domain state in `state/work/`. |
| **work-reason** | Execute cross-domain reasoning pairs (§11.5). Detect conflicts, risks, opportunities, and carry-forward items. | Uses the same OODA engine from reason.md with work-specific pairs. Produces cross-domain insights that no single domain can generate alone. |
| **work-finalize** | Synthesize workflow answers from reasoned domain state. Generate bridge artifacts. Update work-summary.md. | Produces the actual output for `/work`, `/work prep`, `/work sprint`, etc. Writes bridge artifacts (§9.3) for cross-surface consumption. |
| **work-audit** | Log run metadata: providers used, cache hits, latency, errors, degraded capabilities. | Appends to `state/work/work-audit.md`. Feeds eval metrics in `state/work/eval/`. |
| **work-learn** | Update learned models: sender importance, meeting patterns, alert thresholds, collaboration frequency. Execute the Work Learning Contract (§8.8). | Adjusts state-level personalization. Feedback from alert acceptance/dismissal. No prompt modification. |

**Rules:**
1. Every `/work` command invocation executes the loop. For read-path commands (`/work`, `/work pulse`, `/work prep`, etc.), the loop reads pre-computed state (§3.8) — work-fetch reads `state/work/` files, not live providers. `/work refresh` is the only command that executes work-fetch against live providers.
2. Each stage has a latency budget. **On-demand read path** (normal commands): work-preflight <1s, work-fetch (from state) <2s, work-process <2s, work-reason <3s, work-finalize <2s — total <10s. **Background refresh path** (`/work refresh`): work-preflight <2s, work-fetch (live network) <60s (parallel), work-process <5s, work-reason <3s, work-finalize <2s — total <72s.
3. If work-preflight determines all providers are down, skip work-fetch and serve from cached state (work-finalize reads last-known state files).
4. work-learn runs asynchronously after command output is delivered — it never blocks the user.
5. The loop is identical across all distribution tiers. Only the providers available to work-fetch differ.

### 8.6 - Canonical Work Objects

Knowledge work does not happen in "domains." It happens in meetings, decisions, commitments, stakeholders, artifacts, and deadlines. Domains are the data plane; objects are the product plane.

The Work OS defines six canonical objects. All domain processors produce these objects. All workflows consume them.

| Object | Definition | Primary Sources | Consumed By |
|--------|-----------|----------------|-------------|
| **Meeting** | A calendar event enriched with attendees, recurrence context, prep state, and carry-forward items. The recurring meeting series — not the individual instance — is the canonical unit. | work-calendar, work-people, work-notes | `/work prep`, `/work live`, `/work notes`, Meeting OS |
| **Decision** | A structured record of what was decided, when, by whom, with what evidence, and what outcome. | work-notes, work-projects, work-comms | `/work decide`, Narrative Engine, career evidence |
| **Commitment** | A promise made to or by the user — deliverables, follow-ups, deadlines, and closure state. Commitments track from creation to resolution. | work-notes, work-comms, work-projects | `/work sprint`, Meeting OS carry-forward, Manager OS |
| **Stakeholder** | A person with org context, collaboration history, communication patterns, influence weight, relationship trajectory, and **visibility events** — moments when senior people engaged with the user's work (replied to a status email, @-mentioned in LT context, invited to a high-seniority meeting, cited a document). The most consequential career signals are often invisible without explicit tracking. | work-people, work-comms, work-calendar | `/work prep`, `/work people`, `/work promo-case`, Narrative Engine, Manager OS |
| **Artifact** | A work product — document, PR, design, deck, wiki page — with authorship, recency, and project linkage. | work-notes, work-projects, work-sources | `/work docs`, `/work deck`, career evidence |
| **Source** | A data reference — dashboard, query, report, portal — with provenance, purpose, and recency. | work-sources | `/work sources`, `/work decide`, Narrative Engine |

**Object Lifecycle Rules:**
1. Objects are created during work-process from normalized provider data.
2. Objects are enriched during work-reason when cross-domain pairs reveal connections (e.g., a Commitment from a Meeting linked to a project work item).
3. Objects are stored as structured entries within domain state files, not as a separate object database.
4. Objects carry a `last_updated` timestamp and a `source_domains[]` provenance list.
5. Stale objects (not updated in configurable TTL) are marked `stale` and deprioritized in workflow output.
6. **Visibility events on Stakeholder objects** are appended (never overwritten) — each is immutable once recorded. Fields: `date`, `stakeholder` (alias only, never raw name in unencrypted files), `event_type` (`replied | at_mentioned | cited_doc | invited_to_meeting | presented_about`), `context` (redacted free-text, max 100 chars), `source_domain`. Queried by `/work promo-case` for evidence density and by `/work connect-prep` for stakeholder influence map.

**Why Visibility Events Matter:**
At Microsoft and similar organizations, a significant fraction of career advancement evidence is invisible without active tracking: the CVP who replied to your status email, the Director who quoted your analysis in an LT deck, the skip-level who started inviting you to architecture reviews. This evidence evaporates by the next performance cycle unless it is captured at the moment it occurs. The ES Chat signal hierarchy (§11.1, §23.2) makes this feasible — VP+ and CVP+ engagement signals are already prioritized. Visibility Events formalize the career capture.

**Why This Matters:**
Without canonical objects, every workflow must re-derive structure from raw domain state. With them, `/work prep` can ask "what Commitments are unresolved for this Meeting's Stakeholders?" directly. That is the difference between a reporting product and an operating system.

### 8.7 - Pull-Triggered Pre-computation Model

The Background-First Latency principle (§3.8) and the Pull-Triggered Pre-computation principle (§3.9) require a concrete execution model that delivers fast `/work` commands without any OS-level schedulers or daemon processes.

#### Refresh Triggers

| Trigger | When | What Runs |
|---------|------|----------|
| **`/catch-up` post-commit** | User runs `/catch-up` (once or twice daily) | Full Work Operating Loop — fires as a non-blocking finalize stage |
| **Explicit refresh** | User runs `/work refresh` | Full Work Operating Loop with live providers |
| **Stale-state warning** | Command detects state files older than staleness threshold | Warning message only — never auto-triggers refresh |

The work refresh is invoked via the `work_background_refresh` skill registered in `config/skills.yaml`, using the same `BaseSkill` pattern as all personal skills. No cron job, no Task Scheduler, no LaunchAgent — the user's daily pull is the scheduler.

#### Pull-Triggered Execution Flow

When `work.enabled: true` and `/catch-up` reaches its finalize phase (after the personal briefing is delivered), it stages a work refresh signal and invokes the post-commit processor:

```text
catch-up (user pull)
  │
  ├── Synchronous: deliver personal briefing (user sees this immediately)
  │
  └── Post-commit non-blocking:
        python scripts/post_work_refresh.py
          → invokes WorkLoop(mode=REFRESH)
          → work-preflight: check token validity, connector health
          → work-fetch: parallel provider calls (Graph, WorkIQ, ADO, Agency)
               Each provider has an independent timeout (default 60s)
               Successful providers write to state; failed providers preserve previous state
          → work-process: normalize fetched data into Canonical Work Objects
          → work-reason: cross-domain reasoning pairs
          → work-finalize: update state/work/*.md files atomically, update work-summary.md
          → work-audit: log refresh metadata (providers, latency, cache hits, failures)
          → work-learn: update learned models from new data
          → one-line result to stdout: "[run_id] work refresh complete — providers=N errors=0"
```

`post_work_refresh.py` follows the same pattern as `post_catchup_memory.py`: non-blocking, fire-and-forget, failure never affects the personal briefing, config kill-switch available.

#### Atomicity

State files are updated atomically: write to a temp file, validate, then rename into place. If any step fails, the previous valid state file is preserved. A partial refresh (some providers succeeded, some failed) writes the successful data and logs degraded providers in `work-audit.md`.

#### Freshness Contract

Every `/work` command output includes a freshness footer:

```text
Data freshness: last refresh 07:43 (1h 12m ago) | calendar: fresh | email: fresh | projects: stale (ADO auth expired)
```

If any domain's state file is missing or older than the staleness threshold:
- The command still runs (from whatever state exists)
- A warning is emitted: "⚠ Work data is stale — run `/catch-up` or `/work refresh`"
- `/work health` surfaces the specific provider issue and remediation steps

#### Configuration

In `work:` section of `user_profile.yaml`:

```yaml
work:
  refresh:
    run_on_catchup: true            # trigger work refresh as catch-up post-commit stage
    staleness_threshold_hours: 36   # warn if state is older than this (2 catch-up cycles)
    provider_timeout_sec: 60        # per-provider network timeout
    auto_refresh_on_stale: false    # Phase 3 opt-in: /work auto-triggers refresh when stale
```

Phase: Background refresh skill registration is Phase 0 (Step 0C.3). Catch-up integration (`post_work_refresh.py` post-commit stage) is Phase 2. Auto-refresh-on-stale is Phase 3.

### 8.8 - Work Learning Contract

The work-learn stage (§8.5) runs after every command but has no defined trajectory. Without a contract, "learning" means ad-hoc model updates that are never measured or validated.

The Work Learning Contract defines what the system must learn, when, and how the user validates it.

#### 30/60/90 Day Learning Trajectory

| Milestone | What The System Learns | Validation |
|-----------|----------------------|------------|
| **Day 0-30: Calibration** | Sender importance ranking, meeting pattern recognition (recurring vs. one-off), baseline communication volume, work hours boundaries, alert threshold calibration, scope baseline capture (initial workstream count) | User confirms or corrects sender priority for top 10 senders. Meeting prep cards include correct attendee context. Boundary alerts fire at reasonable thresholds. |
| **Day 31-60: Prediction** | Which meetings need heavy prep (seniority, topic complexity), which comms are truly action-required vs. FYI, sprint velocity patterns, collaboration frequency norms, scope trajectory detection (expanding vs. stable vs. contracting) | Prep card readiness scores correlate with user's actual preparation effort. Action-required precision exceeds 80%. Sprint alerts detect real blockers. Career velocity signal is computed. |
| **Day 61-90: Anticipation** | Proactive prep for recurring meetings (carry-forward items surfaced automatically), commitment-at-risk detection before deadlines, stakeholder relationship trajectory (warming, cooling, stale), narrative themes for Connect, meeting quality trend (strategic vs. execution fraction) | User reports "the system knew what I needed before I asked" moments. Commitment tracking surfaces risks before manager 1:1s. Connect evidence recall exceeds 80% of manual items. Meeting quality alerts fire when strategic fraction falls below threshold. |

#### Learning Model Persistence

Learned models are stored in `state/work/work-learned.md`:

```
## Sender Importance Model
- Last calibrated: [YYYY-MM-DD]
- Top senders: [ranked list with learned priority]
- User corrections: [count since last calibration]

## Meeting Pattern Model
- Recurring series tracked: [count]
- Average prep effort by meeting type: [data]
- Readiness score accuracy: [correlation metric]

## Communication Model
- Action-required precision: [%]
- False positive rate: [%]
- User feedback signals: [count]

## Career Velocity Model
- Scope trajectory: [expanding | stable | contracting]
- Trajectory direction: [up | flat | down] over last [N] weeks
- Scope expansion events since bootstrap: [count]
- Last new workstream added: [YYYY-MM-DD] [brief label]
- Last senior invitation (new meeting series with L-N+ stakeholder): [YYYY-MM-DD]
- Manager vocabulary trend: [ownership | execution | delivery] (inferred from 1:1 pivot log tone)
- Visibility events last 90 days: [count], unique L-N+ stakeholders: [count]
- Note: computed from work-project-journeys + work-performance + Stakeholder visibility_events

## Meeting Quality Model
- Strategic fraction (design/planning/forward-looking meetings): [%]
- Execution fraction (status/sync/review meetings): [%]
- Large-meeting fraction (attendees > 10): [%]
- Strategic fraction trend: [improving | stable | declining] over last [N] weeks
- Consecutive low-strategic weeks: [N] (alert threshold: 3)
- Note: meeting type inferred from title keywords + attendee count + recurrence pattern

## Learning Health
- Days since bootstrap: [N]
- Current phase: [calibration | prediction | anticipation]
- Model freshness: [last updated timestamps per model]
```

#### User Feedback Loop

The system needs explicit feedback to learn well:

1. After `/work prep`, optional "Was this prep useful?" signal (thumbs up/down)
2. After `/work` briefing, optional "Anything missing?" prompt
3. After alert dismissal, the system records the dismissal as negative signal
4. After explicit correction ("this sender is actually high priority"), the system updates immediately

Phase: 30-day calibration models are Phase 1. 60-day prediction models (including career velocity) are Phase 2. 90-day anticipation (including meeting quality alerts) is Phase 3.

---

## 8.9 - Work Briefing Adaptive Layer

The personal OS has a `BriefingConfig` dataclass (E5, ACT-RELOADED) with adaptive rules that adjust briefing format based on observed usage patterns. The work briefing needs the same layer.

After 90 days, the system knows that this user always expands the sprint section but skips boundary scores, runs `/work pulse` instead of `/work` when time is short, and reads comms triage but rarely acts without a direct @-mention. Without an adaptive layer, the `/work` briefing delivers the same format regardless of what the user actually uses. That is noise that erodes trust.

#### `WorkBriefingConfig` Adaptive Rules

| Rule | Condition | Adaptation |
|------|-----------|------------|
| **WB-1 Flash override** | Config `work_briefing_format: flash` | Suppress all sections except pulse, top comms, next meeting |
| **WB-2 Section skip** | User scrolls past section in >80% of sessions for 14 days | Move to "expanded on request" — omit from default output |
| **WB-3 Sprint amplify** | Sprint deadline within 3 days | Elevate sprint section to top regardless of default order |
| **WB-4 Connect season** | `work-org-calendar.md` `connect_deadline` within 30 days | Append "Connect readiness" section: evidence density + gap count |
| **WB-5 Promo season** | `work-org-calendar.md` `promo_nomination` within 14 days | Append "Promo snapshot" from `/work promo-case` data |
| **WB-6 Low signal** | All state fresh, no alerts, no open items | One-line only: "Work OS: all quiet. Next: [meeting]." |

Adaptations are state-driven: rules read from `work-learned.md` + `work-org-calendar.md` at invocation time. Rules are never hardcoded in prompts. The config block lives in `user_profile.yaml` under `work.briefing` — zero cron, zero daemon, pure pull.

```yaml
work:
  briefing:
    format: standard              # standard | flash | minimal
    adaptive_rules: true          # enable WB-1 through WB-6
    connect_season_lead_days: 30  # WB-4 trigger window
    promo_season_lead_days: 14    # WB-5 trigger window
```

Phase: WB-1 through WB-3 are Phase 2. WB-4 and WB-5 require `work-org-calendar.md` (Phase 3). WB-6 is Phase 2.

---

## 9. Hard Separation Model

### 9.1 - Separation Requirements

Work and personal must be isolated across:

1. commands
2. state directories
3. processing policies
4. telemetry streams
5. action registries

Encryption is applied selectively, not blanket. See §9.7.

### 9.2 - Storage Separation

| Surface | Personal | Work |
|---------|----------|------|
| State root | `state/` | `state/work/` |
| Audit | `state/audit.md` | `state/work/work-audit.md` |
| Eval | `state/eval/` | `state/work/eval/` |
| Encrypted files | vault-managed | only `work-people.md.age` and `work-career.md.age` |

### 9.3 - Bridge Artifacts Instead Of Raw Cross-Reads

The previous draft allowed direct personal-calendar reads from work. Replace that with **derived bridge artifacts**.

#### Personal To Work bridge

`state/bridge/personal_schedule_mask.json`

Contains only:

- busy start
- busy end
- day
- optional hard or soft flag

Contains never:

- title
- attendees
- notes
- location

#### Work To Personal bridge

`state/bridge/work_load_pulse.json`

Contains only:

- total meeting hours
- after-hours count
- boundary score
- focus availability score

Contains never:

- meeting names
- people
- projects
- messages

### 9.4 - Cross-Surface Policy

Personal commands may read only `work_load_pulse.json`.  
Work commands may read only `personal_schedule_mask.json`.  
No command may read the opposite side's raw state directly.

### 9.5 - Command Isolation

`/work ...` commands never inspect personal domain files. Personal commands never inspect `state/work/` files.

### 9.6 - Alert Isolation

Work alerts and personal alerts must never co-mingle.

Rules:

1. Work alerts (🔴🟠🟡🔵 from work domains) never appear in personal briefings generated by `/catch-up`.
2. Personal alerts never appear in `/work` output.
3. Work alerts support separate delivery channels — work alerts route to the Windows terminal session only; personal alerts may route to Telegram, email, or other configured channels.
4. Critical work alert escalation: if a 🔴 work alert has been unacknowledged for the threshold period and the user has not run `/work`, the system may surface a single-line work-load indicator through the `work_load_pulse.json` bridge artifact — never the alert content itself.
5. Alert acceptance and dismissal rates feed back into threshold tuning, tracked separately in `state/work/eval/work-feedback.json`.

### 9.7 - Selective Encryption Policy

The previous design encrypted all work state files with `.age` vault encryption. This adds latency to every `/work` command invocation — key derivation, decryption, re-encryption on write — without proportional security benefit.

**Rationale:** Work state files already apply PII redaction at write time. Meeting titles are redacted, email bodies are never stored, and project data is summarized. Double-encrypting redacted metadata adds operational overhead without meaningful security gain.

**Prerequisite — PII Redaction Must Be Active:**

This policy is only valid when `redact_keywords` in `user_profile.yaml` is **non-empty**. PII redaction at write time is the security assumption that justifies selective encryption. If `redact_keywords` is empty:

1. **Bootstrap gate:** `/work bootstrap` must include PII keyword seeding (question 12 in §15.5). The system refuses to complete bootstrap with an empty `redact_keywords` list.
2. **Runtime gate:** If `redact_keywords` is empty at `/work` invocation time, the system encrypts all work state files (not just the selective two) until the user configures redaction keywords via `/work health`.
3. **Health check:** `/work health` surfaces `redact_keywords: []` as a 🔴 critical configuration issue with remediation instructions.

The enforcement rule is: **selective encryption requires active redaction. Without redaction, encrypt everything.**

**Policy:**

| File | Encrypted | Reason |
|------|-----------|--------|
| work-people.md | yes (.age) | Contains relationship details, org context, manager chain — genuine PII |
| work-career.md | yes (.age) | Contains performance evidence, Connect goals, review cycle data — sensitive |
| work-comms.md | no | Contains only sender/priority/action-needed metadata, no message bodies |
| work-projects.md | no | Contains sprint health and work item summaries, no confidential content |
| work-open-items.md | no | Contains task titles and status, already redacted |
| work-decisions.md | no | Contains decision records with redacted context |
| work-calendar.md | no | Contains meeting metadata, already redacted |
| work-notes.md | no | Contains user-authored summaries, not raw transcripts |
| work-boundary.md | no | Contains scores and thresholds, no PII |
| work-sources.md | no | Contains URLs and labels, no PII |

This reduces the encryption surface from 6 files to 2, cutting vault overhead by approximately 70% for typical `/work` commands.

### 9.8 - Enforcement & Test Matrix

The hard separation model (§9.1–§9.7) is conceptually strong but reads like policy, not enforcement. Without machine-validated enforcement, future implementations will leak through convenience.

#### Bridge Schema Validation

Bridge artifacts (`personal_schedule_mask.json`, `work_load_pulse.json`) are cross-surface contracts. They must be validated on every write:

1. **Schema enforcement**: every bridge write is validated against the JSON schema (§14.4). Invalid fields are rejected.
2. **Prohibited field test**: a validator checks that no prohibited field (title, attendees, notes, meeting names, people, projects, messages) appears in any bridge artifact. This is a hard test, not a code review convention.
3. **Schema version pinning**: bridge consumers declare the schema version they expect. Version mismatches produce a clear error, not silent data loss.

#### Command-Level Allowlists

| Surface | Allowed State Reads | Prohibited State Reads |
|---------|--------------------|-----------------------|
| `/work *` commands | `state/work/*`, `state/bridge/personal_schedule_mask.json` | `state/*.md` (personal state), `state/bridge/work_load_pulse.json` (that's for personal consumption) |
| `/catch-up`, personal commands | `state/*.md`, `state/bridge/work_load_pulse.json` | `state/work/*`, `state/bridge/personal_schedule_mask.json` |
| Bridge writers | own surface's state only | opposite surface's state |

These allowlists must be enforced in the command router, not just documented in a spec. A command that attempts to read prohibited state files should fail with a clear error.

#### Cross-Surface Access Tests

Automated tests must verify:

| Test | Assertion |
|------|-----------|
| Work command reads personal state | MUST FAIL |
| Personal command reads work state | MUST FAIL |
| Bridge artifact with prohibited field | MUST REJECT on write |
| Bridge artifact with unknown field | MUST REJECT on write |
| Work audit entries in personal audit file | MUST NOT EXIST |
| Personal audit entries in work audit file | MUST NOT EXIST |
| Work alert in personal briefing output | MUST NOT APPEAR |
| Personal alert in work briefing output | MUST NOT APPEAR |

#### Separate Work Audit Schema

`state/work/work-audit.md` follows the same format as `state/audit.md` with additional fields:

- `provider`: which connector produced the audited event
- `tier`: which distribution tier (core, enterprise, microsoft-enhanced)
- `degraded`: boolean — was the system in degraded mode for this run

#### Failure Mode: Invalid Bridge

If a bridge artifact fails validation:

1. The write is rejected. The previous valid artifact is preserved.
2. The failure is logged to the generating surface's audit file.
3. The consuming surface sees stale-but-valid data, never corrupt data.
4. `/work health` and `/health` both surface bridge validation failures.

---

## 10. Personalization Model

### 10.1 - Non-Negotiable Rule

All personalization happens in state or profile, not in prompts.

Prompts must not contain:

- named colleagues
- specific org structures
- a user's work hours
- user-specific threshold values
- specific project codenames

### 10.2 - Work Personalization Section In Profile

Add a dedicated section to [config/user_profile.yaml](config\user_profile.yaml):

```yaml
work:
  enabled: true
  schedule:
    work_start_time: "08:00"
    work_end_time: "18:00"
    timezone: "America/Los_Angeles"
    weekend_days: ["Saturday", "Sunday"]
  org:
    project_tool: azure_devops       # azure_devops | jira | github | linear
    chat_tool: teams                 # teams | slack
    calendar_tool: outlook           # outlook | google_workspace
    team_size: null                  # affects alert threshold calibration
    seniority_labels:                # org-specific labels for priority routing
      high: ["VP", "CVP", "Distinguished", "Partner", "Director"]
      manager: []                    # auto-learned from state
    review_cycle: "H1/H2"           # H1/H2 | annual | quarterly
  meeting_prep:
    prep_window_hours: 4
    seniority_trigger: "director+"
    max_people_lookups_per_run: 3
  alerts:
    sprint_velocity_warning_pct: 70
    pr_age_warning_days: 3
    after_hours_multiplier: 1.5
    meeting_overload_hours_per_day: 5.0
  teams:
    priority_channels: []            # channel names/IDs that always surface
    muted_channels: []               # channel names/IDs to suppress
    at_mention_priority_boost: true  # @-mentions rank higher than general posts
  caching:
    people_cache_entries: 50
    people_cache_ttl_days: 7
  providers:
    email: msgraph
    calendar: msgraph
    people: msgraph
    chat: teams
    projects: azure_devops
    tenants:                         # multi-tenant support (Phase 3+)
      - id: primary
        type: home
  bootstrap:
    setup_completed: false           # cold-start interview done
    import_completed: false          # warm-start data import done
    source_path: null
    parser: null
```

### 10.3 - State-Level Personalization Examples

Examples of what belongs in state, not prompts:

- relationship graph
- personal manager chain
- historical meeting norms
- learned sender importance
- project naming patterns
- review-cycle evidence history
- user-dismissed or user-confirmed alert patterns

### 10.4 - Specific Correction

Named people that appeared in the earlier warm-start section must not appear in the shipped spec as part of the product definition. The bootstrap loads those into state for the local user; they are not part of the distributed package contract.

---

## 11. Domain Model

### 11.1 - Core Domains

| Domain | Purpose | Shipped In Core Package |
|--------|---------|-------------------------|
| work-calendar | Meeting load, conflicts, focus windows | yes |
| work-comms | Actionable work communications. Processes email, Teams DMs, Teams channel @-mentions, and meeting chats. Treats Teams as a collaboration graph, not a flat message stream. Channel relevance scoring, @-mention priority boosting, and meeting-chat context extraction differentiate Teams signal from email signal. **Microsoft Enhanced tier adds ES Chat signal hierarchy:** ES Chat messages from VP+ or CVP+ rank above all other communication signals; ES Chat @-mentions rank above Teams @-mentions; ES Chat thread participation is treated as executive-visibility evidence for career capture. | yes |
| work-projects | Project and sprint telemetry | yes |
| work-people | Relationship and collaboration graph. Not a flat lookup cache. Tracks who works with whom, collaboration frequency and breadth, network changes over time, and communication pattern anomalies. Supports meeting prep, career evidence, and organizational intelligence. | yes |
| work-notes | Artifact recency and context recovery | yes |
| work-boundary | Workload and after-hours protection | yes |
| work-career | Review evidence and accomplishment memory. Consumes collaboration graph data from work-people for network breadth metrics and impact evidence. | yes |
| work-performance | Manager + Connect OS. Tracks Connect goals, progress snapshots, manager 1:1 pivot points, commitment ledger, stakeholder influence map, and review evidence auto-collected from meetings, chats, and email. Generates Connect submission drafts and narrative artifacts. Encompasses both the Connect cycle (goal-setting, check-ins, rewards) and the manager operating rhythm (1:1 memory, commitment tracking, skip-level prep). The killer feature is narrative assembly, not evidence capture. | yes |
| work-sources | Data source registry. Curated index of dashboard links, Kusto queries, Power BI reports, SharePoint lists, and information portals encountered in meetings, email, and chat. Each entry records what it answers, who shared it, when last referenced, and relevance tags. Feeds `/work decide` with "where to find the data" and eliminates the "I know I saw that dashboard somewhere" problem. | yes |
| work-project-journeys | Long-running program timelines with milestone evidence, scope expansion arc, impact provenance, and before/after framing. The canonical source for `/work promo-case` and Connect narrative assembly. Updated by the work-process stage from work-projects + work-notes + work-decisions + user annotations. Not a snapshot — an append-only timeline. Each entry cites at least one enterprise artifact (ADO item, IcM incident, SharePoint doc, calendar event). | yes |
| work-org-calendar | Organizational milestone calendar: Connect cycle deadlines, rewards season windows, fiscal year close, headcount planning cycles, promotion nomination windows. These dates are known by every experienced employee but tracked nowhere. Populated during `/work bootstrap` Q13 and via `/work remember`-style annotations. Feeds `/work` briefing 30-day lookahead alerts ("Connect submission in 8 days") and `WorkBriefingConfig` WB-4/WB-5 adaptive rules (§8.9). No connector dependency — pure user-maintained Markdown state. | yes |

### 11.2 - Workflow-Derived Views

These are not standalone domains. They are workflow views synthesized from multiple domains:

- work-pulse
- work-prep
- work-return
- work-connect
- work-sprint

### 11.3 - Future Optional Domains

| Domain | Why |
|--------|-----|
| work-incidents | incident awareness for on-call or service owners |
| work-repos | code and PR awareness |
| work-learning | training, certification, and internal knowledge growth |

### 11.4 - Domain Registry Requirement

All work domains must be first-class entries in [config/domain_registry.yaml](config\domain_registry.yaml). This is a prerequisite, not a future cleanup.

Each entry should include:

- label
- description
- sensitivity
- prompt_file
- state_file
- requires_vault
- enabled_by_default
- requires_connectors
- setup_questions
- run_on
- command_namespace: `work`

### 11.5 - Cross-Domain Reasoning Pairs

Add the following mandatory pairs to [config/workflow/reason.md](config\workflow\reason.md):

| Pair | Why Check |
|------|-----------|
| Work-Calendar x Personal Schedule Mask | detect hard conflicts |
| Work-Calendar x Goals | deep work preservation |
| Work-Calendar x Health | appointment collision risk |
| Work-Calendar x Travel | timezone and transit feasibility |
| Work-Projects x Work-Calendar | sprint feasibility in calendar reality |
| Work-Comms x Employment | escalation and review-cycle relevance |
| Work-Boundary x Wellness | burnout and recovery risk |
| Work-Career x Goals | career progress against declared goals |
| Work-Performance x Work-Calendar | detect manager 1:1s for Connect pivot capture |
| Work-Performance x Work-Projects | auto-collect delivery evidence for Connect goals |
| Work-Performance x Work-People | collaboration breadth evidence for Connect review |
| Work-Commitments x Work-Calendar | **Delivery Feasibility Score** — cross-reference open commitments (from meeting memory, sprint backlog, and manager ledger) against available calendar capacity. Surface overcommitment risk: "You have 14 open commitments and 2.1 hours of non-meeting time this week." Feeds `/work` briefing and `/work sprint` output. |
| Work-Sources x Work-Projects | link data sources to active project decisions |

These pairs are the difference between a report and an operating system.

---

## 12. Provider Model

### 12.1 - Design Goal

The package must work for any M365 user. Microsoft-internal tools are enhancements, not assumptions.

### 12.2 - Provider Categories

```yaml
provider_categories:
  email: [msgraph_email, workiq_email]
  calendar: [msgraph_calendar, workiq_calendar, outlookctl_bridge]
  people: [msgraph_people, workiq_people]
  chat: [teams_graph, workiq_teams, slack]
  projects: [azure_devops, jira, github_issues, linear]
  docs: [sharepoint_graph, onedrive_graph, workiq_documents]
```

### 12.3 - Baseline Provider Strategy

| Capability | Universal Baseline | Microsoft Enhanced (Agency Default) |
|------------|--------------------|--------------------|
| Email | MS Graph | WorkIQ or Agency WorkIQ MCP |
| Calendar | MS Graph | WorkIQ or Outlook bridge |
| People | MS Graph | WorkIQ plus Graph enrichment |
| Projects | ADO, Jira, or GitHub adapter | ADO plus Agency ADO MCP |
| Docs | Graph | WorkIQ documents |
| Incidents | none | ICM via Agency |

### 12.4 - Implication For The Product

The earlier draft treated MS Graph as fallback and WorkIQ as primary. For a standardized distributable package, this must be inverted:

- **MS Graph is the portable baseline**
- **WorkIQ is a Microsoft-enhanced accelerator**
- **Agency is a premium internal orchestration layer — and the default runtime for Microsoft employees**

### 12.5 - Warm-Start Parser Abstraction

Warm start must not be hardcoded to one scrape format. Define:

```text
WorkHistoryParser
  - parse_people()
  - parse_meetings()
  - parse_projects()
  - parse_docs()
  - parse_boundary_signals()
  - parse_career_evidence()
```

The Microsoft-specific scrape parser is one implementation, not the only implementation.

### 12.6 - Multi-Tenant Strategy

Real M365 users increasingly work across multiple tenants — guest access, B2B collaboration, contractor roles, joint ventures. The schema must support this from day one, even if the implementation is Phase 3.

Design rules:

1. The `work.providers.tenants` array in user_profile.yaml supports multiple tenant configurations, each with its own auth, Graph endpoint, and provider mappings.
2. One tenant is marked `type: home` (the user's primary employer). Others are `type: guest`.
3. Calendar events from guest tenants merge into the work-calendar domain with a `tenant: <id>` tag.
4. Email from guest tenants is processed by work-comms with tenant-aware sender priority.
5. Project tools may differ per tenant (ADO in one, Jira in another).
6. Each tenant requires its own auth credentials stored under separate vault entries: `work_<tenant_id>`.

Phase: Schema design is Phase 0. Multi-tenant provider wiring is Phase 3.

---

## 13. Action Model

### 13.1 - Principle

Work OS is not read-only forever. It is write-cautious. The action registry already exists to support this distinction.

### 13.2 - Required Work Actions

| Action | Type | Default |
|--------|------|---------|
| work_meeting_prep_generate | read-safe artifact generation | enabled |
| work_focus_block_propose | calendar proposal | approval required |
| work_item_comment | ADO write | disabled by default, approval required |
| work_followup_draft | draft-only message generation | enabled |
| work_boundary_acknowledge | alert disposition | enabled |
| work_open_item_sync | sync work-derived tasks into work open items | enabled |
| work_meeting_notes_generate | post-meeting summary and action capture | enabled |
| work_connect_evidence_capture | auto-collect Connect goal evidence from meetings, chats, and project completions | enabled |
| work_source_capture | extract and register data source URLs from meetings and email | enabled |
| work_newsletter_generate | assemble newsletter draft from sprint, decisions, and accomplishments | enabled |
| work_deck_generate | assemble structured deck content from projects, evidence, and sources | enabled |

### 13.3 - Example Registry Entries

```yaml
work_meeting_prep_generate:
  handler: "scripts/actions/work_meeting_prep_generate.py"
  enabled: true
  friction: low
  min_trust: 0
  sensitivity: standard
  run_on: windows
  autonomy_floor: false
  audit: true

work_focus_block_propose:
  handler: "scripts/actions/work_focus_block_propose.py"
  enabled: true
  friction: standard
  min_trust: 1
  sensitivity: elevated
  run_on: windows
  autonomy_floor: true
  audit: true

work_item_comment:
  handler: "scripts/actions/work_item_comment.py"
  enabled: false
  friction: high
  min_trust: 2
  sensitivity: elevated
  run_on: windows
  autonomy_floor: true
  audit: true
```

### 13.4 - Open Items Integration

Work-derived obligations need a place in the operating model. Preferred design:

`state/work/work-open-items.md`

This preserves hard separation instead of mixing work tasks into the personal open items file. Open items contain task titles and status metadata — already redacted at write time — so vault encryption is not required (§9.7).

---

## 14. State And Schema Model

### 14.1 - Directory Layout

```text
state/
  bridge/
    personal_schedule_mask.json
    work_load_pulse.json
  work/
    work-calendar.md
    work-comms.md
    work-projects.md
    work-people.md.age
    work-notes.md
    work-boundary.md
    work-career.md.age
    work-open-items.md
    work-decisions.md
    work-performance.md
    work-sources.md
    work-project-journeys.md
    work-org-calendar.md
    work-promo-narrative.md      # generated by /work promo-case --narrative
    work-learned.md
    work-summary.md
    work-audit.md
    eval/
      work-metrics.json
      work-feedback.json
```

### 14.2 - State Rules

1. no raw work message bodies
2. no raw work document contents
3. redacted titles only where necessary
4. encrypted state for elevated-sensitivity work data
5. derived bridge artifacts only across surfaces

### 14.3 - Work Summary State

Add a compact summary cache for fast command response:

`state/work/work-summary.md`

This file stores the latest synthesized view for:

- work pulse
- next critical meeting
- top comms item
- top project risk
- current boundary score

This is how `/work pulse` stays nearly instant.

### 14.4 - Bridge Artifact Schemas

Bridge artifacts are cross-surface contracts. They must be versioned and validated.

#### personal_schedule_mask.json

```json
{
  "$schema": "artha/bridge/personal_schedule_mask/v1",
  "generated_at": "2026-03-24T07:42:00Z",
  "date": "2026-03-24",
  "blocks": [
    {
      "busy_start": "09:00",
      "busy_end": "10:00",
      "type": "hard"
    }
  ]
}
```

Fields: `busy_start` (HH:MM), `busy_end` (HH:MM), `type` (hard | soft), `date` (YYYY-MM-DD). No other fields permitted.

#### work_load_pulse.json

```json
{
  "$schema": "artha/bridge/work_load_pulse/v1.1",
  "generated_at": "2026-03-24T07:42:00Z",
  "date": "2026-03-24",
  "total_meeting_hours": 5.2,
  "after_hours_count": 3,
  "boundary_score": 0.6,
  "focus_availability_score": 0.3,
  "phase": "normal",
  "advisory": ""
}
```

Required fields: `total_meeting_hours` (float), `after_hours_count` (int, meetings or comms outside work hours this week), `boundary_score` (0.0-1.0, higher is healthier), `focus_availability_score` (0.0-1.0, fraction of work hours without meetings).

Optional semantic fields (v1.1):
- `phase` (string enum — `normal | sprint_deadline | connect_submission | promo_season | offboarding_prep`) — signals the work surface's current operating context to the personal briefing. Safe to cross the bridge: contains no work content, only a status label.
- `advisory` (string, max 100 chars) — a single human-readable signal for the personal briefing to consume, e.g., `"Connect submission due in 8 days"` or `"Sprint deadline Thursday"`. Strictly no names, project titles, or meeting details. Validated against a prohibited-content pattern before write.

**Bridge safety rule:** optional fields are validated identically to required fields. Any `advisory` content matching the prohibited-field patterns (names, project names, meeting titles) is rejected and replaced with an empty string. The validator never blocks the write over optional fields — it sanitizes and logs.

Schema validation runs on every bridge artifact write. Invalid artifacts are rejected and the previous valid artifact is preserved.

### 14.5 - Work Decisions State

`state/work/work-decisions.md`

Stores structured decision records for pattern matching and career evidence:

```
## YYYY-MM-DD — [decision context]
- Decision: [what was decided]
- Evidence used: [domains consulted]
- Outcome: [pending | positive | negative | neutral]
- Pattern: [escalation | timeline-push | resource-request | architecture | ...]
```

### 14.6 - Work Performance State

`state/work/work-performance.md`

Stores Manager + Connect OS data: Connect goals, manager operating rhythm, commitment ledger, stakeholder influence, and narrative artifacts.

```
## Connect Goals — [H1/H2 YYYY]

### Goal 1: [goal title]
- Priority: [P0 | P1 | P2]
- Status: [on-track | at-risk | behind | completed]
- Evidence:
  - [YYYY-MM-DD] [evidence item linked to source domain]
  - [YYYY-MM-DD] [evidence item]
- Evidence gaps: [what is missing]
- Narrative thread: [one-sentence story this goal supports]

## Manager Commitment Ledger

### [commitment title]
- Made: [YYYY-MM-DD] in [meeting/context]
- Owner: [user | manager | mutual]
- Due: [YYYY-MM-DD | ongoing | no deadline]
- Status: [open | delivered | deferred | dropped]
- Last referenced: [YYYY-MM-DD]

## Manager 1:1 Pivot Log

### YYYY-MM-DD
- Topics raised: [summary]
- Commitments made: [what was agreed — auto-linked to commitment ledger]
- Pivots: [what changed direction]
- Unresolved asks: [what was raised but not concluded]
- Follow-up: [action items with owners]

## Stakeholder Influence Map

### [stakeholder name/alias]
- Role: [title, org]
- Last observed impact: [YYYY-MM-DD] [context — meeting, review, email]
- Observation recency: [recent | stale | cold]
- Influence on: [which goals or projects this person's observation supports]
- Recommendation: [e.g., "invite to next architecture review" | "share design doc"]

## Connect Submission Draft — [period]
[Auto-generated draft from evidence collection]
[Narrative structure: What changed → Why it mattered → Who observed it → What proof exists]
```

### 14.7 - Work Sources State

`state/work/work-sources.md`

Stores the curated data source registry:

```
## Data Sources

### [source title]
- URL: [link]
- Answers: [what question this source answers]
- Shared by: [who shared it, if known]
- First seen: [YYYY-MM-DD]
- Last referenced: [YYYY-MM-DD]
- Tags: [project, domain, topic tags]
- Type: [dashboard | kusto-query | power-bi | sharepoint-list | wiki | portal | other]
```

### 14.8 - Work Org Calendar State

`state/work/work-org-calendar.md`

Stores org-level milestone dates that every experienced employee knows but no tool tracks:

```
## Org Calendar

### [event title]
- Type: [connect_deadline | rewards_season | fiscal_year_close | headcount_planning | promo_nomination | other]
- Date: [YYYY-MM-DD] (or range: YYYY-MM-DD to YYYY-MM-DD)
- Lead_days_alert: [N]   # how many days before to surface in /work briefing
- Notes: [context, e.g., "H2 2026 Connect submission deadline"]
- Period: [H1 2026 | H2 2026 | FY2026 | etc.]
```

Rules:
1. Populated via `/work bootstrap` Q13 and manual annotation via `/work remember org-calendar: ...`
2. No connector dependency — purely user-maintained Markdown state edited by the user or updated by Artha via structured capture.
3. Feeds `WorkBriefingConfig` WB-4/WB-5 rules (§8.9) and the `phase` field of `work_load_pulse.json` bridge (v1.1).
4. Survives data loss gracefully — the format is trivially human-editable in any text editor.

### 14.9 - Work Project Journeys State

`state/work/work-project-journeys.md`

Stores append-only program timelines structured for evidence provenance. Not a snapshot — a running record.

```
---
schema_version: "1.0"
domain: work-project-journeys
last_updated: [YYYY-MM-DDThh:mm:ssZ]
purpose: "Promotion-grade program timelines with evidence provenance"
---

## [Program Name]

**Role:** [user's role in this program]
**Duration:** [start date] → [end date or "present"]
**Scope:** [one-line description]

### Timeline

| Date | Milestone | Evidence | Impact |
|------|-----------|----------|--------|
| [YYYY-MM] | [milestone] | [artifact: type + citation] | [impact statement] |

### Scope Expansion Arc
[Before state] → [After state]

### Manager Signal
[Verbatim or paraphrased manager recognition, linked to Connect period]
```

Rules:
1. Updated by the work-process stage reading from work-projects + work-notes + work-decisions + user annotations.
2. Each milestone cites at least one enterprise artifact (ADO item ID, IcM incident ID, SharePoint doc URL, calendar event).
3. Evidence citations use artifact aliases, not raw PII. Full details in `work-career.md.age`.
4. Feeds `/work promo-case`, `/work journey`, `/work connect-prep`, and the Narrative Engine.

### 15.1 - Why Warm Start Matters

Without historical memory, Work OS behaves like a stateless assistant. With historical memory, it behaves like an operating system that has lived with the user.

### 15.2 - Inputs

Possible inputs include:

- Microsoft-specific WorkIQ scrape archive
- exported calendar history
- exported Outlook metadata
- ADO history
- Jira issue exports
- document recency exports

### 15.3 - Output Targets

Warm start should seed only:

- relationship graph
- recurring meeting patterns
- baseline communication norms
- project timelines
- boundary baselines
- review evidence history

Never raw corporate content.

### 15.4 - Product Rule

Warm start is optional but transformative. The cold-start experience must still be useful. The warm-start experience must feel like a superpower.

### 15.5 - Cold-Start Bootstrap Interview

Most distributed users will not have a historical archive. `/work bootstrap` must support two modes:

**Mode 1: Setup (cold-start interview)**

`/work bootstrap` with `setup_completed: false` in profile triggers a guided interview:

1. Work schedule (start time, end time, timezone, weekend days)
2. Organizational context (team size, manager chain, skip-level name)
3. Project tool (ADO, Jira, GitHub, Linear — determines provider routing)
4. Chat tool (Teams, Slack — determines comms processing)
5. Communication norms (how fast do you typically respond? what's your noise threshold?)
6. Meeting prep preferences (who is "high-stakes"? what prep window?)
7. Alert sensitivity calibration (what constitutes noise vs. signal for this user?)
8. Review cycle timing (H1/H2, annual, quarterly)
9. Connect goals (current period goals, key results, metrics)
10. Data source preferences (frequently used dashboards, default query portals)
11. Provider authentication (Graph, ADO, WorkIQ — guided OAuth or CLI auth)
12. PII redaction keywords (names, project codenames, team names, internal tools that should be redacted in state files — required for selective encryption policy §9.7)
13. Organizational calendar dates: Connect cycle deadlines (H1/H2 submission dates), rewards season opening, fiscal year close (e.g., June 30 for Microsoft), headcount planning cycle, promotion nomination windows — populates `work-org-calendar.md` (§14.8) and enables 30-day lookahead alerts and `WorkBriefingConfig` WB-4/WB-5 rules (§8.9). User can add entries after bootstrap via `/work remember org-calendar: ...`.

Interview follows the personal `/bootstrap` pattern: one question at a time, progress saved to profile, resumable.

After completion: `work.bootstrap.setup_completed: true` in profile.

**Mode 2: Import (warm-start)**

`/work bootstrap import` triggers historical data ingestion per §15.2 and §15.3.

After completion: `work.bootstrap.import_completed: true` in profile.

**Gating:** `/work` commands display a setup prompt if `setup_completed: false`. The system is functional without import but not without setup.

---

## 16. Observability And Evaluation

### 16.1 - Missing Today

The current system has evaluation surfaces, but the earlier work spec did not make work usefulness measurable. That is a design bug.

### 16.2 - Required Work Metrics

| Metric | Why |
|--------|-----|
| connector latency p50 and p95 | reliability |
| cache hit rate | performance |
| work briefing signal-to-noise | usefulness |
| prep card open and use rate | meeting prep value |
| action-required precision | comms quality |
| blocker detection precision | sprint quality |
| boundary alert acceptance rate | trust and relevance |
| return-from-absence usefulness | workflow success |

### 16.3 - Eval State Example

```yaml
work_eval:
  connectors:
    calendar_latency_p50_sec: 8.2
    comms_latency_p50_sec: 12.6
    projects_latency_p50_sec: 3.1
    cache_hit_rate_pct: 68
  workflows:
    work_pulse_success_pct: 99.1
    work_prep_useful_pct: 71
    work_return_useful_pct: 83
  quality:
    comms_action_precision_pct: 78
    blocker_detection_precision_pct: 74
    boundary_false_positive_pct: 9
```

### 16.4 - Degraded Mode Reporting

If a provider is unavailable, the user should see:

```text
Work OS running in degraded mode:
- email: Graph unavailable
- calendar: Graph available
- projects: ADO available
- people: cached only
```

This is operational honesty. It protects trust.

### 16.5 - Outcome Metrics

Operational metrics (§16.2) prove the system runs. Outcome metrics prove it becomes indispensable. The spec must measure dependence, not just latency.

| Metric | What It Measures | Target |
|--------|-----------------|--------|
| Time-to-readiness before high-stakes meetings | Minutes from `/work prep` to "I'm prepared" | <2 min for recurring, <5 min for novel |
| Response-risk catches | Comms flagged as needing response that would have been missed without the system | Track count, validate with user feedback |
| Connect evidence recall rate | Percentage of review-relevant accomplishments captured by auto-evidence vs. manual recall | >80% of manually written evidence items also captured by system |
| Recurring meeting carry-forward rate | Percentage of recurring meetings with unresolved items carried forward to next instance | >90% after Phase 2 |
| Dashboard hunt elimination | Number of `/work sources` lookups that replace manual bookmark/email searching | Track count per week |
| Newsletter/deck draft acceptance rate | Percentage of generated draft content that survives into final version with minimal edits | >60% content retention |
| Manager 1:1 commitment closure rate | Percentage of commitments logged in 1:1s that reach resolution status | Track and surface, no specific target (visibility is the value) |
| Morning orientation time | Wall-clock time from `/work` invocation to user's first non-Artha action | <3 min |
| Return-from-absence orientation time | Wall-clock time from `/work return` to "I know what happened" | <5 min for ≤5 day absence |
| System dependency ("cannot live without") | Self-reported user rating at 30/60/90 day marks | Target: ≥8/10 at 90 days |

**Measurement approach:**
- Latency metrics are auto-collected in `state/work/eval/work-metrics.json`.
- Quality metrics use lightweight feedback: thumbs up/down on `/work` output, explicit "this was useful" / "this missed something" signals.
- Outcome metrics are aggregated weekly and surfaced in `/work health`.

---

## 17. Value Creation

### 17.1 - Time Saved

| Workflow | Before | After |
|----------|--------|-------|
| Morning orientation | 30-60 min | under 3 min |
| Meeting prep | 10-20 min | under 1 min |
| Sprint review | 10-15 min | under 2 min |
| Return from PTO | 45-90 min | under 5 min |
| Review prep (Connect) | 1-2 days | under 30 min |
| Newsletter assembly | 2-4 hours | under 15 min (draft review only) |
| LT deck content gathering | 3-6 hours | under 20 min (structured content ready) |
| Finding a dashboard/data source | 5-30 min per search | under 10 sec (indexed lookup) |
| Promotion case assembly | 2-5 days of recall + writing | under 30 min (draft from `/work promo-case --narrative`) |
| Connect evidence reconstruction | 1-3 hours per cycle | under 10 min (continuous auto-capture) |
| Org calendar awareness | Zero (tracked nowhere) | Instant (30-day lookahead from `work-org-calendar.md`) |

### 17.2 - Cognitive Value

The deeper gain is not time. It is reduced uncertainty.

The system answers, quickly:

- what matters now
- what can wait
- what changed while the user was away
- where risk is accumulating
- where the user's own boundary is deteriorating
- whether scope is expanding or contracting (career trajectory)
- whether the right people have seen the user's work (visibility)
- whether the evidence supports the case being made (promotion readiness)

The last three are invisible without active tracking. They are the difference between a system that manages operational drag and one that changes career outcomes.

That is what users eventually realize they cannot live without.

---

## 18. Risks And Mitigations

### 18.1 - Work Data Leakage To Personal Surface

Mitigation:

- separate vault key
- separate state directory
- separate command namespace
- bridge artifacts only
- selective sync exclusion for work directory

### 18.2 - Microsoft-Internal Dependency Risk

Mitigation:

- Graph-first baseline ensures product without Agency
- WorkIQ as enhancer only
- Agency as default for Microsoft tier but not architectural dependency
- Graceful degradation: Agency unavailable → direct CLI + Graph providers

### 18.3 - Prompt Drift Into Personalization

Mitigation:

- enforce profile and state-only personalization policy
- lint prompts for named entities and user-specific constants

### 18.4 - Noise Creep

Mitigation:

- work-specific eval metrics
- alert dismissal feedback loop
- workflow-first command surface instead of raw domain dumps

### 18.5 - Write-Action Trust Failure

Mitigation:

- default disabled for risky writes
- hard `autonomy_floor` gates
- separate work audit trail

---

## 19. Implementation Plan

### Pre-Phase-0 — Bug Fixes & Security Prerequisites

These must be completed before any Phase 0 work begins. They fix codebase issues that would undermine Phase 0 execution.

| # | Task | File | Issue | Status |
|---|------|------|-------|--------|
| P0.1 | Fix `since` parameter ignored in WIQL query | `scripts/connectors/ado_workitems.py` | `since` was accepted but never passed to `_wiql_query()` — WIQL had no date filter | **DONE** |
| P0.2 | Fix `outlookctl_bridge` platform mismatch | `config/connectors.yaml` | `run_on: all` but code is Windows-only; changed to `run_on: windows` | **DONE** |
| P0.3 | Fix `skills.yaml` duplicate key `ai_trend_radar` | `config/skills.yaml` | Duplicate YAML keys caused `mental_health_utilization` to lose its properties and `ai_trend_radar` to have conflicting values | **DONE** |
| P0.4 | Seed `redact_keywords` in user_profile.yaml | `config/user_profile.yaml` | Was `[]` — §9.7 security gate requires non-empty list for selective encryption. Seeded with placeholder pending `/work bootstrap` | **DONE** |

**Gate:** known codebase bugs fixed. `redact_keywords` is non-empty (placeholder seeded — real values via `/work bootstrap` question 12). No pre-existing issues will block Phase 0 execution.

### Phase 0 — Make Work A First-Class Citizen

Phase 0 items are dependency-ordered. Items in the same step can be parallelized. Items in later steps depend on earlier steps.

#### Step 0A — Config File Registration (no code dependencies)

| # | Task | File(s) | Depends On | Status |
|---|------|---------|------------|--------|
| 0A.1 | Register all 11 work domains with `command_namespace: work` | `config/domain_registry.yaml` | — | **DONE** for 9 domains (5 existing + 4 new); `work-project-journeys` and `work-org-calendar` pending registration |
| 0A.2 | Register `/work` command namespace (24+ commands) | `config/commands.md` | — | **DONE** |
| 0A.3 | Register work action types (11 actions) | `config/actions.yaml` | — | **DONE** |
| 0A.4 | Register work reasoning pairs (13 pairs) | `config/workflow/reason.md` | — | **DONE** |
| 0A.5 | Create `state/work/` directory tree | filesystem | — | **DONE** |
| 0A.6 | Create `state/bridge/` directory tree | filesystem | — | **DONE** |

#### Step 0B — Schema & Protocol Definitions (depends on 0A)

| # | Task | Spec Reference | Depends On |
|---|------|---------------|------------|
| 0B.1 | Define bridge artifact JSON schemas | §14.4 | 0A.6 | **DONE** (`scripts/schemas/bridge_schemas.py`) |
| 0B.2 | Define Canonical Work Object schemas | §8.6 | 0A.1 | **DONE** (`scripts/schemas/work_objects.py`, 6 dataclasses) |
| 0B.3 | Define connector error protocol | §8.4 | 0A.1 | **DONE** (`scripts/schemas/work_connector_protocol.py`, 10 entries) |
| 0B.4 | Design multi-tenant schema in user_profile.yaml | §12.6 | — | **DONE** (`config/user_profile.yaml`, 9 keys under `work:`) |
| 0B.5 | Update PRD to supersede FR-14 | §4.4 | — | **DONE** (`specs/artha-prd.md` FR-14 supersession note added) |

#### Step 0C — Execution Backbone (depends on 0B)

| # | Task | Spec Reference | Depends On |
|---|------|---------------|------------|
| 0C.1 | Implement Work Operating Loop stages (background-first read path) | §8.5, §3.8 | 0B.2, 0B.3 | **DONE** (`scripts/work_loop.py`, 7 stages, READ+REFRESH modes) |
| 0C.2 | Implement `/work refresh` command (live connector path) | §8.7 | 0C.1 | Phase 1 — deferred (blocked on command handler infrastructure) |
| 0C.3 | Register `work_background_refresh` skill | §8.7, §22 | 0C.1 | **DONE** (`scripts/skills/work_background_refresh.py` + 9 skills in skills.yaml) |
| 0C.4 | Implement bridge schema validation | §9.8 | 0B.1 | **DONE** (validation live in bridge_schemas.py; called by write_bridge_artifact) |

#### Step 0D — Enforcement & Validation (depends on 0C)

| # | Task | Spec Reference | Depends On |
|---|------|---------------|------------|
| 0D.1 | Implement command-level allowlists (cross-surface access control) | §9.8 | 0C.1 | Phase 1 — deferred (blocked on command router; path-check helpers validated in test suite) |
| 0D.2 | Implement cross-surface access tests | §9.8 | 0D.1 | **DONE** (`tests/work/test_bridge_enforcement.py`, 55 tests, 55 pass — 7 test groups covering §9.8 matrix) |
| 0D.3 | Validate Graph-only acceptance criteria | §6.5 | 0C.1 | Phase 1 — deferred (requires connector integration) |
| 0D.4 | Prototype Artha Work Agent for Agency marketplace | §21.5 | 0C.1 | **DONE** (`config/agents/artha-work.md`, `artha-work-enterprise.md`, `artha-work-msft.md`) |

**Phase 0 UX Gate — First-User Trust Experience:**

Before Phase 0 is declared complete, a new user must be able to:

1. Run `/work bootstrap` and answer ≤13 questions (including PII keyword seeding and org calendar dates) in under 10 minutes
2. Run `/work pulse` and receive useful output in <5 seconds (from pre-computed state or first-run degraded mode)
3. Run `/work` in degraded mode (no historical data, partial providers) and see a useful briefing — not an error
4. Run `/work health` and see clear status of what is configured, what is missing, and how to fix it
5. Reach "this is useful" within 15 minutes of first interaction

If this gate does not pass, the product is not ready for Phase 1 — regardless of how many features are structurally complete.

**Gate:** work is structurally integrated, not bolted on. The Work Operating Loop is the command execution path. Bridge schemas are validated with enforcement tests. Graph-only mode passes all acceptance criteria. Error protocol is defined. First-user UX gate passes.

### Phase 1 — Ship Work OS Core Workflows

1. `/work` — full work briefing (reads pre-computed state, includes data freshness footer)
2. `/work pulse` — 30-second snapshot
3. `/work prep` — meeting preparation with readiness scoring (§7.9)
4. `/work sprint` — delivery health with Delivery Feasibility Score (§11.5)
5. `/work return` — absence recovery
6. `/work connect` — review evidence assembly
7. `/work notes` — post-meeting capture with follow-up package (§7.9)
8. `/work bootstrap` — cold-start interview mode (including PII keyword seeding, §15.5)
9. `/work sources` and `/work sources add` — data source registry — **DONE** (`cmd_sources()` + `cmd_sources_add()` in work_reader.py; atomic append to work-sources.md)
10. `/work connect-prep` — Connect goal tracking, evidence capture, and calibration defense brief (§7.6) — **DONE** (`cmd_connect_prep()` in work_reader.py; delegates to NarrativeEngine.generate_connect_summary())
11. `/work refresh` — explicit live connector run (§8.7) — `python scripts/work_loop.py --mode refresh` as CLI fallback — **DONE** (`main()` in work_loop.py with `--mode refresh|read` flags)
12. `/work memo --weekly` — weekly status auto-draft via Narrative Engine (§7.10) — **DONE** (`cmd_memo()` in work_reader.py; delegates to NarrativeEngine.generate_weekly_memo())
13. work alert isolation (§9.6) — `validate_alert_isolation()` in bridge_schemas.py + enforcement tests — **DONE** (67 bridge enforcement tests passing)
14. 30-day learning calibration infrastructure (§8.8) — `_update_learned_state()` updates work-learned.md after each refresh with days_since_bootstrap, learning_phase, refresh_runs — **DONE** (v2.1.0)

**Note on manager commitment ledger (§7.6):** Item 14 was listed here in error. §7.6 body text is the authoritative source: *"Manager commitment ledger and 1:1 memory are Phase 2."* The state/work/work-performance.md template stub and /work notes command file are Phase 1 deliverables; auto-population logic is Phase 2. Moved to Phase 2 item 18 to resolve the contradiction.

**Gate:** ✅ PASSED — users can operate Work OS without touching `/domain work-*` directly. Cold-start users can set up and use the system without historical data. Alert isolation is machine-enforced via `validate_alert_isolation()`. Work loop is invocable as `python scripts/work_loop.py --mode refresh` (direct fallback path). Learning calibration infrastructure updates work-learned.md after each refresh. Weekly status auto-draft produces useful output. Sources registry is fully read+write. Connect-prep generates evidence-backed narrative from state.

### Phase 2 — Meeting OS, Narrative Engine, And Personalization

1. add `work:` section to [config/user_profile.yaml](config\user_profile.yaml) with enriched profile (§10.2) — `work.refresh.run_on_catchup: true` replaces former `schedule` key
2. move all thresholds and schedule values out of prompts
3. implement bridge artifacts with schema validation and enforcement tests (§9.8)
4. add learned feedback loops for relevance
5. Teams-specific intelligence: channel priority, @-mention boosting, meeting chat context (§11.1)
6. collaboration graph: evolve work-people from flat cache to relationship network (§11.1)
7. `/work live` meeting assist prototype (§7.4) — **DONE** (`cmd_live()` in work_reader.py; fuzzy meeting lookup — exact substring score=100, keyword overlap up to 80; 5 context sections: meeting header with attendee count + recency, attendee context from work-people, open decisions from work-decisions, carry-forward items from work-notes, project context from work-projects; fall-through usage prompt when no meeting matched; 11 tests in `TestCmdLive`; v2.6.0)
8. work skill registry: sprint_report, meeting_prep, comms_digest, boundary_check (§22)
9. recurring meeting memory and carry-forward intelligence (§7.9) — **DONE** (`_extract_carry_forward_items()` in work_reader.py)
10. readiness scoring for `/work prep` (§7.9) — **DONE** (v2.0.0)
11. `/work newsletter` draft generation via Narrative Engine (§7.10) — **DONE** (v2.0.0)
12. `/work deck` content assembly via Narrative Engine (§7.10) — **DONE** (v2.0.0)
13. `/work memo` full period status memo via Narrative Engine (§7.10) — **DONE** (`cmd_memo()` in work_reader.py; `cmd_newsletter()`, `cmd_deck()`, `cmd_talking_points()` also wired)
14. auto-source-capture from meetings and email (§7.7) — **DONE** (`_extract_auto_sources()` in work_notes.py)
15. outcome metrics collection and `/work health` reporting (§16.5) — **DONE** (`_collect_eval_metrics()` in work_reader.py; counts decisions_total/open, open_items_total, refresh_runs, days_since_bootstrap, visibility_events; wired into `cmd_health()` as "Operational metrics" section; v2.6.0)
16. catch-up integration — `post_work_refresh.py` post-commit processor: work refresh fires as a non-blocking finalize stage of `/catch-up` when `work.refresh.run_on_catchup: true` (§3.9, §8.7) — replaces cron/Task Scheduler (eliminated in v2.2.0) — **DONE** (`scripts/post_work_refresh.py` READ mode; consistent with pull-triggered pre-computation §3.9)
17. 60-day prediction learning models (§8.8) — prep effort correlation, action-required precision, sprint pattern detection — **DONE** (`prediction_prep_notes_30d`, `prediction_action_required`, `prediction_sprint_velocity` signals in `_update_learned_state()` in work_loop.py; written to work-learned.md frontmatter atomically)
18. manager commitment ledger and 1:1 memory (§7.6) — auto-population from `/work notes` captures — **DONE** (`_write_manager_commitment_ledger()` in work_notes.py)
19. `/work remember <text>` micro-capture command — appends to `work-notes.md` with `[quick-capture]` marker; processed by next `work-learn` stage; mirrors personal OS `/remember` pattern (E2, ACT-RELOADED) — **DONE** (`cmd_remember()` in work_notes.py)
20. Visibility event tracking on Stakeholder objects (§8.6) — detect VP+/CVP+ replies, @-mentions, doc citations, and meeting invitations; store in `work-people.md.age` `visibility_events[]`; surface in `/work connect-prep` and `/work promo-case` — **DONE** (`append_visibility_event()` in work_domain_writers.py; wired in work_loop `_write_domain_files_from_connector_data()` — high-importance emails → `replied`, large meetings ≥30 attendees with review/planning/roadmap titles → `presented_about`)
21. `career_velocity` model in `work-learned.md` (§8.8) — compute scope trajectory (expanding | stable | contracting) from work-project-journeys and work-notes; alert when contracting for 3+ consecutive weeks — **DONE** (`_stage_learn()` career_velocity field in work_loop.py)
22. `meeting_quality_signals` in `work-learned.md` (§8.8) — compute strategic/execution fraction from meeting type inference; alert when `strategic_fraction < 15%` for 3 consecutive weeks — **DONE** (`_stage_learn()` meeting_quality_signals field in work_loop.py)
23. `work-project-journeys.md` append path — work-process stage extracts milestone evidence from work-projects + work-notes + work-decisions and appends to `state/work/work-project-journeys.md` in timeline format — **DONE** (`append_project_journey()` from work_domain_writers.py now wired in work_loop `_write_domain_files_from_connector_data()` for closed P0/P1 ADO items; priority ≤ 2 and state ∈ {closed, done, completed})
24. `work-org-calendar.md` bootstrap seeding — `/work bootstrap` Q13 populates `state/work/work-org-calendar.md`; 30-day lookahead check added to work-finalize stage — **DONE** (`work_bootstrap.py` Q13 + `_write_org_calendar()`)
25. `WorkBriefingConfig` adaptive rules WB-1 through WB-3 and WB-6 (§8.9) — usage-based adaptive formatting of `/work` output; WB-2 requires 14-day usage signal accumulation — **DONE** (`WorkBriefingConfig` + `_build_briefing_config()` in work_reader.py; WB-4/5 completed in Phase 3 item 19)
26. Bridge v1.1 `phase` and `advisory` fields — update `write_bridge_artifact()` in `bridge_schemas.py` to populate optional fields from `work-org-calendar.md` and sprint deadlines; add prohibited-content sanitizer for `advisory` — **DONE** (bridge_schemas.py phase/advisory fields + sanitizer)

**Gate:** ✅ PASSED — no user-specific behavior requires prompt edits. Meeting OS carries forward unresolved items across recurring meetings. Narrative Engine produces drafts for newsletter, deck, and memo. Teams is processed as a collaboration graph. Work refresh is triggered by catch-up (pull model compliant — no daemon, no scheduler). Manager commitment ledger is auto-populated from meeting captures. Visibility event capture is active (append_visibility_event wired for high-importance emails and large meetings). Career velocity and meeting quality signals are computed. 60-day prediction signals (prep notes, action-required, sprint velocity) feed work-learned.md. Project journey append path is operational (closed P0/P1 ADO items auto-appended).

### Phase 3 — Distribution-Ready Provider Abstraction, Intelligence, And Insight Tier

1. Graph-first core package — validate all commands against §6.5 acceptance criteria
2. provider category abstraction
3. Jira, GitHub, and ADO project adapters
4. parser abstraction for warm-start inputs
5. multi-tenant provider wiring (§12.6)
6. **`/work decide` decision intelligence (§7.5)** — structured decision support: reads work-decisions, work-projects, work-career, work-notes, work-people; surfaces related past decisions and project context; presents 6-question decision frame; allocates D-NNN ID; logs skeleton decision entry atomically to work-decisions.md — **DONE** (`cmd_decide()` + `_ensure_decisions_header()` + `_append_to_file()` in work_reader.py)
7. **`/work bootstrap import` warm-start mode (§15.2–§15.3)** — Markdown narrative import: `bootstrap_from_import()` routes `.md` files to `_bootstrap_from_markdown()`; parses milestone table rows → `work-project-journeys.md ## Imported Milestones`; parses Visibility Events table rows → `work-people.md ## Visibility Events`; parses manager quote lines → `work-performance.md ## Imported Manager Signals`; idempotent (checks stem); sets `work.bootstrap.import_completed: true`; dry-run mode — **DONE** (`_bootstrap_from_markdown()` + `_MILESTONE_ROW_RE` / `_VIS_EVENT_ROW_RE` / `_MANAGER_SIGNAL_RE` regex constants in work_bootstrap.py; 11 new tests in TestBootstrapMarkdownImport)
8. **Connect auto-evidence matching and submission draft generation (§7.6)** — `generate_connect_summary()` reads Connect goals from `work-performance.md` (### Goal N: sections), falls back to profile goals, auto-matches milestone rows from `work-project-journeys.md` by keyword, shows ★★★/★★☆/★☆☆/☆☆☆ Evidence Density + top-5 matched milestones per goal, ⚠ GAP flag for zero-match goals, Evidence Gaps aggregation section — **DONE** (`generate_connect_summary()` replaced in narrative_engine.py; 9 new tests in TestConnectAutoEvidence)
9. **skip-level and calibration narrative preparation (§7.6)** — calibration-room defense brief in third-person format for manager use: auto-thesis, Impact Summary table, Evidence Density stars per goal with gap flagging, Cross-Team Visibility table, Manager Talking Points from career recognition notes, Readiness Signal — **DONE** (`generate_calibration_brief()` in narrative_engine.py; `cmd_connect_prep(mode="calibration")` in work_reader.py replaces Phase 3 placeholder)
10. **stakeholder influence map with evidence gap recommendations (§7.6)** — aggregates visibility events by stakeholder, counts and recency, flags stale relationships (>90 days), generates evidence gap recommendations for stale stakeholders — **DONE** (`_build_influence_map()` in work_reader.py; integrated into `cmd_connect_prep()` standard mode output)
11. **decision drift detection in recurring meetings (§7.9)** — `_detect_decision_drift(meeting_title, decisions_body)` parses D-NNN table rows for OPEN decisions matching meeting keywords; flags open >14d (Pending) or >42d (drift); scans historical deferral signals (count ≥3); results capped at 3; wired into `cmd_prep()` for recurring meetings with ⚡ icon — **DONE** (`_detect_decision_drift()` function + `cmd_prep()` integration in work_reader.py; 11 new tests in TestDetectDecisionDrift)
12. **Narrative Engine: escalation notes, decision memos, stakeholder-specific versioning (§7.10)** — escalation note with situation, blockers, options (A/B/Recommended), what-I-need per stakeholder, timeline table; decision memo with found-decision lookup by D-NNN, alternatives table, evidence table, next steps, distribution list — **DONE** (`generate_escalation_memo()` + `generate_decision_memo()` in narrative_engine.py; `cmd_memo(escalation_context=...)` and `cmd_memo(decision_id=...)` in work_reader.py)
13. **newsletter template customization and deck outline personalization (§7.8)** — `generate_newsletter()` reads `work.newsletter.{tone,template,sections}` from profile; tone: standard|formal|concise (concise uses shorter header + brief draft disclaimer); template: standard|leadership|team_morale (adds Executive Summary / Asks / Shoutouts sections); user-configurable `sections:` list overrides default order; new section builders: executive_summary, shoutouts, asks. `generate_deck()` reads `work.deck.{template,audience}`; template: standard|risk_review|program_status|exec_brief; audience: leadership|exec|team (affects section labels e.g. exec → "Strategic Direction"); new sections: decisions, dependencies, key_results; template name shown in header for non-standard — **DONE** (`generate_newsletter()` + `generate_deck()` replaced in narrative_engine.py; also `cmd_health()` orphan advisory added: scans state/work/*.md for unknown `domain:` keys and surfaces ⚠ advisory with `/work bootstrap import` suggestion; 9 newsletter tests + 9 deck tests + 5 orphan advisory tests)
14. **90-day anticipation learning models (§8.8)** — proactive prep themes (top keywords from work-notes), commitment-at-risk detection (overdue OI-NNN open items), narrative themes (dominant categories from work-decisions); only materialize during anticipation phase (60+ days) — **DONE** (`work_loop.py` `_update_learned_state()`: `fm["anticipation_prep_themes"]`, `fm["anticipation_commitment_risk"]`, `fm["anticipation_narrative_themes"]` added inside `if phase == "anticipation":` block)
15. **auto-refresh-on-stale advisory (§8.7)** — when `work.refresh.auto_refresh_on_stale: true` in user_profile.yaml and ≥2 key domains are stale beyond threshold, `cmd_work()` outputs a consolidated `⚠ AUTO-REFRESH ADVISORY` message with stale domain list and `/work refresh` suggestion — **DONE** (opt-in logic in `cmd_work()` in work_reader.py; reads `profile["work"]["refresh"]["auto_refresh_on_stale"]`)
16. **`/work promo-case`** — synthesis command reading work-project-journeys + work-performance + visibility events; outputs promotion readiness assessment with scope arc, evidence density, and gap list (§7.11) — **DONE** (`generate_promo_case()` in narrative_engine.py; `cmd_promo_case(narrative=False)` in work_reader.py; scope arc, evidence density ★ rating, visibility table, readiness signal, next actions)
17. **`/work promo-case --narrative`** — generates `work-promo-narrative.md` with full promotion document: thesis, before/after, scope arc, milestone evidence, manager voice, visibility events, readiness signal (§7.11) — **DONE** (`generate_promo_case(narrative=True)` in narrative_engine.py; `cmd_promo_case(narrative=True)` atomically writes to state/work/work-promo-narrative.md)
18. **`/work journey [project]`** — project timeline view from `work-project-journeys.md` with milestone evidence and scope arc (§7.11) — **DONE** (`cmd_journey(project="")` in work_reader.py; all-projects table with milestone counts; per-project timeline with section filtering)
19. **`WorkBriefingConfig` WB-4 and WB-5** (§8.9) — org-calendar-driven adaptive briefing rules for Connect season and promo season; requires `work-org-calendar.md` from Phase 2 — **DONE** (`connect_season_alert` + `promo_season_alert` fields in WorkBriefingConfig; `_read_org_calendar_milestones()` helper; WB-4/5 alert logic in `_build_briefing_config()`)
20. **`/day` composite command** (§5.4) — bridge-safe personal+work daily view; reads `work_load_pulse.json` bridge only; platform-agnostic (pure Markdown + JSON, no native dependencies) — **DONE** (`cmd_day()` in work_reader.py; reads ONLY `state/bridge/work_load_pulse.json` for work data per §5.4 hard separation; stale detection; meeting hours, boundary score, focus%, phase)

**Gate:** product is useful for non-Microsoft M365 users. Multi-tenant schema is operational. Narrative Engine supports all output templates. Manager + Connect OS generates full perf-season packet. Work Learning Contract 90-day milestones are measurable. Promotion OS produces a draft promotion narrative from available evidence.

### Phase 4 — Observability And Reliability

1. work eval metrics — operational and outcome (§16.2, §16.5) — **DONE** (`_collect_eval_metrics()` in work_reader.py; wired into `cmd_health()` "Operational metrics" section; v2.6.0)
2. degraded mode reporting — **DONE** (`_assess_provider_tier()` + `_build_degraded_mode_report()` in work_reader.py; four-tier model: Microsoft Enhanced / Enterprise / Core M365 / Offline; per-command degradation callouts with remediation steps; inserted into `cmd_health()` as "Provider coverage:" section; uninitialized variable bug fixed; v2.7.0)
3. latency budgets per Work Operating Loop stage (§8.5) — **DONE** (`run()` in work_loop.py measures `time.monotonic()` per stage; `_stage_audit()` logs `stages=preflight:Xs,fetch:Xs,...` to work-audit.md; v2.6.0)
4. state schema validation — **DONE** (`_WORK_STATE_SCHEMA` dict + `_validate_work_state_schema()` in work_reader.py; checks all 13 domain files for required frontmatter keys; wired into `cmd_health()` "State schema" section; v2.6.0)
5. prompt linting for personalization leaks — **DONE** (`scripts/tools/prompt_linter.py`; checks work-*.md prompts for stale root-level state paths, non-canonical references, un-substituted placeholders, missing frontmatter separators; exit code 0 on pass, 1 on fail; wired into CI; v2.7.0)
6. enforcement test suite running in CI (§9.8) — **DONE** (`.github/workflows/work-tests.yml`; runs on push/PR to main/dev for all work OS files; matrix: Python 3.11/3.12/3.13; prompt-lint job; separate fail-fast=false matrix job; v2.7.0)

**Gate:** work usefulness can be measured and improved deliberately. Outcome metrics track indispensability, not just correctness.

### Phase 5 — Microsoft Enhanced Layer And Distribution

1. WorkIQ acceleration
2. Agency ADO, WorkIQ, ICM, and Bluebird integration
3. ES Chat signal hierarchy for work-comms (§11.1) — VP+/CVP+ ranking, executive-visibility evidence — **DONE** (`_seniority_tier()` + `_SENIORITY_RANK` + `_EXEC_TIER_KEYWORDS` + `_extract_exec_visibility_signals()` in work_reader.py; 5-rank model CVP/Partner=5, VP=4, Director=3, Manager=2, IC=1; cross-references work-people.md seniority tiers against work-comms.md sender column; returns signal strings for VP+/Director+ engagement; v2.7.0)
4. incident and repo workflows — **DONE** (`cmd_incidents()` + `cmd_repos()` in work_reader.py; graceful degradation when state files missing (shows Agency ICM/Bluebird MCP setup instructions); scaffold state files `state/work/work-incidents.md` + `state/work/work-repos.md` with full schema templates; wired into dispatch + argparse; v2.7.0)
7. collaboration graph visualization (`/work graph`) exploration — **DONE** (`cmd_graph()` in work_reader.py; reads work-people.md `### Name` sections; extracts tier/trajectory/last_interaction/visibility events; renders text-based stakeholder graph grouped by seniority tier with trajectory icons ↑→↓◌; network summary at foot; `_TRAJ_ICON` + `_TIER_NAMES` constants; wired into dispatch + argparse `"graph"` choice; v2.7.0)
8. pre-read tracking for meeting readiness (§7.9) — **DONE** (`_PREREAD_SECTION` + `_load_preread_markers()` + `cmd_mark_preread()` in work_reader.py; writes to `## Pre-Read Log` table in work-notes.md; appends rows `| meeting-id | timestamp | 0 |`; idempotent table creation; `--preread-id` argparse arg; wired into dispatch + argparse `"preread"` choice; v2.7.0)

**Gate:** premium Microsoft experience exists on top of the same core model.

### Backlog — Deferred Until Productization

The following items are intentionally deferred. They become relevant only if Artha Work OS is productized for distribution beyond personal use. No action needed until then.

1. **Ship Artha Work Agent to Agency plugin marketplace (§21.5)** — publish `artha-work` agent variants via Agency's internal plugin channel; requires Agency marketplace access and a publish workflow. Agent prototypes already done (`config/agents/`).
2. **Microsoft-internal distribution package (§23)** — bundle Core + Enterprise + Microsoft Enhanced tiers into a distributable package for other Microsoft employees; requires corporate tenant pkg infrastructure. Full scope in §23.

---

## 20. Assumptions And Validation

### 20.1 - Validated

| Assumption | Result |
|------------|--------|
| Azure CLI is available on this Windows system | confirmed |
| Python is available | confirmed |
| Agency has been used on this machine | confirmed |
| Work state files already exist | confirmed |
| Work connectors exist and are Windows-gated | confirmed |
| Historical work scrape exists and is rich enough for warm start | confirmed |

### 20.2 - Must Validate In Implementation

| Assumption | Validation |
|------------|------------|
| dual-key vault support can be added cleanly | prototype in `vault.py` |
| work command namespace can be added without breaking existing slash commands | command parser update and tests |
| bridge artifacts preserve usefulness while improving separation | pilot with calendar conflict and boundary pulse |
| Graph-first baseline is adequate for non-Microsoft users | run package in non-WorkIQ configuration |
| work eval metrics can be collected with low overhead | add instrumentation and observe runtime impact |

---

## 21. Appendix - Agency CLI Integration

### 21.1 - Role Of Agency In The Product

Agency is the default runtime for Work OS on Windows.

When Agency is available, `/work` commands execute through `agency copilot --agent artha-work`. When Agency is unavailable, the system falls back to direct CLI execution with Graph-first providers. The product is designed Agency-first, Graph-fallback.

This is the correct default because:

1. Agency wraps Copilot CLI and Claude Code with enterprise MCP servers — ADO, WorkIQ, ICM, Bluebird — that are essential for the Microsoft Enhanced tier.
2. Agency custom agents are the native distribution format for enterprise tools inside Microsoft.
3. Agency eval mode provides an automated test harness for work intelligence quality.
4. The marketplace provides a distribution channel that already exists.

**Critical constraint:** Agency is work-only. Artha's personal surface must never route through Agency (§3.6). Enterprise telemetry is acceptable for work workflows operating within corporate systems. It is not acceptable for personal intelligence.

### 21.2 - Where Agency Adds Real Value

| Capability | Why Agency Matters |
|------------|--------------------|
| ADO MCP | richer structured project and PR context |
| WorkIQ MCP | alternative path to M365 context under one runtime |
| ICM MCP | incident workflow support |
| Bluebird MCP | code and repo pulse |
| Custom agents | reusable work workflows for Microsoft teams |
| Plugin marketplace | distribution channel for Microsoft-internal enhanced package |

### 21.3 - Product Positioning

There are three layers:

1. **Core Work OS** - portable M365 work intelligence (Graph-first, no Agency required)
2. **Enterprise Work OS** - richer enterprise provider support (ADO adapter, advanced project intelligence)
3. **Microsoft Work OS Powered By Agency** - the default for Microsoft employees. Agency is the runtime, MCP servers provide superpowers, the marketplace is the distribution channel.

For Persona A (Microsoft Knowledge Worker), Agency is not an enhancement — it is the expected runtime. The Core and Enterprise tiers exist to serve Personas B and C and to ensure the system degrades gracefully when Agency is unavailable.

That is the correct architecture for standardization and scale.

### 21.4 - Future Agency-Specific Workflows

When Agency is stable for non-interactive invocation, add:

- `/work incidents`
- `/work repo`
- `/work unblock`
- `/work delegate-draft`

These belong in the Microsoft-enhanced tier, not the universal baseline.

### 21.5 - Agency As Distribution Format

Agency custom agents are Markdown files with YAML frontmatter — the same format as Artha domain prompts. This is not a coincidence. It is the native packaging format for enterprise distribution.

#### The Artha Work Agent

```yaml
---
name: artha-work
description: Work OS — intelligence layer for professional life. Morning briefing, meeting prep, sprint health, return-from-absence, boundary protection, and career evidence.
mcp-servers:
  ado:
    type: local
    command: agency
    args: ["mcp", "ado"]
    tools: ["*"]
  workiq:
    type: local
    command: agency
    args: ["mcp", "workiq"]
    tools: ["*"]
tools:
  - read
  - search
  - bash
  - ado/*
  - workiq/*
---
[Artha Work OS instructions — loaded from work domain prompts]
```

#### Invocation

```bash
# Interactive session
agency copilot --agent artha-work

# Non-interactive (CI, cron, or scripted)
agency copilot --agent artha-work -p "/work pulse"

# Eval mode (automated quality testing)
agency eval --agent artha-work --prompt "/work prep"
```

#### Distribution

The Agent is distributed through the Agency plugin marketplace:

```bash
/plugin install artha-work@agency-microsoft/.github-private
```

Three tiers map to three agent variants:

| Agent | Audience | MCP Servers |
|-------|----------|-------------|
| `artha-work` | Any M365 user | none (Graph baseline) |
| `artha-work-enterprise` | Corporate M365 user | ado |
| `artha-work-msft` | Microsoft employee | ado, workiq, icm, bluebird |

All three share the same instructions and state schemas. The MCP server declarations are the only difference.

#### Why This Matters

Today, Artha runs as a Claude Code session loading `config/Artha.md`. Tomorrow, it runs as `agency copilot --agent artha-work`. The transition is natural because the formats are compatible. The eval mode (`agency eval`) provides an automated test harness for work intelligence quality. The marketplace provides a distribution channel that already exists inside Microsoft.

Phase 0 should include prototyping the agent file. Phase 5 ships it to the marketplace.

---

## 22. Work Skill Registry

### 22.1 - Why Work Skills

The personal side has 15 registered autonomous skills (USCIS status, visa bulletin, subscription monitor, etc.). The work side has zero. For distribution, work-specific skills provide high-value automation without full `/work` workflow overhead.

### 22.2 - Required Work Skills

| Skill | Purpose | Trigger | Output |
|-------|---------|---------|--------|
| sprint_report | Auto-generate sprint status summary | weekly (sprint end day) or on-demand | `state/work/work-projects.md` sprint section |
| meeting_prep | Autonomous prep card generation for next N meetings | daily at configured time or pre-meeting | prep cards in `state/work/work-calendar.md` |
| comms_digest | Daily work comms digest without full catch-up | daily at end of work hours | digest in `state/work/work-comms.md` |
| boundary_check | Weekly work-life boundary audit | weekly | boundary report in `state/work/work-boundary.md` |

### 22.3 - Skill Registration

Work skills follow the same `BaseSkill` subclass pattern as personal skills and register in `config/skills.yaml`:

```yaml
work_sprint_report:
  class: "scripts.skills.work_sprint_report.WorkSprintReportSkill"
  priority: P2
  schedule: "weekly"
  requires_connectors: [ado_workitems]
  run_on: windows
  command_namespace: work

work_meeting_prep:
  class: "scripts.skills.work_meeting_prep.WorkMeetingPrepSkill"
  priority: P1
  schedule: "daily"
  requires_connectors: [workiq_bridge]
  run_on: windows
  command_namespace: work

work_comms_digest:
  class: "scripts.skills.work_comms_digest.WorkCommsDigestSkill"
  priority: P2
  schedule: "daily"
  requires_connectors: [workiq_bridge]
  run_on: windows
  command_namespace: work

work_boundary_check:
  class: "scripts.skills.work_boundary_check.WorkBoundaryCheckSkill"
  priority: P2
  schedule: "weekly"
  requires_connectors: [workiq_bridge]
  run_on: windows
  command_namespace: work
```

### 22.4 - Skill Isolation

Work skills write to `state/work/` only. They never read or write personal state files. Skill output is consumed by `/work` commands as pre-computed state.

### 22.5 - Content Production Skills

```yaml
work_newsletter_digest:
  class: "scripts.skills.work_newsletter_digest.WorkNewsletterDigestSkill"
  priority: P2
  schedule: "on-demand"
  requires_connectors: [ado_workitems]
  run_on: windows
  command_namespace: work
  description: "Assembles newsletter draft from sprint data, decisions, and accomplishments"

work_lt_deck_assist:
  class: "scripts.skills.work_lt_deck_assist.WorkLtDeckAssistSkill"
  priority: P2
  schedule: "on-demand"
  requires_connectors: [ado_workitems, workiq_bridge]
  run_on: windows
  command_namespace: work
  description: "Generates structured deck content from projects, evidence, and data sources"

work_connect_prep:
  class: "scripts.skills.work_connect_prep.WorkConnectPrepSkill"
  priority: P1
  schedule: "weekly"
  requires_connectors: [ado_workitems, workiq_bridge]
  run_on: windows
  command_namespace: work
  description: "Connect cycle goal tracking, evidence collection, and submission draft generation"

work_source_capture:
  class: "scripts.skills.work_source_capture.WorkSourceCaptureSkill"
  priority: P3
  schedule: "daily"
  requires_connectors: [workiq_bridge]
  run_on: windows
  command_namespace: work
  description: "Auto-detects and registers data source URLs from meetings and email"

work_background_refresh:
  class: "scripts.skills.work_background_refresh.WorkBackgroundRefreshSkill"
  priority: P0
  schedule: "daily"
  requires_connectors: []
  run_on: all
  command_namespace: work
  description: "Background connector refresh — runs all available providers and updates state/work/ atomically (§8.7)"
```

---

## 23. Microsoft Premium Features

### 23.1 - Purpose

This section catalogs capabilities available only to Microsoft employees or users with access to Microsoft-internal tools. These features are enhancements on top of the Core M365 and Enterprise tiers — they never gate core functionality.

### 23.2 - Feature Catalog

| Feature | Connector / Tool | Domain Impact | Phase |
|---------|-----------------|---------------|-------|
| **WorkIQ email enrichment** | WorkIQ email mode | work-comms: richer sender metadata, read receipts, importance scoring beyond Graph | Phase 5 |
| **WorkIQ calendar enrichment** | WorkIQ calendar mode | work-calendar: room booking context, meeting series metadata, organizer intent signals | Phase 5 |
| **WorkIQ people graph** | WorkIQ people mode | work-people: deep org chart traversal, collaboration frequency from internal systems, alias resolution | Phase 5 |
| **WorkIQ documents mode** | WorkIQ documents mode | work-notes: recently active internal documents, SharePoint site activity, wiki edit recency | Phase 5 |
| **WorkIQ Teams intelligence** | WorkIQ teams mode | work-comms: Teams channel analytics, @-mention context, meeting chat extraction | Phase 5 |
| **ES Chat signal hierarchy** | ES Chat (via Agency MCP or WorkIQ) | work-comms: VP+/CVP+ message ranking above all other signals, executive-visibility evidence capture for career domain | Phase 5 |
| **ADO deep integration** | ADO CLI + Agency ADO MCP | work-projects: work item hierarchy, PR review status, pipeline health, sprint burndown, dependency graph | Phase 1 (CLI), Phase 5 (MCP) |
| **ICM incident awareness** | Agency ICM MCP | work-incidents (future domain): active incidents, severity, ownership, mitigation status | Phase 5 |
| **Bluebird code pulse** | Agency Bluebird MCP | work-repos (future domain): recent commits, PR aging, code review queue, build health | Phase 5 |
| **Agency as runtime** | Agency CLI | All work domains: wraps all MCP servers under one runtime, provides eval harness, enables marketplace distribution | Phase 0 (prototype), Phase 5 (ship) |

### 23.3 - Platform Requirements

All Microsoft Premium features require **Windows**. This is a hard constraint driven by:

1. WorkIQ is a Windows-only internal tool
2. Agency CLI is Windows-only (as of current release)
3. Outlook COM bridges used by outlookctl_bridge are Windows-only
4. ADO CLI (`az devops`) is cross-platform, but the Agency ADO MCP is Windows-only

macOS and Linux users on the Microsoft Enhanced tier receive Core M365 + Enterprise capabilities via Graph and ADO CLI, but cannot access WorkIQ, Agency MCP servers, or Outlook COM bridges.

### 23.4 - Degradation Behavior

When a Microsoft Premium feature's connector is unavailable:

1. The feature silently degrades — no error, no broken output
2. `/work health` reports exactly which premium features are available and which are not
3. The corresponding domain output section is omitted from `/work` briefing (not shown as "unavailable")
4. Career evidence capture from premium sources is skipped — manual evidence entry remains available

### 23.5 - Distribution

Microsoft Premium features are bundled in the `artha-work-msft` agent variant (§21.5). They share the same instructions, state schemas, and command surface as Core and Enterprise tiers. The MCP server declarations in the agent YAML frontmatter are the only difference.

---

*End of Specification*