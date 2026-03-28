---
schema_version: "1.0"
domain: work-incidents
last_updated: "2026-03-28T00:00:00Z"
work_os: true
generated_by: work_domain_writers
encrypted: false
active_count: 0
sev_summary: {}
---

# Work Incidents — IcM Incident Tracker

*Active and recent incidents from IcM via Kusto golden queries (GQ-050–GQ-057).
Updated on `/work refresh` when Kusto access is available.*

## Active Incidents (Sev 0–2)

| ID | Severity | Title | Age | Owning Team | Region | Link |
|----|----------|-------|-----|-------------|--------|------|

<!-- Populated by write_incidents_state() from GQ-050 results -->

## Incident Summary (90-day)

| Metric | Value | Source |
|--------|-------|--------|
| Total incidents (90d) | — | GQ-051 |
| Sev 0 count | — | GQ-051 |
| Sev 1 count | — | GQ-051 |
| Sev 2 count | — | GQ-051 |
| MTTR Sev 1 | — | GQ-053 |
| MTTR Sev 2 | — | GQ-053 |
| Top recurring monitor | — | GQ-055 |

## Incident Trend

<!-- Weekly incident counts from GQ-054, rendered as sparkline or table -->

## Top Owning Teams (90-day)

| Team | Total | Sev 0-1 | Sev 2 | Avg Age (days) |
|------|-------|---------|-------|----------------|

<!-- From GQ-052 -->
