# The Catch-up core (`scripts/catchup_core/`)

A local, typed, independently testable library that is gradually replacing the
instruction-driven Catch-up for **pilot Domains** (currently Kids/School). It lives beside
the incumbent `scripts/pipeline.py`; nothing imports it from the incumbent path, and it
cannot publish a Briefing until the pilot is cut over (re-artha §12.6).

## Principles
PULL over PUSH (no daemon, schedule or listener) · coverage before confidence (a gap is
never reassurance) · evidence before eloquence (every consequential line cites evidence
or says `{{MISSING}}`) · fail closed (no plaintext fallback, no guessed value).

## Stages
`CREATED → PREFLIGHT → FETCH → NORMALIZE → EXTRACT → RECONCILE → TRIAGE → COMPOSE →
VALIDATE → COMMIT → PRESENTED → FINALIZED`. Each stage commits a journal row; re-running
a Catch-up ID resumes at the first uncommitted stage.

| Module | Role |
|---|---|
| `contracts.py`, `ids.py` | typed request/result, state machine, keyed IDs |
| `domain_pack.py`, `packs/*.yaml` | predicates, per-predicate source authority, alert rules, deadline arithmetic |
| `extract.py` | model request (no tools) and strict validation of candidates |
| `reconcile.py` | corroborate / supersede by authority / dispute — never "latest wins" |
| `triage.py` | horizon, ranking, staleness honesty, `next check by`, cross-Domain cases |
| `compose.py` | cited Briefing, output hygiene, validator |
| `coordinator.py` | stage machine, resume, shadow vs authoritative |
| `ledger.py`, `crypto.py`, `blobs.py`, `snapshot.py` | encrypted local ledger, forgetting, snapshots |
| `vault.py` | in-memory `age` access (no plaintext or key on disk) |
| `incumbent.py`, `legacy_state.py`, `adapters.py` | guarded bridge to the incumbent path and handlers |
| `shadow.py`, `scorecard.py` | parity with the incumbent and the release gate |
| `actions.py`, `action_mapping.py` | hardened action lifecycle contract and migration plan |
| `runtime.py`, `cli.py` | real wiring (shadow only) and the command entry point |

## Running a shadow Catch-up
1. Create `config/catchup_core.yaml` (git-ignored) mapping pilot sources to configured connectors:
   ```yaml
   sources:
     school_email: {connector: gmail, account_scope: personal, kwargs: {query: "label:school"}}
     calendar:     {connector: google_calendar, account_scope: family}
   household_from_profile: true      # subjects and who needs consent come from config/user_profile.yaml
   viewer: owner
   cutover: false                    # authoritative runs also need a signed `release:` block (policy.py)
   ```
   A source whose `class:` is not listed as an authority by any predicate is refused at start-up (it would
   turn every Fact into UNKNOWN): the multi-domain packs name classes such as `email_notice` or `bill_statement`.
2. **Extraction is done by whichever assistant you are running** (Claude, ChatGPT/Codex, Gemini,
   Copilot, a local model): the core never calls a model itself. When it needs extraction it pauses:
   ```
   cd scripts && python -m catchup_core.cli catchup --shadow --json --id <ID>     # prints the requests, exit 40
   # the assistant answers each request's prompt (JSON only), then:
   echo '{"responses": {"<request_id>": "<the model JSON, verbatim>"}}' | \
     python -m catchup_core.cli catchup --shadow --json --id <ID> --responses -
   ```
   Answers are bound to the exact prompt by `request_id`, are treated as untrusted data, and become facts
   only if the schema, the closed vocabularies and the verbatim-evidence check pass. No model, key or CLI
   is configured in Artha. Optional: `ARTHA_EXTRACTOR_CMD` may name any command (stdin prompt, stdout
   JSON) for unattended runs; if set it is used instead of the pause.
3. Record consent for any adult household member's restricted data (`Ledger.grant_consent`).
4. Run `python scripts/catchup_core/cli.py catchup --shadow --json` (from `scripts/` on `PYTHONPATH`,
   i.e. `cd scripts && python -m catchup_core.cli catchup --shadow --json`). Exit codes: 0 complete,
   10 degraded, 20 blocked, 30 usage error, 40 paused awaiting extraction answers.
5. Compare with the incumbent's Briefing using `shadow.incumbent_items` / `shadow.compare`, label
   the items, and feed the cases to `scorecard.score`. The pilot may be considered for cutover only
   when `Scorecard.passes` is true over the agreed number of Catch-ups and you have signed off.

## Guarantees you can rely on
* Restricted values and evidence excerpts exist only as authenticated ciphertext; a missing key blocks the
  operation instead of writing plaintext.
