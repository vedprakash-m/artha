# Work-IQ Integration Spec — Artha Feature Specification
<!-- specs/work-int.md | v1.1 | authored: 2026-03-12 | revised: 2026-03-12 -->

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
6. **Local redaction before LLM transit** — Sensitive project codenames and keywords are redacted *locally* before meeting titles enter the Claude API context. A configurable redaction list in `config/settings.md` ensures corporate IP never leaves the machine in identifiable form.

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

Add **two** checks to `preflight.py`:

#### 4.1.1 WorkIQ Availability (P1, non-blocking)

**Optimized detection:** Instead of running `npx -y @microsoft/workiq@latest version` every session (slow, requires network), use a two-tier check:

```python
import os, json, platform, shutil, subprocess
from pathlib import Path

WORKIQ_CACHE = Path("state/.workiq_cache.json")  # gitignored
CACHE_TTL_HOURS = 24

def check_workiq():
    """Detect WorkIQ availability with cached fast path."""
    os_name = platform.system()
    
    # Fast path: check cache first (avoids npx network call)
    if WORKIQ_CACHE.exists():
        try:
            cache = json.loads(WORKIQ_CACHE.read_text())
            age_hours = (time.time() - cache.get("checked_at", 0)) / 3600
            if age_hours < CACHE_TTL_HOURS:
                return {**cache, "platform": os_name, "from_cache": True}
        except (json.JSONDecodeError, KeyError):
            pass  # stale/corrupt cache — fall through to live check
    
    # Slow path: live detection (only runs once per 24h or on cache miss)
    if not shutil.which("npx"):
        result = {"available": False, "reason": "npx not found"}
    else:
        try:
            proc = subprocess.run(
                ["npx", "-y", "@microsoft/workiq@latest", "version"],
                capture_output=True, text=True, timeout=20
            )
            if proc.returncode == 0:
                result = {"available": True, "version": proc.stdout.strip()}
            else:
                result = {"available": False, "reason": f"exit code {proc.returncode}"}
        except subprocess.TimeoutExpired:
            result = {"available": False, "reason": "timeout (>20s)"}
        except Exception as e:
            result = {"available": False, "reason": str(e)}
    
    # Cache result for fast path on next run
    result["checked_at"] = time.time()
    WORKIQ_CACHE.write_text(json.dumps(result))
    
    return {**result, "platform": os_name, "from_cache": False}
```

**Preflight output:**
- If available: `✓ [P1] WorkIQ MCP: Available (v1.x.x) — work calendar will be fetched ✓`
- If unavailable: `⚠ [P1] WorkIQ MCP: Not available ([reason]) — work calendar skipped`
- If from cache: append `(cached — re-check in [N]h)`

#### 4.1.2 M365 Auth Refresh (P1, non-blocking — Windows only)

WorkIQ requires periodic re-authentication. Detect auth failures early:

```python
def check_workiq_auth():
    """Verify WorkIQ auth is valid by running a minimal query."""
    if not workiq_available:
        return None  # skip on Mac / no WorkIQ
    try:
        result = subprocess.run(
            ["npx", "-y", "@microsoft/workiq@latest", "ask",
             "-q", "What is my name?"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0 or "error" in result.stderr.lower():
            return {
                "auth_valid": False,
                "error": result.stderr.strip() or "non-zero exit",
                "action": "Run: npx -y @microsoft/workiq@latest logout && retry"
            }
        return {"auth_valid": True}
    except Exception as e:
        return {"auth_valid": False, "error": str(e)}
```

**Preflight output on auth failure:**
```
🔴 [P1] WorkIQ Auth: EXPIRED — work calendar unavailable until re-auth
   → Run: npx -y @microsoft/workiq@latest logout && npx -y @microsoft/workiq@latest ask -q "test"
```

