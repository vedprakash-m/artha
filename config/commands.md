## §5 Slash Commands

### `/catch-up`
Full catch-up workflow (§2). Equivalent to "catch me up", "morning briefing", "SITREP".

### `/status`
Quick health check — no email fetch. Display:
```
━━ ARTHA STATUS ━━━━━━━━━━━━━━━━━━━━━━━━━━
Last catch-up:  [N] hrs ago ([time])
Active alerts:  [N 🔴] [N 🟠] [N 🟡]
Domain freshness: [table — domain + last update + staleness]
MCP Tools:  Gmail [✅/❌]  Calendar [✅/❌]
CLIs:       Gemini [✅/❌]   Copilot [✅/❌]
Encryption: [locked/unlocked + file count]
Monthly cost: $[X] / $50 budget ([%]%)
```
Read from `health-check.md` and test MCP/CLI connectivity.

### `/goals`
Goal scorecard only — no email fetch. Read from `state/goals.md`. Display:
```
━━ GOAL PULSE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[goal bar: NAME  ████████░░ 80%  ON TRACK]
[goal bar: NAME  ████░░░░░░ 40%  AT RISK ]
```
Show 2-week trend if available.

**Sprint display (if any sprint is active):**
```
━━ GOAL SPRINTS ━━━━━━━━━━━━━━━━━━━━━━━━━━
[SPRINT NAME]  ██████░░░░  60%  Day 18/30
  Goal: [linked goal] | Target: [description] | ⚡ Pace: [on track|behind|ahead]
  [Calibration note if at 2-week mark: "Calibration pending — pace review?"]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
**Sprint commands:**
- `/goals sprint new` — create a new sprint (Artha asks: name, linked goal, target, duration 14–90 days; default 30 days)
- `/goals sprint pause [name]` — pause sprint progress tracking
- `/goals sprint close [name]` — mark sprint complete; log outcome to memory.md

### `/domain <name>`
Deep-dive into a single domain. Read `state/<name>.md` and `prompts/<name>.md`. Display:
- Last updated timestamp
- All active items (not archived) with status
- Any open alerts for this domain
- Suggested next action (if any)
Valid domain names: immigration, finance, kids, travel, health, home, shopping, goals, calendar, vehicle, estate, insurance, comms

### `/cost`
Show current month API cost estimate vs. $50 monthly budget. Read from `health-check.md:cost_tracking`. Estimate tokens used × current Claude pricing.

### `/health`
System integrity check:
- Verify all files in `config/registry.md` exist on disk
- Verify state file schema versions match prompt expectations
- Test: `vault.py status`, `python scripts/pii_guard.py test` (quiet), Gemini CLI ping, Copilot CLI ping
- Report any drift, missing files, or version mismatches
Display: `✅ N/N checks passed` or itemized failures.

### `/items`
Display all open action items from `state/open_items.md`. Groups:
```
━━ OPEN ITEMS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 OVERDUE (deadline passed)
  OI-NNN [domain] [description] — due [date] ([N days overdue])

🟠 DUE SOON (≤7 days)
  OI-NNN [domain] [description] — due [date]

🟡 UPCOMING
  OI-NNN [domain] [description] — due [date]

🔵 OPEN (no deadline)
  OI-NNN [domain] [description]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[N] open items · [N] synced to Microsoft To Do