* Forgetting writes a tombstone, seals a deletion checkpoint outside the restorable set, deletes derived
  Facts and Alerts, and blocks re-fetch and old-snapshot restore.
* One Catch-up ID publishes at most one logical Briefing; a different text needs an explicit correction.

## Daily use
* `brief` runs the incumbent workflow (authoritative) and then `cli.py catchup --shadow --json` as a comparison.
  `--format flash|standard|deep|digest` changes what is shown; coverage, gaps and `next check by` are always shown.
* `health` lists every capability as `disabled`, `not configured`, `unknown` (configured, never run), `last succeeded`,
  `degraded` or `not implemented`, and whether it is `measured` (needs a verified release scorecard).
  `docs/capability-register.md` and `docs/spec-manifest.json` are generated (`tools/gen_capability_register.py`) and
  drift-checked in CI.
* `feedback <obligation> dismiss|snooze|useful|acknowledge --reason ...` also saves an encrypted replay case under
  `~/.artha-local/catchup_core/replay/` with `expected_outcome: pending_review`; forgetting a Signal deletes its cases.
* Every model request appends a disclosure record (provider, data class, purpose, retention, keyed request id; never the
  prompt) to `~/.artha-local/catchup_core/model-requests/YYYY-MM.jsonl`.
* The Briefing archive of an authoritative run is encrypted under `~/.artha-local/catchup_core/briefings/`; use `history`.

## Harness hooks (`.claude/settings.json`, git-ignored; absolute paths so any working directory works)
* `PreToolUse` -> `scripts/tool_guard.py`: denies bypass-flag CLI launches, the decrypt-to-disk vault command and
  plaintext sensitive State Files. It matches the text of a command, so even a heredoc that merely mentions them is refused.
* `SessionStart` -> `stale_banner.py` (age of the last Catch-up, per-source fetch age, omissions from the last Stop) and `vault_hook.py session-start`.
* `Stop` -> `session_stop.py`: archives once, checks the health record, records omissions for the next open. Never blocks.
Hooks are defence in depth; the core's own checks are the controls.

## Second machine: ingest bundles (re-artha 6.10)
A Connector that lives on another machine (for example Work or school mail on Windows) reaches the authority host only
through a hand-run, signed, encrypted, immutable bundle in the sync folder. There is no listener and no replication.
1. On the authority host: `cli.py bundle-enroll <producer>` prints a secret once. On the other machine:
   `cli.py bundle-enroll <producer> --secret <that value>`. The secret lives only in each OS credential store.
2. Config, on both machines (`config/catchup_core.yaml`):
   ```yaml
   bundles:
     school_email:              # a source name from `sources:`; the authority host imports instead of fetching live
       producer: win-host
       dir: ~/OneDrive/Artha-ingest
       recipient: mac           # authority host name
       account_scope: personal
       namespace: personal      # a Work bundle is rejected by the personal importer
       authority_generation: 1  # bump on operator-led promotion; older bundles are then refused
       max_age_hours: 48
   ```
3. On the other machine, whenever you choose: `cli.py bundle-export school_email`. Only a COMPLETE fetch is published.
4. The next Catch-up on the authority host imports it. A forged file, an unenrolled producer, a wrong recipient, scope or
   generation, a missing part, a sequence gap or data older than `max_age_hours` is quarantined or shown as an
   incomplete scope with its age; it never reads as "nothing to report".

## Action policy guard (RA-24)
`scripts/lib/action_policy.py` runs inside `ActionExecutor.approve()` after the friction floor and before any state
change. It refuses: hand-off-only types (money, credentials, legal, permanent deletion; extend with
`live_trial.handoff_only` in `config/actions.yaml`), expired or unreadable-expiry proposals, standing/automatic
approvals (they prepare, a person executes), and sensitive outbound content without exactly one named recipient.
Registered handlers execute in a bounded child process with a fixed provider egress policy (`lib/action_process.py`).
The child strips unrelated environment variables, refuses other providers and process launches, and expires on parent
loss or timeout. This contains trusted handler execution; it is not an OS sandbox. Platform credential access and
provider behavior still require live validation. Unregistered handlers remain hand-off only.
Sensitive outbound recipients must be registered in the trusted action configuration (`named_recipients`), including
health content whose sensitivity was accidentally left at `standard`.

## Say it your way
`brief everything` / "show everything" runs the comparison with `--format deep`: every Open Item and State File listed.

