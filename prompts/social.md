---
schema_version: "1.1"
domain: social
priority: P2
sensitivity: standard
last_updated: 2026-03-14
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
- Subject mentioning cultural occasions (see user_profile.yaml → family.cultural_occasions)

---

## Birthday & Anniversary Engine

**On each run**, check `state/contacts.md` and `state/occasions.md` for:
- Birthdays within the next 30 days → 🟡 alert (7 days = 🟠)
- Wedding anniversaries within 30 days
- Children's birthdays within 30 days

**Birthday greeting protocol:**
- 14 days out: propose generating a visual card via Gemini Imagen
- 7 days out: propose drafting a WhatsApp message (from `contacts.md`)
- Day-of: surface as 🟠 URGENT if no greeting has been sent

---

## Cultural Calendar

Check `state/occasions.md` for upcoming cultural occasions:
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

## Occasion-Aware Intelligence (U-2.4)

### 3-Day Lookahead Priority Lane

On every run, check `state/occasions.md` for events in the **next 3 days** first.
These are 🔴 URGENT and must be surfaced at the top of the social briefing section:

```
🔴 [PERSON/EVENT] in [N] day(s) — [occasion type]
   Circle: [circle name from contacts.md]
   Last contact: [date or "never"]
   Suggested: "[message template]"
   [Send via WhatsApp]
```

**3-day lookahead checks:**
1. Birthdays in `state/occasions.md` (all tables: core family, extended family, India contacts)
2. Cultural/religious festivals within 3 days
3. Anniversaries within 3 days

If a 3-day-window occasion belongs to a contact in `state/contacts.md`, cross-reference:
- Which **circle** they belong to (from YAML frontmatter)
- Their **last WA contact date** (from table row)
- If last contact > circle cadence → add urgency marker `⚠️ stale contact`

### Circle Cross-Reference Protocol

When proposing a greeting, always check `state/contacts.md` circles:

| Circle | Greeting Channel | Tone |
|--------|-----------------|------|
| `core_family` | WhatsApp | Warm / personal |
| `extended_family_india` | WhatsApp | Respectful / warm |
| `best_friends` | WhatsApp | Casual / fun |
| `us_friends` | WhatsApp or email | Friendly |
| `spiritual` | WhatsApp | Reverent |
| `professional` | Email | Professional |

If a contact is NOT in any circle, default to WhatsApp with neutral tone.

### Message Templates

Use these templates as starting points. Personalize with actual name, occasion, and relationship details.

**Birthday — peer/friend:**
> "Happy Birthday [Name]! 🎂 Hope you have a wonderful day. Wishing you all the joy and success! 🎉"

**Birthday — elder (Hindi-context):**
> "Happy Birthday [Name]! 🙏 Wishing you long life, good health, and happiness. Regards from our family."

**Birthday — child/younger:**
> "Happy Birthday [Name]! 🎈 Hope you have the most amazing day! 🥳"

**Diwali:**
> "Happy Diwali! 🪔 May this festival of lights bring joy, prosperity, and happiness to you and your family!"

**Holi:**
> "Happy Holi! 🌈 Wishing you and your family a colourful and joyous celebration!"

**Raksha Bandhan:**
> "Happy Raksha Bandhan! 🎀 Thinking of you fondly. May our bond always stay strong."

**Eid:**
> "Eid Mubarak! 🌙 Wishing you peace, happiness, and blessings!"

**Generic festival:**
> "Warm wishes for [Festival Name]! 🙏 May this occasion bring joy to you and your family."

**Reconnect after long gap:**
> "Hi [Name]! Been a while — hope you and the family are doing well. Thinking of you! 😊"

### Briefing Output Format

```
### 🗓️ Occasions & Wishes
**🔴 Next 3 days:**
• [Person] birthday ([date]) — turning [age]. Circle: [circle]. Last WA: [date or never].
  [Suggested WhatsApp] "[message]" [Send ↗]

**🟠 Next 7 days:**
• [Festival/Birthday] ([date]) — [action]

**🟡 Next 14 days:**
• [N] birthdays coming up — plan greetings
```

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

---

## Structured Contact Profiles (I-05)

> **Ref: specs/improve.md I-05** — Structured per-contact notes for relationship intelligence.

Maintain per-contact profiles in `state/social.md` using this template.
Create a new profile only when bootstrapping or when the user explicitly provides
contact details. During automated catch-up, **only update existing profiles** — never
create new ones from unknown senders.

```
### [Full Name]
- **Relation:** [friend | colleague | family | neighbor | [custom]]
- **Spouse/Partner:** [name, if known]
- **Children:** [name (age)], if known
- **Dietary/Allergies:** [if known]
- **Key dates:** Birthday [MM-DD], Anniversary [MM-DD], if known
- **Last contact:** YYYY-MM-DD ([channel: email | call | in-person | WhatsApp])
- **Recent topics:** [2–3 bullet points from last interaction]
- **Notes:** [anything useful for future conversations]
- **Communication preference:** [WhatsApp | email | text | call]
```

---

