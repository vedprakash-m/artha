# Work-IQ Integration Spec — Artha Feature Specification
<!-- specs/work-int.md | v2.0 | authored: 2026-03-12 | revised: 2026-03-12 -->

---

> **⚠️ IMPLEMENTATION BLOCKER — §6.2 Corporate Compliance**
> Before ANY coding begins, Ved must obtain Microsoft IT/compliance approval for routing WorkIQ calendar data through Claude API (Anthropic). If denied, fall back to local-only "sidecar" display mode. See §6.2 for details.

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
6. **Partial redaction before LLM transit** — Sensitive project codenames are redacted *locally* via substring replacement before meeting titles enter the Claude API context. Only the matched keyword is replaced (e.g., "Project Cobalt Review" → "[REDACTED] Review"), preserving meeting-type context needed for trigger classification. Configurable in `config/settings.md`.

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
| `tmp/work_calendar.json` | Raw WorkIQ calendar response (this session only) | ❌ Ephemeral — **explicitly deleted** at Step 18 (see §8) | No (tmp/ is gitignored) | Yes — meeting titles contain work context |
| `tmp/.workiq_cache.json` | Cached WorkIQ availability/auth check (24h TTL) | ❌ Ephemeral — regenerated on expiry | No (tmp/ is gitignored) | No — contains only booleans and timestamps |
| `state/work-calendar.md` | Minimal metadata: last fetch time, meeting count/duration, conflict count, density score | ✅ Persisted | Yes | No — no meeting titles, no attendees, no bodies |
| `state/employment.md` | Existing file — updated with schedule density metrics only | ✅ Persisted | Yes (encrypted — **add to encrypted list if not already**) | ⚠️ Elevated — WorkIQ data adds work-pattern sensitivity |

### 3.3 WorkIQ Provenance

**What is `@microsoft/workiq`?** WorkIQ is a Microsoft-published npm package that connects AI agents to Microsoft 365 Copilot via MCP (Model Context Protocol). It provides access to workplace intelligence (calendar, email, documents, Teams messages) grounded in organizational data via Microsoft Graph.

