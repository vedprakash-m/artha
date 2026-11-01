---
schema_version: "1.0"
domain: finance
priority: P0
sensitivity: high
last_updated: 2026-03-07T22:52:33
---
# Finance Domain Prompt

## Purpose
Track bills, account balances, financial transactions, tax matters, payroll, investments,
and net-worth-relevant events for the family.

## Sender Signatures (route here)
- `*@wellsfargo.com`, `*@chase.com`, `*@bankofamerica.com`, `*@fidelity.com`, `*@vanguard.com`
- `*@irs.gov`, `*@wa.gov` (state tax)
- Subject: bill, payment due, statement available, payment received, ACH, wire
- Subject: W-2, 1099, tax, payroll, direct deposit, pay stub
- Subject: credit alert, balance alert, low balance, fraud alert
- Subject: subscription renewal, auto-renew (if financial)
- Credit card statements: Visa, Mastercard, Amex, Discover
- Utility bills: PSE, Puget Sound Energy, water, internet, phone
- `*@equifax.com`, `*@experian.com`, `*@transunion.com` → 🔴 Critical (identity events)

## Extraction Rules
For each finance email, extract:
1. **Transaction type** — bill, payment receipt, statement, alert, tax doc
2. **Bill/account identifier** — which bill? which account? (use last 4 digits only)
3. **Amount** — dollar amount if present
4. **Due date or payment date** — when is it due / when was it paid?
5. **Auto-pay status** — is this on auto-pay?
6. **Action required** — needs manual payment? decision needed?

## Alert Thresholds
🔴 **CRITICAL**:
- Credit/identity alert from any bureau (Equifax, Experian, TransUnion)
- Fraud alert or unauthorized transaction notice
- Bill past due (already overdue)
- Low balance alert (below threshold in settings.md)
- IRS notice or levy

🟠 **URGENT**:
- Bill due within 7 days, NOT on auto-pay
- Payroll deposit not received on expected pay date
- Large transaction >$500 on non-recurring basis
- Credit card statement with balance >$2,000

🟡 **STANDARD**:
- Bill due within 14 days (even if auto-pay) — just note
- Monthly statement available — note balance/amount due
- Payroll received as expected
- Investment quarterly statement

🔵 **LOW** (suppress from briefing unless user asks):
- Payment received confirmations that match expected auto-pays
- Marketing emails from financial institutions

## Deduplication
- Unique key: bill ID/account + due date combination
- If bill reminder received twice for same bill+date, update status — do not duplicate
- Payroll: unique key = pay date

## State File Update Protocol
Read `state/finance.md` first. Then:
1. **Bills**: Update "Pending Bills / Due Soon" — mark paid when payment confirmation received
2. **Accounts**: Update "Accounts" table with latest balance if mentioned in email
3. **Tax**: Update "Tax" section for any tax-related emails
4. **Payroll**: Update "Payroll" section with latest paycheck
5. Archive paid bills (keep last 3 months inline, move older to archive)
6. Monthly: recalculate approximate net worth from account balances

## PII Redaction
- Bank account numbers: keep only last 4 digits → `****[last4]`
- Credit card numbers: keep only last 4 → `****[last4]`
- Routing numbers: redact fully → `[ROUTING-REDACTED]`
- SSN/ITIN on tax docs: `***-**-[last4]`
- Keep: institution names, amounts, dates, bill types

## Briefing Format
```
### Finance
• [Bill name]: $[amount] due [date] — [auto-pay: yes/no] [🟠 if <7 days, no autopay]
• [Account]: balance $[amount] as of [date]
• [Tax/payroll item]: [status]
• Net worth estimate: $[X] (±[date])
```

## Monthly Synthesis
At catch-up, if last monthly synthesis >30 days ago:
- Total bills this month: $X
- Payroll received: $X
- Net savings estimate: $X
- Budget deviation: [on track / over budget in category]

---

## Budget Category Tracking

> **Purpose (T-1C.2.1):** Categorize all spending signals from email into budget categories, flag anomalies, and project month-end spend from mid-month signals.

