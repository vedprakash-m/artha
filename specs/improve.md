# Artha Improvement Plan — Execution Specification

> **Version:** 1.0 | **Date:** March 14, 2026 | **Status:** Approved for Implementation
> **Baseline:** Artha v5.1.0 | 22K LOC | 485 tests | 7 skills | 8 connectors | 18 domains
> **Inputs:** `specs/product_vision_opportunities_report.md` (Gemini market analysis),
> `specs/product_vision_review.md` (architectural review), `specs/enhance.md` v1.4 (domain expansion)
> **Scope:** Incremental capability improvements within the existing architecture.
> No new server processes, no new binary dependencies, no architectural rewrites.

---

## Table of Contents

1. [AS-IS Architecture Map](#1-as-is-architecture-map)
2. [Pain Points & Bottlenecks](#2-pain-points--bottlenecks)
3. [Strategic Direction](#3-strategic-direction)
4. [Improvement Register](#4-improvement-register)
5. [Phase 1 — Intelligence Layer (Weeks 1–3)](#5-phase-1--intelligence-layer-weeks-13)
6. [Phase 2 — Relationship & Social Enrichment (Weeks 4–5)](#6-phase-2--relationship--social-enrichment-weeks-45)
7. [Phase 3 — Action Expansion & Estate (Weeks 6–7)](#7-phase-3--action-expansion--estate-weeks-67)
8. [Phase 4 — Platform Parity & Diagnostics (Weeks 8–9)](#8-phase-4--platform-parity--diagnostics-weeks-89)
9. [Phase 5 — Connector Expansion (Weeks 10–12)](#9-phase-5--connector-expansion-weeks-1012)
10. [Deferred & Rejected Items](#10-deferred--rejected-items)
11. [Risk Register](#11-risk-register)
12. [Privacy & Security Hardening](#12-privacy--security-hardening)
13. [Testing Strategy](#13-testing-strategy)
14. [Success Criteria](#14-success-criteria)
15. [Relationship to enhance.md](#15-relationship-to-enhancemd)

---

## 1. AS-IS Architecture Map

### System Topology

```
User
 │
 ├── AI CLI (Claude Code / Gemini CLI / GitHub Copilot)
 │    ├── reads: config/Artha.md (assembled instruction file)
 │    ├── reads: prompts/*.md (18 domain prompts)
 │    ├── reads/writes: state/*.md, state/*.md.age
 │    └── executes: scripts/*.py
 │
 ├── Telegram Bridge (always-on long-poll)
 │    ├── channel_listener.py → read-only state access
 │    ├── 12 whitelisted commands, rate-limited
 │    └── PII-filtered on every outbound message
 │
 └── Scheduled Skills (cron / manual)
      └── skill_runner.py → 7 skills (USCIS, weather, recalls, etc.)
```

### Data Flow (Catch-Up Workflow)

```
Connectors (Gmail, Outlook, iCloud, GCal, Canvas, OneNote, RSS)
   │ ThreadPoolExecutor ≤8 workers
   ▼
pipeline.py → JSONL stdout
   │
   ▼
AI CLI ingests JSONL → domain routing (email keywords → prompt files)
   │
   ▼
State files updated (state/*.md, state/*.md.age)
   │
   ▼
Briefing generated → saved to briefings/YYYY-MM-DD.md
   │
   ▼
Optional: channel_push.py → Telegram
```

### Component Inventory

| Layer | Components | LOC | Tests |
|---|---|---|---|
| Core scripts | `artha.py`, `pipeline.py`, `vault.py`, `foundation.py`, `skill_runner.py`, `preflight.py`, `pii_guard.py`, `safe_cli.py` | ~7,500 | ~250 |
| Connectors | 8 handlers in `scripts/connectors/` + `base.py` | ~3,200 | ~60 |
| Skills | 7 skills in `scripts/skills/` + `base_skill.py` | ~1,800 | ~40 |
| Views | `goals_view.py`, `scorecard_view.py`, `dashboard_view.py`, `items_view.py`, `status_view.py`, `diff_view.py`, `domain_view.py` | ~2,100 | ~50 |
| Library | `scripts/lib/` (auth, retry, metrics, common, output, html, msgraph, cli_base) | ~2,800 | ~40 |
| Channels | `scripts/channels/telegram.py`, `channel_listener.py`, `channel_push.py` | ~2,400 | ~30 |
| Config/Prompts | 18 domain prompts, 8 YAML configs, domain registry | ~3,000 | ~15 (schema validation) |
| **Total** | | **~22,800** | **485** |

### Dependency Profile (deliberately minimal)

**Core (always installed):** PyYAML, keyring, jsonschema — 3 packages
**Optional (extras):** google-api-python-client, msal, caldav, beautifulsoup4 — installed per `[project.optional-dependencies]`
**Dev:** pytest, pytest-mock, pytest-snapshot, datadiff, anyio
**External binaries:** `age` (encryption), Python 3.11+, Git
**No:** Docker, Node.js runtime, Electron, database, API server, browser binaries

---

## 2. Pain Points & Bottlenecks

These are the identified gaps between what Artha does today and what it should do,
filtered through the architectural review's feasibility assessment.

### P1 — Artha is observational, not computational

**Problem:** Artha meticulously catalogs incoming data (emails, calendar, state files) but
performs almost no computation *on* that data. The Goal Engine tracks progress percentages
but doesn't synthesize cross-domain implications. The finance domain records bills but
doesn't compute runway, burn rate, or threshold alerts.

**Impact:** Users must do their own arithmetic. The system mirrors complexity back to them
rather than absorbing it.

**Root cause:** The AI CLI *is* the runtime. Complex calculations were deferred because
"Claude can do the math." But LLMs are unreliable for precise arithmetic, and the
calculation should be pre-computed and cached in state files.

### P2 — Relationship context is shallow

**Problem:** `state/social.md` tracks birthdays and reconnect intervals but not the rich
context that makes relationship intelligence useful: children's names, dietary preferences,
last conversation topics, shared history.

**Impact:** The pre-meeting briefing is generic. The social domain adds minimal value
beyond what a calendar reminder provides.

**Root cause:** No template exists for structured per-contact notes. The prompt
(`prompts/social.md`) doesn't instruct the AI to extract relationship facts from
processed emails.

### P3 — Estate domain is procedurally empty

**Problem:** `prompts/estate.md` defines alert thresholds for will/trust documents but
provides no structured inventory template. Users don't know what data to maintain.
Digital estate (accounts, 2FA methods, vault recovery procedures) is unaddressed.

**Impact:** Estate planning is the highest-consequence domain with the least
infrastructure. A family emergency would expose this gap immediately.

### P4 — Windows onboarding gap

**Problem:** `setup.sh` provides a polished experience on macOS/Linux. Windows users face
manual PowerShell steps in a README `<details>` block. No interactive wizard auto-launch,
no demo auto-run, no progress output on Windows.

**Impact:** Windows is the world's most common desktop OS. Artha explicitly declares
Windows support but delivers a second-class experience.

### P5 — No unified diagnostic command

**Problem:** `preflight.py` checks prerequisites, `vault.py status` checks encryption,
`pipeline.py --health` checks connectors. There's no single "is everything working?"
command.

**Impact:** Debugging setup issues requires running three separate commands and
cross-referencing output.

### P6 — Gig/irregular income blind spot

**Problem:** The finance domain's email routing keywords don't cover Stripe, PayPal,
Upwork, Venmo, or Etsy transaction confirmations. Users with irregular income streams
have no threshold alerting (1099-K, quarterly tax deadlines).

**Impact:** Growing audience segment (60M+ US adults with independent income) gets
partial value from the finance domain.

### P7 — Health data limited to email

**Problem:** Health domain relies entirely on email parsing (appointment confirmations,
patient portal notifications). Structured health data (Apple Health exports, lab result
trends) is not ingested.

**Impact:** Longitudinal health tracking — the most valuable capability for aging parents
and proactive health management — is impossible.

---

## 3. Strategic Direction

### Guiding Principles

1. **Compute on data you already have.** Every improvement should first exploit data
   already flowing through the pipeline before adding new data sources.
2. **Prompt engineering before Python.** If the AI CLI can achieve an outcome through
   better prompt instructions, prefer that over new scripts.
3. **One skill, one file, one test class.** Every new capability is a self-contained
   skill or prompt enhancement — not a cross-cutting refactor.
4. **Zero new binary dependencies.** No Playwright, no Tesseract, no Electron, no
   Docker. If it requires `npm install` or `brew install <new-thing>`, it's out of scope.
5. **Strangler Fig over Big Bang.** Each phase is independently deployable, tested, and
   backwards-compatible. If Phase 3 is never built, Phases 1-2 still deliver full value.

### Trade-Off Matrix

| Approach | Benefit | Cost | Risk | Selected |
|---|---|---|---|---|
| Add skills (pure Python) | High — computation on existing data | Low — ~200 LOC each | Low — self-contained, testable | **Yes** |
| Enhance prompts (Markdown) | High — zero code changes | Negligible | Low — reversible, version-controlled | **Yes** |
| Add connectors (new data sources) | Medium — fills gaps | Medium — OAuth flows, maintenance | Medium — external API stability | **Selective** |
| Browser automation (Playwright) | Medium — digital chore execution | Very High — 50MB binaries, scraper maintenance | Critical — security, trust model violation | **No** |
| Repackage (Electron/PyInstaller) | Medium — onboarding ease | Very High — architecture change | Critical — contradicts serverless model | **No** |
| Hardware (appliance) | Low — different product | Extreme — manufacturing, firmware | Critical — scope explosion | **No** |

---

## 4. Improvement Register

Master list of all improvements, cross-referenced to phases. Items marked with
origin to trace lineage from the Gemini report → architectural review → this plan.

| ID | Improvement | Origin | Type | Phase |
|---|---|---|---|---|
| I-01 | Financial resilience skill (burn rate, runway) | Vision #8 → Review P0 | Skill | 1 |
| I-02 | 1099-K threshold alerting | Vision #3 → Review P1 | Prompt | 1 |
| I-03 | Gig income email routing keywords | Vision #3 → Review P1 | Config | 1 |
| I-04 | Purchase interval observation | Vision #4 → Review alt | Prompt | 1 |
| I-05 | Enhanced social.md contact template | Vision #6 → Review P1 | Prompt/Template | 2 |
| I-06 | Pre-meeting relationship context | Vision #6 → Review P0 | Prompt | 2 |
| I-07 | AI-driven contact fact extraction | Vision #6 → Review P3 | Prompt | 2 |
| I-08 | Digital estate inventory template | Vision #5 → Review P1 | Prompt/Template | 3 |
| I-09 | Action instruction sheets | Vision #1 → Review alt | Config/Prompt | 3 |
| I-10 | Cancel subscription action type | Vision #1 → Review alt | Config | 3 |
| I-11 | Windows setup.ps1 | Vision #7 → Review P2 | Script | 4 |
| I-12 | `artha.py --doctor` unified diagnostic | Vision #7 → Review P2 | Script | 4 |
| I-13 | Apple Health export connector | Vision #2 → Review P2 | Connector | 5 |
| I-14 | Health prompt structured lab tracking | Vision #2 → Review alt | Prompt | 5 |

---

## 5. Phase 1 — Intelligence Layer (Weeks 1–3)

### Objective
Transform Artha from an observational system to a computational one by adding
arithmetic intelligence on data already present in state files.

### I-01: Financial Resilience Skill

**What:** A new skill (`scripts/skills/financial_resilience.py`) that reads
decrypted `state/finance.md.age` and computes:
- Monthly burn rate (average of last 3–6 months of expenses)
- Emergency fund runway = liquid_savings ÷ monthly_burn_rate
- Single-income scenario: "If [primary income] stops, runway = X months"
- Optional: discretionary vs. non-discretionary expense split

**Why:** This is the single highest-value improvement identified in the vision
review. It converts static financial records into actionable intelligence. It
respects Artha's directive: "Do NOT volunteer financial advice beyond surfacing
facts." Computing a burn rate *is* surfacing a fact.

**Architecture:**

```
state/finance.md.age (decrypted)
    │
    ├── Parse: Monthly expenses table
    ├── Parse: Bank/savings balances
    ├── Parse: Income entries (payroll, gig)
    │
    ▼
financial_resilience.py
    │
    ├── Compute: burn_rate = avg(last_N_months_expenses)
    ├── Compute: runway_months = liquid_savings / burn_rate
    ├── Compute: single_income_runway (if dual-income household)
    │
    ▼
Output: YAML block → stdout (consumed by AI CLI during catch-up)
        + optional: append to state/goals.md if savings goal exists
```

**Implementation:**

```python
# scripts/skills/financial_resilience.py
"""Financial resilience calculator — burn rate, runway, stress scenarios."""

from base_skill import BaseSkill

class FinancialResilienceSkill(BaseSkill):
    name = "financial_resilience"
    cadence = "weekly"
    requires_vault = True

    def execute(self, state_data: dict) -> dict:
        """Parse finance state, compute resilience metrics."""
        expenses = self._parse_monthly_expenses(state_data)
        savings = self._parse_liquid_savings(state_data)
        income_sources = self._parse_income_sources(state_data)

        burn_rate = sum(expenses[-3:]) / min(len(expenses[-3:]), 3) if expenses else 0
        runway = savings / burn_rate if burn_rate > 0 else float('inf')

        result = {
            "burn_rate_monthly": round(burn_rate, 2),
            "liquid_savings": round(savings, 2),
            "runway_months": round(runway, 1),
        }

        if len(income_sources) > 1:
            primary = max(income_sources, key=lambda s: s["amount"])
            reduced_income = sum(s["amount"] for s in income_sources) - primary["amount"]
            reduced_burn = burn_rate - reduced_income if reduced_income < burn_rate else burn_rate
            result["single_income_runway_months"] = round(savings / reduced_burn, 1) if reduced_burn > 0 else float('inf')

        return result
```

**Surfacing:** Add to quarterly scorecard (`scorecard_view.py`). Add to `prompts/finance.md`
as a "Financial Resilience" section that the AI includes in briefings when the skill output
is available.

**State file contract:** The skill reads `state/finance.md.age` but does NOT write it.
Output goes to stdout as YAML. The AI CLI decides whether to update `state/goals.md`.

**Testing:**
- `TestFinancialResilience` class with fixtures for:
  - Normal case (3+ months of data)
  - Insufficient data (<3 months) → graceful degradation
  - Zero expenses → runway = infinity
  - Single-income household → no `single_income_runway` key
  - Dual-income household → `single_income_runway` computed
  - Non-numeric data in state file → skip gracefully
- Target: 8–10 tests

**Dependencies:** None new. Uses `base_skill.py` (existing), reads state files (existing).

**Risk:**
- R1: Finance state file format varies per user. **Mitigation:** Skill uses regex/heuristic
  parsing with graceful fallback. If it can't parse, returns `{"error": "insufficient_data"}`.
- R2: Burn rate is misleading with seasonal variation. **Mitigation:** Report the raw number
  and the date range it covers. Don't interpret it.

### I-02: 1099-K Threshold Alerting

**What:** Add a rule to `prompts/finance.md` instructing the AI to:
1. During catch-up, track YTD income from platforms that issue 1099-K forms
   (Stripe, PayPal, Upwork, Etsy, Venmo business, etc.)
2. When YTD income from any single platform exceeds $5,000, flag in the briefing:
   `🟡 [Platform] YTD income ($X,XXX) exceeds $5,000 — a 1099-K will be issued for this tax year.`
3. At $20,000, escalate to 🟠 with estimated quarterly tax note.

**Why:** Pure prompt engineering. Zero code changes. High value for gig workers.
The AI already processes transaction emails — this just tells it what to watch for.

**Implementation:** Append to `prompts/finance.md`:

```markdown
### Gig & Platform Income Tracking (1099-K)

When processing emails, track cumulative year-to-date (YTD) income from these platforms:
- Stripe, PayPal, Venmo (business), Square, Etsy, eBay, Upwork, Fiverr, Uber, Lyft, DoorDash, Airbnb

Maintain a running total in the "Platform Income" section of state/finance.md:

| Platform | YTD Income | Last Updated |
|----------|-----------|--------------|
| Stripe   | $X,XXX    | YYYY-MM-DD   |

**Alert thresholds:**
- 🟡 YTD from any single platform ≥ $5,000 → "1099-K will be issued for [platform]"
- 🟠 YTD total across all platforms ≥ $20,000 → "Consider quarterly estimated tax payment"
- 🔴 Q4 starts and no quarterly payments made → "Estimated tax deadline approaching"

**Boundary:** Surface the threshold facts. Do NOT calculate tax amounts or withholding.
That is tax advice and is outside Artha's scope.
```

**Testing:** Prompt regression test (opt-in, `@pytest.mark.prompt_regression`).
Provide sample email fixtures with Stripe receipt emails and verify the AI surfaces
1099-K alerts in the briefing output.

### I-03: Gig Income Email Routing Keywords

**What:** Add routing keywords to the finance domain in `config/domain_registry.yaml`
so that Stripe, PayPal, Venmo, Upwork, and Etsy transaction emails are correctly routed.

**Implementation:** Add to `domain_registry.yaml` under `finance.routing_keywords`:

```yaml
    routing_keywords:
      # ... existing keywords ...
      - Stripe
      - PayPal
      - Venmo
      - Upwork
      - Fiverr
      - Etsy
      - "1099-K"
      - "1099-NEC"
      - "payout"
      - "earnings summary"
      - "direct deposit"
      - DoorDash
      - "Uber earnings"
```

**Testing:** Add integration test case to `tests/integration/test_catch_up_e2e.py`:
`test_stripe_receipt_routes_to_finance`.

### I-04: Purchase Interval Observation

**What:** Add a rule to `prompts/shopping.md` instructing the AI to note when a
recurring purchase hasn't been made within its typical interval.

**Implementation:** Append to `prompts/shopping.md`:

```markdown
### Purchase Interval Observation

When you observe a pattern of recurring purchases (same item or category, ≥3 occurrences),
note the typical interval in the Shopping section of state/shopping.md.

If the current date exceeds the expected reorder date by >50%, include a low-priority
note in the briefing:

🔵 "You typically order [item/category] every ~[N] weeks. Last order was [M] weeks ago."

This is observational only — do not auto-order, stage carts, or propose purchases.
```

**Testing:** None required (pure prompt guidance, advisory only).

### Phase 1 Exit Criteria

- [ ] `financial_resilience.py` skill passes 8+ unit tests
- [ ] Skill registered in `config/skills.yaml` with `cadence: weekly`, `requires_vault: true`
- [ ] `prompts/finance.md` updated with 1099-K tracking section
- [ ] `domain_registry.yaml` updated with gig income routing keywords
- [ ] `prompts/shopping.md` updated with purchase interval observation
- [ ] All 485+ existing tests still pass
- [ ] `make pii-scan` clean
- [ ] Manual validation: run catch-up with sample finance state, verify skill output appears

---

## 6. Phase 2 — Relationship & Social Enrichment (Weeks 4–5)

### Objective
Transform the social domain from a birthday/reconnect tracker into a meaningful
relationship intelligence layer — using the AI CLI itself as the extraction engine.

### I-05: Enhanced Social Contact Template

**What:** Define a structured per-contact template in `prompts/social.md` that the
AI maintains in `state/social.md` during catch-up processing.

**Template structure:**

```markdown
### [Contact Name]
- **Relation:** friend / colleague / family / neighbor
- **Spouse/Partner:** [name]
- **Children:** [name (age)], [name (age)]
- **Dietary/Allergies:** vegetarian, nut allergy, etc.
- **Key dates:** Birthday [MM-DD], Anniversary [MM-DD]
- **Last contact:** YYYY-MM-DD (email / call / in-person)
- **Last topics:** [what you discussed]
- **Notes:** [anything contextually useful]
- **Communication preference:** WhatsApp / email / text
```

**Implementation:** Add to `prompts/social.md` after the existing Reconnect Radar section:

```markdown
### Structured Contact Profiles

Maintain per-contact profiles in state/social.md using this template:

```
### [Full Name]
- **Relation:** [friend | colleague | family | neighbor | [custom]]
- **Spouse/Partner:** [name, if known]
- **Children:** [name (age)], if known
- **Dietary/Allergies:** [if known]
- **Key dates:** Birthday [MM-DD], Anniversary [MM-DD], if known
- **Last contact:** YYYY-MM-DD ([channel])
- **Recent topics:** [2-3 bullet points from last interaction]
- **Notes:** [anything useful for future conversations]
```

When processing emails from known contacts during catch-up, silently update
their profile with any new information revealed in the email (e.g., mentions of
children, upcoming events, job changes). Do not create profiles for unknown
senders — only update existing contacts.
```

**Testing:** Snapshot test — provide sample `state/social.md` with 2 contacts,
run a simulated catch-up with an email mentioning a contact's child, verify the
contact profile is updated in the snapshot output.

### I-06: Pre-Meeting Relationship Context

**What:** During the calendar processing phase of catch-up, cross-reference
meeting participants with `state/social.md` contacts and include their context
notes inline with the calendar entry in the briefing.

**Implementation:** Add to the calendar processing instructions in
`config/Artha.core.md` (or `config/workflow/` if modularized):

```markdown
### Pre-Meeting Context Injection

When processing calendar events, for each attendee:
1. Check if the attendee name or email matches a contact in state/social.md
2. If matched, include a compact context block after the calendar entry:

**Example:**
```
📅 Lunch with Dev Sharma — tomorrow 12:30 PM @ Café Vita
   ℹ Dev: colleague at Contoso. Daughter Aisha (15) — AP Physics.
     Vegan. Last spoke Mar 2 re: Seattle JS meetup.
```

Only include context for contacts with substantive notes.
Do not include context for contacts whose profile is just a name and birthday.
```

**Why:** This is the exact "Aha moment" use case from the Gemini report — a user
getting contextually rich pre-meeting intelligence — achieved entirely through
prompt engineering against data already in state files. Zero new infrastructure.

**Testing:** Integration test: provide a calendar event with an attendee matching
a contact in `state/social.md`, verify the briefing output includes the context block.

### I-07: AI-Driven Contact Fact Extraction

**What:** Instruct the AI to passively extract relationship facts from emails
during catch-up and update `state/social.md`. This extends I-05 from manual
template population to automated extraction.

**Implementation:** Add to `prompts/social.md`:

```markdown
### Passive Fact Extraction

During the email processing phase, when you encounter emails from or about
known contacts, extract and update their social.md profile with:

- Job changes mentioned in email signatures or content
- Children's names or ages mentioned in conversation
- Upcoming events they mention (weddings, moves, etc.)
- Dietary mentions ("I'm doing keto", "we're vegetarian now")
- Health mentions ("recovering from surgery") → note sensitively

**Rules:**
- Only update contacts who already have a profile in state/social.md
- Do not create new profiles from email senders
- Do not extract facts from marketing emails, newsletters, or automated messages
- Mark extracted facts with source: `[from email YYYY-MM-DD]`
- If uncertain about a fact, do not record it
```

**Testing:** Prompt regression test (opt-in).

### Phase 2 Exit Criteria

- [ ] `prompts/social.md` updated with structured template, context injection rules, and extraction rules
- [ ] Calendar processing instructions updated for pre-meeting context
- [ ] Snapshot test for contact profile update
- [ ] Integration test for pre-meeting context injection
- [ ] All existing tests pass
- [ ] `make pii-scan` clean

---

## 7. Phase 3 — Action Expansion & Estate (Weeks 6–7)

### Objective
Expand Artha's action layer from "tell me what to do" to "tell me exactly how to
do it" — without autonomous execution. Strengthen the estate domain from empty
prompts to a structured digital legacy inventory.

### I-08: Digital Estate Inventory Template

**What:** Create a comprehensive estate inventory template that guides users through
documenting their digital and legal estate. This is the 80/20 alternative to the
Gemini report's Shamir's Secret Sharing proposal.

**Implementation:** Update `prompts/estate.md` to include:

```markdown
### Digital Estate Inventory

Maintain the following inventory in state/estate.md (encrypted):

#### Legal Documents
| Document | Status | Location | Last Reviewed | Attorney |
|----------|--------|----------|---------------|----------|
| Will | [draft/signed/filed] | [physical location] | YYYY-MM-DD | [name] |
| Trust | ... | ... | ... | ... |
| POA (Financial) | ... | ... | ... | ... |
| POA (Healthcare) | ... | ... | ... | ... |
| AHCD | ... | ... | ... | ... |

#### Password & Access Recovery
| System | Access Method | Recovery Location | Last Verified |
|--------|-------------|-------------------|---------------|
| 1Password Vault | Master password | [sealed envelope location] | YYYY-MM-DD |
| Email (primary) | Recovery email/phone | [documented where] | YYYY-MM-DD |
| Bank (primary) | Online banking | [recovery method] | YYYY-MM-DD |
| Crypto wallet | Seed phrase | [physical storage] | YYYY-MM-DD |

#### Beneficiary Designations
| Account | Current Beneficiary | Last Updated | Institution |
|---------|-------------------|--------------|-------------|
| 401(k) | [name] | YYYY-MM-DD | [institution] |
| Life Insurance | [name] | YYYY-MM-DD | [provider] |
| IRA | [name] | YYYY-MM-DD | [institution] |

#### Auto-Renewing Services (to cancel upon incapacitation)
| Service | Monthly Cost | Cancellation Method | Critical? |
|---------|-------------|-------------------|-----------|
| ... | ... | ... | yes/no |

#### Emergency Contacts & Roles
| Role | Name | Phone | Relationship |
|------|------|-------|-------------|
| Executor | ... | ... | ... |
| Backup executor | ... | ... | ... |
| Attorney | ... | ... | ... |
| Financial advisor | ... | ... | ... |

### Periodic Review
- Quarterly: Review auto-renewing services list
- Annually: Full estate inventory review, verify beneficiary designations
- On life event (marriage, birth, move, job change): Full review

**Alert:** If `last_reviewed` on any legal document exceeds 12 months:
🟡 "Your [document] was last reviewed [N] months ago. Schedule a review."

If `last_reviewed` on Password & Access Recovery exceeds 6 months:
🟡 "Your emergency access procedures were last verified [N] months ago."
```

**Why:** Addresses the highest-consequence gap in Artha without introducing any
autonomous cryptographic protocols. A well-documented manual procedure is the
professional standard for estate planning.

**Testing:** Schema validation — ensure the template renders correctly in Markdown.
No runtime test needed (prompt-only change).

### I-09: Action Instruction Sheets

**What:** Expand the action registry (`config/actions.yaml`) with a new action type
`instruction_sheet` that generates step-by-step how-to guides instead of executing
autonomous behavior.

**Implementation:** Add to `config/actions.yaml`:

```yaml
  cancel_subscription:
    type: instruction_sheet
    enabled: true
    handler: null                     # no script — AI generates from knowledge
    requires_approval: false          # read-only guide generation
    friction: low
    description: >
      Generate a step-by-step cancellation guide for a specific subscription.
      Include: direct URL to cancellation page, phone number for phone-only
      cancellations, estimated time, known retention tactics and responses.
    params:
      service_name: "{service}"
    pii_check: false
    audit: true

  dispute_charge:
    type: instruction_sheet
    enabled: true
    handler: null
    requires_approval: false
    friction: low
    description: >
      Generate a step-by-step guide for disputing a charge with a merchant
      or credit card company. Include: merchant contact, CC dispute process,
      documentation needed, CFPB complaint as escalation.
    params:
      merchant: "{merchant}"
      amount: "{amount}"
      reason: "{reason}"
    pii_check: false
    audit: true
```

**Why:** This is the rational alternative to browser automation. Instead of
building a fragile Playwright scraper that navigates retention dark-patterns,
Artha generates a human-optimized instruction sheet that empowers the user.
The user retains full control; Artha provides the playbook.

**Testing:** Verify YAML schema validity. No runtime test needed.

### I-10: Cancel Subscription Action Type

**What:** When `subscription_monitor.py` detects a price increase or trial-to-paid
conversion, the briefing should proactively offer: "Want a cancellation guide for
[service]? Say 'yes' to generate."

**Implementation:** Add to `prompts/digital.md`:

```markdown
### Subscription Action Proposals

When the subscription_monitor skill detects:
- Price increase → include in briefing with: "Say 'cancel guide [service]' for
  step-by-step cancellation instructions."
- Trial-to-paid conversion → include with: "Trial ends [date]. Say 'cancel guide
  [service]' if you want to cancel before conversion."

The cancel_subscription action (actions.yaml) generates the guide on request.
```

**Testing:** Integration test — provide a state/digital.md with a subscription
price change, verify the briefing includes the cancel guide offer.

### Phase 3 Exit Criteria

- [ ] `prompts/estate.md` updated with full digital estate inventory template
- [ ] `config/actions.yaml` updated with `cancel_subscription` and `dispute_charge` actions
- [ ] `prompts/digital.md` updated with subscription action proposals
- [ ] All existing tests pass
- [ ] `make pii-scan` clean

---

## 8. Phase 4 — Platform Parity & Diagnostics (Weeks 8–9)

### Objective
Close the Windows onboarding gap and provide a single diagnostic command for
troubleshooting across all platforms.

### I-11: Windows setup.ps1

**What:** A PowerShell script that mirrors `setup.sh` behavior:
1. Check prerequisites (Python 3.11+, Git, age)
2. Create venv at `~\.artha-venvs\.venv`
3. Install dependencies
4. Copy profile template
5. Install PII git hook
6. Run demo briefing
7. Offer interactive wizard

**Architecture decisions:**
- PowerShell 5.1+ (ships with Windows 10/11 — no extra install)
- Use `Write-Host` with `-ForegroundColor` for colored output (no ANSI escape sequences)
- Use `python` not `python3` (Windows convention, documented in `Artha.core.md`)
- Check for `age.exe` in PATH or `winget` availability for install suggestion
- No execution policy bypass — script should work under `RemoteSigned`

**Implementation:**

```powershell
# setup.ps1 — Artha Turbo Setup for Windows
# Usage: powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"

function Pass($msg) { Write-Host "  ✓  $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "  ✗  $msg" -ForegroundColor Red }
function Warn($msg) { Write-Host "  !  $msg" -ForegroundColor Yellow }
function Info($msg) { Write-Host "  →  $msg" -ForegroundColor Cyan }

# [1/4] Prerequisites
Write-Host "`n[1/4] Checking prerequisites..." -NoNewline
# ... Python version check, Git check, age check

# [2/4] Virtual environment
$VenvPath = Join-Path $HOME ".artha-venvs\.venv"
if (-not (Test-Path $VenvPath)) {
    python -m venv $VenvPath
}
& "$VenvPath\Scripts\Activate.ps1"
pip install -r scripts\requirements.txt --quiet

# [3/4] Profile template
if (-not (Test-Path "config\user_profile.yaml")) {
    Copy-Item "config\user_profile.starter.yaml" "config\user_profile.yaml"
}

# [4/4] Demo briefing
python scripts\demo_catchup.py

# Offer wizard
$response = Read-Host "Run the 2-minute setup wizard now? [yes/no]"
if ($response -eq "yes") { python artha.py --setup }
```

**Testing:**
- Manual test on Windows VM/machine
- CI: add a Windows matrix entry to `.github/workflows/ci.yml` that runs
  `python -m pytest tests/ -q` on `windows-latest` (if not already present)

**Risk:**
- R3: PowerShell execution policy blocks script. **Mitigation:** Documentation
  includes `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` instruction.
  Script header includes bypass flag.
- R4: `python` command not in PATH on some Windows installs. **Mitigation:**
  Check for `py -3` launcher as fallback (Python Windows launcher).

### I-12: `artha.py --doctor` Unified Diagnostic

**What:** A single command that runs all diagnostic checks and produces a
human-readable pass/fail report.

**Checks to include:**
1. Python version ≥ 3.11
2. Virtual environment active
3. Required packages installed (PyYAML, keyring, jsonschema)
4. `age` binary in PATH
5. Encryption key in system keyring
6. `age_recipient` set in user_profile.yaml
7. OAuth tokens valid (Gmail, Outlook — if configured)
8. State directory exists with expected structure
9. PII git hook installed
10. Last catch-up date (from `state/health-check.md`)

**Output format:**

```
━━ ARTHA DOCTOR ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓  Python 3.11.7
  ✓  Virtual environment active
  ✓  Core packages installed
  ✓  age 1.1.1
  ✓  Encryption key in keyring
  ✓  age_recipient configured
  ✓  Gmail OAuth token valid (expires 2026-04-01)
  ⚠  Outlook OAuth — not configured
  ✓  State directory OK (18 domains)
  ✓  PII git hook installed
  ✓  Last catch-up: 2026-03-14 08:30 (6 hours ago)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  9 passed · 1 warning · 0 failed
```

**Implementation:** Add a `do_doctor()` function to `artha.py` that imports and
runs checks from `scripts/preflight.py` (which already does most of this) plus
adds OAuth token validation and last-catch-up date.

**Architecture:** Reuse `preflight.py` as the engine. `--doctor` is a thin
wrapper that reformats output. No new module needed.

**Testing:**
- `TestDoctor` class with:
  - All checks pass → exit 0
  - Missing `age` → warning, not failure
  - Missing OAuth → warning, not failure
  - Missing venv → failure, exit 1
  - 6–8 tests

### Phase 4 Exit Criteria

- [ ] `setup.ps1` created, documented in README
- [ ] `artha.py --doctor` implemented with 6+ tests
- [ ] README updated: Windows section now references `setup.ps1`
- [ ] All existing tests pass on macOS/Linux; Windows CI (if added) passes
- [ ] `make pii-scan` clean

---

## 9. Phase 5 — Connector Expansion (Weeks 10–12)

### Objective
Add the single highest-value new data source: Apple Health exports. This phase
is conditional on Phases 1–4 being complete and stable.

### I-13: Apple Health Export Connector

**What:** A connector (`scripts/connectors/apple_health.py`) that parses Apple
Health XML export files placed in a designated import directory.

**Data flow:**
1. User exports health data from iPhone: Settings → Health → Export All Health Data
2. User places `export.zip` in `state/imports/` (or any location)
3. User runs: `python scripts/pipeline.py --source apple_health --file path/to/export.zip`
4. Connector extracts and parses `export.xml`
5. Yields structured JSONL records for: weight, blood pressure, heart rate,
   steps, lab results, medications, immunizations

**Architecture:**
- Uses Python stdlib `xml.etree.ElementTree` — no new dependencies
- Does NOT connect to Apple Health APIs (no HealthKit, no network access)
- Import is user-initiated, explicit, local-only
- Connector slot already exists: `domain_registry.yaml` → `health.optional_connectors: [apple_health]`

**Record schema:**

```json
{
  "source": "apple_health",
  "type": "HKQuantityTypeIdentifierBodyMass",
  "value": 82.5,
  "unit": "kg",
  "date_iso": "2026-03-14T08:30:00-07:00",
  "device": "iPhone"
}
```

**Security considerations:**
- Health data is PHI-adjacent. Output goes to `state/health.md.age` (encrypted).
- PII guard scans output before any AI CLI processing.
- Import file should be deleted after processing (user's responsibility, but prompt user).
- No data leaves the local machine.

**Implementation:**

```python
# scripts/connectors/apple_health.py
"""Apple Health XML export parser — local-only, no API, no network."""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from connectors.base import BaseConnector

class AppleHealthConnector(BaseConnector):
    name = "apple_health"

    # Record types to extract (subset of Apple Health's 100+ types)
    TRACKED_TYPES = {
        "HKQuantityTypeIdentifierBodyMass",
        "HKQuantityTypeIdentifierBloodPressureSystolic",
        "HKQuantityTypeIdentifierBloodPressureDiastolic",
        "HKQuantityTypeIdentifierHeartRate",
        "HKQuantityTypeIdentifierStepCount",
        "HKQuantityTypeIdentifierBodyMassIndex",
        "HKQuantityTypeIdentifierOxygenSaturation",
        "HKQuantityTypeIdentifierBloodGlucose",
    }

    def fetch(self, since, max_results, auth_context, source_tag, **kwargs):
        file_path = Path(kwargs.get("file", ""))
        if not file_path.exists():
            raise FileNotFoundError(f"Apple Health export not found: {file_path}")

        # Extract XML from ZIP
        with zipfile.ZipFile(file_path, 'r') as z:
            xml_name = next((n for n in z.namelist() if n.endswith('export.xml')), None)
            if not xml_name:
                raise ValueError("No export.xml found in ZIP")
            # Iterative parsing for memory efficiency
            count = 0
            with z.open(xml_name) as xf:
                for event, elem in ET.iterparse(xf, events=('end',)):
                    if elem.tag == 'Record' and elem.get('type') in self.TRACKED_TYPES:
                        record_date = elem.get('startDate', '')
                        if record_date >= since:
                            yield {
                                "source": "apple_health",
                                "type": elem.get('type'),
                                "value": float(elem.get('value', 0)),
                                "unit": elem.get('unit', ''),
                                "date_iso": record_date,
                                "device": elem.get('sourceName', 'unknown'),
                            }
                            count += 1
                            if count >= max_results:
                                return
                    elem.clear()  # Free memory during iterative parse
```

**Testing:**
- `TestAppleHealthConnector` class with:
  - Valid ZIP with export.xml → yields expected records
  - Missing ZIP → FileNotFoundError
  - ZIP without export.xml → ValueError
  - `since` filter respects date boundary
  - `max_results` caps output
  - Unknown record types ignored
  - Large file memory efficiency (iterative parse)
  - 8–10 tests
  - Test fixture: minimal XML file with 5 records (no real health data)

**Risk:**
- R5: Apple changes export format. **Mitigation:** Only parse well-documented
  `Record` elements. Use defensive parsing with graceful skip on unrecognized
  structure.
- R6: Export files can be very large (500MB+). **Mitigation:** Iterative XML
  parsing (`iterparse`) + `elem.clear()` keeps memory constant. `max_results`
  caps output.

### I-14: Health Prompt Structured Lab Tracking

**What:** Enhance `prompts/health.md` to instruct the AI to maintain a structured
lab results table in `state/health.md.age`.

**Implementation:** Add to `prompts/health.md`:

```markdown
### Longitudinal Lab Results

When lab results are available (from Apple Health import, patient portal emails,
or user-provided data), maintain a chronological table in state/health.md:

#### Lab History — [Family Member Name]
| Date | Test | Result | Unit | Reference Range | Flag |
|------|------|--------|------|----------------|------|
| 2026-03-14 | Total Cholesterol | 205 | mg/dL | <200 | 🟡 |
| 2026-03-14 | LDL | 130 | mg/dL | <100 | 🟠 |
| 2025-09-01 | Total Cholesterol | 195 | mg/dL | <200 | ✅ |

**Trend detection:** If a lab value has ≥3 data points, note the trend:
- ↑ Increasing (latest > previous 2 average)
- ↓ Decreasing
- → Stable (within 5% of previous 2 average)

Surface trends in the quarterly scorecard: "Cholesterol ↑ trending up over 18 months."

**Boundary:** Report trends factually. Do NOT interpret medical significance or
recommend treatment changes. That is medical advice.
```

**Testing:** Prompt regression test (opt-in).

### Phase 5 Exit Criteria

- [ ] `apple_health.py` connector passes 8+ unit tests
- [ ] Connector registered in `config/connectors.yaml` with `enabled: false` (opt-in)
- [ ] `prompts/health.md` updated with lab tracking template
- [ ] All existing tests pass
- [ ] `make pii-scan` clean
- [ ] Manual validation: export health data from iPhone, run connector, verify JSONL output

---

## 10. Deferred & Rejected Items

### Deferred (revisit conditions specified)

| Item | Source | Condition to Revisit |
|---|---|---|
| FHIR API integration | Vision #2 | When SMART on FHIR registration becomes viable for personal apps |
| Docker self-hosted image | Vision #10 | When ≥5 users request it via GitHub Issues |
| Stripe/Upwork/PayPal API connectors | Vision #3 | When email parsing proves insufficient for gig income tracking |
| iMessage/WhatsApp chat history parsing | Vision #6 | When privacy-preserving local-only extraction is architecturally sound |
| Monte Carlo financial simulations | Vision #8 | When deterministic arithmetic proves insufficient for financial resilience |

### Rejected (with rationale)

| Item | Source | Rationale |
|---|---|---|
| **Playwright browser agent** | Vision #1 | Security: unacceptable attack surface (prompt injection → session hijacking). Trust model: violates human-gate contract. Maintenance: N scrapers for N sites. Dependencies: 50+ MB browser binaries. See [product_vision_review.md §1](product_vision_review.md). |
| **Ambient screen OCR daemon** | Vision #9 | Privacy: captures passwords, private messages, medical data displayed on screen. Functionally equivalent to keylogger. Contradicts privacy-first architecture. Dependencies: Tesseract (large binary) + continuous CPU usage. |
| **Hardware appliance** | Vision #10 | Scope: consumer electronics product requiring manufacturing, firmware, FCC certification, customer support. Incompatible with AI-CLI-as-runtime architecture (no embeddable LLM for Pi). |
| **Electron/PyInstaller packaging** | Vision #7 | Architecture: Artha has no server process to embed. Electron adds 200+ MB. PyInstaller is fragile across OS versions. The target user can run `bash setup.sh`. |
| **Shamir's Secret Sharing** | Vision #5 | Cryptographic risk: incorrect implementation means either irrecoverable keys or unauthorized key distribution — both catastrophic. Dead-man's switch has unacceptable false-positive rate. Estate planning requires legal instruments, not autonomous code. |
| **Household supply chain (Instacart/Amazon cart)** | Vision #4 | No public API exists for cart manipulation. Would require browser automation (rejected) or ToS-violating API reverse engineering. Purchase interval observation (I-04) provides 80% of the value. |

---

## 11. Risk Register

| ID | Risk | Probability | Impact | Phase | Mitigation |
|---|---|---|---|---|---|
| R1 | Finance state file format varies across users; resilience skill can't parse | Medium | Medium | 1 | Graceful fallback: return `{"error": "insufficient_data"}`. Document expected format in prompt. |
| R2 | Burn rate misleading with seasonal spending variation | Low | Low | 1 | Report raw number + date range. Don't interpret. User draws own conclusions. |
| R3 | Windows PowerShell execution policy blocks setup.ps1 | Medium | Medium | 4 | Documentation includes `Set-ExecutionPolicy` instruction. Script header includes `-ExecutionPolicy Bypass`. |
| R4 | `python` command not in PATH on Windows (Microsoft Store Python) | Medium | Low | 4 | Check for `py -3` launcher as fallback. Provide troubleshooting in README. |
| R5 | Apple changes Health export XML format | Low | Medium | 5 | Parse only well-documented `Record` elements. Defensive parsing with graceful skip. Version the parser. |
| R6 | Apple Health export files are very large (500MB+) | Medium | Medium | 5 | Iterative XML parsing (`iterparse`) + `elem.clear()` → constant memory. `max_results` caps output. |
| R7 | Prompt changes cause regression in existing briefing quality | Low | High | 1-3 | Prompt regression tests (`@pytest.mark.prompt_regression`) gate each prompt change. Snapshot tests for critical paths. |
| R8 | Gig income routing keywords cause false-positive domain routing | Low | Low | 1 | Monitor routing accuracy via eval_runner hit-rate tracking (enhance.md §2.7). Graduation policy for learned routes. |
| R9 | Social contact extraction creates stale/incorrect facts | Medium | Low | 2 | Extraction only updates existing contacts (never creates). Facts marked with source date. User can correct during next catch-up. |
| R10 | Estate template contains PII if committed | N/A (prevented) | Critical | 3 | `state/estate.md.age` is encrypted + gitignored. Template in `prompts/estate.md` uses placeholder values only. `make pii-scan` validates. |
| R11 | Scope creep — "just one more feature" delays delivery | Medium | High | All | Each phase has explicit exit criteria. Phases are independently deployable. No phase depends on a later phase. Phase N+1 only starts after Phase N passes exit criteria. |

---

## 12. Privacy & Security Hardening

### Existing Defense Layers (preserved)

| Layer | Mechanism | Coverage |
|---|---|---|
| 1 | `pii_guard.py` — regex pre-filter | SSN, CC, ITIN, DOB, phone, email, passport, Aadhaar |
| 2 | AI semantic redaction during catch-up | Context-aware PII in email bodies |
| 3 | Git pre-commit hook (`make pii-scan`) | All distributable files |
| 4 | `age` encryption at rest | Sensitive domains (finance, health, immigration, estate, employment, insurance, vehicle, contacts, digital, boundary) |
| 5 | CI enforcement | `pii-check.yml` GitHub Actions workflow on every push |
| 6 | Keyring credential storage | OAuth tokens, encryption keys — never plaintext |
| 7 | Telegram bridge PII filter | Every outbound message through `pii_guard.filter_text()` |
| 8 | Channel sender whitelist | Unknown senders silently ignored |
| 9 | Rate limiting | 10 commands/min per sender, 60s cooldown |
| 10 | Audit trail | All actions logged to `state/audit.md` |

### Additions in This Plan

**12.1 — Verify .gitignore completeness**

The current `.gitignore` is comprehensive. Verify these entries exist (they do as of
v5.1.0 but must be maintained):

```gitignore
# PII-bearing files — NEVER commit
state/*.md
state/*.md.age
.tokens/
config/user_profile.yaml
config/Artha.identity.md
config/Artha.md
config/settings.md
config/channels.yaml
config/routing.yaml
config/artha_config.yaml
briefings/
summaries/
backups/
```

**12.2 — New state file protection**

If Phase 5 introduces `state/imports/` for Apple Health exports:
- Add `state/imports/` to `.gitignore`
- Health export files contain PHI — must be encrypted at rest or deleted after import

**12.3 — PII guard expansion for health data**

If Apple Health connector is built, add health-specific PII patterns to `pii_guard.py`:
- Medical record numbers (MRN): `\b[A-Z]{2,3}\d{6,10}\b` (institution-prefixed)
- Provider NPI: `\b\d{10}\b` (10-digit NPI — context-dependent, flag only near "NPI" keyword)

**12.4 — Estate domain PII contract**

`prompts/estate.md` template uses placeholder values (`[name]`, `[phone]`, `[location]`).
The filled-in version lives in `state/estate.md.age` — encrypted. The prompt itself
(`prompts/estate.md`) must NEVER contain real data. This is enforced by:
- `make pii-scan` scanning `prompts/*.md`
- CI `pii-check.yml` running on every push

**12.5 — Environment variable discipline**

All credentials follow the existing pattern — no changes needed:

| Credential | Storage | Fallback |
|---|---|---|
| `age` private key | System keyring (`artha`, `age-key`) | `ARTHA_AGE_KEY` env var |
| Gmail OAuth token | `.tokens/gmail-token.json` (gitignored) | — |
| MS Graph OAuth token | `.tokens/msgraph-token.json` (gitignored) | — |
| Telegram bot token | System keyring | `ARTHA_TELEGRAM_BOT_TOKEN` env var |
| Channel PIN | System keyring (`artha-channel-pin`) | `ARTHA_CHANNEL_PIN` env var |

---

## 13. Testing Strategy

### Test Pyramid

```
                    ┌─────────────┐
                    │   Prompt    │  Opt-in, real LLM, ≥80% accuracy
                    │  Regression │  @pytest.mark.prompt_regression
                    ├─────────────┤
                 ┌──┤ Integration │  E2E routing, PII, security
                 │  ├─────────────┤  tests/integration/
              ┌──┤  │    Unit     │  Per-module, mocked I/O
              │  │  ├─────────────┤  tests/unit/
              │  │  │  Snapshot   │  Output format stability
              │  │  └─────────────┘
              │  │
              Total target: 520+ tests (currently 485)
```

### New Tests per Phase

| Phase | New Tests | Target Total |
|---|---|---|
| Phase 1 | 10–12 (financial_resilience: 8, routing: 2, integration: 2) | ~497 |
| Phase 2 | 4–6 (snapshot: 2, integration: 2, prompt regression: 2) | ~503 |
| Phase 3 | 2–3 (schema validation: 2, integration: 1) | ~506 |
| Phase 4 | 6–8 (doctor: 6, CI matrix: 2) | ~514 |
| Phase 5 | 8–10 (apple_health: 8, prompt regression: 2) | ~524 |
| **Total new** | **30–39** | **~524** |

### Test Fixtures

**Financial resilience fixtures:**
```yaml
# tests/fixtures/finance_state_sample.md
# Minimal finance state with 3 months of expenses and savings balance
# Uses redacted/fictional data — never real PII
```

**Apple Health fixtures:**
```xml
<!-- tests/fixtures/apple_health_sample.xml -->
<!-- 5 records: 2 weight, 1 heart rate, 1 steps, 1 blood pressure -->
<!-- Fictional data only -->
```

**Social contact fixtures:**
```markdown
<!-- tests/fixtures/social_state_sample.md -->
### Jane Smith
- **Relation:** colleague
- **Children:** Alex (12)
- **Last contact:** 2026-02-01
```

### CI Integration

Current CI (`ci.yml`):
- `python -m pytest tests/ -q`
- `make pii-scan`
- `make lint`

Additions:
- Phase 4: Add `windows-latest` to CI matrix (if not present)
- All phases: new tests automatically included in `pytest tests/`

---

## 14. Success Criteria

### Per-Phase Success Metrics

| Phase | Metric | Target |
|---|---|---|
| 1 | Financial resilience skill executes without error on sample data | ✅ |
| 1 | Briefing includes burn rate when skill data available | Manual verify |
| 2 | Pre-meeting context appears for known contacts | Manual verify |
| 2 | Contact facts extracted from emails during catch-up | Manual verify |
| 3 | Estate inventory template renders correctly | Schema validation |
| 3 | Cancel subscription guide generated on request | Manual verify |
| 4 | `setup.ps1` completes on Windows 10/11 | Manual verify |
| 4 | `artha.py --doctor` produces correct pass/fail report | 6+ unit tests |
| 5 | Apple Health connector parses XML export correctly | 8+ unit tests |
| 5 | Lab results table maintained across multiple imports | Manual verify |

### Overall Success Criteria

| Criterion | Measurement |
|---|---|
| Zero regression | All 485+ existing tests pass after each phase |
| Zero new binary dependencies | `pip list` unchanged except Python packages |
| PII clean | `make pii-scan` passes after every commit |
| Backward compatible | Users who don't enable new features experience zero changes |
| Documentation current | README reflects new capabilities as they're added |
| Test count growth | ≥520 tests after all 5 phases (from 485 baseline) |

---

## 15. Relationship to enhance.md

This plan and `specs/enhance.md` are complementary, not competing:

| Concern | enhance.md | improve.md (this doc) |
|---|---|---|
| **Scope** | New domains, connectors, household modes, i18n | Intelligence depth on existing domains |
| **Origin** | Internal architecture review | External market analysis → feasibility filter |
| **Domains** | Pets, Caregiving, Community, Business, Wellness | Finance (depth), Social (depth), Estate (depth), Health (depth) |
| **Infrastructure** | Domain registry, enable/disable, lazy loading, subagent model | Financial resilience skill, formatted action sheets, Apple Health connector |
| **UX** | Mobile journaling, live sync, phone workflows | Windows parity, unified diagnostics |
| **Timeline** | Phase 1a/1b → Phase 2 → Phase 3 → Phase 4 | Phase 1 → Phase 5 (independent track) |

**Sequencing guidance:** enhance.md Phase 1a (domain registry infrastructure) can
proceed in parallel with improve.md Phase 1 (financial resilience skill). They touch
different files and have no dependencies.

improve.md's new skill (`financial_resilience.py`) will be registered in the domain
registry created by enhance.md Phase 1a — but it works without the registry too (direct
registration in `config/skills.yaml`).

**No conflict areas identified.** Both plans respect the same architectural constraints:
no new server processes, no new binary dependencies, human-gated write actions,
PII defense preserved.

---

*Lead Principal System Architect*
*March 14, 2026*