- **Publisher:** Microsoft (`@microsoft` npm scope)
- **npm:** `@microsoft/workiq` — [npmjs.com/package/@microsoft/workiq](https://www.npmjs.com/package/@microsoft/workiq)
- **Auth:** Uses M365 Copilot license credentials via Entra ID (enterprise tenant only)
- **Stability:** Currently in active development. API surface may change between versions.
- **Fallback if package disappears:** If `@microsoft/workiq` is removed from npm or deprecated, Artha falls back to "WorkIQ unavailable" gracefully (§7). The integration is purely additive — no Artha functionality depends on it. A future alternative would be direct MS Graph calendar API calls via `scripts/msgraph_fetch.py` (already exists for personal Outlook).

> **Version pinning:** In all `npx` invocations, use a pinned version (`@microsoft/workiq@1.x.x`) rather than `@latest` to avoid breakage from upstream changes. The pinned version is stored in `config/settings.md` under `workiq_version` and updated manually after testing a new release.

### 3.4 What Gets Persisted vs. Ephemeral

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

Add a **combined** WorkIQ detection + auth check to `preflight.py`. Uses a single `npx` call (not two separate calls) to avoid a 30-40 second penalty.

#### 4.1.1 Combined WorkIQ Detection + Auth (P1, non-blocking)

**Strategy:** A single lightweight query (`"What is my name?"`) simultaneously proves:
1. `npx` is installed and `@microsoft/workiq` package resolves
2. M365 auth token is valid (query returns a result only if authenticated)

**Cached fast path:** If the combined check succeeded within 24 hours, skip `npx` entirely.

```python
import os, json, platform, shutil, subprocess, time
from pathlib import Path

WORKIQ_CACHE = Path("tmp/.workiq_cache.json")  # ephemeral, NOT in state/
CACHE_TTL_HOURS = 24
WORKIQ_VERSION = None  # loaded from config/settings.md → workiq_version

def check_workiq_combined():
    """
    Single-call detection + auth check with 24h cache.
    Returns: {available, auth_valid, version, platform, reason, from_cache}
    """
    os_name = platform.system()
    
    # ── Fast path: use cache if fresh ──
    if WORKIQ_CACHE.exists():
        try:
            cache = json.loads(WORKIQ_CACHE.read_text())
            age_hours = (time.time() - cache.get("checked_at", 0)) / 3600
            if age_hours < CACHE_TTL_HOURS:
                return {**cache, "platform": os_name, "from_cache": True}
        except (json.JSONDecodeError, KeyError):
            pass  # corrupt → fall through
    
    # ── Slow path: single npx call (once per 24h) ──
    if not shutil.which("npx"):
        result = {"available": False, "auth_valid": False, "reason": "npx not found"}
    else:
        ver = f"@microsoft/workiq@{WORKIQ_VERSION}" if WORKIQ_VERSION else "@microsoft/workiq@latest"
        try:
            proc = subprocess.run(
                ["npx", "-y", ver, "ask", "-q", "What is my name?"],
                capture_output=True, text=True, timeout=25
            )
            if proc.returncode == 0 and proc.stdout.strip():
                result = {"available": True, "auth_valid": True,
                          "version": WORKIQ_VERSION or "latest"}
            elif proc.returncode != 0 and "auth" in proc.stderr.lower():
                result = {"available": True, "auth_valid": False,
                          "reason": "M365 auth expired",
                          "action": "Run: npx workiq logout && retry"}
            else:
                result = {"available": False, "auth_valid": False,
                          "reason": f"exit {proc.returncode}: {proc.stderr.strip()[:100]}"}
        except subprocess.TimeoutExpired:
            result = {"available": False, "auth_valid": False, "reason": "timeout (>25s)"}
        except Exception as e:
            result = {"available": False, "auth_valid": False, "reason": str(e)}
    
    # Cache for fast path
    result["checked_at"] = time.time()
    try:
        WORKIQ_CACHE.parent.mkdir(parents=True, exist_ok=True)
        WORKIQ_CACHE.write_text(json.dumps(result))
    except Exception:
        pass
    
    return {**result, "platform": os_name, "from_cache": False}
```

**Preflight output:**
- ✅ Available + auth valid: `✓ [P1] WorkIQ: Available + authenticated (v1.x.x) — work calendar will be fetched`
- ⚠️ Available but auth expired: `🔴 [P1] WorkIQ Auth: EXPIRED → run: npx workiq logout && retry`
- ℹ️ Not available: `⚠ [P1] WorkIQ: Not available ([reason]) — work calendar skipped`
- 🔵 From cache: append `(cached [N]h ago — next live check in [M]h)`

**Session flags:** `workiq_available` and `workiq_auth_valid` for use in Step 4.

### 4.2 Fetch Enhancement (Step 4)

Add **WorkIQ Calendar Fetch** as a 7th parallel source. Runs only if `workiq_available == true` AND `workiq_auth_valid == true`.

**Invocation (pinned version, explicit date range):**

```bash
# {START_DATE} and {END_DATE} are computed at runtime as YYYY-MM-DD
# Context pressure GREEN/YELLOW: 7-day window (Mon→Sun)
# Context pressure RED/CRITICAL: 2-day window (today+tomorrow) to save tokens
npx -y @microsoft/workiq@{PINNED_VERSION} ask \
  -q "List all my calendar events from {START_DATE} through {END_DATE}. For each event return EXACTLY this format, one event per line:
DATE | START_TIME | END_TIME | TITLE | ORGANIZER | LOCATION | TEAMS(yes/no)
Example: 2026-03-12 | 09:00 | 10:00 | Sprint Planning | Jane Doe | Teams | yes
Do not add any headers, footers, or commentary. Only output the pipe-delimited lines."
```

> **Why explicit dates:** Relative terms like "this week" are ambiguous — WorkIQ's LLM may interpret them differently depending on day-of-week and locale. Explicit `YYYY-MM-DD` ranges eliminate ambiguity.

> **Why structured format request:** WorkIQ returns LLM-generated markdown. By requesting a rigid pipe-delimited format, we make parsing dramatically more reliable and avoid model-output variance.

**Context pressure integration:**
| Artha context pressure | WorkIQ query window | Rationale |
|----------------------|-------------------|-----------|
| 🟢 Green / 🟡 Yellow | 7 days (Mon→Sun) | Full week view, density trends |
| 🔴 Red / ⚠️ Critical | 2 days (today + tomorrow) | Save ~2,000 tokens |

#### 4.2.0 WorkIQ Output Parsing

**Example WorkIQ response** (actual observed format):

```
2026-03-12 | 08:00 | 08:30 | Enter Top5 Weekly | Shashidhar Joshi | Teams | yes
2026-03-12 | 09:05 | 09:35 | Admin Office Hours | Ved Mishra | Conference Room 3 | no
2026-03-12 | 09:05 | 10:05 | LT Review for XPF | Ramjee Tangutur | Teams | yes
2026-03-12 | 11:00 | 12:00 | xInfraSWPM: XPF Weekly | Ved Mishra | Teams | yes
2026-03-12 | 13:00 | 14:00 | XPF Burn-In Discussion | Anil Kumar | Teams | yes
2026-03-12 | 15:35 | 16:05 | DM & CPE Repair Sync | Li Wei | Teams | yes
```

**Parser (robust, handles format variance):**

```python
import re, hashlib
from datetime import datetime

def parse_workiq_response(raw_text: str) -> list:
    """
    Parse WorkIQ pipe-delimited calendar output into structured events.
    Handles: extra whitespace, missing fields, non-conforming lines (skipped).
    """
    events = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue  # skip headers, commentary, blank lines
        
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue  # malformed — skip
        
        try:
            date_str = parts[0]
            start_str = parts[1]
            end_str = parts[2]
            title = parts[3]
            organizer = parts[4]
            location = parts[5] if len(parts) > 5 else ""
            is_teams = parts[6].lower().startswith("y") if len(parts) > 6 else False
            
            start_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{date_str} {end_str}", "%Y-%m-%d %H:%M")
            duration_min = int((end_dt - start_dt).total_seconds() / 60)
            
            event_id = hashlib.md5(f"{date_str}{start_str}{title}".encode()).hexdigest()[:12]
            
            events.append({
                "id": f"workiq-{event_id}",
                "calendar": "Microsoft Work",
                "summary": title,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "duration_minutes": duration_min,
                "all_day": False,
                "location": location,
                "organizer": organizer,
                "attendees": [],
                "status": "confirmed",
                "source": "work_calendar",
                "is_teams_meeting": is_teams
            })
        except (ValueError, IndexError):
            continue  # unparseable line — skip, don't crash
    
    return events
```

**Failure modes and handling:**
| Failure | Detection | Action |
|---------|----------|--------|
| 0 events parsed from non-empty response | `len(events) == 0 and len(raw_text) > 50` | Log warning: "WorkIQ returned text but 0 events parsed — format may have changed" |
| WorkIQ returns conversational prose instead of pipe-delimited | First line doesn't contain `\|` | Retry with more explicit prompt; if still fails, skip with footer note |
| WorkIQ returns partial data (some days missing) | `len(set(e["start"][:10] for e in events))` < expected days | Log info, proceed with partial data |

**Output handling (updated):**
1. Parse WorkIQ response via `parse_workiq_response()` into structured event list
2. Save raw response to `tmp/work_calendar.json`
3. **Apply partial redaction** (see §4.2.1) — only matched keywords replaced, meeting type preserved
4. Normalize each event to the standard Artha calendar schema (see parser output above)

**Error handling:**
- WorkIQ auth failure → log to `audit.md`, set `workiq_available = false` for session, continue. Briefing footer: `"⚠️ Work calendar unavailable — WorkIQ auth failed (Windows only)"`
- WorkIQ timeout (>30s) → skip, same footer note
- WorkIQ returns empty → log, continue (may be a light day)
- **On Mac (npx/@microsoft/workiq not found)** → footer: `"ℹ️ Work calendar: available on Windows laptop only"`

#### 4.2.1 Partial Redaction (Corporate IP Protection)

**Problem:** Meeting titles like "Project Cobalt Review" or "Azure Silica Roadmap" contain codenames that should not transit to the Anthropic API.

**Key design choice: PARTIAL redaction.** Only the matched keyword/substring is replaced, NOT the entire title. This preserves meeting-type context needed for:
- Meeting-trigger classification (§4.9 — "Interview", "Performance Review")
- Cross-domain conflict display (§4.4 — user needs to know it's a "Review" vs. "Sync")
- Briefing readability

**Example:**
- `"Project Cobalt Review"` → `"[REDACTED] Review"` ✅ (type preserved)
- NOT → `"[PROJECT-REDACTED]"` ❌ (type lost, triggers can't classify)

**Configuration in `config/settings.md`:**

```yaml
## WorkIQ Redaction
# Substrings replaced with [REDACTED] before meeting data enters Claude context.
# Case-insensitive. Supports simple glob patterns (* wildcard).
# Only the matched substring is replaced — surrounding text is preserved.
workiq_redaction:
  keywords:
    - "Project Cobalt"
    - "Project Silica"
    - "ITAR"
    - "Confidential"
  patterns:
    - "CVE-\\d+-\\d+"     # vulnerability identifiers (e.g., CVE-2026-1234)
    - "MSRC-\\d+"          # security response center cases
  replace_with: "[REDACTED]"
```

**Implementation:**

```python
import re

def redact_work_events(events: list, redaction_config: dict) -> list:
    """Apply PARTIAL redaction — only matched substrings replaced, type preserved."""
    keywords = redaction_config.get("keywords", [])
    patterns = redaction_config.get("patterns", [])
    replacement = redaction_config.get("replace_with", "[REDACTED]")
    
    # Build combined regex: keywords (escaped) + patterns (raw regex)
    all_patterns = [re.escape(kw) for kw in keywords] + patterns
    if not all_patterns:
        return events  # empty redaction list → pass through
    
    combined = re.compile("|".join(all_patterns), re.IGNORECASE)
    
    for event in events:
        title = event.get("summary", "")
        new_title, count = combined.subn(replacement, title)
        if count > 0:
            event["summary"] = new_title.strip()
            event["redacted"] = True
            event["redaction_count"] = count
    
    return events
```

**Redaction happens locally BEFORE** the data is included in any Claude API prompt. The raw (unredacted) data in `tmp/work_calendar.json` is **explicitly deleted** at Step 18 (see §8).

**Briefing display examples:**
```
9:00am  💼 [REDACTED] Review (60 min, Teams)       ← type "Review" preserved
10:00am 💼 Sprint Planning (30 min, Teams)          ← no redaction needed
11:00am 💼 [REDACTED] Roadmap Discussion (90 min)   ← type "Roadmap Discussion" preserved
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

**⚠️ Dedup-Excludes-Conflict Rule:** Events that were merged via dedup (source = "work+personal") MUST NOT generate conflict alerts against themselves in §4.4. The dedup window (±5 min) and the conflict window (±15 min) overlap; without this rule, a deduplicated event could simultaneously be treated as a conflict. Implementation: after dedup, set a `merged=True` flag on merged events; §4.4 conflict detection skips any event pair where either has `merged=True`.

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

Rule 8: Meeting load warning (DURATION-BASED, not count-based)
  Eight 15-min syncs (2 hours) is light; three 2-hour workshops (6 hours) is exhausting.
  Use weighted duration, not raw count:
  
  IF total_meeting_minutes > 300 (5 hours):
    Surface: "📊 Heavy meeting load: [N] meetings, [H]h[M]m total. Focus time: [gap]"
    Identify largest gap ≥30 min: "🟢 Best focus window: [start]–[end]"
  
  IF largest_focus_gap < 60 minutes (all gaps < 1 hour):
    Surface: "⚡ Context Switching Fatigue: no focus block >60 min today"
    
  IF total_meeting_minutes < 120 (light day):
    Surface: "🟢 Light meeting day ([N] meetings, [H]h total) — good for deep work"
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
                                          
📊 Today: 13 meetings (6h15m) | Focus window: 11:30am–1:00pm
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
[N] emails → [N] actionable · 💼 [N] work meetings ([H]h[M]m) · signal:noise [N]:[N]
🔒 PII: [N] scanned · [N] redacted · [N] patterns
[ℹ️ Work calendar: Windows-only | not available this session]  ← only when unavailable
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

#### 4.5.1 Teams "Join" Action Proposal (Step 13)

If a Teams meeting is starting within 15 minutes of the catch-up time, surface a low-friction action:

```
━━ 🎯 ACTIONS (Step 13) ━━━━━━━━━━━━━━━━━━━━
→ 💼 Join "Sprint Planning" starting in 8 min (Teams) [Y/n]
```

This moves the integration from "Inquiry" (reading calendar) to "Execution" (acting on it). On approval, open the Teams meeting link via `start <teams_url>` (Windows) or `open <teams_url>` (Mac, if link available from merged dedup).

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

## Weekly Density (rolling — 13-week max)
```yaml
# Updated each catch-up when WorkIQ is available.
# Counts AND minutes — no titles, no attendees, no bodies.
# Rolling 13-week window (one quarter). Entries older than 13 weeks are pruned.
density:
  - week_of: "2026-03-09"
    mon: { count: 13, minutes: 375 }
    tue: { count: 8, minutes: 240 }
    wed: { count: 6, minutes: 210 }
    thu: { count: 9, minutes: 315 }
    fri: { count: 5, minutes: 150 }
    total_count: 41
    total_minutes: 1290
    conflicts_detected: 2
    avg_daily_minutes: 258
    max_focus_gap_minutes: 90
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

> **Sensitivity review:** With WorkIQ data, `employment.md` now contains work-pattern metadata (focus windows, busiest day) that could reveal sensitive schedule patterns. **Confirm `employment.md` is in the encrypted file list** (`vault.py`). If not, add it — this data has elevated sensitivity compared to static role/comp data.

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
- **Temporal filter (critical):** Only create OIs for meetings **in the next 7 days** (future-dated). Past meetings (e.g., in digest mode after 48h+ gap) are logged to `employment.md` metrics only — do NOT create stale "Prepare for..." OIs for meetings that already happened.
- When a `critical` meeting is detected in the future window:
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

> **Note:** The platform detection is now part of the combined check in §4.1.1. This section documents the design rationale.

**Key design decisions:**
1. **Cache lives in `tmp/`** (not `state/`) — avoids accidental git tracking if someone runs `git add state/`. The `tmp/` directory is gitignored and matches the ephemeral data pattern.
2. **Version is pinned** — `config/settings.md` contains `workiq_version: "1.x.x"`. Never use `@latest` in high-frequency paths. Update the pinned version manually after testing a new release.
3. **Single call** — detection + auth check happen in one `npx` invocation to avoid doubling the 10-20 second latency.
4. **24-hour cache TTL** — On Mac (where WorkIQ is never available), the cache persists indefinitely (always returns "npx not found" instantly).

```python
# The implementation is in §4.1.1 check_workiq_combined()
# Cache location: tmp/.workiq_cache.json
# Cache schema: {available, auth_valid, version, checked_at, platform, reason}
```

---

## 6. Privacy & Compliance Considerations

### 6.1 What enters Claude API context

| Data | Enters Claude context? | Mitigation |
|------|----------------------|-----------|
| Work meeting titles | ✅ Yes — **after partial redaction** | Sensitive codenames substring-replaced per `config/settings.md` redaction list. Meeting type preserved (e.g., "[REDACTED] Review"). Ephemeral — not persisted. |
| Work meeting attendee names | ✅ Yes (during conflict detection) | Not persisted to state files. |
| Meeting bodies/agendas | ❌ NO — not requested from WorkIQ | Query explicitly asks for titles + times only. |
| Teams chat content | ❌ NO — not requested | Out of scope. |
| Work emails | ❌ NO — not requested | Out of scope. Explicitly excluded. |
| Meeting duration (minutes) | ✅ Yes | Safe operational data. Persisted as aggregate to work-calendar.md. |

### 6.2 Corporate policy check — ⚠️ IMPLEMENTATION BLOCKER

> **This is Step 0 of the implementation plan (§8). Do NOT proceed past this step until compliance is confirmed.**

**Action required before ANY coding:** Ved must verify with Microsoft IT/compliance that using WorkIQ output in a personal AI assistant context is permitted. Key questions:

1. Is routing WorkIQ calendar data through Claude API (Anthropic) acceptable under Microsoft data handling policy?
2. Does the M365 Copilot license terms permit third-party consumption of calendar metadata?
3. Is local-only processing (no persistence) sufficient, or is additional DLP required?

**Recommendation:** If corporate policy restricts external API transit of calendar data, implement a **local-only mode** where WorkIQ output is displayed directly in the terminal without being processed by Claude. This reduces the integration to a "sidecar" display alongside the Artha briefing.

### 6.3 `/privacy` command update

Add WorkIQ to the privacy surface disclosure:

```
WorkIQ (work calendar):
  Data fetched: Meeting titles, times, durations, organizers (calendar only)
  Local redaction: Sensitive codenames partially replaced per config/settings.md (type preserved)
  Sent to Claude API: Yes (partially redacted + ephemeral, during briefing generation only)
  Persisted to state: Counts + duration aggregates only (no titles, no attendees)
  Available on: Windows laptop only (corp M365 Copilot license)
  Package: @microsoft/workiq (Microsoft-published npm, pinned version)
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
| **Mac + stale work-calendar.md** | Read metadata from last Windows fetch (<12h old) | Footer: "💼 [N] work meetings detected via Windows ([H]h ago, titles unavailable on this device)" |
| **Mac + stale work-calendar.md (>12h)** | Ignore stale metadata entirely | No work calendar reference (data too old to be useful) |
| **Any platform + WorkIQ returns empty** | Log, continue | No work events shown; no error |

**Critical rule:** In ALL degradation scenarios, the personal calendar, email processing, domain routing, and every other Artha feature works identically to current behavior. WorkIQ failure NEVER blocks or alters non-WorkIQ functionality.

---

## 8. Implementation Plan

### Phase 1 — Calendar Read-Only (MVP)

| Step | Task | Files changed | Notes |
|------|------|---------------|-------|
| **0** | **🔴 BLOCKER: Obtain Microsoft IT/compliance approval (§6.2)** | None | Do NOT proceed until confirmed. If denied, implement local-only sidecar mode. |
| 1 | Add combined WorkIQ detection+auth to `scripts/preflight.py` | `scripts/preflight.py` | Insert after existing MS Graph check. Single `npx` call with 24h cache. |
| 2 | Add `workiq_redaction` + `workiq_version` + `meeting_triggers` to `config/settings.md` | `config/settings.md` | Three new YAML sections. |
| 3 | Create `state/work-calendar.md` with schema (§4.6) | `state/work-calendar.md` (new) | Count+duration schema with 13-week rolling window. |
| 4 | Add `tmp/.workiq_cache.json` to `.gitignore` | `.gitignore` | Also confirm `tmp/` glob covers `tmp/work_calendar.json`. |
| 5 | Confirm `state/employment.md` is in vault encrypted file list | `scripts/vault.py` | If not, add it — WorkIQ data elevates sensitivity. |
| 6 | Update `config/Artha.md` **Step 4** — add WorkIQ as 7th source | `config/Artha.md` | **Insert at:** the "PARALLEL FETCH" block (after iCloud Cal). Add: "7. WorkIQ Calendar (if `workiq_available && workiq_auth_valid`): fetch via pinned npx, parse pipe-delimited, apply partial redaction." |
| 7 | Update `config/Artha.md` **Step 8f** — add Rules 7a/7b/8 | `config/Artha.md` | **Insert at:** after existing Rule 6 in the compound signals section. Add cross-domain conflict (7a), internal work conflict (7b), duration-based density (8). |
| 8 | Update `config/Artha.md` **Step 11** — merged calendar briefing format | `config/Artha.md` | **Insert at:** the 📅 TODAY section template. Add 💼 prefix, duration footer, Teams Join action. |
| 9 | Update `config/Artha.md` **Step 13** — Teams Join action proposal | `config/Artha.md` | **Insert at:** the action proposals section. Add "if Teams meeting within 15 min, surface Join action." |
| 10 | Update `config/Artha.md` **Step 16** — health-check workiq schema | `config/Artha.md` | **Insert at:** the catch_up_runs YAML template. Add workiq block. |
| 11 | **Update `config/Artha.md` Step 18 — explicit tmp/ cleanup** | `config/Artha.md` | **⚠️ CRITICAL:** Add `rm -f tmp/work_calendar.json` to Step 18 BEFORE `vault.py encrypt`. Without this, corporate meeting data persists in `tmp/` indefinitely. |
| 12 | Update `state/health-check.md` schema with workiq fields | `state/health-check.md` | Add workiq block to catch_up_runs template. |
| 13 | Test on Windows (full flow — fetch, parse, redact, merge, brief) | — | Verify pipe-delimited parsing against live WorkIQ output. |
| 14 | Test on Mac (graceful degradation — no errors, personal-only briefing) | — | Verify stale metadata display (<12h) and silent skip (>12h). |

> **Artha.md editing note:** `config/Artha.md` is the core application (~92KB, 700+ lines). It is a CLAUDE.md instruction file, not executable code. Each step update must be done carefully with a review of surrounding steps to avoid breaking step numbering or instruction flow. Use `edit` tool with precise `old_str` matching. Test by running a full catch-up after each Artha.md change.

### Phase 2 — Schedule Intelligence (Future — not in scope for Phase 1)

- Weekly meeting density trends in `employment.md`
- "Busiest day" detection → briefing suggests lighter scheduling
- Focus time gap analysis → auto-suggest calendar blocks
- Meeting-to-meeting transit time warnings (if location data available)

### Phase 3 — Work-Personal Balance (Future — not in scope for Phase 1)

- After-hours meeting detection → "You have 2 meetings after 5pm today"
- Weekly work-life balance score in `/scorecard` Health dimension
- PTO/vacation detection → suppress work calendar on PTO days

---

## 9. Testing Checklist

### Windows (WorkIQ available)

- [ ] Preflight combined check detects WorkIQ + validates auth in single npx call
- [ ] Cache returns instantly on subsequent runs (<24h)
- [ ] Step 4 fetches work calendar with explicit date range (not "this week")
- [ ] Pipe-delimited response parsed correctly via `parse_workiq_response()`
- [ ] Partial redaction replaces keywords only, preserves meeting type (e.g., "[REDACTED] Review")
- [ ] Calendar dedup merges fields (title from personal, Teams link from work)
- [ ] Merged events have `merged=True` flag and don't trigger self-conflicts
- [ ] Cross-domain conflicts (work ↔ personal) scored at Impact=3
- [ ] Internal work conflicts (work ↔ work) scored at Impact=1
- [ ] Duration-based density: >300 min = heavy, <60 min gap = fatigue warning
- [ ] Teams "Join" action surfaced for meetings starting within 15 min
- [ ] Critical meetings (Interview, Perf Review) auto-create Employment OIs (future-dated only)
- [ ] Past meetings (digest mode) logged to metrics only, no stale OIs created
- [ ] `tmp/work_calendar.json` created and **explicitly deleted at Step 18**
- [ ] `tmp/.workiq_cache.json` persists for 24h fast path (NOT in `state/`)
- [ ] `state/work-calendar.md` updated with counts + minutes (no titles)
- [ ] 13-week rolling window prunes old density entries
- [ ] `state/employment.md` schedule_metrics updated; file is encrypted
- [ ] `health-check.md` workiq section populated
- [ ] Context pressure RED/CRITICAL triggers 2-day query instead of 7-day

### Mac (WorkIQ unavailable)

- [ ] Preflight reports WorkIQ unavailable (P1 warning, non-blocking)
- [ ] Cache returns "npx not found" instantly (no network call)
- [ ] Step 4 runs all 6 personal sources identically to today
- [ ] No WorkIQ-related errors in audit.md
- [ ] Stale work-calendar.md (<12h) shows "💼 [N] meetings (titles unavailable on this device)"
- [ ] Stale work-calendar.md (>12h) ignored entirely
- [ ] Briefing footer shows "ℹ️ Work calendar: available on Windows laptop only" (or silent)
- [ ] All other Artha features unchanged
- [ ] No regression in any existing test

### Edge Cases

- [ ] WorkIQ returns 0 events (weekend or PTO) → no error, no work section
- [ ] WorkIQ auth token expired mid-session → graceful skip, P1 warning with re-auth command
- [ ] WorkIQ returns >50 events (conference day) → **process ALL for conflict detection**, but **cap briefing display at 25** with "[+N more]" note
- [ ] Personal and work calendar both have same event → dedup merges fields, sets `merged=True`
- [ ] Merged event does NOT trigger conflict alert against itself (dedup-excludes-conflict rule)
- [ ] Network timeout on WorkIQ (>30s) → skip, log, continue
- [ ] Redaction list is empty → no redaction applied, all titles pass through
- [ ] Meeting title matches multiple redaction keywords → each keyword independently replaced (not double-replaced)
- [ ] Partial redaction preserves meeting type: "[REDACTED] Review" not "[REDACTED]"
- [ ] Critical meeting (e.g., "Interview") already has matching OI → skip auto-creation, append note
- [ ] Past critical meeting in digest mode → log to metrics, do NOT create stale OI
- [ ] Cache file `tmp/.workiq_cache.json` is corrupt → fall through to live check gracefully
- [ ] WorkIQ returns conversational prose instead of pipe-delimited → 0 events parsed, log warning, retry once with explicit prompt
- [ ] WorkIQ package removed from npm → detection fails, cached as unavailable, all Artha features work normally

---

## 10. Decision Log

| Decision | Rationale | Alternatives considered |
|----------|-----------|----------------------|
| Calendar only, no email | Work emails are 100+/day; would overwhelm context and introduce corporate IP risk | Full email integration (rejected: noise + compliance) |
| Ephemeral meeting data | Corporate content shouldn't persist in personal OneDrive-synced state files | Full persistence (rejected: compliance risk) |
| Count+duration state file | Enables duration-based trend analysis without storing corporate content | Count-only (rejected: can't distinguish busy vs light days), no state (rejected: loses trends) |
| Runtime platform detection | Avoids config drift between Mac and Windows | Static config flag (rejected: user must remember to toggle) |
| WorkIQ CLI (not MCP server) | Simpler integration; MCP requires persistent server process | MCP server mode (considered for Phase 2 if needed) |
| **Partial redaction (substring-only)** | Full-title replacement destroys meeting type needed for trigger classification and briefing readability | Full-title replacement (rejected: loses "Review"/"Interview" context) |
| **Pinned version (not @latest)** | `@latest` causes breakage from upstream changes and adds network latency; pinned version in `config/settings.md` updated manually after testing | Always @latest (rejected: fragile + slow) |
| **Combined detection+auth (single call)** | Two separate `npx` calls = 30-40 sec penalty; single "What is my name?" call validates both availability and auth | Separate detection + auth (rejected: doubles latency) |
| **Cache in `tmp/` not `state/`** | `state/` is git-tracked; accidental `git add state/` would commit cache. `tmp/` matches ephemeral pattern. | state/.workiq_cache.json (rejected: git tracking risk) |
| **Field-merge dedup** | Work events carry Teams links/rooms while personal copies have friendly titles; merging retains best of both | Discard one (rejected: loses useful metadata) |
| **Dedup-excludes-conflict** | ±5 min dedup window overlaps ±15 min conflict window; without exclusion, merged events generate false self-conflicts | No exclusion (rejected: phantom conflicts) |
| **Cross-domain > internal conflicts** | Work↔personal conflicts require harder life trade-offs than back-to-back Teams calls; Impact=3 vs Impact=1 prevents alert fatigue | Uniform scoring (rejected: floods briefing with internal noise) |
| **Duration-based density (not count)** | 8×15-min syncs (2h) ≠ 3×2h workshops (6h); minutes-based threshold is more meaningful | Count > 8 (rejected: poor signal for actual busyness) |
| **Meeting-triggered OIs (future-only)** | Critical meetings need prep; but in digest mode, past meetings shouldn't create stale OIs | Always create (rejected: stale "Prepare for yesterday's review" OIs), never create (rejected: miss prep reminders) |
| **Stale metadata display on Mac** | Mac can show density count from last Windows fetch (<12h) without titles; >12h is too stale to be useful | Always show stale data (rejected: misleading), never show (rejected: loses context) |
| **Compliance check as Step 0** | If Microsoft denies external API transit, entire integration is blocked; must confirm before coding | Compliance check at end (rejected: wasted implementation effort) |
| **Explicit tmp/ cleanup in Step 18** | Without `rm tmp/work_calendar.json`, corporate meeting data persists indefinitely in tmp/ | Rely on session cleanup (rejected: tmp/ isn't auto-cleaned between sessions) |
| **Context pressure → query variant** | 7-day query at green/yellow; 2-day at red/critical saves ~2,000 tokens when context window is constrained | Always 7-day (rejected: wastes tokens at high pressure), always 2-day (rejected: loses weekly view) |

---

*End of spec. Authored by Artha · March 12, 2026. v2.0 — integrating critical review: compliance blocker, WorkIQ provenance, parsing logic, explicit date ranges, partial redaction, duration-based density, dedup-excludes-conflict, stale metadata handling, Teams Join action, temporal OI filter, tmp/ cleanup, version pinning, context pressure integration, 13-week rolling window, employment.md sensitivity review.*
