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
and net-worth-relevant events for the Mishra family.

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
