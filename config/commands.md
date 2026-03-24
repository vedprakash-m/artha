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
Monthly cost: $[X] / $[BUDGET] budget ([%]%)
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

### `/domains`
List all available domains with enable/disable status and a one-line description.

**Display format:**
```
━━ YOUR DOMAINS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ALWAYS ACTIVE (load every catch-up):
  ✅ finance       — Bills, bank accounts, credit cards, investments
  ✅ immigration   — USCIS filings, visa status, passport, travel docs
  ✅ health        — Doctor appointments, prescriptions, insurance
  ✅ calendar      — Appointments, reminders, events, schedules
  ✅ comms         — Important messages, follow-ups
  ✅ goals         — Personal goals, habit tracking, progress

ENABLED (load when relevant emails received):
  ✅ home          — Mortgage/rent, maintenance, utilities
  ✅ employment    — Payroll, HR, benefits
  ...

DISABLED (not active — enable with /domains enable <name>):
  ⬜ kids          — School events, homework, grades
  ⬜ pets          — [household_types: not applicable] Pet health, vaccinations
  ...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
N enabled · M disabled · Run a catch-up after enabling to load domain data.
```

**Sub-commands:**
- `/domains enable <name>` — enable a domain (calls `toggle_domain(name, enabled=True)` in profile_loader.py)
- `/domains disable <name>` — disable a domain (calls `toggle_domain(name, enabled=False)`)
- `/domains info <name>` — show full domain details (sensitivity, required connectors, setup questions)

**Implementation:**
Read domain list from `config/domain_registry.yaml` via `scripts/profile_loader.py::available_domains()`.
Cross-reference with `config/user_profile.yaml::domains` to determine enabled/disabled state.
Filter out `phase_2` domains (show as "Coming soon" in a separate section only if the user explicitly asks).
For `enable` / `disable` sub-commands: call `profile_loader.toggle_domain()`, then confirm change.



### `/cost`
Show current month API cost estimate vs. configured monthly budget (from `user_profile.yaml → budget.monthly_api_budget_usd`). Read from `health-check.md:cost_tracking`. Estimate tokens used × current AI CLI pricing.

### `/health`
System integrity check:
- Verify all files in `config/registry.md` exist on disk
- Verify state file schema versions match prompt expectations
- Test: `vault.py status`, `python scripts/pii_guard.py test` (quiet), Gemini CLI ping, Copilot CLI ping
- Report any drift, missing files, or version mismatches
Display: `✅ N/N checks passed` or itemized failures.

### `/eval`
Run the catch-up evaluation report (`python scripts/eval_runner.py`). Displays performance
trends, accuracy metrics, signal:noise ratio, and data freshness. Flags:
- `/eval` — full report (performance + accuracy + freshness)
- `/eval perf` — performance only (connector/skill/phase timing trends)
- `/eval accuracy` — accuracy only (acceptance rate, signal:noise)
- `/eval freshness` — domain staleness and OAuth health

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

### `/pr` — PR Manager (Personal Narrative Engine)
> **Requires:** `enhancements.pr_manager: true` (activate via `/bootstrap pr_manager`)
> **Script:** `python3 scripts/pr_manager.py`

