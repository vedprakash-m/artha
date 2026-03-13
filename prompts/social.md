---
schema_version: "1.0"
domain: social
priority: P2
sensitivity: standard
last_updated: 2026-03-07
---
# Social Domain Prompt

> **FR-11 · Relationships & Social Intelligence**
> Ref: PRD FR-11, TS §4, §7.4.3, §7.4.6, UX §18

## Purpose

Track relationships — birthdays, cultural occasions, reconnect opportunities,
and greeting actions. Generate action proposals for occasion-appropriate greetings
(WhatsApp messages, email greetings, visual cards via Gemini Imagen).

The family maintains close ties with relatives and friends near and far.
Cultural occasions relevant to the family's background (defined in §1)
are first-class events — no less important than Western holidays.

---

## Sender Signatures (route here)

- Evite, Paperless Post, or similar — party/event invitations
- Subject: "birthday", "anniversary", "wedding", "baby shower", "graduation"
- Social media notification emails (LinkedIn anniversary, etc.)
- Emails from/about family India contacts
- Subject mentioning cultural occasions: "Diwali", "Holi", "Navratri", "Eid"

---

## Birthday & Anniversary Engine

**On each run**, check `config/contacts.md` and `config/occasions.md` for:
- Birthdays within the next 30 days → 🟡 alert (7 days = 🟠)
- Wedding anniversaries within 30 days
- Children's birthdays within 30 days

**Birthday greeting protocol:**
- 14 days out: propose generating a visual card via Gemini Imagen
- 7 days out: propose drafting a WhatsApp message (from `contacts.md`)
- Day-of: surface as 🟠 URGENT if no greeting has been sent

---

## Cultural Calendar

Check `config/occasions.md` for upcoming cultural occasions:
- 30 days out: mention in weekly summary planning section
- 14 days out: propose creating a visual greeting card batch
- 7 days out: propose sending WhatsApp/email greetings to relevant contact group
- Day-of: surface as 🟡 STANDARD if no action taken

**Occasion-triggered visual generation:**
```
python scripts/safe_cli.py gemini -p "Generate a [occasion] greeting card with traditional [motif] elements. High quality, vibrant colors, warm tone. Save as PNG."
```
(No personal data in Gemini prompt — descriptive only. No PII wrapper needed per TS §8.7.)

---

## Reconnect Radar

Check `state/social.md` Reconnect Radar section:
- Flag contacts with `last_contact` > 90 days in weekly summary
- Propose a reconnect message (personalize based on `state/memory.md` notes)

---

## Extraction Rules

For each social/occasion email:

1. **Event type**: birthday | anniversary | invitation | graduation | condolence | holiday
2. **Person(s)**: who is involved
3. **Date**: date or date range of the occasion
4. **Action proposed**: send greeting | attend event | send gift | no action
5. **Channel**: WhatsApp | email | both
6. **Visual needed**: yes/no (occasions get visuals; standard birthdays may not)

---

## Alert Thresholds

🔴 **CRITICAL**: None in this domain

🟠 **URGENT**:
- Birthday of close family member in ≤ 7 days with no greeting action queued
- Major cultural occasion in ≤ 7 days with no greeting action queued
- Event RSVP deadline in ≤ 2 days

🟡 **STANDARD**:
- Birthday in 8–30 days
- Cultural occasion in 8–30 days
- Reconnect radar: close contact not reached in > 120 days
- Event invitation needing RSVP

🔵 **INFORMATIONAL**:
- Reconnect reminder: contact not reached in 90–120 days

---

## Action Proposal Format

Greetings are Level 0 actions (propose only, never auto-send):
```
ACTION PROPOSAL: Send [occasion] greeting
  Type: WhatsApp message / Email
  To: [Contact Name] ([relationship])
  Occasion: [occasion + date]
  Proposed message: "[drafted message]"
  Visual: [attached / not generated]
  Trust required: Level 1
  [approve] [edit] [skip]
```

---

## State File Update Protocol

Read `state/social.md` first. Then:
1. Add/update upcoming birthdays and occasions (next 30 days)
2. Log sent greetings to Greeting Log
3. Update Reconnect Radar (add new contacts to flag, remove recently contacted)

---

## Briefing Contribution

**In daily briefings:** Only if 🟠 alert (close family birthday within 7 days).

**In weekly summaries:** Include upcoming occasions next 2 weeks, reconnect radar summary.

```
### Social
• [Person] birthday: [date] — [7 days / 14 days]
• [Occasion]: [date] — [proposed action]
• Reconnect: [N] contacts > 90 days since last contact
```

---

## Relationship Signal Extraction

> Parse all emails, calendar events, and message threads for relationship signals.
> Update `state/social.md` relationship graph and communication patterns accordingly.