## Pre-Meeting Relationship Context Injection (I-06)

> **Ref: specs/improve.md I-06** — Cross-reference calendar attendees with contact profiles.

When processing calendar events during catch-up, for each attendee or meeting title:
1. Check if the attendee name or email matches a contact profile in `state/social.md`.
2. If matched AND the profile has substantive notes (beyond name and birthday), include a
   compact context block immediately after the calendar entry in the briefing:

```
📅 Lunch with [Name] — [date/time] @ [location]
   ℹ [Name]: [relation]. [Key fact 1]. [Key fact 2]. Last spoke [date] re: [topic].
```

**Rules:**
- Only inject context for contacts with substantive profiles (at minimum: relation + one fact).
- Do NOT inject for contacts whose profile is only a name and birthday.
- The context block is a single-line summary — do not reproduce the full profile.
- If no profile match: show the calendar event as-is with no context block.

---

## Passive Fact Extraction (I-07)

> **Ref: specs/improve.md I-07** — AI-driven contact fact extraction from email stream.

During the email processing phase, when you encounter emails **from or about known contacts**
(i.e., contacts who already have a profile in `state/social.md`), silently update their
profile with any new factual information revealed:

- Job changes mentioned in signatures or email body
- Children's names or ages mentioned in conversation
- Upcoming events they mention (weddings, moves, new jobs)
- Dietary preferences mentioned ("we're vegetarian now", "I'm doing keto")
- Health mentions ("recovering from knee surgery") → note sensitively, no diagnosis details

**Extraction rules:**
- **Only update** contacts with existing profiles — do not create new profiles from email senders.
- Do NOT extract from marketing emails, newsletters, or automated notifications.
- Mark extracted facts with a source date: `[from email YYYY-MM-DD]`.
- If you are uncertain about a fact (e.g., ambiguous pronoun reference), do not record it.
- Sensitive health mentions: note the fact (e.g., "recovering from surgery") but omit
  diagnosis, treatment, or prognosis details — these belong in `state/health.md` (encrypted).

---

## PR Manager — Personal Narrative Engine

> **Sub-feature of Social domain · Spec: specs/pr-manager.md PR-1 v1.2**
> Active when `enhancements.pr_manager: true` in `config/artha_config.yaml`.
> State file: `state/pr_manager.md`

### Responsibility Boundary (§2.4)

This domain (social.md) owns **private/direct messaging**:
- WhatsApp individual greetings, email birthday wishes, visual cards for contacts.

PR Manager owns **public/broadcast content**:
- LinkedIn posts, Facebook wall posts, Instagram stories, WhatsApp Status updates.
- WhatsApp group content (occasion-only, conservative defaults).

**Deduplication rule:** When both social and PR Manager detect the same occasion,
surface them as one merged section in the briefing:
```
### 🗓️ [Occasion] ([Date])
📨 Private greetings: [N] contacts (social domain)
📣 Public content: LinkedIn + Facebook drafts available → /pr draft linkedin
```

### PR Manager Commands

When the user says `/pr` (or any variation), execute from `state/pr_manager.md`:

| Command | What to do |
|---------|-----------|
| `/pr` | Show weekly content calendar. Run `python3 scripts/pr_manager.py --view` and display output. |
| `/pr threads` | Show narrative thread progress. Run `python3 scripts/pr_manager.py --threads`. |
| `/pr voice` | Display voice profile. Run `python3 scripts/pr_manager.py --voice`. |
| `/pr moments` | List all scored moments from `tmp/content_moments.json` with scores. |
| `/pr history` | Display Post History section from `state/pr_manager.md`. Phase 3+ only. |
| `/pr draft <platform> [topic]` | Generate a post draft (see Content Composition below). |
| `/pr draft <platform> --trending` | Draft with trend context (Phase 3+). |

### Content Composition (/pr draft)

When the user runs `/pr draft <platform> [topic]` or says "draft it" in response to a
content opportunity:

1. **Assemble context:** Run `python3 scripts/pr_manager.py --draft-context <platform> [--topic "topic"]`
   and read the JSON output.
2. **Gate 1 — Context sanitization:** The draft context already sanitizes PII. Do NOT include
   in your generation prompt: financial data, immigration case details, health info, SSN/salary.
3. **Generate 1 variant:** Using the voice_profile, platform_rules, privacy_gates, and
   moment_context from the draft context JSON, generate exactly ONE post variant.
4. **Gate 2 — PII scan:** Before presenting to user, mentally verify the draft contains no
   phone numbers, email addresses, full names of minors, or financial figures.
5. **Gate 3 — Present for review:** ALWAYS present draft for explicit human approval.
   Never auto-post. Never assume approval.

