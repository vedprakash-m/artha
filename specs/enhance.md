# Artha Enhancement Plan

> **Version:** 1.4 | **Date:** March 14, 2026 | **Status:** Draft — Pending Finalization
> **Scope:** New domains, connectors, skills, profile flexibility, cross-environment compatibility, and performance architecture
> **Prerequisite:** All existing 318 tests passing. Current architecture stable at v5.1.
>
> **v1.4 changes:** Specified prompt regression test execution model (real Claude API, `@pytest.mark.prompt_regression`, ≥80% accuracy threshold, excluded from default test run). Replaced `deadline_check` with `always_load: true` for finance/immigration/health (context cost acceptable; false-negative risk too high). Added routing correction graduation policy (`learned_at`, `expiry_after_days: 180`, auto-promote after 5 successful applications). Fixed mobile journal TOCTOU race (rename+recreate pattern). Added iCloud IMAP to §4.9 error UX contract table. Softened "Non-developer → ✅ Full" to "⚠ Improved". Added community domain unique alert justification (IRS $250 substantiation threshold, volunteer over-commitment). Rewrote Phase 3 i18n exit criterion to use synthetic test profiles. Fixed Phase 1a item count (11 not 12). Narrowed Claude watchdog detection (`claude.*Artha`). Added pre→post-mitigation probability to R5b. Assigned §10.1b (mobile journal) to Phase 2 as item 2.15. Added multi-currency dependency note on Phase 3 currency_rates skill. Specified vault TTL touch frequency (per-domain, every 5 domains). Clarified Todoist 2.6 (live sync) vs 2.14 (historical import). Added `triggers` array to subagent contract for cross-domain reasoning. Added connector dependency isolation (optional deps, ImportError graceful degradation).
> **v1.3 changes:** Split Phase 1 into 1a (infrastructure) and 1b (capabilities) with separate exit criteria. Moved community domain to Phase 2 (donation tracking feeds finance). Added prompt regression test framework (1.0i). Specified all 8 integration test cases. Fixed context pressure thresholds (YELLOW=100K, aligned with Tier 2 trigger). Fixed R16 contradiction (fixed priority order, not randomized; acknowledged bias). Added CSV dedup strategies per adapter. Added currency backfill migration note. Added DeprecateField to migration DSL. Derived SENSITIVE_DOMAINS from registry (not hardcoded). Added vault TTL touch for long catch-ups. Fixed session diff crash safety (timestamped checkpoints). Added auto_enable_when to caregiving+pets registry entries. Fixed onboarding menu (added comms pre-checked, fixed kids auto-enabled checkbox). Softened strategic objective. Added connector error UX contract table. Added mobile write journaling for sync conflict prevention. Added routing correction schema. Scoped i18n presets to immigration+tax for Phase 3. Added Pets routing Phase 2 marker. Added domain enable UX message. Expanded plugin validator forbidden imports. Added Tesseract preflight check. Fixed Artha.md→Artha.core.md path. Limited always_load to truly always-relevant domains. Added hit-rate sample size guard. Added requires_vault note to domain registry. Added wellness+Apple Health co-delivery requirement.
> **v1.2 changes:** Adopted feedback from 3 external reviews — fixed `auto_enable_when` injection vector (declarative format), rescoped Pets to Phase 1 reminders + Phase 2 full domain, added caregiving timezone, improved business routing disambiguation, marked Plaid plugin-only, deferred Garmin to Phase 4, added `--format` to view scripts, fixed vault guard size-check logic, narrowed watchdog gemini detection, expanded renter alert thresholds, added point-in-time exchange rate schema, added import preview modes, co-delivered domain registry + lazy loading, moved schema migration to Phase 1, added preflight.py update + integration test harness, added R15/R16 risks, answered all 7 open questions, added per-domain hit rate tracking.
> **v1.1 changes:** Dropped Docker distribution, added §10.3 Cross-Environment Compatibility, added `ARTHA_AGE_KEY` env-var fallback, updated risk register.

---

## Table of Contents

