---
schema_version: "1.0"
domain: work-sources
priority: P3
sensitivity: standard
last_updated: 2026-03-24T00:00:00
---
# Work Sources Domain Prompt

## Purpose
Curated, indexed registry of data references encountered in work: dashboard
links, Kusto queries, Power BI reports, SharePoint lists, information portals,
and wiki pages. Eliminates "I know I saw that dashboard somewhere" cognitive
cost. Feeds /work decide with "where to find the data."

## Data Source
Primary: User-authored captures via /work sources add <url> [context]
Auto-capture (Phase 2): work_source_capture action — detects URLs in meeting notes
  and email metadata and proposes them for registration
Decision-linked (Phase 3): /work decide queries work-sources for relevant data portals

## State File Schema
State file: `state/work/work-sources.md` (NOT encrypted — URLs and labels, no PII)

### Entry format (§14.7)
```
## [source title]
- URL: [link]
- Answers: [what question this source answers — required, 10-80 chars]
- Shared by: [stakeholder alias — omit if unknown]
- First seen: [YYYY-MM-DD]
- Last referenced: [YYYY-MM-DD]
- Tags: [project, domain, topic tags — comma separated]
- Type: [dashboard | kusto-query | power-bi | sharepoint-list | wiki | portal | other]
```

## Extraction Rules
For every source, require:
1. **URL** — full URL, validated (starts with https://)
2. **Answers** — plain English description of what question this source answers
3. **Type** — from the enum above
4. **Tags** — at least one tag (project name or domain)
Optional:
- Shared by, First seen, Last referenced (populated automatically if known)

## Search and Retrieval Rules
/work sources [query]:
1. Full-text search on: title, Answers, Tags
2. Rank results: most recently referenced first, then most recently added
3. Deduplication: same URL = same entry (update Last referenced only)
4. Max display: 10 results per query

## Maintenance Rules
- Entries not referenced in 90 days: marked as stale (🔵 LOW alert)
- Dead URLs (404 on validation): flagged for user review
- Max 200 entries; LRU eviction for older entries when limit reached

## Alert Thresholds
🟡 STANDARD: URL added via auto-capture that has not been confirmed by user (requires confirmation)
🔵 LOW: Source not referenced in 90 days (staleness check); new source added

## Cross-Domain Triggers
- **work-decide**: consulted for relevant data portals when /work decide runs
- **work-projects**: links data sources to active project decisions
- **work-people**: "shared by" field for source provenance

## Briefing Format (used by /work sources [query])
```
### Data Sources matching "[query]"
1. [title] — [type] — last used [date]
   Answers: [what it answers]
   URL: [link]
   Tags: [tags]
```

## Prompt Instructions
1. NEVER store credentials, PAT tokens, SAS tokens, or authentication parameters in URLs
2. URLs must be validated as HTTPS before storage
3. "Answers" field is mandatory — a source without context is useless
4. Auto-capture URLs must present a one-line confirmation before writing to state
   (e.g. "Found URL in meeting notes: [url] — Add to sources registry? [yes/no]")
5. Corporate internal URLs (*.microsoft.com, *.sharepoint.com, *.visualstudio.com) are safe to store
6. Do not store public internet URLs unless they are legitimate work data references
