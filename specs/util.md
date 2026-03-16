# Artha Utilization Plan — From 30% to 100%

> **Status:** Draft  
> **Created:** 2026-03-15  
> **Baseline:** 30% utilization (10/25 domains with data, 2/10 connectors active, user_profile empty)  
> **Target:** 100% utilization — every domain, connector, route, and skill actively contributing  
> **Approach:** 9 workstreams, each independently actionable, ordered by impact  

---

## Table of Contents

1. [U-1: Contacts & Social Circles](#u-1-contacts--social-circles)
2. [U-2: Occasions & Relationship Warmth](#u-2-occasions--relationship-warmth)
3. [U-3: Calendar Intelligence](#u-3-calendar-intelligence)
4. [U-4: Activate All Empty Domains](#u-4-activate-all-empty-domains)
5. [U-5: Connector Overhaul — Privacy & Enablement](#u-5-connector-overhaul--privacy--enablement)
6. [U-6: Populate user_profile.yaml](#u-6-populate-user_profileyaml)
7. [U-7: Routing & Channels](#u-7-routing--channels)
8. [U-8: Fix memory.md — Cross-Session Learning](#u-8-fix-memorymd--cross-session-learning)
9. [U-9: New Skills](#u-9-new-skills)

---

## U-1: Contacts & Social Circles

### Current State

`state/contacts.md` has **44 contacts** across Family, Extended Family (India),
US Close Friends, and Professional sections. Raw data is present but
**unstructured** — no circle classification, no relationship scoring, no
AI-driven outreach suggestions.

### Vision: World-Class Friend Circle System

Contacts should be organized into **circles** — named groups that drive
proactive relationship maintenance. Each circle has a cadence (how often
Artha nudges you to reach out) and a warmth target.

### Circle Definitions

| Circle | Members (seed from contacts.md) | Cadence | Signal Sources |
|--------|---------------------------------|---------|----------------|
| **Core Family** | Archana, Parth, Trisha | Daily (passive — no nudge) | Calendar, emails, school notifications |
| **Extended Family — India** | Pradeep, Sharda, Vandana, Narendra, Ram Vilas, Manoj, Saurabh, nephews/nieces | Bi-weekly | WhatsApp last-contact dates |
| **Best Friends** | Sunil Soni, Rajiv Grover, Indresh, Ashish, Subodh, Anand | Monthly | WhatsApp, email, phone |
| **Foster MBA** | *(populate from LinkedIn/contacts)* | Quarterly | LinkedIn, email |
| **Neighbors** | *(populate manually)* | Monthly | — |
| **School/College Friends** | *(populate from contacts)* | Quarterly | WhatsApp, Facebook |
| **Professional / Microsoft** | *(populate from Outlook contacts)* | As-needed | Outlook, LinkedIn |
| **Spiritual** | Swami Garud Guru Maharaj | Weekly | WhatsApp |

### Self-Learning Architecture

```
Signal Sources → Contact Enrichment → Circle Scoring → Nudge Engine
```

1. **WhatsApp Export Parser** — Parse exported WhatsApp chats to extract:
   - Last contact date per person (already partially in contacts.md `Last WA` column)
   - Message frequency (weekly rolling average)
   - Sentiment signals (manual annotations, not AI-scanned content)

2. **Email Signal Extraction** — During catch-up pipeline (Step 4), extract:
   - Sender/recipient patterns → auto-associate emails with contact records
   - Calendar invites from contacts → relationship activity signal

3. **Call Log Integration** (future) — If Apple Health / Screen Time export
   includes call data, parse call frequency per contact.

4. **Facebook/LinkedIn** (future, opt-in) — Periodic export ingestion for
   birthday dates and relationship milestones.

### Per-Person Rich Notes

Extend `state/contacts.md` schema to include per-person structured fields:

```yaml
## Extended Family — India
### Pradeep Mishra
- **Relationship:** Younger Brother
- **Phone:** +91 90491 21401
- **DOB:** 1986-10-23
- **Family:** Wife: [name], Son: Arnav (DOB: 2013-10-30), Son: Avyaan
- **Location:** [city], India
- **Last Contact:** 2026-03-04 (WhatsApp)
- **Recent Topics:** [auto-extracted or manually noted]
- **Address:** [if known]
- **Notes:** [free text]
```

### AI Message Suggestions

When the nudge engine determines it's time to reach out to someone:

1. Artha checks recent events: birthdays, festivals, news from their region
2. Generates a contextual message suggestion (warm, personal, culturally aware)
3. Presents in briefing: *"You haven't connected with Pradeep in 12 days.
   Suggestion: Ask about Arnav's school — he would be in 7th grade now."*
4. One-tap WhatsApp action via existing `send_whatsapp` action

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-1.1 | Add `circles` YAML block to `state/contacts.md` schema | 30 min |
| U-1.2 | Seed circle membership from existing 44 contacts | 1 hr (manual + AI assist) |
| U-1.3 | Add `relationship_warmth` section to `prompts/social.md` with circle cadence rules | 30 min |
| U-1.4 | Extend catch-up Step 11 to generate "Relationship Check-In" section in briefing | 1 hr |
| U-1.5 | Create `scripts/skills/relationship_pulse.py` — compute days-since-contact per circle, surface stale contacts | 2 hr |
| U-1.6 | Populate rich notes for top 20 contacts (family + best friends) | 1 hr (manual) |
| U-1.7 | WhatsApp export parser for automated last-contact updates | 2 hr |

### Acceptance Criteria
- [ ] Every contact assigned to exactly one circle
- [ ] Briefing includes "Who to reach out to" section with ≥1 suggestion
- [ ] `relationship_pulse` skill runs on every catch-up and alerts on stale contacts

---

## U-2: Occasions & Relationship Warmth

### Current State

`state/occasions.md` has **4 family birthdays**, 1 wedding anniversary, school
milestones, financial deadlines, and immigration deadlines. But it's missing:

- Extended family birthdays (already in `contacts.md` — 10+ DOBs not in occasions)
- Indian festivals and cultural occasions
- AI-generated greeting content
- Proactive "contact someone" suggestions tied to occasions

### Birthday Enrichment

**Immediate action:** Extract all DOBs from `contacts.md` into `occasions.md`:

| Person | DOB | Source |
|--------|-----|--------|
| Pradeep Mishra | 1986-10-23 | contacts.md |
| Sharda Mishra | 1979-10-26 | contacts.md |
| Vandana Mishra Tiwari | 1982-05-29 | contacts.md |
| Manoj Dubey | 1984-07-15 | contacts.md |
| Akash Tiwari | 2005-12-03 | contacts.md |
| Sneha Tripathi | 2000-10-28 | contacts.md |
| Sumit Tripathi | 2004-12-29 | contacts.md |
| Santosh Tripathi | 1977-02-05 | contacts.md |
| Arnav Mishra | 2013-10-30 | contacts.md |
| Rajiv Grover | 1982-06-16 | contacts.md |
| Gauri Shankar Tiwari | 1976-06-25 | contacts.md |

### Indian Festival Calendar

Add to `state/occasions.md` → `## Cultural & Religious Occasions`:

```markdown
## Cultural & Religious Occasions (2026–2027)

| Festival | 2026 Date | Significance | Action |
|----------|-----------|-------------|--------|
| Holi | March 14, 2026 | Festival of colors | Send wishes to India family |
| Gudi Padwa / Ugadi | March 29, 2026 | Hindu New Year | Wish extended family |
| Ram Navami | April 6, 2026 | Lord Ram's birthday | Wish Guru Maharaj |
| Hanuman Jayanti | April 15, 2026 | | |
| Eid ul-Fitr | ~March 31, 2026 | | Wish Muslim friends |
| Raksha Bandhan | August 19, 2026 | Brother-sister bond | Sharda, Vandana → Ved, Pradeep |
| Janmashtami | August 26, 2026 | Krishna's birthday | |
| Ganesh Chaturthi | September 5, 2026 | | |
| Navratri | October 2–10, 2026 | Nine nights | |
| Dussehra | October 11, 2026 | Victory of good | |
| Diwali | October 31, 2026 | Festival of lights | **Major** — gifts/calls to all |
| Bhai Dooj | November 2, 2026 | Brother-sister | Sharda, Vandana → Ved |
| Thanksgiving | November 26, 2026 | US holiday | Friends gathering |
| Christmas | December 25, 2026 | | Neighbor/school community |
| Makar Sankranti | January 14, 2027 | Harvest festival | |
| Republic Day | January 26, 2027 | India national day | |
```

### AI-Generated Wishing Content

When a birthday or festival is within 3 days, Artha generates:

1. **WhatsApp message suggestion** — contextual, warm, in English or Hindi
   based on contact's language preference
2. **Image prompt** — A suggested prompt for visual generation tools (e.g.,
   "Create a Diwali greeting card featuring diyas and the text 'Happy Diwali
   from the Mishra family'")
3. **Gift suggestion** — For close contacts, suggest gift ideas based on
   known relationship context

### Proactive Contact Suggestions

Integrate with U-1 circles: when an occasion is approaching, Artha cross-
references the relevant circle and suggests specific people to wish.

*Example briefing output:*
> **🎂 Upcoming (3 days):**
> - Vandana's birthday (May 29) — turning 44. Last contact: Dec 14 (6 months ago!).
>   Suggested: "Happy Birthday Didi! Hope you and the family are well. 🎉"
>   [Send via WhatsApp ↗]

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-2.1 | Merge all DOBs from contacts.md into occasions.md | 30 min |
| U-2.2 | Add Indian festivals calendar section to occasions.md (2026–2027) | 30 min |
| U-2.3 | Add US holidays + school holidays to occasions.md | 15 min |
| U-2.4 | Add occasion-aware rules to `prompts/social.md`: 3-day lookahead, message templates | 30 min |
| U-2.5 | Extend briefing template to include "Occasions & Wishes" section | 30 min |
| U-2.6 | Create `scripts/skills/occasion_tracker.py` — surface upcoming occasions, cross-ref circles | 2 hr |

### Acceptance Criteria
- [ ] All known DOBs from contacts.md present in occasions.md
- [ ] Festival calendar covers Indian festivals + US holidays for 12 months
- [ ] Briefing shows upcoming occasions with message suggestions
- [ ] Occasions with stale contacts get highlighted with urgency

---

## U-3: Calendar Intelligence

### Current State

`state/calendar.md` exists and Google Calendar connector is enabled. But
calendar intelligence is **passive** — Artha reads events, doesn't create them.

### Self-Learning Event Creation

Artha should detect implicit calendar events from email signals and propose
adding them:

| Signal Source | Example | Proposed Event |
|---------------|---------|----------------|
| Doctor appointment confirmation email | "Your appointment with Dr. Smith on April 15 at 2:30 PM" | Calendar: "Dr. Smith Appointment" Apr 15 2:30 PM |
| Flight booking confirmation | "Alaska Airlines SEA→SFO Mar 28 dep 6:15 AM" | Calendar: "Flight SEA→SFO" Mar 28 6:15 AM |
| School email | "Parent-Teacher Conference Mar 20 5:00 PM" | Calendar: "PTC — Parth/Trisha" Mar 20 5:00 PM |
| Bill due date | "Your Wells Fargo mortgage payment due Apr 1" | Calendar: "Mortgage Due" Apr 1 (recurring) |
| Insurance renewal | "Policy renewal April 21, 2026" | Calendar: "Home Insurance Renewal" Apr 21 |
| DMV/registration | "Vehicle registration due October 2026" | Calendar: "Mazda Registration" Oct 1 |

### Judgment: Family vs Personal Calendar

Rules for calendar placement:

```yaml
family_calendar_patterns:
  - school events (PTC, performances, sports)
  - family doctor appointments
  - travel involving family
  - birthdays and anniversaries
  - home maintenance (both adults attend)

personal_calendar_patterns:
  - individual doctor appointments
  - work meetings
  - personal subscriptions/renewals
  - solo activities
```

### Kid's School Calendar Integration

| School | Source | Calendar |
|--------|--------|----------|
| Tesla STEM High School (Parth) | LWSD website / Canvas LMS | Parth's school events |
| Inglewood Middle School (Trisha) | LWSD website / Canvas LMS | Trisha's school events |

**Lake Washington School District (LWSD)** publishes a calendar. Key dates to track:
- First/last day of school
- Breaks (winter, spring, summer)
- Half days / early release
- Parent-teacher conferences
- No-school days (teacher workdays, holidays)

### Public Holiday Calendar

Enable Google Calendar's built-in holiday calendars:
- `en.usa#holiday@group.v.calendar.google.com` — US public holidays
- `en.indian#holiday@group.v.calendar.google.com` — Indian public holidays

Add calendar IDs to `user_profile.yaml → integrations.google_calendar.calendar_ids.holidays`.

### Prerequisites

1. **Enable `add_calendar_event` action** — Currently disabled in `config/actions.yaml`
   (`enabled: false`). Requires:
   - Implement `scripts/actions/gcal_add_event.py` handler
   - Add `calendar.events.insert` write scope to Google OAuth
   - Set `requires_approval: true` (human-gated — no event created without "yes")

2. **Email-to-event extraction rules** in `prompts/calendar.md`:
   - Define patterns for appointment confirmations, flight bookings, school notices
   - Confidence threshold: only propose events above 80% confidence

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-3.1 | Add holiday calendar IDs to user_profile.yaml | 5 min |
| U-3.2 | Add LWSD school calendar dates to occasions.md | 30 min |
| U-3.3 | Add email-to-event extraction rules in `prompts/calendar.md` | 45 min |
| U-3.4 | Implement `scripts/actions/gcal_add_event.py` handler | 2–3 hr |
| U-3.5 | Add Google Calendar write scope to existing OAuth setup | 30 min |
| U-3.6 | Enable `add_calendar_event` in actions.yaml | 5 min |
| U-3.7 | Add calendar intelligence rules to briefing: "Events detected in emails — add to calendar?" | 30 min |

### Acceptance Criteria
- [ ] Holiday calendars (US + India) appear in catch-up context
- [ ] School calendar dates for both kids are tracked
- [ ] Email signals generate proposed calendar events in briefing
- [ ] All event creation is human-gated (`requires_approval: true`)

---

## U-4: Activate All Empty Domains

### Current State — Domain Utilization Matrix

| Domain | State File | Has Data | Template Only | Action Needed |
|--------|-----------|----------|---------------|---------------|
| finance | finance.md.age | ✅ 33KB | — | Maintaining |
| immigration | immigration.md.age | ✅ | — | Maintaining |
| health | health.md.age | ✅ | — | Maintaining |
| insurance | insurance.md.age | ✅ 12KB | — | Maintaining |
| vehicle | vehicle.md.age | ✅ 15KB | — | Maintaining |
| kids | kids.md | ✅ 2KB | — | Maintaining |
| contacts | contacts.md | ✅ 5KB | — | Enriching (U-1) |
| occasions | occasions.md | ✅ 3KB | — | Enriching (U-2) |
| calendar | calendar.md | ✅ | — | Enhancing (U-3) |
| transactions | transactions.md | ✅ 239KB | — | Maintaining |
| **comms** | comms.md | ❌ | Template | **Activate** |
| **goals** | goals.md | ❌ | Template | **Activate** |
| **home** | home.md | ❌ | Template | **Activate** |
| **employment** | employment.md | ❌ | Template | **Activate** |
| **travel** | travel.md | ❌ | Template | **Activate** |
| **learning** | learning.md | ❌ | Template | **Activate** |
| **digital** | digital.md | ❌ | Template | **Activate** |
| **decisions** | decisions.md | ❌ | Template | **Activate** |
| **dashboard** | dashboard.md | ❌ | Template | **Activate** |
| **open_items** | open_items.md | ❌ | Template | **Activate** |
| **shopping** | shopping.md | ❌ | Template | **Activate** |
| **social** | social.md | ❌ | Template | **Activate** |
| **estate** | estate.md.age | ~1.2KB | Skeleton | **Populate** |
| **boundary** | boundary.md | ❌ | Template | **Activate** |
| **wellness** | wellness.md | ❌ | Template | **Activate** |
| **scenarios** | scenarios.md | ❌ | Template | **Activate** |
| **memory** | memory.md | ❌ | Schema only | **Fix** (U-8) |

**17 domains need activation.** Strategy: progressive population during
daily catch-ups. The AI extracts domain-relevant data from emails during
each session and populates the state file incrementally.

### Domain Activation Priorities

#### Priority 1 — Activate immediately (data already available in emails)

These domains have data flowing through Gmail/Outlook that is currently
being ignored because the state files are empty templates.

| Domain | Why P1 | Bootstrap Data Source |
|--------|--------|---------------------|
| **comms** | Emails contain pending follow-ups, messages needing reply | Next catch-up: extract "awaiting reply" threads |
| **goals** | User can articulate current goals right now | `/bootstrap goals` interview |
| **employment** | Microsoft employment data in emails (payroll, HR, benefits) | Catch-up extraction from work emails |
| **shopping** | Amazon/Costco order confirmations already in email | Catch-up extraction |
| **digital** | Subscription renewal emails already flowing | Catch-up extraction from digital service emails |
| **open_items** | Action items identified in every catch-up but not persisted | Enable todo_sync action + catch-up Step 11 writes |

#### Priority 2 — Activate with one-time bootstrap

| Domain | Bootstrap Method |
|--------|-----------------|
| **home** | `/bootstrap home` — mortgage details (Wells Fargo ****XXXX, $4,503.62/mo), property info from King County, utilities |
| **estate** | Already has 1.2KB skeleton; needs will/trust status, beneficiary designations, POA status |
| **decisions** | Capture 2–3 active decisions (next good one to seed the format) |
| **travel** | Next trip plans, frequent flyer programs (Alaska Airlines mileage plan) |
| **social** | Merge with U-1 circles data; friend event tracking |

#### Priority 3 — Activate with external data

| Domain | External Source |
|--------|----------------|
| **learning** | Active courses, certifications, reading list — manual entry or Canvas LMS |
| **wellness** | Apple Health export → `scripts/connectors/apple_health.py` (U-5) |
| **dashboard** | Auto-generated from all other domains — activates when ≥10 domains have data |
| **boundary** | Personal reflection on screen time, work-life balance — manual seeding |
| **scenarios** | Created on-demand via `/scenarios` command |

### Self-Activating Pipeline

Once connectors are enabled (U-5), the catch-up pipeline will organically
populate these domains:

```
Gmail fetch → routing.yaml classification → domain state update
```

The key enabler is **routing** (U-7): when routing rules map email senders
and subjects to domains, the AI automatically extracts relevant facts into
the correct state file during Step 11 (state reconciliation).

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-4.1 | Run `/bootstrap goals` to populate goals.md with current goals | 15 min |
| U-4.2 | Run `/bootstrap home` to populate home.md with mortgage/property data | 15 min |
| U-4.3 | Run `/bootstrap employment` to seed with Microsoft employment basics | 10 min |
| U-4.4 | Enable `open_items` domain — set `enabled_by_default: true` in domain_registry.yaml (already true for decisions) | 5 min |
| U-4.5 | Run next catch-up — comms, shopping, digital should self-populate from email data | 30 min |
| U-4.6 | Manually seed estate.md with current estate planning status | 20 min |
| U-4.7 | Enable wellness domain after Apple Health connector (U-5) is set up | 5 min |
| U-4.8 | Dashboard auto-activates once ≥10 domains are populated | 0 min |

### Acceptance Criteria
- [ ] ≥20 domains have non-template content after 7 days of catch-ups
- [ ] goals.md has ≥3 tracked goals
- [ ] home.md has mortgage details populated
- [ ] open_items.md persists action items across sessions

---

## U-5: Connector Overhaul — Privacy & Enablement

### Critical Fix: Gmail MCP Privacy

**Problem:** `config/connectors.yaml` has `mcp: prefer_mcp: true` on the
Gmail connector. Gmail MCP servers are **third-party unofficial tools**
that proxy email data through untrusted infrastructure.

**Fix:** Set `prefer_mcp: false` for Gmail. Use only the official Google
API via `scripts/connectors/gmail.py` (OAuth2 with user-consented scopes).

```yaml
# connectors.yaml — Gmail section (CHANGE)
gmail:
  mcp:
    prefer_mcp: false  # PRIVACY: Gmail MCP is 3rd-party, not Google-official
```

**Also audit all other connectors.** The `prefer_mcp` flag should be:
- `false` for ALL connectors handling personal email content (Gmail, Outlook, iCloud)
- `true` only for connectors where a well-known official MCP server exists
- `false` for local-file connectors (Apple Health — already correct)

Recommended `prefer_mcp` settings:

| Connector | Current | Recommended | Rationale |
|-----------|---------|-------------|-----------|
| gmail | `true` | **`false`** | No official Google MCP. 3rd-party = privacy risk |
| google_calendar | `true` | `false` | Same — use official Google API |
| outlook_email | `true` | `false` | Use MS Graph API directly |
| icloud_email | `true` | `false` | Use IMAP directly |
| outlook_calendar | `true` | `false` | Use MS Graph API directly |
| icloud_calendar | `true` | `false` | Use CalDAV directly |
| canvas_lms | `true` | `false` | Use Canvas REST API |
| onenote | `true` | `false` | Use MS Graph API |
| rss_feed | *(no section)* | N/A | No MCP applicable |
| apple_health | `false` | `false` | Correct — local file only |

### Connector Enablement Roadmap

#### Already Enabled (2/10)
- ✅ Gmail (`scripts/connectors/gmail.py`)
- ✅ Google Calendar (`scripts/connectors/gcal.py`)

#### Enable Now — Low Friction

| Connector | Setup Steps | Script |
|-----------|-------------|--------|
| **Outlook Email** | 1. Run `python scripts/setup_msgraph_oauth.py`<br>2. Authenticate with Microsoft account<br>3. Set `enabled: true` in connectors.yaml<br>4. Add to user_profile.yaml: `integrations.microsoft_graph.enabled: true` | `scripts/connectors/outlook.py` |
| **Outlook Calendar** | Same OAuth as above (MS Graph scopes include Calendar.Read) | `scripts/connectors/outlook_calendar.py` |
| **OneNote** | Same OAuth as above — add `Notes.Read` scope<br>Already has handler: `scripts/connectors/onenote.py` | `scripts/connectors/onenote.py` |

> **Note:** All three Microsoft connectors share the same MS Graph OAuth
> token. Running `setup_msgraph_oauth.py` once enables all three.

#### Enable Now — Medium Friction

| Connector | Setup Steps |
|-----------|-------------|
| **iCloud Email** | 1. Generate app-specific password at appleid.apple.com<br>2. Run `python scripts/setup_icloud_auth.py`<br>3. Set `enabled: true` in connectors.yaml<br>4. Add to user_profile.yaml: `integrations.icloud.enabled: true` |
| **iCloud Calendar** | Same app password as above — uses CalDAV protocol |
| **Canvas LMS** | Per-child setup:<br>1. Get API key from Tesla STEM Canvas instance for Parth<br>2. Get API key from Inglewood Middle Canvas instance for Trisha<br>3. Store in keyring: `artha-canvas-api-key-parth`, `artha-canvas-api-key-trisha`<br>4. Set `enabled: true`, configure `children_config` in connectors.yaml |

#### Enable Now — Local File (Zero Friction)

| Connector | Setup Steps |
|-----------|-------------|
| **Apple Health** | 1. iPhone → Health app → Profile → Export All Health Data<br>2. Transfer export.zip to Mac<br>3. Run: `python scripts/pipeline.py --source apple_health --file ~/Desktop/export.zip`<br>4. Output goes to `state/health.md` (encrypted)<br>5. Set `enabled: true` in connectors.yaml<br>6. Enable `wellness` domain in domain_registry.yaml |

#### Enable — RSS Feeds

RSS feeds for immigration and news tracking. Suggested feeds:

```yaml
rss_feed:
  enabled: true
  feeds:
    # Immigration tracking
    - url: "https://www.uscis.gov/rss/news"
      tag: immigration
      label: "USCIS News"
    - url: "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
      tag: immigration
      label: "Visa Bulletin"
    # Finance
    - url: "https://www.irs.gov/newsroom/rss-feeds"
      tag: finance
      label: "IRS News"
    # School district
    - url: "https://www.lwsd.org/rss"
      tag: kids
      label: "LWSD News"
```

**Instapaper Import:** If you use Instapaper, export your feed subscriptions
(Settings → Export → OPML) and Artha can ingest the OPML file to auto-populate
the RSS feed list.

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-5.1 | **CRITICAL:** Set `prefer_mcp: false` on Gmail connector in connectors.yaml | 2 min |
| U-5.2 | Set `prefer_mcp: false` on ALL personal-data connectors | 5 min |
| U-5.3 | Run `python scripts/setup_msgraph_oauth.py` — enables Outlook email + calendar + OneNote | 10 min |
| U-5.4 | Enable Outlook email, Outlook calendar, OneNote in connectors.yaml | 5 min |
| U-5.5 | Run `python scripts/setup_icloud_auth.py` — app-specific password flow | 10 min |
| U-5.6 | Enable iCloud email + calendar in connectors.yaml | 5 min |
| U-5.7 | Get Canvas LMS API keys for Parth (Tesla STEM) and Trisha (Inglewood Middle) | 15 min |
| U-5.8 | Enable Canvas LMS in connectors.yaml with per-child config | 10 min |
| U-5.9 | Export Apple Health data from iPhone and run pipeline | 10 min |
| U-5.10 | Enable Apple Health connector + wellness domain | 5 min |
| U-5.11 | Configure RSS feeds in connectors.yaml | 10 min |
| U-5.12 | Run `python scripts/pipeline.py --health-check` to verify all connectors | 5 min |

### Acceptance Criteria
- [ ] Gmail connector has `prefer_mcp: false`
- [ ] ALL personal-data connectors have `prefer_mcp: false`
- [ ] ≥7/10 connectors enabled and health-check passing
- [ ] MS Graph OAuth token works for Outlook + OneNote
- [ ] Canvas LMS fetches grades for both kids
- [ ] Apple Health data appears in wellness.md

---

## U-6: Populate user_profile.yaml

### Current State

```yaml
# CURRENT — nearly empty
some: value
schema_version: '1.1'
encryption:
  age_recipient: age1asdktxzk9kmuhw9s259stfg26qmqqkrag60h32jwwfg54l2ksd4qq426g8
```

This is critically incomplete. The profile drives routing, domain enablement,
briefing personalization, and connector configuration. An empty profile means
Artha is running blind.

### Target State

Based on known data from state files, contacts, and user-provided information:

```yaml
schema_version: "1.0"

family:
  name: "Mishra"
  cultural_context: "south-asian-american"
  primary_user:
    name: "Vedprakash Mishra"
    nickname: "Ved"
    role: primary
    emails:
      gmail: ""          # FILL: primary Gmail address
      outlook: "vedprakash.m@outlook.com"
      icloud: ""         # FILL: iCloud address if used
    phone: "+14159528201"
  spouse:
    enabled: true
    name: "Archana Mishra"
    role: "spouse"
    filtered_briefing: true
  children:
    - name: "Parth Mishra"
      age: 17
      grade: "11th"
      school:
        name: "Tesla STEM High School"
        district: "Lake Washington School District"
        email_domain: "lwsd.org"
        canvas_url: ""     # FILL: Tesla STEM Canvas URL
        canvas_keychain_key: "artha-canvas-api-key-parth"
      milestones:
        college_prep: true
        class_of: 2027
        new_driver: true
    - name: "Trisha Mishra"
      age: 12
      grade: "7th"
      school:
        name: "Inglewood Middle School"
        district: "Lake Washington School District"
        email_domain: "lwsd.org"
        canvas_url: ""     # FILL: Inglewood Canvas URL
        canvas_keychain_key: "artha-canvas-api-key-trisha"
      milestones:
        college_prep: false
        class_of: 2031

location:
  city: "Redmond"           # VERIFY: exact city (Redmond area based on LWSD + property)
  state: "Washington"
  county: "King County"
  country: "US"
  timezone: "America/Los_Angeles"
  property_tax_provider: "https://payment.kingcounty.gov/Home/Index?app=PropertyTaxes"
  property_parcel_id: ""    # FILL: King County parcel ID

household:
  type: "family"
  tenure: "owner"
  adults: 2
  single_person_mode: false

domains:
  immigration:
    enabled: true
    context: "EB-2 India, I-140 approved Feb 2019"
    path: "EB-2"
    origin_country: "India"
  finance:
    enabled: true
    institutions:
      - "wellsfargo.com"
      - "chase.com"
      - "fidelity.com"
      - "discover.com"
      - "bankofamerica.com"
      - "citi.com"
      - "apple.com"
    alert_thresholds:
      bill_due_days: 7
      low_balance_usd: 500
  vehicle:
    enabled: true
    vehicles:
      - make: "Mazda"
        model: "CX-5"
        year: ""             # FILL
        vin: ""              # FILL from state/vehicle.md
      - make: "Kia"
        model: "EV6"
        year: ""             # FILL
        vin: ""              # FILL from state/vehicle.md
  employment:
    enabled: true
    employer: "Microsoft"
    workiq_enabled: false

integrations:
  gmail:
    enabled: true
    account: ""              # FILL: primary Gmail address
  google_calendar:
    enabled: true
    calendar_ids:
      primary: "primary"
      additional: []         # FILL: family calendar ID if separate
      holidays: "en.usa#holiday@group.v.calendar.google.com"
  microsoft_graph:
    enabled: false           # Enable after U-5.3 (setup_msgraph_oauth.py)
    account: "vedprakash.m@outlook.com"
    todo_sync: true
    todo_lists: {}           # Populated by setup_todo_lists.py
  icloud:
    enabled: false           # Enable after U-5.5 (setup_icloud_auth.py)
    account: ""              # FILL: iCloud email
  canvas_lms:
    enabled: false           # Enable after U-5.7 (Canvas API keys)

system:
  artha_dir: "/Users/ved/Library/CloudStorage/OneDrive-Personal/Artha"
  sync_provider: "onedrive"
  briefing_timezone: "America/Los_Angeles"
  cost_budget_monthly_usd: 20.0

briefing:
  email: ""                  # FILL: email for briefing delivery
  timezone: "America/Los_Angeles"
  default_format: "standard"
  spouse_filtered: true
  email_enabled: false       # Enable when email briefing delivery is desired
  archive_enabled: true
  weekend_planner: true
  monthly_retrospective: true

budget:
  monthly_api_budget_usd: 20.0
  alert_at_percent: 80
  currency: "USD"

encryption:
  age_recipient: "age1asdktxzk9kmuhw9s259stfg26qmqqkrag60h32jwwfg54l2ksd4qq426g8"

capabilities:
  gmail_mcp: false           # PRIVACY: no third-party MCP for Gmail
  calendar_mcp: false
  vault_encryption: true
  email_briefings: false
  weekly_summary: true
  action_proposals: true
  todo_sync: true
  preflight_gate: true
  open_items_tracking: true
```

### Fields Requiring User Input

| Field | Why Needed | Current |
|-------|-----------|---------|
| `family.primary_user.emails.gmail` | Gmail fetch, briefing delivery | Unknown |
| `family.primary_user.emails.icloud` | iCloud connector | Unknown |
| `location.city` | Location-aware alerts | Inferred: Redmond/Sammamish area |
| `location.property_parcel_id` | Property tax skill | Unknown |
| Vehicle year/VIN | Vehicle registration tracking | In vehicle.md but need to extract |
| Canvas URLs | Canvas LMS connector | School-specific URLs |
| `briefing.email` | Where to send email briefings | Unknown |

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-6.1 | Replace current user_profile.yaml with the populated template above | 5 min |
| U-6.2 | Fill in Gmail address, iCloud address, exact city | 5 min (user input needed) |
| U-6.3 | Extract vehicle VINs from state/vehicle.md and add to profile | 10 min |
| U-6.4 | Run `python scripts/generate_identity.py` to rebuild Artha.md from new profile | 5 min |
| U-6.5 | Run `python scripts/generate_identity.py --with-routing` to generate routing.yaml | 5 min |
| U-6.6 | Validate profile against schema: `python -c "from scripts.profile_loader import *; validate_profile()"` | 2 min |

### Acceptance Criteria
- [ ] user_profile.yaml validates against user_profile.schema.json
- [ ] All family members present with correct ages and schools
- [ ] Location set to Pacific Time with King County property tax
- [ ] All integrations reflect actual enabled/disabled state
- [ ] `generate_identity.py` runs without errors

---

## U-7: Routing & Channels

### Routing — Current State

`config/routing.yaml` contains only `key: value` — a placeholder. The
routing engine has no rules, so **all emails are classified by AI content
analysis alone** with no sender/subject hints.

### Fix: Generate routing.yaml from Profile

Once user_profile.yaml is populated (U-6), run:

```bash
python scripts/generate_identity.py --with-routing
```

This generates `config/routing.yaml` with:

1. **System routes** (copied from routing.example.yaml):
   - Immigration: USCIS senders, visa keywords
   - Finance: Bank senders, bill keywords
   - Health: Provider senders, appointment keywords
   - Travel: Airline senders, booking keywords
   - Shopping: Amazon/Costco/USPS senders
   - Home: Mortgage, HOA, utility keywords
   - Vehicle: Registration, recall keywords
   - Estate: Will, trust, POA keywords
   - Kids: School senders, grade keywords
   - Suppress: Marketing/promotional patterns

2. **User routes** (generated from user_profile.yaml):
   - `school_domains: ["lwsd.org"]` — routes LWSD emails to kids domain
   - `finance_institutions: ["wellsfargo.com", "chase.com", "fidelity.com", "discover.com", "bankofamerica.com", "citi.com"]`
   - `immigration_attorney_domain: "fragomen.com"`
   - `health_provider_domains: ["premera.com", "providence.org"]`

### Channels — Current State

No `config/channels.yaml` exists. The channel bridge is disabled.

### Enable Telegram Channel

Telegram has already been verified as working (per health-check.md from
prior sessions). Steps:

1. Copy `config/channels.example.yaml` → `config/channels.yaml`
2. Configure:

```yaml
defaults:
  push_enabled: true
  redaction: full
  push_format: flash
  max_push_length: 500
  listener_host: ""

channels:
  telegram:
    enabled: true
    adapter: "scripts/channels/telegram.py"
    auth:
      method: keyring
      credential_key: "artha-telegram-bot-token"
    recipients:
      primary:
        id: ""             # FILL: Ved's Telegram chat_id
        name: "Ved"
        access_scope: full
        push: true
        interactive: true
      spouse:
        id: ""             # FILL: Archana's Telegram chat_id (optional)
        name: "Archana"
        access_scope: family
        push: true
        interactive: true
    features:
      push: true
      interactive: true
      documents: true
      buttons: true
    health_check: true
```

3. Run setup wizard: `python scripts/setup_channel.py --channel telegram`
4. Test: `python scripts/channel_push.py --dry-run`
5. Start listener: `python scripts/channel_listener.py --channel telegram`

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-7.1 | Populate user_profile.yaml first (U-6) | Dependency |
| U-7.2 | Run `generate_identity.py --with-routing` to create routing.yaml | 5 min |
| U-7.3 | Verify routing.yaml has all domain routes with user-specific senders | 5 min |
| U-7.4 | Copy channels.example.yaml → channels.yaml | 2 min |
| U-7.5 | Run `python scripts/setup_channel.py --channel telegram` | 5 min |
| U-7.6 | Test push: `python scripts/channel_push.py --dry-run` | 2 min |
| U-7.7 | Enable interactive listener (Layer 2) | 5 min |

### Acceptance Criteria
- [ ] routing.yaml has system_routes + user_routes with school/finance/immigration domains
- [ ] channels.yaml exists with Telegram enabled
- [ ] Post-catch-up push delivers flash briefing to Telegram
- [ ] `/help` command works in Telegram listener

---

## U-8: Fix memory.md — Cross-Session Learning

### Root Cause Analysis

`state/memory.md` has `facts: []` despite 5+ daily catch-up sessions.

**Why it's empty:**

1. **fact_extractor.py exists and works** — it reads session histories from
   `tmp/session_history_*.md`, extracts durable facts, and writes them to
   `state/memory.md`.

2. **But it never runs in production.** The fact extraction step is defined
   in `config/Artha.core.md` as Step 11c (post-session), but it requires:
   - A session history file to exist in `tmp/`
   - The catch-up workflow to invoke the extractor after the session

3. **The gap:** Session histories are generated during catch-up, but the
   fact extraction step is not automatically triggered. It would need to be
   invoked manually:
   ```bash
   python -c "from scripts.fact_extractor import extract_facts; extract_facts()"
   ```
   Or added as Step 21 in the catch-up pipeline.

### Fix

Two approaches (choose one):

**Option A: Add to catch-up pipeline (recommended)**

Add fact extraction as Step 21 in `config/Artha.core.md`, after channel push
(Step 20) and before session cleanup:

```
Step 21 — Fact Extraction
   Run: python -c "from scripts.fact_extractor import extract_facts; extract_facts()"
   Input: tmp/session_history_*.md (current session)
   Output: Appends to state/memory.md facts[] YAML frontmatter
   Extracts: corrections, patterns, preferences, schedule facts, contact updates
```

**Option B: Run manually after each session**

After completing a catch-up, run:
```bash
python -c "from scripts.fact_extractor import extract_facts; extract_facts()"
```

### What Facts Should Be Captured

Based on fact_extractor.py's schema:

| Fact Type | Example | Source |
|-----------|---------|--------|
| `correction` | "Costco purchases are not anomalous — routine bulk shopping" | User corrects AI during catch-up |
| `pattern` | "Wed always has 2–3 school emails from LWSD" | Observed across 3+ sessions |
| `preference` | "Ved prefers flash briefing format on weekdays" | User behavior signal |
| `contact` | "Pradeep's new phone: +91 ..." | Contact update in session |
| `schedule` | "Parth has soccer practice Tuesdays and Thursdays" | Recurring calendar pattern |
| `threshold` | "Don't alert on Chase Freedom charges under $50" | User-set threshold |

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-8.1 | Add Step 21 (fact extraction) to Artha.core.md catch-up pipeline | 15 min |
| U-8.2 | Run fact_extractor.py manually on existing session histories in tmp/ | 5 min |
| U-8.3 | Verify facts[] in memory.md frontmatter is non-empty after extraction | 2 min |
| U-8.4 | Confirm subsequent catch-ups auto-extract and append facts | Next catch-up |

### Acceptance Criteria
- [ ] `state/memory.md` `facts:` array contains ≥1 entry after next catch-up
- [ ] Fact extraction runs automatically as part of the catch-up pipeline
- [ ] Corrections made during catch-ups are captured as `type: correction` facts

---

## U-9: New Skills

### Currently Enabled Skills (8)

| Skill | Cadence | Domain |
|-------|---------|--------|
| `uscis_status` | every_run | immigration |
| `visa_bulletin` | weekly | immigration |
| `passport_expiry` | every_run | immigration |
| `king_county_tax` / `property_tax` | daily | home/finance |
| `noaa_weather` | every_run | general |
| `nhtsa_recalls` | weekly | vehicle |
| `subscription_monitor` | every_run | digital |
| `financial_resilience` | weekly | finance |

### Proposed New Skills

#### High Priority — Immediate Value

| # | Skill | Domain | Cadence | Description |
|---|-------|--------|---------|-------------|
| 1 | **`relationship_pulse`** | social | every_run | Compute days-since-contact per circle (U-1), surface stale contacts, generate reach-out suggestions |
| 2 | **`occasion_tracker`** | social | every_run | 7-day lookahead for birthdays, anniversaries, festivals; generate greeting suggestions (U-2) |
| 3 | **`school_calendar`** | kids | daily | Track LWSD calendar events, breaks, half-days, PTC dates; alert when school events are within 3 days |
| 4 | **`credit_monitor`** | finance | daily | Parse credit monitoring alert emails (Equifax, TransUnion, CreditKarma), surface new inquiries, score changes, account openings |
| 5 | **`bill_due_tracker`** | finance | every_run | Extract bill due dates from state/finance.md and state/occasions.md, alert 7/3/1 days before due, flag overdue |

#### Medium Priority — Enhance with External Data

| # | Skill | Domain | Cadence | Description |
|---|-------|--------|---------|-------------|
| 6 | **`package_tracker`** | shopping | every_run | Extract USPS/FedEx/UPS tracking numbers from email, surface delivery status, alert on "out for delivery" |
| 7 | **`mortgage_tracker`** | home | monthly | Pull mortgage amortization progress from Wells Fargo emails/statements, track principal vs interest ratio, show payoff timeline |
| 8 | **`health_appointment`** | health | daily | Extract upcoming doctor/dentist/eye appointments from email confirmations, auto-propose calendar events |
| 9 | **`canvas_grades`** | kids | daily | When Canvas LMS connector is enabled, pull latest grades/missing assignments for Parth and Trisha, surface GPA trends |
| 10 | **`flight_tracker`** | travel | on-demand | When travel emails detected, extract booking refs, check flight status via public APIs, alert on delays/cancellations |

#### Low Priority — Future Enhancements

| # | Skill | Domain | Cadence | Description |
|---|-------|--------|---------|-------------|
| 11 | **`gas_price`** | vehicle | weekly | Check local gas prices (GasBuddy API) — relevant for CX-5, less so for EV6 |
| 12 | **`ev_charging`** | vehicle | weekly | Track Kia EV6 charging patterns from Apple Health / vehicle telematics (if available) |
| 13 | **`library_holds`** | learning | daily | King County Library System (KCLS) hold notifications, due date tracking |
| 14 | **`meal_planner`** | home | weekly | Costco membership + past purchases → suggest weekly meal plans |
| 15 | **`investment_rebalance`** | finance | monthly | Fidelity/Vanguard portfolio allocation check, surface when out of target allocation band |

### Skill Implementation Pattern

All skills follow `BaseSkill` (in `scripts/skills/base_skill.py`):

```python
class RelationshipPulseSkill(BaseSkill):
    """Check relationship warmth across circles."""

    def pull(self) -> Dict[str, Any]:
        # Read state/contacts.md, compute days-since-last-contact
        ...

    def parse(self, raw: Dict) -> List[Dict]:
        # Return list of stale contacts with circle info
        ...

    def to_dict(self) -> Dict[str, Any]:
        # Return structured alert data for briefing
        ...
```

Register in `config/skills.yaml`:

```yaml
relationship_pulse:
  enabled: true
  priority: P1
  cadence: every_run
  requires_vault: true
  description: "Check contact freshness across circles, nudge stale relationships"
```

### Implementation Steps

| Step | Action | Effort |
|------|--------|--------|
| U-9.1 | Implement `relationship_pulse.py` skill (U-1 dependency) | 2 hr |
| U-9.2 | Implement `occasion_tracker.py` skill (U-2 dependency) | 2 hr |
| U-9.3 | Implement `bill_due_tracker.py` skill | 1.5 hr |
| U-9.4 | Implement `credit_monitor.py` skill | 1.5 hr |
| U-9.5 | Implement `school_calendar.py` skill | 1.5 hr |
| U-9.6 | Register new skills in config/skills.yaml | 10 min |
| U-9.7 | Add tests for each new skill | 2 hr |
| U-9.8 | Implement remaining P2/P3 skills as time permits | Ongoing |

### Acceptance Criteria
- [ ] ≥5 new skills implemented and registered
- [ ] All new skills follow BaseSkill pattern
- [ ] All new skills have unit tests
- [ ] skill_runner.py discovers and executes new skills correctly

---

## Execution Sequence

### Phase 1 — Foundation (Do First)

| Order | Workstream | Action | Time |
|-------|-----------|--------|------|
| 1 | **U-6** | Populate user_profile.yaml | 30 min |
| 2 | **U-5.1** | Fix Gmail MCP privacy flag | 2 min |
| 3 | **U-5.2** | Fix ALL MCP flags | 5 min |
| 4 | **U-7.2** | Generate routing.yaml from profile | 5 min |
| 5 | **U-8.1** | Add fact extraction to catch-up pipeline | 15 min |

### Phase 2 — Connector Enablement (~1 hour)

| Order | Workstream | Action |
|-------|-----------|--------|
| 6 | **U-5.3–5.4** | MS Graph OAuth → enable Outlook + OneNote |
| 7 | **U-5.5–5.6** | iCloud auth → enable iCloud email + calendar |
| 8 | **U-5.7–5.8** | Canvas LMS API keys → enable for Parth + Trisha |
| 9 | **U-5.9–5.10** | Apple Health export → enable wellness domain |
| 10 | **U-5.11** | Configure RSS feeds |
| 11 | **U-7.4–7.7** | Enable Telegram channel |

### Phase 3 — Domain Activation (~2 hours across multiple catch-ups)

| Order | Workstream | Action |
|-------|-----------|--------|
| 12 | **U-4.1–4.3** | Bootstrap: goals, home, employment |
| 13 | **U-4.5** | Run catch-up — self-populates comms, shopping, digital |
| 14 | **U-4.6** | Seed estate.md with current planning status |
| 15 | **U-2.1–2.3** | Enrich occasions.md with DOBs + festivals + holidays |
| 16 | **U-1.1–1.3** | Set up circles in contacts.md schema |

### Phase 4 — Intelligence Layer (~1 week)

| Order | Workstream | Action |
|-------|-----------|--------|
| 17 | **U-9.1–9.2** | Build relationship_pulse + occasion_tracker skills |
| 18 | **U-9.3–9.5** | Build bill_due_tracker + credit_monitor + school_calendar skills |
| 19 | **U-1.4–1.5** | Add circle-aware briefing sections |
| 20 | **U-3.4–3.6** | Build calendar event creation handler |

---

## Utilization Scorecard

Track progress with this scorecard (update weekly):

| Dimension | Baseline | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Target |
|-----------|----------|---------|---------|---------|---------|--------|
| Domains with data | 10/25 | 10/25 | 12/25 | 20/25 | 25/25 | 25/25 |
| Connectors enabled | 2/10 | 2/10 | 8/10 | 8/10 | 8/10 | 8/10 |
| user_profile fields | ~5% | 90% | 95% | 95% | 100% | 100% |
| routing.yaml | placeholder | full | full | full | full | full |
| channels | 0 | 0 | 1 (Telegram) | 1 | 1 | 1+ |
| skills | 8 | 8 | 8 | 8 | 13+ | 13+ |
| memory.md facts | 0 | 0 | 1+ | 5+ | 20+ | growing |
| Occasions tracked | 4 birthday | 4 | 4 | 15+ birthday + festivals | 15+ | 15+ |
| Contact circles | 0 | 0 | 0 | 6+ | 6+ active | 6+ |
| **Overall Score** | **30%** | **45%** | **65%** | **85%** | **100%** | **100%** |

---

## Dependency Map

```
U-6 (profile)
 ├──→ U-7 (routing — needs profile for user_routes)
 ├──→ U-5.3 (MS Graph — needs outlook account from profile)
 └──→ U-4 (domains — profile drives domain enablement)

U-5 (connectors)
 ├──→ U-4 (domains self-populate when connectors fetch data)
 └──→ U-9 (skills — some need connector data: canvas_grades, health)

U-1 (contacts/circles)
 ├──→ U-2 (occasions cross-references circles for nudges)
 └──→ U-9.1 (relationship_pulse skill reads circle data)

U-8 (memory.md)
 └──→ All future catch-ups benefit from fact persistence
```

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| MS Graph OAuth fails | Outlook/OneNote/calendar disabled | Fall back to Gmail-only; retry OAuth setup |
| Canvas LMS API not available for LWSD | Kids grades not trackable | Use email-based grade notifications as fallback |
| iCloud app password flow changes | iCloud email/calendar disabled | Monitor Apple developer docs; use forwarding rules |
| Apple Health export too large | Health data parsing slow | Use `default_lookback: 90d` to limit scope |
| Gmail MCP accidentally re-enabled | Privacy regression | Add preflight check: `assert gmail.mcp.prefer_mcp == false` |
| Fact extractor produces low-quality facts | Memory pollution | Confidence threshold: only store facts with confidence ≥ 0.7 |
