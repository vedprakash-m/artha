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

## Phase 2 — First Integration (~2 minutes)

### Q6: Gmail Setup
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
