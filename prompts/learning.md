---
schema_version: "1.0"
domain: learning
priority: P1
sensitivity: standard
last_updated: 2026-03-07
---
# Learning Domain Prompt

> **FR-10 · Personal Learning & Development**
> Ref: PRD FR-10, TS §4, UX §7

## Purpose

Track the primary user's personal learning activities — courses, books, newsletters, podcasts,
and Obsidian vault signal (if accessible). Connect learning activity to goal
metrics. Surface when the monthly learning goal is at risk. Prevent newsletter
content from becoming noise.

This domain does NOT track the children's school learning — that belongs in
`kids.md`. This domain is the primary user's personal growth only.

---

## Sender Signatures (route here)

- Coursera, Udemy, edX, LinkedIn Learning, Pluralsight, O'Reilly
- Substack newsletters (any — digest format)
- Email receipts from book purchases (Kindle, Amazon, Audible)
- YouTube Learning notifications (if configured)
- Podcast apps (if email digests exist)
- Subject: "course", "certificate", "lesson", "chapter", "episode"
- Internal Microsoft L&D or training announcements (learning-related only)

---

## Extraction Rules

For each learning-related email:

1. **Type**: course | book | article | podcast | newsletter | certificate | event
2. **Title**: name of the course, book, newsletter
3. **Platform**: Coursera, Kindle, Substack, etc.
4. **Progress** (if progress email): percentage complete, modules done
5. **Key learning**: 1-sentence summary if article/newsletter (never store full content)
6. **Completion signal**: certificate email, "you finished" trigger
7. **Action needed**: enroll deadline, renewal, live session to attend

---

## Newsletter Handling

Newsletters are HIGH-VOLUME, LOW-SIGNAL. Apply the following:
- **DO NOT** treat newsletters as individual items — digest them weekly
- Capture the newsletter name + date only; no article summaries unless
  the newsletter has a direct connection to an active goal or domain
- Exception: newsletters on immigration, finance, or immigration policy —
  forward these items to the relevant domain prompt

Newsletter digest format (weekly summary contribution only):
```
Learning: [N] newsletters: [name1], [name2]... [optional: 1 standout item]
```

---

## Alert Thresholds

🔴 **CRITICAL**: None in this domain

🟠 **URGENT**:
- Active course has missed 3+ consecutive sessions (stall risk)
- Certificate deadline within 7 days with less than 50% completion
- Paid course nearing expiry with < 50% completion

🟡 **STANDARD**:
- Monthly learning goal on track: no alert (positive signal, mention in weekly summary)
- Monthly learning goal at risk: total hours < 50% of target by mid-month
- New course enrollment (noteworthy, not urgent)
- Certificate earned (positive — always mention)

🔵 **INFORMATIONAL**:
- Monthly hours vs. target (weekly summary only)
- New newsletter subscription detected

---

## State File Update Protocol

Read `state/learning.md` first. Then:
1. Update `Active Courses` table with any progress updates
2. Append `Recent Learning Activity` with today's items  
3. Update `monthly_target_hours` current_month_hours (increment for logged sessions)
4. If newsletter: update the newsletter digest list (no full content)
5. Update streak counter if activity occurred

## Goal Integration

Read `state/goals.md`. Find the learning consistency goal.
If learning hours this month < target threshold:
- Flag in briefing contribution under goals section
- Suggest: "X hrs learning this month — [N] hrs behind target"

---

## Briefing Contribution

**In daily briefings:** Only if alert exists or positive milestone reached (cert earned).

```
### Learning
• [Course]: [progress]% — [action/note]
• Certificate earned: [name] on [platform]
```

**In weekly summaries:** Always include a 2-3 line learning summary.

---

## PII Allowlist

```
## PII Allowlist
# Course ID numbers, certificate serial numbers, enrollment IDs — not PII
# Example: "Certificate #1234567" → allow
```
