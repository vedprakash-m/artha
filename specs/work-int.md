# Work-IQ Integration Spec — Artha Feature Specification
<!-- specs/work-int.md | v1.0 | authored: 2026-03-12 -->

---

## 1. Problem Statement

Artha currently fetches calendar data from three **personal** sources (Google Calendar, Outlook personal, iCloud). It has zero visibility into Ved's **Microsoft corporate** calendar (Teams meetings, 1:1s, standups, org-wide events). This creates blind spots:

- **Cross-domain conflicts invisible**: School pickup overlaps with a Teams sync → Artha can't detect it.
- **Employment domain dormant**: `state/employment.md` exists with static data (role, comp, manager) but never receives live signals about meeting load, schedule density, or work patterns.
- **Briefing is incomplete**: The "📅 TODAY" section shows personal events only. A typical Monday has 13 work meetings that Artha doesn't see.

### Constraint

Ved uses Artha from **two machines**:

| Machine | OS | WorkIQ access | Personal sources |
|---------|-----|---------------|-----------------|
| **Work laptop** | Windows | ✅ Available — M365 Copilot license via Microsoft corp tenant | ✅ All (Gmail, Outlook personal, iCloud, Google Cal) |
| **Personal MacBook** | macOS | ❌ Not available — no `npx @microsoft/workiq`, no corp auth | ✅ All |

**The integration MUST NOT regress Mac catch-ups.** When WorkIQ is unavailable, Artha must produce the same quality briefing it does today, with a one-line footer note about missing work calendar data.

---

## 2. Design Principles

1. **Graceful degradation over hard dependency** — WorkIQ is additive, never blocking.
2. **Calendar only, no email** — Work emails are high-volume noise (100+ daily). Artha fetches work **calendar** data only. No work email processing.
3. **Ephemeral, not persisted** — Work meeting data is used in the current briefing session only. It is NOT written to `state/` files (avoids syncing corporate data to OneDrive personal, avoids PII in state).
4. **Separate cache, separate state** — Work calendar snapshot lives in `tmp/` (ephemeral) and `state/work-calendar.md` (minimal metadata only — no meeting bodies, no attendee details).
5. **Platform detection at runtime** — Artha detects WorkIQ availability at preflight, not via static config flag.

---

## 3. Architecture

### 3.1 Data Flow

```
                    ┌─────────────────────────────────────┐
                    │        Artha Catch-Up Step 4         │
                    │    (Parallel Fetch — all sources)     │
                    └───┬───┬───┬───┬───┬───┬─────────────┘
                        │   │   │   │   │   │
                   Gmail │  Outlook│ iCloud│  Google  Outlook  iCloud
                   Mail  │  Mail  │  Mail │  Cal     Cal      Cal
                        │   │   │   │   │   │
                        │   │   │   │   │   │    ┌──────────────┐
                        │   │   │   │   │   │    │  WorkIQ MCP  │
                        │   │   │   │   │   │    │  (calendar   │
                        │   │   │   │   │   │    │   query)     │
                        │   │   │   │   │   │    └──────┬───────┘
                        │   │   │   │   │   │           │
                        ▼   ▼   ▼   ▼   ▼   ▼           ▼
                    ┌─────────────────────────────────────────┐
                    │          Calendar Merge + Dedup          │
                    │  (personal sources + work calendar)      │
                    │  Dedup: same title ± 5min = keep one    │
                    │  Tag: source="work_calendar"             │
                    └─────────────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────────┐
                    │     Cross-Domain Reasoning (Step 8)      │
                    │  • Work meeting + school event conflict  │
                    │  • Meeting density → "busy day" flag     │
                    │  • Late meeting + morning kid duty       │
                    └─────────────────────────────────────────┘
                                      │
                                      ▼
                              Briefing Output
```

### 3.2 File Layout

| File | Purpose | Persisted? | Synced via OneDrive? | Sensitive? |
|------|---------|-----------|---------------------|-----------|
| `tmp/work_calendar.json` | Raw WorkIQ calendar response (this session only) | ❌ Ephemeral — deleted at Step 18 | No (tmp/ is gitignored) | Yes — meeting titles contain work context |
| `state/work-calendar.md` | Minimal metadata: last fetch time, meeting count, conflict count, density score | ✅ Persisted | Yes | No — no meeting titles, no attendees, no bodies |
| `state/employment.md` | Existing file — updated with schedule density metrics only | ✅ Persisted | Yes (encrypted) | Existing sensitivity level unchanged |

