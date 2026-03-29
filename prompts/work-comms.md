---
schema_version: "1.0"
domain: work-comms
priority: P3
sensitivity: elevated
last_updated: 2026-03-15T00:00:00
---
# Work Communications Domain Prompt

## Purpose
Triage work email and Teams messages. Surface only items requiring user
action — filter out noise, FYI threads, and automated notifications.

## Data Source
Primary: WorkIQ email + Teams queries (via workiq_bridge connector, modes: email + teams)

## Extraction Rules
For each work communication:
1. **Channel** — email or Teams DM or Teams channel
2. **Sender** — display name; use work-people for role context if available
3. **Subject / Preview** — subject line (email) or first ~120 chars (Teams)
4. **Action Required** — yes/no (WorkIQ classifies; verify with heuristics below)
5. **Priority** — derived from: sender seniority + message age + content signals
6. **Age** — hours since received/sent
7. **Deep Link** — Outlook or Teams link for direct access (if provided by WorkIQ)

## Action-Required Heuristics
Classify as action-required if ANY of:
- WorkIQ `needs_response` or `needs_action` = yes
- Subject contains: "please review", "action required", "needs your input",
  "feedback requested", "approval needed", "sign-off", "blocking on you"
- Teams DM (all DMs have implicit action potential unless FYI-flagged)
- Sender is manager or skip-level and thread is unacknowledged >12h

## Thread Closure Validation (Two-Pass Protocol)

**MANDATORY:** After identifying action-required threads, run a verification pass
before surfacing them in the briefing. Do NOT skip this step.

**Pass 1 — Identify:** Extract all threads classified as action-required (above heuristics).

**Pass 2 — Validate:** For EACH action-required thread, ask WorkIQ:
  *"Did I (Ved) reply to or send a follow-up message in this thread after [timestamp of the inbound message]?"*
  Or equivalently check: *"What is the latest message in this thread and who sent it?"*

**Pass 2b — Ownership Check:** For EACH remaining OPEN thread, verify the action is actually directed at YOU:
  *"In this thread, who specifically is being asked to take action? Is the ask directed at Ved, or at someone else?"*
  If the ask is directed at someone else (e.g., "Altaf, can you provide scenarios?"), reclassify as:
  `👀 [WATCHING] [sender]: "[subject]" — action on [owner], you are cc'd`
  Do NOT surface other people's action items as YOUR action items just because you're in the thread.

**Classification after validation:**
- ✅ **RESOLVED** — You already replied. Show in briefing as:
  `✅ [RESOLVED] [sender]: "[subject]" — you replied [age] ago. Latest: [your reply summary]`
- ⏳ **OPEN** — No reply from you. Show in briefing as:
  `⏳ [OPEN] [sender]: "[subject]" — awaiting your response ([age]h) [action needed]`
- 🔄 **AWAITING OTHERS** — You replied, now waiting on them. Show as:
  `🔄 [PENDING] [sender]: "[subject]" — you replied, awaiting their response`

**Briefing display rules:**
- OPEN items appear in the 🔴/🟡 action sections (as before)
- RESOLVED items appear in a **separate "Threads Resolved" section** at the bottom — provides transparency without false urgency
- AWAITING OTHERS items appear in the "Pending" section
- Always show the count: "5 threads surfaced: 2 open, 2 resolved, 1 awaiting others"

**Why this matters:** Without validation, every inbound ask appears "open" even if the user
already responded. This creates false urgency and erodes trust in the briefing.

## Pre-Filter (Suppress from Briefing)
- Senders matching: no-reply@, noreply@, donotreply@, mailer-daemon@
- Subject matching calendar RSVP patterns: "Accepted:", "Declined:", "Tentative:"
- Automated build/release/pipeline notifications
- Distribution list broadcasts with no question or ask
- Newsletter announcements, wellness/community email

## Alert Thresholds
🔴 CRITICAL: VP+ email awaiting response >24h; escalation thread from manager
🟠 URGENT: Manager email unanswered >12h; cross-team request >24h old
🟡 STANDARD: 5+ unacknowledged action threads; Teams DM with action request
🔵 LOW: FYI threads (suppressed by default — count only); automated notifications

## State File Update Protocol
Read `state/work/work-comms.md` first. Then update:
1. **Action Required** — rows sorted by priority desc, then age desc
2. **Pending** — threads where user has replied and awaits others' response
3. **Summary** — counts: new email / new Teams / action items / pending

## PII Redaction
- Apply `integrations.workiq.redact_keywords` to all subject lines
- NEVER write email body or Teams message body to state file
- OK to store: sender name, subject (80-char max), action classification, age
- Truncate subjects to 80 characters

## Briefing Format
```
### Work Comms
• X threads surfaced: Y open, Z resolved, W awaiting others

⏳ OPEN (action needed):
• 🟠 [sender]: "[subject]" — awaiting your response ([age]h)
• 🟡 [sender]: "[subject]" — [action needed] ([age]h)

🔄 AWAITING OTHERS:
• [sender]: "[subject]" — you replied [age] ago, pending their response

✅ RESOLVED (no action needed):
• [sender]: "[subject]" — you replied [age] ago. Latest: [summary]

🔵 [count] FYI threads (filtered)
```
