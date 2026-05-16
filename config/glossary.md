# Artha Canonical Glossary

Authoritative terms for all Artha agent surfaces. Use the canonical term in output; users may speak naturally.
Hard cap: 3 KB. Do not add entries without removing equivalent text.

---

## Terms

**Open Item** — A tracked task or commitment requiring action before a deadline. Stored in `state/open_items.md`.
_Avoid_: task, to-do, todo, action item, ticket.

**Catch-up** — A pipeline execution that reads signals, processes domains, and produces a briefing.
_Avoid_: run, session, pipeline run, execution.

**Briefing** — The markdown document produced at the end of a catch-up. The human-readable output.
_Avoid_: report, summary, update, digest (digest is a specific briefing format, not a synonym).

**Domain** — A structured life area (e.g., finance, health, immigration) with its own prompt, state file, and signal sources.
_Avoid_: category, area, section, topic, module.

**Signal** — A raw data point ingested from a connector before triage.
_Avoid_: event, data point, notification, input.

**Alert** — A signal that passed triage and warrants surfacing in the briefing. Alerts have severity (P1–P4).
_Avoid_: warning, flag, notification, issue.

**Sprint** — A time-boxed goal focus period. Goals have at most one active sprint at a time.
_Avoid_: cycle, phase, period, push, streak.

**Connector** — A data-source integration (email, calendar, bank, etc.) that produces signals.
_Avoid_: integration, plugin, source, feed.

**Pipeline** — The orchestration layer (`scripts/pipeline.py`) that sequences connectors → domains → briefing generation.
_Avoid_: system, backend, engine, runner.

**State File** — A file in `state/` that persists structured data between catch-ups.
_Avoid_: data file, config file, memory file, store.

**Goal** — A named outcome tracked in `state/goals.md` with progress and sprints.
_Avoid_: objective, target, KPI, intention.

**Sentinel** — A scheduled or threshold-based monitor that fires an alert when a condition is met.
_Avoid_: monitor, watcher, trigger, checker.

**Scenario** — A conditional plan in `state/scenarios.md` with an "if X then Y" structure.
_Avoid_: plan, contingency, playbook.

**Decision** — A pending choice in `state/decisions.md` requiring user input before a deadline.
_Avoid_: question, choice, option.

**Digest** — A compressed briefing format (~300 words) for low-context sessions. A format variant of a briefing, not a synonym.
_Avoid_: using "digest" and "briefing" interchangeably — they are distinct: digest is one format of briefing.

---

## Relationships

- A **catch-up** produces exactly one **briefing**.
- **Signals** are ingested by **connectors** and triaged into **alerts**.
- **Alerts** drive **open items** when action is required.
- **Domains** each have one **state file** and one prompt section.
- **Goals** contain zero or one active **sprint**.
- **Sentinels** fire **alerts**; they do not create open items directly.
- A **digest** is a short-format **briefing**; all other briefing properties apply.