## Uncertain actions
An action whose outcome is not known (a timeout or lost response after dispatch) is `unknown`, blocks duplicates, and is
never retried blindly. `cli.py reconcile <action_id>` asks the provider: for `reminder_create`, exactly one matching task
created after the approval closes it as succeeded; no match, several matches or a failed lookup keep it `unknown`
(absence is not proof). `cli.py resolve <action_id> succeeded|failed` records a person's decision (human only; `failed`
releases the duplicate guard). Both are audited.

## Channels are gone
The Telegram, Slack and Discord code, the always-on engine and the nudge daemon were removed (see `docs/channels.md`).


## Runtime closure (WP-13)

- Fetch completion and bundle acknowledgments follow durable evidence ingestion. Bundle schema v2 preserves calendar
  metadata and cancellations; re-export old v1 bundles. Signed acknowledgments bind the producer, recipient, scope,
  sequence and authority generation; a crash before acknowledgment replays safely.
- Runtime retrieval rejects unresolved household IDs. The profile retains generated person IDs so a corrected name,
  corrected birth date or key rotation cannot silently change the identity. Parental grants do not become adult consent.
- Bundle ingestion/export rechecks current operation permissions. Full snapshot export refuses inaccessible data;
  a permission-filtered portable household exchange remains a later deliverable. The local owner can forget retained
  data after revocation. Restores retain the sealed deletion history and quarantine action snapshots.
- Grounded obligation names link independent messages; contradictory independent sources dispute rather than creating
  two current deadlines. Missing identity is not guessed. Non-pilot obligations are validated as source spans and passed
  to the incumbent as review commitments, with no permission to mutate pilot records or dispatch actions.
- Freshness follows the exact evidence/account scope, including enums and appointments. Historical explanations use
  historical status/provenance/validity. Corrections add notices to affected Briefing history without changing its text.
- Snapshots include encrypted State Files and YAML, detect changing database/curated inputs, and expire old generations
  during Catch-up (default 30 days, always preserving one current generation). Approved rollback bytes must decrypt and
  pass database integrity checks. No background cleanup process is installed.
- Capability health uses per-capability operation evidence. “Measured” additionally requires reviewed consented case IDs
  in the approved scorecard's `capability_reviews`, not a passing test count or an unrelated finalized Catch-up.

## Documents and Goal observations

`cli.py ingest <file.pdf> --subject <name> --shadow` imports a text PDF after scoped permission checks. Original bytes and
page text are encrypted locally; extraction runs in a short-lived process with byte/page/time limits. Image-only or
unreadable pages stay incomplete. No OCR result is asserted as a verified field. `documents:manual` consent is separate
from permission to read another person's mail. Original document ingestion is local and does not upload the PDF.

An explicitly authored active Goal can declare a minimum metric:

```yaml
metric:
  kind: on_time_obligations
  domain: kids_school
  predicate: exam_registration_deadline
  target: 3
  period_days: 30
  freshness_hours: 48
  sources: [school_email, calendar]
```

Catch-up and `goals` derive observations from visible current Facts and completion evidence, persist them encrypted,
and recompute after corrections. Missing coverage, unresolved interpretation and stale or disputed evidence produce
unknown progress. Goals without an authored metric continue to show authored progress as not re-verified.

## Review feedback

`cli.py review --shadow` lists pending feedback. `cli.py review <id> --label correct|incorrect|handled|timing --shadow`
labels its saved encrypted replay bundle. Correctness counters are grouped by Domain/severity; handled/timing responses
are not counted as correctness labels. Explicit Fact corrections also capture their prior input bundle for review.
No feedback automatically becomes personal memory.

## Remaining release work

Cutover remains disabled. Required live evidence includes scoped source/query validation, consented shadow outcomes,
source-negative critical-case review, cross-Domain usefulness, Goal correction/recomputation, supported-platform
credentials/cancellation/recovery, and measured workload, latency, cost and owner effort. Source freshness and source
precedence in the scorecard must match measured inputs and the actual runtime pack.

Later scope still includes household assignment/acceptance, emergency/custodian recovery, permission-filtered portable
exchange, broad Goal/Sprint metrics and standing responsibility plans, and OCR/vision expansion. The optional action
track still needs complete evidence/authorization/provider-version preconditions and broader provider-specific live
validation. None of those capabilities are implied by the fixes or by green local checks.

## Goal plans, activity and "show everything"
* **Goal plans (RA-25, pull only).** Put `milestones:` on a Goal in `state/goals.md` (`id`, `title`, `status`, `evidence`,
  `prerequisites`, `target_date`). Each Catch-up lists `GOAL PLANS`: *ready* (prerequisites done with evidence, next step
  named), *blocked* (what it waits for) and *unverified* (marked done without evidence, so it is not counted). Nothing
  advances between sessions and a plan never grants permission to act.
