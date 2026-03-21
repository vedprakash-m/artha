---
schema_version: "1.0"
purpose: "First-run conversational setup for new Artha users"
created: 2026-03-13
ref: "hardening.md §6.2, commands.md /bootstrap"
---

# Bootstrap Interview — First-Run Experience

This document defines the conversational interview flow for new Artha users.
It is triggered automatically when `config/user_profile.yaml` does not exist,
or manually via the `/bootstrap` command.

## Core Principles

1. **Conversational, not a form.** Ask one question at a time.
2. **Progressive disclosure.** Start with essentials, add depth later.
3. **Platform-aware.** Auto-detect OS and available CLIs.
4. **Value-first.** Get to the first useful output as fast as possible.

## Phase 1 — Identity (Required, ~2 minutes)

Ask these questions in order. Each answer populates `config/user_profile.yaml`.

### Q1: Name
"What's your name? (This is used for briefing personalization.)"
→ Maps to: `family.primary_user.name`, `family.primary_user.display_name`

### Q2: Email
"What's your primary email address? (Used for email fetching and briefing delivery.)"
→ Maps to: `integrations.gmail.account`, `briefing.email`
→ Validate: basic email format check

### Q3: Family
"Who else is in your household? You can list names and ages, or skip for now."
→ Maps to: `family.spouse`, `family.children[]`
→ Accept: free-text, parse names and ages
→ Skip allowed: "Just me for now" → set `family.members: [primary_user]`

### Q4: Location
"What city/state are you in? (Used for timezone and location-aware alerts.)"
→ Maps to: `location.city`, `location.state`, `location.timezone`
→ Auto-derive timezone from state if possible

### Q5: Cultural Context (Optional)
"Artha can tailor briefings to your cultural background. Options:
 - south-asian-american
 - east-asian-american
 - latin-american
 - european-american
 - first-gen-immigrant
 - military-family
 - custom (describe your own)
 - skip

Which fits best? (You can always change this later.)"
→ Maps to: `family.cultural_context`
→ If a preset name: load from `config/presets/cultural/<name>.yaml`

## Phase 1b — Household Type + Domain Selection (~2 minutes)

Run this phase immediately after Phase 1 (before any integration setup).
Use the domain registry from `config/domain_registry.yaml` to drive this section.

### Q6: Household Type
"What best describes your household? This helps me focus my briefings.
  1) Just me            → single
  2) Me + partner       → couple
  3) Me + partner + kids → family
  4) Multi-generational (parents/grandparents) → multi_gen
  5) Roommates / shared housing → roommates"
→ Maps to: `household.type`
→ Default if skipped: "single"
→ Sets initial domain applicability (kids domain auto-hidden for single/couple/roommates)

### Q7: Housing Tenure
"Do you own or rent your home?
  1) Own (mortgage / paid off)
  2) Rent (lease / landlord)
  3) Other / skip"
→ Maps to: `household.tenure`
→ If renter: activate renter overlay in home domain (prompts/home.md §Renter-Overlay)

### Q8: Domain Selection Menu
Display the domain menu based on `household.type` from Q6. Show only domains
applicable to this household type (per `domain_registry.yaml::household_types`).

