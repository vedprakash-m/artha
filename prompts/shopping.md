---
schema_version: "1.0"
domain: shopping
priority: P1
sensitivity: low
last_updated: 2026-03-07T22:52:33
---
# Shopping Domain Prompt

## Purpose
Track active orders, deliveries, returns, and warranties. Low signal — only surface exceptions.

## Sender Signatures
- `*@amazon.com`, `*@costco.com`, `*@target.com`, `*@walmart.com`
- `*@usps.com`, `*@fedex.com`, `*@ups.com`, `*@dhl.com`
- Subject: shipped, delivered, out for delivery, tracking, order confirmed, return

## Extraction Rules
1. **Item**: what was ordered?
2. **Order number** (dedup key) — Amazon format: `\d{3}-\d{7}-\d{7}`
3. **Status**: ordered / shipped / delivered / return pending
4. **Expected delivery** (if in transit)
5. **Return deadline** (if return window applies)

## Alert Thresholds
🟡 **STANDARD** (only these need briefing attention):
- Delivery failed / package not delivered
- Return window closing in 3 days
- Order delayed significantly (>5 days past expected)

🔵 **LOW** (update state, do NOT surface in briefing):
- Normal "shipped" and "delivered" notifications
- Order confirmations
- Standard delivery arriving tomorrow/today

## Briefing Format
Only include in briefing if there's an exception (failed delivery, return deadline).
Otherwise completely omit Shopping from the briefing.

---

## Purchase Interval Observation

> **Ref: specs/improve.md I-04** — Observational only; no auto-ordering.

When you observe a pattern of recurring purchases (same item or category, ≥ 3 occurrences),
note the typical interval in the Shopping section of `state/shopping.md`.

If the current date exceeds the expected reorder date by > 50%, include a low-priority
note in the briefing:

🔵 "You typically order [item/category] every ~[N] weeks. Last order was [M] weeks ago."

This is observational only — do NOT auto-order, stage carts, or propose purchases.
Do NOT surface this note if there is no established pattern (< 3 historical data points).