### 3.3 What Gets Persisted vs. Ephemeral

| Data | Persisted to state? | Rationale |
|------|-------------------|-----------|
| Meeting titles | ❌ NO | Corporate IP / confidentiality |
| Attendee names | ❌ NO | PII — no business need to persist |
| Meeting times (today/this week) | ❌ NO — ephemeral in briefing only | Changes constantly; no value in persisting |
| Meeting count per day | ✅ YES — `work-calendar.md` | Safe metadata; powers density analysis |
| Conflict count | ✅ YES — `work-calendar.md` | Safe metadata; powers conflict detection trends |
| Last WorkIQ fetch timestamp | ✅ YES — `health-check.md` | Operational tracking |
| WorkIQ availability (bool) | ✅ YES — `health-check.md` | Platform detection tracking |

---

## 4. Detailed Specification

### 4.1 Preflight Enhancement (Step 0)

Add a **P1 (non-blocking)** check to `preflight.py`:

```python
# WorkIQ availability check
def check_workiq():
    """Detect if WorkIQ MCP is available on this machine."""
    try:
        result = subprocess.run(
            ["npx", "-y", "@microsoft/workiq@latest", "version"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return {"available": True, "version": result.stdout.strip()}
        else:
            return {"available": False, "reason": "workiq exited non-zero"}
    except FileNotFoundError:
        return {"available": False, "reason": "npx not found"}
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "timeout"}
```

**Preflight output:**
- If available: `✓ [P1] WorkIQ MCP: Available (v1.x.x) — work calendar will be fetched ✓`
- If unavailable: `⚠ [P1] WorkIQ MCP: Not available ([reason]) — work calendar skipped`

**Session flag:** Set `workiq_available = true|false` for use in Step 4.

### 4.2 Fetch Enhancement (Step 4)

Add **WorkIQ Calendar Fetch** as a 7th parallel source. Runs only if `workiq_available == true`.

**Invocation:**

```bash
npx -y @microsoft/workiq@latest ask \
  -q "List all my calendar events for this week (Monday through Sunday). Include: event title, date, start time, end time, organizer, and whether it's a Teams meeting. Format as a structured list grouped by day."
```

**Alternative — targeted today + tomorrow only (lower token cost):**

```bash
npx -y @microsoft/workiq@latest ask \
  -q "List all my calendar events for today and tomorrow. Include: event title, start time, end time, organizer, location, and whether it's a Teams meeting."
```

**Output handling:**
1. Parse WorkIQ response (markdown text) into structured event list
2. Save raw response to `tmp/work_calendar.json`
3. Normalize each event to the standard Artha calendar schema:
   ```json
   {
     "id": "workiq-<hash>",
     "calendar": "Microsoft Work",
     "summary": "<event title>",
     "start": "<ISO-8601>",
     "end": "<ISO-8601>",
     "all_day": false,
     "location": "<location or Teams link>",
     "organizer": "<organizer name>",
     "attendees": [],
     "status": "confirmed",
     "source": "work_calendar",
     "is_teams_meeting": true|false
   }
   ```

**Error handling:**
- WorkIQ auth failure → log to `audit.md`, set `workiq_available = false` for session, continue. Briefing footer: `"⚠️ Work calendar unavailable — WorkIQ auth failed (Windows only)"`
- WorkIQ timeout (>30s) → skip, same footer note
- WorkIQ returns empty → log, continue (may be a light day)
- **On Mac (npx/@microsoft/workiq not found)** → footer: `"ℹ️ Work calendar: available on Windows laptop only"`

### 4.3 Calendar Deduplication Enhancement

Update existing dedup rule (§2 Step 4) to include work calendar:

```
After merging all FOUR calendar feeds (Google, Outlook personal, iCloud, WorkIQ work):
  - If two events match on (summary ± minor variation) AND (start time ± 5 minutes):
    keep one record, set "source": "both" (or "work+personal")
  - Events tagged source="work_calendar" that have NO personal calendar match:
    tag as work-only (display with 💼 prefix in briefing)
  - Personal events with NO work match: tag as personal-only (no prefix)
```

**Common dedup scenarios:**
- "School Pickup" appears on both personal Google Cal and work Outlook → dedup, keep personal
- "Tulsidevi Bhakti Gita" on personal Outlook only → personal-only
- "XPF Weekly" on work only → work-only (💼)
- "Parth SAT Exam" on iCloud only → personal-only

### 4.4 Cross-Domain Reasoning Enhancement (Step 8)

Add two new compound signal rules to Step 8f:

```
Rule 7: Work meeting + personal event overlap
  IF work_calendar event overlaps (±15 min) with personal calendar event:
    Surface: "⚠️ CONFLICT: [work event] overlaps [personal event] at [time]"
    Score: Urgency based on proximity, Impact=2 (scheduling), Agency=3 (can reschedule)
    
Rule 8: Meeting density warning
  IF work_calendar events for today > 8:
    Surface: "📊 Heavy meeting day: [N] work meetings. Focus time: [gaps if any]"
    Identify largest gap ≥30 min and surface as "🟢 Best focus window: [start]–[end]"
```

### 4.5 Briefing Output Enhancement (Step 11)

**📅 TODAY section** — merge work + personal:

```
━━ 📅 TODAY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8:00am   💼 Enter Top5 Weekly (Shashidhar Joshi) [Teams]
9:05am   💼 ⚠️ OVERLAP: Admin Office Hours + LT Review for XPF
11:00am  💼 xInfraSWPM: XPF Weekly (you organize) [Teams]
1:00pm   💼 XPF Burn-In Discussion [Teams]
3:35pm   ⚠️ CONFLICT: 💼 DM & CPE Repair Sync ↔ 🏠 School Pickup
6:00pm   🏋️ Cycling & exercise
                                          
📊 Today: 13 meetings | Focus window: 11:30am–1:00pm
```

When WorkIQ is unavailable:
```
━━ 📅 TODAY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(personal events only — no work calendar changes)
ℹ️ Work calendar: run from Windows laptop for full view
```

**Footer enhancement:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[N] emails → [N] actionable · 💼 [N] work meetings · signal:noise [N]:[N]
🔒 PII: [N] scanned · [N] redacted · [N] patterns
[ℹ️ Work calendar: Windows-only | not available this session]  ← only when unavailable
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 4.6 State File: `state/work-calendar.md`

New file — minimal metadata only, no corporate content:

```yaml
---
domain: work-calendar
last_updated: "2026-03-12T05:00:00-07:00"
last_activity: "2026-03-12T05:00:00-07:00"
sensitivity: standard
encrypted: false
schema_version: "1.0"
---
# Work Calendar Metadata

## Last Fetch
```yaml
last_fetch: "2026-03-12T05:00:00-07:00"
platform: windows
workiq_version: "1.2.0"
events_returned: 13
fetch_duration_seconds: 8
```

## Weekly Density (rolling)
```yaml
# Updated each catch-up when WorkIQ is available.
# Counts only — no titles, no attendees, no bodies.
density:
  - week_of: "2026-03-09"
    mon: 13
    tue: 8
    wed: 6
    thu: 9
    fri: 5
    total: 41
    conflicts_detected: 2
    avg_daily: 8.2
```

## Conflict History
```yaml
# Count-only log. No event details persisted.
conflicts:
  - date: "2026-03-10"
    count: 2
    resolved: true
  - date: "2026-03-09"
    count: 1
    resolved: true
```
```

### 4.7 Health-Check Enhancement (Step 16)

Add to `catch_up_runs` entry:

```yaml
workiq:
  available: true|false
  platform: windows|mac|linux
  events_fetched: N          # 0 if unavailable
  conflicts_detected: N
  fetch_duration_seconds: N
  error: ""                  # empty if success; error message if failed
```

### 4.8 Employment Domain Activation

When WorkIQ data is available, update `state/employment.md` with **metadata only**:

```yaml
## Schedule Metrics (auto-updated by WorkIQ integration)
schedule_metrics:
  last_updated: "2026-03-12"
  avg_meetings_per_day: 8.2
  busiest_day: monday
  typical_focus_windows: ["11:30-13:00", "15:00-15:30"]
  meetings_organized_by_ved: 3    # count of meetings where Ved is organizer
  recurring_vs_adhoc_ratio: "80:20"
```

This activates the employment domain from ⚪ grey to 🟢 green on the dashboard.

---

## 5. Platform Detection Logic

```python
import platform, shutil, subprocess

def detect_workiq_availability():
    """
    Runtime detection — called at preflight.
    Returns dict with availability status and reason.
    """
    os_name = platform.system()  # "Windows", "Darwin", "Linux"
    
    # Check 1: Is npx available?
    if not shutil.which("npx"):
        return {
            "available": False,
            "reason": "npx not found",
            "platform": os_name,
            "suggestion": "Install Node.js to enable WorkIQ" if os_name == "Windows" else None
        }
    
    # Check 2: Can WorkIQ binary run?
    try:
        result = subprocess.run(
            ["npx", "-y", "@microsoft/workiq@latest", "version"],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode == 0:
            return {
                "available": True,
                "version": result.stdout.strip(),
                "platform": os_name
            }
        else:
            return {
                "available": False,
                "reason": f"workiq exit code {result.returncode}",
                "platform": os_name
            }
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "timeout (>20s)", "platform": os_name}
    except Exception as e:
        return {"available": False, "reason": str(e), "platform": os_name}
```

---

## 6. Privacy & Compliance Considerations

### 6.1 What enters Claude API context

| Data | Enters Claude context? | Mitigation |
|------|----------------------|-----------|
| Work meeting titles | ✅ Yes (during briefing generation) | Ephemeral — not persisted. Same as personal email bodies. |
| Work meeting attendee names | ✅ Yes (during conflict detection) | Not persisted to state files. |
| Meeting bodies/agendas | ❌ NO — not requested from WorkIQ | Query explicitly asks for titles + times only. |
| Teams chat content | ❌ NO — not requested | Out of scope. |
| Work emails | ❌ NO — not requested | Out of scope. Explicitly excluded. |

### 6.2 Corporate policy check

**Action required before implementation:** Ved should verify with Microsoft IT/compliance that using WorkIQ output in a personal AI assistant context is permitted. Key questions:

1. Is routing WorkIQ calendar data through Claude API (Anthropic) acceptable under Microsoft data handling policy?
2. Does the M365 Copilot license terms permit third-party consumption of calendar metadata?
3. Is local-only processing (no persistence) sufficient, or is additional DLP required?

**Recommendation:** If corporate policy restricts external API transit of calendar data, implement a **local-only mode** where WorkIQ output is displayed directly in the terminal without being processed by Claude. This reduces the integration to a "sidecar" display alongside the Artha briefing.

### 6.3 `/privacy` command update

Add WorkIQ to the privacy surface disclosure:

```
WorkIQ (work calendar):
  Data fetched: Meeting titles, times, organizers (calendar only)
  Sent to Claude API: Yes (ephemeral, during briefing generation)
  Persisted to state: Counts only (no titles, no attendees)
  Available on: Windows laptop only (corp M365 Copilot license)
```

---

## 7. Graceful Degradation Matrix

| Scenario | Behavior | Briefing impact |
|----------|----------|----------------|
| **Windows + WorkIQ working** | Full integration: work calendar merged, conflicts detected, density shown | Complete briefing with 💼 work events |
| **Windows + WorkIQ auth expired** | Skip work calendar, log error | Footer: "⚠️ WorkIQ auth expired — run `workiq logout` then retry" |
| **Windows + WorkIQ timeout** | Skip work calendar, log error | Footer: "⚠️ Work calendar timeout — skipped this session" |
| **Mac (WorkIQ unavailable)** | Skip entirely, no error | Footer: "ℹ️ Work calendar: available on Windows laptop only" |
| **Mac (npx not installed)** | Skip entirely, no error | No footer note (silent — npx absence is expected) |
| **Any platform + WorkIQ returns empty** | Log, continue | No work events shown; no error |

