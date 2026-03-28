---
schema_version: "1.0"
domain: work-products
priority: P3
sensitivity: standard
last_updated: 2026-03-28T00:00:00
---
# Work Products Domain Prompt

## Purpose
Durable product and technology knowledge base that persists across projects.
Products (xStore, Direct Drive, Blob Storage, etc.) are long-lived; projects
(XPF migration, Rubik, Armada) are time-bound changes to those products.
This domain captures *what the products ARE* — architecture, components,
dependencies, team ownership, data sources — so Artha can inject product
context into meetings, memos, and reflections without re-learning each session.

Eliminates: "What team owns the Direct Drive data plane?" cognitive cost.
Feeds: `/work prep` (product context injection), `/work reflect` (product-tagged
accomplishments), `/work memo` (product-aware narratives), `/work promo-case`
(product scope evidence).

## Data Source
Primary: User-authored captures via `/work products add <name>`
Auto-capture (Phase 2): Extract product references from meeting notes, design
  reviews, architecture discussions, and onboarding documents via WorkIQ.
Cross-reference: Link products to active projects via `state/work/work-projects.md`
  area_path and tags.

## State File Architecture

### Index file: `state/work/work-products.md` (NOT encrypted — no PII)
Lightweight taxonomy with per-product summary and pointer to deep file.

### Deep files: `state/work/products/<slug>.md` (NOT encrypted)
One file per product with full architecture, components, dependencies, teams,
data sources, and Kusto tables.

## Index Entry Format (§work-products.md)
```
## <Product Name>
- Slug: <kebab-case-identifier>
- Layer: <data-plane | control-plane | offering | platform | tooling>
- Status: <active | deprecated | planned>
- Team: <owning team name>
- Active Projects: <comma-separated project names that are changing this product>
- Deep File: products/<slug>.md
- Summary: <1-2 sentence description of what the product does>
- Last Updated: <YYYY-MM-DD>
```

## Deep File Format (§products/<slug>.md)
```yaml
---
schema_version: "1.0"
domain: work-product-deep
product: "<Product Name>"
slug: "<kebab-case>"
layer: "<data-plane | control-plane | offering | platform | tooling>"
team: "<owning team>"
last_updated: "<ISO timestamp>"
generated_by: "work_domain_writers"
work_os: true
---

# <Product Name>

## Architecture Overview
<2-5 paragraphs: what the product does, where it sits in the stack,
key design decisions, scale characteristics>

## Components
| Component | Purpose | Owner | Status |
|-----------|---------|-------|--------|

## Dependencies
| Dependency | Type | Direction | Notes |
|-----------|------|-----------|-------|

## Team & Stakeholders
| Role | Person | Context |
|------|--------|---------|

## Data Sources & Observability
| Source | Type | Cluster/URL | Notes |
|--------|------|-------------|-------|

## Operations — ADO & IcM Mapping

### ADO Area Paths
| Area Path | Scope | Notes |
|-----------|-------|-------|

### IcM Service Tree & Queues
| Service/Queue | Component | Severity Routing | Notes |
|---------------|-----------|------------------|-------|

## Related Projects
| Project | Relationship | Status |
|---------|-------------|--------|

## Key Metrics
| Metric | Current Value | Source | Last Updated |
|--------|---------------|--------|-------------|

## Knowledge Log
<!-- Append-only: new learnings, architecture changes, decisions -->
### <YYYY-MM-DD>
<What was learned and from what context (meeting, doc, email)>
```

## Extraction Rules
When encountering product information in meetings, emails, or documents:
1. **Product Name** — canonical name (check index for existing entry first)
2. **Layer** — classify: data-plane, control-plane, offering, platform, tooling
3. **Components** — named subsystems or services
4. **Dependencies** — upstream/downstream product relationships
5. **Team** — owning team and key stakeholders
6. **Architecture facts** — design decisions, scale characteristics, tech stack
7. **ADO Area Path** — when an ADO work item references a product (via area_path), capture the path mapping
8. **IcM Queue** — when an incident references a product component, capture the IcM service/queue name

## IcM & ADO Progressive Learning Rules
IcM queues and ADO area paths are aligned to products and components. These are learned
progressively — not all at once — from operational signals:

### Learning triggers
- **Incident captured** (`work-incidents.md` update or `/work notes` with incident context):
  Cross-reference the IcM service name and queue against this product's component table.
  If a match is found, fill in the IcM queue row for that component.