* **`activity`** shows each proposal in the spec's vocabulary (`lifecycle` DRAFT…CANCELLED, `group` pending / approved /
  executing / unknown / completed, `reversibility`) without migrating the live action database, plus whether actions are paused.
* **`catchup --everything`** is the deep format with every Open Item and State File listed ("show everything").
* **`reconcile` runs in the same bounded child process** as execution (minimal environment, provider-only egress, no
  sub-processes); a child that returns no receipt leaves the action `unknown`.
* `docs/traceability-register.md` maps every requirement to acceptance IDs, code and tests and records the PRD
  feature-group dispositions; it is generated and drift-checked together with the capability register.

## Responsibilities, household tasks and performance
All three are read-only, evaluated at each Catch-up, and absent files contribute nothing.
* **Standing responsibilities (RA-25)** in `state/responsibilities.md`: `id`, `outcome`, `allowed_operations`
  (only `read`, `draft`, `propose`; anything else is reported and ignored), `cadence_days`, `last_checked`,
  `next_check`, `completion_evidence`, `stop_condition`, `status`. A due check appears under `RESPONSIBILITIES`;
  "completed" without evidence stays open as unverified.
* **Household tasks (RA-19)** in `state/household_tasks.md`: `id`, `obligation`, `subject`, `owner`,
  `status` (`proposed`, `accepted`, `refused`, `done`), `assigned_on`, `escalate_after_days`, `completion_evidence`,
  `visible_to`. The `HOUSEHOLD` section shows escalations, refusals, owner-less tasks, duplicates (remind once) and
  tasks marked done without evidence; a task is shown only to the people in `visible_to`. Artha never contacts anyone.
* **Performance.** Each run appends one line (status, seconds, stage seconds, model requests; no content) to
  `~/.artha-local/catchup_core/metrics/YYYY-MM.jsonl`. `health` reports p50/p95 against the provisional 3-minute gate and the
  share of complete runs against the 95% goal, and refuses to judge fewer than five runs.

## Cost estimate (approximate, from sizes only)
Each run also records prompt and answer *sizes* in characters (never content). Add your own dated prices to
`config/catchup_core.yaml` and `health` reports `usd_per_run` and `projected_monthly_usd` against the $50 gate:
```yaml
pricing:
  input_usd_per_million_tokens: 3.0
  output_usd_per_million_tokens: 15.0
  as_of: 2026-10-01
  chars_per_token: 4        # optional; default 4
  runs_per_month: 30        # optional; the cadence the projection assumes
```
Assistant-side usage is not visible to Artha, so this is an estimate and says so; without pricing it reports sizes only.

## Read-only MCP server and traces
* **`cli.py mcp`** is a stdio MCP server your own assistant launches on demand (a short-lived process, never a listener).
  Tools: `beliefs`, `why`, `items`, `goals`, `health`, `activity`, `history`, plus `propose_correction` and
  `propose_feedback`, which only return an inert draft. There is no tool that writes, sends, approves, forgets, exports,
  captures, pauses or resolves. The viewer is fixed by your configuration (no `actor` argument), arguments are validated
  strictly, results are data-only (no links, images or markup) and size-capped, and errors name a class, never a path.
  Register it in your assistant's MCP settings as the command `python3 scripts/catchup_core/cli.py mcp`.
* **Traces.** Each run also appends one OpenTelemetry-shaped trace (OTLP/JSON `resourceSpans`; a `catchup` span and a
  `stage.*` child per stage; schema `artha.trace.v1`) to `~/.artha-local/catchup_core/traces/YYYY-MM.jsonl`.
  Attributes are status, mode and counts only, so a trace never contains prompts, answers, names or evidence.

## Benchmark (`make bench`)
`tools/benchmark_core.py` runs full Catch-ups over synthetic mail (no personal data, no network, no model) and reports cold
and warm seconds, seconds per stage, and whether cost per signal stays flat as volume grows. On a development Mac:

| Signals | Cold | Warm (same mail again) | Largest stage |
|---|---|---|---|
| 500 | 0.3 s | 0.06 s | fetch |
| 2,000 | 1.2 s | 0.26 s | fetch |
| 8,000 | 5.6 s | 1.8 s | fetch |

Scaling is linear (cost per signal ratio 1.15 from 500 to 8,000). The benchmark found and fixed a quadratic reconcile
(every candidate decoded every Fact of its predicate; the obligation filter now runs in SQL: 3.1 s to 0.27 s at 2,000
signals). A deterministic test counts Fact decodes so that regression cannot return unnoticed. These numbers exclude
connector latency and assistant time, which dominate in real use: they show the core is not the bottleneck, they are not
the release measurement.