1. [Strategic Objective](#1-strategic-objective)
2. [Domain Enable/Disable Architecture](#2-domain-enabledisable-architecture)
3. [New Domains](#3-new-domains)
4. [New Connectors](#4-new-connectors)
5. [New Skills](#5-new-skills)
6. [Profile Flexibility & Household Modes](#6-profile-flexibility--household-modes)
7. [Internationalization](#7-internationalization)
8. [Data Import & Migration](#8-data-import--migration)
9. [Performance Architecture — Subagent Model](#9-performance-architecture--subagent-model)
10. [Reliability & Distribution Hardening](#10-reliability--distribution-hardening)
11. [Phased Delivery Plan](#11-phased-delivery-plan)
12. [Risk Register](#12-risk-register)
13. [Success Criteria](#13-success-criteria)
14. [Open Questions](#14-open-questions)

---

## 1. Strategic Objective

Deepen Artha's value for the current user while building the structural flexibility that makes future extensibility natural — serving diverse household types, life situations, and execution environments. Distribution-readiness is a consequence of this quality, not the primary design target.

**Target audience expansion:**

| Audience | Current Support | After Enhancement |
|----------|----------------|-------------------|
| Immigrant tech family with kids | ✅ Full | ✅ Full |
| Single professional, no kids | ⚠ Partial (family-centric language) | ✅ Full |
| Couple without children | ⚠ Partial | ✅ Full |
| Renter (no mortgage) | ⚠ Missing renter flows | ✅ Full |
| Pet owner | ❌ No domain | ✅ Full |
| Family caregiver (sandwich gen) | ❌ No domain | ✅ Full |
| Side-business/freelancer | ❌ No domain | ✅ Full |
| Fitness-focused individual | ⚠ Goal only, no data connectors | ✅ Full |
| Non-US resident | ❌ US-specific legal/gov | ✅ Preset-based |
| Non-developer user | ⚠ Requires Python + venv | ⚠ Improved (guided setup + bootstrap wizard; Python/age/OAuth prerequisites remain) |

---

## 2. Domain Enable/Disable Architecture

### 2.1 Current State

Domains are toggled via `domains.<name>.enabled: true|false` in `config/user_profile.yaml`. The `profile_loader.py` function `enabled_domains()` returns the active list. The pipeline reads `connectors.yaml` for data sources, and the catch-up workflow reads prompt files only for enabled domains.

**What works well:**
- Single source of truth (`user_profile.yaml`)
- `generate_identity.py` assembles `Artha.md` from only enabled domains
- Connectors independently `enabled: true|false` in `connectors.yaml`
- Skills independently `enabled: true|false` in `skills.yaml`

**What's missing:**
- No runtime CLI to enable/disable without editing YAML
- No onboarding wizard that presents domains as a menu
- No dependency graph (enabling `kids` should suggest `canvas_lms` connector)
- No validation that enabling a domain without its connector produces a meaningful experience
- Adding a new domain requires touching 4+ files manually

### 2.2 Proposed: Domain Registry with Dependency Graph

Create `config/domain_registry.yaml` — a single manifest that declares every domain, its dependencies, and its setup requirements:

```yaml
# config/domain_registry.yaml
schema_version: "1.0"

domains:
  immigration:
    display_name: "Immigration & Visa"
    description: "Track visa status, case milestones, document expiry for all family members"
    icon: "🛂"
    default_enabled: false          # Not everyone has immigration needs
    sensitivity: critical
    priority: P0
    requires_connectors: []         # Works from email routing alone
    optional_connectors: []
    requires_skills: [uscis_status, visa_bulletin]
    # Note: vault-awareness for skills is declared in skills.yaml (requires_vault: true),
    # not here. skill_runner.py reads skills.yaml for vault handling.
    optional_skills: []
    requires_profile_fields:
      - domains.immigration.context
    household_types: [single, couple, family, multi_gen]
    setup_questions:
      - key: domains.immigration.context
        ask: "Briefly describe your immigration situation (visa type, pending cases)."
      - key: domains.immigration.origin_country
        ask: "What is your country of birth? (For Visa Bulletin tracking)"
    conflicts: []
    suggests: [employment]          # Immigration often ties to employment

  finance:
    display_name: "Finance & Bills"
    description: "Unified bill tracking, spending alerts, net worth, tax prep"
    icon: "💰"
    default_enabled: true
    sensitivity: high
    priority: P0
    requires_connectors: []         # Works from email alone
    optional_connectors: [plaid]    # Real-time transactions
    requires_skills: []
    optional_skills: [credit_monitor]
    requires_profile_fields: []
    household_types: [single, couple, family, multi_gen]
    setup_questions:
      - key: domains.finance.institutions
        ask: "List your financial institutions (banks, brokerages, credit cards)."
      - key: domains.finance.alert_thresholds.bill_due_days
        ask: "How many days before a bill is due should I alert you? (default: 7)"
        default: 7
    conflicts: []
    suggests: [business]            # Side income users often need both

  kids:
    display_name: "Kids & School"
    description: "Grades, attendance, school calendar, college prep"
    icon: "🎒"
    default_enabled: false          # Only if children[] is non-empty
    auto_enable_when:
      field: "family.children"
      condition: non_empty            # non_empty | equals | greater_than | exists
    sensitivity: medium
    priority: P0
    requires_connectors: []
    optional_connectors: [canvas_lms]
    optional_skills: [school_calendar]
    household_types: [family, multi_gen]
    setup_questions: []             # Children are configured in family section
    conflicts: []
    suggests: []

  pets:
    display_name: "Pets & Animal Care"
    description: "Vet appointments, vaccinations, medications, insurance, licensing"
    icon: "🐾"
    default_enabled: false
    auto_enable_when:
      field: "domains.pets.animals"
      condition: non_empty
    sensitivity: standard
    priority: P2
    requires_connectors: []
    optional_connectors: []
    optional_skills: [pet_license]
    household_types: [single, couple, family, multi_gen]
    requires_profile_fields:
      - domains.pets.animals
    setup_questions:
      - key: domains.pets.animals
        ask: "Tell me about your pets (name, species, breed, age for each)."
    conflicts: []
    suggests: []

  caregiving:
    display_name: "Caregiving & Elder Care"
    description: "Track care recipients, medical schedules, Medicare/Medicaid, caregiver coordination"
    icon: "🤝"
    default_enabled: false
    auto_enable_when:
      field: "family.care_recipients"
      condition: non_empty
    sensitivity: high
    priority: P1
    requires_connectors: []
    optional_connectors: []
    requires_skills: []
    household_types: [single, couple, family, multi_gen]
    requires_profile_fields:
      - family.care_recipients
    setup_questions:
      - key: family.care_recipients
        ask: "Who do you provide care for? (name, relationship, location, conditions)"
    conflicts: []
    suggests: [estate, health]

  business:
    display_name: "Side Business / Freelance"
    description: "Invoice tracking, client payments, quarterly taxes, business expenses"
    icon: "💼"
    default_enabled: false
    sensitivity: high
    priority: P1
    requires_connectors: []
    optional_connectors: [plaid]
    requires_profile_fields:
      - domains.business.entity_name
    household_types: [single, couple, family]
    setup_questions:
      - key: domains.business.entity_name
        ask: "Business or freelance entity name?"
      - key: domains.business.type
        ask: "Type: freelance, LLC, S-Corp, rental_income, or other?"
    conflicts: []
    suggests: [finance]

  wellness:
    display_name: "Fitness & Wellness"
    description: "Exercise tracking, sleep, nutrition, mental health check-ins"
    icon: "🏃"
    default_enabled: false
    sensitivity: medium
    priority: P2
    requires_connectors: []
    optional_connectors: [apple_health, garmin, strava]
    requires_skills: []
    household_types: [single, couple, family, multi_gen]
    setup_questions:
      - key: domains.wellness.tracked_metrics
        ask: "What do you want to track? (exercise, sleep, weight, steps, heart_rate)"
        default: [exercise, sleep]
    conflicts: []
    suggests: [health]

  community:
    display_name: "Volunteering & Community"
    description: "Donation tracking, volunteer shifts, nonprofit board commitments"
    icon: "🏘️"
    default_enabled: false
    sensitivity: standard
    priority: P3
    requires_connectors: []
    optional_connectors: []
    requires_skills: []
    household_types: [single, couple, family, multi_gen]
    setup_questions:
      - key: domains.community.organizations
        ask: "Organizations you volunteer with or donate to regularly?"
    conflicts: []
    suggests: [finance]              # Donation tax tracking

  # --- existing domains follow the same schema ---
  # health, home, travel, vehicle, insurance, estate, social, learning,
  # comms, goals, digital, shopping, boundary, calendar, employment
  # (Each gets the same structured entry with household_types, setup_questions, etc.)
```

### 2.3 Domain Toggle Commands

Add runtime domain management without YAML editing:

```
/domains                    → List all domains (enabled/disabled/available)
/domains enable <name>      → Enable domain, run setup questions if needed
/domains disable <name>     → Disable domain (state file preserved, not deleted)
/domains setup <name>       → Re-run setup questions for a domain
/domains suggest            → Recommend domains based on current profile
```

**Implementation:**

| File | Change |
|------|--------|
| `config/domain_registry.yaml` | New file — full domain manifest (as above) |
| `config/commands.md` | Add `/domains` command documentation |
| `config/Artha.core.md` | Add `/domains` to §1 command table |
| `scripts/profile_loader.py` | Add `available_domains()`, `domain_registry()`, `toggle_domain()` |
| `scripts/generate_identity.py` | Read domain_registry for prompt assembly |
| `config/bootstrap-interview.md` | Phase 1.5: domain selection menu |

### 2.4 Onboarding Domain Menu

During `/bootstrap` Phase 1, after identity and location, present a domain selection screen:

```
━━ DOMAIN SELECTION ━━━━━━━━━━━━━━━━━━━━━━

Based on your profile, here are Artha's life domains.
Check the ones you want active (you can change this anytime with /domains).

  ✅ Finance & Bills          💰  (recommended — everyone needs this)
  ✅ Calendar                 📅  (recommended)
  ✅ Health                   🏥  (recommended)
  ✅ Comms                    📧  (recommended)
  ☐  Immigration & Visa      🛂  (for visa holders / green card process)
  ✅ Kids & School           🎒  (auto-enabled: you have 2 children)
  ☐  Pets & Animal Care      🐾
  ☐  Caregiving & Elder Care 🤝
  ☐  Side Business           💼
  ☐  Fitness & Wellness      🏃
  ☐  Home & Property         🏠
  ☐  Travel                  ✈️
  ☐  Vehicle                 🚗
  ☐  Insurance               🛡️
  ☐  Estate Planning         📋
  ☐  Social & Relationships  👥
  ☐  Learning                📚
  ☐  Shopping                🛒
  ☐  Digital Life            💻
  ☐  Community & Volunteering 🏘️
  ☐  Work-Life Boundary      ⚖️
  ☐  Employment              🏢

Which domains do you want? (type numbers, names, or "all recommended")
```

**Auto-enable logic:**
- `children: []` non-empty → auto-check `kids`
- `family.care_recipients` present → auto-check `caregiving`
- `domains.pets.animals` present → auto-check `pets`
- `domains.business.entity_name` present → auto-check `business`
- All household types get: `finance`, `calendar`, `health`, `comms`, `goals`

### 2.5 Post-Onboarding Toggle UX

After initial setup, domain changes happen through:

1. **Chat command:** `/domains enable pets` — runs setup questions inline, updates `user_profile.yaml`, regenerates `Artha.md`

**Domain enable response template:**
```
✅ Pets & Animal Care domain enabled.
  Next catch-up will include pet reminders.
  (Current session already in progress — pets will not appear until next catch-up.)
```
2. **Profile edit:** Manual `user_profile.yaml` edit + `python scripts/generate_identity.py` to rebuild
3. **Bootstrap drill-down:** `/bootstrap pets` — detailed interview for one domain

**State file lifecycle:**
- **Enable:** Create `state/<domain>.md` from template if it doesn't exist. If it exists (previously disabled), preserve existing data.
- **Disable:** Leave `state/<domain>.md` untouched. Stop processing in catch-up. Domain becomes invisible in briefings.
- **Delete data:** Explicit `/domains purge <name>` (requires confirmation) — deletes state file.

### 2.6 Connector Auto-Discovery

When a domain is enabled, check its `requires_connectors` and `optional_connectors`:

```
User: /domains enable kids

Artha: ✅ Kids & School domain enabled.

  Recommended connector: Canvas LMS (school grade tracking)
  Your profile lists 2 children with Canvas URLs.
  Want to set up Canvas integration now? (yes/later)
```

When a connector is enabled, auto-enable dependent domain if not already active:

```
User: (sets up Plaid connector)

Artha: ✅ Plaid connected (3 accounts linked).
  This unlocks real-time data for the Finance domain.
  Also useful for: Side Business domain (if enabled).
```

---

## 3. New Domains

### 3.1 Pets & Animal Care

> **Phase 1 scope: "Pet Reminders" only.** Vet communications are predominantly phone/portal — email signal is too sparse for meaningful extraction. Phase 1 ships date-driven reminders from profile fields (vaccinations, medications, license renewal). Full email-routing domain prompt ships in Phase 2 alongside a vet appointment calendar mechanism.

**Files to create:**

| File | Purpose |
|------|---------|
| `prompts/pets.md` | Phase 1: date-driven reminders only. Phase 2: full extraction rules |
| `state/templates/pets.md` | Bootstrap template |
| `scripts/skills/pet_license.py` | County pet license renewal check (optional) |

**Profile schema addition:**
```yaml
domains:
  pets:
    enabled: true
    animals:
      - name: "Max"
        species: "dog"
        breed: "Golden Retriever"
        dob: "2022-03-15"
        microchip: ""              # Optional
        vet:
          name: "Example Vet Clinic"
          phone: ""
        insurance:
          provider: ""
          policy_number: ""
        vaccinations:
          rabies_due: "2027-03-15"
          dhpp_due: "2027-03-15"
        medications: []
```

**Routing rules:**

> **⚠️ Phase 2 only — do not implement in Phase 1.** Phase 1 ships date-driven reminders from profile fields only. These routing rules activate when the full Pets domain ships in Phase 2.

```yaml
pets:
  priority: standard
  senders: ["*@banfield.com", "*@vca.com", "*@petinsurance.com", "*@trupanion.com", "*@nationwide.com"]
  subject_keywords: [vaccination, rabies, flea, heartworm, pet insurance, grooming, boarding, vet appointment]
```

**Alert thresholds:**
- 🔴 Vaccination overdue
- 🟠 Vaccination due within 30 days, medication refill due
- 🟡 Annual wellness check due, license renewal within 60 days
- 🔵 Grooming/boarding confirmations

**Risks:**
- Low data volume from email — most vet communications are phone/portal
- Mitigation: Phase 1 uses profile-field dates only (vaccination due, medication refill, license renewal). No email routing in Phase 1. Phase 2 adds email routing + vet iCal feed support for clinics that offer it.

**Effort:** Small (1 prompt, 1 template, schema update). Phase 1 = date-driven reminders only; no routing rules needed.

### 3.2 Caregiving & Elder Care

**Files to create:**

| File | Purpose |
|------|---------|
| `prompts/caregiving.md` | Extraction rules for medical/facility communications |
| `state/templates/caregiving.md` | Bootstrap template |

**Profile schema addition:**
```yaml
family:
  care_recipients:
    - name: "Mom"
      relationship: "mother"
      location: "Mumbai, India"      # Or local
      conditions: ["diabetes", "hypertension"]
      providers:
        primary_care: ""
        specialists: []
      medications:
        - name: "Metformin"
          refill_due: "2026-04-01"
      insurance:
        type: "Medicare Part A + B"  # Or private, or home-country
      emergency_contacts:
        - name: "Brother"
          phone: ""
      poa_status: "healthcare POA executed"
      care_level: "independent"      # independent | assisted | facility | memory_care
      timezone: "Asia/Kolkata"       # IANA timezone — defaults from location if omitted
```

**Cross-domain links:**
- `estate.md` ← POA, healthcare directive status
- `finance.md` ← Cost of care tracking
- `travel.md` ← Visit planning (especially for remote caregiving)
- `social.md` ← Sibling coordination

**Alert thresholds:**
- 🔴 Medication refill overdue, missed provider appointment
- 🟠 Medicare re-enrollment window (Oct 15 – Dec 7), appointment in <3 days with no confirmation
- 🟡 Quarterly care-level review due, sibling coordination reminder
- 🔵 Care recipient birthday, provider appointment confirmation

**Risks:**
- High sensitivity data (HIPAA-adjacent for US users). Encrypt state file (`.age`).
- International caregiving: timezone for appointment tracking, currency for costs
- Mitigation: `care_recipients[].timezone` (IANA format) normalizes all times to user's local timezone in briefings. Defaults to timezone inferred from `.location` if omitted. Medication reminders and appointment alerts always display in user's local time with source timezone noted.

**Effort:** Medium (complex prompt with medical extraction, cross-domain links, encrypted state)

### 3.3 Side Business / Freelance

**Files to create:**

| File | Purpose |
|------|---------|
| `prompts/business.md` | Invoice/payment extraction, tax deadline tracking |
| `state/templates/business.md` | Bootstrap template |

**State schema:**
```markdown
## Revenue
| Client | Invoice # | Amount | Date | Status |
|--------|-----------|--------|------|--------|

## Expenses
| Vendor | Category | Amount | Date | Deductible |
|--------|----------|--------|------|------------|

## Quarterly Taxes
| Quarter | Due Date | Estimated | Paid | Status |
|---------|----------|-----------|------|--------|
| Q1 2026 | Apr 15   | $X,XXX    | —    | ⚠ Due  |

## Accounts Receivable Aging
| Client | Invoice | Amount | Days Outstanding |
|--------|---------|--------|------------------|
```

**Alert thresholds:**
- 🔴 Quarterly estimated tax due within 14 days (IRS penalties start on the due date)
- 🟠 Quarterly estimated tax due within 30 days, invoice 30+ days unpaid, business license/permit renewal
- 🟡 Invoice 15+ days unpaid, new payment received
- 🔵 Expense receipt for tax tracking

**Risks:**
- Blurring personal and business finance in routing — an email from Chase could be personal OR business
- Mitigation: Priority-based routing with disambiguation:
  1. First check if sender matches `domains.business.email_accounts[]` or transaction references a `domains.business.bank_accounts[]` account number → route to `business.md`
  2. If no explicit match, route to `finance.md` (safer default — avoids losing personal transactions)
  3. Surface ambiguous cases: "⚠️ Routing ambiguous: Chase $847 — [finance] or [business]?" and remember corrections in `state/memory.md → routing_preferences`

**Routing correction mechanism (simplified):**
```yaml
# state/memory.md → routing_preferences section
routing_overrides:
  - sender: "alerts@chase.com"
    account_contains: "*4523"     # Business checking last 4
    route_to: business
    learned_at: "2026-03-10"
    expiry_after_days: 180
    applied_count: 0
  - sender: "alerts@chase.com"
    route_to: finance              # Default for this sender
    learned_at: "2026-03-10"
    expiry_after_days: 180
    applied_count: 0
```
When the user corrects routing, append an override entry. On next catch-up, the override is checked before the default routing rules. This is simpler than modifying `routing.yaml` directly — overrides are user-specific and sit in state, not config.

**Override lifecycle:** Each entry includes `learned_at` (ISO date) and `expiry_after_days: 180` (default 6 months). Expired overrides are pruned during catch-up journal ingest. If an override is applied successfully 5+ times without contradiction, it graduates: the rule is appended to `routing.yaml` with a `# auto-learned YYYY-MM-DD` comment and removed from `routing_overrides`. This prevents silent accumulation of stale corrections.

**Effort:** Medium (prompt, template, routing disambiguation logic, quarterly tax calendar)

### 3.4 Fitness & Wellness

**Files to create:**

| File | Purpose |
|------|---------|
| `prompts/wellness.md` | Extraction rules for fitness app emails, manual check-in prompts |
| `state/templates/wellness.md` | Bootstrap template |

**State schema:**
```markdown
## This Week
| Day | Exercise | Duration | Source |
|-----|----------|----------|--------|

## Trends (7-day rolling)
- Steps avg: X,XXX/day
- Sleep avg: Xh XXm
- Resting HR: XX bpm
- Weight: XXX lbs (trend: ↓0.5 lbs/week)

## Goals Integration
- Exercise 4x/week: 2/4 this week (⚠ Behind)
```

**Connector dependency:** This domain is **dramatically more useful** with a wearable data connector (Apple Health, Garmin, Strava). Without it, data comes from:
- Gym/class confirmation emails (Peloton, ClassPass, gym check-in)
- Manual entry during check-in prompts
- Goal engine manual updates

**Risks:**
- Without a real data connector, this domain produces thin briefings
- Mitigation: Ship the domain prompt + Apple Health connector together as a package. Mark wellness domain as "enhanced" (not "full") when no wearable connector is active.

**Effort:** Small for domain prompt. Medium for Apple Health connector (see §4).

### 3.5 Community & Volunteering

**Files to create:**

| File | Purpose |
|------|---------|
| `prompts/community.md` | Donation receipt extraction, volunteer schedule tracking |
| `state/templates/community.md` | Bootstrap template |

**State schema:**
```markdown
## Organizations
| Org | Role | Commitment | Next Event |
|-----|------|------------|------------|

## Donations (YTD)
| Date | Org | Amount | Tax-Deductible | Receipt |
|------|-----|--------|----------------|---------|
| Total YTD | | $X,XXX | $X,XXX | |

## Volunteer Hours (YTD)
| Date | Org | Hours | Activity |
|------|-----|-------|----------|
```

**Cross-domain links:**
- `finance.md` ← Donation tax deduction tracking (auto-aggregate for F3.10)
- `calendar.md` ← Volunteer shift commitments

**Why a dedicated domain (not folded into finance/calendar):**
- **Year-end donation summary with IRS compliance:** community.md tracks 501(c)(3) status per org, aggregates YTD totals, and flags missing receipts above the $250 substantiation threshold. Finance.md tracks expense line items — it doesn't track org-level metadata or IRS receipt requirements.
- **Volunteer commitment alerts:** "You're scheduled for 3 shifts this week" is a community-domain alert. Calendar sees individual events but doesn't aggregate patterns or flag over-commitment.
- Donation receipt extraction feeds `finance.md` cross-domain (auto-aggregate for F3.10), but the org-level tracking and compliance logic lives here.

**Risks:**
- Low signal density — volunteer communications are often informal
- Mitigation: Manual entry via `/items add` for volunteer shifts. Auto-extract donation receipts from email (clear sender signatures from major orgs like United Way, GoFundMe, PayPal Giving Fund).

**Effort:** Small (straightforward prompt and template)

---

## 4. New Connectors

### 4.1 Plaid (Bank Transactions)

**File:** `scripts/connectors/plaid.py`

**Architecture:**
```
┌─────────────┐     ┌─────────────┐     ┌───────────────┐
│ Plaid Link  │────→│ Access Token │────→│ /transactions │
│ (one-time   │     │ stored in    │     │ /get endpoint │
│  browser    │     │ keyring      │     │ read-only     │
│  flow)      │     │              │     │               │
└─────────────┘     └─────────────┘     └───────┬───────┘
                                                 │
                                    JSONL records │
                                                 ▼
                                    ┌───────────────────┐
                                    │ pipeline.py        │
                                    │ outputs to stdout  │
                                    └───────────────────┘
```

**Setup script:** `scripts/setup_plaid.py`
- Creates Plaid Link token via Plaid API
- Opens browser for account linking
- Stores access token in system keyring
- Supports multiple institutions per Plaid item

**Connector contract:**
```python
def fetch(*, since, max_results, auth_context, source_tag, **kwargs):
    """Yield transaction records from Plaid."""
    # GET /transactions/get with start_date=since, end_date=today
    # Yield: {id, date_iso, merchant, amount, category, account_name, source}

def health_check(auth_context):
    """Verify Plaid access token is valid."""
    # GET /item/get — returns item status
```

**connectors.yaml registration:**
```yaml
plaid:
  type: financial
  provider: plaid
  enabled: false                 # Opt-in only
  auth:
    method: api_key
    credential_key: "artha-plaid-access"
  fetch:
    handler: "connectors.plaid"
    max_results: 500
  retry:
    max_attempts: 3
    base_delay_seconds: 2.0
```

**Risks:**
| Risk | Severity | Mitigation |
|------|----------|------------|
| Plaid costs $$ per API call | High | Free tier: 100 items. Cache aggressively (daily fetch, not per-catchup). Use Plaid Sandbox for testing. |
| Bank re-authentication (Plaid Link update mode) | Medium | Detect `ITEM_LOGIN_REQUIRED` error, surface as 🟠 alert with re-link instructions |
| PII in transaction data (merchant names reveal location/habits) | High | PII guard extended to redact merchant names in briefings. Raw data stays in encrypted state file only. |
| Plaid deprecation / API changes | Low | Abstract behind connector interface. Akoya/FDX as fallback documented. |
| Plaid unavailable outside US | Medium | Alternative connectors for other countries (see §7) |

**Effort:** Medium-High. Plaid Link integration, token management, transaction categorization, PII guard extension.

> **Distribution note:** Plaid is **plugin-only** — it requires a paid developer account and is US-only. It will not ship as a built-in connector. Users who want Plaid enable it by adding their own API keys to `connectors.yaml`. This answers Open Question Q1.

### 4.2 Apple Health Export

**File:** `scripts/connectors/apple_health.py`

**Architecture:** Parse the Apple Health XML export (`export.xml` from Health app → Share → Export All Health Data). Not a live API — user periodically exports. Watch a designated folder.

```python
def fetch(*, since, max_results, auth_context, source_tag, **kwargs):
    """Parse Apple Health export.xml for recent records."""
    # Look in ~/OneDrive/Artha/inbox/ or configured path
    # Parse: steps, heart_rate, sleep_analysis, workouts, weight
    # Yield structured records with date, type, value, unit

def health_check(auth_context):
    """Check if export file exists and is recent."""
```

**Risks:**
| Risk | Severity | Mitigation |
|------|----------|------------|
| Export is manual (friction) | Medium | Shortcuts automation on iOS can auto-export periodically. Document the Shortcut recipe. |
| Export file is huge (500MB+) | Medium | Streaming XML parser (`xml.etree.ElementTree.iterparse`), filter by date range |
| No real-time data | Low | Acceptable for daily/weekly trends. Not for live heart-rate alerts. |

**Effort:** Medium. XML parsing is complex (Apple's schema is deep). Partially built (`parse_apple_health.py` exists — needs wiring).

### 4.3 Garmin Connect / Strava / Fitbit

**Files:** `scripts/connectors/garmin.py`, `scripts/connectors/strava.py`

**Architecture:** OAuth2 API integrations. Each yields activity/sleep/step records.

**Risks:**
| Risk | Severity | Mitigation |
|------|----------|------------|
| Rate limits on free-tier APIs | Medium | Cache results per-day. One fetch per catch-up. |
| OAuth token refresh complexity | Medium | Reuse existing `lib/auth.py` OAuth2 patterns. Same flow as Gmail/MS Graph. |
| Garmin has no official public API | High | Use `garminconnect` Python library (reverse-engineered). Fragile. |

**Effort:** Medium per connector. Strava (official API, well-documented) > Garmin (unofficial) > Fitbit (Google ownership, API in flux).

**Recommendation:** Ship Strava in Phase 3 (cleanest API, official OAuth2). Apple Health in Phase 2 (most users, XML export works today). **Garmin deferred to Phase 4** — the `garminconnect` library is reverse-engineered and breaks on every Garmin auth change; maintaining it is not worth the fragility for initial release. Skip Fitbit until Google stabilizes the API.

### 4.4 SMS / iMessage

**File:** `scripts/connectors/imessage.py` (macOS only)

**Architecture:** Read macOS `~/Library/Messages/chat.db` (SQLite). Full Disk Access required.

**Risks:**
| Risk | Severity | Mitigation |
|------|----------|------------|
| Requires Full Disk Access (macOS privacy gate) | High | Document clearly. Cannot be automated. User must grant in System Settings. |
| Extreme privacy sensitivity | Critical | Opt-in per contact. Sender allowlist only. Never store message bodies — extract structured data only (dates, confirmation codes). |
| macOS only — no Windows/Linux iMessage | Medium | Platform-gated: only available when `sys.platform == "darwin"`. Silent skip on other platforms. |
| Database schema changes between macOS versions | Medium | Pin to known schema. Validate at preflight. |

**Effort:** Medium. SQLite query is simple; privacy UX is complex.

**Recommendation:** Defer to Phase 3. High value but high privacy/permission bar. Ship as opt-in plugin, not built-in.

### 4.5 WhatsApp Export

**File:** `scripts/connectors/whatsapp_export.py`

**Architecture:** Parse WhatsApp standard `.txt` chat exports. User exports specific chats manually and drops files into `~/OneDrive/Artha/inbox/whatsapp/`.

```python
def fetch(*, since, max_results, auth_context, source_tag, **kwargs):
    """Parse WhatsApp exported .txt chat files."""
    # Standard format: "DD/MM/YYYY, HH:MM - Sender: message"
    # Extract: dates, mentioned events, travel plans, coordination items
```

**Risks:**
- Manual export friction (same as Apple Health)
- Date format varies by locale (DD/MM vs MM/DD)
- Mitigation: Locale-aware parser with configurable date format in connector config

**Effort:** Small. Text parsing is straightforward.

### 4.6 Document OCR Scanner

**File:** `scripts/connectors/document_ocr.py`

**Architecture:** Watch `~/OneDrive/Artha/inbox/scans/` for new PDF/image files. OCR them. Route extracted text through domain routing.

```python
def fetch(*, since, max_results, auth_context, source_tag, **kwargs):
    """OCR documents in the inbox/scans folder."""
    # macOS: Use Vision framework via pyobjc
    # Linux/Windows: Tesseract OCR fallback
    # Yield: {id (filename hash), date_iso, body (OCR text), source: "scan"}
```

**Risks:**
| Risk | Severity | Mitigation |
|------|----------|------------|
| OCR quality varies wildly | Medium | Log confidence score. Flag low-confidence extractions for human review. |
| `pyobjc` dependency is macOS-only | Medium | Tesseract as cross-platform fallback. Both in optional deps. |
| Large images = slow processing | Low | Resize to 300 DPI before OCR. Process max 10 files per catch-up. |

> **Preflight check:** Tesseract is not pip-installable — it's a system binary. When `document_ocr` connector is enabled, `preflight.py` must check `shutil.which("tesseract")` and surface a P1 warning (not P0 — optional connector) with per-platform install instructions: `brew install tesseract` (macOS), `apt-get install tesseract-ocr` (Linux), `choco install tesseract` (Windows).

**Effort:** Medium. Two OCR backends, file watching, routing integration.

### 4.7 RSS / Atom Feed

**File:** `scripts/connectors/rss_feed.py`

**Architecture:** stdlib-only (`xml.etree.ElementTree` + `urllib`). No external dependencies.

```yaml
# connectors.yaml
rss_feeds:
  type: feed
  provider: rss
  enabled: false
  feeds:
    - url: "https://www.uscis.gov/rss/news"
      domain: immigration
      label: "USCIS News"
    - url: "https://schooldistrict.org/feed"
      domain: kids
      label: "School District"
  fetch:
    handler: "connectors.rss_feed"
    max_results: 50
```

**Risks:** Minimal. Stdlib implementation. No auth. No rate limits for most feeds.

**Effort:** Small. Highest value-to-effort ratio of any new connector.

### 4.8 Todoist / Apple Reminders

**Files:** `scripts/connectors/todoist.py`, `scripts/connectors/apple_reminders.py`

**Todoist:** REST API v2. API token auth (simple). Bidirectional sync same pattern as `todo_sync.py`.

**Apple Reminders:** EventKit via `pyobjc` (macOS), or Shortcuts export. No official REST API.

```yaml
# user_profile.yaml
integrations:
  tasks:
    provider: todoist     # todoist | apple_reminders | microsoft_todo
    sync_enabled: true
```

**Risks:**
- Apple Reminders has no cross-platform story (macOS only via pyobjc)
- Todoist API is stable and well-documented
- Must ensure only ONE task provider is active for sync (avoid duplicate items)

**Effort:** Small (Todoist — clean API). Medium (Apple Reminders — platform-specific).

### 4.9 Connector Error UX Contract

Every connector must define its failure UX before shipping. This prevents inconsistent error handling across 15+ connectors.

| Connector | Failure Mode | Severity | User Message | Blocks Briefing? | Resolution |
|-----------|-------------|----------|-------------|-------------------|------------|
| Gmail | OAuth token expired | 🟠 | "Gmail: re-authentication needed" | No — other connectors proceed | `python scripts/google_auth.py` |
| MS Graph | Token refresh failed | 🟠 | "Outlook: re-authentication needed" | No | `python scripts/setup_msgraph_oauth.py` |
| Plaid | `ITEM_LOGIN_REQUIRED` | 🟠 | "Bank link expired — re-link via Plaid" | No | Re-run Plaid Link flow |
| Plaid | API key invalid/missing | 🔴 | "Plaid: API key not configured" | No (plugin-only) | Add keys to `connectors.yaml` |
| Apple Health | Export file not found | 🟡 | "Apple Health: no export file in inbox/" | No | Export from Health app → Share |
| Apple Health | Export >30 days old | 🟡 | "Apple Health: export is stale (last: Mar 1)" | No | Re-export |
| RSS | Feed unreachable | 🟡 | "RSS: [label] feed unavailable" | No | Check URL in connectors.yaml |
| WhatsApp | No export files in inbox | 🔵 | "WhatsApp: no new exports found" | No | Export chat from WhatsApp app |
| Document OCR | Tesseract not installed | 🟡 | "OCR: Tesseract not found — install via brew/apt" | No | Install system binary |
| iCloud IMAP | App-specific password expired | 🟠 | "iCloud: app-specific password expired" | No | Generate new password at appleid.apple.com |
| iCloud IMAP | IMAP auth failure | 🟠 | "iCloud: IMAP login failed" | No | `python scripts/setup_icloud_auth.py` |
| Any connector | Network timeout | 🟡 | "[connector]: network timeout — skipped" | No — degraded mode | Automatic retry next catch-up |

> **Rule:** No connector failure blocks the briefing. All failures produce degraded-mode sections with the failure message. The user always gets a briefing — even if incomplete.

---

## 5. New Skills

### 5.1 Passport Expiry Monitor

**File:** `scripts/skills/passport_expiry.py`

**Implementation:** Pure state-file reader. Reads passport expiry dates from `state/immigration.md`. Computes days to expiry. Returns alerts.

```python
class PassportExpiry(BaseSkill):
    compare_fields = ["days_to_expiry", "alert_level"]
    
    def execute(self):
        # Parse state/immigration.md for passport entries
        # For each family member: compute expiry date
        # Return: {status, data: {family_member, expiry_date, days_remaining, alert_level}}
```

**Cadence:** `daily` (trivial compute, important deadline)
**Effort:** Trivial. 50-80 lines. Highest value-to-effort ratio.

> **Vault awareness:** Passport data lives in `state/immigration.md.age` (encrypted). `skill_runner.py` must decrypt before running skills that read encrypted state files, and re-encrypt after. Add a `requires_vault: true` field to the skill registry entry so `skill_runner.py` can handle this automatically.

### 5.2 Subscription Price Watcher

**File:** `scripts/skills/subscription_monitor.py`

**Implementation:** Compare current subscription amounts in `state/digital.md` against previous cache. Alert on price changes.

**Cadence:** `weekly`
**Effort:** Small. ~100 lines.

### 5.3 School Calendar Sync

**File:** `scripts/skills/school_calendar.py`

**Implementation:** Fetch iCal feeds from school district URL (configured in profile). Parse for closures, early releases, conferences. Merge into `state/calendar.md`.

**Cadence:** `weekly`
**Effort:** Small. `icalendar` library or manual VCALENDAR parsing.

### 5.4 Credit Report Monitor

**File:** `scripts/skills/credit_monitor.py`

**Implementation:** Track credit report pull schedule per bureau with configurable cadence. Post-2021, all three bureaus offer free weekly reports via annualcreditreport.com. Track `last_pulled` date per bureau in `state/finance.md`. Default cadence: every 4 months per bureau (staggered), configurable down to weekly.

```yaml
# user_profile.yaml
credit_monitoring:
  cadence: quarterly_staggered   # weekly | monthly | quarterly_staggered
  bureaus:
    equifax:    { last_pulled: 2026-01-15 }
    experian:   { last_pulled: 2026-02-12 }
    transunion: { last_pulled: 2026-03-08 }
```

**Cadence:** Configurable (default `monthly` check, which surfaces the next bureau due)
**Effort:** Small. Calendar logic + email routing.

### 5.5 Currency Exchange Rates

**File:** `scripts/skills/currency_rates.py`

**Implementation:** Fetch daily rates from ECB or exchangerate.host (free, no key). Cache in `tmp/currency_cache.json`.

**Cadence:** `daily`
**Effort:** Trivial. ~60 lines. Needed for multi-currency finance (§7.2).

---

## 6. Profile Flexibility & Household Modes

### 6.1 Household Type Enum

Add to `user_profile.yaml`:

```yaml
family:
  household_type: family    # single | couple | family | multi_gen | roommates
```

**Impact on system behavior:**

| Household Type | Auto-Enabled Domains | Suppressed Sections | Briefing Language |
|----------------|---------------------|---------------------|-------------------|
| `single` | finance, health, calendar, goals, comms | "Family" headers, kids, spouse references | "you" (never "your family") |
| `couple` | finance, health, calendar, goals, comms, social | Kids section | "you and [spouse]" |
| `family` | finance, health, calendar, goals, comms, kids | — | "your family" |
| `multi_gen` | finance, health, calendar, goals, comms, caregiving | — | "your household" |
| `roommates` | finance, calendar, goals, comms | Family, kids, spouse | "you" |

**Implementation:**
- `profile_loader.py` → `household_type()` function
- `config/Artha.core.md` → conditional briefing templates based on household type
- `generate_identity.py` → inject household-appropriate language into `Artha.identity.md`

### 6.2 Renter Mode

```yaml
domains:
  home:
    enabled: true
    tenure: renter          # owner | renter
    lease:
      landlord: "Example Property Management"
      start_date: "2025-09-01"
      end_date: "2026-08-31"
      monthly_rent: 2500
      auto_pay: true
    renters_insurance:
      provider: "Lemonade"
      policy_number: ""
      renewal_date: "2026-09-01"
```

**Prompt overlay:** When `tenure: renter`, the `home.md` prompt adds lease-specific extraction rules and suppresses mortgage/property-tax/HOA content.

**Alerts (renter mode):**
- 🔴🔴 Lease expiry within 30 days with no renewal
- 🔴 Lease expiry within 60 days with no renewal discussion
- 🟠 Rent payment failed/late, renter's insurance renewal in 30 days, lease expiry within 90 days
- 🟡 Lease renewal negotiation window (120 days), maintenance request follow-up

**Effort:** Small. Prompt overlay + profile field. No new code.

### 6.3 Care Recipients in Profile

See §3.2 for the full `family.care_recipients[]` schema. This is a profile extension, not a separate domain config — care recipients sit alongside `spouse` and `children` as first-class family members that Artha tracks.

---

## 7. Internationalization

### 7.1 Legal/Government Framework Presets

**Directory:** `config/presets/legal/`

```
config/presets/legal/
├── us.yaml          # USCIS, IRS, SSA, state DMVs
├── canada.yaml      # IRCC, CRA, Service Canada
├── uk.yaml          # Home Office, HMRC, DVLA
├── australia.yaml   # Home Affairs, ATO, RMS
├── india.yaml       # MHA, IT Dept, RTO
└── eu-generic.yaml  # Schengen, EU Blue Card
```

Each preset defines:
```yaml
name: canada
immigration:
  agency: "IRCC"
  visa_types: ["work permit", "PR", "citizenship", "student permit", "LMIA"]
  tracking_ids: ["UCI", "application number"]
  bulletin_equivalent: "Express Entry draws"
  status_check_url: "https://www.canada.ca/en/immigration-refugees-citizenship/services/application/check-status.html"
tax:
  authority: "CRA"
  fiscal_year: "calendar"    # calendar | april (UK/India)
  filing_deadline: "April 30"
  quarterly_installments: [march_15, june_15, sept_15, dec_15]
vehicle:
  authority: "provincial"
  registration_cycle: "annual"
  inspection_required: true
property:
  tax_authority: "municipal"
  assessment_cycle: "annual"
healthcare:
  system: "provincial health insurance"
  tracking: ["OHIP", "MSP", "Alberta Health Care"]
```

**Implementation:**
- `profile_loader.py` → `load_legal_preset(country)` merges preset into profile
- `generate_identity.py` → inserts country-specific instructions into prompts
- `uscis_status.py` skill → abstract to `immigration_status.py` with country dispatcher
- `property_tax.py` skill → abstract to pluggable backends per country

**Risks:**
- Enormous surface area — each country is a rabbit hole
- Mitigation: Ship US (existing) + Canada + UK first. Community-contributed presets for others via plugin system. Each preset is a YAML file — no code required.

> **Phase 3 scope limit:** Canada and UK presets ship with `immigration` and `tax` sections only. The `vehicle`, `property`, and `healthcare` sections are stubs with `status: not_implemented` — each has provincial/regional variation (Canada has 10 provincial healthcare systems; UK healthcare is NHS but vehicle/property are regionally administered). These ship in Phase 4 via community contribution with per-section `last_verified` dates.

**Effort:** Medium per country. US is done. Each additional country ~2-3 days for immigration + tax preset. Full preset (all sections) is a Phase 4 community goal.

### 7.2 Multi-Currency Support

```yaml
# user_profile.yaml
location:
  currencies:
    primary: USD
    additional: [INR, EUR]
```

**Implementation:**
- `currency_rates.py` skill (§5.5) fetches daily rates
- `finance.md` prompt: amounts can include currency code: `₹50,000 INR ($595 USD)`
- Net worth aggregation converts all to primary currency
- `state/finance.md` stores amounts with ISO currency code and point-in-time rate:

```yaml
# Point-in-time exchange rate schema (for historical accuracy)
transaction:
  amount_original: 50000
  currency: INR
  exchange_rate_at_time: 0.0119    # rate on transaction date
  amount_usd_at_time: 595.00
  date: 2026-03-08
# Charts use historical rate. Balances/net-worth use current rate.
```

**Effort:** Small. Skill + prompt language + profile field + rate caching.

---

## 8. Data Import & Migration

### 8.1 Import Framework

**File:** `scripts/import_data.py` — unified entry point.

```bash
python scripts/import_data.py --source todoist --file ~/export.json
python scripts/import_data.py --source mint --file ~/transactions.csv
python scripts/import_data.py --source ynab --file ~/budget.csv
python scripts/import_data.py --source apple_health --file ~/export.xml
python scripts/import_data.py --source csv --file ~/data.csv --domain finance --mapping auto
```

**Import adapters (one file each):**

| Adapter | Source Format | Target State File | Priority |
|---------|-------------|-------------------|----------|
| `todoist` | JSON export | `state/open_items.md` | High |
| `apple_reminders` | CSV (Shortcuts export) | `state/open_items.md` | Medium |
| `mint` | CSV (Intuit Mint) | `state/finance.md` | Medium |
| `ynab` | CSV/JSON | `state/finance.md` | Medium |
| `apple_health` | XML | `state/wellness.md` | Medium |
| `generic_csv` | CSV with header mapping | Any state file | High |
| `notion` | JSON (Notion API export) | Mapped to domains | Low |

**Architecture:**
```python
class ImportAdapter:
    """Base class for data import adapters."""
    source_name: str
    target_domain: str
    dedup_strategy: str  # "source_id" | "content_hash" | "composite_key"
    
    def validate(self, file_path: Path) -> bool: ...
    def parse(self, file_path: Path) -> list[dict]: ...
    def preview(self, records: list[dict], limit: int = 5) -> str: ...
    def apply(self, records: list[dict], state_file: Path) -> int: ...
```

**Deduplication strategies per adapter:**

| Adapter | `dedup_strategy` | Key Fields | Notes |
|---------|-----------------|------------|-------|
| `todoist` | `source_id` | Todoist task ID | Stable across exports |
| `apple_reminders` | `source_id` | Reminder UUID | Stable |
| `mint` | `composite_key` | `date + merchant + amount + account` | No stable ID in CSV. Accepts false negatives on same-day identical transactions (rare) rather than false positives (missed dedup). |
| `ynab` | `source_id` | YNAB transaction ID (if JSON) or `composite_key` (if CSV) | JSON export preferred |
| `apple_health` | `source_id` | Health record UUID | Stable |
| `generic_csv` | `content_hash` | SHA-256 of full row | Safest default for unknown schemas |
```

**Safety:**
- Always preview before applying: "Found 347 transactions. Apply to state/finance.md? (yes/preview more/cancel)"
- Additional preview modes:
  - `--validate` — checks file format and schema only, no import
  - `--summary` — shows record count, date range, field mapping, estimated state file changes
  - `--sample N` — shows first N parsed records for spot-checking before apply
- Backup state file before any import
- Idempotent: re-importing the same file does not create duplicates (dedup by source ID)

**Risks:**
- Format changes in export files break parsers
- Mitigation: Validate schema before parsing. Clear error messages with format documentation links.

**Effort:** Small per adapter (CSV parsing is routine). Framework itself ~200 lines.

### 8.2 State File Schema Migration

**File:** `scripts/migrate_state.py`

```python
MIGRATIONS = {
    ("1.0", "1.1"): [
        AddField("domains.pets", default={"enabled": False, "animals": []}),
        AddField("family.care_recipients", default=[]),
        AddField("family.household_type", default="family"),
        AddField("location.currencies", default={"primary": "USD", "additional": []}),
        RenameField("domains.home.mortgage", "domains.home.housing"),  # Generalize
        DeprecateField("domains.home.mortgage", renamed_to="domains.home.housing"),
        # DeprecateField preserves old key for one migration cycle, then removes in next
    ],
}
```

Called by `scripts/upgrade.py` automatically. Backs up profile before mutating.

**Migration DSL operations:**

| Operation | Behavior |
|-----------|----------|
| `AddField(path, default)` | Adds field if missing, preserves if exists |
| `RenameField(old, new)` | Copies value to new key |
| `DeprecateField(old, renamed_to)` | Keeps old key readable for one cycle, logs warning, removes in next migration |

> **Currency backfill note:** Existing multi-currency entries in `state/finance.md` (e.g., HDFC NRI account, Indian investments) will be marked `exchange_rate_at_time: historical_unknown`. The currency rates skill will NOT backfill historical rates — pre-migration balances are treated as approximate USD conversions at the rate recorded at time of entry. Only new entries post-migration get point-in-time rates.

**Effort:** Small. Migration framework ~150 lines. Each migration is declarative.

---

## 9. Performance Architecture — Subagent Model

### 9.1 The Problem

Current architecture:

| Component | Execution Model | Current Scale | After Enhancement |
|-----------|----------------|---------------|-------------------|
| Connectors (pipeline.py) | `ThreadPoolExecutor` (parallel) | 8 connectors | 15+ connectors |
| Skills (skill_runner.py) | `ThreadPoolExecutor` (parallel) | 6 skills | 12+ skills |
| Domain processing | Sequential in AI context | 18 domains | 23+ domains |
| Briefing synthesis | Sequential in AI context | 1 briefing | 1 briefing |

**The bottleneck is NOT connectors or skills** — those already run in parallel via ThreadPoolExecutor and complete in seconds (total wall-clock ~5-15s for all connectors + skills combined).

**The bottleneck IS domain processing in the AI context window.** With 23+ domains, each requiring prompt loading + email routing + state file reading + state file updating, the single Claude context window approaches pressure limits:

```
Current load (18 domains):
  - 18 prompt files × ~400 lines avg = ~7,200 lines
  - 18 state files × ~100 lines avg = ~1,800 lines
  - Artha.md instructions: ~2,000 lines
  - Email batch (100 emails): ~5,000 lines
  - Total: ~16,000 lines ≈ ~48K tokens (well within 200K)

Enhanced load (23+ domains):
  - 23 prompt files × ~400 lines avg = ~9,200 lines
  - 23 state files × ~100 lines avg = ~2,300 lines
  - Artha.md instructions: ~2,500 lines
  - Email batch (200 emails + transactions): ~10,000 lines
  - Total: ~24,000 lines ≈ ~72K tokens (still within 200K, but quality degrades)
```

### 9.2 Three-Tier Scaling Strategy

**Tier 1 — Lazy Domain Loading (Phase 1, No Subagents)**

Only load prompt files and state files for domains that have **active signals** in the current catch-up:

```
Step 4: Fetch all data (parallel, existing)
Step 5: Route emails/events to domains (lightweight — routing rules only)
Step 6: For each domain WITH routed items:
          Load prompts/<domain>.md
          Load state/<domain>.md
          Process items
          Update state file
        For domains with NO routed items:
          Skip entirely (don't load prompt or state)
          Exception: domains with time-based alerts (immigration deadlines, bill due dates)
                     — load these regardless
```

**Impact:** On a typical catch-up, 8-12 of 23 domains have active signals. This cuts context consumption by 40-50% with zero architectural change.

**Implementation:**
- `config/domain_registry.yaml` → add `always_load: true|false` per domain
- `config/Artha.core.md` → modify Step 6 to check for routed items before loading
- Domains with `cadence: every_run` skills OR that are inherently always-relevant should use `always_load: true`: **calendar, comms, goals, finance, immigration, health**. Finance and immigration have periodic deadlines that are too high-consequence to risk skipping via a lazy-load false negative. Health has medication reminders. The context cost of these 6 domains (~18K tokens) is acceptable given that lazy loading on the remaining ~17 domains still saves >30%.

**Effort:** Small. Prompt instruction change + registry field.

**Tier 2 — Domain Batching with Context Recycling (Phase 2, No Subagents)**

If context pressure reaches YELLOW (>100K tokens), process domains in two passes:

```
Pass 1: P0 + P1 domains (immigration, finance, kids, health, calendar)
  → Generate alerts and critical briefing sections
  → Write state files
  → Release domain prompts from context

Pass 2: P2 + P3 + P4 domains (remaining)
  → Generate supplementary briefing sections
  → Write state files

Final: Synthesize full briefing from both passes
```

**Implementation:** This is a workflow instruction change, not a code change. Artha's catch-up workflow already processes domains sequentially — the change is to explicitly release (stop referencing) earlier domain prompts when processing later ones.

**Effort:** Small. Prompt engineering only.

**Tier 3 — Subagent Delegation (Phase 3, Major Architecture Change)**

For catch-ups with extreme data volume (200+ emails, 20+ active domains), delegate domain processing to `claude --print` subagents:

```
┌─────────────────────────────────────────────────────────┐
│ MAIN AGENT (orchestrator)                               │
│                                                         │
│ Step 4: Fetch all data (existing parallel pipeline)     │
│ Step 5: Route items to domains (lightweight)            │
│ Step 6: For high-volume domains, spawn subagents:       │
│                                                         │
│   ┌─────────────────────┐  ┌─────────────────────┐     │
│   │ SUBAGENT: Finance   │  │ SUBAGENT: Kids      │     │
│   │                     │  │                     │     │
│   │ Input:              │  │ Input:              │     │
│   │  - prompts/finance  │  │  - prompts/kids     │     │
│   │  - state/finance    │  │  - state/kids       │     │
│   │  - 23 finance emails│  │  - 15 school emails │     │
│   │                     │  │                     │     │
│   │ Output:             │  │ Output:             │     │
│   │  - Updated state    │  │  - Updated state    │     │
│   │  - Briefing section │  │  - Briefing section │     │
│   │  - Alerts list      │  │  - Alerts list      │     │
│   └─────────────────────┘  └─────────────────────┘     │
│                                                         │
│ Step 7: Collect subagent outputs                        │
│ Step 8: Cross-domain reasoning (main agent only)        │
│ Step 9: Synthesize briefing                             │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Implementation:**
```bash
# Spawn subagent for finance domain
echo '{"prompt": "prompts/finance.md", "state": "state/finance.md", "emails": [...]}' | \
  claude --print -p "Process these finance emails per the prompt. Return: updated state file content, briefing section (markdown), and alerts (JSON array)."
```

**When to trigger subagents:**
- Context pressure hits RED (>150K tokens estimated)
- A single domain has >30 items to process
- User explicitly requests deep analysis (`/catch-up deep`)

**When NOT to use subagents:**
- Normal catch-ups (<100 emails, <15 active domains) — overhead of spawning + collecting > savings
- Quick commands (`/status`, `/goals`) — instant response from state files
- Cross-domain reasoning — must stay in main agent (e.g., "immigration status affects finance")

**Subagent contract (structured output):**
```json
{
  "domain": "finance",
  "state_update": "... updated markdown ...",
  "briefing_section": "... markdown section ...",
  "alerts": [
    {"severity": "🟠", "domain": "finance", "message": "PSE bill due Mar 26", "action": "Pay $300.63"}
  ],
  "open_items": [
    {"description": "Review Q1 tax estimate", "priority": "P1", "domain": "finance"}
  ],
  "triggers": [
    "bill_due", "tax_deadline_approaching"
  ]
}
```

**Risks of Subagent Model:**

| Risk | Severity | Mitigation |
|------|----------|------------|
| Cross-domain reasoning loss | High | Only DOMAIN PROCESSING is delegated. Cross-domain reasoning (Step 8) stays in main agent. Subagents return structured data, not final prose. The `triggers` array (e.g., `["academic_decline"]` from Kids, `["spending_spike"]` from Finance) lets the main agent identify cross-domain correlations and pull relevant raw data back into context for synthesis. |
| Subagent spawning cost | Medium | Each `claude --print` call costs tokens. Only spawn when context pressure is RED. Cost budget check before spawning. |
| Subagent failure / timeout | Medium | 60-second timeout per subagent. On failure, fall back to in-context processing for that domain. |
| Race conditions on state files | Low | Subagents don't write state files directly. They return proposed updates, and the main agent writes sequentially. |
| Inconsistent output format | Medium | Strict output schema enforced in subagent prompt. JSON validation on response. |
| API rate limits (Claude) | Medium | Max 5 concurrent subagents. Sequential fallback if rate-limited. |

### 9.3 Pipeline Performance at Scale

The ThreadPoolExecutor-based pipeline (connectors + skills) scales well to 15+ concurrent operations. Current `max_workers` settings:

| Component | Current `max_workers` | Proposed | Rationale |
|-----------|----------------------|----------|-----------|
| `pipeline.py` (connectors) | default (5 or CPU count) | 8 | 15 connectors, but IO-bound. 8 threads sufficient. |
| `skill_runner.py` (skills) | 5 | 6 | 12 skills, but most are fast HTTP calls. |

**No architecture change needed.** The ThreadPoolExecutor model handles 15+ concurrent IO-bound operations well. Each connector/skill has its own retry logic and timeout. Failures are isolated.

**Connector dependency isolation:** With 15+ connectors, a broken dependency in one connector (e.g., `pyobjc` failing on Linux) must not prevent Artha from starting. All connector-specific dependencies are `optional` in `pyproject.toml`. Each connector's `import` is wrapped in `try/except ImportError` — if a dependency is missing, the connector is marked `⚠ DEGRADED` in `/status` output and skipped during pipeline execution, rather than crashing the session.

**New concern: startup cost.** With 15+ connectors, `pipeline.py` imports all handler modules at load time. Lazy-import optimization:

```python
# Current: all modules imported at startup
# Proposed: import only when connector is enabled
def _load_handler(handler_path):
    # Existing implementation already does lazy import via importlib
    # No change needed — current design is correct
```

### 9.4 Performance Monitoring

Add catch-up timing telemetry to `state/health-check.md`:

```yaml
performance:
  last_catchup:
    total_seconds: 47.2
    fetch_seconds: 8.3
    skills_seconds: 3.1
    domain_processing_seconds: 28.5
    synthesis_seconds: 7.3
    domains_processed: 14
    domains_skipped: 9
    emails_processed: 87
    context_pressure: GREEN     # GREEN (<100K) | YELLOW (100-150K) | RED (>150K)
  trend:
    avg_total_7d: 42.1
    max_total_7d: 63.4
```

**Alert:** If `avg_total_7d > 120s`, surface 🟡 performance warning with suggestion to disable low-value domains or enable subagent mode.

### 9.5 Decision: When to Introduce Subagents

**Recommendation: Do NOT build subagent model in Phase 1 or Phase 2.**

Rationale:
1. Lazy domain loading (Tier 1) alone cuts context by 40-50%, which is sufficient for 23 domains
2. Domain batching (Tier 2) extends the ceiling to ~30 domains without code changes
3. Subagent spawning adds latency (5-10s per spawn), cost (2x tokens), and failure modes
4. The current 200K context window can handle 23 domains if loaded lazily
5. Subagents should be built only when measured context pressure consistently hits RED

**Build Tier 1 now. Build Tier 2 when domain count exceeds 25. Build Tier 3 only when Tier 2 proves insufficient — likely at 30+ active domains or 300+ emails per catch-up.**

---

## 10. Reliability & Distribution Hardening

### 10.0 LLM-Proof Vault & Command Reliability ("Dispatcher, Not Database")

**Problem statement:** Artha runs on multiple AI CLIs (Claude Code, Gemini CLI, Copilot Chat). Each has different hook systems, different session lifecycle guarantees, and different failure modes. When the LLM forgets to decrypt before reading state, forgets to encrypt after a session, or "summarizes" an empty placeholder file as if it were real data, the consequences range from stale briefings to **plaintext sensitive data sitting on OneDrive indefinitely**.

This is not a theoretical risk. It was observed in production: Gemini CLI skipped decryption for `/dashboard`, reported empty state, then after correction decrypted and rendered the dashboard but **forgot to re-encrypt**. The vault_hook.py and LaunchAgent watchdog exist but are Claude Code-specific — they do not protect Gemini or Copilot sessions.

**Architectural principle:** The LLM is the **dispatcher**, not the database. Every command that reads or writes state should invoke a deterministic Python script that handles vault lifecycle internally. The LLM's job is to decide *which* script to call and to format the output — never to manually orchestrate decrypt → read → process → encrypt sequences.

#### 10.0.1 Script-Backed Commands (Command Hardening)

Transform all state-reading commands from "AI reads files and reasons" to "AI calls a script that returns structured output":

| Command | Current | Proposed Script | Vault Handling |
|---------|---------|----------------|----------------|
| `/dashboard` | AI reads state files, synthesizes | `scripts/dashboard_view.py` | Script decrypts → reads → formats → re-encrypts |
| `/status` | AI reads health-check.md | `scripts/status_view.py` | Script reads plaintext only (no vault needed) |
| `/goals` | AI reads goals.md | `scripts/goals_view.py` | Script reads plaintext (goals.md is not encrypted) |
| `/items` | AI reads open_items.md | `scripts/items_view.py` | Script reads plaintext |
| `/domain <name>` | AI reads prompt + state | `scripts/domain_view.py <name>` | Script decrypts if needed → reads → re-encrypts |
| `/diff` | AI computes delta | `scripts/diff_view.py` | Script decrypts → computes → re-encrypts |
| `/scorecard` | AI reads multiple state files | `scripts/scorecard_view.py` | Script decrypts → reads → re-encrypts |

**Script contract (all view scripts):**

```python
#!/usr/bin/env python3
"""scripts/dashboard_view.py — Deterministic dashboard renderer."""

import sys
from pathlib import Path

ARTHA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ARTHA_DIR / "scripts"))

from foundation import STATE_DIR
from vault import do_decrypt, do_encrypt, is_locked

def main():
    needs_relock = False
    
    # 1. Vault gate — decrypt if needed
    if is_locked():
        result = do_decrypt()
        if result != 0:
            print("ERROR: Vault decrypt failed. Cannot render dashboard.", file=sys.stderr)
            sys.exit(1)
        needs_relock = True          # We decrypted, so we must re-encrypt
    
    try:
        # 2. Read state files (deterministic, no LLM reasoning)
        dashboard = render_dashboard()
        
        # 3. Output structured result (--format controls density)
        print(dashboard)
    finally:
        # 4. ALWAYS re-encrypt if we decrypted (even on error)
        if needs_relock:
            do_encrypt()

    sys.exit(0)
```

**Key guarantee:** The `try/finally` block ensures encryption happens even if the script crashes mid-read. The LLM never touches `vault.py` directly for view commands — it calls the view script, which handles the full lifecycle.

**Files to create:**

| File | Effort | Priority |
|------|--------|----------|
| `scripts/dashboard_view.py` | Small | P0 — most-used command |
| `scripts/domain_view.py` | Small | P0 — requires vault for encrypted domains |
| `scripts/diff_view.py` | Medium | P1 — requires git or snapshot comparison |
| `scripts/status_view.py` | Trivial | P1 — reads only plaintext |
| `scripts/goals_view.py` | Trivial | P2 — reads only plaintext |
| `scripts/items_view.py` | Trivial | P2 — reads only plaintext |
| `scripts/scorecard_view.py` | Small | P2 — reads multiple files |

> **All view scripts accept a `--format` argument:** `flash | standard | digest` (default: `standard`). The script outputs the appropriate density; the LLM reformats for context but never needs to read raw state files. This lets the same script serve both `/catch-up flash` and `/catch-up deep` without the LLM re-reading state.

#### 10.0.2 Vault-Aware Read Guard

**Problem:** When the vault is locked, encrypted state files exist as 44-byte `.md` placeholders (or are missing entirely). An LLM reading these files sees empty/garbage content and may hallucinate or report "no data" without realizing the vault is locked.

**Solution:** `scripts/vault_guard.py` — a lightweight pre-read validator that any CLI can call:

```python
"""scripts/vault_guard.py — Vault state validator for file reads."""

# Derive from domain_registry.yaml instead of hardcoding — single source of truth.
# When new domains (e.g., caregiving) are added with sensitivity: high, they're
# automatically protected without updating this file.
import yaml

def _load_sensitive_domains() -> list[str]:
    registry_path = Path(__file__).parent.parent / "config" / "domain_registry.yaml"
    if registry_path.exists():
        with open(registry_path) as f:
            registry = yaml.safe_load(f)
        return [name for name, cfg in registry.get("domains", {}).items()
                if cfg.get("sensitivity") in ("high", "critical")]
    # Fallback if registry doesn't exist yet (pre-Phase 1a)
    return ["immigration", "finance", "insurance", "estate",
            "health", "audit", "vehicle", "contacts", "occasions"]

SENSITIVE_DOMAINS = _load_sensitive_domains()

def check_file_readable(filepath: str) -> dict:
    """Check if a state file is genuinely readable or a locked placeholder.
    
    Returns:
        {"readable": True} or 
        {"readable": False, "reason": "Vault locked. Run: python scripts/vault.py decrypt"}
    """
    path = Path(filepath)
    domain = path.stem.replace(".md", "")
    
    if domain in SENSITIVE_DOMAINS:
        age_file = path.with_suffix(".md.age")
        lock_file = path.parent.parent / ".artha-decrypted"
        
        # Primary signal: .age file exists but vault is locked
        if age_file.exists() and not lock_file.exists():
            return {"readable": False, "reason": f"Vault locked — {domain} is encrypted. "
                    "Run: python scripts/vault.py decrypt"}
        
        # Secondary signal: .age file missing, but plaintext is suspiciously small
        # (indicates incomplete decrypt or corrupted state — NOT a normal locked state)
        if not age_file.exists() and path.exists() and path.stat().st_size < 100:
            return {"readable": False, "reason": f"State file {domain}.md appears to be a "
                    "stub (< 100 bytes) and no .age backup exists. Vault may not have "
                    "decrypted properly. Run: python scripts/vault.py decrypt"}
    
    return {"readable": True}
```

**Integration across CLIs:**

| CLI | Hook Mechanism | Integration |
|-----|---------------|-------------|
| Claude Code | `PreToolUse` hook in `.claude/settings.json` | Already uses `vault_hook.py`. Add `vault_guard.check_file_readable()` call. |
| Gemini CLI | `GEMINI.md` instructions | Add explicit instruction: "Before reading ANY file in state/, run `python scripts/vault_guard.py <filepath>`. If it returns `readable: false`, decrypt first." |
| Copilot Chat | `.github/copilot-instructions.md` | Same instruction as Gemini. |
| Any other CLI | `config/Artha.core.md` instructions | Same instruction. |

**Artha.core.md addition (§11 Operating Rules):**
```
9. **Vault-aware reads**: Before reading any file in `state/`, check vault status. 
   For script-backed commands, the script handles this internally. For ad-hoc reads, 
   run `python scripts/vault_guard.py <filepath>` first. If it returns "not readable", 
   decrypt the vault before proceeding. NEVER summarize or report on a state file 
   that is a locked placeholder — this produces hallucinated output.
```

#### 10.0.3 Universal Vault Watchdog (CLI-Agnostic)

**Problem:** The existing `com.artha.vault-watchdog.plist` only checks for `claude` process names. Gemini CLI runs as `node` (via npm). Copilot runs inside VS Code. The watchdog misses non-Claude sessions entirely.

**Solution:** Generalize the watchdog to detect ANY active Artha session, not just Claude:

```bash
# Updated watchdog logic (replaces current pgrep -x "claude"):

# Check if ANY known AI CLI is running from the Artha directory
ARTHA_SESSION_ACTIVE=false

# Claude Code — narrow match to avoid false positives from unrelated processes
pgrep -f "claude.*Artha\|Artha.*claude" > /dev/null 2>&1 && ARTHA_SESSION_ACTIVE=true

# Gemini CLI — narrow match to avoid false positives from other node processes
pgrep -f "gemini.*Artha\|Artha.*gemini" > /dev/null 2>&1 && ARTHA_SESSION_ACTIVE=true

# VS Code (Copilot) — check if VS Code has the Artha folder open
pgrep -f "Code.*Artha" > /dev/null 2>&1 && ARTHA_SESSION_ACTIVE=true

# Fallback: check lock file age — if lock is >30 minutes old, assume stale
if [ "$ARTHA_SESSION_ACTIVE" = false ]; then
    LOCK_AGE=$(($(date +%s) - $(stat -f %m "$LOCK_FILE" 2>/dev/null || echo 0)))
    if [ "$LOCK_AGE" -gt 1800 ]; then
        # Lock older than 30 min with no active session → auto-encrypt
        echo "[${TIMESTAMP}] WATCHDOG: Stale lock (${LOCK_AGE}s). Auto-encrypting."
        "${PYTHON}" "${VAULT_PY}" encrypt
    fi
fi
```

**Additional enhancement: inactivity-based auto-lock.**

Add a `vault_ttl_minutes` setting in `config/user_profile.yaml`:

```yaml
system:
  vault_ttl_minutes: 30        # Auto-encrypt if vault open > N minutes with no activity
```

> **TTL race prevention:** A catch-up with 23 domains and 200+ emails may exceed 30 minutes. The catch-up workflow must periodically touch the lock file timestamp to reset the auto-lock TTL. Specifically: during Steps 6 through 14 (domain processing), touch `.artha-decrypted` at the start of each domain prompt execution and after every 5 domains processed, whichever comes first. Add to `Artha.core.md` catch-up workflow: *"During domain processing (Steps 6–14), touch the vault lock file (`touch .artha-decrypted`) at the start of each domain and after every 5 domains to prevent the watchdog from auto-encrypting mid-session."*

The `vault.py` lock file already records a timestamp. Add a `vault.py auto-lock` command that checks lock age:

```python
def do_auto_lock():
    """Called by watchdog/cron. Encrypts if lock is older than TTL."""
    if not LOCK_FILE.exists():
        return 0
    lock_age_minutes = (time.time() - LOCK_FILE.stat().st_mtime) / 60
    ttl = load_profile().get("system", {}).get("vault_ttl_minutes", 30)
    if lock_age_minutes > ttl:
        log(f"Auto-lock: vault open {lock_age_minutes:.0f} min (TTL={ttl}). Encrypting.")
        return do_encrypt()
    return 0
```

**Cross-platform watchdog:**

| Platform | Mechanism | File | Phase |
|----------|-----------|------|-------|
| macOS | LaunchAgent (existing plist, updated) | `scripts/com.artha.vault-watchdog.plist` | 1a |
| Windows | Task Scheduler XML | `scripts/artha-vault-watchdog.xml` (new) | Future — build when a Windows user exists |
| Linux | systemd timer or cron | `scripts/artha-vault-watchdog.service` (new) | Future — build when a Linux user exists |

> **Note:** Only the macOS LaunchAgent ships in Phase 1a. Windows/Linux watchdog files are deferred — the `auto-lock` TTL mechanism in `vault.py` provides platform-independent safety regardless.

#### 10.0.4 Auto-Diff on Session Close

**Problem:** When an LLM claims "I updated state/finance.md," there's no verification. The user trusts the AI's claim without evidence.

**Solution:** After every session that touched state files, automatically show what changed:

```bash
python scripts/diff_view.py --since-session
```

**Implementation:**
1. At catch-up start (Step 1), snapshot all state file checksums to `tmp/.catchup_<timestamp>_checksums.json` (timestamped to prevent overwrite on crash)
2. At catch-up end, compare current checksums against the timestamped snapshot
3. Clean up the checkpoint file on successful completion
4. If a previous checkpoint exists at next catch-up start (indicating a crash), log a warning and create a fresh checkpoint
5. Display a compact diff summary:

```
━━ SESSION CHANGES ━━━━━━━━━━━━━━━━━━━━━━━
  state/finance.md       +3 lines  (bill due date added)
  state/calendar.md      +8 lines  (5 events added)
  state/open_items.md    +2 items  (OI-047, OI-048)
  state/immigration.md   unchanged
  state/goals.md         unchanged
  
  5 files checked · 3 modified · 2 unchanged
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Artha.core.md integration:** Add to Step 18 (session close):
```
Step 18b — Session diff verification:
Run `python scripts/diff_view.py --since-session`.
Display the change summary before the closing message.
If zero files were modified during a catch-up that processed >0 emails, 
surface a ⚠ warning: "Processed N emails but no state files were updated — 
verify that domain routing is working correctly."
```

#### 10.0.5 Implementation Priority within Phase 1

These reliability enhancements should be **early Phase 1 items** — they protect all subsequent work:

| # | Item | Effort | Priority | Rationale |
|---|------|--------|----------|-----------|
| 10.0.1a | `dashboard_view.py` | Small | P0 | Most-used command. Highest failure rate. |
| 10.0.1b | `domain_view.py` | Small | P0 | Vault-sensitive domains need script protection. |
| 10.0.2 | `vault_guard.py` | Trivial | P0 | 30 lines. Prevents the observed Gemini failure. |
| 10.0.3 | Universal watchdog | Small | P0 | Closes the "forgot to encrypt" gap for all CLIs. |
| 10.0.4 | Session diff | Small | P1 | Trust-building. Lower urgency than vault safety. |
| 10.0.1c-g | Remaining view scripts | Small each | P2 | Incremental. Plaintext-only commands less urgent. |

**Risks:**

| Risk | Severity | Mitigation |
|------|----------|------------|
| Script-backed commands reduce LLM flexibility (can't customize output format) | Medium | Scripts output structured Markdown. LLM can re-format for context (flash vs. standard). Scripts handle data; LLM handles presentation. |
| View scripts add startup cost (Python interpreter per command) | Low | Each script runs in <1s. Subprocess overhead is negligible for interactive commands. |
| Watchdog false-positive kills active session encryption | Low | Check lock file age AND process list. Only auto-encrypt when BOTH confirm no active session. |
| `diff_view.py` shows sensitive data in terminal | Medium | Diff shows file names and line counts only (not content). For encrypted domains, show "modified" without content preview. |

### 10.1 Offline / Degraded Mode

**When:** All connectors fail (no internet), or specific connectors fail (partial).

**Behavior:**
```
Total failure:
  ⚠ OFFLINE MODE — No data sources reachable.
  Briefing generated from cached state files only.
  Data freshness: state files last updated [timestamp].

Partial failure:
  ⚠ DEGRADED — 2 of 8 connectors failed (Outlook, iCloud).
  Gmail + Google Calendar data is current.
  [Proceed with available data, note gaps in briefing footer]
```

**Implementation:**
- `pipeline.py` already returns exit code 3 for partial success
- Add explicit `OFFLINE` briefing template to `config/briefing-formats.md`
- State files always available locally (OneDrive offline access)
- Calendar, goals, open items, dashboard — all render from state without connectors

**Effort:** Small. Template + footer logic.

### 10.1b Mobile Write Journaling (Sync Conflict Prevention)

**Problem:** The Telegram channel bridge (`channel_listener.py`) writes directly to `state/open_items.md`. If a Mac terminal session has the vault decrypted simultaneously, OneDrive will create a sync conflict file when both writers modify the same file.

**Solution:** Mobile bridge writes to a journal file; the Mac catch-up ingests it.

```
Mobile bridge (Telegram/WhatsApp):
  Writes to: state/inbox/mobile_journal.jsonl   (append-only, never read by mobile)
  
Mac catch-up (Step 2, before domain processing):
  1. Rename: state/inbox/mobile_journal.jsonl → mobile_journal_YYYY-MM-DD.jsonl.processing
  2. Create: new empty state/inbox/mobile_journal.jsonl (Telegram can append immediately)
  3. Read + merge: .processing file entries into state/open_items.md (and other target files)
  4. Rename: .processing → .done

  This rename+recreate pattern closes the TOCTOU gap: if a Telegram message
  arrives between steps 1 and 4, it appends to the new .jsonl file and is
  processed in the next catch-up — never lost or double-processed.
```

**Journal entry format:**
```json
{"ts": "2026-03-14T10:30:00Z", "type": "open_item", "text": "Call dentist", "priority": "P2", "source": "telegram"}
{"ts": "2026-03-14T11:15:00Z", "type": "note", "domain": "health", "text": "Mom's new medication: Lisinopril 10mg", "source": "telegram"}
```

**Key invariant:** The Mac is the **single writer** to state files. Mobile is append-only to the journal. This eliminates OneDrive sync conflicts entirely.

**Effort:** Small. JSONL append in channel_listener.py + journal ingest step in catch-up workflow.

### 10.2 Plugin Validator

**File:** `scripts/validate_plugin.py`

```bash
python scripts/validate_plugin.py ~/.artha-plugins/connectors/my_connector.py
python scripts/validate_plugin.py ~/.artha-plugins/skills/my_skill.py
```

**Validates:**
- Required functions exist (`fetch`, `health_check` for connectors; `get_skill` + `execute` for skills)
- Function signatures match the protocol (correct parameter names)
- No forbidden imports (`subprocess`, `os.system`, `os.popen`, `os.exec*`, `shutil.rmtree`, `socket`, `ctypes`, `pickle`)
- No dynamic import via string (`importlib.import_module` with non-literal argument)
- Module loads without errors
- Returns structured pass/fail with actionable fix suggestions

> **Security disclaimer:** This validator catches obvious dangerous patterns — it is NOT a complete security sandbox. AST inspection cannot detect all obfuscated malicious code. Only install plugins from sources you trust.

**Effort:** Small. ~150 lines. AST inspection + import test.

### 10.3 Cross-Environment Compatibility

Artha runs across multiple execution environments. Each has different capabilities. This section defines the compatibility matrix and the adaptations needed to ensure vault reliability and full functionality in every supported environment.

#### Supported Environments

| Environment | OS | Keyring | Watchdog | Vault Hooks | Network | Status |
|---|---|---|---|---|---|---|
| **Claude Code (terminal)** | macOS / Windows / Linux | ✅ System keychain | ✅ LaunchAgent / Task Scheduler | ✅ `PreToolUse` hook | ✅ Full | **Full support** |
| **Claude Cowork (VM)** | Linux (sandbox) | ⚠ Env-var fallback | ❌ Ephemeral VM — not needed | ❌ Instruction-based | ⚠ Partial (MS Graph, iCloud blocked) | **Supported with constraints** |
| **Gemini CLI (terminal)** | macOS / Windows / Linux | ✅ System keychain | ✅ Same as Claude Code | ❌ Instruction-based | ✅ Full | **Full support** |
| **GitHub Copilot (VS Code)** | macOS / Windows / Linux | ✅ System keychain | ✅ Same as Claude Code | ❌ Instruction-based | ✅ Full | **Full support** |
| **Telegram channel bridge** | macOS / Windows / Linux | ✅ System keychain (env-var fallback exists) | ✅ Runs as service | N/A (daemon, not LLM session) | ✅ Full | **Full support** |

#### Environment-Specific Adaptations

**Claude Cowork (Linux sandbox VM):**

Cowork is an ephemeral Linux VM. Key constraints and mitigations:

| Constraint | Impact | Mitigation |
|---|---|---|
| No macOS Keychain / Windows Credential Manager | `keyring.get_password()` fails | `ARTHA_AGE_KEY` env-var fallback in `foundation.get_private_key()` |
| No LaunchAgent / Task Scheduler | Vault watchdog cannot run | Not needed — VM is ephemeral; plaintext left behind is destroyed on VM shutdown |
| `pgrep` cannot detect LLM process | Watchdog process detection fails | Inactivity-based `auto-lock` (lock-file TTL) is the safety net |
| `age` may not be pre-installed | `vault.py decrypt` fails | `preflight.py` checks for `age` and provides install instructions (`apt-get install age`) |
| MS Graph + iCloud blocked by VM proxy | Outlook and iCloud connectors fail | Already handled — Artha.core.md documents these as known constraints and continues with Gmail/Google Calendar |
| No OneDrive sync | Encrypted files don't sync to cloud during VM session | Safe — vault state is local to VM workspace; user runs from local terminal for persistent changes |

**Credential fallback chain** (required change to `foundation.get_private_key()`):

```python
def get_private_key() -> str:
    """Retrieve age private key: keyring first, then ARTHA_AGE_KEY env var."""
    svc, acct = _config["KC_SERVICE"], _config["KC_ACCOUNT"]
    key = None
    try:
        key = keyring.get_password(svc, acct)
    except Exception:
        pass  # Keyring unavailable (Docker, Cowork VM, CI)
    if not key:
        key = os.environ.get("ARTHA_AGE_KEY", "").strip()
    if not key:
        die("Cannot retrieve age private key. "
            "Store it in keyring or set ARTHA_AGE_KEY environment variable.")
    return key
```

This follows the pattern already established by the Telegram connector (`scripts/channels/telegram.py` lines 370-390).

**Not supported (and not planned):**

| Environment | Reason |
|---|---|
| Docker container | No system keychain, no OneDrive sync, no AI CLI runtime inside container. Adds complexity without clear user benefit. Cowork VM covers the "cloud sandbox" use case. |
| Bare SSH session (no AI CLI) | Artha requires an AI CLI as its runtime. SSH alone provides no LLM reasoning layer. |

#### Supported Environments Documentation

The supported environment matrix MUST be maintained in three places:
1. `specs/artha-prd.md` — canonical reference (§8 Architecture)
2. `README.md` — user-facing quick reference
3. `docs/supported-clis.md` — detailed setup per environment

When a new environment is added or an existing one changes status, all three must be updated in the same commit.

---

## 11. Phased Delivery Plan

### Phase 1 — Foundation (Profile Flexibility + Low-Effort Wins)

**Goal:** Make Artha adoptable by any household type, and make vault/command reliability bulletproof across all AI CLIs.

### Phase 1a — Infrastructure Foundation (Vault + Registry + Migration)

**Goal:** Harden vault reliability, establish domain registry, and build test infrastructure. Everything in Phase 1b depends on this.

| # | Deliverable | Files Changed/Created | Effort | Tests |
|---|------------|----------------------|--------|-------|
| 1.0a | **Vault-guard pre-read check** | `scripts/vault_guard.py`, `GEMINI.md`, `AGENTS.md`, `config/Artha.core.md` | Trivial | 4 |
| 1.0b | **`dashboard_view.py` (script-backed, accepts `--format`)** | `scripts/dashboard_view.py` | Small | 5 |
| 1.0c | **`domain_view.py` (script-backed, accepts `--format`)** | `scripts/domain_view.py` | Small | 5 |
| 1.0d | **Universal vault watchdog** | `scripts/com.artha.vault-watchdog.plist` (update), `scripts/vault.py` (add `auto-lock`) | Small | 4 |
| 1.0e | **Session diff on close** | `scripts/diff_view.py`, `config/Artha.core.md` | Small | 4 |
| 1.0f | **`ARTHA_AGE_KEY` env-var fallback** | `scripts/foundation.py` | Trivial | 3 |
| 1.0g | **Update `preflight.py` for `pipeline.py` architecture** | `scripts/preflight.py` | Small | 3 |
| 1.0h | **Integration test harness** | `tests/test_catchup_workflow.py` | Medium | 8 |

> **1.0h test cases (must cover all 8):**
> 1. Happy path: mock email fetch → domain routing → state file updated → briefing generated
> 2. Zero emails: no connector data, offline/state-only briefing triggers correctly
> 3. Vault lifecycle: auto-decrypt at start, auto-re-encrypt at end (verify `.age` file restored)
> 4. Single connector failure: partial data, degraded mode warning in briefing, non-failing domains unaffected
> 5. Lazy loading: domain with no routed items is skipped (prompt never loaded)
> 6. Preflight pass → catch-up proceeds; preflight fail → catch-up halted with actionable error
> 7. Safety-critical skill (`safety_critical: true`) runs even when its domain is disabled
> 8. Session diff: correct file-change summary after catch-up (checksums match actual modifications)

| 1.0i | **Prompt regression test framework** | `tests/prompts/fixtures/`, `tests/test_prompt_extraction.py` | Medium | 5 |
| 1.1 | Domain registry + lazy domain loading (co-delivered — tightly coupled) | `config/domain_registry.yaml`, `Artha.core.md` | Medium | 7 |
| 1.2 | State schema migration system | `scripts/migrate_state.py`, `scripts/upgrade.py` (update) | Medium | 6 |

> **1.0i prompt regression testing:** Golden-file test framework. `tests/prompts/fixtures/` contains test email batches per domain (≥5 fixture emails each for existing domains: finance, immigration, health, home, kids). `tests/test_prompt_extraction.py` feeds fixtures through domain prompts and compares extracted items against expected golden files. Every new domain prompt in Phase 1b+ must ship with ≥5 extraction fixtures. This catches silent prompt regressions that unit tests cannot detect.
>
> **Execution model:** Prompt regression tests call the real Claude API with `temperature=0` for near-deterministic output. They are marked `@pytest.mark.prompt_regression` and **excluded from the default `make test` run** — execute separately via `pytest -m prompt_regression`. Golden files are committed to the repo and updated manually when prompt changes are intentional (not auto-updated). Acceptance threshold: extraction accuracy ≥80% on fixtures (not 100% — LLM output has valid variation across runs). This is significantly more infrastructure than a unit test; budget accordingly.

**Phase 1a total:** 11 items. ~54 new tests. **Exit criterion:** Vault is hardened across all CLIs, domain registry exists, schema migration runs cleanly, prompt regression framework is in place.

### Phase 1b — New Capabilities (Domains + Skills + Connectors)

**Goal:** Make Artha adoptable by any household type with new domains, skills, and connectors — built on the Phase 1a foundation.

| # | Deliverable | Files Changed/Created | Effort | Tests |
|---|------------|----------------------|--------|-------|
| 1.3 | Household type enum + single-person mode | `user_profile.schema.json`, `user_profile.example.yaml`, `profile_loader.py`, `Artha.core.md` | Small | 5 |
| 1.4 | Renter mode | `prompts/home.md` (overlay), `user_profile.schema.json` | Small | 3 |
| 1.5 | `/domains` command | `commands.md`, `Artha.core.md`, `profile_loader.py` | Small | 4 |
| 1.6 | Pet Reminders (reduced scope — profile-field date alerts only, no email routing) | `prompts/pets.md`, `state/templates/pets.md` | Trivial | 2 |
| 1.7 | Passport expiry skill (with `requires_vault: true`) | `scripts/skills/passport_expiry.py`, `skills.yaml`, `scripts/skill_runner.py` | Small | 4 |
| 1.8 | Subscription price watcher | `scripts/skills/subscription_monitor.py`, `skills.yaml` | Small | 3 |
| 1.9 | RSS feed connector | `scripts/connectors/rss_feed.py`, `connectors.yaml` | Small | 4 |
| 1.10 | Onboarding domain menu | `bootstrap-interview.md`, `_bootstrap.py` | Medium | 5 |
| 1.11 | Offline/degraded mode | `Artha.core.md`, `briefing-formats.md` | Small | 3 |
| 1.12 | Performance telemetry + per-domain hit rate tracking | `Artha.core.md` (health-check schema) | Small | 3 |
| 1.13 | Remaining view scripts (`status`, `goals`, `items`, `scorecard`) | `scripts/{status,goals,items,scorecard}_view.py` | Small | 6 |

> **Each new domain/skill in Phase 1b must ship with ≥5 prompt extraction fixtures** in `tests/prompts/fixtures/<domain>/` (established by 1.0i).

**Phase 1b total:** 11 items. ~42 new tests.

### Phase 2 — New Domains + Key Connectors

**Goal:** Ship the domains that unlock the largest new audiences.

| # | Deliverable | Effort | Tests |
|---|------------|--------|-------|
| 2.1 | Caregiving domain | Medium | 8 |
| 2.2 | Side business domain | Medium | 6 |
| 2.3 | Wellness domain (co-deliver with 2.6 — see note) | Small | 4 |
| 2.4 | Pets domain (full — email routing, vet calendar, Phase 1 date-only→full promotion) | Small | 4 |
| 2.5 | Community domain (donation receipt extraction feeds `finance.md` tax deductions) | Small | 3 |
| 2.6 | Todoist connector (live bidirectional sync — ongoing task management; sets `integrations.tasks.provider` during onboarding) | Small | 5 |
| 2.7 | Apple Health connector (co-deliver with 2.3 — see note) | Medium | 6 |
| 2.8 | WhatsApp export connector | Small | 4 |
| 2.9 | School calendar skill | Small | 3 |
| 2.10 | Credit monitor skill | Small | 3 |
| 2.11 | Multi-currency support (schema + manual rate entry; live FX lookup via currency_rates skill ships in Phase 3 item 3.7) | Small | 4 |
| 2.12 | Plugin validator | Small | 5 |
| 2.13 | Domain batching (Tier 2 perf) | Small | 2 |
| 2.14 | Data import framework + Todoist adapter (one-time historical import of existing Todoist archive) | Medium | 6 |
| 2.15 | Mobile write journaling (§10.1b — Telegram/WhatsApp → JSONL journal, Mac single-writer ingest) | Small | 3 |

> **Co-delivery requirement:** Items 2.3 (Wellness) and 2.7 (Apple Health) are co-deliverables. The wellness domain exits beta only after the Apple Health connector ships — a wellness domain without data produces thin briefings. Do not mark 2.3 complete before 2.7.

> **Community domain note:** Donation receipt extraction is the primary value and feeds into `finance.md` tax deduction aggregation (§3.5 cross-domain link). Volunteer scheduling is thin without calendar integration — ship volunteer org tracking as manual entry (`/items add`) in Phase 2, with calendar-based auto-tracking in Phase 3+.

**Phase 2 total:** ~15 work items. ~66 new tests.

### Phase 3 — Connectors + Internationalization + Distribution

**Goal:** International users, real financial data, data import tooling.

| # | Deliverable | Effort | Tests |
|---|------------|--------|-------|
| 3.1 | Plaid connector — **plugin-only distribution** (requires paid API key; not built-in) | High | 10 |
| 3.2 | Strava connector | Medium | 5 |
| 3.3 | Apple Reminders connector | Medium | 4 |
| 3.4 | Document OCR connector | Medium | 6 |
| 3.5 | Canada legal preset | Medium | 4 |
| 3.6 | UK legal preset | Medium | 4 |
| 3.7 | Currency rates skill | Trivial | 3 |
| 3.8 | ~~Docker distribution~~ *Dropped — Cowork VM covers cloud sandbox use case* | — | — |
| 3.9 | Import adapters (Mint, YNAB, CSV) | Medium | 8 |
| 3.10 | Subagent delegation (Tier 3 perf) — if needed | High | 10 |

**Phase 3 total:** ~9 work items (1 dropped). ~54 new tests.

### Phase 4 — Community + Polish (Future)

| # | Deliverable |
|---|------------|
| 4.1 | iMessage connector (macOS only) |
| 4.2 | Garmin connector (deferred from Phase 3 — unofficial API fragility) |
| 4.3 | Slack/Discord channel completion |
| 4.4 | Notion connector |
| 4.5 | Additional country presets (Australia, India, EU) |
| 4.6 | Community-contributed domain/skill/connector marketplace |

---

## 12. Risk Register

### Critical Risks

| ID | Risk | Probability | Impact | Mitigation | Phase |
|----|------|-------------|--------|------------|-------|
| R1 | **Plaid API cost exceeds budget** | Medium | High | Start with Plaid Sandbox. Cache transactions daily. Alert when approaching billing tier. Document alternative (manual bank CSV import). | 3 |
| R2 | **Profile schema migration breaks existing users** | Medium | High | Always backup before migration. Dry-run mode. Rollback command. Never remove fields — only add or rename. | 2 |
| R3 | **Context window pressure with 23+ domains degrades briefing quality** | Medium | High | Tier 1 (lazy load) + Tier 2 (batching) first. Measure before building Tier 3. Strict domain loading budget tracked in telemetry. | 1-2 |
| R4 | **Cross-domain reasoning degrades when domains processed by subagents** | Low | Critical | Subagents return structured data. Cross-domain reasoning always runs in main agent with access to all domain summaries. Never delegate cross-domain logic. | 3 |
| R5 | **Plugin security — malicious third-party code** | Low | Critical | Plugin validator at preflight. No auto-load — user must explicitly register. Allowlist for built-in modules enforced. Document "only install plugins you trust." | 2 |
| R5b | **LLM skips vault decrypt/encrypt on non-Claude CLIs** | High → Medium (post-mitigation) | Critical | Script-backed commands (§10.0.1) handle vault lifecycle internally. Universal watchdog (§10.0.3) as safety net. Vault guard (§10.0.2) blocks reads of locked files. Three independent defenses — all in Phase 1. | 1 |

### High Risks

| ID | Risk | Probability | Impact | Mitigation | Phase |
|----|------|-------------|--------|------------|-------|
| R6 | **New domains produce thin briefings without connectors** | High | Medium | Ship domains WITH their recommended connector. Mark "enhanced" vs "basic" capability level per domain. Clearly communicate what data sources power each domain. | 1-2 |
| R7 | **Garmin connector breaks (unofficial API)** | High | Low | Treat Garmin as best-effort. Strava (official API) as primary fitness connector. Apple Health as offline fallback. | 3 |
| R8 | **International legal presets contain inaccurate regulatory info** | Medium | High | Community review process. Disclaimer: "Artha is not legal advice." Each preset includes `last_verified` date and official source URLs. | 3 |
| R9 | ~~Docker keyring workaround~~ *Dropped — Docker distribution removed from scope (Cowork VM covers the cloud sandbox use case). Env-var fallback still needed for Cowork VM and is implemented in §10.3.* | — | — | — | — |
| R10 | **Test suite growth (from 318 to ~531) slows CI** | Medium | Low | Parallel test execution (`pytest-xdist`). Test categorization (unit/integration/e2e). Allow `pytest -m unit` for fast runs. | 1-2 |

### Medium Risks

| ID | Risk | Probability | Impact | Mitigation | Phase |
|----|------|-------------|--------|------------|-------|
| R11 | **Onboarding domain menu is overwhelming (23 options)** | Medium | Medium | Smart defaults based on household_type. Group domains into categories (Essential, Family, Financial, Lifestyle). Pre-check only 3-5 defaults. | 1 |
| R12 | **Business domain routing conflicts with finance domain** | Medium | Medium | Priority-based disambiguation (§3.3): explicit match → finance default → surface ambiguous → remember corrections. | 2 |
| R13 | **Apple Health XML file too large for reasonable parse times** | Low | Medium | Streaming parser with date-range filter. Process max 30 days. Skip record types not configured for tracking. | 2 |
| R14 | **Todoist sync with Todoist conflicts with Microsoft To Do sync** | Low | High | Profile field `integrations.tasks.provider` — only ONE provider active. Validate during preflight. | 2 |
| R15 | **LLM token cost scales with domain count** | Medium | Medium | 23 domains + 15 connectors = significantly more context per catch-up. Track per-catch-up token consumption in telemetry (§1.13). Lazy loading (§1.3) and batching (§2.12) are the primary mitigations. Set a per-catch-up context budget and alert at 80%. | 1-3 |
| R16 | **Domain extraction quality degrades under context pressure** | Medium | High | Later-processed domains receive less LLM attention as context fills up. Track per-domain hit rate (extraction success per email) in `state/health-check.md`. If a domain's hit rate drops below 60% (minimum 10 catch-ups with ≥1 routed email before alerting), surface as ⚠ in system health. **Hit rate = emails routed → state file entries created / emails routed total.** Note: Tier 2 domain batching (§2.12) uses fixed priority order (P0 → P1 → P2+), which means lower-priority domains systematically receive less attention. This is an accepted trade-off — monitor and escalate to Tier 3 if P2+ hit rates consistently degrade. | 1-2 |

---

## 13. Success Criteria

### Phase 1a Exit Criteria (Infrastructure Foundation)
- [ ] `vault_guard.py` blocks reads of locked state files across all CLIs (Claude, Gemini, Copilot)
- [ ] `vault_guard.py` derives `SENSITIVE_DOMAINS` from `domain_registry.yaml` (not hardcoded)
- [ ] `/dashboard` runs via `dashboard_view.py` with automatic decrypt/re-encrypt — never leaves vault open
- [ ] `/domain <name>` runs via `domain_view.py` with same vault guarantee
- [ ] View scripts accept `--format flash|standard|digest` and produce density-appropriate output
- [ ] Vault watchdog detects Gemini CLI and Copilot sessions (not just Claude)
- [ ] Catch-up workflow touches lock file at the start of each domain and after every 5 domains (Steps 6–14) to prevent auto-lock
- [ ] `foundation.get_private_key()` falls back to `ARTHA_AGE_KEY` env var when keyring is unavailable (Cowork VM, CI)
- [ ] Session diff shows accurate file change summary at session close (crash-safe timestamped checkpoints)
- [ ] `preflight.py` validates `pipeline.py` architecture (not deleted individual scripts)
- [ ] Integration test (`test_catchup_workflow.py`) covers all 8 specified test cases
- [ ] Prompt regression framework exists with ≥5 fixtures per existing domain (finance, immigration, health, home, kids)
- [ ] Domain registry + lazy loading co-delivered, lists all 23 domains with dependency graphs
- [ ] State schema migration runs cleanly (`scripts/migrate_state.py`) with `DeprecateField` support
- [ ] All tests passing (existing 318 + ~54 new = ~372 total)

### Phase 1b Exit Criteria (New Capabilities)
- [ ] Single-person household receives briefings with no "family" language
- [ ] Renter mode produces home domain alerts without mortgage references (4-tier threshold: 120d/90d/60d/30d)
- [ ] `/domains enable|disable` works without editing YAML, with UX message about next-catch-up activation
- [ ] Pet Reminders produces date-driven alerts from profile fields (no email routing in Phase 1)
- [ ] Passport expiry skill fires correct alerts (with `requires_vault: true` flag)
- [ ] Subscription price watcher skill fires on price changes
- [ ] RSS connector fetches and routes to configured domains
- [ ] Lazy domain loading reduces context consumption by >30% on quiet days (calendar, comms, goals, finance, immigration, health are `always_load`; remaining ~17 domains lazy-loaded)
- [ ] Offline mode produces usable briefing when all connectors fail
- [ ] Per-domain hit rate tracked in `state/health-check.md` (min 10 catch-ups before alerting)
- [ ] Bootstrap interview includes domain selection menu with comms pre-checked per Q7
- [ ] Each new domain/skill ships with ≥5 prompt extraction fixtures
- [ ] All tests passing (~372 + ~42 = ~414 total)

### Phase 2 Exit Criteria
- [ ] Caregiving domain tracks medications, appointments, and care coordination (with timezone support)
- [ ] Business domain separates freelance/rental income from personal finance (priority-based routing with correction memory)
- [ ] Wellness domain displays exercise trends from Apple Health data (co-delivered with Apple Health connector)
- [ ] Pets domain fully promoted — email routing, vet calendar, full prompt
- [ ] Community domain extracts donation receipts into finance tax deductions
- [ ] Todoist sync works bidirectionally (matching todo_sync.py reliability)
- [ ] Multi-currency amounts display correctly with point-in-time exchange rates
- [ ] Plugin validator catches basic contract violations (expanded forbidden imports list)
- [ ] Mobile journal mechanism prevents OneDrive sync conflicts from Telegram writes (rename+recreate TOCTOU-safe pattern)
- [ ] All tests passing (~480 total)

### Phase 3 Exit Criteria
- [ ] Plaid connector pulls real bank transactions into finance domain (plugin-only — not built-in)
- [ ] At least one non-US legal preset (Canada or UK) is complete with immigration + tax sections, `last_verified` date, `source_urls[]`, and disclaimer. Alerts fire correctly for a synthetic Canadian/UK user profile in unit tests (test profile committed to `tests/fixtures/profiles/`)
- [ ] Cowork VM runs a complete catch-up with `ARTHA_AGE_KEY` env-var fallback (Gmail + Google Calendar only, MS Graph/iCloud known-blocked)
- [ ] Data import from at least 3 sources works (Todoist, CSV, Apple Health) with `--validate`, `--summary`, `--sample` preview modes and defined dedup strategies
- [ ] Subagent delegation (if built) reduces large catch-up time by >25%
- [ ] All tests passing (~531 total)

---

## 14. Open Questions (Resolved)

| # | Question | Decision | Rationale |
|---|---------|----------|-----------|
| Q1 | Should Plaid be built-in or plugin-only? | **Plugin-only.** | Plaid requires a paid developer account and is US-only. Shipping it built-in confuses free-tier users and adds a hard dependency. Users who want Plaid add their own API keys to `connectors.yaml`. See §4.1. |
| Q2 | YAML or Python for domain registry? | **YAML.** | Consistent with existing config pattern (`connectors.yaml`, `skills.yaml`, `routing.yaml`). Community-editable without touching Python. Co-located with `domain_registry.yaml` where lazy loading metadata also lives. |
| Q3 | Worth supporting `household_type: roommates`? | **Yes.** | Low implementation cost (one enum value + suppress family content). Enables split-expense awareness. Suppresses family/kids/spouse language that would be confusing for roommates. Large addressable audience (young professionals, college). |
| Q4 | Subagent delegation: opt-in setting or auto-triggered? | **Auto-triggered by context pressure RED.** | No user setting needed. When health-check reports context pressure at RED level, the system automatically enables domain batching/subagent delegation. Avoids adding yet another configuration option. |
| Q5 | How to validate community legal presets? | **Self-attested with metadata.** | Each preset includes `last_verified` date, `verified_by` (contributor name), disclaimer ("not legal advice"), and `source_urls[]` linking to official government pages. Community review via PR. No formal legal review — that would block contributions. |
| Q6 | Should disabled domains still run safety-critical skills? | **Yes, for skills flagged `safety_critical: true`.** | NHTSA recalls are safety-critical even if vehicle domain is "disabled." Add a `safety_critical: true` flag to the skill registry entry. `skill_runner.py` always runs safety-critical skills regardless of domain enable/disable state. |
| Q7 | Default onboarding domains? | **finance, health, calendar, comms.** | NOT goals — an empty goals state file creates a bad first impression. Prompt goals setup post-onboarding once the user has experienced one or two catch-ups. These 4 domains have the strongest email signal and require zero additional setup. |