```
Also accepts: `/items add [description]` to interactively add a new item (Artha asks: domain, deadline, priority, then appends to `open_items.md` and pushes to To Do if configured).

Markdown: `/items done OI-NNN` marks item done; `/items defer OI-NNN [days]` defers.

### `/items quick`
Show only the 5-Minute task list from `state/memory.md → quick_tasks`. Quick display:
```
━━ ⚡ QUICK TASKS (≤5 min, phone-ready) ━━
• [QT-001] [domain] [description]
• [QT-002] [domain] [description]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[N] quick tasks · updated [time]
```
If `quick_tasks` is empty or not yet populated: "No quick tasks identified — run a catch-up to detect them."

### `/bootstrap` and `/bootstrap <domain>`
Guided interview to systematically populate state files. Replaces empty/bootstrap placeholder data with real user-provided information.

**Usage:**
- `/bootstrap` — show all domains with population status, then select one to populate
- `/bootstrap <domain>` — jump directly to that domain's interview
- `/bootstrap quick` — rapid setup mode: collect only the 3–5 highest-priority fields per domain
- `/bootstrap validate` — re-run validation on all existing state files; report field gaps, format errors, and stale data without modifying anything
- `/bootstrap integration` — guided setup for a new data integration (Gmail, Calendar, Outlook, iCloud)

**Workflow:**
```
1. If no domain specified, display population status table:
   ━━ BOOTSTRAP STATUS ━━━━━━━━━━━━━━━━━━━━
   Domain          Status           Action
   immigration     ⚠ placeholder    /bootstrap immigration
   finance         ⚠ placeholder    /bootstrap finance
   kids            ✅ populated     —
   health          ⚠ placeholder    /bootstrap health
   ...
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2. For selected domain:
   a. Read state file schema from prompts/<domain>.md
   b. Derive interview questions from schema fields
   c. Ask ONE question at a time — never dump a form
   d. Validate input:
      - Dates: must be ISO 8601 (YYYY-MM-DD)
      - Amounts: must be numeric
      - Required enum fields: offer valid options
   e. For sensitive fields (SSN, case numbers, account numbers):
      "This field is stored encrypted at rest. Enter your value:"
      Apply Layer 2 semantic redaction before writing
   f. After each domain section, confirm:
      "Here's what I'll save for [section]. Correct? [yes / edit / skip]"

3. Progress tracking:
   - Save progress per domain in memory.md → context_carryover
   - User can exit mid-interview: "Saved progress — resume with /bootstrap <domain>"
   - Already-answered fields preserved on resume

4. After writing:
   a. Run Layer 2 post-write verification (Step 8c)
   b. Update frontmatter: updated_by: user_interview (replaces 'bootstrap')
   c. Update last_updated timestamp
   d. Show completion summary:
      "✅ [domain].md populated: [N] fields written, verification passed"
   e. If domain is encrypted: vault.py decrypt → write → verify → vault.py encrypt

5. Detection rules for population status:
   - `updated_by: bootstrap` → ⚠ placeholder
   - `updated_by: user_interview` or `updated_by: artha-catchup` with >5 populated fields → ✅ populated
   - File missing → ❌ missing
```

**`/bootstrap quick` — Rapid setup mode:**
```
Ask only the 3–5 highest-priority fields per domain (marked `priority: high` in each prompt schema).
Skip optional / enrichment fields entirely.
Suitable for first-run users who want to get started in under 10 minutes.
After completing all high-priority fields, summarize:
  "✅ Quick setup complete. You can deepen any domain with /bootstrap <domain>."
```

**`/bootstrap validate` — Validation-only mode:**
```
For each populated state file:
  1. Check required fields are present and non-empty
  2. Validate date formats (ISO 8601)
  3. Validate numeric fields (no units embedded in numeric values)
  4. Check for stale data (last_updated older than 180 days)
  5. Scan for residual bootstrap placeholders (e.g. "[TBD]", "placeholder")
  6. Run PII guard sanity check (no raw PII in encrypted-at-rest fields)
Output a report card — do NOT modify any files.
  ━━ VALIDATION REPORT ━━━━━━━━━━━━━━━━━━━━━
  Domain         Result   Issues
  immigration    ✅ OK    —
  finance        ⚠ stale  last_updated 210 days ago
  health         ❌ gaps  3 required fields missing
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**`/bootstrap integration` — Add a new data integration:**
```
Present a menu of available integrations:
  1. Gmail / Google Calendar  (setup_google_oauth.py)
  2. Outlook / Microsoft To Do / Teams  (setup_msgraph_oauth.py)
  3. iCloud Mail / Calendar  (setup_icloud_auth.py)
  4. Canvas LMS  (canvas_fetch.py)
  5. Apple Health  (parse_apple_health.py)
Guide user through the selected setup script with step-by-step prompts.
After completion, run the corresponding --health check and report status.
```