- **ADO work item routed** (area_path in `work-projects.md`):
  If the area_path starts with a known product's ADO prefix, link the work item to that product.
- **Meeting notes mention IcM queue** (e.g., "escalated to XStore\XFE queue"):
  Extract queue name and map to the component.
- **On-call rotation discovered** (e.g., "on-call alias: xstore-oncall@"):
  Add to the product's Team & Stakeholders table.

### Learning behavior
- **First time a queue is seen**: add to IcM table with `[auto-learned]` tag, prompt user confirmation
- **Subsequent sightings**: increase confidence, remove `[auto-learned]` tag after 3 confirmations
- **Conflicting mappings**: flag for user review (e.g., same queue mapped to two products)
- **ADO area paths are stable**: once learned, they rarely change. Mark with `[confirmed]` after first user validation.

### Cross-domain triggers
- `work-incidents.md` → extract IcM service/queue → match to product component → update deep file
- `work-projects.md` → extract ADO area_path → match to product prefix → update product index `Active Projects`
- `work-people.md` → extract on-call/team aliases → match to product team → update stakeholder table

## Update Rules
- **Append-only for facts**: New components, dependencies, and knowledge log entries are appended
- **Overwrite for architecture**: If a product's architecture fundamentally changes (rewrite, deprecation), update the Architecture Overview section
- **Never delete without user confirmation**: Products are durable; even deprecated products retain their deep file
- **Cross-reference on write**: When updating a product, check if active projects reference it and update the `Active Projects` field in the index

## Routing Rules
- **Index writes** → `state/work/work-products.md` (update or append entry)
- **Deep file writes** → `state/work/products/<slug>.md` (create or update)
- **New product** → create both index entry AND deep file stub
- **Existing product update** → update deep file, touch index `Last Updated`

## Staleness Policy
- Product knowledge decays **much slower** than project knowledge
- Staleness threshold: **180 days** (6 months) — vs 14 days for projects
- Products not referenced in 180 days: marked as 🔵 LOW alert (may need review)
- Products with `status: deprecated` are never flagged as stale

## Alert Thresholds
🟠 STANDARD: Product architecture change detected in design review or meeting transcript
🔵 LOW: Product not referenced in 180 days (staleness check); new product auto-detected

## Cross-Domain Triggers
- **work-projects**: When a project references a product (via area_path or tags), link them
- **work-people**: Product team members populate people graph
- **meetings (prep)**: Meeting title keyword match → inject product context from deep file
- **narrative (memo/newsletter)**: Product-aware accomplishment categorization
- **reflection loop**: Accomplishments tagged by product in Accomplishment Index
- **golden-queries**: KQL queries tagged by product for data context
- **work-sources**: Data sources linked to products

## Briefing Format (used by `/work products`)
```
### Product Knowledge Index
Products tracked: N | Last updated: YYYY-MM-DD

| Product | Layer | Team | Active Projects | Status |
|---------|-------|------|-----------------|--------|
```

## Briefing Format (used by `/work products <name>`)
```
### <Product Name> — Deep Knowledge
Layer: <layer> | Team: <team> | Status: <status>

Architecture: <2-line summary>
Components: <count> tracked
Dependencies: <count> upstream, <count> downstream
Active Projects: <list>
Recent Knowledge: <last 3 knowledge log entries>
```

## Meeting Context Injection Format
When a meeting title matches a product keyword, inject up to 4 lines:
```
── Product Context ──
  📦 <Product Name> (<layer>) — <1-line summary>
  👥 Key contacts: <2-3 names from team table>
  🔗 Active projects: <project list>
  📊 Key metric: <most important metric + current value>
```

## Prompt Instructions
1. NEVER store PII in product files — use role/alias, not personal details
2. Product slugs must be kebab-case, globally unique, and stable (never rename)
3. When auto-capturing from meetings, present a confirmation before writing:
   "Detected product reference: [name]. Add to product knowledge? [yes/no]"
4. Deep files are append-heavy — use Knowledge Log for incremental learnings
5. Architecture Overview is the "expensive" section — only update on significant changes
6. Cross-reference with `work-projects.md` area_path to auto-link projects to products
7. When a product is deprecated, set `status: deprecated` but NEVER delete the deep file