**`/pr`** — Content calendar for this week. Shows scored moments + quota status.
Run: `python3 scripts/pr_manager.py --view`
```
━━ 📣 PR MANAGER — CONTENT CALENDAR ━━━━━━━━━━━━━━━━━━━━━━━━━━
Moment                     Platforms       Thread   Score
────────────────────────────────────────────────────────
🟠 Holi (Wed Mar 25)      LI + FB + WA    NT-2     0.92
🟡 Q1 reflection          LinkedIn        NT-5     0.68
Posts this week: 0/2 (LinkedIn) · 0/2 (FB) · 0/2 (IG)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**`/pr threads`** — Narrative thread progress: last post, post cadence, reception trend per thread.
Run: `python3 scripts/pr_manager.py --threads`

**`/pr voice`** — Display active voice profile: tone, language, AVOID list, signature elements, any user overrides.
Run: `python3 scripts/pr_manager.py --voice`

**`/pr moments`** — All detected moments with convergence scores (Phase 3+).
Run: `python3 scripts/pr_manager.py --step8 --verbose` then display `tmp/content_moments.json`.

**`/pr history`** — Post history (last 30 days). Read `state/pr_manager.md → Post History` section.

**`/pr draft <platform>`** — Generate a draft for `<platform>` (linkedin, facebook, instagram, whatsapp_status).
> **Requires:** `enhancements.pr_manager.compose: true` (Phase 3)
1. Run: `python3 scripts/pr_manager.py --draft-context <platform>` → get JSON context
2. Use context to generate 1 clean variant in-context
3. Display in Content Proposal format (§9.2 of specs/pr-manager.md)
4. Apply 3-gate PII firewall (context sanitization + pii_guard.py + human review)
5. On approval: `python3 scripts/pr_manager.py --log-post <platform> "<topic>" <thread_id> <score>`

**`/pr draft <platform> <topic>`** — Draft about a specific topic (e.g. `/pr draft linkedin "Holi 2026"`).

**`/pr draft <platform> --trending`** — Fresh Gemini trend context bypass cache (Phase 3+, costs ~$0.02).

**Platform shorthands:** `li` → `linkedin`, `fb` → `facebook`, `ig` → `instagram`, `wa` → `whatsapp_status`

**Command gating:** Phase 1: `/pr`, `/pr threads`, `/pr voice` — require `enhancements.pr_manager: true`.
Phase 3+: `/pr draft`, `/pr moments`, `/pr history` — additionally require `enhancements.pr_manager.compose: true`.

---

### `/stage` — Content Stage (PR-2)

> **Sub-feature of PR Manager (PR-2) · Spec: specs/pr-stage.md v1.3.0**
> Active when `enhancements.pr_manager.stage: true` in `config/artha_config.yaml`.
> State file: `state/gallery.yaml`

**`/stage`** — List all active content cards (seed, drafting, staged, approved).
Display: card ID, occasion, event date, status, days until event, platform draft summary.

**`/stage preview <CARD-ID>`** — Show full card with draft content for all platforms.
Display draft text, PII flags, approval status per platform.

**`/stage approve <CARD-ID>`** — Mark card as approved; emit copy-ready content for each platform.
Prints formatted post text, any visual prompt file paths.

**`/stage draft <CARD-ID>`** — Manually trigger draft generation for a seed card.
Requires: `enhancements.pr_manager.stage: true`. Phase 2: calls LLM with deep context.

**`/stage posted <CARD-ID> <platform>`** — Log that a post has been published on `<platform>`.
Updates platform draft status to posted; triggers archive when all platforms resolved.

**`/stage dismiss <CARD-ID>`** — Archive a card without posting (user decided not to post).
Card moves to `dismissed` state and is eventually archived to `gallery_memory.yaml`.

**`/stage history [year]`** — Browse cross-year archived cards from `state/gallery_memory.yaml`. (Phase 4)

**Command gating:** All `/stage` commands require `enhancements.pr_manager.stage: true`.
Phase 1: `/stage` list is available (auto-populated from Step 8).
Phase 2: `/stage preview`, `/stage draft`, `/stage approve`, `/stage posted`, `/stage dismiss`.
Phase 4: `/stage history`.

---

### `/radar` — AI Trend Radar (PR-3)

> **Requires:** `enhancements.pr_manager.ai_trend_radar: true` (activate via `/bootstrap ai_trend_radar`)
> **State file:** `state/ai_trend_radar.md`
> **Signal output:** `tmp/ai_trend_signals.json`

**`/radar`** — Display current AI trend signals.
1. Read `tmp/ai_trend_signals.json`.
2. If `signal_count == 0` or file absent: show "No signals yet — run the skill to pull fresh signals."
3. Otherwise display in table format:
```
━━ 📡 AI TREND RADAR ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generated: <generated_at>  |  <signal_count> signals

 #  Score   Category        Topic
──────────────────────────────────────────────────────────────
 1  0.20  [technique]     How to Do AI-Assisted Engineering
 2  0.15  [model_release] GPT-5 launches...
...
Topics of interest: Claude Tools, MCP Servers, Agentic Workflows
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
4. Flag any signal where `try_worthy: true` with a `⚡ TRY` badge.
5. Show current topics from `state/ai_trend_radar.md → topics_of_interest`.

**`/radar topic list`** — Show all topics in the Interest Graph (from `state/ai_trend_radar.md → topics_of_interest`).

**`/radar topic add <name>`** — Add a new topic to the Interest Graph.
1. Prompt for keywords (comma-separated) if not provided.
2. Append to `state/ai_trend_radar.md → topics_of_interest` with today's date, `boost: 0.3`, `source: manual`.
3. Confirm: "Added topic '<name>' to radar Interest Graph."

**`/radar topic remove <name>`** — Remove a topic from the Interest Graph.
1. Find entry in `state/ai_trend_radar.md → topics_of_interest` matching the name (case-insensitive).
2. Remove and confirm: "Removed topic '<name>' from radar Interest Graph."

**`/radar run`** — Pull fresh signals (calls `artha_run_skills("ai_trend_radar")` via MCP if available, or instructs user to run `python3 -c "from pathlib import Path; from scripts.skills.ai_trend_radar import get_skill; get_skill(Path('.')).pull()"`).

---

