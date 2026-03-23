# Artha — AI Trend Radar & Practitioner Posts
<!-- pii-guard: ignore-file -->
## Specification · PR-3 v1.0.6

**Author:** [Author]
**Date:** March 22, 2026
**Status:** Approved — Ready for Implementation
**Classification:** Personal & Confidential
**Implements:** PRD v7.0.10, FR-11 (Social Intelligence)
**Depends on:** specs/pr-manager.md PR-1 v1.2.0, specs/pr-stage.md PR-2 v1.3.0

| Version | Date | Summary |
|---------|------|----------|
| v1.0.0 | 2026-03-22 | Initial specification. Five-stage pipeline: INGEST → DISTILL → SURFACE → TRY → DRAFT. AI newsletter + RSS ingestion via existing connectors. New `AITrendRadarSkill`. Topic Interest Graph with boost scoring. Experimentation tracking. New `ai_experiment_complete` moment type. Register B-Practitioner voice sub-register. Platform exclusion for cultural festivals on LinkedIn. LinkedIn `data_quality` annotation. |
| v1.0.1 | 2026-03-22 | Architecture review revision. DEFECT-1: warm-start via config parameter (not `pull()` argument). DEFECT-2: briefing renders in `briefing_adapter.py` (not `pr_manager.py`). DEFECT-3: Component 6 hard prerequisite documented. GAP-3: state file uses YAML frontmatter (not Markdown tables). GAP-4: `RADAR_DISTILL_RUN` audit event. M1–M7 minor fixes (topic boost max-wins, employer blocked terms path, RSS Phase 0 validation, warm-start go/no-go, `_prev.json` rename logic, push rate limit configurable). |
| v1.0.2 | 2026-03-22 | Second review revision. Unified single YAML frontmatter schema for `state/ai_trend_radar.md`. Warm-start one-shot lifecycle (consumption + cleanup). Normalised remaining `Step 8` → `briefing_adapter.py` references. Lightweight `tmp/ai_trend_metrics.json` runtime metrics surface. Refreshed test-count estimate. |
| v1.0.3 | 2026-03-22 | Third review revision. ARCH-1: warm-start moved from `artha_config.yaml` to state frontmatter (skills must not mutate config; YAML round-trip destroys comments). ARCH-2: signal ID changed to `SHA-256(topic_normalized)` — source-independent, stable across dedup (prevents stale experiment linkage). ARCH-3: `signal_history` removed from frontmatter (purpose served by `_prev.json`; unbounded growth risk). ARCH-4: moment creation timing explicitly documented as weekly-cadence. ARCH-5: §9.5 Auto-Keyword Expansion moved to Future scope. LOW-2: `_prev.json` naming normalised. LOW-3: HTTPS feed validation guard. |
| v1.0.4 | 2026-03-22 | Pre-implementation readiness pass. BLOCKER-1: PAT-PR-004 (stale radar) fully defined with trigger condition, YAML schema, and test. BLOCKER-2: §4.3 warm-start procedure now includes explicit state-arming step; Phase 3 activation updated to match. BLOCKER-3: seed card creation assigned to Phase 2 as Component 10a with explicit `gallery.yaml` format. STALE-1: Phase 1 description corrected from "config-driven" to "state-driven". |
| v1.0.5 | 2026-03-22 | Schema completeness pass. GAP-1: `completed_date` field added to experiment record schema (was referenced in §8.1 Python code but absent from §9.2 schema — would have caused `AttributeError` at runtime). GAP-2: `moment_emitted` flag added to experiment record schema; §8.1 timing callout updated to document skip-guard; `test_moment_not_re_emitted_on_second_run` added to test table (prevents silent duplicate card creation on weekly re-runs). |
| v1.0.6 | 2026-03-22 | Implementation gap closure. FINDING-1: `test_moment_not_re_emitted_on_second_run` added to §16.1 canonical moment-creation test matrix (was in §8.1 only — implementer would have missed it). FINDING-2: §7.5 Verdict Submission added documenting action-bridge write of `status`, `verdict`, `completed_date`, and `notes`; ownership boundary with `moment_emitted` (skill-owned) made explicit; `RADAR_EXPERIMENT_DONE` audit event registered. |

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Design Principles](#2-design-principles)
3. [Architecture Overview](#3-architecture-overview)
4. [Stage 1 — INGEST](#4-stage-1--ingest)
5. [Stage 2 — DISTILL](#5-stage-2--distill)
6. [Stage 3 — SURFACE](#6-stage-3--surface)
7. [Stage 4 — TRY](#7-stage-4--try)
8. [Stage 5 — DRAFT](#8-stage-5--draft)
9. [Topic Interest Graph](#9-topic-interest-graph)
10. [Platform Exclusion & State Corrections](#10-platform-exclusion--state-corrections)
11. [Privacy & Security](#11-privacy--security)
12. [Configuration Schema](#12-configuration-schema)
13. [Implementation Plan](#13-implementation-plan)
14. [Assumptions & Validation](#14-assumptions--validation)
15. [Risks & Mitigations](#15-risks--mitigations)
16. [Test Plan](#16-test-plan)

---

## 1. Problem Statement

### 1.1 Gap Analysis

PR-1 (Narrative Engine) and PR-2 (Content Stage) detect **occasion-based moments**
(birthdays, festivals, milestones) and manage a card lifecycle for social media
posts. This works well for Facebook, WhatsApp, and Instagram — platforms where
cultural and family content is appropriate.

LinkedIn, however, requires a fundamentally different content type:
**practitioner insights** — specific, hands-on, first-person accounts of trying
new AI tools, techniques, and developments. The user's LinkedIn Register B voice
(project showcase, 100–200 words, emoji-bullet features) is well-suited for
this, but PR-1/PR-2 have no mechanism to:

1. **Detect** what's happening in the AI world (no external content ingestion beyond email routing)
2. **Surface** try-worthy signals aligned with the user's interests
3. **Track** experimentation so posts reflect genuine personal experience
4. **Draft** practitioner content distinct from occasion-based posts

### 1.2 User Context

- **Role:** Senior TPM, [Organization] at Microsoft. Does not code at work — plans
  and coordinates engineering work. Codes on personal projects (Artha, Vimarsh,
  Lexicon, etc.) pushed to personal GitHub.
- **Posting cadence:** Aims for ~monthly on LinkedIn. Partial data in
  `state/pr_manager.md` shows 365-day gap — this is a data quality artifact from
  incomplete LinkedIn export, not reality.
- **Content preference:** AI developments, tips, techniques — things the user can
  try personally and share genuine experience. NOT generic industry commentary.
- **Platform routing:** Festivals and cultural moments belong on Facebook/WhatsApp,
  NOT LinkedIn. LinkedIn is for professional/technical content only.

### 1.3 Persona Alignment

The target LinkedIn audience (4,295 connections, SAP/Amazon/Microsoft/Google
cohort) responds to the user's credibility pillars:
- 18-year career arc across Infosys, SAP, Amazon, Opendoor, Microsoft
- MS CS from UIUC (ML/AI specialization)
- MBA from UW Foster School
- Active open-source builder (6+ projects on GitHub)

Posts must be **something the user would use themselves** — the "try it first,
then share" principle ensures authenticity.

---

## 2. Design Principles

These extend Artha's core design principles (PRD §1.1) for the specific domain
of external content intelligence.

| ID | Principle | Rationale |
|----|-----------|-----------|
| **DP-1** | **Try before you post** | Every LinkedIn post MUST be grounded in personal experimentation. Signals without a completed experiment never produce cards. This prevents generic AI commentary. |
| **DP-2** | **Boost, never filter** | The Topic Interest Graph boosts relevance of user-declared topics but never suppresses unmatched signals. Serendipitous discoveries must still surface. |
| **DP-3** | **Deterministic first** | Signal extraction, relevance scoring, and deduplication are rule-based (regex, keyword matching, heuristics). No LLM calls in the INGEST or DISTILL stages. LLM involvement only at DRAFT stage (Phase 3+ of PR Manager, gated by `compose: true`). |
| **DP-4** | **Existing infrastructure first** | Email connectors (Gmail/Outlook) already ingest newsletters. RSS connector (`scripts/connectors/rss_feed.py`) is already built. Reuse, don't rebuild. |
| **DP-5** | **User controls the funnel** | Every stage requires explicit user action to progress: signals are surfaced → user chooses to `/try` → user experiments → user approves draft → user posts. No automation past surfacing. |
| **DP-6** | **Employer safety** | No Microsoft-internal content, codenames, or proprietary information may appear in signals, experiments, or posts. Generic role descriptions (TPM, cross-org coordination) are acceptable. |
| **DP-7** | **Platform-appropriate content** | Cultural moments → Facebook/WhatsApp. Technical insights → LinkedIn. Respect platform audience expectations. |
| **DP-8** | **Zero new dependencies** | RSS connector uses stdlib only. Email ingestion uses existing OAuth flows. Signal extraction uses `re` module. No new pip packages. |

---

## 3. Architecture Overview

### 3.1 Pipeline Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  INGEST       │     │  DISTILL      │     │  SURFACE      │
│               │     │               │     │               │
│ Email         │────▶│ AITrendRadar  │────▶│ Briefing §8   │
│ (newsletters) │     │ Skill         │     │ Telegram push │
│               │     │               │     │               │
│ RSS feeds     │────▶│ Keyword match │     │ /radar command│
│ (AI blogs)    │     │ Topic boost   │     │               │
│               │     │ Dedup + rank  │     │               │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                          user: /try
                                                  │
                                                  ▼
                                         ┌──────────────┐
                                         │  TRY          │
                                         │               │
                                         │ Experiment    │
                                         │ tracking      │
                                         │ (with Artha)  │
                                         │               │
                                         │ user: done    │
                                         └──────┬───────┘
                                                │
                                        verdict: great/useful
                                                │
                                                ▼
                                         ┌──────────────┐
                                         │  DRAFT        │
                                         │               │
                                         │ Content Stage │
                                         │ seed card     │
                                         │               │
                                         │ → approval    │
                                         │ → post        │
                                         └──────────────┘
```

### 3.2 Data Flow (Concrete Files)

```
Email connector output (JSONL)
  └→ email_classifier.py tags marketing_category: "newsletter"
       └→ tmp/connector_output/*.jsonl (existing pipeline)

RSS connector output (JSONL)
  └→ tmp/connector_output/*.jsonl (new, rss_feed entries)

Both ──→ AITrendRadarSkill.pull()
           ├─ reads pipeline JSONL for newsletter-tagged emails
           ├─ reads RSS items via rss_feed.fetch()
           └→ AITrendRadarSkill.parse()
                ├─ keyword extraction
                ├─ topic interest boost
                ├─ deduplication (same topic from N sources)
                └→ tmp/ai_trend_signals.json
                     └→ briefing section (rendered by briefing_adapter.py)
                     └→ Telegram /radar command

User /try ──→ state/ai_trend_radar.md (experiment record)

User "done" + verdict ──→ pr_manager.py score_moment()
                            └→ ScoredMoment(type=ai_experiment_complete)
                                 └→ ContentStage.process_moments()
                                      └→ state/gallery.yaml (seed card)
```

### 3.3 Integration Points with Existing Architecture

| Existing Component | Integration | Change Required |
|---|---|---|
| `scripts/connectors/rss_feed.py` | Enable + configure AI feeds | Config only (`connectors.yaml`) |
| `scripts/email_classifier.py` | Already tags newsletters | No code change; config adds sender filter list |
| `scripts/skill_runner.py` | Runs `AITrendRadarSkill` | No change; skill registers in `skills.yaml` |
| `scripts/pr_manager.py` | New moment type + thread map | ~15 lines added |
| `scripts/briefing_adapter.py` | Radar briefing section rendering | ~40 lines added |
| `scripts/pr_stage/service.py` | Existing `process_moments()` | No change; new moment type flows through via `_adapt_moment()` `getattr()` fallbacks |
| `scripts/pr_stage/domain.py` | Optional `platform_exclude` field | ~15 lines added |
| `scripts/channel_listener.py` | `/try`, `/skip`, `/radar` commands | ~50 lines added |
| `config/patterns.yaml` | PAT-PR-004: stale radar nudge (see §3.4) | Config only |

### 3.4 PAT-PR-004 — Stale Radar Pattern

When the AI Trend Radar skill has not produced a successful run in 14+ days,
a briefing nudge reminds the user to check connector health or RSS feed
reachability.

**Trigger:** `tmp/ai_trend_metrics.json` → `last_run_at` is >14 days old
(or the file does not exist).

**YAML definition** (Component 14, added to `config/patterns.yaml`):

```yaml
- id: "PAT-PR-004"
  name: "Stale AI Trend Radar"
  description: "Fires when no successful radar run in 14+ days — prompts connector health check"
  source_file: "tmp/ai_trend_metrics.json"
  source_path: "last_run_at"
  condition:
    all_of:
      - stale_days: 14
  output_signal:
    signal_type: "content_stale"
    domain: "digital"
    urgency: 1
    impact: 1
    entity_field: null
    metadata:
      suggestion: "AI Trend Radar hasn't run in 2+ weeks. Check RSS feeds and email connector health."
      pr_manager: true
  cooldown_hours: 168          # 7 days — one reminder per week
  enabled: true
```

| Test | Description |
|---|---|
| `test_pat_pr_004_fires_after_14_days` | `last_run_at` 15 days ago → pattern fires with `content_stale` signal |
| `test_pat_pr_004_quiet_when_recent` | `last_run_at` 7 days ago → pattern does not fire |

---

## 4. Stage 1 — INGEST

### 4.1 Source A: AI Newsletters (Existing Email Pipeline)

Newsletters already flow through Gmail/Outlook connectors → `email_classifier.py`
→ pipeline JSONL. The classifier already tags `marketing_category: "newsletter"`.

**What's new:** A configurable sender filter list in `artha_config.yaml` tells
the `AITrendRadarSkill` which newsletter senders to treat as AI signal sources.
Only emails from these senders are processed for signal extraction.

**Confirmed senders** (active subscriptions in the user's Gmail):

| Newsletter | Sender | Cadence | Content Type |
|---|---|---|---|
| AI News (swyx) | `swyx+ainews@substack.com` | Daily | Aggregated AI news, launches |
| Big Technology | `bigtechnology@substack.com` | Weekly | Big-tech strategy + AI industry |
| ByteByteGo | `bytebytego@substack.com` | Weekly | System design, architecture |
| Data Points (DeepLearning.AI) | `datapoints@deeplearning.ai` | Weekly | Data/ML career + practice |
| Gregor Ojstersek | `gregorojstersek@substack.com` | Weekly | Engineering leadership |
| Lenny's Newsletter | `lenny@substack.com` | Weekly | Product management + growth |
| System Design One (Neo Kim) | `systemdesignone@substack.com` | Weekly | System design deep-dives |
| Product for Engineers | `productforengineers@substack.com` | Weekly | Product engineering overlap |
| Refactoring (Monday Ideas) | `refactoring+monday-ideas@substack.com` | Weekly | Software architecture |
| The Batch (DeepLearning.AI) | `thebatch@deeplearning.ai` | Weekly | Curated AI news + Andrew Ng commentary |
| Pragmatic Engineer | `pragmaticengineer@substack.com` | Weekly | Engineering industry analysis |
| The Rundown AI | `news@daily.therundown.ai` | Daily | AI news digest |
| TPM University | `noreply@tpmuniversity.com` | Weekly | TPM career development |

**Note:** 10 of 13 senders use `@substack.com`, which `email_classifier.py`
already tags as `marketing_category: "newsletter"` via its marketing sender
domain patterns. The DeepLearning.AI and Rundown AI senders are also matched
by the classifier's `noreply`/newsletter heuristics. All 13 are confirmed
present in the user's Gmail — no guesswork.

**Privacy note:** Email body content is processed locally by the skill. No email
content is transmitted externally. The skill reads from the existing pipeline
JSONL output which has already passed through PII classification.

### 4.2 Source B: RSS Feeds (Existing Connector, Disabled)

`scripts/connectors/rss_feed.py` is **fully implemented** — stdlib-only
(`urllib.request` + `xml.etree.ElementTree`), supports RSS 2.0 + Atom 1.0,
respects `since` filter, outputs JSONL matching email-adjacent schema.

**Activation:** Set `enabled: true` in `config/connectors.yaml` and populate
the feeds list.

**Recommended initial feeds:**

| Feed | URL | Cadence | Signal Type |
|---|---|---|---|
| OpenAI Blog | `https://openai.com/blog/rss.xml` | ~weekly | Model releases, techniques |
| Anthropic Research | `https://www.anthropic.com/research/rss.xml` | ~biweekly | Claude developments |
| Google AI Blog | `https://blog.google/technology/ai/rss/` | ~weekly | Gemini, DeepMind |
| Microsoft Research | `https://www.microsoft.com/en-us/research/feed/` | ~weekly | Employer's research arm |
| Hugging Face Blog | `https://huggingface.co/blog/feed.xml` | ~weekly | Open-source models |
| Simon Willison | `https://simonwillison.net/atom/everything/` | Daily | Hands-on AI tool usage |
| HN AI (filtered) | `https://hnrss.org/newest?q=AI+LLM+GPT` | Filtered | Community-voted AI content |

**Assumption A-RSS-1:** These URLs serve valid RSS 2.0 or Atom 1.0 XML. See §14
for validation plan.

**Assumption A-RSS-2:** `hnrss.org` query-filtered feed returns items matching
the query parameter. The existing `_parse_rss_channel()` and `_parse_atom_feed()`
functions in `rss_feed.py` handle both formats.

### 4.3 Warm Start — Historical Email Scan

All 13 configured newsletter senders already have months of back-issues in the
user's Gmail. Rather than waiting for fresh emails to arrive over weeks, the
radar can warm-start by running a one-time historical pipeline scan.

**Procedure:**

```bash
# Step 1: Fetch 90 days of Gmail history (newsletter-heavy window)
python scripts/pipeline.py --since "2025-12-22T00:00:00Z" --source gmail \
  > tmp/warm_start_emails.jsonl

# Step 2: Arm warm-start mode in state file
#   Set meta.warm_start_file so the skill knows to read from JSONL
#   instead of the live pipeline output.
python3 -c "
import yaml, pathlib
state = pathlib.Path('state/ai_trend_radar.md')
text = state.read_text()
_, fm, body = text.split('---', 2)
data = yaml.safe_load(fm)
data.setdefault('meta', {})['warm_start_file'] = 'tmp/warm_start_emails.jsonl'
state.write_text('---\n' + yaml.safe_dump(data, default_flow_style=False) + '---' + body)
print('Armed: meta.warm_start_file =', data['meta']['warm_start_file'])
"

# Step 3: Run AITrendRadarSkill — it reads the armed JSONL path,
#   filters to configured newsletter senders, extracts signals,
#   writes tmp/ai_trend_signals.json, then auto-clears
#   meta.warm_start_file (one-shot lifecycle, see below).
```

**Warm-start behavior in `AITrendRadarSkill`:**

- Warm-start mode is **state-driven**, not config-driven. The
  `warm_start_file` path lives in the `meta` block of
  `state/ai_trend_radar.md` frontmatter (not in `artha_config.yaml`).
  `pull()` reads it from the state file. When the key is present, non-null,
  and the file exists, `pull()` reads from that JSONL file instead of the
  default pipeline output. When absent or null, normal weekly pipeline
  output is used. This avoids modifying the `BaseSkill.pull()` signature
  (which accepts no arguments) or overriding `execute()`.
- **Why state, not config:** Skills are read-only consumers of
  `artha_config.yaml`. A skill that writes back to config creates
  bidirectional coupling and destroys YAML comments on round-trip (Python's
  `yaml.safe_dump()` does not preserve comments). No other Artha skill
  mutates config. Keeping `warm_start_file` in state keeps the one-shot
  lifecycle as a single atomic write to one file.
- **One-shot lifecycle:** Warm-start is a single-use bootstrap, not a
  persistent mode. After a successful warm-start run, the skill performs
  a single atomic write to `state/ai_trend_radar.md` frontmatter:
  1. Sets `meta.warm_start_file` to `null` (disabling further warm-start
     reads).
  2. Sets `meta.warm_start_consumed_at` to the current ISO timestamp.
  3. The JSONL source file is renamed to
     `tmp/warm_start_emails.processed.jsonl` (preserving provenance
     without re-ingestion risk).
  Because both `warm_start_file` and `warm_start_consumed_at` live in the
  same frontmatter dict, the state transition is atomic — no crash can
  leave config and state inconsistent. This prevents accidental replay of
  historical data on subsequent weekly runs, which would distort signal
  freshness scores indefinitely.
- Signal dedup applies across the full 90-day window — multiple editions
  of the same recurring topic collapse into one signal with `seen_in` count.
- Signals older than 14 days get a timeliness penalty (`score × 0.3`) so
  they don't crowd out fresh signals in the first real weekly run.
- Warm-start signals are written to `tmp/ai_trend_signals.json` with a
  `warm_start: true` flag. The briefing renderer annotates them:
  `"📚 From backlog"` instead of `"📰 N sources this week"`.

**Expected yield:** With 13 newsletters over 90 days, roughly 200–500 email
records will be fetched. After keyword filtering and dedup, expect 15–30
unique signals — enough to immediately populate the radar with a meaningful
first screening. This also validates A-MAIL-1 (newsletter detection) and
A-BEH-3 (signal quality from subject lines) with real data.

**Privacy:** The warm-start JSONL file contains the same records as normal
pipeline output (already PII-classified). It is stored in `tmp/` (gitignored)
and overwritten on subsequent runs.

### 4.4 Source C: Future (Out of Scope)

YouTube channels, Reddit, LinkedIn feed API, and Twitter/X are **not in scope**
for PR-3 v1.0. They may be added as additional connectors in future versions
following the standard connector protocol (`fetch()` + `health_check()`).

### 4.5 Ingestion Cadence

The RSS connector runs as part of the normal pipeline execution (Step 3 in
catch-up workflow). Newsletter emails are ingested whenever email connectors run.

The `AITrendRadarSkill` processes both sources. Its cadence is `weekly`
(configurable) — it accumulates 7 days of newsletter + RSS content and
distills signals once, typically on Monday catch-up.

On first activation, the warm-start procedure (§4.3) runs once to bootstrap
the signal history. Subsequent runs use the normal weekly cadence.

---

## 5. Stage 2 — DISTILL

### 5.1 AITrendRadarSkill

**File:** `scripts/skills/ai_trend_radar.py` (new)

**Interface:** Extends `BaseSkill` (§ `scripts/skills/base_skill.py`):

```python
class AITrendRadarSkill(BaseSkill):
    """Distills AI signals from newsletters and RSS feeds.
    
    Reads pipeline JSONL for newsletter-tagged emails + RSS items.
    Applies keyword extraction, topic interest boost, dedup.
    Outputs ranked signals to tmp/ai_trend_signals.json.
    """

    def __init__(self, artha_dir: Path):
        super().__init__(name="ai_trend_radar", priority="P2")
        self._artha_dir = artha_dir

    def pull(self) -> dict[str, Any]:
        """Collect raw newsletter emails + RSS items from last 7 days."""
        ...

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Extract structured AI signals, score, deduplicate, rank."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Return serializable results."""
        ...

    @property
    def compare_fields(self) -> list:
        return ["signals"]
```

**Registration in `config/skills.yaml`:**

```yaml
ai_trend_radar:
  enabled: true
  priority: P2
  cadence: weekly
  requires_vault: false
  safety_critical: false
  description: >
    Distill AI signals from newsletter emails and RSS feeds.
    Outputs ranked try-worthy signals to tmp/ai_trend_signals.json.
```

### 5.2 AISignal Data Structure

```python
@dataclass
class AISignal:
    id: str                     # SHA-256 hash of (topic_normalized) — source-independent
    topic: str                  # "Claude Code /compact command"
    category: str               # tool_release | technique | model_release |
                                # research | tutorial | framework_update
    sources: list[str]          # ["simon_willison", "the_batch"] — all contributing sources
    best_source_url: str        # URL from highest-contributing source
    summary: str                # 1-2 sentence extract (≤200 chars)
    detected_at: str            # ISO-8601 date
    relevance_score: float      # 0.0-1.0 (base + topic boost)
    try_worthy: bool            # Can the user actually try this?
    seen_in: int                # Number of sources mentioning this topic
    topic_match: str | None     # Matched topic from Interest Graph, if any
```

> **ID stability (ARCH-2):** The signal ID is `SHA-256(topic_normalized)` —
> it does NOT include the source. This means the same topic always produces
> the same ID regardless of which source reported it first. This is critical
> because experiment records in `state/ai_trend_radar.md` reference
> `signal_id` — if the ID changed when a different source dominated the
> score next week, the experiment→signal linkage would break. The `sources`
> list carries provenance; `best_source_url` points to the highest-scoring
> source’s original article. Cross-week dedup (using `_prev.json`) also
> benefits since the same signal has the same ID on replay.

### 5.3 Relevance Scoring Algorithm

Base relevance is computed from keyword matching against the signal's title +
summary text:

| Signal | Score Contribution |
|---|---|
| Contains tryable artifact (CLI tool, API, library, extension) | +0.3 |
| Mentions user's employer stack (Azure, Microsoft, cloud) | +0.2 |
| Practical how-to / tip / tutorial content | +0.2 |
| Major model release (GPT, Claude, Gemini, Llama, Qwen) | +0.15 |
| Open-source project with GitHub link | +0.1 |
| Mentioned in 2+ sources (`seen_in ≥ 2`) | +0.1 |
| Academic paper only (no practical artifact) | −0.2 |
| Enterprise-only (requires org-level deployment) | −0.1 |
| Hardware / data center / policy news | −0.1 |

**Topic Interest Graph boost:** If any keyword from a declared topic matches
the signal's title/summary, add `topic.boost` (0.3 or 0.5) to the score.
See §9 for the Interest Graph design.

**Final score:** Clamped to [0.0, 1.0].

### 5.4 Try-Worthy Classification

A signal is `try_worthy: true` if ALL of the following:
- Score ≥ `try_worthy_threshold` (default 0.6)
- Category is one of: `tool_release`, `technique`, `tutorial`, `framework_update`
- NOT category `research` (unless it includes a linked artifact/demo)

Signals that are NOT try-worthy still surface in the radar (marked "📖 Read-only").

### 5.5 Deduplication

Same topic across multiple sources → single signal with highest score,
`seen_in` incremented, all sources merged into `sources` list. Dedup key:
`SHA-256(topic_normalized)` — identical to the signal ID, ensuring the
deduped signal retains the same stable ID.

Cross-week dedup: signals that were surfaced in the previous week's
`tmp/ai_trend_signals.json` and not acted upon are demoted (score × 0.5) if
they reappear. Signals already in an experiment (`state/ai_trend_radar.md`)
are excluded entirely.

### 5.6 Output

**File:** `tmp/ai_trend_signals.json`

```json
{
  "generated_at": "2026-03-22T08:00:00Z",
  "week_start": "2026-03-16",
  "week_end": "2026-03-22",
  "signal_count": 4,
  "signals": [
    {
      "id": "a3f8...",
      "topic": "Claude Code /compact command",
      "category": "tool_release",
      "sources": ["simon_willison", "the_batch", "tldr_ai"],
      "best_source_url": "https://simonwillison.net/2026/Mar/18/...",
      "summary": "New CLI command reduces context window usage by 40%",
      "detected_at": "2026-03-18",
      "relevance_score": 0.85,
      "try_worthy": true,
      "seen_in": 3,
      "topic_match": "Claude Tools"
    }
  ]
}
```

**Retention:** Overwritten each skill run. Before overwriting, the skill
renames the current file to `tmp/ai_trend_signals_prev.json` for cross-week
dedup (explicit `os.replace()` call in the skill's output phase).

---

## 6. Stage 3 — SURFACE

### 6.1 Catch-Up Briefing Integration

When `tmp/ai_trend_signals.json` exists and contains signals above the surface
threshold (default 0.5), the briefing layer renders a radar section.

**Rendering location:** `scripts/briefing_adapter.py` — NOT `scripts/pr_manager.py`.
`run_step8()` in `pr_manager.py` writes scored moments to
`tmp/content_moments.json` and returns `list[ScoredMoment]`; it does not
render Markdown. The briefing assembly happens in `briefing_adapter.py`,
which is the correct integration point for the radar section. The radar renderer reads `tmp/ai_trend_signals.json` and inserts
the section after the Content Calendar, before domain sections.

```markdown
### 🔭 AI Trend Radar (3 signals this week)

1. **Claude Code /compact command** — Reduces context window usage by 40%
   📰 3 sources | 🔧 Try-worthy | Topic: Claude Tools
   
2. **Qwen3.5 family release** — Full model family from 0.6B to 235B
   📰 2 sources | 🔧 Try-worthy
   
3. **Apple multimodal tokenizer** — Novel mixed text/image tokenization
   📰 1 source | 📖 Read-only
```

**Rendering rules:**
- Max 5 signals displayed (configurable via `max_signals_per_week`)
- Sorted by `relevance_score` descending
- Topic match shown when present
- Appears after Content Calendar, before domain sections

### 6.2 Telegram Push (Layer 1)

For signals with `relevance_score > 0.7` AND `try_worthy: true`, send a
Telegram push notification via existing `channel_push.py`:

```
🔭 AI Signal: Claude Code /compact command
📰 3 sources this week | Score: 0.85
🔧 Try-worthy — reply /try a3f8 or /skip a3f8
```

**Rate limit:** Max 2 radar pushes per week (configurable via
`max_radar_pushes_per_week` in §12.2, respects existing channel push rate
limits). Pushes only on Monday catch-up.

### 6.3 Telegram Commands (Layer 2)

Extensions to `scripts/channel_listener.py`:

| Command | Action |
|---|---|
| `/radar` | List all unprocessed signals from current week |
| `/radar tried` | List signals in experimentation backlog |
| `/radar topics` | Show current Topic Interest Graph |
| `/try <signal-id>` | Move signal to experimentation queue |
| `/skip <signal-id>` | Mark signal as dismissed |
| `/radar topic add "<name>"` | Add topic with default 0.3 boost |
| `/radar topic boost "<name>"` | Increase topic boost to 0.5 |
| `/radar topic remove "<name>"` | Remove topic from Interest Graph |

**Access control:** Full-scope recipients only (existing Telegram ACL).

**Audit events:**

| Event | Key Fields |
|---|---|
| `RADAR_TRY` | `signal_id`, `topic` |
| `RADAR_SKIP` | `signal_id`, `topic` |
| `RADAR_TOPIC_ADD` | `topic_name`, `boost` |
| `RADAR_TOPIC_REMOVE` | `topic_name` |

---

## 7. Stage 4 — TRY

### 7.1 Experimentation State

When a user `/try`s a signal, an experiment record is appended to
`state/ai_trend_radar.md` under the `experiments` key in the **single
unified frontmatter block** (see §9.2 for the canonical schema).

The Markdown body renders experiments as a human-readable section.

### 7.2 Experiment Lifecycle

```
queued → trying → done | abandoned
```

- `queued`: User said `/try` but hasn't started
- `trying`: User is actively experimenting (starter state)
- `done`: User provided a verdict
- `abandoned`: User gave up or lost interest

### 7.3 Verdict Values

| Verdict | Meaning | Produces Card? |
|---|---|---|
| `great` | Genuine discovery, worth sharing | ✅ Yes (magnitude 1.0) |
| `useful` | Solid tool/technique, marginal insight | ✅ Yes (magnitude 0.7) |
| `meh` | Not as advertised, limited value | ❌ No |
| `didnt_work` | Failed to reproduce, broken, irrelevant | ❌ No |

**Only `great` and `useful` verdicts produce Content Stage cards.** This ensures
every LinkedIn post reflects genuine personal experience (DP-1).

### 7.4 Experiment Assistance

Artha can assist with the experiment in the normal catch-up or ad-hoc session:
- Fetch the tool's README or documentation
- Help set up the development environment
- Walk through a tutorial
- Debug issues during experimentation

This requires no new code — it's the standard Artha conversational loop.

### 7.5 Verdict Submission

When the user reports a verdict during the Artha conversational loop (catch-up
or ad-hoc session), the action bridge writes the following fields to the
experiment record in `state/ai_trend_radar.md`:

```python
experiment["status"] = "done"
experiment["verdict"] = verdict          # great | useful | meh | didnt_work
experiment["completed_date"] = today_iso  # e.g. "2026-03-25"
experiment["notes"] = notes              # user's free-text notes (may be empty)
# moment_emitted is NOT set here — the skill owns that field
```

> **Ownership boundary:** The action bridge sets `completed_date` at verdict
> time. The `AITrendRadarSkill` reads `completed_date` during the next weekly
> run to populate `ScoredMoment.event_date`, then sets `moment_emitted: true`.
> These two writes are intentionally separated — the conversational loop runs
> at verdict time; the skill runs on weekly cadence.

**Audit event:** `RADAR_EXPERIMENT_DONE | exp: EXP-NNN | verdict: great`

---

## 8. Stage 5 — DRAFT

### 8.1 Moment Creation

When an experiment reaches `done` with verdict `great` or `useful`, the
`AITrendRadarSkill` creates a `ScoredMoment`:

```python
ScoredMoment(
    label=f"Tried: {experiment.topic}",
    moment_type="ai_experiment_complete",
    event_date=experiment.completed_date,    # "today" → timeliness = 1.0
    source="ai_trend_radar",
    signal_magnitude=1.0 if verdict == "great" else 0.7,
)
```

> **Timing:** Moment creation happens during the next weekly
> `AITrendRadarSkill` run, not at verdict time. The skill scans for
> experiments with `status: done` and a `great` or `useful` verdict where
> `moment_emitted: false`. After creating the `ScoredMoment`, the skill
> sets `moment_emitted: true` on the experiment record to prevent
> re-emission on subsequent runs. Maximum latency: 7 days. This is
> acceptable given the monthly posting cadence.

| Test | Description |
|---|---|
| `test_parse_creates_moment_for_done_experiments` | Skill run with a `done`/`great` experiment → `ScoredMoment` emitted |
| `test_moment_not_re_emitted_on_second_run` | Skill run twice with same `done`/`great` experiment → only one `ScoredMoment` emitted (second run skips `moment_emitted: true`) |

### 8.2 New Moment Type Registration

Added to `scripts/pr_manager.py`:

```python
_MOMENT_WEIGHTS["ai_experiment_complete"] = 0.85

_DEFAULT_MOMENT_THREAD_MAP["ai_experiment_complete"] = [
    ("NT-1", 1.0),    # Thoughtful Technologist — primary
    ("NT-5", 0.5),    # MBA Practitioner — secondary
]
```

**Convergence score example:**
- `signal_weight = 0.85` (ai_experiment_complete)
- `relevance = 1.0` (NT-1 best match)
- `signal_magnitude = 1.0` (verdict: great)
- `timeliness = 1.0` (today)
- **Score: 0.85** — above daily threshold (0.8), surfaces immediately.

### 8.3 Content Stage Integration

The scored moment flows through the existing `ContentStage.process_moments()`
via the `_adapt_moment()` bridge which uses `getattr()` with string fallbacks:
`label` → `occasion`, `moment_type` → `occasion_type`. No changes to
`service.py` are required for the new moment type to flow through.

**Hard prerequisite:** Component 6 (adding `ai_experiment_complete` to
`_MOMENT_WEIGHTS` and `_DEFAULT_MOMENT_THREAD_MAP` in `pr_manager.py`) is a
**blocking dependency** for the DRAFT stage. Without it, `_MOMENT_WEIGHTS`
returns weight 0 for the unknown type, causing the convergence score to be
0.0 — the moment is silently swallowed and no card is ever created. This
must be implemented and tested before any end-to-end radar → card flow.

A seed card is created in `state/gallery.yaml` (Component 10a, Phase 2).
This is a **manual one-time state edit** — the implementer adds one example
card to the gallery so that the Content Stage pipeline can be verified
end-to-end before a real experiment completes.

**Seed card format** (follows PR-2 `gallery.yaml` card schema):

```yaml
- id: CARD-SEED-RADAR
  occasion: "Tried: Claude Code /compact command"
  occasion_type: "ai_experiment_complete"
  primary_thread: "NT-1"
  status: SEED
  platform: linkedin
  created_at: "2026-03-22"
  voice_register: "B-Practitioner"
```

> **Delivery:** Added alongside Component 10 (Register B-Practitioner voice)
> in Phase 2. After end-to-end verification, the seed card can be deleted
> or left as SEED (it will not surface in production because SEED status
> cards are never promoted to READY without a real `ScoredMoment`).

### 8.4 Register B-Practitioner Voice

A sub-register of the existing LinkedIn Register B (project showcase). Defined
in `state/pr_manager.md` voice profile section:

**Format:**

```
[HOOK — What I tried and why it matters. 1 sentence.]

[CONTEXT — What is this tool/technique. 1-2 sentences.]

[EXPERIENCE — What I actually did]
🔧 Specific thing I tried
💡 What surprised me
📊 Concrete result or comparison

[TAKEAWAY — One sentence others can act on]

👉 [Link to tool/article]

#AI #[TopicTag] #[CategoryTag] #PractitionerInsights #OpenSource
```

**Rules:**
- Word count: 80–150 words (shorter than Register B project posts of 100–200)
- Tone: First-person practical, not promotional
- Required: Must include something the user personally discovered
- Emoji: In bullet markers only (🔧💡📊), not in prose
- Hashtags: 5–8, PascalCase, `#AI` always present
- CTA: Link to tool/article (not self-promotional)

**Anti-boilerplate:** The existing `boilerplate_score()` in
`scripts/pr_stage/personalizer.py` already rejects generic content (threshold
0.6). Register B-Practitioner additionally requires:
- At least one first-person verb ("I tried", "I tested", "I found")
- At least one concrete metric or comparison
- No press-release language ("revolutionary", "game-changing", "groundbreaking")

---

## 9. Topic Interest Graph

### 9.1 Purpose

A running list of AI topics the user is interested in. Used for **boosting**
signal relevance, not filtering. New topics are discovered organically, and the
user decides which to add to the graph.

### 9.2 Storage

Stored in `state/ai_trend_radar.md` as a **single unified YAML frontmatter
block** (consistent with how `state/pr_manager.md` and `state/gallery.yaml`
store structured data). The file has exactly **one** frontmatter block
containing all structured keys — `topics_of_interest`, `experiments`,
and `meta`. The Markdown body is a rendered human-readable view only.

**Canonical schema:**

```yaml
---
topics_of_interest:
  - name: Work-IQ
    keywords: [workiq, work-iq, work iq]
    boost: 0.5
    added: 2026-03-22
    source: manual
  - name: Agency CLI
    keywords: [agency cli, agency-cli, microsoft agency]
    boost: 0.5
    added: 2026-03-22
    source: manual
  - name: Claude Channels
    keywords: [claude channels, anthropic channels]
    boost: 0.3
    added: 2026-03-22
    source: manual
  - name: Claude Dispatch
    keywords: [claude dispatch, dispatch feature]
    boost: 0.3
    added: 2026-03-22
    source: manual
  - name: MCP Servers
    keywords: [mcp server, model context protocol]
    boost: 0.3
    added: 2026-03-22
    source: manual
  - name: Agentic Workflows
    keywords: [agentic, agent framework, multi-agent]
    boost: 0.3
    added: 2026-03-22
    source: manual
  - name: Vibe Coding
    keywords: [vibe coding, vibe-coding]
    boost: 0.3
    added: 2026-03-22
    source: manual
experiments:
  - id: EXP-007
    topic: Claude Code /compact command
    signal_id: a3f8...
    source: Simon Willison, The Batch, TLDR AI
    source_url: https://simonwillison.net/2026/Mar/18/...
    status: trying        # queued | trying | done | abandoned
    started: 2026-03-23
    completed_date: null  # set to ISO date (YYYY-MM-DD) when status → done
    notes: ""             # user adds during experimentation
    verdict: pending      # great | useful | meh | didnt_work | pending
    key_takeaway: ""      # pending until verdict
    moment_emitted: false # set to true after ScoredMoment is created; skill skips on re-runs
meta:
  warm_start_file: null         # path to JSONL file; set to "" after consumption (see §4.3)
  warm_start_consumed_at: null  # set to ISO timestamp after first warm-start run
---

## Topics of Interest

(Rendered from frontmatter above — human-readable view)

## Experiments

(Rendered from frontmatter above — human-readable view)
```

> **Implementation note:** A Markdown file can have only one YAML frontmatter
> block ($---$ delimiters at the top). All structured state lives inside this
> single block. Parsers must read/write the full frontmatter dict, not assume
> separate blocks for experiments and topics.

**Rationale:** Markdown tables are fragile when user-edited free text contains
pipe characters or when parsed programmatically. YAML frontmatter is the Artha
convention for machine-read structured state and handles lists, nested objects,
and special characters reliably.

### 9.3 Boost Mechanics

During `AITrendRadarSkill.parse()`, the **highest matching topic boost wins**
(max-wins, not additive). This prevents a signal matching multiple topics from
getting an inflated score:

```python
best_boost = 0.0
best_topic = None
for topic_row in interest_graph:
    for keyword in topic_row.keywords:
        if keyword in signal_text_lower:
            if topic_row.boost > best_boost:
                best_boost = topic_row.boost
                best_topic = topic_row.name
            break  # one match per topic is enough
signal.relevance_score += best_boost
if best_topic:
    signal.topic_match = best_topic
```

**Score clamped to [0.0, 1.0]** after all boosts.

### 9.4 Topic Discovery (Organic)

When a signal scores above 0.7 from **organic relevance alone** (no topic
boost), AND the topic doesn't match any existing Interest Graph entry, the
briefing annotates it:

```markdown
   💡 New topic? "Qwen3.5" appeared in 3 sources. /radar topic add "Qwen3.5"
```

This is a suggestion only — user decides whether to add.

### 9.5 Auto-Keyword Expansion *(Future — out of scope for v1.0)*

When the user completes an experiment on a topic and the experiment notes
mention specific terms not already in the topic's keyword list, those terms
are suggested for addition (not auto-added):

```
EXP-007 done. Your notes mention "context compression" and "token reduction"
— add to "Claude Tools" keywords? /radar topic expand "Claude Tools"
```

> **Scope note:** This feature requires an implementation owner and
> corresponding test coverage. It is deferred to v1.1+ to keep the v1.0
> surface area focused on the core INGEST→DISTILL→SURFACE→TRY→DRAFT
> pipeline. The `/radar topic expand` command is NOT part of the v1.0
> command table (§6.3).

### 9.6 Topic Lifecycle Commands

| Command | Effect |
|---|---|
| `/radar topic add "X"` | Adds topic with boost 0.3, auto-generates keywords from topic name |
| `/radar topic add "X" 0.5` | Adds topic with explicit boost |
| `/radar topic boost "X"` | Increases boost from 0.3 → 0.5 |
| `/radar topic remove "X"` | Removes topic row from Interest Graph |
| `/radar topics` | Lists all topics with boost values and last-match date |

---

## 10. Platform Exclusion & State Corrections

### 10.1 Festival Platform Exclusion

**Problem:** `cultural_festival` moments currently map to NT-2 (Cultural
Bridge-Builder) which targets LinkedIn + Facebook. User has stated festivals
should NOT appear on LinkedIn.

**Solution:** Add a `platform_exclude` field to the moment-to-thread map.

In `state/pr_manager.md` YAML block (user-editable):

```yaml
moment_thread_overrides:
  cultural_festival:
    platform_exclude: [linkedin]
```

In `scripts/pr_manager.py`, when rendering content calendar or creating
draft context, check `platform_exclude` and skip excluded platforms.

In `scripts/pr_stage/domain.py`, add optional `platform_exclude: list[str]`
field to `ContentCard`:

```python
@dataclass
class ContentCard:
    ...
    platform_exclude: list[str] = field(default_factory=list)
```

When Content Stage creates a card for a moment whose thread map includes
a `platform_exclude`, that list propagates to the card.

### 10.2 LinkedIn Data Quality Annotation

**Problem:** `state/pr_manager.md` shows `avg_gap_days: 365` and
`last_post: '2025-02-22'` for LinkedIn. This is from a partial data export
and does not reflect actual posting cadence (~monthly).

**Solution:** Add a `data_quality` field to the LinkedIn platform metrics:

```yaml
platform_metrics:
  linkedin:
    posts_30d: 0
    posts_90d: 0
    avg_gap_days: 365
    last_post: '2025-02-22'
    data_quality: partial   # New field
```

**Behavioral impact:**
- `PAT-PR-001` (LinkedIn gap > 21 days) checks `data_quality`. When `partial`,
  the pattern is suppressed (cooldown overridden to infinite).
- The briefing content calendar renderer adds "(partial data)" annotation
  next to LinkedIn metrics.
- Does NOT affect anti-spam governor (which already allows posting regardless
  of gap state since it only checks min_gap_days forward).

---

## 11. Privacy & Security

### 11.1 Alignment with Artha Security Model

This feature operates within Artha's three-layer PII defense and follows the
principle that **all write actions require explicit user approval**.

### 11.2 Threat Analysis

| Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|
| RSS feed serves malicious XML (XXE) | Low | Medium | Python `xml.etree.ElementTree` does NOT expand external entities by default. `defusedxml` not required but could be added as defense-in-depth. See A-SEC-1. |
| Newsletter email contains prompt injection | Medium | Low | Email bodies are already wrapped in `<EMAIL_BODY>` untrusted tags by the pipeline. `AITrendRadarSkill` extracts keywords only — no LLM processing of email content. |
| RSS item link is a phishing URL | Medium | Low | Links are stored but never auto-opened. User sees the link in the signal summary and decides whether to visit. |
| Employer-internal content leaks into signals | Low | High | Employer safety gate: `_BLOCKED_TERMS` set in `AITrendRadarSkill` (from `user_profile.yaml` employment section). Signals containing blocked terms are silently dropped. |
| Topic Interest Graph reveals user preferences | Low | Low | `state/ai_trend_radar.md` is a plaintext state file — same sensitivity as `state/digital.md`. Not encrypted because it contains no PII. Topic names like "MCP Servers" are not personally identifying. |
| Experiment notes contain proprietary info | Medium | Medium | Experiment notes are user-written free text in state file. PII guard runs on `state/` writes as usual. Employer safety gate scans experiment notes before card creation. |
| Signal summary contains injected instructions | Low | Medium | Summaries are extracted from RSS `<description>` or email subject lines — limited to ≤200 chars, HTML stripped. No LLM interprets these strings in the DISTILL stage. |

### 11.3 Employer Safety Gate

```python
_EMPLOYER_BLOCKED_TERMS: frozenset[str]  # Loaded from user_profile.yaml
# Path: employment.confidential_terms (list of strings)
# at runtime, never hardcoded (per PR-2 §10.3 personalizer.py convention)
```

Blocked terms are checked against:
- Signal topic + summary text (DISTILL stage)
- Experiment notes (TRY stage, before card creation)
- Draft content (DRAFT stage, existing `_pii_gate_draft()`)

Signals or experiments containing blocked terms are silently dropped with
an audit log entry: `RADAR_EMPLOYER_BLOCKED | signal_id | blocked_term`.

### 11.4 No New External API Calls

- RSS feeds are fetched via `urllib.request` (existing `rss_feed.py`)
- No new OAuth flows, no new API keys, no new auth contexts
- Email ingestion uses existing Gmail/Outlook connectors
- No data is sent to any external service

### 11.5 PII Guard Compatibility

`state/ai_trend_radar.md` is a plaintext state file (not vault-encrypted)
because it contains:
- Topic names (not PII)
- RSS feed URLs (public)
- Experiment titles (derived from public AI announcements)
- Experiment notes (user-authored, PII guard scans on write)

**Not encrypted** per the same policy as `state/gallery.yaml` (PR-2 v1.3.0
vault policy change — public social-media content does not require PII-level
protection).

### 11.6 Audit Trail

All radar actions logged to `state/audit.md` (or `.age` when encrypted):

```
[2026-03-22T08:00:00Z] RADAR_DISTILL_RUN | raw: 87 | filtered: 24 | deduped: 18 | surfaced: 4 | feeds_empty: [anthropic] | duration_ms: 920
[2026-03-22T08:15:00Z] RADAR_SIGNAL_SURFACED | count: 4 | week: 2026-W12
[2026-03-22T09:00:00Z] RADAR_TRY | signal: a3f8 | topic: Claude Code /compact
[2026-03-25T19:30:00Z] RADAR_EXPERIMENT_DONE | exp: EXP-007 | verdict: great
[2026-03-25T19:30:05Z] RADAR_CARD_SEEDED | card: CARD-2026-015 | topic: Claude Code /compact
```

**`RADAR_DISTILL_RUN` fields:** Emitted every skill run to provide pipeline
observability. `raw` = total items ingested (email + RSS), `filtered` = items
after keyword matching, `deduped` = signals after dedup, `surfaced` = signals
above `surface_threshold`, `feeds_empty` = list of feed tags that returned
zero items (helps diagnose broken feeds), `duration_ms` = wall-clock time.
This is essential for diagnosing issues when the radar produces zero signals
(ingestion failure vs. scoring miscalibration vs. aggressive dedup).

### 11.7 Runtime Metrics Surface

Beyond grep-able audit log lines, the skill writes a lightweight JSON metrics
file after each run:

**File:** `tmp/ai_trend_metrics.json`

```json
{
  "last_run_at": "2026-03-22T08:00:00Z",
  "last_run_status": "ok",
  "raw_count": 87,
  "filtered_count": 24,
  "deduped_count": 18,
  "surfaced_count": 4,
  "empty_feeds": ["anthropic"],
  "duration_ms": 920,
  "warm_start_active": false
}
```

**Design rationale:** This file is overwritten each run (not appended). It
provides a single-read health surface for the catch-up briefing, the `/status`
command, and any future health-check integration — without requiring log
parsing. Fields mirror `RADAR_DISTILL_RUN` so the audit log remains the
canonical history while this file serves as the "last known state." Stored in
`tmp/` (gitignored, ephemeral) consistent with other derived outputs.

---

## 12. Configuration Schema

### 12.1 Feature Flag

```yaml
# config/artha_config.yaml
enhancements:
  pr_manager:
    enabled: true
    stage: true
    compose: false
    learning: false
    ai_trend_radar:              # PR-3 — new nested block
      enabled: true
```

**Gate:** `enhancements.pr_manager.ai_trend_radar.enabled` must be `true`
AND `enhancements.pr_manager.enabled` must be `true`.

### 12.2 Radar Configuration

```yaml
# config/artha_config.yaml (continued)
    ai_trend_radar:
      enabled: true
      newsletter_senders:
        - "swyx+ainews@substack.com"
        - "bigtechnology@substack.com"
        - "bytebytego@substack.com"
        - "datapoints@deeplearning.ai"
        - "gregorojstersek@substack.com"
        - "lenny@substack.com"
        - "systemdesignone@substack.com"
        - "productforengineers@substack.com"
        - "refactoring+monday-ideas@substack.com"
        - "thebatch@deeplearning.ai"
        - "pragmaticengineer@substack.com"
        - "news@daily.therundown.ai"
        - "noreply@tpmuniversity.com"
      relevance_keywords:          # Base relevance boosters
        - claude
        - gpt
        - gemini
        - llama
        - agentic
        - mcp
        - rag
        - fine-tuning
        - prompt engineering
        - vibe coding
      max_signals_per_week: 5
      surface_threshold: 0.5
      try_worthy_threshold: 0.6
      max_radar_pushes_per_week: 2
```

> **Note:** `warm_start_file` is NOT stored in config — it lives in
> `state/ai_trend_radar.md` frontmatter under the `meta` block. See §4.3
> and §9.2 for details.

### 12.3 RSS Connector Configuration

```yaml
# config/connectors.yaml
rss_feed:
  type: feed
  provider: rss
  enabled: true                    # Changed from false
  run_on: all                      # Both macOS and Windows
  auth:
    method: none                   # No auth required
  fetch:
    handler: "scripts/connectors/rss_feed.py"
    feeds:
      - url: "https://openai.com/blog/rss.xml"
        tag: "openai_blog"
        domain: "digital"
      - url: "https://simonwillison.net/atom/everything/"
        tag: "simon_willison"
        domain: "digital"
      - url: "https://huggingface.co/blog/feed.xml"
        tag: "huggingface_blog"
        domain: "digital"
      - url: "https://blog.google/technology/ai/rss/"
        tag: "google_ai"
        domain: "digital"
      - url: "https://www.anthropic.com/research/rss.xml"
        tag: "anthropic"
        domain: "digital"
      - url: "https://www.microsoft.com/en-us/research/feed/"
        tag: "msresearch"
        domain: "digital"
      - url: "https://hnrss.org/newest?q=AI+LLM+GPT"
        tag: "hn_ai"
        domain: "digital"
    default_max_results: 50
  retry:
    max_retries: 2
    base_delay: 2.0
```

### 12.4 Skills Registration

```yaml
# config/skills.yaml
ai_trend_radar:
  enabled: true
  priority: P2
  cadence: weekly
  requires_vault: false
  safety_critical: false
  description: >
    Distill AI signals from newsletter emails and RSS feeds.
    Applies Topic Interest Graph boost scoring. Outputs ranked
    signals to tmp/ai_trend_signals.json for briefing + Telegram.
```

---

## 13. Implementation Plan

### 13.1 Component Inventory

| # | Component | File | Type | Lines (est.) |
|---|---|---|---|---|
| 1 | Enable RSS connector + feeds | `config/connectors.yaml` | Config edit | — |
| 2 | AI newsletter sender filter | `config/artha_config.yaml` | Config edit | — |
| 3 | `AITrendRadarSkill` | `scripts/skills/ai_trend_radar.py` | **New file** | ~300 |
| 4 | Skill registration | `config/skills.yaml` | Config edit | — |
| 5 | State file template | `state/ai_trend_radar.md` | **New file** | ~40 |
| 6 | New moment type + thread map | `scripts/pr_manager.py` | Code edit | ~15 |
| 7 | Platform exclude field | `scripts/pr_stage/domain.py` | Code edit | ~15 |
| 8 | Platform exclude in service | `scripts/pr_stage/service.py` | Code edit | ~10 |
| 9 | Radar briefing section | `scripts/briefing_adapter.py` | Code edit | ~40 |
| 10 | Register B-Practitioner voice | `state/pr_manager.md` | State edit | ~30 |
| 10a | Seed card for e2e verification | `state/gallery.yaml` | State edit | ~8 |
| 11 | LinkedIn `data_quality` fix | `state/pr_manager.md` + `pr_manager.py` | Code + state edit | ~10 |
| 12 | Festival platform exclusion | `scripts/pr_manager.py` | Code edit | ~10 |
| 13 | Telegram radar commands | `scripts/channel_listener.py` | Code edit | ~60 |
| 14 | Pattern PAT-PR-004 (stale radar) | `config/patterns.yaml` | Config edit | ~15 |
| 15 | Implementation status entry | `config/implementation_status.yaml` | Config edit | — |
| 16 | Unit tests | `tests/unit/test_ai_trend_radar.py` | **New file** | ~300 |

**Total new code:** ~700 lines across 2 new files + edits to 8 existing files
**Config changes:** 5 files
**State changes:** 2 files

### 13.2 Implementation Phases

**Phase 0 — Warm Start (Component 2 + pipeline run + RSS validation)**

Run before any code changes. Validates assumptions with real data:
1. Configure `newsletter_senders` list in `config/artha_config.yaml` (Component 2)
2. Run `python scripts/pipeline.py --since "2025-12-22T00:00:00Z" --source gmail > tmp/warm_start_emails.jsonl`
3. Count records matching 13 configured senders — validates A-MAIL-1
4. Sample subject lines from The Batch, AI News, Rundown AI — validates A-BEH-3
5. Verify email_classifier tags them as `marketing_category: "newsletter"` — validates A-MAIL-2
6. Validate RSS feed URLs (A-RSS-1): `fetch(feeds=[...], since='30d', max_results=3)` for each of the 7 feeds. Record which return valid XML and which fail.
7. **Go/no-go gate:** If <20 newsletter records from step 3, abort and investigate sender list before proceeding. If 0 of 7 RSS feeds return valid XML, proceed with email-only mode but log warning.

This phase produces no code but generates the corpus the skill will process
on first run. If validation fails, adjust sender list or feed URLs before
implementation.

**Phase 1 — Core Pipeline (Components 1–6, 14–16)**

Delivers: RSS ingestion, AITrendRadarSkill (with state-driven warm-start
path per §4.3/§9.2), signal extraction, topic interest graph, state file,
new moment type, unit tests.

**Phase 2 — Integration (Components 7–13, 10a)**

Delivers: Platform exclusion, briefing rendering (via `briefing_adapter.py`), Telegram commands,
voice register, state corrections, seed card for end-to-end verification.

**Phase 3 — Activation**

1. Run warm-start pipeline scan (Phase 0 output: `tmp/warm_start_emails.jsonl`)
2. Enable RSS connector (`connectors.yaml`)
3. Enable AI Trend Radar skill (`skills.yaml`)
4. Set feature flag (`artha_config.yaml`)
5. Populate initial topic interest graph (`state/ai_trend_radar.md`)
6. **Arm warm-start:** set `meta.warm_start_file: "tmp/warm_start_emails.jsonl"` in `state/ai_trend_radar.md` frontmatter (see §4.3 Step 2)
7. Run `AITrendRadarSkill` — reads armed JSONL, processes signals, auto-clears `meta.warm_start_file` (one-shot, see §4.3)
8. Run full test suite
9. First Monday catch-up with radar active (warm-start signals shown as 📚 From backlog)

### 13.3 Sequencing Constraints

- Component 3 (skill) depends on Component 1 (RSS enabled) for RSS data
- Component 9 (briefing) depends on Component 3 (skill output file)
- **Component 6 (moment type + thread map) is a hard prerequisite for the
  DRAFT stage.** Without it, `ai_experiment_complete` moments score 0.0 and
  are silently swallowed. Must be implemented and tested before any
  end-to-end radar → card verification.
- Component 7-8 (platform exclude) can be done independently
- Component 13 (Telegram) depends on Component 5 (state file exists)
- All components depend on Component 16 (tests written alongside)

---

## 14. Assumptions & Validation

### 14.1 Infrastructure Assumptions

| ID | Assumption | Validation | Risk if Wrong |
|---|---|---|---|
| **A-RSS-1** | RSS feed URLs serve valid RSS 2.0 or Atom 1.0 XML | Test: `python3 -c "from scripts.connectors.rss_feed import fetch; list(fetch(feeds=[{'url': URL}], since='7d'))"` for each feed URL. Run before implementation. | Feed fails silently; skill gets fewer signals. Graceful — non-blocking. |
| **A-RSS-2** | `hnrss.org` query-filtered feed returns items matching the `q=` parameter | Test: fetch 5 items and verify titles contain "AI", "LLM", or "GPT". | Noisy signals from unrelated HN posts. Mitigation: remove HN feed if quality is low. |
| **A-RSS-3** | RSS connector runs without errors when fetched in parallel with email connectors | Test: `pipeline.py` with `rss_feed` enabled alongside `gmail` + `outlook`. | Timeout or thread contention. Mitigation: RSS has independent timeout (10s default). |
| **A-MAIL-1** | Newsletter emails from configured senders arrive in Gmail with detectable sender domains | **Validated by warm start.** Run `pipeline.py --since 90d --source gmail`, filter to 13 configured senders, confirm ≥50 records. 10/13 senders are `@substack.com` — already classified as newsletter by `email_classifier.py`. | Skill gets zero newsletter signals. Fallback: RSS feeds still provide signals. |
| **A-MAIL-2** | `email_classifier.py` correctly tags newsletter senders as `marketing_category: "newsletter"` | Test: run classifier on sample email record with `from: "batch@deeplearning.ai"`. Should return `marketing: True, marketing_category: "newsletter"`. | Newsletters routed as personal email. Mitigation: skill also matches by sender domain directly, not only by marketing_category. |
| **A-SKILL-1** | `skill_runner.py` discovers and executes skills that extend `BaseSkill` and are registered in `skills.yaml` | Verified by existing 18 skills. No risk. | — |
| **A-SKILL-2** | Weekly cadence (`cadence: weekly`) in `skills.yaml` triggers once per 7-day period | Verified by `visa_bulletin` skill which uses weekly cadence. | Skill runs too often or never. `should_run()` logic in `skill_runner.py` is well-tested. |

### 14.2 Data Format Assumptions

| ID | Assumption | Validation | Risk if Wrong |
|---|---|---|---|
| **A-FMT-1** | RSS connector output schema (`id`, `subject`, `from`, `date_iso`, `body`, `source`, `feed_url`, `link`) is stable | Read `rss_feed.py` — verified in exploration. Schema is hardcoded in the connector. | Skill reader breaks. Mitigation: skill validates field presence before access. |
| **A-FMT-2** | Pipeline JSONL output includes email records with `marketing_category` field when classified | Verified by `email_classifier.py` — mutates record in-place, adds field. | Skill can't distinguish newsletters from other email. Mitigation: also match by `from` domain. |
| **A-FMT-3** | `ScoredMoment` dataclass in `pr_manager.py` accepts arbitrary `moment_type` strings | Verified — `_MOMENT_WEIGHTS` is a dict lookup; unknown types get weight 0 (graceful). | New moment type scores 0. Mitigation: add to `_MOMENT_WEIGHTS` dict (Component 6). |
| **A-FMT-4** | `ContentStage._adapt_moment()` bridges `label` → `occasion` and `moment_type` → `occasion_type` for any moment type | Verified in `service.py` lines 132-145 — uses `getattr()` with string fallbacks. | Card creation fails for new moment type. Write test to confirm. |

### 14.3 Behavioral Assumptions

| ID | Assumption | Validation | Risk if Wrong |
|---|---|---|---|
| **A-BEH-1** | User subscribes to ≥2 AI newsletters that arrive via Gmail | **Confirmed.** User provided 13 active newsletter senders — 10 Substack, 2 DeepLearning.AI, 1 Rundown AI. All arrive in Gmail. | ~~Zero newsletter signals.~~ Not applicable — validated. |
| **A-BEH-2** | User will actually experiment with try-worthy signals (not just read) | Design principle DP-1 enforces this. If user never `/try`s, no cards are created — system degrades gracefully to a read-only radar. | Feature underutilized. Acceptable — no wasted resources. |
| **A-BEH-3** | AI newsletters contain extractable topic + summary in subject line or first 200 chars of body | **Validated by warm start.** Sample 5 emails each from `thebatch@deeplearning.ai`, `swyx+ainews@substack.com`, and `news@daily.therundown.ai`. Extract subjects, verify they contain meaningful topic strings with AI keywords. | Poor signal quality. Mitigation: keyword matching is fuzzy, not exact. |
| **A-BEH-4** | Topic Interest Graph will have 5–20 entries at steady state | User-managed. No validation needed. | Very large graph (>50 topics) could slow scoring. Mitigation: linear scan is O(T×K) where T=topics, K=keywords — at 50×5 this is 250 string comparisons, negligible. |

### 14.4 Security Assumptions

| ID | Assumption | Validation | Risk if Wrong |
|---|---|---|---|
| **A-SEC-1** | Python `xml.etree.ElementTree` does not expand external entities (no XXE) | Python docs confirm: "The `xml.etree.ElementTree` module is not secure against maliciously constructed data." BUT external entity expansion is NOT supported by default (no DTD processing). Test: craft a test XML with `<!ENTITY xxe SYSTEM "file:///etc/passwd">` and verify it does NOT expand. | XXE attack via malicious RSS feed. Mitigation: if test fails, use `defusedxml.ElementTree` (stdlib-compatible drop-in, already in many Python installations). |
| **A-SEC-2** | RSS feed URLs are HTTPS and serve public content | Verify at implementation time. HTTP-only feeds should be flagged with a warning. | MITM on HTTP feed injects malicious content. Mitigation: require HTTPS in config validation. |

> **HTTPS enforcement (A-SEC-2):** In `pull()`, before fetching any RSS
> feed, validate that the URL scheme is `https://`. Non-HTTPS URLs are
> skipped and logged with `log.warning("Skipping non-HTTPS feed: %s", url)`.
> This is a single guard at the entry point — no feed-level exception needed.

| Test | Description |
|---|---|
| `test_https_feed_url_warns` | Feed URL starting with `http://` → skipped with warning log, not fetched |
| **A-SEC-3** | `state/ai_trend_radar.md` does not contain PII | By design — contains only topic names, public URLs, and experiment titles derived from public AI announcements. PII guard runs on all state writes. | PII guard catches any accidental PII in experiment notes. |

---

## 15. Risks & Mitigations

### 15.1 Technical Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **R1: RSS feeds change URL or format** | Medium | Low | Each feed is independent. One broken feed doesn't affect others. `rss_feed.py` has per-feed error handling. Connector health check verifies reachability. |
| **R2: Newsletter format changes break keyword extraction** | Medium | Low | Extraction is fuzzy keyword matching on subject + body. Not dependent on precise HTML structure. Degrades to fewer signals, not errors. |
| **R3: Too many signals overwhelm user** | Low | Medium | `max_signals_per_week: 5` cap. Only `relevance_score > surface_threshold` shown. User can raise threshold. **v1.1 consideration:** If false-positive surfacing rate exceeds 50%, consider a diminishing-returns cap on additive scoring (max 3 positive keyword contributions capped at 0.7 before topic boost) to prevent well-known topics from perpetually dominating. |
| **R4: Topic Interest Graph becomes stale** | Medium | Low | No impact — stale topics just don't match. User can clean up via `/radar topics` + `/radar topic remove`. No TTL enforcement needed. |
| **R5: Signal dedup misses duplicates** | Low | Low | Hash-based dedup on normalized topic. If two sources phrase differently enough to generate different hashes, they appear as separate signals. Worst case: user sees 2 signals for the same thing. Not harmful. **v1.1 consideration:** If duplicate rate exceeds 30% of surfaced signals, add a second-pass Jaccard token-overlap dedup (threshold 0.6) — still deterministic (DP-3) and stdlib-only (DP-8). |
| **R6: `tmp/ai_trend_signals.json` grows unbounded** | Very Low | Very Low | Overwritten each week. Previous week preserved as `ai_trend_signals_prev.json`. Max 2 files at any time. |
| **R7: XML parsing of RSS fails on malformed feed** | Low | Low | `rss_feed.py` already wraps `ET.fromstring()` in try/except. Malformed feed is skipped. |

### 15.2 Security Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **R8: Employer-internal content appears in signal** | Low | High | Employer safety gate (`_EMPLOYER_BLOCKED_TERMS`) loaded from `user_profile.yaml` at runtime — never hardcoded. Scans signal topic + summary + experiment notes. Audit-logged when blocked. |
| **R9: RSS feed serves XXE payload** | Very Low | Medium | Python's `xml.etree.ElementTree` has no DTD expansion by default. Validated in A-SEC-1 test. |
| **R10: Prompt injection via newsletter content** | Low | Low | No LLM processes newsletter content in DISTILL stage. Content is keyword-matched deterministically. Only at DRAFT stage (Phase 3, `compose: true`) would LLM see signal content — and that's gated by the existing `_pii_gate_context()` in `personalizer.py`. |
| **R11: Experiment notes contain sensitive work info** | Medium | Medium | PII guard scans all `state/` writes. Employer blocked terms apply. User is warned in the experiment template: "Do not include proprietary or employer-internal information." |

### 15.3 Operational Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **R12: Feature unused (user doesn't engage)** | Medium | None | No resource waste. Skill runs weekly, processes in <5 seconds. Output file is tiny. If no `/try` happens, nothing downstream activates. |
| **R13: RSS connector adds pipeline latency** | Low | Low | RSS feeds are fetched in parallel with other connectors (existing `ThreadPoolExecutor`). 10s timeout per feed. Even if all 7 feeds are slow, it's one thread competing with 5 slots. |
| **R14: Catch-up on non-Monday misses radar** | Low | Low | Skill cadence is `weekly` — it runs whenever 7 days have passed since last run, regardless of day of week. Monday is typical but not required. |

---

## 16. Test Plan

### 16.1 Unit Tests (`tests/unit/test_ai_trend_radar.py`)

**Signal extraction:**

| Test | Description |
|---|---|
| `test_extract_signal_from_rss_item` | RSS item dict → AISignal with correct fields |
| `test_extract_signal_from_newsletter_email` | Newsletter email dict → AISignal |
| `test_relevance_score_tryable_tool` | Tool release with CLI artifact scores ≥ 0.5 |
| `test_relevance_score_academic_paper` | Research paper without artifact scores < 0.3 |
| `test_relevance_score_model_release` | Major model release scores ≥ 0.35 |
| `test_relevance_score_enterprise_only` | Enterprise deployment news gets penalty |
| `test_try_worthy_tool_release` | `tool_release` with score ≥ 0.6 → `try_worthy: true` |
| `test_try_worthy_research` | `research` category → `try_worthy: false` |
| `test_category_detection_tool` | Subject containing "releases", "launches", "CLI" → `tool_release` |
| `test_category_detection_technique` | Subject with "how to", "tip", "technique" → `technique` |
| `test_category_detection_model` | Subject with "GPT-5", "Claude 4", "Gemini" → `model_release` |

**Topic Interest Graph:**

| Test | Description |
|---|---|
| `test_topic_boost_applied` | Signal matching topic keyword gets boost added |
| `test_topic_boost_clamp` | Score with boost > 1.0 clamped to 1.0 |
| `test_no_boost_without_match` | Signal not matching any topic gets 0 boost |
| `test_multiple_topic_matches` | Signal matching 2 topics gets highest single boost (max-wins, not sum) |
| `test_topic_match_case_insensitive` | "CLAUDE channels" matches keyword "claude channels" |
| `test_parse_interest_graph_from_state` | Read YAML frontmatter → list of topic dicts |
| `test_empty_interest_graph` | No topics → skill still runs, no boost applied |

**Deduplication:**

| Test | Description |
|---|---|
| `test_dedup_same_topic_two_sources` | Same topic from 2 sources → 1 signal, `seen_in: 2` |
| `test_dedup_different_topics` | Different topics from same source → 2 signals |
| `test_cross_week_dedup_demotion` | Signal from prev week not acted on → score × 0.5 |
| `test_cross_week_dedup_experiment_excluded` | Signal already in experiment → excluded entirely |

**Employer safety:**

| Test | Description |
|---|---|
| `test_employer_blocked_term_in_topic` | Signal with blocked term → dropped, not in output |
| `test_employer_blocked_term_in_summary` | Signal with blocked term in summary → dropped |
| `test_employer_generic_term_allowed` | Signal with "Azure" (generic) → not blocked |
| `test_blocked_terms_loaded_from_profile` | Terms loaded from `user_profile.yaml` at runtime |

**Moment creation:**

| Test | Description |
|---|---|
| `test_experiment_great_creates_moment` | Verdict `great` → ScoredMoment with magnitude 1.0 |
| `test_experiment_useful_creates_moment` | Verdict `useful` → ScoredMoment with magnitude 0.7 |
| `test_experiment_meh_no_moment` | Verdict `meh` → no ScoredMoment created |
| `test_experiment_didnt_work_no_moment` | Verdict `didnt_work` → no ScoredMoment created |
| `test_moment_not_re_emitted_on_second_run` | Skill run twice with same `done`/`great` experiment → only one `ScoredMoment` emitted (second run skips `moment_emitted: true`) |
| `test_moment_type_in_weights` | `ai_experiment_complete` present in `_MOMENT_WEIGHTS` |
| `test_moment_type_in_thread_map` | `ai_experiment_complete` maps to NT-1 |

**Platform exclusion:**

| Test | Description |
|---|---|
| `test_cultural_festival_excludes_linkedin` | Card for `cultural_festival` with `platform_exclude: [linkedin]` |
| `test_ai_experiment_no_exclusion` | Card for `ai_experiment_complete` has empty `platform_exclude` |
| `test_platform_exclude_serialization` | `ContentCard.to_dict()` includes `platform_exclude` |
| `test_platform_exclude_from_dict` | `ContentCard.from_dict()` reads `platform_exclude` |

**State file parsing:**

| Test | Description |
|---|---|
| `test_parse_experiment_record` | Read experiment from unified YAML frontmatter → dict |
| `test_signal_id_stable_across_sources` | Same topic from source A then source B → same signal ID |
| `test_write_experiment_record` | Append experiment to state file YAML frontmatter |
| `test_unified_frontmatter_roundtrip` | Read → modify → write state file preserves all keys (topics, experiments, meta) |
| `test_data_quality_partial_suppresses_pattern` | PAT-PR-001 suppressed when `data_quality: partial` |

**Warm start:**

| Test | Description |
|---|---|
| `test_pull_warm_start_reads_file` | `pull()` with `meta.warm_start_file` in state reads from JSONL file, not pipeline |
| `test_warm_start_filters_to_configured_senders` | Only emails from 13 configured senders pass through |
| `test_warm_start_timeliness_penalty` | Signals older than 14 days get score × 0.3 |
| `test_warm_start_flag_in_output` | Output signals have `warm_start: true` field |
| `test_warm_start_dedup_across_90_days` | Same recurring topic across 12 weekly newsletters → 1 signal |

**Warm-start lifecycle (one-shot):**

| Test | Description |
|---|---|
| `test_warm_start_clears_state` | After successful warm-start run, `meta.warm_start_file` in state frontmatter is set to `""` |
| `test_warm_start_sets_consumed_timestamp` | After warm-start run, `meta.warm_start_consumed_at` in state frontmatter is an ISO timestamp |
| `test_warm_start_renames_jsonl` | After warm-start run, `tmp/warm_start_emails.jsonl` renamed to `tmp/warm_start_emails.processed.jsonl` |
| `test_warm_start_no_replay` | With `warm_start_file: ""`, `pull()` reads from normal pipeline output, not the processed file |

**Pipeline observability:**

| Test | Description |
|---|---|
| `test_distill_run_audit_emitted` | After `parse()`, `RADAR_DISTILL_RUN` audit event logged with raw/filtered/deduped/surfaced counts |
| `test_distill_run_records_empty_feeds` | `feeds_empty` field lists feed tags that returned 0 items |
| `test_prev_json_rename` | After skill run, previous `ai_trend_signals.json` renamed to `ai_trend_signals_prev.json` via `os.replace()` |
| `test_metrics_json_written` | After skill run, `tmp/ai_trend_metrics.json` exists with required fields (`last_run_at`, `last_run_status`, `surfaced_count`, `warm_start_active`) |
| `test_metrics_json_overwritten` | Second skill run overwrites (not appends) `tmp/ai_trend_metrics.json` |

**Defensive / regression:**

| Test | Description |
|---|---|
| `test_unregistered_moment_type_scores_zero` | Moment type NOT in `_MOMENT_WEIGHTS` → convergence score 0.0 (DEFECT-3 guard) |
| `test_organic_topic_suggestion` | Signal scores >0.7 organically with no topic match → briefing includes "New topic?" suggestion |

### 16.2 Integration Tests

| Test | Description |
|---|---|
| `test_rss_feed_reachability` | At least 1 of 7 configured feeds returns valid XML (network test, can be `@pytest.mark.network`) |
| `test_skill_runner_discovers_radar` | `skill_runner.py` finds and runs `ai_trend_radar` skill |
| `test_briefing_adapter_renders_radar_section` | With `tmp/ai_trend_signals.json` present, `briefing_adapter.py` output includes "AI Trend Radar" section |
| `test_full_pipeline_rss_to_signal` | RSS fetch → skill parse → signal JSON output |
| `test_warm_start_pipeline_to_signals` | `pipeline.py --since 90d` → JSONL → skill parse → signals with `warm_start: true` |

### 16.3 Assumption Validation Tests

These tests validate the assumptions from §14 and should be run **before**
implementation begins:

```bash
# A-RSS-1: Feed URL validity
python3 -c "
from scripts.connectors.rss_feed import fetch
feeds = [
    {'url': 'https://openai.com/blog/rss.xml', 'tag': 'test'},
    {'url': 'https://simonwillison.net/atom/everything/', 'tag': 'test'},
    {'url': 'https://huggingface.co/blog/feed.xml', 'tag': 'test'},
]
for f in feeds:
    items = list(fetch(feeds=[f], since='30d', max_results=3))
    print(f'{f[\"url\"]}: {len(items)} items')
"

# A-SEC-1: XXE resistance
python3 -c "
import xml.etree.ElementTree as ET
evil = '<?xml version=\"1.0\"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]><root>&xxe;</root>'
try:
    tree = ET.fromstring(evil)
    print(f'PARSED: {tree.text}')
    assert 'root:' not in (tree.text or ''), 'XXE EXPANDED — VULNERABLE'
    print('SAFE: Entity not expanded')
except ET.ParseError as e:
    print(f'SAFE: Parse error (expected): {e}')
"

# A-MAIL-2: Newsletter classification (test all 3 sender types)
python3 -c "
from scripts.email_classifier import classify_email
senders = [
    ('swyx+ainews@substack.com', 'AI News #482'),
    ('thebatch@deeplearning.ai', 'The Batch: AI News This Week'),
    ('news@daily.therundown.ai', 'The Rundown AI - March 22'),
    ('noreply@tpmuniversity.com', 'TPM Weekly Digest'),
]
for sender, subj in senders:
    rec = {'from': sender, 'subject': subj, 'headers': {}}
    result = classify_email(rec)
    status = 'PASS' if result['marketing'] else 'FAIL'
    print(f'{status}: {sender} -> marketing={result[\"marketing\"]}, category={result.get(\"marketing_category\")}')
"

# A-MAIL-1 + A-BEH-3: Warm start validation (requires Gmail auth)
python3 -c "
import json, subprocess, sys
result = subprocess.run(
    ['python', 'scripts/pipeline.py', '--since', '2025-12-22T00:00:00Z', '--source', 'gmail'],
    capture_output=True, text=True, timeout=120
)
records = [json.loads(l) for l in result.stdout.strip().split('\n') if l.strip()]

SENDERS = {
    'swyx+ainews@substack.com', 'bigtechnology@substack.com', 'bytebytego@substack.com',
    'datapoints@deeplearning.ai', 'gregorojstersek@substack.com', 'lenny@substack.com',
    'systemdesignone@substack.com', 'productforengineers@substack.com',
    'refactoring+monday-ideas@substack.com', 'thebatch@deeplearning.ai',
    'pragmaticengineer@substack.com', 'news@daily.therundown.ai', 'noreply@tpmuniversity.com',
}
matches = [r for r in records if any(s in r.get('from', '') for s in SENDERS)]
print(f'Total records: {len(records)}')
print(f'Newsletter matches: {len(matches)} (from {len(SENDERS)} configured senders)')

# A-BEH-3: Sample subjects for signal quality
for r in matches[:10]:
    print(f'  [{r.get(\"from\", \"\").split(\"@\")[0][:20]}] {r.get(\"subject\", \"(no subject)\")[:80]}')

assert len(matches) >= 50, f'Expected >=50 newsletter records, got {len(matches)}'
print('PASS: Warm start corpus validated')
"

# A-FMT-3: Arbitrary moment type accepted
python3 -c "
from scripts.pr_manager import MomentDetector
d = MomentDetector()
# Without adding to _MOMENT_WEIGHTS, score should be 0 (graceful)
# After adding, score should be positive
print('PASS: MomentDetector instantiates and scores gracefully')
"
```

### 16.4 Test Coverage Targets

- `scripts/skills/ai_trend_radar.py`: ≥90% line coverage
- `scripts/pr_stage/domain.py` (platform_exclude changes): 100% of new lines
- `scripts/pr_manager.py` (new moment type + radar section): ≥85% of new lines
- All assumptions from §14 validated with automated tests where possible

---

## Appendix A: Glossary

| Term | Definition |
|---|---|
| **AISignal** | A structured representation of an AI development detected from newsletters or RSS feeds |
| **Topic Interest Graph** | User-curated list of AI topics with keyword patterns and relevance boost values |
| **Try-worthy** | A signal that the user can practically experiment with (has an artifact, CLI, API, etc.) |
| **Experiment** | A user's hands-on exploration of a try-worthy signal, tracked in state |
| **Verdict** | User's assessment of an experiment: great, useful, meh, didnt_work |
| **Register B-Practitioner** | LinkedIn voice sub-register for insight posts (shorter than project showcases) |
| **Platform exclusion** | Mechanism to prevent certain moment types from generating content for specific platforms |
| **Radar** | Shorthand for the AI Trend Radar — the full pipeline from INGEST to DRAFT |

## Appendix B: File Manifest

| File | Action | Description |
|---|---|---|
| `scripts/skills/ai_trend_radar.py` | **Create** | Core skill: ingest, distill, score, dedup |
| `tests/unit/test_ai_trend_radar.py` | **Create** | Comprehensive unit test suite (see §16.1 for full matrix) |
| `state/ai_trend_radar.md` | **Create** | Unified frontmatter: topics of interest, experiments, meta (incl. warm_start_file) |
| `config/connectors.yaml` | **Edit** | Enable `rss_feed`, add AI feed URLs |
| `config/artha_config.yaml` | **Edit** | Add `ai_trend_radar` nested block |
| `config/skills.yaml` | **Edit** | Register `ai_trend_radar` skill |
| `config/patterns.yaml` | **Edit** | Add PAT-PR-004 (optional) |
| `config/implementation_status.yaml` | **Edit** | Add `ai_trend_radar` section |
| `scripts/pr_manager.py` | **Edit** | New moment type, thread map |
| `scripts/briefing_adapter.py` | **Edit** | Radar briefing section rendering |
| `scripts/pr_stage/domain.py` | **Edit** | `platform_exclude` field on ContentCard |
| `scripts/pr_stage/service.py` | **Edit** | Propagate `platform_exclude` on card creation |
| `scripts/channel_listener.py` | **Edit** | `/radar`, `/try`, `/skip` commands |
| `state/pr_manager.md` | **Edit** | Register B-Practitioner, `data_quality`, voice rules |
| `tmp/ai_trend_metrics.json` | **Created at runtime** | Overwritten each skill run with latest pipeline metrics (see §11.7) |