```
━━ CHOOSE YOUR DOMAINS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
These 6 domains are always active (can't be disabled):
  ✅ Finance       — Bills, bank, credit cards, investments
  ✅ Calendar      — Appointments, reminders, events
  ✅ Comms         — Important messages, follow-ups
  ✅ Goals         — Personal goals, habit tracking
  ✅ Health        — Doctor appointments, prescriptions

[If household.type != 'single' && origin_country set]:
  ✅ Immigration   — USCIS, visa status, passport

──────────────────────────────────────────────────────
Enable these optional domains? (Artha will watch for related emails)

 Y = yes  N = no  ?  = tell me more  ↵  = yes (default)

  [ ] Home         — Rent/mortgage, maintenance, utilities    (Y/n/?)
  [ ] Employment   — Payroll, HR, benefits, performance       (Y/n/?)
[If family or multi_gen]:
  [ ] Kids & School — Homework, grades, school events         (Y/n/?)
  [ ] Travel        — Flights, hotels, itineraries            (Y/n/?)
  [ ] Digital       — App subscriptions, streaming, domains   (Y/n/?)
  [ ] Learning      — Online courses, certifications          (Y/n/?)
  [ ] Shopping      — Orders, returns, price tracking         (Y/n/?)
  [ ] Social        — Events, birthdays, relationships        (Y/n/?)
  [ ] Insurance     — Health, auto, home/renter policies      (Y/n/?)
[Additional domains based on profile]:
  [ ] Vehicle       — Car maintenance, registration           (Y/n/?)

Type domain names to enable more, or press Enter to continue.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Implementation notes:**
- Use `config/domain_registry.yaml::enabled_by_default` as the pre-selected state
- Domains with `household_types` that exclude the current type are NOT shown
- Single-person mode (`household.type = single`): pre-deselect kids, suppress spouse references
- After selection: write enabled/disabled state to `config/user_profile.yaml::domains`
- Call `scripts/profile_loader.py::toggle_domain()` for each selection
- For domains where user says `?`: show the full description from domain_registry.yaml

### Q9: Pets (conditional)
Show only if user's household_type allows pets (all types):
"Do you have any pets? (Artha can track vaccinations, medication due dates, vet appointments.)
  y — yes → enter pet names and types
  n — no (default)"
→ If yes: set `domains.pets.enabled: true` + prompt for pet names/species
→ Maps to: `domains.pets.animals` (name, species for each)


## Phase 2 — First Integration (~2 minutes)

### Q10: Gmail Setup
"Want to connect your Gmail for email briefings? This is the highest-value integration.
 I'll walk you through Google OAuth. (yes/later)"
→ If yes: guide through `python scripts/setup_google_oauth.py`
→ If later: note in `state/open_items.md` as "Set up Gmail integration"

### After Gmail (if connected):
"You're ready for your first briefing. Say 'catch me up' anytime."

## Phase 3 — Progressive Enhancement (Subsequent sessions)

These are NOT asked during first run. They appear as suggestions in briefing footers
or when the user runs `/bootstrap integration`.

- Microsoft Graph (Outlook, To Do, Teams calendar)
- iCloud Mail & Calendar
- Canvas LMS (for families with school-age children)
- Vault encryption setup (`age` + keyring)
- Apple Health import

## Profile Generation

After Phase 1 completes, generate `config/user_profile.yaml` using the template
in `config/user_profile.example.yaml`. Fill in answered fields, leave others
with sensible defaults or empty values.

Then run:
```bash
python scripts/generate_identity.py
```
This creates `config/Artha.identity.md` and assembles `config/Artha.md`.

## Resume Capability

If the user exits mid-interview:
- Save progress to `state/memory.md` → `context_carryover.bootstrap_progress`
- On next session, detect incomplete bootstrap and offer: "Want to continue setup?"
- Already-answered fields are preserved — don't re-ask

## Demo Mode Integration

If the user says "catch me up" before completing setup:
1. Run `python scripts/demo_catchup.py` — shows a complete sample briefing
2. After demo: "That was a demo with sample data. Ready to set up your own? (yes/later)"

---

## PR Manager Bootstrap (`/bootstrap pr_manager`)

Trigger: user runs `/bootstrap pr_manager` OR setup completes and user has social platforms.
Ref: specs/pr-manager.md §14 — Pre-Bootstrap Questions

Ask these 5 questions before activating `enhancements.pr_manager: true`.

### PRQ1: Social media intent
"Would you like Artha to help you craft social media posts — LinkedIn, Facebook, Instagram,
WhatsApp Status? Or would you prefer to handle social media yourself?"
→ Options: [yes, all platforms] [yes, LinkedIn only] [yes, festivals/occasions only] [no thanks]
→ If 'no thanks': do NOT activate pr_manager. Confirm: "No problem — you can enable it later
  with `/bootstrap pr_manager`."
→ Maps to: `state/pr_manager.md → Voice Profile Overrides` (note: scope preference)

### PRQ2: LinkedIn role
"What role does LinkedIn play for you?
  (a) Active thought leadership — I want to build a professional presence
  (b) Light presence — I post occasionally when inspired
  (c) Dormant — I rarely use it"
→ If (a): prioritize NT-1 + NT-5 threads, set posting_limits.linkedin.max_per_week: 2
→ If (b): default limits apply
→ If (c): suppress LinkedIn-first content suggestions; Instagram/Facebook first
→ Maps to: narrative thread priority in state/pr_manager.md

### PRQ3: Family in posts
"Are you comfortable with Artha referencing your family in posts?
  (a) Yes — first names on private platforms, initials only on public (LinkedIn)
  (b) Yes — first names on all platforms
  (c) Prefer to keep family out of social media entirely"
→ If (a) or (b): NT-4 (Proud Dad) thread is active
→ If (c): NT-4 thread disabled; family never referenced in public posts
→ Maps to: `state/pr_manager.md` Voice Profile Overrides (family reference rule)

### PRQ4: Hindi language preference
"When it comes to Hindi/cultural references in posts, do you prefer:
  (a) Transliteration — Holi ki shubhkamnayein (recommended default)
  (b) Devanagari script — होली की शुभकामनाएं
  (c) English only"
→ Maps to: voice_profile.hindi_style in pr_manager.md
→ Default if skipped: transliteration

### PRQ5: Topics to target or avoid
"Any topics you specifically want to post about or specifically avoid?"
→ Accept: free text
→ Add to `state/pr_manager.md → Voice Profile Overrides` table
→ Skip allowed: "We'll tune this over time based on what you like."

### Post-Bootstrap Action
After all 5 questions:
1. Write bootstrap answers to `state/pr_manager.md → Voice Profile Overrides` section
2. Set `enhancements.pr_manager: true` in `config/artha_config.yaml`
3. Enable PAT-PR-001 and PAT-PR-002 in `config/patterns.yaml` (set `enabled: true`)
4. Confirm: "PR Manager activated! During your next catch-up, I'll start surfacing
   content opportunities. Use /pr anytime to see your content calendar."
5. Run health check: `python3 scripts/pr_manager.py --check`