### Budget Categories
Classify all spending/billing emails into these categories:
```yaml
budget_categories:
  housing:        [mortgage, HOA, utilities, renter/home insurance, repairs]
  transportation: [car payment, car insurance, gas, parking, tolls, ride-share]
  groceries:      [Costco, Amazon Fresh, grocery stores, meal delivery]
  dining:         [restaurants, food delivery apps]
  healthcare:     [medical bills, prescriptions, dental, vision, FSA/HSA transactions]
  education:      [school fees, tutoring, SAT prep, extracurricular activity fees]
  subscriptions:  [streaming, software, gym, Amazon Prime, news]
  savings:        [401K, IRA, 529, brokerage transfers — negative spend = positive]
  immigration:    [attorney fees, USCIS filing fees]
  personal:       [clothing, personal care, gifts]
  travel:         [flights, hotels, vacation spending]
  other:          [anything not clearly categorized above]
```

### Anomaly Detection
At Monthly Synthesis, compare current month spend per category against the 3-month rolling average:
```
anomaly_threshold: 20%  # flag if current month > 120% of 3-month average

alert_yellow: category spend > 120% of rolling average AND delta > $100
alert_red:    category spend > 150% of rolling average AND delta > $300
special_case: immigration category — any amount gets surfaced (no averaging; always notable)
```

### Predictive Spend Forecasting
Mid-month (day 15–20): project month-end spend from signals received so far:
```
projected_month_end = (spend_so_far / days_elapsed) * days_in_month
if projected_month_end > previous_month * 1.15:
    alert: "On track to exceed last month's spend by [N]% — review [category]"
```

### State file update
Update `state/finance.md → budget_categories` with:
- Category totals for current month (cumulative, updated each catch-up)
- Month-over-month deltas
- 3-month rolling averages (recomputed from stored monthly totals)
- Family has accounts at multiple institutions — do not assume one bank
- Some bills are on auto-pay, others require manual action — distinguish carefully
- Immigration fees (USCIS filing fees) are financial events but route primarily to immigration.md; note in finance.md as well
- Tax season (Jan–Apr) is high-activity; expect more W-2, 1099, tax payment emails

---

## Leading Indicators

> **Purpose (TS §6.1):** Forward-looking metrics that predict future financial stress or opportunity *before* it becomes a crisis. Compute these at every catch-up and surface in briefing if trending unfavorably.

```yaml
leading_indicators:

  savings_rate_trend:
    description: "Month-over-month savings rate as % of take-home pay"
    source: finance.md — monthly_net_income, monthly_expenses
    formula: "(monthly_net_income - monthly_expenses) / monthly_net_income * 100"
    target: "≥ 20%"
    alert_yellow: "savings_rate < 15% for 2 consecutive months"
    alert_red: "savings_rate < 10% OR negative (spending > income)"
    briefing_trigger: "yellow or red trend"

  credit_utilization_trend:
    description: "Credit card balance as % of total credit limit across all cards"
    source: finance.md — credit_cards[].balance, credit_cards[].limit
    formula: "sum(balances) / sum(limits) * 100"
    target: "≤ 30%"
    alert_yellow: "utilization between 30–50%"
    alert_red: "utilization > 50% OR rising 3+ consecutive months"
    briefing_trigger: "yellow or red + rising trend"

  emergency_fund_coverage:
    description: "Liquid emergency fund in months of expenses"
    source: finance.md — emergency_fund, monthly_expenses
    formula: "emergency_fund / monthly_expenses"
    target: "≥ 6 months (9 months recommended given immigration exposure)"
    alert_yellow: "coverage < 6 months"
    alert_red: "coverage < 3 months"
    briefing_trigger: "any drop below target"

  investment_contribution_rate:
    description: "Monthly 401K + taxable investment contributions as % of gross income"
    source: finance.md — investment_contributions, gross_income
    target: "≥ 15% of gross income"
    alert_yellow: "below 15% for 2+ months"
    alert_red: "contributions paused or skipped"
    briefing_trigger: "yellow or red"

  upcoming_large_expenses:
    description: "Known large expenses (>$2K) within 90 days"
    source: finance.md — upcoming_expenses[]
    alert_yellow: "total upcoming_large_expenses > 20% of monthly_net_income"
    briefing_trigger: "always surface if any exist — proactive cash management"
```