### `/dashboard`
Life dashboard — comprehensive system overview. Read from `state/dashboard.md` (rebuilt each catch-up).
```
━━ ARTHA DASHBOARD ━━━━━━━━━━━━━━━━━━━━━━━

📊 LIFE PULSE
Domain          Status    Alert   Last Updated
immigration     🟡        —       2 days ago
finance         🟡        —       2 days ago
kids            🟢        —       today
health          ⚪        —       never
[...all 17 domains...]

⚡ ACTIVE ALERTS (ranked by U×I×A)
1. [U×I×A=27] [domain] [description]
2. [U×I×A=18] [domain] [description]

📋 OPEN ITEMS: [N] total ([N] overdue · [N] due this week)
[Top 5 items by priority]

🏥 SYSTEM HEALTH
Context pressure: [green/yellow/red] | OAuth: [N/N healthy] | Last catch-up: [N]h ago
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### `/scorecard`
Life Scorecard — 7-dimension life quality assessment. Generated during Sunday catch-up.
```
━━ LIFE SCORECARD ━━━━━━━━━━━━━━━━━━━━━━━━
Dimension               Score   Trend   Notes
Physical Health         [N]/10  [↑↓→]   [brief note]
Financial Health        [N]/10  [↑↓→]   [brief note]
Career & Growth         [N]/10  [↑↓→]   [brief note]
Family & Relationships  [N]/10  [↑↓→]   [brief note]
Immigration & Legal     [N]/10  [↑↓→]   [brief note]
Home & Environment      [N]/10  [↑↓→]   [brief note]
Personal Development    [N]/10  [↑↓→]   [brief note]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Composite: [N.N]/10 [trend]    Week of [date]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
Dimensions scored 1–10 using state file data. Composite = average of 7 dimensions. Week-over-week trend comparison when ≥2 weeks of data available.

### `/relationships`
Relationship graph overview. Read `state/social.md`. Display:
```
━━ RELATIONSHIP PULSE ━━━━━━━━━━━━━━━━━━━━━
Close family:     [N/N on cadence]
Close friends:    [N/N on cadence | X overdue]
Extended family:  [N/N on cadence | X overdue]

🔴 Overdue reconnects:
  [Name] ([tier]) — [N] days since contact (target: [frequency])

📅 Upcoming (14 days):
  [Name]: [birthday/occasion] — [date]

⚡ Life events needing acknowledgment:
  [Name]: [event] ([N] days since detected)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### `/decisions`
View active decision log. Read `state/decisions.md`. Display all active decisions with ID, summary, and review trigger.
Optional: `/decisions DEC-NNN` for full detail of one decision.
Optional: `/decisions add` to interactively log a new decision (Artha asks for context, domains, alternatives).

### `/scenarios`
View and run scenario analyses. Read `state/scenarios.md`. Display:
```
━━ SCENARIO ENGINE ━━━━━━━━━━━━━━━━━━━━━━━━
WATCHING (not triggered):
  SCN-001: Mortgage Refinance — trigger: rate < 6.0%
  SCN-002: Job Change Impact — trigger: mention of job transition
  SCN-003: College Cost Planning — trigger: SAT score / annual
  SCN-004: Immigration Timeline — trigger: PD movement / EAD risk
  SCN-005: Emergency Fund Stress Test — trigger: finance review

ACTIVE (triggered this session): [if any]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
`/scenarios SCN-NNN` runs a specific scenario analysis with current state data.

