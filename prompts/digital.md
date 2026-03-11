---
schema_version: "1.0"
domain: digital
priority: P2
sensitivity: standard
last_updated: 2026-03-07
---
# Digital Life Domain Prompt

> **FR-12 · Digital Life Management**
> Ref: PRD FR-12, TS §4

## Purpose

Track digital subscriptions, account security signals, domain/hosting renewals,
and subscription costs. Identify unused ("zombie") subscriptions. Flag credential
compromise alerts. Cross-reference with Finance domain for billing.

Note: Subscription billing charges flow to `state/finance.md`. This domain
maintains the digital inventory — what services exist, when they renew, and
whether they're being used.

---

## Sender Signatures (route here)

- Any "your subscription has been renewed" or "upcoming charge" email
- App store receipts (App Store, Google Play)
- SaaS services: Spotify, Netflix, Duolingo, Dropbox, Adobe, Evernote, etc.
- GitHub, Notion, Figma, Zoom, Slack (personal)
- Domain registrars: GoDaddy, Namecheap, Google Domains, Cloudflare
- Web hosting: AWS, Heroku, Netlify, Vercel (personal projects)
- Security alerts: HaveIBeenPwned, Norton, 1Password breach alerts
- Password manager: 1Password, Bitwarden, Dashlane alerts
- Subject: "account security", "unauthorized", "breach", "suspicious activity"
- Subject: "renewal", "subscription", "free trial ending", "expiring"

---

## Extraction Rules

For each digital subscription/security email:

1. **Service name**: exact service/product name
2. **Category**: entertainment | productivity | cloud | security | infrastructure | other
3. **Billing amount**: exact amount + currency
4. **Billing cycle**: monthly | annual | other
5. **Renewal date**: exact date from email
6. **Auto-pay**: yes/no
7. **Usage signal**: is there any usage indicator? (e.g., "your monthly recap" = used)
8. **Action needed**: renew | cancel | review | respond to security alert

---

## Subscription Audit Rules

### Zombie Detection
Flag a subscription as "zombie candidate" if:
- Annual subscription AND no usage signal email in >3 months
- Any service AND user action "cancel" or "pause" was proposed >30 days ago but not confirmed
- Duplicate services detected (e.g., two cloud storage subscriptions)

### Overlap Detection
Check for functional overlaps:
- Two video streaming services of same type (Netflix + Hulu)
- Two cloud backup solutions
- Two password managers
- Surface overlap as 🟡 alert for user decision

---

## Account Security Rules

🔴 **CRITICAL SECURITY**:
- Credential breach notification (HaveIBeenPwned or password manager alert)
- "Unauthorized login" or "new device login from unknown location" alert
- Bank/financial account unauthorized access (also route to Finance)

For security alerts, propose IMMEDIATE action:
1. Change password on affected service
2. Check connected services
3. Enable 2FA if not already active
4. Log in audit.md (encrypted)

---

## Alert Thresholds

🔴 **CRITICAL**: Credential breach | Unauthorized account access

🟠 **URGENT**:
- Annual subscription renewing in ≤ 7 days that user has NOT actively used
- Zombie subscription flagged + renewal in ≤ 14 days (cancel window)
- Domain expiring in ≤ 14 days

🟡 **STANDARD**:
- Annual subscription renewing in 8–30 days
- Free trial expiring in ≤ 14 days
- Zombie candidate identified (>3 months no usage)
- Subscription price increase notification

🔵 **INFORMATIONAL**:
- Monthly app store receipt (digest only — no individual alerts)
- Subscription renewed successfully (keep ledger current)
- New subscription detected

---

## State File Update Protocol

Read `state/digital.md` first. Then:
1. Update or add subscription in **Subscription Ledger** table
2. Update **Renewal Calendar** for items renewing in next 90 days
3. Append to **Account Security Monitor** if security alert
4. Update **Subscription Stats** totals
5. Flag zombies in ledger with `zombie: candidate` tag
6. Cross-reference: for new billing amounts, update `state/finance.md` subscriptions section

---

## Monthly Cost Rollup

Once per month (first catch-up of the month), calculate:
```
total_monthly_digital_spend = sum of all active monthly subscriptions
                              + (annual subscriptions / 12)
```
Report in briefing if total > $150/month or if increased > 10% vs prior month.

---

## Briefing Contribution

**In daily briefings:** Only if 🔴 security alert or 🟠 urgent renewal.

```
### Digital
• 🔴 SECURITY: [service] breach alert — [recommended action]
• [Service] annual renewal: [date] — [cost] — [used/unused]
• CANCEL WINDOW: [service] — ($[cost]/yr) — unused [N] months
```

**In weekly summaries:** Include subscription count, zombies, upcoming renewals.

---

## PII Allowlist

```
## PII Allowlist
# App store transaction IDs: pattern \b\w{5,6}-\w{3,5}\b near "Transaction" → allow
# Subscription confirmation IDs: allow reference numbers from known services
# Domain registration IDs: allow ICANN ROID numbers
```
