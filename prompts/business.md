---
schema_version: "1.0"
domain: business
priority: P2
sensitivity: high
last_updated: 2026-03-21T00:00:00
requires_vault: true
phase: phase_2
---
# Side Business / Freelance Domain Prompt

> **CONNECT §5.5** — Activated by `connect.domains.business_prompt: true` in `config/artha_config.yaml`.

## Purpose
Track freelance/side business income, expenses, client relationships, invoicing,
and tax obligations. Designed for 1099/gig workers, Etsy sellers, consultants,
content creators, and anyone with non-W2 income.

## Sender Signatures (route here)
- `*@stripe.com`, `*@paypal.com`, `*@square.com`, `*@squareup.com`
- `*@shopify.com`, `*@etsy.com`, `*@redbubble.com`
- `*@upwork.com`, `*@fiverr.com`, `*@toptal.com`, `*@freelancer.com`
- `*@quickbooks.com`, `*@freshbooks.com`, `*@waveapps.com`, `*@xero.com`
- Subject: invoice, payout, earnings, 1099, contractor payment
- Subject: client payment, project completion, freelance, gig payment
- Subject: quarterly estimated tax, self-employment tax
- Subject: Stripe payout, PayPal transfer, Square deposit

## Extraction Rules
1. **Type**: income | expense | invoice | client | tax_document | subscription
2. **Amount**: transaction amount (income positive, expense negative)
3. **Date**: payment date, invoice date, or tax deadline
4. **Client/Platform**: name or platform (OK in encrypted state)
5. **Tax implications**: 1099-K threshold tracking, quarterly tax flag
6. **Action**: invoice / collect / file / deduct / renew

## Alert Thresholds
🔴 **CRITICAL**:
- 1099-K threshold crossed (configurable: `user_profile.yaml → integrations.business.irs_1099k_threshold`, default $5,000 — IRS reporting trigger as of 2026)
- Always include: "Consult your CPA — thresholds change annually."

🟠 **URGENT**:
- Invoice unpaid >30 days — follow up
- Quarterly estimated tax due <14 days (April 15, June 15, Sept 15, Jan 15)
- Business license renewal due

🟡 **STANDARD**:
- New client payment received
- Platform payout processed
- Business subscription renewal due

🔵 **INFO**:
- Monthly revenue summary
- New invoice sent confirmation
- Annual 1099-K or 1099-NEC available

## Tax Tracking
- Cumulative platform income tracked vs. 1099-K threshold (IRS-configurable per year)
- Quarterly estimated tax payment reminders: April 15, June 15, Sept 15, January 15
- Deductible business expenses isolated from personal spending
- Always: "Consult your CPA for specific tax advice. Thresholds and rules change annually."

## Finance Cross-Reference
Business income/expenses contribute to net worth and cash flow in the finance domain.
Separate tracking ensures personal and business finances can be reported independently
for tax purposes. Flag to finance domain: "Business income this month: $X."

## PII Handling
- Client names: OK in encrypted state
- EIN / SSN / Tax ID → NEVER stored anywhere in Artha
- Bank routing/account numbers → `[ACCOUNT-ON-FILE]`
- Invoice amounts and dates: OK in encrypted state
- Revenue totals: OK in encrypted state summaries

## State File Update Protocol
Read `state/business.md.age` first. Then:
1. **Income**: log new payment with amount, client/platform, date
2. **Invoices**: update invoice status (issued / paid / overdue / cancelled)
3. **Expenses**: log deductible business expenses with category
4. **Clients**: update client communication and project status
5. **Tax**: update YTD income vs. threshold; flag approaching deadlines
6. Cross-reference business income with finance domain monthly summary

## Briefing Format
```
### 💼 Business
• **Revenue YTD**: $[X] ([N]% of annual target)
• **This month**: $[X] income, $[X] expenses
• **Outstanding invoices**: [N] invoices totaling $[X]
• **Next tax deadline**: [date] — estimated payment ~$[X]
• **1099-K status**: $[X] of $[threshold] threshold ([N]%)
• **Action**: [overdue invoices, tax deadline, license renewal]
```
Always include: "Tax estimates are for planning only — consult your CPA."
Omit if nothing actionable.

## State File Schema Reference
```markdown
## Income Log
| Date | Client/Platform | Amount | Project | Invoice # | Notes |
|------|----------------|--------|---------|----------|-------|

## Expenses Log
| Date | Vendor | Amount | Category | Deductible | Notes |
|------|--------|--------|----------|-----------|-------|

## Active Clients
| Client Name | Status | Active Project | Rate | Last Payment | Next Invoice |
|------------|--------|---------------|------|-------------|-------------|

## Invoices
| Invoice # | Client | Date Issued | Amount | Due Date | Status | Paid Date |
|----------|--------|------------|--------|----------|--------|-----------|

## Tax Tracker
YTD Income: $0
1099-K Threshold: $5000 (configurable)
1099-K Progress: 0%
Next Quarterly Deadline: [date]
Estimated Payment: $0

## Annual Summary
| Year | Total Income | Total Expenses | Net Profit | Tax Paid |
|------|-------------|---------------|-----------|---------|
```
