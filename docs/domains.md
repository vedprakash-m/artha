# Artha Domains

Artha organizes your life data into 17 domains. Each domain has a corresponding
state file in `state/`, a prompt file in `prompts/`, and optional skill modules
in `scripts/skills/`.

## Domain Catalog

| # | Domain | State File | Prompt File | Description |
|---|--------|-----------|-------------|-------------|
| 1 | **finance** | `state/finance.md` | `prompts/finance.md` | Accounts, balances, taxes, insurance claim tracking |
| 2 | **health** | `state/health.md` | `prompts/health.md` | Medical records, prescriptions, appointments, insurance |
| 3 | **kids** | `state/kids.md` | `prompts/kids.md` | School calendar, assignments, activities, milestones |
| 4 | **home** | `state/home.md` | `prompts/home.md` | Maintenance schedule, utilities, appliances, vendors |
| 5 | **travel** | `state/travel.md` | `prompts/travel.md` | Upcoming trips, bookings, loyalty programs, passport/visa requirements |
| 6 | **vehicle** | `state/vehicle.md` | `prompts/vehicle.md` | Registration, insurance, maintenance schedule, fuel records |
| 7 | **insurance** | `state/insurance.md` | `prompts/insurance.md` | Policy numbers, coverage, premiums, claim history |
| 8 | **estate** | `state/estate.md` | `prompts/estate.md` | Wills, beneficiaries, account inventory for estate planning |
| 9 | **immigration** | `state/immigration.md` | `prompts/immigration.md` | Visa status, case numbers, deadlines, document expiry |
| 10 | **social** | `state/social.md` | `prompts/social.md` | Relationships, occasions, gift ideas, reconnect intelligence |
| 11 | **learning** | `state/learning.md` | `prompts/learning.md` | Courses, reading list, certifications, skill goals |
| 12 | **comms** | `state/comms.md` | `prompts/comms.md` | Communication patterns, pending replies, action items from email |
| 13 | **goals** | `state/goals.md` | `prompts/goals.md` | Goal sprints, milestones, habit tracking, OKR-style structure |
| 14 | **digital** | `state/digital.md` | `prompts/digital.md` | Subscriptions, accounts, passwords hygiene, digital estate |
| 15 | **shopping** | `state/shopping.md` | `prompts/shopping.md` | Wishlist, pending orders, price alerts, household supplies |
| 16 | **boundary** | `state/boundary.md` | `prompts/boundary.md` | Work/life separation, contact filtering, noise reduction rules |
| 17 | **calendar** | `state/calendar.md` | `prompts/calendar.md` | Synthesized calendar context across all data sources |

## Domain State File Format

Each state file is a Markdown document with a YAML frontmatter block:

```markdown
---
domain: <name>
last_updated: YYYY-MM-DD
updated_by: artha-catchup | user_interview | bootstrap
schema_version: "1.0"
---

## Summary
[One-paragraph current status]

## Key Data
[Domain-specific structured data]

## Action Items
- [ ] ...

## Alerts
[Any time-sensitive items]
```

### Population Status

The `updated_by` frontmatter field indicates how the file was last populated:

| Value | Meaning |
|-------|---------|
| `bootstrap` | Placeholder data — not yet populated with real data |
| `user_interview` | Populated via `/bootstrap <domain>` interview |
| `artha-catchup` | Updated by automated email/calendar extraction |

Files with `updated_by: bootstrap` are shown with a ⚠ indicator in the dashboard.

## Domain Priority

Domains are ranked by urgency × importance for the catch-up briefing:

1. **P0 — Life-impacting deadlines**: estate, insurance, immigration (if applicable)
2. **P1 — Financial health**: finance, vehicle (registration/insurance)
3. **P2 — Family logistics**: kids, health, home
4. **P3 — Planning & enrichment**: travel, social, learning, goals
5. **P4 — Background maintenance**: digital, shopping, boundary, comms, calendar

Alert thresholds for each domain are defined in the corresponding `prompts/<domain>.md` file.