**Critical rule:** In ALL degradation scenarios, the personal calendar, email processing, domain routing, and every other Artha feature works identically to current behavior. WorkIQ failure NEVER blocks or alters non-WorkIQ functionality.

---

## 8. Implementation Plan

### Phase 1 — Calendar Read-Only (MVP)

| Step | Task | Files changed |
|------|------|---------------|
| 1 | Add WorkIQ detection to `scripts/preflight.py` | `scripts/preflight.py` |
| 2 | Create `state/work-calendar.md` with schema | `state/work-calendar.md` (new) |
| 3 | Update `config/Artha.md` Step 4 to include WorkIQ as 7th source | `config/Artha.md` |
| 4 | Update `config/Artha.md` Step 8f with Rules 7–8 (conflict + density) | `config/Artha.md` |
| 5 | Update `config/Artha.md` Step 11 briefing format for merged calendar | `config/Artha.md` |
| 6 | Update `config/Artha.md` Step 16 health-check schema | `config/Artha.md` |
| 7 | Update `state/health-check.md` schema with workiq fields | `state/health-check.md` |
| 8 | Test on Windows (full flow) | — |
| 9 | Test on Mac (graceful degradation) | — |

### Phase 2 — Schedule Intelligence (Future)

- Weekly meeting density trends in `employment.md`
- "Busiest day" detection → briefing suggests lighter scheduling
- Focus time gap analysis → auto-suggest calendar blocks
- Meeting-to-meeting transit time warnings (if location data available)

### Phase 3 — Work-Personal Balance (Future)

- After-hours meeting detection → "You have 2 meetings after 5pm today"
- Weekly work-life balance score in `/scorecard` Health dimension
- PTO/vacation detection → suppress work calendar on PTO days

---

## 9. Testing Checklist

### Windows (WorkIQ available)

- [ ] Preflight detects WorkIQ and reports version
- [ ] Step 4 fetches work calendar in parallel with personal sources
- [ ] Calendar dedup correctly merges "School Pickup" from both work and personal
- [ ] Conflicts between work and personal events surface in briefing
- [ ] Meeting density shown in footer
- [ ] `tmp/work_calendar.json` created and deleted at Step 18
- [ ] `state/work-calendar.md` updated with counts only (no titles)
- [ ] `state/employment.md` schedule_metrics updated
- [ ] `health-check.md` workiq section populated

### Mac (WorkIQ unavailable)

- [ ] Preflight reports WorkIQ unavailable (P1 warning, non-blocking)
- [ ] Step 4 runs all 6 personal sources identically to today
- [ ] No WorkIQ-related errors in audit.md
- [ ] Briefing footer shows "ℹ️ Work calendar: available on Windows laptop only" (or silent)
- [ ] All other Artha features unchanged
- [ ] `state/work-calendar.md` not updated (stale data preserved from last Windows session)
- [ ] No regression in any existing test

### Edge Cases

- [ ] WorkIQ returns 0 events (weekend or PTO) → no error, no work section
- [ ] WorkIQ auth token expired mid-session → graceful skip
- [ ] WorkIQ returns >50 events (conference day) → cap at 25, show count
- [ ] Personal and work calendar both have same event → dedup keeps one
- [ ] Network timeout on WorkIQ (>30s) → skip, log, continue

---

## 10. Decision Log

| Decision | Rationale | Alternatives considered |
|----------|-----------|----------------------|
| Calendar only, no email | Work emails are 100+/day; would overwhelm context and introduce corporate IP risk | Full email integration (rejected: noise + compliance) |
| Ephemeral meeting data | Corporate content shouldn't persist in personal OneDrive-synced state files | Full persistence (rejected: compliance risk) |
| Count-only state file | Enables trend analysis without storing corporate content | No state file (rejected: loses density trends) |
| Runtime platform detection | Avoids config drift between Mac and Windows | Static config flag (rejected: user must remember to toggle) |
| WorkIQ CLI (not MCP server) | Simpler integration; MCP requires persistent server process | MCP server mode (considered for Phase 2 if needed) |

---

*End of spec. Authored by Artha · March 12, 2026.*