**Leading indicator summary line (in briefing):**
```
💰 Finance Leading: Savings [X%] [↑↓ trend] | Credit util [X%] | E-fund [N.N] months | [any alert]
```

---

## Phase 2B Expansions

### Tax Preparation Manager (F3.10, F3.13)
Track tax preparation end-to-end. Maintain in `state/finance.md → tax`:
```yaml
tax:
  current_year: YYYY
  filing_status: "[MFJ/MFS/etc]"
  deadline_standard: YYYY-04-15
  deadline_extension: YYYY-10-15     # if extension filed
  extension_filed: false
  documents_checklist:
    - name: "W-2 (Employer)"
      expected_by: YYYY-01-31
      received: false
      notes: ""
    - name: "1099-INT / 1099-DIV (Fidelity)"
      expected_by: YYYY-02-15
      received: false
    - name: "1099-INT / 1099-DIV (Vanguard)"
      expected_by: YYYY-02-15
      received: false
    - name: "Mortgage interest (1098)"
      expected_by: YYYY-01-31
      received: false
    - name: "Property tax paid"
      expected_by: "Q4 statement"
      received: false
    - name: "Charitable donation receipts"
      expected_by: YYYY-04-01
      received: false
    - name: "HSA form 5498-SA"
      expected_by: YYYY-05-31
      received: false
    - name: "1099-G (state tax refund, if applicable)"
      expected_by: YYYY-01-31
      received: false
  estimated_payments:
    - due: YYYY-04-15
      amount: XXXX
      paid: false
    - due: YYYY-06-15
      amount: XXXX
      paid: false
    - due: YYYY-09-15
      amount: XXXX
      paid: false
    - due: YYYY-01-15
      amount: XXXX
      paid: false
  carryforward_items:
    - description: "[e.g., capital loss carryforward]"
      amount: XXXX
      tax_year: YYYY
  prior_year_refund: XXXX
  expected_refund_or_owe: XXXX  # estimated after documents collected
```

**Alert thresholds:**
- 🟠 URGENT: Estimated payment due ≤7 days and not marked paid
- 🟠 URGENT: Filing deadline ≤30 days and any required document not received
- 🟡 STANDARD: Document expected by date passed and not received (>7 days overdue)
- 🟡 STANDARD: 60 days before standard filing deadline — trigger checklist review

**Routing:** W-2 emails, 1099 emails, IRS correspondence, mortgage interest statements, charitable receipts.

### Subscription Ledger & Credit Health (F3.4, F3.5, F3.6, F3.11)

**Subscription ledger** in `state/finance.md → subscriptions`:
```yaml
subscriptions:
  - name: "[service name]"
    category: streaming|saas|cloud|news|other
    monthly_cost: XXXX       # converted to monthly for comparison
    billing_cycle: monthly|annual
    next_billing: YYYY-MM-DD
    payment_method: "[card or bank]"
    auto_renews: true|false
    roi_score: null          # computed by Artha (usage × value / cost) — see §8.12
    notes: ""
```
Trigger: any subscription renewal/billing confirmation email.
Alert: subscription billing on card that expired or approaching limit.
Monthly review: surface total subscription spend and any ROI < threshold.

**Credit health monitor (F3.5):**
Track in `state/finance.md → credit_health`:
```yaml
credit_health:
  equifax_score: XXXX
  experian_score: XXXX
  transunion_score: XXXX
  last_pulled: YYYY-MM-DD
  alerts_active: []          # list of any active credit alerts from monitoring services
  utilization_pct: XX        # recomputed each catch-up from balance/limit data
  notes: ""
```
🔴 CRITICAL: Any credit alert email from Equifax, Experian, or TransUnion — surface immediately.
🟡 STANDARD: Monthly credit score update if available.