**Session flag:** Set `workiq_available = true|false` and `workiq_auth_valid = true|false` for use in Step 4.

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
3. **Apply local redaction** (see §4.2.1) before any data enters Claude context
4. Normalize each event to the standard Artha calendar schema:
   ```json
   {
     "id": "workiq-<hash>",
     "calendar": "Microsoft Work",
     "summary": "<event title — redacted if matched>",
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

#### 4.2.1 Local Redaction List (Corporate IP Protection)

**Problem:** Meeting titles like "Project [Confidential] Review" or "Azure Cobalt Roadmap" contain codenames that should not transit to the Anthropic API in identifiable form.

**Solution:** A configurable redaction list in `config/settings.md`:

```yaml
## WorkIQ Redaction
# Keywords/patterns replaced with [REDACTED] before meeting data enters Claude context.
# Case-insensitive. Supports simple glob patterns (* wildcard).
workiq_redaction:
  keywords:
    - "Project Cobalt"
    - "Project Silica"
    - "ITAR"
    - "FedRAMP*"
    - "Confidential"
  patterns:
    - "CVE-*"           # vulnerability identifiers
    - "MSRC-*"          # security response center cases
  replace_with: "[PROJECT-REDACTED]"
```

**Implementation:**

```python
import re

def redact_work_events(events: list, redaction_config: dict) -> list:
    """Apply local redaction to meeting titles before LLM transit."""
    keywords = [k.lower() for k in redaction_config.get("keywords", [])]
    patterns = redaction_config.get("patterns", [])
    replacement = redaction_config.get("replace_with", "[REDACTED]")
    
    # Compile glob patterns into regexes
    compiled = [re.compile(p.replace("*", ".*"), re.IGNORECASE) for p in patterns]
    
    for event in events:
        title = event.get("summary", "")
        for kw in keywords:
            if kw in title.lower():
                event["summary"] = replacement
                event["redacted"] = True
                break
        if not event.get("redacted"):
            for pat in compiled:
                if pat.search(title):
                    event["summary"] = replacement
                    event["redacted"] = True
                    break
    return events
```

**Redaction happens locally BEFORE** the data is included in any Claude API prompt. The raw (unredacted) data in `tmp/work_calendar.json` is deleted at Step 18.

**Briefing display of redacted events:**
```
9:00am  💼 [PROJECT-REDACTED] (60 min, Teams)
```

### 4.3 Calendar Deduplication Enhancement

Update existing dedup rule (§2 Step 4) to include work calendar with **field merging**:

```
After merging all FOUR calendar feeds (Google, Outlook personal, iCloud, WorkIQ work):
  - If two events match on (summary ± minor variation) AND (start time ± 5 minutes):
    MERGE fields from both sources (not simply discard):
      • summary:  prefer Personal version (more readable / user-created)
      • location: prefer Work version (has Teams link / room booking)
      • organizer: prefer Work version (has full org identity)
      • times:    prefer Work version (canonical / Exchange-authoritative)
      • set "source": "work+personal"
  - Events tagged source="work_calendar" that have NO personal calendar match:
    tag as work-only (display with 💼 prefix in briefing)
  - Personal events with NO work match: tag as personal-only (no prefix)
```

**Why merge, not discard:** Work calendar entries typically carry the Teams meeting link and room location, while personal copies have user-friendly titles (e.g., "School Pickup" vs. "Block: School Pickup"). Merging retains the best of both.

**Common dedup scenarios:**
- "School Pickup" (personal) + "Block: School Pickup" (work) → merged: title from personal, location from work
- "Tulsidevi Bhakti Gita" on personal Outlook only → personal-only
- "XPF Weekly" on work only → work-only (💼)
- "Parth SAT Exam" on iCloud only → personal-only

### 4.4 Cross-Domain Reasoning Enhancement (Step 8)

Add new compound signal rules to Step 8f with **conflict type distinction**:

```
Rule 7a: Cross-Domain Conflict (work ↔ personal) — HIGH IMPACT
  IF work_calendar event overlaps (±15 min) with personal calendar event:
    Surface: "🔴 CROSS-DOMAIN CONFLICT: [work event] ↔ [personal event] at [time]"
    Score: Urgency based on proximity, Impact=3 (cross-domain — requires lifestyle trade-off), Agency=3
    Reasoning: Cross-domain conflicts require harder trade-offs (can't just decline, 
    often involves family obligations). These carry higher U×I×A than internal work conflicts.

