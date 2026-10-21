# Extended command protocols

When the user invokes any slash command, read `config/commands.md` for the full command
reference and execute accordingly. Available commands: `/catch-up`, `/status`, `/goals`,
`/domain`, `/domains`, `/cost`, `/health`, `/items`, `/bootstrap`, `/dashboard`, `/scorecard`,
`/relationships`, `/decisions`, `/scenarios`, `/diff`, `/privacy`, `/teach`, `/power`,
`/pr`, `/stage`, `/radar`, `/career`.

**`/career` — Career Search Intelligence (FR-25):** Requires active career goal
(`state/goals.md` with `category: career` + `status: active`) AND `~/.artha-local/cv.md`
or `~/.artha-local/cv-short.md` outside the repo. Loads `prompts/career_search.md`
ONLY on invocation — never during `/catch-up`. State: `state/career_search.md` (vault-
encrypted, `sensitivity: high`). Trace: `state/career_audit.jsonl`.

- `/career eval <URL|JD text>` — Run 7-block A–G evaluation, write report to
  `briefings/career/{NNN}-{slug}-{date}.md`, append tracker row, run
  `reconcile_summary()`, emit `career_eval_started` and `career_eval_completed`
  trace events. Proposes PDF if score ≥ 4.0.
- `/career tracker` — Render Applications table + summary counts from
  `state/career_search.md` frontmatter summary block.
- `/career pdf <NNN>` — Generate ATS-optimized CV PDF via
  `scripts.skills.career_pdf_generator.CareerPdfGenerator(report_number="<NNN>")`.
  Idempotent: skips regen if input hash unchanged.
- `/career cover <NNN>` — Generate a 1-page cover letter (markdown + PDF) via
  `scripts.skills.career_cover_letter.CareerCoverLetter(report_number="<NNN>")`.
  Deterministic composition from Block E/B + article-digest; hash-idempotent.
- `/career apply <NNN> [--portal X] [--referrer Y]` — Transition tracker row
  Evaluated → Applied. Records timestamp + portal + referrer, auto-closes
  matching sponsorship-verify OI, emits `career_apply` trace event.
  Invokes `scripts/career_apply.py`.
- `/career prep <NNN|company>` — Assemble interview-prep packet (Block F
  stories + counters + case study + matched KB deep-dives from
  `state/interview_prep.md`) to `briefings/career/{NNN}-interview-prep.md`.
  Invokes `scripts/career_prep.py`.
- `/career scan` — Run 72h-TTL sweep of configured ATS portals
  (`config/career_portals.yaml`) via
  `scripts.skills.portal_scanner.PortalScanner`; appends new matches as
  `New` tracker rows and emits `portal_scan` trace events.
- `/career story [append|list]` — Surface or append STAR+Reflection entries in
  the Story Bank (validates against closed-vocabulary tags per FR-CS-7).
- `/career stories` — Alias for `/career story list`.

Full behavior: `prompts/career_search.md` + `config/commands.md §/career`.

**`/pr` — PR Manager:** Run `python3 scripts/pr_manager.py --view` (or `--threads` / `--voice`
for subcommands). Requires `enhancements.pr_manager: true`. See `prompts/social.md §PR Manager Commands`
and `config/commands.md §/pr` for full behavior.

**`/stage` — Content Stage (PR-2):** Requires `enhancements.pr_manager.stage: true` in
`config/artha_config.yaml`. State file: `state/gallery.yaml`.

- `/stage` or `/stage list` — List all active cards (seed, drafting, staged, approved).
  Load `state/gallery.yaml`, filter cards where `status ∈ {seed, drafting, staged, approved}`,
  and format per `prompts/social.md §Card Display Format`.
- `/stage preview <ID>` — Show full card: occasion, event date, status, platform drafts, PII flags.
- `/stage approve <ID>` — Mark card approved; emit copy-ready content per platform.
  Call: `python3 -c "import sys; sys.path.insert(0,'scripts'); from pr_stage.service import ContentStage; from pathlib import Path; s=ContentStage(Path('state/gallery.yaml'),Path('state/gallery_memory.yaml')); ..."`
  or read the YAML directly and confirm to the user, then update `state/gallery.yaml` status field.
- `/stage draft <ID>` — Generate draft content for a seed card. During LLM-driven catch-up, drafts are generated inline at Step 8-A-6 (cards should arrive pre-drafted). Use this command only for cards that were missed or need re-drafting.
- `/stage posted <ID> <platform>` — Log post as published; update platform draft status.
- `/stage dismiss <ID>` — Archive card without posting.
- `/stage history [year]` — Phase 4 only: browse `state/gallery_memory.yaml`.

Full behavior and display formats: `prompts/social.md §Content Stage` and `config/commands.md §/stage`.
---