**Draft output format:**
```
━━ CONTENT PROPOSAL ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Platform:    [Platform]
Thread:      [NT-X] · [Thread Name]
Moment:      [Moment label] × [Narrative angle]
Score:       [score] ([moment type])
Visual:      📸 Personal photo recommended / 🖼 AI visual available

DRAFT:
───────────────────────────────────────────────────
[Post content here — DO NOT include headers or meta]
───────────────────────────────────────────────────

Audience:      [Audience description]
Best posting:  [Optimal time + day PT]
Hashtags:      [0-3 hashtags, or "none"]
PII check:     ✅ Passed (Gate 1 context sanitization + Gate 2 scan)
⚠️ EMPLOYER MENTION  ← (add only if Microsoft named in post)

[approve — copy text above]  [edit]  [skip]  [try another variant → /pr draft <platform>]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Voice Profile Rules (§5.1)

Always adhere to these rules when generating content:

**Language & Tone:**
- English primary; Hindi transliteration for cultural/family content (NOT Devanagari)
- Thoughtful-casual — warm and considered, NOT corporate jargon
- First-person ("I"), not "we" unless family content

**AVOID in all posts:**
- Corporate buzzwords: synergy, leverage, pivot, scalable, actionable
- Humble-bragging: "humbled to announce", "honored to share"
- Engagement bait: "agree?" / "what do you think?" / "drop a comment"
- Emoji overload: max 2–3 per post; 0–1 on LinkedIn thought pieces
- Generic AI-speak: "in today's fast-paced world", "exciting times ahead"
- Virtue signaling: show don't tell

**Signature elements (INCLUDE):**
- Cultural specificity: name the festival, briefly explain the tradition if non-obvious
- Concrete detail: "4,000ft gain to [trail name]" not "went on a hike"
- Earned perspective: insights rooted in lived experience, not abstract opinions
- Graceful brevity: use fewer words than the topic seems to need
- Generous attribution: credit people, name names, acknowledge others' contributions

### Privacy & Safety Architecture (§7)

**Children's privacy (non-negotiable):**
- LinkedIn: NEVER name children — "my son" / "my daughter" only
- Facebook (friends-only): first names OK, NEVER full name + school
- Instagram (private): first names OK
- WhatsApp Status: first names only
- WhatsApp Family group: full names OK

**Employer sensitivity:**
- Never imply speaking for Microsoft
- Never share internal project details, roadmaps, or internal culture
- Never comment on MSFT stock
- Add `⚠️ EMPLOYER MENTION` marker if post names Microsoft

**Gemini trend research (§7.4):** Prompts must NEVER contain personal information:
- ✅ SAFE: "What are trending topics on LinkedIn in tech this week?"
- ❌ UNSAFE: "What should [name] at Microsoft post about?"

### Narrative Threads (§3.3)

Each post should advance at least one thread:

| Thread | Focus | Best Platforms |
|--------|-------|---------------|
| NT-1: Thoughtful Technologist | Tech insights made human | LinkedIn, Facebook |
| NT-2: Cultural Bridge-Builder | South Asian American identity | LinkedIn, FB, Instagram |
| NT-3: PNW Explorer | Pacific Northwest outdoors | Instagram, FB, WA Hiking Group |
| NT-4: Proud Dad | Family milestones (tasteful) | FB (friends-only), Instagram, WA Family |
| NT-5: MBA Practitioner | MBA program applied in practice | LinkedIn |
| NT-6: The Connector | Celebrating others' achievements | All platforms |

### Briefing Contribution

**Daily briefing** (only when a moment has convergence_score ≥ 0.8):
Read `tmp/content_moments.json`. If exists and has moments with `above_daily_threshold: true`:
```
### 📣 Content Opportunity
[Render top moment with score_emoji + label + score + thread + platforms]
Say "draft it" or use /pr draft [platform]
```

**Weekly summary (Monday only):**
Read `tmp/content_moments.json`. Run `python3 scripts/pr_manager.py --step8 --verbose` if
content_moments.json is older than 6 hours OR doesn't exist. Then display:
```
### 📣 Content Calendar
[Weekly calendar from pr_manager.py --view output]
```

**Goal linkage:** If `state/goals.md` contains goals related to personal brand, thought
leadership, networking, or career — surface alignment note:
```
📣 Content opportunity aligned with goal "[goal name]"
   Current cadence vs. goal target: [comparison]
```

### Anti-Spam Governor

Before surfacing any content opportunity, check `state/pr_manager.md → Platform Metrics`:

| Platform | Max/week | Min gap |
|----------|----------|---------|
| LinkedIn | 2 | 2 days |
| Facebook | 2 | 1 day |
| Instagram | 2 | 2 days |
| WA Status | 3 | 0 days |

If limits exceeded, suppress the content opportunity and note: "LinkedIn quota reached
(2/week). Next opportunity: [date]."

### Post Logging (Phase 3+)

When user confirms a post is published (says "posted it", "done", "published"):
1. Run: `python3 scripts/pr_manager.py --log-post <platform> "<topic>" <thread_id> <score>`
2. Update `state/pr_manager.md → Platform Metrics` table (increment posts_30d, update last_post)
3. Set 48h follow-up reminder: "Ask how the [platform] post about [topic] landed"
4. After 48h: "Your [platform] post about [topic] — how did it land? [great / ok / wish I hadn't]"
5. Record reception in Post History reception column