Rule 7b: Internal Work Conflict (work ↔ work) — LOWER IMPACT
  IF two work_calendar events overlap:
    Surface: "⚠️ WORK CONFLICT: [event A] overlaps [event B] at [time]"
    Score: Urgency based on proximity, Impact=1 (internal — resolvable via decline/delegate), Agency=3
    Reasoning: Back-to-back Teams calls are common and self-resolvable. Don't flood 
    the briefing with internal scheduling noise — surface them, but don't escalate.

Rule 8: Meeting density warning
  IF work_calendar events for today > 8:
    Surface: "📊 Heavy meeting day: [N] work meetings. Focus time: [gaps if any]"
    Identify largest gap ≥30 min and surface as "🟢 Best focus window: [start]–[end]"
```

**U×I×A scoring example:**
| Conflict type | Urgency | Impact | Agency | Score | Alert tier |
|---------------|---------|--------|--------|-------|------------|
| Work call ↔ School pickup | 3 | 3 | 3 | 27 | 🔴 Critical |
| Work call ↔ Kid's concert | 3 | 3 | 2 | 18 | 🟠 Action |
| Work call ↔ Work call | 2 | 1 | 3 | 6 | ℹ️ Info |

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

### 4.9 Meeting-Triggered Open Items (Employment Domain)

Certain work meeting types should auto-generate Open Items when detected:

```yaml
# config/settings.md — meeting_triggers section
meeting_triggers:
  critical:   # → 🔴 Critical alert tier, auto-create Employment OI
    - "Interview"
    - "Performance Review"
    - "Connect*"         # manager connects (e.g., "Connect with Ramjee")
    - "Calibration"
    - "Promotion*"
    - "PIP*"
  action:     # → 🟠 Action tier, suggest OI creation
    - "All-Hands"
    - "Reorg*"
    - "Town Hall"
    - "Skip Level"
```

**Behavior:**
- When a `critical` meeting is detected in today/tomorrow's work calendar:
  - Auto-create an Employment domain Open Item: `OI-AUTO-xxx: Prepare for [Meeting Title] on [Date]`
  - Surface in briefing with 🔴 tier: `"🔴 [Employment] Performance Review tomorrow at 2pm — prep needed"`
  - If a matching OI already exists (fuzzy match on title + date), skip creation
- When an `action` meeting is detected:
  - Surface in briefing with 🟠 tier
  - Suggest OI creation: `"Consider: prepare talking points for All-Hands Thursday"`
  - Do NOT auto-create (user must confirm)

**Deduplication with existing OIs:** Before creating, check `state/open_items.md` for any OI with the same meeting name and date within ±2 days. If found, skip and append a note to the existing OI instead.

---

## 5. Platform Detection Logic

```python
import platform, shutil, subprocess, json, time
from pathlib import Path

WORKIQ_CACHE = Path("state/.workiq_cache.json")  # gitignored
CACHE_TTL_HOURS = 24  # re-check every 24 hours

def detect_workiq_availability():
    """
    Runtime detection — called at preflight.
    Uses a cached fast path to avoid slow npx network calls every session.
    Returns dict with availability status and reason.
    """
    os_name = platform.system()  # "Windows", "Darwin", "Linux"
    
    # ── Fast path: use cache if fresh ──
    if WORKIQ_CACHE.exists():
        try:
            cache = json.loads(WORKIQ_CACHE.read_text())
            age_hours = (time.time() - cache.get("checked_at", 0)) / 3600
            if age_hours < CACHE_TTL_HOURS:
                return {**cache, "platform": os_name, "from_cache": True}
        except (json.JSONDecodeError, KeyError):
            pass  # corrupt cache — fall through
    
    # ── Slow path: live check (once per 24h) ──
    
    # Check 1: Is npx available?
    if not shutil.which("npx"):
        result = {
            "available": False,
            "reason": "npx not found",
            "suggestion": "Install Node.js to enable WorkIQ" if os_name == "Windows" else None
        }
    else:
        # Check 2: Can WorkIQ binary run?
        try:
            proc = subprocess.run(
                ["npx", "-y", "@microsoft/workiq@latest", "version"],
                capture_output=True, text=True, timeout=20
            )
            if proc.returncode == 0:
                result = {"available": True, "version": proc.stdout.strip()}
            else:
                result = {"available": False, "reason": f"workiq exit code {proc.returncode}"}
        except subprocess.TimeoutExpired:
            result = {"available": False, "reason": "timeout (>20s)"}
        except Exception as e:
            result = {"available": False, "reason": str(e)}
    
    # Cache for fast path on subsequent runs
    result["checked_at"] = time.time()
    result["platform"] = os_name
    try:
        WORKIQ_CACHE.write_text(json.dumps(result))
    except Exception:
        pass  # non-critical if cache write fails
    
    return {**result, "from_cache": False}
