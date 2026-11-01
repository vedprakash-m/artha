# The Catch-up core (`scripts/catchup_core/`)

Release validation continuation, 2026-10-27: v4 total/live case counters require literal matching integers,
the outer precision floor must be a finite probability, and release Domains are a distinct explicit list.
Stage 1 clearance must be a reviewed passing JSON object; owner-review fields require nonempty text.
Malformed artifacts refuse with `PermissionDenied` rather than raw container/coercion exceptions.
These checks do not establish that reviewed observations occurred or authorize cutover.

Corpus labels describe expectations, not live-data provenance or model outcomes. Labeling does not
set `consented_live`; unsupported historical claims are not exported. Malformed or source-rebound
labels refuse, and frozen-output disclosure rechecks every referenced source under current permissions.
Independent held-out, live and review annotations remain separately evidenced release inputs.
Work batch commit/discard shares the publication lock with joined copied-context workers, so teardown
cannot bypass staging or overwrite a concurrent staged replacement.

### Reviewed Windows local-storage adoption

The explicit `storage-preview` and `storage-adopt` checkpoint commands address only a legacy configured
machine-local root and its existing installation lock. They do not enroll recovery inventory, keys or
Work identity, and ordinary runtime/registry operations still refuse unsafe storage. From `scripts/`:

```text
python -m catchup_core.checkpoint_cli storage-preview
python -m catchup_core.checkpoint_cli storage-adopt --sha256 <exact-preview-sha256> --reviewed --writers-stopped
```

The apply command requires separate live approval, exact current metadata review and stopped-writer
attestation. It holds exclusive native access, preserves ownership/payloads/descendant ACLs and changes
only the reviewed DACLs to the existing private contract. Interrupted or changed state needs a new
preview; no recursive ACL repair or automatic weaker-permission rollback is allowed. An already-private
installation is a verified no-op. Writer exclusion is cooperative; the attestation does not prove that
arbitrary host tools are stopped or confined. Successful adoption is not inventory review or recovery.
Native synthetic tests cover descriptor preservation, contention and process death; every live adoption
requires its own exact approval and stopped-writer attestation.
Configured roots overlapping the repository, home or known sync roots are refused using the existing
checkpoint path policy. Review digests have no TTL/single-use claim; changed metadata/configuration
requires fresh review. Native tests preserve stored descendant owner/group/DACLs and operative flags;
the Windows projected inherited-provenance bit may change. Root access/direct-child inheritance
intentionally change; unchanged reachability for every other principal is not claimed. SACL bytes were
not read or empirically compared.
UNC/device namespaces and remote/unknown/missing/CD-ROM drive classifications are refused before
resolution or target access. Ordinary local fixed/removable/RAM drives remain supported. Remote
classification tests use injected results; no actual remote filesystem acceptance is claimed.

Git privacy hooks require an executable `python3`. On Windows, ensure the Git process resolves an
actual installed Python 3 interpreter rather than the Windows Store alias. A process-local command
alias to an existing validated interpreter preserves the real hooks and their checks; it is not a
reason to disable hooks or install another runtime automatically. Missing interpreter/scanner support
remains a failed gate.

Current continuation, 2026-10-24: source add/update/remove review binds the current local owner, permission generation,
effective workspace cohort, Domain packs and connector registry. Apply rechecks these and exact file identity before
atomic replacement and directory flush. It grants no consent and cannot enable held provider sync. Household-profile
or connector-registry changes invalidate evaluation evidence even when core YAML is unchanged.

Recovery acceptance uses `artha.recovery-rehearsal.v2`: separately reviewed native Mac personal, Windows Work and
personal OneDrive-web observations bind checkpoint hashes/generations, cohort and current privacy replay. The Work
artifact participates in cached guard identity. Required fields are not proof the observations actually occurred.
Former-host retirement remains governed by its separate protocol when needed.

Normal full-state CLI restore keeps historical non-ledger files in encrypted quarantine when deletion/privacy
lineage cannot be proven, including opaque encrypted artifacts. Ledger-only replay cannot certify arbitrary
copied State File content. The approved review-gated encrypted quarantine/clean-file workflow is implemented
with current-authority and exact per-file review checks; its native recovery and owner acceptance remain open.
Inspection-only recovery cannot install keys or activate a copy; do not remove its activation fence.
Full recovery is not ready merely because fail-closed containment is tested.

Spend seals retain replay identities across UTC-month rollover while counters remain month-specific. Actual native
recovery, independent evaluation and cutover remain open; retain-all and held action/sync policies are unchanged.

A local, typed, independently testable library that is gradually replacing the
instruction-driven Catch-up for **pilot Domains** (currently Kids/School). It lives beside
the incumbent `scripts/pipeline.py`; nothing imports it from the incumbent path, and it
cannot publish a Briefing until the pilot is cut over (re-artha §12.6).

Checkpoint safety continuation (2026-10-23): recursive traversal failures and changed SQLite/file generations
block publication. Personal runtime construction, Catch-up, inspection and action approval share the capture
fence; checkpoints retain ledger locks and verify ciphertext before write-once publication. An interrupted
capture leaves private ciphertext requiring recovery review. Restored action databases are quarantined under
a non-runtime name; restored cutover is false, effects paused, and an ancestor restore marker blocks runtime
and action entry points before live storage or credential use. Do not remove the marker to activate a copy:
profile key recovery, current privacy continuity and authority reconciliation still require acceptance.
Contract 0.7.0/migration 0016 retains source observation and occurrence times per Fact version; unavailable
older times remain unknown. These controls do not establish Work-profile or native Windows recovery.