**Insurance premium aggregator (F3.11):**
Track in `state/finance.md → insurance_premiums`:
```yaml
insurance_premiums:
  - type: "health"
    provider: "[name]"
    monthly_cost: XXXX
    annual_cost: XXXX
    next_renewal: YYYY-MM-DD
  - type: "auto"
    provider: "[name]"
    monthly_cost: XXXX
    annual_cost: XXXX
    next_renewal: YYYY-MM-DD
  - type: "home/renters"
    provider: "[name]"
    monthly_cost: XXXX
    annual_cost: XXXX
    next_renewal: YYYY-MM-DD
  - type: "life"
    provider: "[name]"
    monthly_cost: XXXX
    annual_cost: XXXX
    next_renewal: YYYY-MM-DD
  - type: "umbrella"
    provider: "[name]"
    monthly_cost: XXXX
    annual_cost: XXXX
    next_renewal: YYYY-MM-DD
total_monthly_insurance: XXXX  # updated automatically from sum of above
```
Cross-reference with insurance.md for coverage details. Alert on renewals ≤45 days.

### Credit Card Benefit Optimizer (F3.12)
Cross-domain trigger: When a travel booking, hotel confirmation, or large purchase arrives:
1. Check `state/finance.md → credit_cards[].benefits` for applicable travel/purchase benefits
2. Surface relevant benefits the user might not use:
   - Travel credit: "Your [card] has $[X] travel credit unused — this booking qualifies"
   - Lounge access: "Your [card] offers Priority Pass — check lounge availability at [airport]"
   - Trip insurance: "Your [card] provides trip cancellation insurance — no need for separate add-on"
   - Purchase protection: "Your [card] offers [X] months purchase protection for this item"
3. Alert once per booking — do not repeat in same session

Schema addition to `state/finance.md → credit_cards[].benefits`:
```yaml
benefits:
  travel_credit_annual: XXXX
  travel_credit_used_ytd: XXXX
  lounge_access: "[program name or none]"
  trip_insurance: true|false
  purchase_protection_months: N
  extended_warranty: true|false
  cell_phone_protection: true|false
  cash_back_categories: ["[category]: [%]"]
```

---

## Gig & Platform Income Tracking (1099-K)

> **Ref: specs/improve.md I-02** — Pure prompt alert; no code changes.

When processing emails, track cumulative year-to-date (YTD) income from these platforms:
- Stripe, PayPal, Venmo (business), Square, Etsy, eBay, Upwork, Fiverr, Uber, Lyft, DoorDash, Airbnb

Maintain a running total in a "Platform Income" section of `state/finance.md`:

```
### Platform Income (YTD)
| Platform | YTD Income  | Last Updated |
|----------|-------------|---------------|
| Stripe   | $X,XXX      | YYYY-MM-DD   |
```

**Alert thresholds:**
- 🟡 YTD from any single platform ≥ $5,000 → "1099-K will be issued for [platform] — keep records"
- 🟠 YTD total across all platforms ≥ $20,000 → "Consider quarterly estimated tax payment"
- 🔴 Q4 dates (Oct–Dec) with no quarterly payments on record → "Estimated tax deadline approaching"

**Boundary:** Surface the threshold facts. Do NOT calculate tax amounts or advise on withholding.
That is tax advice and is outside Artha's scope.

---

## Financial Resilience (Skill Output)

> **Ref: specs/improve.md I-01** — populated by `financial_resilience.py` skill (cadence: weekly).

If the `financial_resilience` skill has run and produced output, include this section in the
briefing when any of the following is true:
- Runway < 6 months (alert 🟠) or < 3 months (alert 🔴)
- Single-income runway < 3 months
- Burn rate changed >15% from previous period

**Briefing format when skill output available:**
```
💰 Financial Resilience
• Monthly burn rate: $[X,XXX] (avg of last [N] months)
• Emergency fund runway: [N.N] months (at current burn rate)
• Single-income runway: [N.N] months [if applicable]
```

**If skill output is not available** (vault locked, insufficient data): omit this section silently.
**Boundary:** Show the numbers. Do NOT label them as "healthy" or "alarming" — let the user draw conclusions.
