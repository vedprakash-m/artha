# Artha — Agent Instructions

Applies to every coding agent. Prefer caution over speed; use judgment for trivial work. Combine with project instructions and the user's current authorization.

## Load and route

- Before responding, read and follow `config/Artha.md`; use `config/glossary.md` terms. At session start, load `state/lessons.md` fully.
- `config/Artha.md` §R is the authoritative router: load its workflow files in order, including every Catch-up workflow. Coding requests use `config/workflow/development.md`; do not ingest household data for development.
- Commands use **bare words, without `/`**: VS Code intercepts slash commands (`/brief` fails with “Unknown command”).

| Command / aliases | Purpose / data |
|---|---|
| `brief`, `catch me up`, `morning briefing`, `SITREP` | Catch-up → Briefing; execute the routed workflow |
| `work`, `what's happening at work`, `work briefing` | Work Briefing; authorized files in `state/work/` |
| `items`, `what's open`, `what's overdue`, `action items` | Open Items; `state/open_items.md` |
| `goals`, `how are my goals`, `goal progress`, `sprint` | Goals; `state/goals.md` |
| `domain <X>`, `tell me about <X>`, `<X> status` | Deep dive; `prompts/<X>.md` + authorized `state/<X>.md` |
| `content`, `what should I post`, `draft a post` | Content calendar; `state/pr_manager.md` + `state/gallery.yaml` |
| `guide`, `what can you do`, `show commands` | Briefly explain these commands |
| `health`, `system status`, `is everything OK` | System status; `scripts/preflight.py` via routed preflight |

Subcommands: `config/commands.md`. VS Code Copilot needs **Agent** mode (not Ask/Edit) for files/tools; execute suggested scripts in the terminal when prompted. MCP provides GitHub, Kusto, DevBox and other access when available. Work agents: `@artha-work-msft`, `@artha-work`.

## Think, simplify, change precisely

- State assumptions before implementation. Surface uncertainty, competing interpretations and tradeoffs; do not choose silently. If unclear, stop, name the ambiguity and ask. Offer simpler approaches; push back when warranted.
- Solve only the requested problem: no speculative features, single-use abstractions, unrequested flexibility/configuration or impossible-error handling. Replace 200 lines with 50 if sufficient; ask whether a senior engineer would find it overcomplicated.
- Every changed line must trace to the request. Match existing style; avoid adjacent code/comment/formatting improvements or refactoring working code. Remove imports/variables/functions your changes orphaned; report unrelated dead code without deleting it unless asked.
- Before shared-behavior changes (Pipeline, state document, verb output, reason code, skill vocabulary), assess callers, stored records, rendered text, tests, plans and specs; explain changes/side effects. Others rely on this infrastructure.
- Define verifiable success criteria; loop until verified. For multi-step work, pair each plan step with its check. Validation → invalid-input tests; bug → reproducer then fix; refactor → passing tests before/after.

## Feature lifecycle — in order

Canonical specs: `specs/artha-prd.md`, `specs/artha-tech-spec.md`, `specs/artha-ux-spec.md`. IaaG backlog: `specs/iaag-backlog.md` when present. Archive superseded plans/completed feature specs under `.archive/specs/`.

1. **Spec:** Write `specs/<feature>.md` with requirements, data model, alternatives, risks, assumptions and acceptance criteria. Iterate with owner until approved; no implementation beforehand. Work settled by an approved `specs/plan.md` needs no further approval. Unsettled decisions/spec amendments require owner review: alternatives and recommendation, one decision at a time.
2. **TDD:** For behavior, write failing tests then implement. Use real processes/Git/signals when testing them (lock races, kills at each effect step, index preservation). Tests must not depend on developer Git configuration, host credentials or production data.
3. **Validate:** Tests, coverage ≥95%, spec verification, parity checks. For Artha's full `pytest tests/` suite, use `tools/run_tests_isolated.py`; run `scripts/verify_specs.py` where present and existing repository quality gates.
4. **UAT:** Exercise the feature; show actual outcomes versus before/expected behavior and confirm whether expectations were met. Never claim unperformed UAT.
5. **Finalize:** Backfill canonical specs for new components/rules; move feature spec to `.archive/specs/` (`Move-Item` in PowerShell); update `CHANGELOG.md` with date, feature table, modified files and tests; audit `.gitignore` for PII/secrets; run privacy checks before committing. Commit (a) code/tests and (b) specs/archive. Normally push and confirm CI, **subject to user direction: currently keep commits local; do not push.** Report unavailable CI honestly.

## Evidence and plan discipline

- Claim only what evidence supports. A response must never predate the facts it reports.
- Where `specs/plan.md` exists, update dashboard/checkboxes before ending a turn that changed what is true; add a §13 entry for milestone changes. Use its exact five-state vocabulary: `✅` needs commit hash or CI run; local-only evidence stays `◐`; `🔬` needs a dated real-host observation.
- Host behavior (VS Code, Copilot, subagents, approvals) is **unobserved** until a recorded spike proves it; never say “should work.” Each spike requires separate authorization.
- Record spec defects in plan §12.3; immediately raise defects affecting never-waivable requirements (PRD §4.7), where those sections exist.

## Component-specific architecture and gates

Retained rules below apply to components defining these TypeScript paths/tools. This checkout lacks `core/`, `pipeline.ts`, `package.json`, `tools/coverage.ts`, `tools/lint-skills.ts` and `specs/plan.md`; do not invent them or claim their checks ran.

- `core/` is pure: no I/O/adapter imports. Effects use six ports: store, git, runner, host, clock, random. Only adapters, `wire.ts`, `main.ts` touch filesystem/processes/network. Enforce with `npm run lint`.
- Every verb uses one `pipeline.ts`: lock → recovery → envelope → effects → publish → external jobs. Front-ends hold no verdict/authority/budget logic.
- Only `core/verdict.ts` produces endpoints; only `core/budget.ts` changes counters. Only the kernel writes binding facts (endpoints, receipts, counters, ownership); agents explain, never relabel.
- M0 forbids `vm`, worker threads, `eval`, computed `import()`, client sockets and remote Git commands.
- Tag tests proving PRD requirements with `[REQ-ID]` in titles for `tools/coverage.ts`.
- Before calling work done, pass all `npm run ci` gates: typecheck, `lint-arch`, `lint-skills`, `coverage`, bundle build within size budget, every test. `npm run privacy` is the separate pre-commit gate.
- Node 24+ executes TypeScript directly; shipped bundles must run on Node 22.12+.

## Agent text and writing

- Skills/agent text is executable product behavior: respect token budgets; name only existing verbs, flags, decision kinds, reason codes and actions. State controls no more strongly than their mechanism class permits. `tools/lint-skills.ts`, where present, checks structure, not meaning: read your text.
- Use literal, plain language over metaphors/flourishes (“a setting worth changing,” not “a dial worth turning”) in comments, skills, specs, commits and replies. Avoid performative prose and unintended connotations.

Success: fewer unnecessary diffs/rewrites and clarifications before implementation rather than after mistakes.