The owner confirmed on 2026-10-24 that Work uses the personal `age-key` / `artha` vault identity. Work checkpoints
now escrow that shared identity with independent `artha-work-checkpoint` envelope keys and a separate passphrase;
personal ledger/HMAC keys are excluded. Work creation requires Windows, with no copying and no repository/known
sync destination. `verify PATH --profile work` verifies with the host's Work checkpoint keys; passphrase-only
`recovery PATH --inspection-only` needs no OS keys. Normal Work restore remains blocked without current privacy
proof, including old checkpoints passed as personal. This does not prove native Windows or live-vault recovery.

Release evidence (2026-10-24) uses `artha.release.v4`. Each case and workload measurement must match the current
implementation/prompt/pack/configuration/contract/model cohort; changing only the cutover flag or release approval
does not create a cohort hash cycle. `artha.extract.v2` bundles include the expected cohort. Responses may include
`model_identity` (provider, model, revision, retention_policy) and `cohort` beside `responses`; authoritative resumes
require an exact match. Legacy anonymous answers remain accepted in shadow mode and their frozen output is marked
unverified for release. Matching host-reported metadata does not independently attest the provider or host isolation.

`python -m catchup_core.checkpoint_cli recovery CHECKPOINT --inspection-only` verifies a checkpoint using its
passphrase and in-memory escrow keys without requiring or installing host keys. It reports structural counts,
keeps privacy history unverified, and cannot activate or dispatch. Normal restore still requires trusted current
consent/deletion history. The owner explicitly retained ES1 scrypt/AES-GCM escrow; its ciphertext digest is bound
inside the authenticated manifest. Old checkpoints without that binding require recovery review, not an implicit pass.

Local integration continuation (2026-10-24): release preservation requires an approved full-installation ACK2
checkpoint, a non-aliasing independent copy and a checkpoint-bound recovery rehearsal. A same-device copy needs
explicit reviewed cloud-recovery evidence; merely copying ciphertext does not establish an independent failure
domain. Selective AE1 snapshots cannot satisfy this requirement. Restore authenticates the requested profile/tree
before plaintext output and verifies physical files/databases before privacy transformations. Native key inventory,
current fresh-host privacy continuity and owner/platform recovery acceptance remain unfinished.

Authority transfer journals a paused queue intent before ledger promotion, then confirms matching epochs; interrupted
steps remain non-dispatching and resume against the same receipt. Owner-attested quiescence/credential revocation
is recorded as an attestation, not a native observation or a remote kill switch. Core-derived action review binds
Fact versions, subjects, current export permission and freshness through claim/worker checks. Unsupported fresh
provider-version verification refuses. Neither mechanism lifts held action types or authorizes live promotion.

CalDAV now preserves date-only all-day values and exclusive ends, retains valid siblings with degraded coverage
after parsing failures and discloses unmatched explicit calendar selections. Full provider cursor/reset/deletion
semantics are still incomplete. Goal text inspection preserves bounded review gaps. OCR's existing page allowance
counts attempts, including empty/failed recognition; field-level confirmation is separate unfinished work.
Credential setup/enrollment uses native-only storage and writer fences; iCloud setup no longer mirrors passwords
into repository files. Existing legacy plaintext copies require separate reviewed disposition and are not removed.

