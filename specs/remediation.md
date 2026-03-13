# Artha — Remediation & Standardization Plan

> **Version:** 1.0  
> **Date:** 2025-07-17  
> **Status:** Draft for review  
> **Purpose:** Comprehensive critical review + actionable execution plan to transform Artha from a single-user deployed system into a distributable, secure, customizable Personal Intelligence OS — without breaking the currently deployed instance.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What's Brilliant — Strengths to Preserve](#2-whats-brilliant)
3. [What's Broken — Critical Issues](#3-whats-broken)
4. [Spec-Code Gap Analysis](#4-spec-code-gap-analysis)
5. [Prompt Genericization Plan (Blocker #1)](#5-prompt-genericization-plan)
6. [Unified Pipeline Architecture (Blocker #2)](#6-unified-pipeline-architecture)
7. [Instruction File Modularization](#7-instruction-file-modularization)
8. [Config Layer Consolidation](#8-config-layer-consolidation)
9. [Security & Privacy Non-Negotiables](#9-security--privacy-non-negotiables)
10. [Risk Register](#10-risk-register)
11. [Trade-Off Matrices](#11-trade-off-matrices)
12. [Revised Execution Blueprint](#12-revised-execution-blueprint)
13. [Extensibility, Customization & Adoption](#13-extensibility-customization--adoption)
14. [Per-Spec Critique](#14-per-spec-critique)
15. [Forward-Looking Maintainability](#15-forward-looking-maintainability)
16. [Appendix: Prompt Audit Detail](#a-prompt-audit-detail)
17. [Appendix: Script Audit Detail](#b-script-audit-detail)

---

## 1. Executive Summary

Artha is architecturally exceptional — a serverless, CLI-agnostic, prompt-driven personal OS that runs entirely on user-controlled infrastructure. The core abstractions (prompts-as-logic, markdown-as-state, AI-CLI-as-runtime) are sound and differentiated. Approximately **60-70% of the standardization work outlined in `specs/standardization.md` is already implemented** in the codebase.

However, two critical blockers prevent distribution:

1. **13 of 17 domain prompts contain hardcoded PII** — family names, school names, locations, cultural context, employer details. This is the single largest obstacle.
2. **7 fetch scripts duplicate ~2,100 lines** of shared infrastructure (retry, HTML stripping, output formatting, health checks, CLI args). This creates maintenance burden and inconsistency risk.

The path forward requires **surgical precision** — genericize prompts, extract shared pipeline infrastructure, modularize the instruction file, and consolidate the config layer — all without breaking the currently deployed system for the existing user.

### The Golden Rule

> Every change must pass the strangler-fig test: the new code works alongside the old code, the old code continues to function, and the migration is incremental. No big-bang rewrites.

---

## 2. What's Brilliant

### 2.1 Architecture

| Strength | Evidence |
|----------|----------|
| **No server, no daemon** | Entire system is pull-based. User triggers "catch me up" → AI CLI executes 21-step workflow. Zero attack surface when idle. |
| **CLI-agnostic** | Loader files (CLAUDE.md, GEMINI.md, AGENTS.md, .github/copilot-instructions.md) all point to single `config/Artha.md`. Works with Claude Code, Gemini CLI, Copilot CLI. |
| **Prompts-as-logic** | Domain extraction rules, alert thresholds, and briefing formats live in `prompts/*.md` — editable, versionable, no compilation step. |
| **Markdown-as-state** | 29 state files are human-readable, diffable, and version-controllable. No database required. |
| **Feature flags** | `settings.md` controls domain enablement, integration toggles, and system behavior without code changes. |

### 2.2 Security

| Layer | Implementation | Quality |
|-------|----------------|---------|
| **Layer 1: Regex guard** | `pii_guard.py` — pattern-based detection for SSNs, credit cards, DOBs, addresses, phone numbers | ★★★★☆ |
| **Layer 2: AI semantic** | LLM reviews its own output for semantic PII leakage | ★★★★☆ |
| **Layer 3: Outbound filter** | `safe_cli.py` — wraps CLI invocations, blocks dangerous flags, prevents exfiltration | ★★★★☆ |
| **Encryption at rest** | `vault.py` + `age` encryption for sensitive state files (finance, health, immigration, etc.) | ★★★★★ |
| **Credential isolation** | System keyring via `keyring` library, never stored in files | ★★★★★ |
| **Net-Negative Write Guard** | AI must not silently delete content from state files | ★★★★☆ |

### 2.3 Production-Quality Scripts

The fetch scripts demonstrate excellent engineering:

- **Retry with exponential backoff**: Handles 429, 500-504, quota exhaustion
- **JSONL output schema**: Machine-parseable, one record per line, stderr-only logging
- **Health checks**: `--health` flag verifies auth and connectivity without data fetch
- **Dry-run mode**: `--dry-run` in Outlook/iCloud scripts for safe testing
- **Venv re-exec**: `_bootstrap.py` ensures scripts always run in the correct venv
- **Body truncation**: 8000-char cap on email bodies, 500-char on calendar descriptions
- **Exit codes**: 0=success, 1=retryable error, 2=quota exhausted

### 2.4 Already-Shipped Standardization Work

These components from the standardization spec are **already implemented and working**:

| Component | Status | File(s) |
|-----------|--------|---------|
| Profile loader singleton | ✅ Done | `scripts/profile_loader.py` |
| Identity generation | ✅ Done | `scripts/generate_identity.py` |
| PII guard (regex) | ✅ Done | `scripts/pii_guard.py` |
| Safe CLI wrapper | ✅ Done | `scripts/safe_cli.py`, `scripts/safe_cli.sh` |
| Vault encryption | ✅ Done | `scripts/vault.py`, `scripts/vault.sh` |
| Bootstrap wizard | ✅ Done | `scripts/_bootstrap.py` |
| Demo catch-up flow | ✅ Done | `scripts/demo_catchup.py` |
| Local mail bridge | ✅ Done | `scripts/local_mail_bridge.py` |
| Migration tool | ✅ Done | `scripts/migrate.py` |
| Preflight checks | ✅ Done | `scripts/preflight.py` |
| Skill plugin framework | ✅ Done | `scripts/skill_runner.py`, `scripts/skills/base_skill.py` |
| User profile example | ✅ Done | `config/user_profile.example.yaml` |
| Routing example | ✅ Done | `config/routing.example.yaml` |
| Settings example | ✅ Done | `config/settings.example.md` |
| Quickstart guide | ✅ Done | `docs/quickstart.md` |
| Domain docs | ✅ Done | `docs/domains.md` |
| Security docs | ✅ Done | `docs/security.md` |
| Skills docs | ✅ Done | `docs/skills.md` |
| Troubleshooting | ✅ Done | `docs/troubleshooting.md` |
| Loader files | ✅ Done | `CLAUDE.md`, `GEMINI.md`, `AGENTS.md`, `.github/copilot-instructions.md` |

---

## 3. What's Broken

### 3.1 Critical Issues (Must Fix Before Distribution)

#### Issue C1: 13/17 Prompts Contain Hardcoded PII

**Severity:** 🔴 Critical — blocks distribution entirely

13 of 17 domain prompts still reference specific family names, school names, locations, employer names, cultural context (e.g., "Diwali", specific dietary preferences), and financial institutions by name. These prompts are the **logic layer** of Artha — they cannot be excluded from distribution.

| Prompt | PII Type | Specific Examples |
|--------|----------|-------------------|
| `kids.md` (455 lines) | Family names, school names, grade levels, activities | Child names, Canvas LMS URLs, specific schools |
| `finance.md` (539 lines) | Account types, institutions, employers | Specific banks, investment platforms, employer 401k |
| `social.md` (513 lines) | Cultural events, community names, relationships | Festival names, community organizations |
| `health.md` | Provider names, health conditions, medications | Specific doctors, pharmacies, conditions |
| `travel.md` | Destinations, loyalty programs, preferences | Airline/hotel accounts, specific destinations |
| `immigration.md` | Visa types, case numbers, filing dates | Specific visa categories, USCIS references |
| `insurance.md` | Policy numbers, coverage types, providers | Specific insurers, policy details |
| `vehicle.md` | Vehicle details, dealer info | Make/model/year, dealership names |
| `goals.md` | Personal aspirations, timelines | Career goals, financial targets |
| `learning.md` | Courses, certifications, skills | Specific platforms, course names |
| `comms.md` | Contact names, communication patterns | Named contacts, relationship context |
| `calendar.md` | Recurring event types, attendee patterns | Specific meeting types, people |
| `boundary.md` | Work-life boundaries, employer context | Company policies, manager names |

**Clean prompts** (4): `home.md`, `shopping.md`, `digital.md`, `estate.md`

**Impact:** Any user installing Artha receives prompts designed for a specific family's life. The AI will look for the wrong children's grades, monitor the wrong health conditions, and track the wrong financial accounts.

Full per-prompt audit in [Appendix A](#a-prompt-audit-detail).

#### Issue C2: Instruction File Too Large (1,942 Lines)

**Severity:** 🔴 Critical — causes unreliable execution

`config/Artha.md` is 1,942 lines. Most LLMs have diminishing attention beyond ~1,000 lines of instruction text. Steps 15-21 of the catch-up workflow, the slash commands, and the briefing format specifications are in the danger zone.

Additionally, §14 ("Phase 2B Features") describes **unimplemented features** (sentiment tracking, relationship scoring, decision intelligence) that the LLM may attempt to execute, producing hallucinated outputs.

**Evidence:** The split architecture (`Artha.identity.md` + `Artha.core.md`) already exists but the assembled `Artha.md` is still monolithic.

#### Issue C3: HTML Stripping Duplicated 4x

**Severity:** 🟡 High — maintenance and consistency risk

The `_HTMLStripper` class and `strip_html()` function are copy-pasted across:
- `scripts/gmail_fetch.py`
- `scripts/msgraph_fetch.py`
- `scripts/icloud_mail_fetch.py`
- `scripts/msgraph_onenote_fetch.py`

Each copy is ~40 lines. Thread footer removal regexes (~25 lines) are also duplicated across the 3 email fetch scripts. A bug fix in one copy may not propagate to others.

#### Issue C4: Config Layer Redundancy

**Severity:** 🟡 High — confusing for new users, causes drift

Identity data (names, emails, timezone, location) exists in **both** `config/user_profile.yaml` and `config/settings.md`. During onboarding, a new user must enter the same information twice, with no validation that they match.

After standardization, `user_profile.yaml` should be the **single source of truth** for all identity data. `settings.md` should contain **only** capabilities, system settings, domain configurations, and integration setup status.

#### Issue C5: `occasions.md` Contains Maximum PII

**Severity:** 🟡 High — data safety risk

`config/occasions.md` contains full dates of birth, wedding anniversary, car payment schedule, visa renewal dates, insurance coverage amounts, and school enrollment details. This file is not in the standard `.gitignore` pattern for user data.

**Fix:** Add `config/occasions.md` to `.gitignore`, add `.age` encryption wrapper, create `config/occasions.example.md` with fictional data.

### 3.2 Moderate Issues

#### Issue M1: Retry Logic Duplicated 6x

Retry with exponential backoff is implemented independently in 6 scripts with slight parameter variations (MAX_RETRIES ranges from 3-4, BASE_DELAY from 1.0-1.5s, MAX_DELAY from 30-60s). These should be configurable instances of a shared retry decorator.

#### Issue M2: No First-Run Detection

When a new user installs Artha and runs "catch me up," there's no mechanism to detect that state files are empty, prompts need configuration, and integrations haven't been set up. The system should gracefully detect cold-start conditions and route to the bootstrap wizard.

#### Issue M3: No `pyproject.toml`

Python packaging uses `scripts/requirements.txt` only. No `pyproject.toml` means:
- No version management
- No entry points for CLI commands
- No dependency groups (core vs. dev vs. optional)
- No editable install support

#### Issue M4: Skills `compare_fields` Not Abstract

In `scripts/skills/base_skill.py`, `compare_fields` is a regular `@property`, not `@abstractmethod`. Subclasses can silently omit it, causing delta detection to silently fail rather than raising an error at class definition time.

#### Issue M5: `noaa_weather.py` Has Misleading Comment

The skill has `email = "your-gmail@example.com"` with a comment claiming it reads from settings.md, but it never actually parses settings. The NOAA API requires a contact email for its User-Agent header — this should read from `profile_loader.get("family.primary_user.email")`.

#### Issue M6: Phase 2B Features Documented as Instructions

§14 of `Artha.md` describes future features (sentiment tracking, relationship scoring, pattern recognition, decision intelligence) in imperative language. LLMs may interpret these as current capabilities and attempt to execute them, producing hallucinated trend data.

**Fix:** Move §14 to a separate file (e.g., `config/roadmap.md`) not included in the assembled instruction file.

#### Issue M7: 3 Deprecated Scripts Contain Hardcoded Paths

`_mark_tasks_done.py`, `deep_mail_review.py`, `deep_mail_review2.py` contain hardcoded file paths and personal data. These appear to be one-off utilities that should be archived or deleted.

### 3.3 Production Incidents & Learnings

The following issues are documented from real operational failures observed during catch-up and dashboard sessions. Each failure class has a root cause and a concrete fix.

#### Issue M8: `pii_guard.py` Driver's License Coverage Gap *(Security — Evidenced in Production)*

**Severity:** 🟡 High — PII reached git pre-commit hook undetected by the primary guard

`pii_guard.py` currently has two DL detection patterns:
1. **WA-state specific:** Matches `WDL` + 9 alphanumeric characters — covers Washington state only
2. **Context-dependent generic:** Matches `driver's license: XXXXX` or `DL: XXXXX` label-prefixed formats

Any bare DL number without a label, or a number from a state with a different structural pattern, is silently passed. The other 49 states each have a unique format (e.g., California: `A` + 7 digits; Texas: 8 digits; New York: 9 digits; Florida: `A` + 12 alphanumeric). None of these are caught.

**Production incident:** A driver's license number stored in `state/kids.md` passed through `pii_guard.py --strict` undetected and was only caught by the git pre-commit hook. Relying on the git hook as the final backstop is insufficient — by that point the data has already been processed, potentially logged, and shown in AI CLI output.

**Root cause:** The AAMVA (American Association of Motor Vehicle Administrators) standard defines DL formats per state, but `pii_guard.py` only implements the WA-specific structural pattern. The generic pattern requires explicit labeling that real-world data often omits.

**Fix:** Extend `pii_guard.py` with AAMVA-compliant regex patterns for all 50 state DL formats, plus a heuristic for bare alphanumeric strings that match the length + character class signature of DL numbers when found near known DL-related keywords ("license", "state ID", "identification").

```python
# Example patterns to add to pii_guard.py:
# California: A + 7 digits
re.compile(r"\bA\d{7}\b"),
# Texas: 8 digits
re.compile(r"\b\d{8}\b"),  # NOTE: only in PII context (near DOB, name, etc.)
# Florida: A + 12 alphanumeric  
re.compile(r"\b[A-Z]\d{12}\b"),
# New York: 9 digits OR 3 letters + 6 digits
re.compile(r"\b[A-Z]{3}\d{6}\b|\b\d{9}\b"),  # context-gated
```

Note: bare digit sequences risk false positives. Apply them only within a ±2-line window of PII-indicator words. Add test cases for each new pattern.

#### Issue M9: Venv Not Universally Enforced — Utility Scripts Run Against System Python

**Severity:** 🟡 Medium — causes runtime failures in ad-hoc and AI-generated scripts

`_bootstrap.py` venv re-exec is present in the production fetch scripts but **absent from utility and operational scripts** (dashboard generation, data analysis, profile operations, etc.). When the AI CLI invokes a utility script — especially one it generates on the fly — it runs against the system Python, which is missing `yaml`, `keyring`, and other project dependencies.

**Production incident:** Dashboard generation failed on the first two attempts with `ModuleNotFoundError: No module named 'yaml'`. Resolution required manually identifying and activating the project venv before re-running.

**Compounding issue — two-venv ambiguity:** The project documents `~/.artha-venvs/.venv` as the canonical venv path, but the workspace also contains a local `.venv` at the repo root. Depending on how the session was started, different venvs may be active. This inconsistency caused confusion about which environment to use.

**Fix:**
1. All scripts that `import yaml`, `import keyring`, or use any project dependency must include the `_bootstrap.py` re-exec guard
2. Establish ONE canonical venv path — recommend the local `{ARTHA_DIR}/.venv` over `~/.artha-venvs/.venv` for portability (the venv travels with the workspace on OneDrive)
3. Add a `scripts/lib/bootstrap_guard.py` that can be imported as a one-liner at the top of any utility script
4. Document the venv decision in `config/settings.md` and `docs/quickstart.md`

```python
# One-liner guard for any utility script (proposed scripts/lib/bootstrap_guard.py):
from scripts._bootstrap import reexec_in_venv; reexec_in_venv()  # noqa: E702
```

#### Issue M10: Shell Invocation Fragility — No Stable Script Entry Points

**Severity:** 🟡 Medium — causes repeated retry loops in AI CLI sessions

When the AI CLI needs to run a Python script with complex arguments or multi-line logic, it must either (a) write the code to a temp file and execute it, or (b) pass Python code directly via shell. Option (b) fails systematically due to nested quote conflicts and backtick interpretation by bash/zsh. This forces 2-3 failed attempts before the AI CLI adapts its approach.

**Production incident:** Dashboard generation failed in attempts 1 and 2 with `unexpected EOF` and `syntax error` from bash misinterpreting the multi-line Python passed to the shell command tool. The AI CLI only succeeded on attempt 3 after writing the script to `tmp/dashboard.py` first — an approach it should have used from the start but had no documented convention for.

**Root cause:** No stable `artha <command>` entry point exists. Every operation requires knowing the exact Python invocation, the correct venv path, and the correct working directory.

**Fix:**
1. `pyproject.toml` entry points mapping named commands to script functions (e.g., `artha-dashboard`, `artha-catchup`, `artha-health`)
2. A `scripts/artha` runner shim that activates the venv and delegates to named commands with pass-through arguments
3. Convention: all AI CLI-generated scripts should write to `tmp/` and execute from there — document this in the system prompt (§6 of `Artha.core.md`)

```bash
# Proposed scripts/artha runner pattern:
#!/usr/bin/env bash
source "$(dirname "$0")/../.venv/bin/activate"
python -m scripts."$1" "${@:2}"
```

---

## 4. Spec-Code Gap Analysis

The standardization spec (`specs/standardization.md`, ~2,700 lines) plans 19 sections of work across 5 phases. Here is the actual vs. planned status:

### 4.1 Already Done (~60-70%)

| Spec Section | What It Plans | What Already Exists | Gap |
|-------------|---------------|---------------------|-----|
| §1 File Structure | Directory layout | Already matches spec layout | None |
| §3 Profile Loader | Singleton YAML access | `profile_loader.py` — full implementation with LRU cache, dot-notation, type hints | None |
| §4 Identity Generation | Template → identity.md | `generate_identity.py` with `--validate`, `--with-routing` | None |
| §5 Bootstrap Wizard | Interactive setup | `_bootstrap.py` exists | Minor: missing first-run detection |
| §7 PII Guard | Regex + AI scanning | `pii_guard.py` + `safe_cli.py` + `vault.py` | Minor: no automated CI hook |
| §8 Preflight Checks | Pre-run validation | `preflight.py` with health/auth checking | None |
| §14 Documentation | Quickstart, domains, security | 5 docs in `docs/` directory | Minor: no architecture doc |
| §15 Example Configs | Fictional family examples | `user_profile.example.yaml`, `routing.example.yaml`, `settings.example.md` | None |
| §17 Demo Mode | Safe demo workflow | `demo_catchup.py` | None |
| §18 Migration | In-place upgrade tool | `migrate.py` with state version tracking | None |

### 4.2 Partially Done (~20%)

| Spec Section | What It Plans | Current State | Remaining Work |
|-------------|---------------|---------------|----------------|
| §2 Settings Split | Separate identity from capabilities | `user_profile.yaml` + `settings.md` both exist but have overlapping data | Deduplicate: remove identity from settings.md |
| §6 Prompt Genericization | Template variables in all prompts | 4/17 clean, 13/17 still hardcoded | Genericize 13 remaining prompts |
| §9 Instruction Modularization | Split Artha.md into sections | Split exists (identity.md + core.md) but assembled file is still 1,942 lines | Further modularize into workflow.md, commands.md, format.md |
| §16 Skill Framework | BaseSkill + runner | Exists and works but `compare_fields` isn't abstract, `noaa_weather` has issues | Minor fixes |

### 4.3 Not Started (~10-20%)

| Spec Section | What It Plans | Status | Priority |
|-------------|---------------|--------|----------|
| §10 CI/CD Pipeline | GitHub Actions for PII scanning, testing | Not started | Medium — can ship without it initially |
| §11 Python Packaging | pyproject.toml, entry points | Not started | Medium |
| §12 Test Framework | pytest suite for all scripts | Tests dir exists but appears minimal | Medium |
| §13 Changelog Management | Automated changelog from commits | CHANGELOG.md exists but manual | Low |
| §19 Unified Fetch Pipeline | Common fetch infrastructure | Not started — see §6 of this doc | High |

### 4.4 Key Insight

The standardization spec reads as if starting from scratch, but the codebase is **significantly ahead** of what the spec assumes. The remaining work is narrower and more surgical than the spec implies. The execution plan in this document (§12) reflects the actual remaining delta.

---

## 5. Prompt Genericization Plan (Blocker #1)

This is the **single most important task** for distribution readiness. Without it, every user receives prompts written for a specific family.

### 5.1 Strategy: Template Variables

Every hardcoded personal reference becomes a `{{variable}}` placeholder that `generate_identity.py` resolves at install time from `user_profile.yaml`.

**Example transformation (kids.md):**

```markdown
# Before (hardcoded)
Monitor Canvas LMS grades for Arjun (10th grade, Advanced Math, AP Physics)
and Priya (7th grade, Pre-Algebra, Life Science). Alert if any grade drops
below B+ or assignment is missing for more than 2 days.

# After (genericized)  
{{#each children}}
Monitor {{#if this.lms}}{{this.lms.platform}} grades for {{this.name}}
({{this.grade_level}}{{#if this.courses}}, {{join this.courses ", "}}{{/if}}).
{{/if}}
Alert if any grade drops below {{grade_alert_threshold | default: "B+"}} or
assignment is missing for more than {{missing_assignment_days | default: 2}} days.
{{/each}}
```

### 5.2 Template Engine Choice

| Option | Pros | Cons | Recommendation |
|--------|------|------|----------------|
| **Jinja2** | Mature, well-known, powerful | Heavy dependency, overkill for variable substitution | ❌ Too complex |
| **Python `str.format`** | Zero dependency | No conditionals, no iteration | ❌ Too limited |
| **Mustache/Chevron** | Logic-less, simple, `chevron` is 1-file lib | No complex iteration | ⚠️ Acceptable |
| **Custom `{{var}}` resolver** | Zero dependency, fits existing `generate_identity.py` pattern | Must handle `#each`, `#if`, defaults | ✅ **Recommended** |

**Recommendation:** Extend `generate_identity.py` to handle `{{var}}`, `{{#each collection}}...{{/each}}`, `{{#if condition}}...{{/if}}`, and `{{var | default: value}}` syntax. This keeps the existing pattern, adds no dependencies, and covers all prompt genericization cases.

### 5.3 Prompt Genericization Priority

| Priority | Prompt | Lines | PII Density | Effort |
|----------|--------|-------|-------------|--------|
| P0 | `kids.md` | 455 | 🔴 Very High — names, schools, grades, activities | Large |
| P0 | `finance.md` | 539 | 🔴 Very High — accounts, employers, institutions | Large |
| P0 | `health.md` | ~200 | 🔴 Very High — providers, conditions, medications | Medium |
| P0 | `immigration.md` | ~150 | 🔴 Very High — visa types, case numbers | Medium |
| P1 | `social.md` | 513 | 🟡 High — cultural events, community names | Medium |
| P1 | `insurance.md` | ~200 | 🟡 High — policy numbers, coverage details | Medium |
| P1 | `vehicle.md` | ~150 | 🟡 High — vehicle details, dealer info | Small |
| P1 | `comms.md` | ~150 | 🟡 Medium — contact names, patterns | Small |
| P2 | `goals.md` | ~200 | 🟡 Medium — personal aspirations | Small |
| P2 | `calendar.md` | ~150 | 🟡 Medium — event types, attendees | Small |
| P2 | `travel.md` | ~200 | 🟡 Medium — loyalty programs, destinations | Small |
| P2 | `learning.md` | ~150 | 🟡 Low-Medium — courses, certifications | Small |
| P2 | `boundary.md` | ~100 | 🟡 Low-Medium — employer context | Small |

### 5.4 user_profile.yaml Extensions

To fully genericize all prompts, `user_profile.yaml` needs these additional sections:

```yaml
# Required additions for full prompt genericization:

family:
  children:
    - name: ""
      grade_level: ""
      school: ""
      lms:
        platform: "canvas"  # or "google_classroom", "schoology"
        url: ""
        courses: []
      activities: []           # NEW
      health_notes: []         # NEW

health:
  providers: []                # NEW: list of {name, specialty, phone}
  pharmacy: ""                 # NEW
  conditions: []               # NEW: list of tracked conditions
  medications: []              # NEW: list of current medications

finance:
  institutions: []             # NEW: list of {name, type, accounts: []}
  employer_benefits:           # NEW
    retirement_plan: ""
    hsa: false
    fsa: false

insurance:
  policies: []                 # NEW: list of {type, provider, policy_number}

vehicles: []                   # NEW: list of {year, make, model, vin, dealer}

immigration:
  status: ""                   # NEW: visa type or citizen
  cases: []                    # NEW: list of {type, receipt_number, status}

social:
  cultural_events: []          # NEW: list of {name, month, type}
  communities: []              # NEW: list of community/org names

travel:
  loyalty_programs: []         # NEW: list of {program, number, tier}
  home_airport: ""             # NEW
```

### 5.5 Migration Path

1. **Add new sections** to `user_profile.yaml` with empty defaults
2. **Add corresponding sections** to `user_profile.example.yaml` with fictional data
3. **Genericize prompts one at a time** — each prompt gets a PR/commit
4. **Run `generate_identity.py --validate`** after each change to verify resolution
5. **Test with both** the real profile and the example profile
6. **The existing user's prompts continue working** because their `user_profile.yaml` has the real data that fills the templates

### 5.6 Validation Rule

After genericization, the following command MUST pass:

```bash
# Verify no hardcoded PII leaks into genericized prompts
python scripts/pii_guard.py prompts/*.md --strict
# AND: verify all templates resolve without {{unresolved}} placeholders
python scripts/generate_identity.py --validate --check-prompts
```

---

## 6. Unified Pipeline Architecture (Blocker #2)

### 6.1 The Problem

Artha currently has **7 independent fetch scripts** that share ~70% of their code:

| Script | Type | Lines | Unique Logic |
|--------|------|-------|--------------|
| `gmail_fetch.py` | Email | ~350 | Gmail API, base64 MIME decoding |
| `msgraph_fetch.py` | Email | ~400 | MS Graph OData, Retry-After header, Teams detection |
| `icloud_mail_fetch.py` | Email | ~350 | IMAP protocol, RFC 3501 mailbox names |
| `gcal_fetch.py` | Calendar | ~250 | Google Calendar API, visibility flags |
| `msgraph_calendar_fetch.py` | Calendar | ~300 | MS Graph calendar, online meeting detection |
| `icloud_calendar_fetch.py` | Calendar | ~300 | CalDAV RFC 4791, VTODO reminders |
| `canvas_fetch.py` | LMS | ~200 | Canvas REST API, per-student profiles |

**Shared infrastructure duplicated across these scripts (~2,100 LOC):**

| Component | Copies | Lines Each | Total Waste |
|-----------|--------|------------|-------------|
| Retry with backoff | 6 | ~50 | ~250 |
| HTML stripping | 3-4 | ~40 | ~120 |
| Footer removal | 3 | ~25 | ~50 |
| JSONL output formatting | 7 | ~30 | ~180 |
| CLI argument parsing | 7 | ~40 | ~210 |
| Health check template | 7 | ~30 | ~180 |
| Auth dispatch | 4 | ~60 | ~180 |
| **Total** | | | **~1,170 LOC wasted** |

### 6.2 The Question

> "Should we be thinking of a standardized script/pipeline with extensible configuration for all potential sources serving variety of needs for variety of users?"

**Answer: Yes, but with careful scoping.** The right abstraction is a **shared infrastructure layer** (retry, HTML, output, health), NOT a monolithic "universal fetcher." Each source has legitimately unique protocol logic that shouldn't be forced into a common interface.

### 6.3 Recommended Architecture: Shared Library + Plugin Scripts

```
scripts/
├── lib/                          # NEW: shared infrastructure
│   ├── __init__.py
│   ├── retry.py                  # Configurable retry with backoff
│   ├── html_processing.py        # HTMLStripper + footer removal
│   ├── output.py                 # JSONL formatting + body truncation
│   ├── cli_base.py               # Common argparse groups
│   ├── health.py                 # Health check template
│   └── auth.py                   # Auth dispatcher (OAuth, keychain, token)
├── gmail_fetch.py                # Uses lib/ — only Gmail-specific logic remains
├── msgraph_fetch.py              # Uses lib/ — only MS Graph-specific logic
├── icloud_mail_fetch.py          # Uses lib/ — only IMAP-specific logic
├── gcal_fetch.py                 # Uses lib/ — only Google Calendar-specific logic
├── msgraph_calendar_fetch.py     # Uses lib/ — only MS Graph calendar logic
├── icloud_calendar_fetch.py      # Uses lib/ — only CalDAV-specific logic
├── canvas_fetch.py               # Uses lib/ — only Canvas-specific logic
└── ...
```

### 6.4 Shared Library Design

#### `lib/retry.py`

```python
"""Configurable retry with exponential backoff for Artha fetch scripts."""

import time
import logging
from functools import wraps
from typing import Set, Optional

RETRYABLE_CODES: Set[int] = {429, 500, 502, 503, 504}
RETRYABLE_PHRASES = ("rate limit", "quota", "throttl", "temporarily unavail")

def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_codes: Optional[Set[int]] = None,
):
    """Decorator for retry with exponential backoff.
    
    Respects Retry-After headers when present on HTTP responses.
    """
    codes = retryable_codes or RETRYABLE_CODES

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    
                    # Check if retryable
                    status = getattr(exc, 'status_code', getattr(exc, 'code', None))
                    msg = str(exc).lower()
                    is_retryable = (
                        (status and status in codes) or
                        any(phrase in msg for phrase in RETRYABLE_PHRASES)
                    )
                    if not is_retryable:
                        break
                    
                    # Calculate delay (respect Retry-After if available)
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    retry_after = getattr(exc, 'retry_after', None)
                    if retry_after and isinstance(retry_after, (int, float)):
                        delay = min(float(retry_after), max_delay)
                    
                    logging.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed: {exc}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            
            raise type(last_exc)(
                f"Failed after {max_retries + 1} attempts: {last_exc}"
            ) from last_exc
        return wrapper
    return decorator
```

#### `lib/html_processing.py`

```python
"""Shared HTML stripping and email footer removal for Artha fetch scripts."""

import re
from html import unescape
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "head", "meta", "noscript"}
_BLOCK_TAGS = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "hr", "section", "article"}

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS and self._pieces:
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        raw = unescape(raw)
        # Collapse 3+ blank lines to 2
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def strip_html(html_content: str) -> str:
    """Convert HTML to readable plain text. Falls back to regex on parse failure."""
    if not html_content:
        return ""
    try:
        stripper = _HTMLStripper()
        stripper.feed(html_content)
        return stripper.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_content).strip()


# Footer / reply separator patterns
_FOOTER_PATTERNS = [
    r"^-{2,}\s*$",                                    # -- separator
    r"^On .+ wrote:$",                                # Gmail reply header
    r"^From:.+\nSent:.+\nTo:.+\nSubject:",           # Outlook reply header
    r"^>{1,3}\s",                                      # Quoted text
    r"Sent from my (iPhone|iPad|Galaxy|Android)",     # Mobile signatures
    r"Get Outlook for (iOS|Android)",                 # Outlook mobile
    r"This email and any attachments",                # Legal disclaimers
    r"CONFIDENTIALITY NOTICE",                        # Legal notices
    r"(Unsubscribe|unsubscribe|opt.out|click here to)",  # Marketing
    r"Microsoft Teams meeting",                       # Teams invite blocks
]
_FOOTER_RE = re.compile("|".join(f"({p})" for p in _FOOTER_PATTERNS), re.MULTILINE)


def strip_footers(text: str) -> str:
    """Remove reply separators, mobile signatures, and legal disclaimers."""
    if not text:
        return ""
    match = _FOOTER_RE.search(text)
    if match:
        return text[:match.start()].rstrip()
    return text
```

#### `lib/output.py`

```python
"""JSONL output formatting with consistent field ordering and truncation."""

import json
import sys
from typing import Any

MAX_BODY_LENGTH = 8000
MAX_DESCRIPTION_LENGTH = 500


def emit_jsonl(record: dict[str, Any], *, stream=None) -> None:
    """Write a single JSONL record to stdout (or specified stream)."""
    out = stream or sys.stdout
    out.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
    out.flush()


def truncate_body(text: str, max_length: int = MAX_BODY_LENGTH) -> str:
    """Truncate text to max_length, appending indicator if truncated."""
    if not text or len(text) <= max_length:
        return text or ""
    return text[:max_length] + "\n[…truncated]"


def truncate_description(text: str, max_length: int = MAX_DESCRIPTION_LENGTH) -> str:
    """Truncate calendar description to max_length."""
    return truncate_body(text, max_length)
```

#### `lib/cli_base.py`

```python
"""Common CLI argument groups for Artha fetch scripts."""

import argparse
from datetime import datetime, timezone, timedelta


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add --health and --reauth flags shared by all fetch scripts."""
    parser.add_argument("--health", action="store_true",
                        help="Verify auth and connectivity, print summary, exit")
    parser.add_argument("--reauth", action="store_true",
                        help="Force re-authentication")


def add_email_args(parser: argparse.ArgumentParser) -> None:
    """Add email-specific arguments: --since, --max-results, --folder, --dry-run."""
    add_common_args(parser)
    parser.add_argument("--since", type=str,
                        default=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                        help="Fetch emails since this ISO datetime")
    parser.add_argument("--max-results", type=int, default=50,
                        help="Maximum number of emails to fetch")
    parser.add_argument("--folder", type=str, default="INBOX",
                        help="Mail folder to fetch from")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be fetched without fetching")


def add_calendar_args(parser: argparse.ArgumentParser) -> None:
    """Add calendar-specific arguments: --from, --to, --today-plus-days, --list-calendars."""
    add_common_args(parser)
    today = datetime.now(timezone.utc).date()
    parser.add_argument("--from", dest="from_date", type=str,
                        default=today.isoformat(),
                        help="Start date (ISO format)")
    parser.add_argument("--to", type=str,
                        default=(today + timedelta(days=7)).isoformat(),
                        help="End date (ISO format)")
    parser.add_argument("--today-plus-days", type=int,
                        help="Override --to with today + N days")
    parser.add_argument("--list-calendars", action="store_true",
                        help="List available calendars and exit")
```

### 6.5 Migration Strategy: Strangler Fig

The migration MUST be incremental. Each script is migrated independently:

1. **Extract** shared code into `scripts/lib/` (new module, no changes to existing scripts)
2. **Migrate one script** (start with `gmail_fetch.py` as the template)
3. **Run both versions** in parallel to verify output equivalence
4. **Replace** the original script with the refactored version
5. **Repeat** for each remaining script, one at a time
6. **Delete** duplicated code from `lib/` candidates only after all scripts are migrated

**Critical constraint:** Each migrated script MUST produce **byte-identical JSONL output** to the original. The catch-up workflow's prompt parsing depends on the exact field names and ordering.

### 6.6 What NOT to Unify

Some code looks duplicated but serves **legitimately different purposes**:

| Component | Why It Stays Separate |
|-----------|----------------------|
| **Auth flows** | OAuth PKCE (Google), OAuth client-credentials (MS Graph), IMAP login (iCloud), API key (Canvas) are fundamentally different protocols |
| **API pagination** | Google uses `nextPageToken`, MS Graph uses `@odata.nextLink`, IMAP uses `SEARCH` then `FETCH` — different iteration models |
| **Response parsing** | Gmail returns base64 MIME payloads, Outlook returns JSON, IMAP returns RFC 2822 — different deserialization per source |
| **Canvas fetch** | Writes state files directly (not JSONL), uses `profile_loader` — architecturally different from email/calendar fetch |

**Rule of thumb:** Unify infrastructure (retry, output, HTML, CLI args, health checks). Preserve source-specific protocol logic.

### 6.7 Future Source Extensibility

The `scripts/lib/` architecture makes it trivial for users to add new data sources:

```python
#!/usr/bin/env python3
"""Fetch emails from a new provider using Artha's shared infrastructure."""

import argparse
from scripts.lib.retry import with_retry
from scripts.lib.html_processing import strip_html, strip_footers
from scripts.lib.output import emit_jsonl, truncate_body
from scripts.lib.cli_base import add_email_args

def main():
    parser = argparse.ArgumentParser(description="Fetch from MyProvider")
    add_email_args(parser)
    args = parser.parse_args()
    
    if args.health:
        # Provider-specific health check
        ...
        return
    
    for message in fetch_from_my_provider(args):
        body = strip_footers(strip_html(message["html_body"]))
        emit_jsonl({
            "source": "my_provider",
            "subject": message["subject"],
            "body": truncate_body(body),
            # ... standard fields
        })

if __name__ == "__main__":
    main()
```

A new user adding their own provider writes ~50 lines of source-specific code instead of ~350 lines of mostly-duplicated infrastructure.

---

## 7. Instruction File Modularization

### 7.1 The Problem

`config/Artha.md` at 1,942 lines exceeds reliable LLM attention span. The current split (`Artha.identity.md` + `Artha.core.md`) is a good start but doesn't address the core size problem.

### 7.2 Proposed Split

```
config/
├── Artha.md                    # Assembled output (identity + core + active modules)
├── Artha.identity.md           # §1: Generated from user_profile.yaml (EXISTS)
├── Artha.core.md               # §2-§5: Core workflow, routing, privacy (EXISTS)
├── Artha.commands.md            # NEW: §6-§8: Slash commands, goal sprints
├── Artha.formats.md             # NEW: §9-§11: Briefing templates, formatting rules
├── Artha.advanced.md            # NEW: §12-§13: Advanced features (dashboard, scenarios)
└── roadmap.md                   # NEW: §14: Future features (NOT included in assembly)
```

### 7.3 Assembly Logic

`generate_identity.py` already assembles `Artha.md`. Extend it to:

1. Always include: `Artha.identity.md` + `Artha.core.md`
2. Conditionally include: `Artha.commands.md`, `Artha.formats.md`, `Artha.advanced.md` based on `settings.md` feature flags
3. Never include: `roadmap.md`

This keeps the assembled file under ~1,200 lines for typical configurations.

### 7.4 Risk Mitigation

- **If a CLI can only load one file:** Assembly continues to produce a single `Artha.md`
- **If assembly breaks:** Individual files are valid markdown and can be manually concatenated
- **Backward compatibility:** The assembled output is identical to the current monolith — no behavior change

---

## 8. Config Layer Consolidation

### 8.1 Current State

| File | Purpose | Issue |
|------|---------|-------|
| `user_profile.yaml` | Identity, family, locations, integrations | ✅ Correct scope |
| `settings.md` | Feature flags, system settings, domain configs | ❌ Also contains identity data |
| `artha_config.yaml` | Microsoft To Do list IDs | ⚠️ Could merge into user_profile.yaml |
| `occasions.md` | Birthdays, anniversaries, milestones | ❌ Maximum PII, not properly excluded |
| `contacts.md.age` | Contact directory | ✅ Encrypted |
| `skills.yaml` | Skill cadence configs | ✅ Correct scope |
| `routing.example.yaml` | Route rules template | ✅ Correct scope |

### 8.2 Target State

| File | Contains After Migration |
|------|--------------------------|
| `user_profile.yaml` | ALL identity data, family details, integrations config, To Do list IDs |
| `settings.md` | Feature flags, system behavior, domain enable/disable, setup checklist ONLY |
| `occasions.md` | Stays as-is BUT gets `.gitignore` entry + example file |
| `artha_config.yaml` | **Deprecated** — merge To Do list IDs into `user_profile.yaml` under `integrations.microsoft_todo.lists` |

### 8.3 Migration Steps

1. Add To Do list IDs to `user_profile.yaml` under `integrations.microsoft_todo.lists`
2. Update `todo_sync.py` to read from `profile_loader.get("integrations.microsoft_todo.lists")` instead of `artha_config.yaml`
3. Remove identity sections from `settings.md`
4. Add `config/occasions.md` to `.gitignore`
5. Create `config/occasions.example.md` with fictional dates
6. Deprecate `artha_config.yaml` with a comment pointing to `user_profile.yaml`

---

## 9. Security & Privacy Non-Negotiables

These are **absolute requirements** that cannot be traded off for convenience or simplicity:

### 9.1 The Three Laws

1. **No PII in committed files.** Every committed file must pass `pii_guard.py --strict`. No exceptions.
2. **Credentials never in files.** All secrets live in system keyring (`keyring` library) or age-encrypted vaults. Never in YAML, markdown, or environment variables committed to repo.
3. **Outbound filtering always active.** `safe_cli.py` must wrap all CLI invocations. No raw `subprocess` calls in user-facing scripts.

### 9.2 Defense-in-Depth Layers

```
Layer 0: .gitignore             → Prevents accidental commits of user data
Layer 1: pii_guard.py (regex)   → Catches structural PII (SSN, CC, DOB patterns)
                                   ⚠️  KNOWN GAP: DL detection is WA-specific + label-dependent
                                      Bare DL numbers from other 49 states are missed (see Issue M8)
Layer 2: AI semantic review     → Catches contextual PII the regex misses
Layer 3: safe_cli.py            → Blocks dangerous CLI flags, URL exfiltration
Layer 4: vault.py + age         → Encrypts sensitive state files at rest
Layer 5: keyring                → Isolates credentials from file system
Layer 6: git pre-commit hook    → Last-resort catch before commit (must NOT be the primary guard)
```

### 9.3 Pre-Distribution Checklist

- [ ] All 17 prompts pass `pii_guard.py --strict`
- [ ] `config/occasions.md` in `.gitignore`
- [ ] `config/occasions.example.md` exists with fictional data
- [ ] No hardcoded file paths in any committed script
- [ ] `user_profile.example.yaml` contains no real data
- [ ] `settings.example.md` contains no real data
- [ ] All `.age` files are in `.gitignore`
- [ ] `state/` directory is in `.gitignore` (user data)
- [ ] `tmp/` directory is in `.gitignore`
- [ ] No API keys, tokens, or secrets in any committed file
- [ ] `safe_cli.py` has tests for all blocked patterns
- [ ] `pii_guard.py` has tests for all detection patterns
- [ ] `pii_guard.py` DL patterns cover all 50 state AAMVA formats, not just WA (see Issue M8)
- [ ] `pii_guard.py` test suite includes bare CA, TX, NY, FL DL formats as positive cases
- [ ] All scripts with project dependencies include `_bootstrap.py` venv re-exec guard (see Issue M9)
- [ ] Single canonical venv path documented and enforced (local `.venv` vs `~/.artha-venvs/.venv`)
- [ ] `pyproject.toml` entry points defined for all user-facing commands (see Issue M10)
- [ ] CI/CD runs `pii_guard.py --strict` on every PR

---

## 10. Risk Register

| ID | Risk | Severity | Likelihood | Impact | Mitigation |
|----|------|----------|------------|--------|------------|
| R1 | Prompt genericization breaks existing user's catch-up workflow | 🔴 Critical | Medium | User's daily workflow stops working | Strangler-fig: keep originals until templates verified with real data |
| R2 | Template engine adds too much complexity to prompts | 🟡 High | Medium | Prompts become hard to edit for non-technical users | Use simplest possible syntax (just `{{var}}`), avoid complex logic in templates |
| R3 | Shared lib/ changes break a specific fetch script | 🟡 High | Medium | One data source stops working | Output equivalence tests for each script before/after migration |
| R4 | Instruction file modularization confuses LLM | 🟡 High | Low | Catch-up workflow degrades | Assembly still produces single file — LLM never sees the splits |
| R5 | New user profile fields create YAML complexity | 🟡 Medium | Medium | Onboarding friction increases | Extensive comments in example file, bootstrap wizard handles field population |
| R6 | PII leaks through template logic errors | 🔴 Critical | Low | Private data exposed in committed files | CI gate: `pii_guard.py --strict` blocks PR merge if PII detected |
| R7 | Cross-platform path issues in lib/ modules | 🟡 Medium | Medium | Scripts fail on Windows | Use `pathlib.Path` everywhere, test on Windows CI runner |
| R8 | Retry parameter changes affect API rate limits | 🟡 Medium | Low | Provider throttling or blocks | Make retry params configurable per-source, document provider-specific limits |
| R9 | Config migration breaks To Do sync | 🟡 Medium | Low | Task sync stops working | Backward-compat shim: check both `artha_config.yaml` and `user_profile.yaml` |
| R10 | OneNote/Canvas scripts diverge from pipeline pattern | 🟢 Low | High | Inconsistent architecture | Accept divergence — these sources have legitimately different output patterns |
| R11 | Bare DL numbers from non-WA states bypass `pii_guard.py` | 🔴 Critical | High — **evidenced in production** | State ID data stored in state files and processed without redaction | Immediate: extend DL patterns (Task B7); interim: add CI gate that scans `state/kids.md` and `state/immigration.md` |  
| R12 | Utility scripts run against system Python → `ModuleNotFoundError` | 🟡 Medium | High — **evidenced in production** | AI CLI session retries, wasted time, potential incomplete output | Add venv guard to all scripts (Task B8); define single canonical venv path |

---

## 11. Trade-Off Matrices

### 11.1 Prompt Genericization Approach

| Approach | Complexity | Maintainability | User Experience | Risk | Verdict |
|----------|------------|-----------------|-----------------|------|---------|
| **A: Custom `{{var}}` resolver** | Low | High — simple Python, no deps | Good — familiar syntax | Low | ✅ **Chosen** |
| **B: Jinja2 templates** | Medium | Medium — powerful but complex | Poor — intimidating for non-devs | Medium | ❌ Overkill |
| **C: Manual prompt writing per user** | None | Low — every user writes from scratch | Poor — huge onboarding burden | High | ❌ Impractical |
| **D: Prompt marketplace** | High | Low — versioning nightmare | Great — community contributions | High | ❌ Future phase (Phase 3+) |

### 11.2 Fetch Script Consolidation

| Approach | LOC Reduction | Breaking Risk | Extensibility | Maintenance | Verdict |
|----------|--------------|---------------|---------------|-------------|---------|
| **A: Shared lib/ + existing scripts** | ~1,100 | Low — incremental | High — new sources easy | High — one place to fix | ✅ **Chosen** |
| **B: Abstract BaseFetcher class** | ~1,500 | Medium — forces interface | Medium — must extend class | Medium — inheritance complexity | ⚠️ Over-engineered |
| **C: Single universal_fetch.py** | ~1,800 | High — monolith risk | Low — config-driven only | Low — everything in one file | ❌ Wrong abstraction |
| **D: No change (status quo)** | 0 | None | Low — copy-paste for new sources | Low — fix bugs in 7 places | ❌ Technical debt grows |

### 11.3 Instruction File Strategy

| Approach | LLM Reliability | User Editability | CLI Compat | Verdict |
|----------|-----------------|------------------|------------|---------|
| **A: Modular sources → assembled output** | High — controls size | High — edit small files | High — single output file | ✅ **Chosen** |
| **B: Single monolith (status quo)** | Declining — 1,942 lines | Medium — one search target | High | ❌ Growing problem |
| **C: Multiple instruction files loaded per CLI** | Variable — CLI dependent | High | Low — not all CLIs support | ❌ Portability risk |

### 11.4 Config Consolidation

| Approach | Simplicity | Migration Risk | User Experience | Verdict |
|----------|-----------|----------------|-----------------|---------|
| **A: Merge into user_profile.yaml** | High — one file for identity | Low — backward-compat shim | Good — one place to configure | ✅ **Chosen** |
| **B: Keep all 4 config files** | Low — scattered config | None | Poor — configure 4 files | ❌ Status quo friction |
| **C: Single unified config.yaml** | Highest — one file total | High — settings.md is markdown | Mixed — one file but complex | ❌ Different formats (YAML vs md) |

---

## 12. Revised Execution Blueprint

Based on the gap analysis (§4), here is the **minimal-cut execution plan** — only the work that actually remains.

### Phase A: Distribution Blocker Removal (Highest Priority)

**Goal:** Make it possible for another user to install and use Artha.

| Task | Effort | Dependencies | Acceptance Criteria |
|------|--------|-------------|---------------------|
| A1: Genericize P0 prompts (kids, finance, health, immigration) | Large | user_profile.yaml extensions | All 4 pass `pii_guard.py --strict` with example profile |
| A2: Genericize P1 prompts (social, insurance, vehicle, comms) | Medium | A1 (patterns established) | All 4 pass `pii_guard.py --strict` |
| A3: Genericize P2 prompts (goals, calendar, travel, learning, boundary) | Small | A1 (patterns established) | All 5 pass `pii_guard.py --strict` |
| A4: Extend `user_profile.yaml` + example file | Medium | None | Example file renders all templates without unresolved placeholders |
| A5: Extend `generate_identity.py` with template resolution | Medium | A4 | `--validate --check-prompts` passes |
| A6: Create `config/occasions.example.md` | Small | None | Fictional data, .gitignore updated |
| A7: Remove identity duplication from `settings.md` | Small | None | settings.md has zero personal data |
| A8: Move §14 (Phase 2B) to `config/roadmap.md` | Small | None | Unimplemented features not in instruction file |

### Phase B: Infrastructure Consolidation

**Goal:** Reduce maintenance burden and enable source extensibility.

| Task | Effort | Dependencies | Acceptance Criteria |
|------|--------|-------------|---------------------|
| B1: Create `scripts/lib/` with retry, html, output, cli_base modules | Medium | None | All modules have unit tests |
| B2: Migrate `gmail_fetch.py` to use lib/ | Small | B1 | Byte-identical JSONL output |
| B3: Migrate remaining 5 email/calendar scripts | Medium | B2 (template verified) | All produce identical output |
| B4: Fix `base_skill.py` — make `compare_fields` abstract | Small | None | Subclass without `compare_fields` raises TypeError |
| B5: Fix `noaa_weather.py` email sourcing | Small | None | Uses `profile_loader.get()` |
| B6: Delete/archive deprecated scripts | Small | None | `_mark_tasks_done.py`, `deep_mail_review*.py` removed |
| B7: Extend `pii_guard.py` with AAMVA multi-state DL patterns | Small | None | Bare CA/TX/NY/FL/IL DL formats detected; test suite has positive + negative cases for each state format added |
| B8: Add venv re-exec guard to all utility scripts | Small | None | Every script that imports project deps has `_bootstrap.py` guard; `python scripts/foo.py` from system Python auto-re-execs into project venv |

### Phase C: Instruction File Optimization

**Goal:** Ensure reliable LLM execution of all features.

| Task | Effort | Dependencies | Acceptance Criteria |
|------|--------|-------------|---------------------|
| C1: Split `Artha.core.md` into core + commands + formats + advanced | Medium | None | Each file < 500 lines |
| C2: Update `generate_identity.py` assembly to include module selection | Small | C1 | Assembled file < 1,200 lines for typical config |
| C3: Add first-run detection to catch-up workflow | Small | None | Empty state triggers bootstrap suggestion |

### Phase D: Packaging & Quality

**Goal:** Professional distribution packaging.

| Task | Effort | Dependencies | Acceptance Criteria |
|------|--------|-------------|---------------------|
| D1: Create `pyproject.toml` with CLI entry points | Small | None | `pip install -e .` works; `artha-dashboard`, `artha-health`, `artha-catchup` entry points defined and callable without venv prefix |
| D2: Add pytest suite for lib/ modules | Medium | B1 | `pytest` passes with >80% coverage on lib/ |
| D3: Add pytest suite for `pii_guard.py` | Small | None | All PII patterns have positive + negative tests, including all DL formats added in B7 |
| D4: Create GitHub Actions CI workflow | Medium | D2, D3 | PII scan + tests run on every PR |
| D5: Add architecture doc to `docs/` | Small | None | Covers prompts-as-logic, state model, security layers |
| D6: Create `scripts/artha` runner shim | Small | D1 | `./scripts/artha <command>` activates venv and runs correctly regardless of calling context; AI CLI can invoke any script via this shim without quoting failures |

### Phase E: Polish & Onboarding

**Goal:** Excellent first-run experience.

| Task | Effort | Dependencies | Acceptance Criteria |
|------|--------|-------------|---------------------|
| E1: Enhance bootstrap wizard with guided profile population | Medium | A4 | New user can complete setup in < 15 minutes |
| E2: Create `docs/CONTRIBUTING.md` | Small | None | Covers adding domains, skills, sources |
| E3: Create `docs/adding-a-source.md` | Small | B1 | Step-by-step guide using lib/ |
| E4: Update README.md for distribution | Small | All prior phases | Installation → first catch-up in < 20 minutes |

### Execution Order

```
Phase A ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ (critical path)
         A4 → A5 → A1 → A2 → A3
         A6, A7, A8 (parallel, no dependencies)

Phase B ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ (can start during Phase A)
         B1 → B2 → B3
         B4, B5, B6 (parallel, no dependencies)

Phase C ━━━━━━━━━━━━━ (can start during Phase B)
         C1 → C2
         C3 (parallel)

Phase D ━━━━━━━━━━━━━━━━━━━━━━━━ (after B1, can parallel with C)
         D1
         D2, D3 (parallel, after B1/existing code)
         D4 (after D2, D3)
         D5

Phase E ━━━━━━━━━━━━━━━━━ (after Phase A-D)
         E1, E2, E3, E4 (largely parallel)
```

---

## 13. Extensibility, Customization & Adoption

### 13.1 Extensibility Dimensions

Artha must be extensible along 5 axes without requiring code changes:

| Axis | How Users Extend | Mechanism |
|------|------------------|-----------|
| **Domains** | Add new life domains (e.g., "pets", "volunteering", "hobbies") | Create `prompts/domain.md`, add domain to `settings.md`, create `state/domain.md` |
| **Data sources** | Add email/calendar providers, APIs, local files | Create `scripts/new_source_fetch.py` using `lib/` modules |
| **Skills** | Add data fidelity checks (e.g., flight status, stock prices, school closures) | Create `scripts/skills/new_skill.py` extending `BaseSkill`, register in `skills.yaml` |
| **Briefing formats** | Customize output style (bullets vs. prose, priorities, grouping) | Edit `Artha.formats.md` or add format presets |
| **Alert rules** | Customize what triggers alerts (grade thresholds, balance warnings) | Edit domain prompt thresholds or add to `user_profile.yaml` |

### 13.2 User Personas & Customization Paths

| Persona | Configuration Complexity | Domains | Integrations |
|---------|--------------------------|---------|--------------|
| **Solo professional** | Minimal — work email + calendar | comms, calendar, finance, health | Gmail or Outlook |
| **Couple, no kids** | Low — two people, no school tracking | comms, calendar, finance, health, home, social | Gmail + shared calendar |
| **Family with kids** | Medium — school monitoring, activities | All domains | Gmail + Canvas/LMS + shared calendars |
| **Multi-platform user** | Medium — multiple email/calendar sources | Per user | Gmail + Outlook + iCloud |
| **Privacy-maximalist** | Low config, high security | Any | Local mail bridge (IMAP) only, all state encrypted |
| **Power user** | High — custom skills, domains, sources | Extended | Multiple sources + custom skills + custom domains |

### 13.3 Adoption Funnel

```
1. Install (git clone + venv setup)           → README + quickstart guide
2. Bootstrap (run /bootstrap)                 → Interactive wizard
3. First catch-up (run "catch me up")          → Demo mode if no integrations
4. Connect first source (e.g., Gmail OAuth)    → Per-source setup guide
5. Customize domains (enable/disable)          → settings.md toggles
6. Customize prompts (edit thresholds)         → Direct markdown editing
7. Add sources (new providers)                 → docs/adding-a-source.md
8. Add skills (custom data checks)             → docs/skills.md
9. Contribute back (PR with new domain/skill)  → CONTRIBUTING.md
```

### 13.4 Minimum Viable Installation

The absolute minimum for a working Artha installation:

1. `config/user_profile.yaml` — populated with name, email, timezone
2. `config/settings.md` — copied from example, domains toggled
3. `config/Artha.md` — assembled from templates
4. One data source connected (e.g., Gmail OAuth)
5. A supported AI CLI installed (Claude Code, Gemini CLI, or Copilot CLI)

Everything else (encryption, skills, advanced domains, multi-source) is additive.

---

## 14. Per-Spec Critique

### 14.1 PRD v4.1

| Aspect | Assessment |
|--------|------------|
| **Vision** | ★★★★★ — Exceptional. "Personal Intelligence OS" is precisely positioned. |
| **User stories** | ★★★★☆ — Comprehensive but could use acceptance criteria per story. |
| **Non-functional requirements** | ★★★☆☆ — Missing: performance targets (catch-up should complete in < N minutes), reliability targets (99.x% of catch-ups succeed), scalability targets (handles N emails in a batch). |
| **Success metrics** | ★★★☆☆ — "User adoption" is undefined. What constitutes adoption? Daily use? Weekly? What's the retention target? |
| **Competitor analysis** | ★★☆☆☆ — No comparison to existing personal dashboard tools (Notion, Obsidian, Capacities, Monica CRM). Understanding competitive positioning sharpens product decisions. |
| **Gap:** | Define quantitative success metrics and performance SLAs. |

### 14.2 Tech Spec v2.2

| Aspect | Assessment |
|--------|------------|
| **Architecture** | ★★★★★ — Pull-based, serverless, CLI-agnostic is brilliant. |
| **Security model** | ★★★★★ — Defense-in-depth with 5 layers is industry-grade. |
| **Data model** | ★★★★☆ — Markdown-as-state is elegant but lacks schema contracts (what fields MUST a state file have?). |
| **Error handling** | ★★★★☆ — Good exit code conventions but no circuit-breaker pattern for repeated API failures. |
| **Cross-platform** | ★★★☆☆ — OneDrive sync dependency limits Linux users. Alternative sync mechanisms (Syncthing, git) not addressed. |
| **Gap:** | Define state file schemas, add circuit-breaker for API calls, document alternative sync options. |

### 14.3 UX Spec v1.5

| Aspect | Assessment |
|--------|------------|
| **Briefing formats** | ★★★★★ — Flash/standard/deep tiers are well-designed. |
| **Slash commands** | ★★★★☆ — Comprehensive command set. Missing: command discovery (`/help` should list all commands with descriptions). |
| **Onboarding** | ★★★☆☆ — Bootstrap wizard exists but no guided first-run experience. New user gets dropped into a system designed for an existing user. |
| **Error UX** | ★★☆☆☆ — What does the user see when Gmail auth fails? When a skill times out? When state files are corrupted? No error UX specification. |
| **Accessibility** | ★★☆☆☆ — Terminal-only interface. No consideration for users who need screen magnification, high contrast, or alternative output formats. |
| **Gap:** | Define error UX, add `/help` command, design first-run experience, consider accessibility. |

### 14.4 Standardization Spec

| Aspect | Assessment |
|--------|------------|
| **Comprehensiveness** | ★★★★★ — 2,700 lines covering 19 sections is exhaustive. |
| **Accuracy** | ★★☆☆☆ — Treats ~60-70% of already-done work as TODO. Phases include tasks that are already shipped. This inflates the perceived effort and creates confusion about what actually needs to be done. |
| **Dependency ordering** | ★★★☆☆ — Phase ordering is mostly correct but some tasks have hidden dependencies (e.g., prompt genericization depends on user_profile.yaml extensions first). |
| **Test strategy** | ★★☆☆☆ — Mentions testing but doesn't define output equivalence testing for fetch script migration, which is the highest-risk operation. |
| **Migration safety** | ★★★★☆ — Strangler-fig principle is stated but not operationalized (no rollback procedures, no feature flags for gradual cutover). |
| **Gap:** | Rebase against actual codebase state, add output equivalence test protocol, define rollback procedures. This remediation document serves as the corrected execution plan. |

---

## 15. Forward-Looking Maintainability

### 15.1 Technical Debt Trajectory

| If We Do Nothing | In 6 Months | In 12 Months |
|------------------|-------------|--------------|
| HTML stripping duplicated 4x | Potential inconsistency across email sources | Bug in one copy causes data quality regression in one source only — hard to debug |
| 1,942-line instruction file | LLM attention degrades further as features added | Catch-up workflow becomes unreliable for later steps |
| 13 hardcoded prompts | Distribution impossible | Fork-based customization creates maintenance nightmare |
| No CI/CD | PII leak goes undetected | User trust incident |

### 15.2 Architecture Decision Records (to Create)

These decisions should be documented as ADRs (Architecture Decision Records) in `docs/adr/`:

1. **ADR-001: Shared lib/ vs. BaseFetcher inheritance** → Chose composition over inheritance
2. **ADR-002: Custom template engine vs. Jinja2** → Chose custom for zero-dependency
3. **ADR-003: Assembled instruction file vs. multi-file loading** → Chose assembly for CLI compatibility
4. **ADR-004: user_profile.yaml as single identity source** → Chose consolidation over scattered config
5. **ADR-005: Markdown-as-state vs. SQLite** → Chose markdown for human-readability and diffability

### 15.3 Monitoring & Observability (Future Phase)

Not required for distribution but valuable for reliability:

- **Catch-up success rate:** Track how often the full 21-step workflow completes successfully
- **Source health:** Track per-source fetch success rates, latency, error types
- **PII guard effectiveness:** Track detection rates, false positives, false negatives
- **User engagement:** Track which domains are actually used vs. enabled but ignored

---

## Appendix A: Prompt Audit Detail {#a-prompt-audit-detail}

### Per-Prompt Analysis

| Prompt | Lines | Clean? | PII Categories Found | Genericization Approach |
|--------|-------|--------|----------------------|-------------------------|
| `boundary.md` | ~100 | ❌ | Employer name, work policies, manager references | Replace with `{{employer.name}}`, `{{employer.policies}}` |
| `calendar.md` | ~150 | ❌ | Recurring event types, specific attendee names | Replace with `{{calendar.recurring_events}}`, use profile contacts |
| `comms.md` | ~150 | ❌ | Named contacts, relationship descriptions | Replace with `{{contacts.vip_list}}`, relationship context from profile |
| `digital.md` | ~120 | ✅ | None — fully generic | No changes needed |
| `estate.md` | ~100 | ✅ | None — fully generic | No changes needed |
| `finance.md` | 539 | ❌ | Bank names, investment platforms, employer 401k, specific account types | Replace with `{{finance.institutions}}`, `{{finance.employer_benefits}}` |
| `goals.md` | ~200 | ❌ | Personal career goals, financial targets, timeline dates | Replace with loaded goals from `state/goals.md` — prompt should define structure, not content |
| `health.md` | ~200 | ❌ | Doctor names, pharmacy, conditions, medications, provider details | Replace with `{{health.providers}}`, `{{health.conditions}}`, `{{health.medications}}` |
| `home.md` | ~120 | ✅ | None — uses generic patterns | No changes needed |
| `immigration.md` | ~150 | ❌ | Visa types, case numbers, filing dates, USCIS references | Replace with `{{immigration.status}}`, `{{immigration.cases}}` |
| `insurance.md` | ~200 | ❌ | Policy numbers, named insurers, coverage amounts | Replace with `{{insurance.policies}}` |
| `kids.md` | 455 | ❌ | Children's names, schools, grade levels, Canvas URLs, courses, activities | Replace with `{{#each children}}` iteration, `{{child.school}}`, `{{child.lms}}` |
| `learning.md` | ~150 | ❌ | Specific course names, certification targets, platform URLs | Replace with `{{learning.courses}}`, `{{learning.certifications}}` |
| `shopping.md` | ~100 | ✅ | None — fully generic | No changes needed |
| `social.md` | 513 | ❌ | Cultural festival names, community organizations, relationship context | Replace with `{{social.cultural_events}}`, `{{social.communities}}` |
| `travel.md` | ~200 | ❌ | Airline/hotel loyalty numbers, home airport, specific destinations | Replace with `{{travel.loyalty_programs}}`, `{{travel.home_airport}}` |
| `vehicle.md` | ~150 | ❌ | Vehicle make/model/year, dealer name, service history | Replace with `{{#each vehicles}}` iteration |

### Template Variable Registry

Complete list of template variables needed across all prompts:

```
# From user_profile.yaml (existing)
{{family.primary_user.name}}
{{family.primary_user.email}}
{{family.spouse.name}}
{{location.timezone}}
{{location.city}}
{{location.state}}

# From user_profile.yaml (new sections needed)
{{#each children}}                  → kids.md, goals.md
{{health.providers}}                → health.md
{{health.conditions}}               → health.md
{{health.medications}}              → health.md
{{health.pharmacy}}                 → health.md
{{finance.institutions}}            → finance.md
{{finance.employer_benefits}}       → finance.md
{{insurance.policies}}              → insurance.md
{{#each vehicles}}                  → vehicle.md
{{immigration.status}}              → immigration.md
{{immigration.cases}}               → immigration.md
{{social.cultural_events}}          → social.md
{{social.communities}}              → social.md
{{travel.loyalty_programs}}         → travel.md
{{travel.home_airport}}             → travel.md
{{learning.courses}}                → learning.md
{{learning.certifications}}         → learning.md
{{employer.name}}                   → boundary.md
{{calendar.recurring_events}}       → calendar.md
{{contacts.vip_list}}               → comms.md
```

---

## Appendix B: Script Audit Detail {#b-script-audit-detail}

### Production Scripts (Keep & Refactor)

| Script | Lines | Quality | Issues | Action |
|--------|-------|---------|--------|--------|
| `gmail_fetch.py` | ~350 | ★★★★★ | HTML stripping duplicated | Migrate to lib/ |
| `msgraph_fetch.py` | ~400 | ★★★★★ | HTML stripping + retry duplicated | Migrate to lib/ |
| `icloud_mail_fetch.py` | ~350 | ★★★★☆ | HTML stripping + retry duplicated | Migrate to lib/ |
| `gcal_fetch.py` | ~250 | ★★★★★ | Retry duplicated | Migrate to lib/ |
| `msgraph_calendar_fetch.py` | ~300 | ★★★★☆ | Retry duplicated | Migrate to lib/ |
| `icloud_calendar_fetch.py` | ~300 | ★★★★☆ | Retry duplicated | Migrate to lib/ |
| `canvas_fetch.py` | ~200 | ★★★★☆ | Different output pattern (state files, not JSONL) | Keep separate, uses profile_loader |
| `vault.py` | ~200 | ★★★★★ | None | No changes needed |
| `pii_guard.py` | ~250 | ★★★★★ | None | Add tests |
| `safe_cli.py` | ~150 | ★★★★★ | None | Add tests |
| `preflight.py` | ~200 | ★★★★☆ | None | No changes needed |
| `profile_loader.py` | ~100 | ★★★★★ | None | No changes needed |
| `generate_identity.py` | ~200 | ★★★★★ | Needs template extension (§5) | Extend for prompt templates |
| `_bootstrap.py` | ~150 | ★★★★☆ | No first-run detection | Add cold-start detection |
| `skill_runner.py` | ~150 | ★★★★★ | None | No changes needed |
| `todo_sync.py` | ~200 | ★★★★☆ | Reads artha_config.yaml directly | Migrate to profile_loader |
| `migrate.py` | ~150 | ★★★★☆ | None | No changes needed |
| `demo_catchup.py` | ~100 | ★★★★☆ | None | No changes needed |
| `local_mail_bridge.py` | ~150 | ★★★★☆ | None | No changes needed |
| `google_auth.py` | ~100 | ★★★★☆ | None | No changes needed |

### Auth Setup Scripts (Keep As-Is)

| Script | Purpose | Quality | Issues |
|--------|---------|---------|--------|
| `setup_google_oauth.py` | Google OAuth PKCE flow | ★★★★☆ | None |
| `setup_msgraph_oauth.py` | MS Graph OAuth flow | ★★★★☆ | None |
| `setup_icloud_auth.py` | iCloud keychain setup | ★★★★☆ | None |
| `setup_todo_lists.py` | MS To Do list ID discovery | ★★★★☆ | None |

### Utility Scripts (Review)

| Script | Purpose | Quality | Action |
|--------|---------|---------|--------|
| `parse_apple_health.py` | Apple Health XML parser | ★★★★☆ | Keep |
| `parse_contacts.py` | Contacts parser | ★★★★☆ | Keep |
| `historical_mail_review.py` | One-time mail analysis | ★★★☆☆ | Archive if no longer needed |
| `deep_mail_review.py` | Deep email analysis | ★★☆☆☆ | Hardcoded paths — archive or delete |
| `deep_mail_review2.py` | Deep email analysis v2 | ★★☆☆☆ | Hardcoded paths — archive or delete |
| `_mark_tasks_done.py` | One-time task cleanup | ★★☆☆☆ | Hardcoded data — archive or delete |

### Skill Plugins

| Skill | Purpose | Quality | Issues |
|-------|---------|---------|--------|
| `base_skill.py` | Abstract base class | ★★★★☆ | `compare_fields` not `@abstractmethod` |
| `nhtsa_recalls.py` | Vehicle recall checker | ★★★★★ | None |
| `noaa_weather.py` | Weather alerts | ★★★☆☆ | Hardcoded email, misleading comment |
| `property_tax.py` | Property tax lookup | ★★★★☆ | None |
| `uscis_status.py` | Immigration case status | ★★★★☆ | None |
| `visa_bulletin.py` | Visa bulletin checker | ★★★★☆ | None |

---

*End of document. This remediation plan supersedes the phase structure in `specs/standardization.md` by reflecting the actual state of the codebase. Execute Phase A first — everything else is secondary to making Artha distributable.*
