# Work OS — User Guide

Work OS is the professional intelligence layer of Artha. It reads your calendar, work items, communications, and project state, then gives you structured briefings, meeting prep, and career evidence — all in under 2 seconds from cached state.

---

## What Work OS Is (and Isn't)

Work OS is **not a chatbot for your work email**. It is a structured daily briefing system for knowledge workers that:

- Fetches work data once (on `/work refresh`) and caches it in `state/work/` Markdown files
- Serves every subsequent command from cache in <2s
- Keeps work data 100% separate from personal Artha data (hard separation — no cross-contamination)
- Works entirely offline if you refresh beforehand

---

## Prerequisites

| Requirement | Why |
|-------------|-----|
| `work.enabled: true` in `user_profile.yaml` | Activates all Work OS domains |
| ADO configured (`ado_org_url`, `ado_project`) | Work item queries |
| M365 / Graph access | Calendar, comms, people data |
| Agency CLI (optional) | Richer data via ADO/WorkIQ/ICM MCP servers |

To check your setup:

```
/work health
```

---

## First-Time Setup

Run the guided bootstrap interview — it asks 12 questions covering your role, team, timezone, sprint cadence, ADO config, and privacy preferences:

```
/work bootstrap
```

Or if you have an existing MD file with structured history (e.g., an inflection-point narrative):

```
/work bootstrap import path/to/file.md
```

Use `--dry-run` to preview what would be written without committing.

---

## The Daily Workflow

### Step 1 — Refresh (once per morning)

```
/work refresh
```

Fetches fresh data from all configured providers (ADO, WorkIQ, ICM, Graph/M365) and writes state files to `state/work/`. Takes up to 60 seconds. Everything after this is instant.

> **Tip:** If you run `/catch-up` in the morning, Work OS refresh happens automatically as a post-catch-up stage — no need to run it separately.

### Step 2 — Morning Briefing

```
/work
```

Full work briefing: today's calendar with readiness scores, active sprint health, open items, comms signals, and career/org alerts. Footer shows data age and staleness warnings.

### Step 3 — Focus by Area

| Command | What it shows |
|---------|---------------|
| `/work pulse` | 30-second status snapshot (sprint + top items) |
| `/work sprint` | Delivery health, burndown, blockers |
| `/work prep` | Today's meetings with readiness scoring |
| `/work comms` | Email/Teams signals, exec visibility |
| `/work people <name>` | Person lookup — org context, collaboration history |
| `/work projects` | All active projects and their state |
| `/work sources` | Registered data sources |
| `/work docs` | Recent work artifacts |
| `/work graph` | Stakeholder influence map with trajectory icons |

---

## Meeting Intelligence

### Before a Meeting

```
/work prep
```

Shows today's meetings with a readiness score (0–100) based on: recurring meeting history, carry-forward items, attendee seniority, open action items, and high-stakes keywords.

### During a Meeting (Live Assist)

```
/work live <meeting-id>
```

Surfaces attendee context, open decisions, carry-forward items, and project context for the named meeting.

### After a Meeting

```
/work notes <meeting-id>
```

Captures decisions (D-NNN IDs), action items (OI-NNN IDs), and notes. Appends atomically to `state/work/work-notes.md`.

For quick capture without structure:

```
/work remember <text>
```

---

## Career & Performance

### Connect Cycle Prep

```
/work connect-prep
```

Assembles evidence for your review cycle: matched milestones, scope expansion arc, visibility events, manager voice quotes, and evidence gaps.

```
/work connect-prep --calibration
```

Generates a third-person calibration defense brief formatted for use in a calibration meeting (manager talking points, impact table, readiness signal).

### Promotion Readiness

```
/work promo-case
```

Analyzes your project journeys for scope arc, evidence density, and visibility events. Outputs a promotion readiness score with gap analysis.

```
/work promo-case --narrative
```

Generates a full promotion narrative Markdown draft ready for submission.

### Project Journey Timeline

```
/work journey [project-name]
```

Shows milestone timeline with ADO/IcM/doc provenance — the base layer for promo-case and Connect evidence.

---

## Decision Support

```
/work decide <context>
```

Surfaces related past decisions, project context, and a 6-question decision frame. Allocates a D-NNN ID and logs a skeleton entry to `state/work/work-decisions.md`.

---

## Status Memos & Content

### Weekly Status Memo

```
/work memo --weekly
```

Auto-drafts a weekly status memo from sprint data, decisions, and delivery notes via the Narrative Engine.