The current engineering/owner-gate matrix and parallel completion plan are in
[the implementation status](re-artha-implementation-status.md#current-audit-and-completion-plan--2026-10-24).

## Recovery Dependency Registry

The owner-approved registry is machine-private metadata, not a key store or permission authority. Personal and
Work registries live separately under `~/.artha-local/recovery-registry/`, independent of runtime-local overrides.
Known writers record pending intent before native changes and finalize only after verified readback. Pending
operations, unregistered dependencies or missing historical review block complete checkpoint capture. No background
process runs, no historical review is inferred, and inspection never enrolls keys or repairs pending work.

Explicit historical inventory uses the existing checkpoint command module (run from `scripts/`):

```sh
python -m catchup_core.checkpoint_cli registry-declare --profile personal \
  --kind vault --service age-key --account artha --generation legacy
python -m catchup_core.checkpoint_cli registry-preview --profile personal
python -m catchup_core.checkpoint_cli registry-review --profile personal \
  --sha256 <exact-preview-sha256> --reviewer <local-owner> --reviewed-at <explicit-time-with-offset>
```

Declare the full reviewed inventory, not only the illustrative vault entry. Key material uses `key` with account
`key.N` and generation `N`, plus `key_head`/`current_version` with the current version. Credentials use their logical
head (`credential`, service/account from the writer, exact native generation); current/retired chunks are derived
from verified heads. Client, bundle and privacy dependencies use their declared native service/account. New
registered writes preserve historical review, while a new manual declaration requires review again. Preview emits
bounded metadata and its digest, never secret values; a digest changed by another write must be reviewed afresh.
Do not put secrets into service/account/generation/reviewer arguments. Run the corresponding review separately
on the Windows Work host using `--profile work`; sharing the age identity does not permit copying Work data.

These commands attest the owner's historical inventory, not an automatic search of the OS store. A missing or
deleted native dependency still blocks capture. Failed native/metadata publication leaves pending intent; repeat
the original explicit writer operation after reviewing its outcome, not a manual registry edit. A matching retry
can finish an acknowledged native generation without changing it. A different explicit replacement needs the
normal writer/approval path. Per-record locks reject concurrent mutation of the same OS record.

Registry files restored from a checkpoint are inactive installation artifacts. They do not authorize replacing the
current host's registry, secrets or privacy heads. Passphrase-only recovery remains inspection-only; OS key
installation and cutover stay blocked until current privacy history and owner recovery acceptance are established.
Native inventory/recovery and both-host UAT were not performed as part of implementation.

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
| `actions.py`, `action_mapping.py` | hardened action lifecycle contract; the action database keeps its stored status words and `action_mapping` is the one mapping to the lifecycle vocabulary (pinned by a test) |
| `runtime.py`, `cli.py` | runtime wiring for shadow and release-gated authoritative runs, and the command entry point |

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
   is configured in Artha. Optional: `ARTHA_EXTRACTOR_CMD` may select the bundled pattern extractor (stdin prompt, stdout
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

* `cd scripts && python -m catchup_core.cli goals --shadow --weekly` compares existing dated quantity/habit evidence.
  Quantities need an exact measurement seven days before the current verified point; habits compare the authored
  window shifted seven days with every check-in recorded. Missing/conflicting inputs stay unknown. The same read-only
  view is available through MCP `goals` with `{"weekly": true}`. Authored next actions remain visible beside metrics.
  Other metric histories, leading-indicator links and broader Sprint intelligence remain completion work.

* `brief` runs the incumbent workflow and then `cli.py catchup --shadow --json` as a comparison. The incumbent
  retains record types without an approved transfer; follow the Open Items fence in `config/workflow/catchup.md`.
  An authoritative core run is separate and release-gated. `--format flash|standard|deep|digest` changes what is
  shown; coverage, gaps and `next check by` are always shown.
* `health` lists every capability as `disabled`, `not configured`, `unknown` (configured, never run), `last succeeded`,
  `degraded` or `not implemented`, and whether it is `measured` (needs a verified release scorecard).
  `docs/capability-register.md` and `docs/spec-manifest.json` are generated (`tools/gen_capability_register.py`) and
  drift-checked in CI.
* `feedback <obligation> dismiss|snooze|useful|acknowledge --reason ...` also saves an encrypted replay case under
  `~/.artha-local/catchup_core/replay/` with `expected_outcome: pending_review`; forgetting a Signal deletes its cases.
* Every model request appends a disclosure record (provider, data class, purpose, retention, keyed request id; never the
  prompt) to `~/.artha-local/catchup_core/model-requests/YYYY-MM.jsonl`.
* The Briefing archive of an authoritative run is encrypted under `~/.artha-local/catchup_core/briefings/`; use `history`.

## Harness hooks (Claude and VS Code Copilot)

Run `python tools/configure_agent_hooks.py` from a terminal to repair or install `.claude/settings.json` without
changing permissions or domain records. Both setup scripts invoke it. A one-time local backup preserves the prior
settings. Commands select the Unix or Windows external Artha venv, then the platform Python launcher; no host-specific
absolute path is synced. Copilot uses `powershell`, earlier VS Code Local uses `windows`, and the Unix fallback supports
Claude's project-root variable. Reload the VS Code window after changing hooks.

Copilot can discover `.claude/settings.json` as repository hooks. A missing Mac executable on Windows can therefore
produce “Denied by preToolUse hook from repo settings” before a Work command runs. Check the hook's stderr in the agent
logs. Do not remove protections or bulk-decrypt state as a workaround. If denial names a protected file or decrypt
command, use `vault.py read <domain>` or the routed Work reader instead.

* Pre-tool guard accepts compatible snake_case and Copilot camelCase arguments, including Windows paths/PowerShell.
  It refuses bypass launches, decrypt-to-disk and direct sensitive plaintext. These are heuristic defense-in-depth
  checks; the core's own permissions remain the boundary. Quoted diagnostic text/heredoc bodies are not commands.
* Session start reports staleness and stray plaintext; post-tool checks warn about stray plaintext.
* Stop checks the health record and archives an existing draft once. It does not rewrite domain records.

Shell/PowerShell process tests use synthetic stdin, paths and data. Native Windows Copilot acceptance remains required;
other installed user/plugin/organization hooks may independently deny calls.

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
loss or timeout. After queue approval checks, the parent supplies one provider access credential through a bound
private pipe; no refresh grant/client secret enters the child. Its normal credential APIs are scoped and read-only,
handler diagnostics are discarded and known tokens are redacted from receipts. Each parent authentication HTTP request
has a ten-second timeout. This contains trusted Python handler execution; it is not an OS sandbox against native code.
Platform credential access and provider behavior still require live validation. Unregistered handlers remain hand-off only.
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

The full-v4 release requires live evidence including scoped source/query validation, consented shadow outcomes,
source-negative critical-case review, cross-Domain usefulness, Goal correction/recomputation, supported-platform
credentials/cancellation/recovery, and measured workload, latency, cost and owner effort. Source freshness and source
precedence in the scorecard must match measured inputs and the actual runtime pack. The owner-approved slice instead
uses a signed waiver for its named gates and retains the other release requirements. Check the effective release on
its authority host; this document does not establish its current validity or authorize a cutover.

Later scope still includes record-level permission filtering for household assignments, emergency/custodian recovery, permission-filtered portable
exchange, broad Goal/Sprint metrics and standing responsibility plans, and OCR/vision expansion. The optional action
track still needs complete evidence/authorization/provider-version preconditions and broader provider-specific live
validation. None of those capabilities are implied by the fixes or by green local checks.

## Cutover and preservation of Domain state (owner requirement, 2026-10-22)

**Historical context:** This plan was written on 2026-10-22 while cutover was disabled. An owner-approved slice
was later released; the plan below is not a statement of current host status or an authorization to promote.
The Windows Copilot `work` entry point has owner-reported recovery; that does not establish core Windows cutover
or restore acceptance. Implementation work does not authorize promotion.

### 1. Protect the existing installation before any migration or authority change

The first cutover operation must be a complete, encrypted, verified recovery checkpoint. Inventory and a coordinated
write freeze are prerequisites to capturing it, not permission to modify data first. Inventory paths and metadata
without putting private contents into development logs. Include every participating machine; a Mac-only copy cannot
recover Windows-only state or settings. Record the checkpoint generation, exact code revision, local uncommitted
code/configuration, schema versions, authority host, and the manifest digest.

| Required coverage | Capture and verification requirement |
|---|---|
| All State Files | Recursively inventory `state/`, including encrypted and plaintext files, nested directories, YAML/JSON, lessons, attachments and companion files. No extension allowlist may silently omit data. Preserve bytes, relative paths and required permissions. Work state is a separate policy-controlled encrypted backup with separate keys/access; do not import it into personal snapshots. |
| All configuration and runtime inputs | Recursively inventory `config/`, prompts, packs, workflows, agent instructions, `.github/`, `.claude/`, `.vscode/`, local overrides and relevant machine-local settings. Include ignored/local specifications and configuration needed to reproduce behavior. Secrets remain encrypted; secret values never enter the public manifest or logs. Preserve local work and branch/commit identities without resetting, stashing or cleaning the checkout. |
| Local databases and linked artifacts | Locate actual configured stores on each host, including outside the checkout and nested under `~/.artha-local/`. Capture SQLite through its online backup API and verify integrity/foreign keys as applicable; copying a live `.db` alone can omit committed WAL data. Include evidence blobs, documents, captures, replay/corpus records, action/audit history, privacy capsules, cursors, bundles and acknowledgments needed for recovery. |
| User-visible history and recovery records | Include Briefings, summaries, correction history and existing backup catalogs needed to explain retained records. Inventory other referenced external paths explicitly. Label reproducible caches as such; never infer that an unknown file is disposable. |
| Keys and trusted authority metadata | Verify recovery of the incumbent age key, core encryption/HMAC key versions, current trusted privacy anchor and history, device/authority identities, and bundle signing/recipient keys where used. OS credential stores are not ordinary files. Use a separately protected, policy-approved recovery path and prove it works; do not print keys or assume a folder backup contains them. OAuth credentials may instead require reauthorization. Work recovery follows its own policy. |

Stop Catch-ups, scheduled writers, action dispatch and manual edits on all participating hosts for the final capture;
let in-flight operations settle and reconcile uncertain effects. Resolve pending sync/conflict copies explicitly.
Capture one coherent application generation across stores and files, and verify that the input inventory and hashes
are unchanged before/after capture. A collection of individually successful snapshots is insufficient.
Do not sync live SQLite/WAL/SHM files through OneDrive.

The protected checkpoint must have an encrypted inventory of every expected path, size/hash, store/schema/key
version, exclusions with reasons, and capture errors. A missing, unreadable, changed, skipped or unaccounted required
file blocks cutover. Unknown paths, links or policy boundaries require resolution, not silent omission.
Verify decryption and contents, then verify a second encrypted copy on an independent failure domain. Another folder
on the same disk or the same synced tree is insufficient. Retain this baseline outside automatic GFS/core snapshot
pruning until owner acceptance and the agreed recovery window have ended; deletion still follows retention/erasure
policy. Keep current privacy revocations/tombstones independently recoverable as they advance.

### 2. Demonstrate restoration before touching authority

Restore to a new isolated destination with ingestion, outbound effects and scheduled jobs disabled. Never rehearse
over live `state/` or an active database. Prove exact file coverage/hash equality for the baseline, database integrity,
key access and reference completeness; record elapsed recovery time and any intentionally transformed records.
Then rehearse migrations and rollback on that copy, including a correction made after promotion and a later
revocation/deletion. Restored output must honor the latest privacy history; byte equality with an old checkpoint is
not permission to resurrect erased data. Action snapshots remain quarantined and cannot grant dispatch.
Exercise the Windows paths actually used by the owner and demonstrate that `work` still works without changing
Work authority. If keys or the trusted privacy history cannot be recovered on the intended recovery host, stop.

### 3. Pass release gates and promote a narrow slice

Finish the engineering and external acceptance list in `re-artha-implementation-status.md`, including measured
quality/latency/cost/effort, source and permission validation, supported-platform recovery, owner UAT and approved
release artifacts. Record a per-record-type authority matrix: current writer, proposed writer, consumers, host,
switch, validation evidence and rollback owner. Retain existing Domain Markdown, Goals and Open Items authority
until a specifically reviewed transition; the initial evidence pilot does not imply permission to rewrite them.
Keep Windows Work on its existing independent path and leave external-effect types held unless separately approved.

Use shadow results for comparison while the incumbent remains the writer. Immediately before an approved switch,
repeat the quiesced verified checkpoint so changes since the baseline are included. Promote one agreed slice on one
authority host, freeze its former writer, and verify the authority generation. Secondary hosts must not become a
second writer. Check file hashes, identifiers, dates, statuses, counts, references, manual edits and representative
commands against the agreed baseline. Unexplained differences block expansion. Observe a defined owner-approved
window before widening scope or retiring any incumbent component; set that window from measured workload.

### 4. Roll back without losing changes made after cutover

On missing records, unexplained overwrites, permission failures, conflicting writers, broken critical commands,
unexpected effects or failed agreed release thresholds: stop the new writer/dispatch and preserve its current
generation first. Disable promotion and revoke/fence its authority before restoring any former writer.
Restore the last verified checkpoint into a separate location; identify and replay/reconcile reviewed post-cutover
changes before reactivation. A flag change alone does not reverse database migrations or recover new records.
Never overwrite the live installation blindly with an older archive. Preserve the latest revocations/tombstones,
audit and action-operation history; reconcile provider outcomes rather than re-executing restored approvals.
External sends/edits are not undone by filesystem rollback. Resume only after integrity, data reconciliation and
single-writer checks pass. Missing current privacy/key metadata requires recovery, not bypassing the checks.

### Expected impact and current readiness limits

Existing Domain State Files should remain byte-identical during the initial pilot except separately authorized
normal-use writes; there is no planned bulk replacement, empty-file regeneration or automatic summarization of
their contents. New core evidence/projections and local database schemas can change, and selected configuration,
routing and authority metadata will change at an approved switch. Any later State File migration needs a named
mapping, preservation of IDs/history, semantic comparison and its own demonstrated rollback.

The current backup tools do **not** yet prove this complete cutover contract. `scripts/backup.py` uses a configured
registry/fallback, scans only top-level local `*.db` files, encrypts those database paths directly, and can publish a
partial archive after skips/errors. Its regular restore can write into the installation. Core `backup.py` uses
SQLite online backup, consistency checks and an encrypted envelope, but captures selected artifacts and supplied
top-level curated files rather than all recursive state/configuration or OS keys. Its restore requires a new
destination and current trusted privacy history, and quarantines actions/curated files. The separate `snapshot.py`
API is not interchangeable with that encrypted restore. None alone establishes a complete machine recovery point.

**Blocking work:** complete-inventory capture with fail-closed completeness checks; coherent cross-store capture;
independent-copy/retention protection; protected key/privacy/authority recovery; and an owner/platform restore and
post-cutover-change rollback rehearsal. These must be implemented and evidenced before promotion, alongside the
remaining release gates. The goal is no loss of committed data at the quiesced checkpoint; current backups and tests
do not establish zero-loss recovery, a measured recovery time or zero risk. No live backup, restore or cutover was
performed to write this plan.

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
  `status` (`proposed`, `accepted`, `refused`, `done`), `completion_evidence`, `visible_to`. Assigned work is accepted
  by household convention; no member enrollment, acceptance response or delay escalation is required. The `HOUSEHOLD`
  section shows reported refusals, missing owners, duplicates and tasks marked done without evidence. Explicit visibility
  is fail-closed; an empty list hides the record. Artha never contacts anyone or infers data-access consent from assignment.
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
* **Traces.** Each invocation also appends one OpenTelemetry-shaped trace (OTLP/JSON `resourceSpans`; a `catchup` span and a
  `stage.*` child per stage; schema `artha.trace.v2`) to `~/.artha-local/catchup_core/traces/YYYY-MM.jsonl`.
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


### Completion evidence and publication integrity (2026-10-21)

`complete milestone:<goal>:<item>`, `complete responsibility:<id>`, `complete household:<id>` and
`complete open_item:<id>` show the exact
commitment with an expiring `apply_token`. Repeat with `--apply <apply_token>` only after reviewing it. The accountable
owner confirms completion; the ledger stores a versioned `user_confirmed` Fact. Catch-up resolves it against the
current commitment and all subject permissions. Old prose references remain unverified. No authored Goal priority,
owner or target is overwritten. Changing the commitment invalidates its previous completion binding.

New evidence uses UTF-8 byte coordinates (extractor schema v2, core contract 0.2.0). Migration 0011 labels existing
character coordinates and adds a durable calendar-cancellation journal. Do not remove the old coordinate reader or
cancellation recovery during migration cleanup. Unknown obligations after cancellation require review, not a claim
that they were completed. CalDAV collection URLs are stable classification keys; event instances have distinct IDs.

Draft publication rechecks permission, Fact, rendered-content and keyed curated-input versions. A changed generation
requires a new Catch-up ID. Arbitrary command extractors are refused; the bundled deterministic extractor runs with
isolated Python and no inherited credential environment. Assistant exchange remains model-neutral, with host isolation
subject to its own acceptance. Every effect-capable action requires exact preview review regardless of configuration.

Under the owner's 2026-10-25 override, `make check` requires 85% core combined/line/branch,
changed-line and security-harness combined coverage. Tests and platform contracts are unchanged.
These are engineering controls, not live acceptance. The definitive remaining work and live
gates are in `docs/re-artha-implementation-status.md`.
The isolated security harness writes `coverage-harness.xml` to the invoking directory before
its disposable source checkout is removed; the generated report is ignored by Git.


### Goal progress and completion revisions (2026-10-21)

`complete ... --reopen` previews withdrawal of an earlier completion; apply its new token after review. Repeating the
same applied token is idempotent within its expiry. A token from before a withdrawal cannot reconfirm the commitment.
Tokens bind the prior Fact version, actor, permissions and record content. Reviewed Fact replacement uses an atomic
version check so a concurrent correction cannot be overwritten.

An authored `metric: {kind: milestones}` counts only current permitted confirmations for that Goal's exact milestones.
Optional `target` is a positive integer no larger than the milestone count; invalid/unverified plans stay unknown.

Contract 0.8.0 adds canonical Open Item completion linkage. An item needs an explicit accountable `owner`,
`domain` or `source_domain`, and resolved subject scope. Preview `complete open_item:OI-001`, review the exact
record, then apply the returned token. Its versioned confirmation removes that exact item from `items` and the
Briefing without editing the authored State File. `--reopen` withdraws the confirmation; a changed commitment
also becomes open again. Old review tokens cannot restore withdrawn completion. Historical authored terminal
statuses retain their incumbent meaning; they do not by themselves establish a verified Goal outcome.

A Goal milestone may declare `open_item_ref: OI-001`. The Goal/milestone and item must have the same explicit
owner, Domain and complete subject/co-subject scope. Goal observations and plans then share the item's current
completion Fact version; reopening, changed content or revoked access recomputes them. Missing, duplicate,
malformed, inaccessible or mismatched links need review. Within a Goal, duplicate links cannot count the same
item twice. Goal queries recheck linked records and Fact versions before returning. No file is rewritten and no
external effect is authorized by a completion.

Completion subject scopes retain parent Goal subjects as well as milestone subjects. `subject_id` is an explicit
alias for `subject`; contradictory aliases require review. Milestones whose parent used `subject_id` now bind
that identity in the completion key and require fresh confirmation of any older unbound proof. Confirmation
checks the reviewed permission generation inside the ledger write transaction, including a competing-process
revocation between preview validation and write.
Completion revisions recompute progress and flag affected archived Briefings without rewriting historical text.
Goal, plan, responsibility and household completion dependencies pin their actual Fact versions through publication.

On-time-obligation metrics accept Connector names or `connector:account_scope` in `sources`. Both source coverage and
contributing Fact lineage must match. Corrections retain source membership through historical evidence. A rolling
`period_days` includes today and exactly that many calendar dates. Missing/stale coverage remains unknown.
Goals and Sprints are read from one captured State File; duplicate identities, missing active Goal references,
conflicting active Sprints and invalid date intervals are visible review gaps. No authored fields are changed.


Legacy action migration uses SQLite online backup rather than a main-file copy, so committed WAL rows are retained.
It verifies the staging database and publishes atomically without replacing an existing local database. The source is
preserved. A process death before publication leaves no partial destination; a death just after publication leaves a
complete database. A private `.actions-migrate-*.db` staging file can remain after forced termination. This does not
supply action quarantine, authority-transfer or real multi-platform recovery evidence.


### Action uncertainty, upgrade and calendar review (2026-10-21)

The authoritative action queue performs one historical uncertainty upgrade. It creates and integrity-checks a private
`actions.db.pre-quarantine-<id>.db` recovery snapshot before changing nonempty history. Unclaimed historical execution
and ambiguous/partial failures become `unknown`; uncertain archived records reenter the active queue. Historical
approvals without payload-bound review lose authority. Marker, audit and status changes commit together. Modern
in-flight claims survive ordinary concurrent startup. Unsupported historical statuses stop the upgrade for review.
Unknown records without a usable logical identity conservatively block the same action type and Domain. Reconciliation
releases that hold; reopening does not undo the resolution. Audit identities remain readable after archiving.
Recovery snapshots are for controlled offline reconciliation, not direct activation as action authority. The encrypted
restore flow continues to keep action history in `actions.quarantined.db`; no restored snapshot grants dispatch.

A calendar modification `--show` reads the current event, shows before/after values, and binds its ETag into the queued
payload with a compare-and-set. A changed payload or lifecycle refuses the receipt. Dispatch requires that ETag, checks
it on a fresh read, and sends `If-Match`; HTTP 412 cannot trigger an overwrite retry. A matching returned event ID and
new ETag are required for success. Undo is another reviewed `calendar_modify` proposal bound to that resulting version.
The action remains held for real provider acceptance. This implements the provider's documented version condition:
https://developers.google.com/calendar/api/guides/version-resources

The compact preview is followed by the complete canonical approval payload. Fields beyond the former truncation
limits remain visible; content over the review bound is refused without a receipt. Partial, unrecognized or malformed
execution results retain uncertainty and duplicate protection. A partial To Do sync cannot report full success.
These local trusted-code controls do not implement the remaining credential broker or general Fact authorization binding.


### Corpus permissions and output evidence (2026-10-21; contract 0.4.0)

Ledger migration 0013 retains resolved Signal/source subjects, including negative Signals. Scope checks govern corpus
prefill/label/load and source coverage inspection; missing provenance is denied. Source view revocation hides retained
cases. Corpus files participate in snapshots, journaled erasure and tombstone replay on restore.

Human labels and evaluated outputs have separate authority. A returned Briefing records its actually rendered, cited
P1/P2 obligation keys, Catch-up ID, permission generation and implementation/pack/prompt version inputs. A Fact or
historical Alert row is insufficient. `python -m catchup_core.corpus_cli evaluate --catchup-id ID` joins reviewed labels
to the named retained result; it never reuses v1 stored `surfaced` values. Goal correction labeling accepts JSON
`--before` and `--after` observations, which must differ. These receipts do not establish independent model quality or UAT.

The Goal writer supports `--owner` and `--metric-definition` (a JSON object) on create/update. Dated metric definitions
require Domain, predicate, subject, explicit source scopes and an accountable owner. Unknown flags and incomplete
contracts fail before publication. Dated observations use capture; `--metric-current` cannot override a source-backed
metric. The writer preserves the body, serializes processes, detects intervening file edits and flushes publication.
A busy writer returns exit 3; inspect and retry. Source-backed outcome/habit support remains opt-in.


## Logical Catch-up measurements (contract 0.5.0)

Invocation metrics use `artha.metrics.invocation.v2`; migration 0014 reserves a sequence before each attempt. `health`
combines pauses/resumes into one Catch-up, reports wait-inclusive and active latency separately, and does not count a
completed replay as another sample. Missing sequence history or an entirely lost append stays unverified/unknown.
Current implementation cohorts prevent older code from supplying the current code's latency evidence. Pending/unknown
Catch-ups remain in the completion denominator. Reads use a bounded recent tail; omitted history cannot establish a pass.

Trace v2 uses distinct invocation/stage span IDs inside one Catch-up trace. Existing traces remain unchanged. Cost uses
recorded sizes from paused work too, with estimates separated from explicitly reported usage. The estimate does not enforce cash billing;
`budget_enforced` remains false. Request admissions have separate durable bounds. Actual measurements and
release evidence remain required; a usage receipt does not independently verify provider billing.

### External checkpoint dependency continuation — 2026-10-24

Local code `d1b7158` captures configured external action databases (including committed WAL rows) and external
privacy capsules. Physical installation/action fences are deduplicated; executing/unreadable action state blocks
before escrow access. Rediscovery under the fences and before publication rejects new dependencies. Known Work
OAuth/recovery files are excluded from personal capture; conflicting roots and denied metadata block capture.
The completion checklist records the passing synthetic/process gate and remaining full-inventory, Work cache,
writer-fence and native recovery work. This is not a complete preservation or cutover acceptance.

### WorkIQ cache privacy continuation — 2026-10-24

WorkIQ source records and narratives now persist as verified age ciphertext in the machine-local
`work/cache/workiq.json.age` namespace. The shared OS `age-key` / `artha` identity decrypts it; there is no
environment-secret or plaintext fallback. Private source text stays in memory during encryption/decryption.
Preflight has a separate bounded health receipt with only availability/authentication flags, platform and UTC
time. It never persists the probe's identity response. Both writers share the Work cache process lock with Work
checkpoint capture. A restored installation cannot use these cache/probe entry points to resume activity.

Cache identity binds connector implementation, resolved redaction policy, result-affecting scope/depth and
supplied account/tenant IDs. Malformed/future-dated entries
are misses. This does not attest the provider's actual signed-in account. Concurrent writes merge under a lock.
Cache read failure is a Connector failure; persistence failure retains fresh positive records with an explicit
processing gap. Rich calendar/email/Teams templates now supply their required lookback parameters.

Legacy plaintext is never reused automatically. The hand-run maintenance interface is:

```sh
PYTHONPATH=scripts python -m catchup_core.work_cache plan
PYTHONPATH=scripts python -m catchup_core.work_cache apply --sha256 <reviewed-digest> --writers-stopped
```

The owner must stop old writers before applying the reviewed digest. Plan reports only bytes and SHA256. Apply
refuses linked/reparse directory ancestry, preserves and decrypt-verifies a content-addressed encrypted archive,
rechecks the source, then replaces it with
an inert read-only retirement marker. Archived legacy data is not fresh cache evidence. Retry after interruption
uses the verified archive; changed source or archive refuses. The new lock cannot stop an already loaded old
writer; ordinary read-only behavior on Windows still needs native acceptance. Prior synced copies/cloud history
and artifact retention/deletion need separate owner-reviewed handling. No live migration has been executed.

Personal checkpoints exclude the local Work namespace, known legacy WorkIQ cache/breaker files and interrupted
legacy cache temporaries. Work inventory captures known legacy cache/OAuth/recovery artifacts separately, without
unrelated sibling files. Local roots containing the repository are refused, and physical Work exclusions apply
to each captured root. Work source-cache writers are fenced; other Work writers and full OS recovery-key inventory
remain open. The private ignored breaker still stores only technical state in its legacy location. This change
does not establish complete Work isolation, a native security boundary or release acceptance.

### Approved admission and retention policy (2026-10-24)

The owner approved 2,000 request reservations per UTC month and 64 pending Signals per selection,
with 16 requests/512 KiB prompt bytes per bundle, 64 requests per logical Catch-up and three attempts
per Signal. `config/catchup_core.example.yaml` contains the explicit `resource_limits` mapping.
These bound request admission; they do not establish assistant cash billing or independently verify
reported token counts. Missing usage stays unknown. Usage receipts are immutable, and increased actual
usage advances the external spend seal before acknowledgement; an exact retry repairs a failed seal write.
Shadow and authoritative spend histories use separate namespace records. A missing previously registered
seal blocks admission rather than granting a new budget. Missing fresh-host custody still requires the
recovery/privacy ceremony; deleting metadata is not a supported budget reset.

The owner chose retain-all for cutover: `retention: {mode: retain_all}` explicitly retains all classes with no automatic expiry periods. Explicit
forget remains available. Source/replay expiry mechanisms do not establish general automatic erasure of
all document, corpus, Briefing, trace, history or checkpoint copies; those classes remain retained and
unsupported expiry is reported. Review separate periods and dependency exceptions before enabling expiry.

Authority transfer applies to the shared personal Ledger/action queue. A Work content checkpoint cannot
authorize that promotion. The former host's signed retirement is a software fence, mirrored in its queue
and marker; it is not proof that the provider revoked a credential on another machine. Recovery, native
host/cloud rehearsals, provider parity, independent quality/measurement evidence and owner UAT must be
reviewed before the release setting or action holds change.


### Required release acceptance evidence (scorecard v4)

Old v3 scorecards cannot authorize a new release. V4 retains exact implementation/configuration/model cohort
binding and requires an independently labeled held-out corpus, identified by `corpus.id` and `cohort`.
Declare development obligation/thread IDs and `development_end_at`; each evaluation binds its `corpus_id`,
held-out split, obligation/thread IDs, `as_of` and `latest_evidence_at`. Overlapping development groups,
future evidence or inconsistent chronology refuse release. These are reviewed corpus declarations; the
validator cannot independently discover mislabeled source data.

Per-Domain `completed_runs`/`total_runs` must meet the existing 95% complete-run requirement. Workload
records bind the same cohort and reference machine for `normal_cold`, `normal_warm`, `long_gap`,
`provider_reset`, `retention_1y`, `retention_2y` and `digest_limited`. Normal workloads match approved bounds;
the limited Digest skips fetch/vault and finishes below ten seconds. Long-gap/reset records explicitly
report fair progress, honest incomplete processing and preserved verified Alerts; retention-volume records
report verified queries/restores without plaintext fallback. Missing evidence is a refusal, not a pass.

`document_fields` binds that same corpus/cohort. Report integer `correct`/`total` separately for dates,
amounts and names, and `false`/`total` for false confirmations. The owner-approved measured floors are
99% accuracy and a 0.5% false-confirmation ceiling. Zero denominators and Boolean counters refuse. These
ratios do not establish statistical confidence or sample adequacy; independent evaluation must review those.
Below-floor evidence cannot authorize release; ambiguous OCR and consequential interpretations still need
exact field confirmation. No real corpus observations were generated by the implementation tests.

### Encrypted historical-file recovery quarantine (2026-10-25)

Personal `checkpoint_cli restore` requires the independently current local owner/privacy context before it
can restore historical material. Authenticated canonical personal/shadow ledgers pass schema, integrity,
foreign-key and exact page-image checks before plaintext output; replayed ledgers are checked again.
Other members stay encrypted in `.artha-quarantine` under the inactive restored-installation fence.
The original checkpoint is unchanged. This does not activate the restored installation or install OS keys.

Metadata-only `quarantine-list DEST` reports opaque member IDs. `--details`, review, content view and release
require current recovery view/export grants from every protected subject. Historical grants are not current
authority. The encrypted catalog captures original source-affinity before first seal so known erasures can
be recomputed even after SQL purge and authenticated old-catalog rollback. A prototype catalog missing this
provenance must be reconstructed from the original checkpoint; no provenance is inferred from purged rows.

Use the existing `python -m catchup_core.checkpoint_cli` command family (with `scripts` on `PYTHONPATH`):

1. `quarantine-list DEST` to obtain an opaque `MEMBER_ID`.
2. `quarantine-review DEST MEMBER_ID notes.txt` to obtain an exact review token.
3. `quarantine-view DEST MEMBER_ID notes.txt --review TOKEN` to inspect bounded UTF-8 text as escaped,
   explicitly untrusted JSON. Do not render the content as HTML/Markdown or persist it in agent logs.
4. After actual human review, `quarantine-release DEST MEMBER_ID notes.txt --review TOKEN --attest-clean`.
   This publishes owner-private, write-once **encrypted** `reviewed-exports/notes.txt.ae1`, not plaintext.
   Attestation means human review, not automated proof of arbitrary prose lineage or another adult's consent.
5. `quarantine-export-view DEST notes.txt` reads an already reviewed encrypted export without the original
   short-lived token, while requiring current content authority/catalog/privacy binding. It prints only bounded
   UTF-8 escaped JSON; no named plaintext file or activation is created.

Keys, executable/configuration files, databases and known denied artifacts cannot become clean-file exports.
Changed actor, permissions, privacy history, catalog, original member or output binding refuses reuse. Metadata
access cannot read content. Missing/unsafe files, competing publication and mid-operation revocation refuse.

Bounds refuse rather than truncate: 16,384 members, 8 MiB catalog, 1 MiB chunks, 65,536 chunks total,
16 GiB per historical member and 64 GiB total. Proven plaintext ledgers have an additional 256 MiB serialized-image
preflight/replay limit; peak working memory can be higher. Text views are at most 256 KiB; encrypted clean exports at most 16 MiB.
Extracted quarantine directories are not portable: their original physical directory identity is authenticated.
Preserve the original encrypted checkpoint and reconstruct on a different destination using current privacy proof.

The native Windows backend now implements handle-relative/no-reparse access, private DACLs, stable identities,
locking and publication, with actual Windows synthetic coverage. This does not establish live native
credential-store/cloud recovery, owner UAT or cutover acceptance. Lost-device Work activation remains held.

### Same-host Work privacy lifecycle — 2026-10-27

The owner approved same-host-only Windows Work recovery and explicitly held Agency subprocess and DC-3
reflection snapshot/rollback for initial enrolled use. The resulting degraded capabilities must be disclosed.
Supported entry points refuse these operations; model file tools are not host-confined. Direct unjournaled
edits trigger drift refusal, not retroactive prevention.

Explicit lifecycle commands use the same checkpoint CLI:

- `work-privacy-status`: bounded metadata only; never enrolls or repairs.
- `work-privacy-enroll --reviewed`: explicitly enrolls native identity and current inventory.
- `work-privacy-reconcile --reviewed`: adopts reviewed current bytes while invalidating older versions.
- `work-privacy-recover-pending`: rolls authenticated interrupted intent forward only.
- `work-privacy-revoke --member work_state/notes.md --reviewed`: permanently restricts recovery of that member.

Do not invoke these live operations merely to validate code. Missing review refuses before effects.
Enrollment binds Windows machine/user, random installation and canonical `state/work`/`local/work` roots;
the dedicated identity key/current history are never captured or restored. Pending, drifted, mismatched
or missing authority blocks capture/recovery. Old unbound checkpoints stay inspection-only.

Work capture retains encrypted cache members, but cache authority, keys, credentials and configuration
cannot become clean exports. Historical Work prose goes to encrypted quarantine with current-history
checks and exact per-file human review. Restored copies never activate or dispatch. Missing original
identity/key/history permits inspection only; no key installation or lost-host promotion.

Employer-specific producers/tests remain private and gitignored. Opted-in
`python tools/run_tests_isolated.py --private-work --evidence ABSOLUTE_EXTERNAL_JSON_PATH`
uses a reviewed source/synthetic-test allowlist, validated nonsynchronized temporary storage and metadata-only
external evidence binding public/private bytes, runtime, selection and exit outcome. Default shared CI
cannot certify those private producers. This is a cooperative test workspace, not a host sandbox.