### Signal Types
```yaml
signal_types:
  event_invitation:
    source: email subject contains "invited", "party", "wedding", "shower", "celebration"
    action: Extract event name, date, host. Add to Upcoming Events. Queue RSVP action if ≤ 14 days.

  birthday_mention:
    source: email mentioning "birthday" for known contact OR birthday in contacts.md within 30-day window
    action: Update upcoming birthdays section. Queue greeting proposal.

  reply_pattern_signal:
    source: Contact replied to our email within 24hrs → high reciprocity signal
    action: Update communication_patterns.initiated_vs_received_ratio. Log positive reciprocity.

  group_thread_activity:
    source: Email/WhatsApp thread involving ≥3 known contacts
    action: Log in communication_patterns.group_thread_activity. Update last_contact for all participants.

  life_event_signal:
    source: Subject or body contains "congratulations", "condolences", "new job", "baby", "moved", "engaged", "married"
    action: Create life_events entry. Queue acknowledgment action within 48 hours.

  reconnect_signal:
    source: Contact reaches out after extended silence (>60 days) OR mutual friend mentions them
    action: Move out of reconnect queue. Update last_contact. Log engagement.
```

### Signal Extraction Rules (Per Email)
1. Check subject + sender against known contacts in contacts.md
2. Identify signal_type from above taxonomy
3. Extract: `{signal_type, contact_name, date_detected, raw_context_snippet (≤100 chars)}`
4. Update `state/social.md` relevant section (life_events, reconnect_queue, upcoming_birthdays, communication_patterns)
5. Propose action if appropriate (greeting, RSVP, acknowledgment)

---

## Reconnect Queue Logic

> Automatically populate `state/social.md → reconnect_queue` based on last_contact vs frequency_target.

```yaml
reconnect_thresholds:
  close_family:
    yellow: 7 days overdue
    red: 14 days overdue
    action: "WhatsApp message or call"

  close_friend:
    yellow: 45 days overdue
    red: 90 days overdue
    action: "WhatsApp catch-up message"

  extended_family:
    yellow: 120 days overdue
    red: 180 days overdue
    action: "WhatsApp or email check-in; time occasion-based messages"

  acquaintance:
    yellow: 270 days overdue
    red: 365 days overdue
    action: "LinkedIn/email touchpoint; keep warm"
```

**Reconnect queue generation (every catch-up):**
1. For each contact in `state/social.md → relationships`, compute `days_since_contact`
2. Compare to `frequency_target` converted to days (daily=1, weekly=7, biweekly=14, monthly=30, quarterly=90, annual=365)
3. If `days_since_contact > threshold`, add to reconnect_queue with `days_overdue` and `tier`
4. Sort queue by tier priority: close_family first, then close_friend, then extended_family
5. Surface up to **5 reconnect suggestions** per briefing (don't overwhelm)

---

## Contact Frequency Monitoring

> Track whether communication cadence is meeting targets.

**Per-contact status (computed at each catch-up):**
```
on_cadence:   last_contact ≤ frequency_target days
at_risk:      frequency_target < last_contact ≤ 1.5x frequency_target
overdue:      last_contact > 1.5x frequency_target
```

**Group-level summary for briefing:**
```
Close family: N/N on cadence (or: X overdue)
Close friends: N/N on cadence (or: X at risk)
Extended family: N/N on cadence (or: X overdue)
```

---

## Reciprocity Tracking

> Flag communication imbalance — if always initiating but rarely receiving responses, flag for awareness.

```yaml
reciprocity_rules:
  low_reciprocity_flag:
    condition: initiated > 3x received over rolling 90 days AND total_initiated >= 3
    action: Surface in briefing as informational: "[Contact] — you've reached out 5x with 1 response in 90 days"
    severity: informational
    note: "User decides whether to continue investing; no judgment — some relationships are one-directional by design"

  high_reciprocity_positive:
    condition: received > initiated over rolling 90 days (they reach out more)
    action: Update pattern, no alert (positive)

  dormant_mutual_flag:
    condition: Both initiated and received = 0 for > double the frequency_target
    action: Move to reconnect queue
```

---

## Weekly Relationship Health Summary

**Include in weekly briefing social section:**
```
### 🤝 Relationship Health
• On cadence: [N] contacts | At risk: [N] | Overdue: [N]
• Reconnect queue: [top 3 names + days overdue]
• Upcoming (14 days): [birthdays + occasions list]
• Life events requiring acknowledgment: [if any]
```

---

## PII Allowlist

```
## PII Allowlist
# Phone numbers in contacts.md are allowlisted for WhatsApp URL generation
# Example: "+1-555-867-5309" → PII when stored in state files
# Exception: contacts.md is encrypted — the phone numbers live there safely
# State/social.md must NEVER store phone numbers — use name + contacts.md reference only
```