### Custom Memo

```
/work memo
```

Interactive Narrative Engine — choose template: `weekly_memo`, `talking_points`, `boundary_report`, `connect_summary`, `escalation`, `decision`.

### Team Newsletter

```
/work newsletter [period]
```

Drafts a team newsletter from sprint + decision + career data (e.g., `--period "last 2 weeks"`).

### LT Deck

```
/work deck [topic]
```

Scaffolds an LT deck content outline for the given topic.

---

## Work-Life Boundary

```
/work boundary
```

Surfaces boundary violations: after-hours meeting patterns, weekend pings, meeting load vs. focus block ratio, and policy compliance status.

---

## Absence Recovery

```
/work return [window]
```

Catch-up after PTO or travel. Summarizes what changed while you were away across all domains. Window examples: `3d`, `1w`, `2w`.

---

## Data Sources

### View Registered Sources

```
/work sources
```

### Add a New Source

```
/work sources add <url> [context]
```

Registers a new data source (document, dashboard, runbook) in `state/work/work-sources.md` for use in briefings and evidence assembly.

---

## 11 Work OS Domains

Each domain has a state file in `state/work/` that is updated on refresh:

| Domain | State File | What It Tracks |
|--------|-----------|----------------|
| `work-calendar` | work-calendar.md | Today/week meetings, readiness, carry-forward |
| `work-comms` | work-comms.md | Email/Teams signals, exec engagement, unread count |
| `work-projects` | work-projects.md | All active projects, status, blockers |
| `work-people` | work-people.md | Collaboration graph, stakeholder seniority, visibility events |
| `work-notes` | work-notes.md | Post-meeting notes, decisions (D-NNN), action items (OI-NNN) |
| `work-boundary` | work-boundary.md | Meeting load, hours, after-hours patterns |
| `work-career` | work-career.md | Role alignment, scope arc, career velocity |
| `work-performance` | work-performance.md | Sprint metrics, delivery score, goals progress |
| `work-sources` | work-sources.md | Registered data source registry |
| `work-project-journeys` | work-project-journeys.md | Long-running program timelines with milestone provenance |
| `work-org-calendar` | work-org-calendar.md | Org dates: Connect deadlines, fiscal events, rewards season |

---

## Agent Tiers

Work OS ships three agent definitions for use with the Agency CLI:

| Tier | File | Best For |
|------|------|----------|
| `artha-work` | `config/agents/artha-work.md` | Any M365 user (Graph baseline) |
| `artha-work-enterprise` | `config/agents/artha-work-enterprise.md` | M365 + Azure DevOps |
| `artha-work-msft` | `config/agents/artha-work-msft.md` | Microsoft employees (ADO + WorkIQ + ICM + Bluebird) |

The correct tier is automatically selected based on your `user_profile.yaml` provider config.

---

## Privacy & Separation

- Work state files live in `state/work/` — **never mixed** with personal state
- `user_profile.yaml` is gitignored — your org, team, and ADO config never reach GitHub
- Cross-surface bridge artifacts (`work_load_pulse.json`, `work_context.json`) carry **only aggregate numeric metrics** — no strings, names, or content cross the boundary
- PII keywords configured in `user_profile.yaml` under `work.redact_keywords` are stripped before any state file is written

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "State file is stale" warning | Run `/work refresh` |
| Missing domain data | Check `/work health` → "Provider coverage" |
| ADO queries return nothing | Verify `ado_org_url`, `ado_project`, `ado_area_path` in `user_profile.yaml` |
| Agency not found | Install Agency CLI; Work OS falls back to direct providers automatically |
| Bootstrap not completing | Run `/work bootstrap` interactively; use `--dry-run` to debug |

For detailed connector diagnostics: see [docs/connectors.md](connectors.md).

---

## Quick-Reference Command Card

```
/work                    Morning briefing
/work pulse              30-second snapshot
/work refresh            Fetch fresh data (required daily)
/work prep               Today's meetings with readiness score
/work notes <id>         Post-meeting capture
/work remember <text>    Quick micro-capture
/work sprint             Sprint & delivery health
/work people <name>      Person context lookup
/work connect-prep       Review cycle evidence assembly
/work promo-case         Promotion readiness report
/work promo-case --narrative  Full promo narrative draft
/work decide <context>   Structured decision support
/work memo --weekly      Auto-draft weekly status
/work return [Nd]        Absence recovery catch-up
/work health             System & connector health check
/work bootstrap          Guided first-time setup
```