### `/goals leading`
Goal scorecard with leading indicators. Read `state/goals.md` + leading indicator data from domain prompts. Display:
```
━━ GOAL PULSE + LEADING INDICATORS ━━━━━━━━
[GOAL NAME]  ████████░░  80%  ON TRACK
  Leading: [domain] — [indicator name]: [value] [trend ↑↓→] [status]

[GOAL NAME]  ████░░░░░░  40%  AT RISK
  Leading: [indicator]: [value]  ⚠️ [alert if triggered]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### `/diff [period]`
Show meaningful state changes over a period. Uses git history of the `state/` directory.
- `/diff` → changes since last catch-up (default)
- `/diff 7d` → changes in last 7 days
- `/diff 30d` → changes in last 30 days
- `/diff DEC-NNN` → changes since a specific decision was logged

**Implementation:**
```bash
git log --since="[period]" --name-only --format="" -- state/*.md \
  | sort -u \
  | while read f; do git diff HEAD~N -- "$f"; done
```
Summarize each changed file as:
```
state/immigration.md  [+N/-N lines]
  + Added: [brief description of additions]
  - Removed: [brief description of removals]
```
Filter out `last_updated:` timestamp changes as noise. Show only semantic content changes.
If git history not available: "No git history found — run `git init && git add state/ && git commit -m 'Artha baseline'` to enable /diff."

### `/privacy`
Show the current privacy surface. Display:
```
━━ PRIVACY SURFACE ━━━━━━━━━━━━━━━━━━━━━━━━
Encrypted at rest (age):
  ✅ immigration.md · finance.md · health.md · insurance.md
  ✅ estate.md · audit.md · vehicle.md · contacts.md

PII filtering accuracy (last 30 days):
  Scanned: [N] emails | Redactions applied: [N] | Patterns caught: [N]
  False positive rate: [N]% | False negative rate: estimated [N]%

Data flows to external services:
  Claude API:   email bodies + state excerpts (PII pre-filtered)
  Gemini CLI:   web research queries only (via safe_cli.py, PII scanned)
  Copilot CLI:  code queries only (via safe_cli.py, PII scanned)
  MS To Do:     open item descriptions (no PII policy: never store PII in titles)
  Gmail:        briefing emails to self only (sensitivity-filtered for sensitive domains)
  OneDrive:     encrypted state files at rest

Last encryption cycle: [timestamp from vault.py status]
Vault lock state: [locked|unlocked]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
Read PII stats from `health-check.md → pii_footer` (aggregated over rolling 30 days). Read vault state from `vault.py status`.

### `/teach [topic]`
Domain-aware explanation using the user's own data as context. `[topic]` can be a concept, acronym, or question.

**Examples:**
- `/teach EAD` → explains Employment Authorization Document using current immigration state
- `/teach priority date` → explains USCIS Visa Bulletin priority dates using actual PD from immigration.md
- `/teach EB-2 NIW` → explains National Interest Waiver with case context from state files
- `/teach compound interest` → explains with reference to actual account values from finance.md

**Format:**
```
━━ TEACH: [topic] ━━━━━━━━━━━━━━━━━━━━━━━━━
[2–4 paragraph explanation in plain English]
[What this means for YOUR situation (using state file data):]
  • [Specific implication 1]
  • [Specific implication 2]
[Related: [linked concept if relevant]]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
If topic is immigration-related, load `state/immigration.md` for context (even if encrypted — decrypt first).
If topic is finance-related, load `state/finance.md`.
If topic not recognized: "I don't have specific state data for '[topic]' but here's a general explanation: [...]"

### `/power`
**Power Half Hour** — focused 30-minute session. Artha becomes a rapid-fire action assistant:
1. Lists all open items due ≤7 days (ordered by U×I×A score)
2. Presents each item with FNA annotation
3. For each item: "Done / Defer / Escalate / Skip?"
4. Executes approved actions (email draft, calendar event) with minimal friction
5. At completion: "Power Hour complete — [N] items resolved, [N] deferred"
Log session to `state/audit.md` as `POWER_HOUR | [timestamp] | items_handled: [N]`

---