```

> **Performance note:** The cached approach avoids a 10–20 second `npx` network hit on every session start. Cache invalidates after 24 hours or on explicit `--force-recheck` flag. On Mac where WorkIQ is never available, the cache persists indefinitely (always returns "npx not found" instantly).

---

## 6. Privacy & Compliance Considerations

### 6.1 What enters Claude API context

| Data | Enters Claude context? | Mitigation |
|------|----------------------|-----------|
| Work meeting titles | ✅ Yes — **after local redaction** | Sensitive codenames replaced with `[PROJECT-REDACTED]` per `config/settings.md` redaction list before API transit. Ephemeral — not persisted. |
| Work meeting attendee names | ✅ Yes (during conflict detection) | Not persisted to state files. |
| Meeting bodies/agendas | ❌ NO — not requested from WorkIQ | Query explicitly asks for titles + times only. |
| Teams chat content | ❌ NO — not requested | Out of scope. |
| Work emails | ❌ NO — not requested | Out of scope. Explicitly excluded. |
| Redacted meeting titles | ✅ Yes (as `[PROJECT-REDACTED]`) | Original titles never leave machine. Only the redacted placeholder enters API context. |

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
  Local redaction: Sensitive project names replaced per config/settings.md redaction list
  Sent to Claude API: Yes (redacted + ephemeral, during briefing generation only)
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
| 1 | Add WorkIQ detection (cached) + auth check to `scripts/preflight.py` | `scripts/preflight.py` |
| 2 | Add `workiq_redaction` section to `config/settings.md` | `config/settings.md` |
| 3 | Create `state/work-calendar.md` with schema | `state/work-calendar.md` (new) |
| 4 | Add `state/.workiq_cache.json` to `.gitignore` | `.gitignore` |
| 5 | Update `config/Artha.md` Step 4 to include WorkIQ as 7th source + redaction | `config/Artha.md` |
| 6 | Update `config/Artha.md` Step 8f with Rules 7a/7b/8 (cross-domain + internal + density) | `config/Artha.md` |
| 7 | Update `config/Artha.md` Step 11 briefing format for merged calendar | `config/Artha.md` |
| 8 | Update `config/Artha.md` Step 16 health-check schema | `config/Artha.md` |
| 9 | Update `state/health-check.md` schema with workiq fields | `state/health-check.md` |
| 10 | Add `meeting_triggers` config for auto-OI creation | `config/settings.md` |
| 11 | Test on Windows (full flow — fetch, redact, merge, brief) | — |
| 12 | Test on Mac (graceful degradation — no errors, personal-only briefing) | — |

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

- [ ] Preflight detects WorkIQ and reports version (cached fast path after first run)
- [ ] Preflight auth check validates M365 token; surfaces P1 warning on expiry
- [ ] Step 4 fetches work calendar in parallel with personal sources
- [ ] Local redaction replaces configured keywords before Claude API transit
- [ ] Calendar dedup merges fields (title from personal, Teams link from work)
- [ ] Cross-domain conflicts (work ↔ personal) scored at Impact=3
- [ ] Internal work conflicts (work ↔ work) scored at Impact=1
- [ ] Meeting density shown in footer
- [ ] Critical meetings (Interview, Perf Review) auto-create Employment OIs
- [ ] `tmp/work_calendar.json` created and deleted at Step 18
- [ ] `state/work-calendar.md` updated with counts only (no titles)
- [ ] `state/.workiq_cache.json` persists for 24h fast path
- [ ] `state/employment.md` schedule_metrics updated
- [ ] `health-check.md` workiq section populated

### Mac (WorkIQ unavailable)

- [ ] Preflight reports WorkIQ unavailable (P1 warning, non-blocking)
- [ ] Cache returns "npx not found" instantly (no network call)
- [ ] Step 4 runs all 6 personal sources identically to today
- [ ] No WorkIQ-related errors in audit.md
- [ ] Briefing footer shows "ℹ️ Work calendar: available on Windows laptop only" (or silent)
- [ ] All other Artha features unchanged
- [ ] `state/work-calendar.md` not updated (stale data preserved from last Windows session)
- [ ] No regression in any existing test

### Edge Cases

- [ ] WorkIQ returns 0 events (weekend or PTO) → no error, no work section
- [ ] WorkIQ auth token expired mid-session → graceful skip, P1 warning with re-auth command
- [ ] WorkIQ returns >50 events (conference day) → cap at 25, show count
- [ ] Personal and work calendar both have same event → dedup merges fields (title from personal, link from work)
- [ ] Network timeout on WorkIQ (>30s) → skip, log, continue
- [ ] Redaction list is empty → no redaction applied, all titles pass through
- [ ] Meeting title matches multiple redaction keywords → single replacement (not double-redacted)
- [ ] Critical meeting (e.g., "Interview") already has matching OI → skip auto-creation, append note
- [ ] Cache file `.workiq_cache.json` is corrupt → fall through to live check gracefully

---

## 10. Decision Log

| Decision | Rationale | Alternatives considered |
|----------|-----------|----------------------|
| Calendar only, no email | Work emails are 100+/day; would overwhelm context and introduce corporate IP risk | Full email integration (rejected: noise + compliance) |
| Ephemeral meeting data | Corporate content shouldn't persist in personal OneDrive-synced state files | Full persistence (rejected: compliance risk) |
| Count-only state file | Enables trend analysis without storing corporate content | No state file (rejected: loses density trends) |
| Runtime platform detection | Avoids config drift between Mac and Windows | Static config flag (rejected: user must remember to toggle) |
| WorkIQ CLI (not MCP server) | Simpler integration; MCP requires persistent server process | MCP server mode (considered for Phase 2 if needed) |
| **Local redaction before API transit** | Meeting titles may contain confidential project names; redacting locally prevents corporate IP from reaching Anthropic API | No redaction (rejected: compliance risk), server-side redaction (rejected: data already left machine) |
| **Cached platform detection** | `npx -y @latest version` takes 10-20s and requires network; 24h cache avoids this overhead on every session start | Always run live check (rejected: slow), static flag (rejected: config drift) |
| **Field-merge dedup** | Work events carry Teams links/rooms while personal copies have friendly titles; merging retains best of both | Discard one (rejected: loses useful metadata) |
| **Cross-domain > internal conflicts** | Work↔personal conflicts require harder life trade-offs than back-to-back Teams calls; different Impact scores prevent alert fatigue | Uniform scoring (rejected: floods briefing with internal noise) |
| **Meeting-triggered OIs** | Critical work events (interviews, perf reviews) need advance prep; auto-OI creation ensures they aren't missed in Employment domain | Manual-only OI creation (rejected: easily missed when Employment domain is new) |
| **M365 auth refresh in preflight** | WorkIQ requires periodic re-auth; detecting expiry early surfaces a clear P1 warning with actionable command | No auth check (rejected: catch-up silently skips work calendar with unclear error) |

---

*End of spec. Authored by Artha · March 12, 2026. Revised v1.1 incorporating feedback on redaction, caching, dedup merging, conflict scoring, meeting-triggered OIs, and auth refresh.*
