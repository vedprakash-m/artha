# Changelog

All notable changes to Artha are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added / Fixed — public release hardening and re-artha live fixes, 2026-11-01

| Change | Modified files | Tests |
|---|---|---|
| `consent grant` opened the ledger read-only, so a real grant failed after the person typed their name. A grant now counts as a mutation (`cli._mutates`); `consent list` stays read-only. | `scripts/catchup_core/cli.py` | `test_consent_cli.py` (grant mutates, list does not); `test_wiring.py` now asserts the `usage` mutation through the helper |
| Personal checkpoint inventory was blocked by the empty `authoritative.json.lock` and `shadow.json.lock` files that Catch-up runs leave in the spend-seal directory. Those two lock names are accepted (regular, single-link); any other stray file still blocks. | `scripts/catchup_core/checkpoint_cli.py` | `test_state_checkpoint_cli.py::test_spend_seal_lock_files_do_not_block_capture_but_other_artifacts_do` |
| The privacy gate now refuses to push commits whose author or committer email is not a GitHub noreply address (or a reserved `.invalid` test address). Commit identity is published with every commit and was not scanned before. | `.githooks/privacy_scan.py` | `test_privacy_gate.py` (personal email refused, noreply allowed) |
| Two quarantine review tests replaced the last ciphertext byte with a constant and failed about 1 run in 256 when the byte already matched; a helper now flips it. | `tests/catchup_core/test_quarantine_review.py` | helper test plus both parametrized cases |
| Ledger test searched random base64 for a 3-letter token; it now checks the whole plaintext phrase. | `tests/catchup_core/test_ledger.py` | `test_open_item_description_is_encrypted` |
| CI installs `age` on Windows from the pinned upstream v1.3.1 release with a SHA-256 check (Chocolatey has no such package). The canary test accepts the new step. | `.github/workflows/ci.yml`, `tests/dev/test_check_canaries.py` | `test_ci_native_encryption_jobs_install_age_before_tests` |
| Public tree scrubbed of owner-specific location, persona, school, employer and internal program names; history replaced by a single commit. Code identifiers, file names and regexes were left unchanged. | docs, prompts, config prose, `scripts/pr_manager.py` persona strings, test fixtures | full isolated suite (9,783 passed before the manifest refresh) |

Known open: the Windows contract jobs fail on GitHub-hosted runners with `filesystem object belongs to another owner`
(elevated runner; new files are owned by Administrators). The fix needs a Windows session and a security review.

### Verified — Windows Work UAT, 2026-11-01

The isolated tracked Work/UAT/checkpoint selection passed **558 tests** with one dependency-version warning;
the private-work verification safeguards passed **54 tests**. The separately opted-in local synthetic
producer selection (`tests/work/test_work_publication_producers.py`) exited 0 under the source-pinned runner,
which recorded metadata-only evidence for 24 allowlisted private source files and suppressed pytest output.
The gitignored `tests/work/` suite was not run. These results do not verify a live WorkIQ provider, live State
Files or owner UAT.

Changed: `tools/uat/README.md` and `docs/spec-manifest.json`. No live provider or State File was accessed.

### Fixed — Catch-up release and Open Items guidance, 2026-11-01

The routed Process phase now refers to the canonical Core-owned Open Items fence. That fence identifies
the incumbent `kids` alias, preserves the approved writer during a blocked or degraded core run, and tells
the assistant not to write affected Open Items when ownership is unknown. The Catch-up and identity
instructions distinguish shadow comparison from release-gated authoritative publication and the signed
slice waiver from the full-v4 scorecard. These are instruction changes, not a new runtime enforcement
mechanism; current authority-host behavior and owner UAT remain unverified.

Changed: `config/workflow/process.md`, `config/workflow/catchup.md`, `config/Artha.core.md`,
`docs/catchup-core.md` and `tests/unit/test_catchup_workflow.py`. Verification: 80 isolated workflow,
core Open Items and slice-release tests passed; 34 identity-generation tests passed; the four focused
instruction regressions passed after the final edit. No live State Files or release settings were changed.

### Verified — Windows results for commits `9730456^..cb033ea`, 2026-11-01

Full isolated Windows run of `tests/catchup_core` and `tests/uat` (`tools/run_tests_isolated.py`, Python 3.11)
on `01e4f8c`: **4502 passed, 109 skipped, 0 failed**. Later commits `d0fb802` and `cb033ea` were checked with
targeted tests only (Work cache 33/33; consent and register 156 targeted tests), ruff, mypy and register
`--check`. All commits are local; no push occurred. Changes under `scripts/catchup_core` invalidate the
signed release: re-signing and live runs are pending on the Mac, and owner UAT is pending.
Manifest/status-only commits in the range: `695a04c`, `0c7cd5d`, `b6d6ac4`, `7aa2184`, `0f97828`,
`a0421f8`, `33c5dd3`, `3bdbcd5`, `ac165bd`, `d11a43f`, `885fc4a`, `be99004`, `c01c3a2`, `1752590`,
`cc4d8f5`, `01e4f8c`, `f7cafe4`.

### Added — Consent command and curated-scope consent prompt, 2026-11-01

New `consent grant|list` command (`catchup_core/consent_cli.py`): interactive only; an adult reads the
scopes and types their own name to record consent in person (RA-12). The prompt for a
`curated:open_items` grant no longer calls it "mail and calendar items"; it says the grant covers open
items entries that may concern the person, including entries that name no one. A new test reproduces a
degraded run: Open Items with no subject or owner tag need consent from every consent-required adult, and
without it required `open_items` coverage is unavailable. This is the specified behaviour; no code change.
Commits: `aa064b7`, `a28594b` (merge `b2716dd`), `cb033ea` (consent part).

### Added — Insurance, travel, digital and pets Domain packs; finance payment wording, 2026-11-01

New packs at 0.1.0 with synthetic fixtures: insurance (renewal and premium due dates,
`premium_payment_status`, open claims), travel (departure and check-in events, passport/visa expiry),
digital (subscription renewal, domain expiry, failed payment) and pets (vaccination, vet appointment,
licence renewal). `extract.py` accepts `pets` as a non-pilot obligation Domain. Finance pack 0.7.0:
`payment_status` aliases ground "posted", "being processed", "payment successful" and "ACH Success
Payment" as `received`. A new per-predicate `unless` key rejects a sentence that also says failed,
declined, returned, reversed, scheduled, pending, will post or cancelled.
Commits: `f75a288`, `345bab2`, `685adda`, `a513daf`; merges `e277115`, `780cc2b`, `f0896ad`.

### Fixed — Date and datetime grounding, 2026-11-01

A datetime value now grounds when the quote has a natural-language date and a time that resolve to it.
A missing time, a contradicting weekday, an unknown or conflicting zone, or "3:30" without am/pm does not
ground. Numeric dates use the same month-first proof rule as date-only grounding. "The Nth of this/next
month" resolves from the message observation date, not the wall clock; December rolls to January,
non-existent days do not ground, a bare "the 25th" stays ungrounded and negation still rejects.
Commits: `a9e8a26`, `e6cfd50`, `a2f2e8b`; merges `9b45047`, `a352dbc`.

### Fixed — Windows hardening, 2026-11-01

`core_open_items.md` is written with a private DACL on Windows (`blobs.private_atomic_bytes`). A legacy
non-private copy written before that change is removed once and republished privately instead of failing
every later authoritative run at finalize. Preflight now finds listening Artha daemon processes on
Windows (`netstat` plus process command lines) and prints a Windows fix hint. Health-check compaction now
writes the `domain` and `last_updated` fields the schema guard requires, and the script-health check skips
trailing Python warning lines when it reports a failure reason. The Work cache process-death rehearsal
now runs natively on Windows instead of skipping.
Commits: `75bb7ec`, `243ad6a`, `1706e79`, `67ca197`, `d0fb802`.

### Added — UAT scenario scripts and capability register closure routes, 2026-11-01

`tools/uat/` scripts for RA-06, RA-09, RA-15, RA-19, RA-20, RA-21 and RA-23 run the `catchup_core` CLI
and print a JSON report of steps, expected and observed results. `--scratch` uses a synthetic workspace;
live mode runs read and preview steps only, and local writes need `--allow-local-writes`.
`tests/uat` runs every script in scratch mode. These scripts have not been run live. The capability
register gains a "Closure route" column (`capabilities.CLOSURE_ROUTES`); it states a route, not acceptance.
Commits: `173a6ac` (merge `d73b355`), `cb033ea` (register part).
`tools/uat/wp_19_work_cache.py` (scratch only) runs the Work cache migration `plan`/`apply` steps of the
WP-19 sheet against a synthetic legacy cache and a throwaway age key, with the Work journal forced to not
enrolled; it has no live mode. Commit: `23152c0`.
The scratch script also exercises the shared WorkIQ health-receipt writer through both preflight paths with
synthetic successful, failed-authentication and unavailable probes. It checks the exact technical fields,
UTC timestamp, cached reuse and absence of probe output; network and process access are blocked during
these checks. Fourteen scratch steps and 35 isolated UAT/Work integration tests passed on Windows. This
does not establish that a live Work briefing produced a receipt or replace owner UAT. The three
in-process receipt tests covered 48 of 50 executable lines (96%) in the new receipt scenario; this
measurement does not include subprocess coverage.
An isolated checkpoint regression checks that known synthetic Work cache, OAuth, breaker, temporary and
credential-recovery artifacts are excluded from personal inventory and included in Work inventory. It also
checks the ephemeral cache lock and aggregate-only CLI output. Live inventory membership remains unverified.
UAT guidance distinguishes the WorkIQ preflight receipt from a read-only Work Briefing and notes that a fresh
receipt may be reused without another probe. No live WorkIQ probe was run.

### Changed — Catch-up core gap fixes from shadow runs, 2026-10-31

The core writes `state/core_open_items.md` for released Domains, listing each obligation once. A slice
release may name several Domains. Finance, immigration, health, home and vehicle packs read their own
email sources. Calendar events that name no single person no longer hide calendar coverage. A payment
fact settles its bill (`resolves` key; finance up to 0.6.0: any received payment settles its bill). Stale
recall facts keep their severity; vehicle recall notices raise P2. New pack keys: `lapse_days` drops
dates past the point anyone can act on; `aliases` maps enum values to message wording. Reminder
proposals are queued for future-dated obligations for owner approval. Script launch of
`catchup_core/cli.py` no longer lets `catchup_core/actions.py` shadow the `actions` package; the
instructions use the module entry point for authoritative runs.
Commits: `eac621f`, `bdd8ca0`, `0aa0a31`, `4cacc82`, `fd9ecae`, `2852c6d`, `c2e90e4`, `ac7c4e7`,
`6946ceb`, `53de652`, `c564d6c`, `3a018d1`, `3ad8b86`, `6077937`, `548a98f`, `a683357`, `a90819a`,
`89a13da`, `dd708c0`.

### Added — First-slice release profile and recovery-receipt digest, 2026-10-29

`release.profile=slice` keeps owner approval, Stage 1 clearance, rollback snapshot, independent copy and
recovery rehearsal, and replaces the v4 scorecard with a signed waiver naming the six waivable gates.
The full profile is unchanged. Under the slice profile the recovery receipt waives only Windows Work
recovery. New `checkpoint_cli privacy-digest` prints the verified privacy-history digest that recovery
receipts need, after the owner authorization gate. Added `docs/catchup-core-rollback-runbook.md` and
status-document records.
Commits: `d1e72f1`, `174d16e`, `38dcca8`, `4537362`, `1893db3`, `9730456`, `8c39317`, `ff315bf`.

### Observed — Owner-approved local-storage adoption, 2026-10-28

After separate exact preview approval and stopped-writer attestation, the existing explicit command
changes only the local-root/installation-lock DACLs and exits 0. Readback is private/eligible, inventory
not enrolled; identities, owners, sizes and LastWriteTime remain unchanged. The maintenance operation
is not enrollment, recovery, whole-store hardening or owner release/UAT sign-off. Both previously
authorized registry metadata previews still refuse; read-only diagnosis confirms expected refusal of
missing profile inventory, not an adoption/bootstrap defect. No registry/directory/key was created.
No key/backend, provider effect, recovery, cutover or push occurred.

### Verified — Local Windows implementation checkpoint, 2026-10-28

Frozen-source gates pass: full Windows Python 3.11.15 **9,573 passed, 186 skipped, 20 xfailed**;
core **93.13% combined / 94.31% line / 89.92% branch**; mandatory security **1,684 passed,
77 skipped, 88.04%**; changed core lines **10,922/11,738 (93.05%)**, including untracked modules.
No source drift across aggregate gates. Native 3.12 changed boundaries pass **219 tests, one skip**.
Opted-in private Work source-pinned synthetic verification exits 0 with cleanup. All results remain
local; earlier failed measurements are retained. The checkpoint covers the release/corpus/Work
publication and explicit storage-adoption changes described below.

At this code checkpoint, read-only native storage preview was legacy/eligible with inventory not enrolled. No live ACL adoption,
keys/enrollment, provider effects, native recovery, independent acceptance, owner UAT, cutover or push
occurred. Active feature/baseline specs remain unarchived. Final documentation/register/privacy checks
and local delivery are separate follow-up checks, not claims of live release readiness.

Final lint/typing/import/writer/schema/register/whitespace and working/history privacy checks pass.
Local code/test/delivery checkpoint: `a5b49f2`, with the actual staged pre-commit hook passing;
canonical documentation is delivered separately. Git-only CRLF/LF normalization affects ten staged
paths, without other content differences. Native measurements bind recorded working bytes, so
intended deployment source/cohort equality still requires explicit verification. A temporary session
alias supplies the existing Python 3.11 executable to the `python3` hook shebang; no hook bypass,
runtime installation or permanent PATH change occurred.

### Fixed — Release validation, corpus provenance and Work publication, 2026-10-27

| Area | Change |
|---|---|
| Release evidence | Explicitly refuse malformed clearance/scopes/review values, coerced or inconsistent total/live counters and invalid precision floors; preserve v4 numerical policy. |
| Corpus | Do not invent live provenance from labels; reject malformed/rebound identities and reauthorize frozen outcome disclosure against every current source permission. |
| Work publication | Serialize batch commit/discard with copied-context workers under the existing lock, preventing staged-write escape and lost replacement. |

Modified: release policy/acceptance tests, corpus/provenance tests, Work publication/tests and directly
related specifications/documentation. Reproductions precede repairs. Windows Python 3.11 release
integration passes 444 tests; parent isolated corpus selection passes 40; owned Work selection passes 94.
Native investigation passes 114 tests, one skipped, without establishing the intermittent failure cause.
Final integrated aggregate gates remain pending. No live enrollment, recovery, provider effect, cutover
or push occurred.

First-wave frozen Windows Python 3.11 gates subsequently pass: security 1,622 passed/77 skipped,
87.82% combined; core 4,123 passed/109 skipped, 92.99% combined/94.22% line/89.66% branch.
Existing Make/privacy canaries pass 85 tests; no tooling was installed. These results precede the
separately approved legacy-storage adoption workflow and do not certify its implementation or UAT.

### Added — Reviewed legacy Windows storage adoption, 2026-10-27

The existing checkpoint CLI adds `storage-preview` and exact-reviewed `storage-adopt` for only the
configured local root and existing installation lock. Preserve owners/payloads/keys/inventory and
stored descendant DACLs; reject unsafe roots/identities, changed previews and cooperating-writer
contention. Partial changes require fresh review; already-private state is a verified no-op.
Stopped writers are attested, not host-confined. Root access/direct-child inheritance intentionally
change; no SACL measurement or unchanged every-principal reachability is claimed.

Modified: new adoption module/tests, native filesystem helper, checkpoint CLI, capability/security
registration and canaries, canonical specs and operator documentation. Native focused verification:
166 passed, one existing skip, 100% new-module coverage. Twelve root-boundary regressions failed
before repair; three registration canaries failed before wiring. Final source-frozen gates remain
pending. No live ACL change, inventory enrollment, recovery, release or push occurred.

The aggregate unknown-error injection failure was reproduced to cached-instance monkeypatch teardown;
restore original attribute absence and prove the later injection actually executes. Runtime refusal
semantics are unchanged. Machine-local volume checks also reject remote/unknown/missing/CD-ROM
classifications before resolution/target access, with local fixed/removable/RAM support preserved.
Final native/adoption focused verification: 174 passed, one existing skip, 100% module coverage.
Earlier aggregate failure remains recorded; no passing final gate is inferred from this focused result.

The full-suite Domain-neutrality scanner also caught a generic filesystem local variable named `home`.
Rename it `user_directory`; do not expand the grandfathered list or change Domain vocabulary, messages,
constants or runtime guards. The targeted parent architecture/adoption/register/Make selection passes
112 tests after repair. Final passing aggregate results are still required.

### Fixed — Bash UTF-8 parity and private Work verification, 2026-10-27

Generated Bash hooks now invoke Python in explicit UTF-8 mode, matching PowerShell. The actual Windows
3.12 Unicode defect was reproduced before repair. Hook tests pass 14 cases each on Windows Python
3.11/3.12/3.13, both shells and no skips. No real hook installation was performed.

The isolated test runner adds explicit opted-in private Work source verification with a fixed reviewed
allowlist, public/private source/runtime/selection hashes and metadata-only evidence outside the checkout
and known sync roots. Missing/changed/unsafe source, unsafe evidence and cleanup failures refuse; default
copying/ignore rules are unchanged. Modified: hook generator/tests, isolated runner and its tests, release
cohort registration/canaries and related specifications. Final runner validation: 72 passed, 96% coverage.
Actual opted-in source-pinned private synthetic verification runs twice with exit 0 on native Python 3.13;
minimum-runtime tooling/registration: 80 passed. It is not provider, native recovery or owner acceptance.

Work producers now serialize threaded sessions, refuse inaccessible enrollment before legacy fallback,
validate bound paths before reading and journal dashboard publication. Cache/staging are preserved without
clean-export authority. Recovery adds current-bound encrypted quarantine and explicit reviewed lifecycle
commands; focused recovery aggregate: 996 passed, 80 skipped, plus 12 final regressions. Parent strict mypy
passes 64 core files. The owner explicitly retained Agency/DC-3 holds with disclosed degraded capabilities;
arbitrary host/model file tools are not confined. The first integrated security gate fails at 84.17% despite
1,352 passing tests; missing checkpoint-CLI integration tests join the selector after a failing canary.
Full native/core/security gates remain pending; nothing was pushed or activated.

The diagnostic full minimum-runtime rerun reported eight failures: stale generated evidence and a
session-only plugin leaking into nested synthetic pytest processes. Registers were refreshed and diagnostic
scope repaired; all 76 affected development tests pass with instrumentation. This is not a passing full
gate or an explanation of the earlier intermittent native refusals. The subsequent full isolated Windows
Python 3.11.15 invocation exits 0: 9,446 passed, 186 skipped, 20 xfailed, 11 warnings, 3,135.50 seconds.
Both Ruff checks and 64-file strict core typing pass; current security/core coverage gates remain open.

### Fixed — Windows hook encoding and release evidence validation, 2026-10-26

| Area | Change |
|---|---|
| Native hooks | Explicit UTF-8 subprocess transport and Python UTF-8 mode preserve synthetic Unicode through generated PowerShell hooks, including a non-UTF-8 Windows parent/child environment. |
| Release evidence | Reject malformed nested JSON containers, coerced/duplicate obligation labels and nonboolean case flags with explicit release refusal; retain valid v4 artifacts and existing numerical gates. |
| Recovery scope | Owner approved same-host-only Work recovery using existing current privacy authority; lost-host activation stays held. Implementation/rehearsal completion is not implied by the amendment. |

Modified: hook generator and portability tests; release policy and acceptance-v4 tests; active baseline,
WP-18 and implementation status. Reproductions precede the fixes. Agent selections passed 13 hook tests
(one skip), 215 acceptance tests and 41 adjacent release tests. Actual Windows Python 3.11.15 combined
hook/release/artifact selection passed 273 tests, one skip; 88 environment packages pass dependency checks.
Synthetic 480-Signal cold/warm Catch-ups took 12.135/0.632 seconds, with zero warm model requests.
These measurements are not live provider/recovery/UAT or release evidence. Full gates remain pending;
no production state, credentials, cutover, real hook installation, commit or push changed.

Native delivery/privacy fixtures now use explicit interpreter, Bash, UTF-8 and Git-discovery boundaries.
The privacy scanner's real reporting path supports non-UTF-8 Windows stdout. The bounded selections pass
85 tests on each of Windows Python 3.11 and 3.13, with no skips. The credential migration death fixture
terminates the actual Windows interpreter worker before closing stdin; its focused selection passes
100 tests, eight skipped. Modified: the two delivery/privacy test files, privacy scanner, credential,
legacy-writer and degraded-environment fixtures. Full aggregate validation is not yet repeated.

The approved local Work privacy protocol foundation adds authenticated installation/member history,
journaled publication, drift refusal and checkpoint binding; 68 focused tests pass. Producer/cache and
recovery integration remains in progress. Both new authority modules join mandatory security coverage,
and tracked Work producer bytes join the public release fingerprint. Intentionally ignored employer wiring
remains private; the owner approved separate local source-pinned integration evidence. Modified: Work privacy module/tests,
capability registration/canaries and Makefile. No existing module selector or coverage floor was removed;
no Work recovery, enrollment or release is claimed.

### Fixed — remaining recovery bounds and minimum-runtime verification, 2026-10-25

POSIX recovery now enforces the existing serialized ledger-output ceiling before writing; exact equality
remains accepted. Privacy capsule replay enforces its existing plaintext ceiling before JSON decoding,
matching publication. Synthetic tests preserve original bytes/head/ciphertext after refusal and cover a
valid-ciphertext final-reread substitution. Eight unchanged pure Windows syntax tests now execute on Mac;
real native filesystem tests remain native. No gate floor or platform selector was removed.

Modified: recovery access/privacy history and existing recovery/quarantine/privacy-capacity tests;
active WP-17/WP-18 proposal sections and current documentation. Reproducers failed before repair.
Actual Mac Python 3.11.15 full suite: 9,042 passed, 264 skipped, 20 xfailed; later capsule/pure-test changes
have focused 53-test Python 3.14 and 45-test Python 3.11 evidence. Mac 3.12 full suite passed 9,052 tests
(256 skipped, 20 xfailed). Privacy-history security registration was added after a failing canary; current
core coverage passes at 89.97% combined/90.81% line/87.68% branch (3,770 tests). Expanded harness failed at
73.53% versus 85% despite 1,179 passing tests, with all platform modules retained. Comprehensive changed-line
coverage passes 9,338/10,714 (87.16%), explicitly including the untracked native backend via a disposable index.
Final 61 gate/register tests, static/schema and working/history/staged privacy checks pass. Coverage union,
optional applicability and Work custody proposals remain unapproved; no native/live recovery, provider effect,
UAT, cutover, real-index change, commit or push. Native/recovery/independent acceptance remains required.

### Fixed — Mac takeover audit and evidence fidelity, 2026-10-25

| Area | Change / verification boundary |
|---|---|
| Evidence | Preserve user confirmation and independent corroboration through revisions/disputes; resolve eligible matching evidence in place; disputed appointments are not actionable conflicts |
| Time | Retain Signal observation/occurrence and unknown validity; document upload/ingestion cannot invent omitted years; factual field confirmation does not authorize an effect with unknown applicability |
| Recovery | Hold unsafe legacy restore; publish through exclusive temporary descriptors; authenticate presealed credential inputs with existing scoped keys |
| Runtime | Recheck connector configuration bytes before work/publication |
| Delivery | Scan untracked working bytes without plaintext scanner copies; preserve exact exemptions; cover new-ref history/empty-tree diffs; reject empty/untracked coverage omissions; normalize isolated report roots before cleanup |
| Portability | Provision native CI encryption and preserve real host-correct PowerShell/Windows-handle tests |

Modified: core reconciliation/triage/coordinator/documents/history/views/runtime/backup/capability mapping;
credential migration; privacy hooks/configuration/workflows; Makefile, isolated runner, diff coverage checker,
hook generator, synthetic benchmark/scenarios and their existing focused tests; technical/status/handoff
documentation and the local-only active baseline. No branch/worktree/commit/push or real index change.

TDD reproduced ten initial failures and further independent-review precedence/document cases. Final Mac
full suite: 9,042 passed, 263 skipped, 20 xfailed; core: 3,757 passed, 183 skipped, 89.86% combined,
90.71% line/87.53% branch; guarded changed-line coverage: 9,144/9,903 (92.34%). Mandatory security
coverage **failed at 72.49% versus 85%**, despite 1,123 passing tests; native Windows code remains
selected. Subsequent report-path/empty-match tooling repair passed 51 focused tests, not another full
suite. Static and actual working/history/staged privacy checks passed before final documentation.
Current-source supported-platform/security validation, Work-privacy/validity protocol decisions, live
recovery/evaluation/workloads and owner UAT/cutover remain open. Full evidence and parallel exit plan:
`docs/re-artha-implementation-status.md`. No live payload/key/provider operation or release is claimed.

### Changed — owner coverage override, 2026-10-25

| Area | Change / verification boundary |
|---|---|
| Coverage | Owner-approved 85% floors for core combined/line/branch, changed-line and security-harness combined coverage; no tests, module selectors or platform requirements removed |
| Release safety | Product-quality, privacy, recovery, independent evaluation, rollback and owner cutover acceptance remain required; historical gate failures retain their original disposition |

Modified: Makefile defaults, coverage checker default, CI labels, development canaries, technical
contract and active re-artha baseline/status/handoff. New boundary regressions first failed at exactly
85% and the old Makefile defaults (two failed, two passed). Afterward all 22 development canaries
and applicable Ruff checks passed. Synthetic native cold/warm Catch-ups completed at 30/120/480 Signals;
480 took 12.559/0.701 seconds with no warm extraction requests. This is not live p95/cost acceptance.
Both full Ruff configurations, regenerated-register checks and 134 release-fence/coherence/register
tests passed. Actual working-byte privacy passed for 82 files; tracked and unpublished-history privacy
passed. Clean core finished: 3,786 passed, 109 skipped, 33 warnings; 93.18% combined.
The original 95%-target command failed; separate 85%-floor XML validation passed with
94.26% line/90.23% branch. Changed-line coverage at 85% passed (656 missing of 9,876 lines,
93% displayed), excluding the untracked native backend covered by separate module evidence.
No platform/recovery/release completion claim is made.
No live recovery, provider effect, activation or push is authorized by this coverage override.

### Fixed — isolated security coverage evidence, 2026-10-25

The security harness now writes an ignored XML report to the invoking directory before disposable
source cleanup. A failing report-survival canary and a failing path-with-spaces case preceded the fix;
all 24 development canaries pass afterward. Modified: Makefile, `.gitignore`, development canaries
and core/status/handoff documentation. No coverage selectors or thresholds changed in this repair.
Syntax/import/state-write, Windows/Linux-platform strict core typing, strict credential typing,
applicable Ruff and profile schema checks pass. Minimum Windows Python 3.11 validation is in progress
using an existing managed interpreter and a separate external venv; real POSIX/recovery/UAT remain open.
Final cohorts were refreshed/checked; seven register/manifest tests, both complete Ruff configurations
and actual-working-byte privacy for 83 files passed.

Minimum Windows Python 3.11 contracts finished with 4,004 passed, two failed, 111 skipped and two
warnings. Both hook-fixture failures reproduced: a bare managed `python.exe` symlink lacked its runtime
layout. Fixtures now create real no-pip venvs, with a quoted Git Bash launcher on Windows.
All 14 hook portability tests pass afterward on both Python 3.11.15 and 3.13.15; product hook commands
are unchanged. Modified: hook portability tests and generated cohorts; applicable Ruff and cohort
checks pass. This is focused closure of failures, not a claim that the original aggregate passed.

The repaired security target then passed its actual 85% default: 1,180 passed, 77 skipped, seven
warnings, 87.92% combined, exit 0. XML survived disposable-source cleanup (89.32% line/83.93%
branch); the harness enforces combined coverage, not a separate branch floor.
The final repaired Python 3.11 Windows CI selection passed: 4,006 passed, 111 skipped, two
dependency/support warnings, 3,139.78 seconds, exit 0, retained log. This closes the minimum-runtime
gap for that selection; real POSIX/recovery/evaluation/UAT remain open.

### Fixed and audited — native Windows takeover, 2026-10-25 (in progress)

| Area | Change / verification boundary |
|---|---|
| Release identity | Include native quarantine implementation/tests in the cohort; canonical migration/prompt path labels prevent separator-only hash differences between hosts |
| Credential fixtures | Compact oversized-input parameter IDs preserve rejection testing without overflowing Windows' environment-variable limit |
| Native text and paths | Preserve reviewed configuration LF/CRLF bytes; read PowerShell hook JSON as UTF-8; reject portable rooted/drive/alias artifact names; redact OS exception payloads |
| Native concurrency | Use genuine shared/exclusive Windows locks, retain byte-zero legacy interoperability and capture through the owning handle without omitting inventory |
| Artifact custody | Publish new blob/replay/Work-cache ciphertext with explicit protected DACLs; retain bounded same-handle retention reads and refuse unsafe historical targets without migration |
| Action processes | Flush writable migration snapshots and observe a stable native parent handle instead of the transient venv launcher; uncertainty and consumed-once rules remain unchanged |
| Test isolation | Keep code and machine-local test data in sibling directories inside one disposable container; prevent false repository/cloud-storage refusals |
| Gate parity | Repair actual Makefile/privacy canaries; include native filesystem/session-lock controls in harness coverage; configure Windows 3.11/3.12 core and credential/action CI (not executed here) |
| Recovery registry | Type-check POSIX-only APIs behind platform guards; real Windows process termination proves pending-intent preservation, capture/contender exclusion and exact retry with synthetic metadata |
| Completion plan | Reconcile current mechanisms with historical audit gaps; record native engineering, separate Work/current-privacy recovery, independent evaluation and owner release prerequisites |

Modified: capability mapping, recovery registry, registry/manifest/credential-migration tests, canonical technical
notes and current status/handoff documentation. Native filesystem integration is being validated separately;
this entry is not a full Windows or cutover pass. Verified slices: 11 register/capability tests, 30 credential
migration tests, 74 registry tests (68 POSIX-only skips); syntax/import/state-write and tracked/unpublished privacy
checks. The initial recovery baseline failed; the registry/credential subset missed the 95% combined coverage
floor (93.36%). Current full-suite/typing/coverage results and unresolved gates remain in the implementation
status. No native secret installation, production restore, consent/UAT signature, cutover or push.

Further modified: checkpoint CLI, configuration publication, hook command generator and focused tests;
suite-wide pytest payload-ID hook and regression tests. Verified: 38 payload-ID/credential tests and
102 configuration/hook tests (one POSIX-shell skip). First full Windows run: 8,249 passed, 476 failed,
21 errors, 181 skipped, 20 xfailed; its OneDrive scratch location also caused deliberate synced-storage
refusals. Native integration, retention and action/development gates remain in progress; this is not completion.

Later verified slices: 134 retention tests; 221 native lock/capture/retention cases (one POSIX-only skip);
77 action runtime/durability cases; 35 Makefile/privacy canaries; 155 provider/registry-writer/Work-cache/runner
cases (three skips). Both core Ruff configurations and strict Windows/Linux-platform type checks of 63 core
source files passed at their recorded checkpoints. Static Linux typing is not Linux runtime validation.
Aggregate core/coverage verification remains open; no thresholds were reduced. Missing GNU make was installed;
the pre-existing Azure/MSAL/requests environment conflicts were not resolved by unrelated upgrades.

The next native core snapshot failed: 3,503 passed, 81 failed, 109 skipped; 92.13% combined coverage
(93.47% line / 88.49% branch). Remaining failures and the native restored-DACL producer gap are recorded in the
status document. Subsequent scalar CRLF tests passed 123 cases; protected native spend-seal publication and
actual abrupt-death vault checks passed 64 cases; legacy writer/capture fences passed 27 cases (eight existing
skips). These are targeted follow-ups, not a passing aggregate or release approval.

Native restored-output creation now uses protected-DACL roots, markers, directories and files, with
context-managed flush/close before replay and refusal of unsafe pre-existing destinations. The final
producer/integrity/quarantine-restore slice passed 105 tests (six skips), including actual RecoveryAccess
and CLI replay. Full chain/aggregate coverage remains unverified; a broader in-progress attempt was stopped
and is not counted as a pass. Original ciphertext, current-history checks and inactive restoration remain intact.

Runtime resume fingerprints now deduplicate discovered and registered sources using portable labels
(69 runtime/register tests passed). Native source capture refuses unrepresented NTFS streams, including
directory/root streams, through bounded checked-handle metadata inventory and pre-publication rechecks.
Trailing-name fixtures use actual native names rather than normalized aliases. Preservation/checkpoint/lock
tests passed 105 cases with two existing skips, including late empty streams, unavailable metadata and
oversized inventory. Modified: runtime, native filesystem/checkpoint inventory, capability/security selectors,
register/preservation tests and canonical technical/status notes. Cohorts require fresh generation;
aggregate coverage and independent release gates remain open.

Native registry metadata now uses checked owner-private directories/files, bounded reads and flushed
atomic native publication. Integration repaired the preceding installation/session-lock producer rather
than weakening the registry when it refused mode-bit-only Windows roots. Registry/lock/writer tests:
142 passed, 69 explicit skips; release-cohort/isolated-home/runner tests: 85 passed. Modified: registry,
session locks, registry/writer/lock tests and hermetic bootstrap/isolation assertions. Full native recovery
chain: 200 passed, three fixture failures, six skips; the three exact private-ledger adversarial fixtures
were repaired and passed separately, not counted as a passing aggregate. Core/coverage revalidation remains
in progress; synthetic metadata custody does not certify native-secret recovery or owner acceptance.

Further native integration repaired coverage/log/output producers that created the authority root before
registry enrollment. Coverage persistence now shares the installation writer fence; new log/output ancestors
use checked native private-directory creation. Coverage/credential/action integration passed 289 tests
(eight skips); provider/Goal/output/logger/lock integration passed 171 tests (one skip). Telemetry native
serialization and bounded stable-handle reuse repaired reproduced 132/160 concurrent record loss and a
12.25-second/1K performance failure: 49 focused tests passed, including three 160/160 trials and 0.28-second
timing at the unchanged five-second limit. Telemetry whole-module coverage was 80%, not a 95% gate.
Modified: coverage/logger/local-path producers, native directory helper, telemetry, related fixtures and
tests. The obsolete core snapshot was stopped; the earlier non-core aggregate failed 60 cases and 16 setup
errors. Final combined aggregates and unchanged coverage gates remain required.

The unchanged native security target passed 1,052 tests with 77 skips but failed combined coverage at
84.64%; this is not a passing gate. Missing lines include POSIX-only custody/lock branches as well as native
refusal paths. Additional lock refusal/cleanup tests passed 22 cases (76% diagnostic module coverage);
action-process API-boundary and missing evidence-binding tests passed 61 cases at 96.99% combined coverage.
The initial action follow-up missed the actual floor at 94.88% despite rounded output. Modified: session-lock
and action-process tests, status/handoff evidence. Complete-suite/core coverage remains running against its
earlier copied test snapshot; final generated evidence and cross-platform gates remain open.

Native filesystem refusal tests exposed a leaked directory handle when the leaf DACL was rejected.
Registering the handle before validation restores reverse-order cleanup without weakening refusal.
Modified: native filesystem cleanup, 26 native test cases and generated cohort artifacts. The initial
worker result was 49 passed/one skipped/one failed; after repair the filesystem/lock slice passed
72 tests with one skip, and seven manifest tests plus applicable Ruff checks passed. Diagnostic coverage
remains below the unchanged release floor. The long aggregate predates this source repair.

The long isolated Windows invocation finished: 8,815 passed, 185 skipped, 20 xfailed, zero test failures/errors,
181 warnings, 3,408.72 seconds. Exit 1: core coverage failed at 93.02% combined, 94.16% line and 89.91% branch.
Changed-line coverage against origin/main also failed (686 uncovered/9,876 changed lines, displayed 93%).
These are diagnostic evidence from before the latest cleanup repair, not final current-source release gates.
Real POSIX execution and a reviewed exact-source cross-platform coverage strategy remain unresolved; native
controls and unchanged floors are retained. No commit, push or cutover occurred.

Continued Windows-local validation added migration precondition review cases (41 passed, 100% module
coverage), private-publication failure cleanup, real-Ledger export/cancellation boundaries and native lock
descriptor failure cleanup. Five complete affected test files passed 300 tests; native filesystem/lock
failure testing separately passed 96 tests with one skip at its recorded snapshot. Modified: action mapping,
retention, integrated-surface, session-lock and native-filesystem tests. Native filesystem diagnostic coverage
remains 86.49% combined, not a passing gate. No platform exclusions or threshold changes were added.

Further native tests reproduced relative-child and security-descriptor-failure handle leaks, a false
private-DACL refusal for repeated ancestor/leaf basenames, and loss of writable access in returned
non-private child handles. Cleanup scopes, positional leaf checks and requested-access preservation now
repair those defects. Native filesystem/lock/bounded-preservation validation passed 170 tests with two
skips; native filesystem combined coverage reached 95.21% at the actual 95% threshold. Modified: native
filesystem backend/tests, canonical technical notes and generated cohorts. Both Ruff configurations and
strict Windows/Linux-platform checks of 63 core files passed. A current-source full test-only invocation
is pending; aggregate/security/changed-line coverage and independent release acceptance remain open.

Final native identity/ancestry/closed-handle tests brought the native filesystem/lock/preservation selection
to 172 passed, two skipped. Native filesystem coverage passed actual combined, independent line and branch
floors at 96.00%, 97.28% and 91.12%. Final regenerated manifest tests: seven passed. Tracked/unpublished-history
privacy passed with real gitleaks. These focused results do not close full-core/security/changed-line coverage.

The new complete isolated Windows test-only invocation passed: 8,986 passed, 185 skipped, 20 xfailed,
eight incumbent deprecation warnings, zero test failures/errors, 2,850.87 seconds, exit 0. It includes all
production fixes; the final two native tests and generated cohort refresh passed separately. No coverage
was collected in this invocation, so failed aggregate/security/changed-line coverage remains unresolved.

Added 11 passing native recovery/database-bound and original-affinity erasure tests, with no production
behavior change. Updated the active re-artha spec with actual Windows takeover evidence instead of leaving
only the historical deferred allocation. An exact-source cross-platform coverage proposal remains unapproved;
no gate semantics, host installations or remote access changed. Current-source security coverage is pending.

A new failing canary test reproduced inherited `COVERAGE_FILE` corruption: a nested miniature coverage
check overwrote the parent dataset. The canary subprocess now owns a tree-local dataset and clears
inherited automatic coverage startup variables; all 18 canaries pass under parent coverage. The earlier
full-suite coverage report contained its fake package and remains failed diagnostic history, not clean
release evidence. Current core-only coverage is being measured at unchanged selectors/floors.

### Added and fixed — encrypted recovery quarantine, 2026-10-25

| Area | Behavior |
|---|---|
| Historical files | Encrypted chunk/catalog quarantine under the inactive restore fence; no historical file plaintext on disk; original checkpoint preserved |
| Current privacy | Exact independently current consent, explicit metadata/content roles, original authenticated source-affinity and rollback-resistant erasure denial |
| Restore integrity | Canonical proven ledger paths; bounded schema/history/physical/FK preflight and post-replay page-image checks; process-death containment |
| Review/export | Exact expiring per-file review and human clean-content attestation; write-once self-contained encrypted exports; bounded current-consent CLI text view |
| Integration | Preserve and consolidate prior source administration, provider/resource and release-evidence repairs; register all three new security controls |

Code/tests commit `b24056d` on local main. Required final components passed: 8,813 isolated tests; 3,637 core
(97.16% line / 93.27% branch); 1,053 security cases; 97.09% changed-line coverage; lint/typing/schema/register/privacy.
The initial core fixture failure was repaired and rerun; exact combined evidence is in the implementation status.
Windows secure quarantine remains unfinished and fails closed. Work/fresh-device activation, native recovery,
independent evaluation/provider trials and owner UAT remain open. No native keys/effects, cutover or push.

### Fixed — resumed swarm integration and current cutover boundaries, 2026-10-24

| Area | Change |
|---|---|
| Source administration | Finish inherited add/update/remove review; bind current owner/permissions/cohort through atomic application, revalidate removal and hold unaccepted sync activation |
| Evaluation | Hash effective household/connector inputs; track Work recovery-artifact changes; require versioned native recovery/privacy/copy acceptance |
| Resources | Retain request replay protection across month rollover and old restores without charging old counters to the current month |
| Providers | Preserve CalDAV deletions across later failures, bound/replay Gmail listing and enforce Graph result caps while retaining degraded scopes/cursors |
| Restore privacy | Refuse unproven non-ledger historical content; remove staged plaintext and retain the checkpoint; reviewed recovery workflow remains pending |

Modified: user configuration/CLI/commands/runtime, release policy, resources, calendar/mail handlers, checkpoint
restore policy, named synthetic/process tests and generated evidence. All inherited work remains on local main;
no branch/worktree/push, live ingestion, key installation, native recovery, provider effect, consent change, UAT or
cutover. Current exact validation and parallel completion plan are in `docs/re-artha-implementation-status.md`.
Recovery quarantine design remains an owner decision; fail-closed containment is not full restoration readiness.

Final `make check` passed: 8,301 isolated / 3,125 core tests; core 97.10% line / 93.30% branch, changed-line
8,023/8,272; security controls 610 passed, one skipped, 96.09% combined. Strict typing/lint/register/schema and
tracked/unpublished/untracked privacy checks passed. Log: `/tmp/artha-resumed-swarm-verified-20261024.log`.
Synthetic 30/120/480-Signal scaling remained roughly linear; no real provider/model performance is claimed.
The current uncommitted aggregate is verified locally, not accepted for cutover or archived as complete.

### Fixed — renewed takeover audit, 2026-10-24 (local; acceptance pending)

| Area | Final behavior |
|---|---|
| Authority | Verified checkpoint before retirement; serialized dispatch/retirement; database+marker fence; exact retry/profile/key/receipt binding; epoch-safe resume; preserve owner pause |
| Actions | Recheck current evidence before reconciliation; refuse missing core capability; preserve undo evidence, sensitivity and resulting ETag |
| Preservation | Remaining legacy writer fences and configured-local path parity; failed publication retains buffers; strict mode-scoped spend custody and capture classification |
| Resource accounting | Actual receipt advances external seal; exact retry repairs failed seal writes; older restores retain uncertain spend; real SQLite-row backlog fairness |
| Provider interruption | Preserve confirmed deleted-collection/reset evidence while withholding new cursor/full-listing authority |
| Release | Scorecard v4 enforces complete-run, workload, held-out split and approved document count floors; new controls/providers included in implementation cohort |
| Owner policy | Explicit 2,000 reservations/UTC month and 64 pending selection with reviewed bundle/retry bounds; explicit retain-all prevents automatic expiry without pretending policy is unset |
| Integration | Inherited resource/Goal tests use actual contracts; expanded regression/process coverage and regenerated evidence registers |

Modified: source/tests/configuration in the local implementation commit, including authority, action worker/undo,
connectors/coordinator, preservation/registry/session fences, resources/retention, Goals/documents/runtime wiring,
legacy writers and capability mapping. Ignored private Work code remains local and is not force-added.
Verified safety commit `8cde320`: make check passed (8,107 isolated / 2,935 core; 97.05% line, 93.17% branch;
610 security controls). Exact logs and remaining gates are in `docs/re-artha-implementation-status.md`.
Native/cloud recovery, consented evaluation/provider trials and owner UAT remain pending; no cutover or push.

### Added — completion rows 1-7 engineering slices, 2026-10-24

| Area | Change |
|---|---|
| Recovery | Streaming SQLite capture, capture-side path portability checks, reviewed-inventory requirement for recovery, vault and legacy-writer installation fence, bounded preservation scan with owner-only spill |
| Authority | Signed retirement receipt from the former host verified before promotion; lost-host path recorded as unverified owner attestation; owner pause preserved across transfer |
| Actions | Provider-version precondition registry (calendar modify only; other types refuse a declared version); queue approval fix for versioned calendar changes |
| Providers | Calendar occurrence withdrawal after a complete resync only; exact-identity collection withdrawal; opt-in cursors for Google, Graph and CalDAV calendar and Gmail, Graph and IMAP mail (none enabled); silent mail failures now reported |
| Resources | Provider usage receipts separate from estimates, spend seal registered as a recovery class and wired, cancellation before disclosure, `usage` command, visible policy_needed state |
| Lifecycle | Retention expiry mechanism for source and replay through the deletion journal; periods unset; CLI apply still held; per-table bounded preview |
| Product | Leading indicators, Sprint calibration (authored tolerance), proposal-only tradeoffs, strict pack contracts, vehicle pack, core-neutrality test |
| Surfaces | Scoped interchange, custodian view, reviewed `configure`, form/table/script handling in documents |
| Extraction | Malformed sibling items no longer abort a chunk; stronger date, negation and whole-token support checks |
| Test isolation | Module-level production-path check; audit driver fixed |

Modified: see commits after f08e2f3. Migration 0019 (resource usage). Final `make check`: 7,953 isolated passed,
2,781 core passed (97.52%), 606 security-control passed. No live provider, host, UAT or cutover evidence; cutover stays disabled.

### Added — reviewed profile-scoped recovery registry, 2026-10-24

| Area | Change |
|---|---|
| Dependency authority | Bounded metadata-only personal/Work registries; explicit exact-digest historical declarations/review; no automatic native enumeration or review signing |
| Publication | Pending-before-write, verified completion, hashed per-record process locks, retained failure/death intents and exact acknowledgement recovery |
| Writers | Key/head creation and rotation, credential heads/deletion, clients, bundles and privacy anchors register through existing writer/fence paths |
| Checkpoints | Require reviewed dependencies, native/generation consistency, deleted-record refusal and registry pinning; preserve current and retired credential chunks |
| Path safety | Private creation under ordinary umask, no-follow lock descriptor and refusal of linked/nonprivate registry metadata without target normalization |
| Evidence | New registry and writer regression/process tests; registry included in mandatory security coverage and generated capability evidence |

Modified: new `scripts/catchup_core/recovery_registry.py`, checkpoint CLI, crypto/ledger/bundle and credential/client
writers, session locks, Makefile, capability mapping, registry/writer/checkpoint tests and generated registers.
Approved contract and operating instructions remain local-review aware. Final `make check`: 7,101 isolated / 2,013
core tests passed; core 97.01% line / 92.88% branch; changed-line 4,506/4,662; security controls 595 passed, one
skipped, 98.31% combined. Static/register/schema/privacy checks passed, including all 12 untracked files. Details
are in `docs/re-artha-implementation-status.md`. No live review, key access/migration, provider effect,
checkpoint/recovery, cutover, commit or push. Native historical inventory and current-privacy recovery acceptance
remain required; restored metadata never activates an installation.

### Fixed — inherited recovery/action integration and observation fidelity, 2026-10-24

| Area | Change |
|---|---|
| Recovery | Authenticate pinned input/profile/tree before output; verify physical members before transformations; deny malformed keys and unavailable quarantine metadata |
| Privacy | Require canonical v2 anchor replay; fence bundle/Google/iCloud credential changes; native-only iCloud setup with no plaintext mirror/fallback |
| Authority/actions | Integrate queue-first transfer intent, epoch/pause checks and crash recovery; bind/recheck exact Fact/subject/permission/freshness preconditions and preserve dispatch uncertainty |
| Observation | Preserve CalDAV all-day/exclusive-end values and partial positives; degrade missing selected calendars or rejected siblings |
| Resources/views | Count OCR attempts rather than successes; preserve bounded Goal review gaps in text inspection |
| Process | Preserve 73 local commits and inherited staged/unstaged work on main; record source-grounded gaps, parallel completion plan and undecided policy contracts; include the new evidence broker in the mandatory security coverage gate |

Modified: checkpoint/CLI/session locks, authority-transfer protocol, ledger/runtime/release/command integration,
queue/executor/action evidence and metadata policy, credentials/Google/iCloud setup, bundles, CalDAV, documents,
views, Goal writer fences, named synthetic/process tests, capability mapping and generated registers. The detailed
current audit is in `docs/re-artha-implementation-status.md`. Final `make check` passed: 6,814 isolated / 1,774 core
tests; core 97.13% line / 92.87% branch, changed-line 4,330/4,464; expanded controls 407 passed, one skipped,
98.69% combined. Typing, lint, generated evidence, schema and tracked/unpushed/staged/untracked privacy checks
passed. Full native escrow, general provider preconditions/lifecycle, resource/retention policies, broader
product contracts and independent owner/platform acceptance remain open. No live credentials/data, provider effects,
production migration/recovery, consent change, cutover, commit or push occurred in this continuation.

### Fixed — encrypted WorkIQ cache and profile preservation, 2026-10-24

| Area | Change |
|---|---|
| Source cache | Verified age ciphertext in machine-local Work storage; merge under a process lock; no plaintext/environment-key fallback |
| Cache correctness | Bind connector bytes, resolved redaction policy and request scope/depth; reject malformed/future entries; repair rich-query lookback formatting |
| Preflight | Separate bounded technical receipt; never persist identity-probe stdout; surface failed receipt writes |
| Legacy migration | Exact reviewed digest and stopped-writer prerequisite; verified encrypted archive before read-only retirement; refuse linked/reparse ancestry |
| Checkpoints | Fence Work cache writers, separate known Work/legacy temporary/OAuth/recovery artifacts, and reject repository-containing local roots |

Code `de6df85`: Work cache/vault adapters, connector, local paths/health receipt, both preflight paths,
checkpoint/CLI, named synthetic/process tests and generated registers. Full gate: 6,360 isolated / 1,465 core
tests; 97.33% line / 93.24% branch; 97.48% changed-line coverage; controls 279 passed, one skipped. Typing, lint,
schema, registers, privacy and the commit hook passed. Canonical specs, operating documentation and completion
checklist record the behavior and remaining Work identity/retention, recovery-key, native platform and owner
acceptance work. No live keys/data, migration, provider effect, cutover or push occurred.

### Fixed — checkpoint external dependency inventory, 2026-10-24

| Area | Change |
|---|---|
| Dependencies | Capture configured external action databases with consistent WAL images and external privacy capsules; exclude known Work OAuth/recovery files |
| Quiescence | Fence physical local/action roots once; block executing or unknown action state before reading escrow keys |
| Publication | Rediscover dependencies under the fence and before publication; denied metadata blocks capture |

Code `d1b7158`: checkpoint/CLI, capability register, synthetic/process tests and generated registers.
Full gate: 6,308 isolated / 1,434 core tests; 97.28% line / 93.15% branch; 97.42% changed-line coverage;
controls 279 passed, one skipped. Typing, lint, schema, registers and privacy passed. Documentation records
remaining Work cache/privacy, complete key inventory, writer-fence and native recovery gaps. No live keys,
ingestion, provider effect, migration, owner UAT, cutover or push occurred.

### Added — canonical Open Item completion and Goal linkage, 2026-10-24

| Area | Change |
|---|---|
| Completion | `complete open_item:<id>` uses exact owner review and versioned confirmation; reopening or changed content restores the open projection without rewriting authored files |
| Goal linkage | Explicit milestone `open_item_ref` shares current scoped completion evidence with metrics/plans; ambiguous, inaccessible or mismatched links stay unknown and duplicates cannot inflate progress |
| Permission safety | Retain parent/child subjects and explicit aliases; check reviewed permissions inside the write transaction and recheck linked records/Fact versions before query delivery |

Code `ca53592`, contract 0.8.0: CLI/commands, curated projections, Goal/plan/runtime integration, ledger review
preconditions, 29 named synthetic/process cases and generated registers. Full gate: 6,291 isolated / 1,417 core
tests; 97.25% line / 93.09% branch; 97.35% changed-line coverage; controls 279 passed, one skipped. Typing, lint,
schema, registers and privacy passed. Canonical specs, workflow and completion checklist document the rules and
remaining work. No live ingestion, effect, migration, owner UAT, cutover or push occurred.

### Fixed — Work recovery isolation and temporal/calendar completeness, 2026-10-24

| Area | Change |
|---|---|
| Work recovery | Owner-confirmed shared age vault identity, separate Work checkpoint keys/passphrase, Windows/destination guards, passphrase-only inspection and current-privacy restore gate |
| Key consistency | Pin/recheck capture keys, authenticate recovery payloads, honour runtime-local overrides, and reject invalid copying before publishing |
| History | Compare explicit instants with microsecond precision, preserve monotonic versions under clock rollback, and reject ambiguous cutoffs |
| Calendar | Retain positives across malformed pages, follow empty Graph pages, disclose loops/caps/omissions and reject invalid scopes/limits/windows |

Code `2e13660`: checkpoint/CLI, ledger, Google/Graph handlers, named regression tests and generated registers.
Full gate: 6,262 isolated / 1,388 core tests; 97.24% line / 93.04% branch; 97.29% changed-line coverage;
controls 279 passed, one skipped. Typing, lint, schema, registers and privacy passed. Canonical specs and the
completion checklist record actual scope and remaining work. Native Windows/live-vault recovery, full privacy
continuity, independent outcomes and cutover remain unaccepted. No live ingestion, effect or push occurred.

### Fixed — checkpoint safety, temporal evidence and release cohorts, 2026-10-24

| Area | Change |
|---|---|
| Preservation | Block unreadable subtrees and changed database/file generations; verify foreign keys, stream metadata and recovery escrow; publish immutable verified ciphertext |
| Recovery | Enforce restored-installation quarantine; preserve interrupted ciphertext; provide owner-approved passphrase-only inspection without key installation or activation |
| Runtime | Share checkpoint fences with wired personal Catch-up, inspection and action approval; refuse lost-key re-enrollment |
| Evidence | Migration 0016/contract 0.7.0 keeps source times per Fact version; older unknown history stays unknown |
| Evaluation | Release v3/extraction v2 bind current cohorts and reported response identity; anonymous shadow work cannot establish measured release evidence |
| Coverage | Follow Graph calendar discovery pages and disclose incomplete discovery while retaining known calendars |

Modified: checkpoint/CLI, backup, runtime/coordinator/inspection, action guards, ledger/views/migration, exchange,
release/corpus policy, Graph connector, named synthetic/process tests and generated registers. Canonical specs,
workflow, completion checklist and local feature contracts record the behavior and owner recovery decisions.
Local code `fbbd8a1`: full gate passed (6,198 isolated / 1,363 core tests; 97.20% line / 92.97% branch;
97.20% changed-line coverage; controls 279 passed, one skipped). Typing, lint, schema, registers and privacy passed.
The completion checklist records synthetic recovery performance and remaining engineering/acceptance gates. No live recovery,
provider effect, household ingestion, cutover or push is authorized by this engineering checkpoint.

### Fixed — bounded privacy recovery; documented complete cutover protection and handoff, 2026-10-22

| Area | Change |
|---|---|
| Privacy recovery | Bounded OS anchors, authenticated encrypted history capsules, continuity and process-safe publication |
| Recovery material | Preserve unattributed/orphan ciphertext; retire only a verified anchored predecessor |
| Cutover | Require complete verified state/configuration backup, independent copy and restore/rollback rehearsal |
| Handoff | Consolidate pending engineering work on local main; record remaining work, evidence and private local dependencies |

Code `bbec5de`, local only. Modified: ledger/privacy_history/runtime/CLI, capability mapping, synthetic/process tests
and generated registers; canonical specs, core/status documentation and agent handoff updated separately.
Full gate: 6,082 isolated / 1,252 core tests, 97.12% line / 92.58% branch; 2,381/2,453 changed lines covered
(97.06%); 279 controls passed, one skipped, 98.18% combined. Typing, lint, schema and privacy passed. Native platform
UAT, emergency key recovery and complete cutover capture/rehearsal remain open. No live migration, data restore,
cutover, provider effect or push occurred. Feature notes stay active pending owner/platform acceptance.

### Fixed — runtime resource ownership and early retention validation, 2026-10-22

| Area | Change |
|---|---|
| Startup | Close the ledger after wiring failures and interruptions; transfer ownership only on success |
| Validation | Reject retention beyond the existing pruner contract before run work; leave snapshots/configuration unchanged |

Modified: runtime, capability registry, actual-builder failure tests and generated registers. Code `723e165`, local only.
Full gate: 6,039 isolated / 1,209 core tests; 97.07% line / 92.45% branch; 2,255/2,320 changed lines covered (97.20%);
controls 279 passed, one skipped, 98.18% combined. Typing, lint, registers, schema and privacy passed. No live ingestion,
provider operation, data migration, cutover or push occurred.

### Fixed — Windows Copilot hook portability and dispatch policy freshness, 2026-10-22

| Area | Change |
|---|---|
| Windows | Replace Mac-only shared hook commands with platform launchers; preserve settings/permissions and backups |
| Protocol | Handle Copilot inputs, PowerShell invocations and Windows paths without weakening vault protections |
| Dispatch | Bind current policy/pause/approver/expiry/fresh preview through credential capture and child startup |
| Limits | Reserve per-type capacity atomically in the actual queue; count attempts once, including archives |

Code `8c02735` (dispatch) and `0d5d67f` (hooks), local only. Modified: executor/queue, action policy/process, guard,
portable hook installer/setup, focused process/regression tests, capability registry, Makefile and generated registers.
Latest full gate: 6,022 isolated / 1,192 core tests, 97.06% line / 92.41% branch; 2,227/2,294 changed lines covered
(97.08%); controls 279 passed, one skipped, 98.18% combined. All checks passed. Real two-process rate/death boundaries
passed. Owner confirmed Windows VS Code Copilot “Work runs now.” Settings repair left all 600 checked data files
unchanged and cutover false. No agent-run Work ingestion, provider effect, data migration or push occurred. Broader
re-Artha engineering/acceptance remains open; completed hook feature notes archived after this scoped owner UAT.

### Fixed — release artifact lifetime and exact rollback bytes, 2026-10-22

| Area | Change |
|---|---|
| Approval | Recheck clearance/scorecard bytes through publication; block withdrawn artifacts at COMMIT |
| Rollback | Decode the one approved byte string; reverify changed local file revisions |
| Availability | Preserve inspection and local owner safety authority independently of release evidence |

Modified: policy/runtime/backup, capability registry, synthetic artifact/runtime/publication tests and generated
registers. Local code `81d7355`: full gate passed; 5,959 isolated / 1,192 core tests, 97.06% line / 92.41% branch;
controls 216 passed, one skipped, 98.30% combined. Cache trusts local filesystem revisions, not malicious metadata.
No live ingestion, provider request, owner UAT, push or cutover occurred. Model/code cohort binding remains open.

### Fixed — kernel cutover fence and independent quality reads, 2026-10-22

| Area | Change |
|---|---|
| Authority | Require literal configured cutover=true in the kernel; respect environment opt-out |
| Controls | Preserve read-only inspection and local owner safety authority while disabled |
| Quality | Inspect release evidence independently of enabling execution; update evidence fixtures explicitly |

Modified: policy/runtime/health, capability registry, direct-runtime and evidence fixtures, generated registers.
Local code `deeec6a`: all checks passed; 5,946 isolated / 1,179 core tests, 97.01% line / 92.35% branch,
2,194/2,262 changed lines covered (96.99%). Control suite: 216 passed, one skipped, 98.30% combined. Synthetic tests
reproduced false/missing/coerced flag and environment bypasses with otherwise passing release evidence. No live flag,
source data, provider call, owner UAT, push or cutover changed.

### Added — source-backed weekly Goal comparison; fixed WAL startup contention, 2026-10-22

| Area | Change |
|---|---|
| Weekly review | Read-only goals --weekly / MCP boolean option, exact dated quantity/habit windows and typed movement |
| Evidence | Bind both periods, recompute correction/forgetting, keep unavailable comparisons unknown |
| Trust | Unverified provenance cannot establish progress; preserve explicit owner confirmation precedence |
| Startup | Bounded WAL contention retry, short SQL busy waits, verified mode and failed-handle cleanup |

Modified: Goal model/metrics, shared renderer/commands/CLI/MCP, ledger startup, capability registry, synthetic/process
tests and generated registers. Local code `b71047d`: full gate passed on rerun after repairing the reproduced WAL race;
5,936 isolated / 1,169 core tests, 96.99% line / 92.27% branch, 2,148/2,217 changed lines covered (96.89%). Controls:
216 passed, one skipped, 98.30% combined. Core contract is 0.6.0; no new historical store or retention policy. Broader
Goal intelligence remains open. No live provider/model request, data migration, owner UAT, push or cutover occurred.

### Fixed — retain Goal next actions beside metrics, 2026-10-22

| Area | Change |
|---|---|
| Guidance | Keep verified metric assessment and authored next action/date status through shared Goal/Briefing rendering |
| Validation | Reject coerced action types and malformed date prefixes; keep missing actions explicitly unknown |
| Counts | Expose active Goal count independently of presentation lines; retain standing-commitment semantics |

Modified: runtime Goal renderer, capability registry, metric/command/Briefing fixtures and generated registers. Local
code `1cc5806`: all gates passed; 5,900 isolated / 1,133 core tests, 96.94% line / 92.11% branch,
2,084/2,153 changed lines covered (96.80%). Control suite: 216 passed, one skipped, 98.30% combined. Exact source
dependencies remain bound and authored values remain unchanged. No live record write, provider effect, owner UAT, push
or cutover was performed.

### Fixed — scoped volume history and current source coverage, 2026-10-22

| Area | Change |
|---|---|
| Volume history | Separate configured account/filter baselines; preserve thresholds and exclude unattributed incumbent history |
| Coverage | Derive the runtime header from current authorized results; exclude retired accounts from current coverage/health |
| Inventory | Bind manual and bundle scopes explicitly; reject unresolved bundle source/account names |

Modified: adapter/coordinator/runtime/commands, capability registry, source/inventory tests and generated registers.
Local code `d8b0316`: all gates passed; 5,883 isolated / 1,116 core tests, 96.93% line / 92.10% branch,
2,086/2,150 changed lines covered (97.02%). Controls: 216 passed, one skipped, 98.30% combined. History contains bounded
counts, not provider content, and does not authenticate the provider account. No old evidence was deleted; no provider
call, live ingestion, UAT, push or cutover occurred.

### Fixed — preserve interrupted Connector results, 2026-10-22

| Area | Change |
|---|---|
| Adapter | Retain validated positives/cancellations after iterator failure under degraded coverage |
| Validation | Reject malformed individual records and empty cancellation identities; continue later valid data |
| Recovery | Prove unchanged completion watermark, retained prior records and idempotent retry through actual coordinator |

Modified: adapter, capability registry, adapter/runtime/integration fixtures and generated registers. Local code
`479f599`: all checks passed on rerun after isolating synthetic coverage histories; 5,871 isolated / 1,104 core tests,
96.92% line / 92.06% branch, 2,059/2,129 changed lines covered (96.71%). Control suite: 216 passed, one skipped,
98.30% combined. Existing synthetic benchmark (30/120/480 signals): cold 0.058/0.171/0.827s, warm 0.024/0.223/0.367s,
scaling ratio 0.89 on this host during development. This small offline run excludes providers/models and does not prove
normal-workload p95, production parity or sustained capacity. No live ingestion/effect, UAT, push or cutover occurred.

### Fixed — migration history and retained schema integrity, 2026-10-22

| Area | Change |
|---|---|
| Schema | Migration 0015 journals exact SQL checksums, filenames, verification time and honest execution/adoption provenance |
| Verification | Check canonical core objects and history before journaled opens, migration and integrity preflight |
| Recovery | Atomic schema/version/history publication; process-kill and barrier-controlled concurrent-upgrade cases |

Modified: ledger, schema_history module, migration 0015, capability registry, synthetic/process tests and registers.
Local code `66e91b0`: full gate passed with 5,858 isolated / 1,091 core tests, 96.92% line / 92.05% branch. After staging
new files, coverage rerun passed all 1,091 core tests and 2,025/2,098 changed lines (96.52%); schema_history is 100%
line/branch covered. The restricted-sandbox rerun could not launch nested OCR sandboxes; the host rerun used by the
passing full gate passed. Control suite: 216 passed, one skipped, 98.30% combined; typing/lint/privacy/register gates passed.
This detects corruption/version drift, not arbitrary same-user journal rewrites. No production database was opened or
upgraded; owner/platform acceptance, provider calls, push and cutover remain unperformed.

### Fixed — record-scoped curated retrieval and current query permissions, 2026-10-22

| Area | Change |
|---|---|
| Retrieval | Filter typed records by declared subjects/owner, nested scopes, current consent and explicit visibility |
| Planning | Inherit Goal permissions into Sprints; private records cannot consume bounds or inject child validation gaps |
| Commands | Re-read allowed content and recheck permission/runtime generation; keep exact completion binding |
| Legacy | Conservative untagged authorization, no free prose or duplicate typed-file metadata in the projection |

Modified: curated/runtime/commands/legacy adapter, capability registry, regression tests and generated registers.
Local code `5aa2688`: 5,827 isolated tests, 1,060 core tests; 96.91% line / 92.01% branch; 1,959/2,024 changed lines
covered (96.79%); control suite 216 passed, one skipped, 98.30% combined. All standard checks passed on the second full
run after updating two policy-validator test doubles and adding the hidden-Goal Sprint regression. Trusted kernel scope
tags are authored metadata; this does not establish semantic tagging accuracy or full family-channel acceptance.
No live data ingestion, provider call, owner UAT, push or cutover was performed.

### Fixed — current runtime policy and strict configuration types, 2026-10-22

| Area | Change |
|---|---|
| Runtime | Recheck exact policy/profile/pack/prompt/code bytes before stages, fetches, model dispatch and publication |
| Configuration | Reject coerced booleans, malformed sources and invalid identifiers/integer bounds before fetch |

Modified: coordinator/runtime, boundary/runtime tests and generated manifest. Local code `8c4afe0`: full gate passed
with 5,794 isolated and 1,027 core tests; 96.94% line / 92.07% branch; 1,849/1,907 changed lines covered (96.96%).
Control suite: 216 passed, one skipped, 98.30% combined. Focused follow-up: 184 passed in 3.74s; public-code hashing
about 3.4 ms/check on this host. No provider call, household ingestion, push, cutover or owner UAT was performed.

### Added — approved-action credential scope; fixed strict typing control, 2026-10-22

| Area | Change |
|---|---|
| Broker | One provider's access credential through a bound private pipe; no refresh grants/client secrets in children |
| Worker | Read-only scoped credential APIs, pre-import validation, exact queue claim, expiry and receipt redaction |
| Registry | Share the existing executor handler allowlist with worker/broker; preserve all enablement/approval/held-type gates |
| Quality | Repair suppressed strict checks; add bad-module regressions and both action controls to coverage gating |

Modified: action credentials/process/executor/registry, credential storage, parent authentication request timeouts,
Makefile/typing policy, synthetic process tests and generated registers. Local code `7efe55f`: all gates passed;
5,756 isolated tests, 989 core, 96.92% line / 92.00% branch. Expanded control suite: 216 passed, one skipped, 98.30%
combined; broker 100% line/branch. This contains trusted Python handlers, with no claim of an OS sandbox. No host
credential read, live provider request/effect, held-type promotion, push, cutover or owner UAT occurred in validation.

### Changed — household assignment convention; fixed concurrent OAuth refresh, 2026-10-22

| Area | Change |
|---|---|
| Household | Owner-approved default acceptance, no member ceremony/delay escalation; preserve verified completion and visibility |
| Refresh | Serialize grant use, publish against the exact read revision and preserve newer login/revocation |
| Storage | Canonical per-user credential locks shared across runtime roots and migration |

Modified: coordination, credential storage/migration, Google/Graph refresh, process/integration tests and registers.
Local code `b4b157d`: full gate passed with 5,696 isolated tests and 989 core tests; 96.93% line / 92.03% branch.
Hook/credential gate: 140 passed, one skipped, 98.42% combined. No member response or data-access consent is fabricated;
no provider request, host credential access, push, cutover or owner UAT was performed by this verification.

### Fixed — encryption-key durability and provider authentication, 2026-10-22

| Area | Change |
|---|---|
| Data keys | Serialize first use/rotation across ledgers, verify material before version publication and retain immutable versions |
| Google | Restore expiry in the SDK's UTC format, refresh unknown freshness, restrict refresh endpoint and require explicit interactive setup |
| Graph | Repair the actual shared helper interface, preserve silent-refresh grants and validate refreshed tokens in the selected personal/Work scope |

Modified: crypto, Google/Graph/shared authentication, process/integration tests and generated manifest. Local code
`50f7e75`: 5,669 isolated tests passed; 979 core tests passed on rerun, 96.94% line / 92.03% branch,
1,795/1,858 changed lines covered (96.61%), and 123 hook/credential tests passed (one skipped). Remaining quality
gates passed. The rerun followed an authorized lessons edit that overlapped with the production-file snapshot.
Real processes reproduced the key race and exercised competing rotation and death at both publication boundaries.
No host credential read, provider call, rotation of live keys, push or owner UAT was performed for these repairs.


### Fixed — strict Domain policy and composite arithmetic, 2026-10-22

| Area | Change |
|---|---|
| Packs | Reject coerced booleans, malformed policy quantities/lists and duplicate sources; validate optional fields |
| Composition | Preserve per-predicate calendar, timezone and freshness; retain required status; identify all component versions |
| Boundaries | Refuse conflicting ownership/classes; include migration source in runtime generation; reject future check dates |

Modified: Domain loader/composition, triage, runtime, responsibilities, tests and generated traceability. Local code
`75b9b5b`: 5,628 isolated tests and 957 core tests; all quality gates passed. Retention/jurisdiction/quantity and pack
migration contracts remain open, along with the other requirements in docs/re-artha-implementation-status.md. No push.

### Fixed — logical Catch-up measurements and silent telemetry gaps, 2026-10-22

| Area | Change |
|---|---|
| Metrics | Combine paused invocations, distinguish active/wait-inclusive latency, ignore terminal replays and retain blocked samples |
| Integrity | Migration 0014 reserves per-implementation sequences; health detects wholly missing/torn appends and incomplete resume history |
| Health | Current implementation cohorts, pending/unknown counts, bounded history reads and finite-value checks |
| Traces/cost | Invocation span identities in trace v2; paused work included in explicitly approximate cost, with no usage/enforcement claim |

Modified: metrics, ledger/migration, coordinator, commands, contract version, tests and registers. Local code `bcb7def`:
5,590 isolated tests, 919 core tests, all quality gates passed. No live workload or model quality evaluation, provider
effect, owner UAT, cutover or push. Contract 0.5.0 invalidates incompatible resumed inputs; start a fresh Catch-up after
normal schema upgrade. Existing telemetry files are retained and are not rewritten into new evidence.

### Added — source-aware responsibilities and prepared Goal steps, 2026-10-22

| Area | Change |
|---|---|
| Responsibilities | Exact source scopes/freshness, version-bound typed stop conditions, current permission/provenance/interval checks |
| Goal plans | Bounded authored instruction sheets retained in the encrypted Catch-up; blocked prerequisites remain blocked |
| Briefing | Logical commitment counts; a verified stop is informational and correction removes stale quiet claims |

Modified: responsibility/plan evaluation, runtime, coordinator, composer, tests and generated registers. Local code
`4138b62`: 5,570 isolated tests and 899 core tests; all quality gates passed. No provider effect, household authentication,
owner UAT or cutover is claimed. The active completion effort continues.

### Fixed — platform OAuth storage and recovery, 2026-10-21

| Area | Change |
|---|---|
| Credentials | Verified bounded OS records, immutable generations, separate Work services; platform-only backend selection |
| Migration | Exact-generation preview, recovery before sealing, encrypted recovery into OS records, newer-record conflict refusal |
| Consumers | Google/Graph, shared auth, SharePoint and preflight use actual OS records; no implicit secret-file fallback |
| Isolation | Scrubbed child environments retain null keyring; provider/control versions invalidate resumed Catch-ups |

Modified: credential store/migration/CLI, provider/shared auth, preflight, crypto/runtime, Makefile, synthetic tests and
traceability. Local implementation `8778994`: 5,553 isolated tests and 882 core tests; all gates passed. Reviewed personal
migration completed on macOS without provider calls. Recovery/OS behavior on Windows/Linux and action credential
brokering remain open. Ignored Work overlay changes stay local; no push or cutover.

### Fixed — corpus privacy, output evidence and canonical writers, 2026-10-21

| Area | Change |
|---|---|
| Corpus | Current source/subject permissions; separate labels and actual returned-output receipts; snapshot, erasure and old-restore protection |
| Inspection | Source coverage and pending counts respect permission; unknown provenance is denied |
| Goal writer | Serialized durable publication, intervening-edit check, explicit owner/source definitions and strict CLI flags |
| Calendar | Actual Google handler preserves calendar identity on cancellation |
| Test isolation | Native household API imports denied; Reminders checks the platform flag at entry |

Modified: core contracts, migration 0013, corpus/CLI, ledger/backup/commands/runtime/composer/coordinator, metric definition
validation, Goal writer, Google/Apple Connectors, synthetic tests and generated registers. Local verification:
5,475 isolated tests; 876 core tests; 96.80% line / 91.61% branch; strict typing, lint, privacy and registers passed.
Implementation `e1b072f`; no push, cutover or owner acceptance. Credential isolation and remaining product work continue.

### Fixed — action review, uncertainty and historical upgrade, 2026-10-21

| Area | Change |
|---|---|
| Historical actions | Verified recovery snapshot; atomic uncertainty quarantine, approval invalidation and archived recovery; audit retention across archiving |
| Review | Complete canonical payload; oversized previews cannot receive receipts; provider context binds by compare-and-set |
| Calendar | Reviewed ETag, fresh version check and If-Match; matching versioned receipts; version-bound undo; no stale overwrite retry |
| Outcomes | Partial/unrecognized results retain unknown state and duplicate protection; malformed worker receipts cannot establish success |

Modified: action queue/executor/orchestrator/process, Calendar/To Do handlers, their synthetic tests, generated register
and technical/UX/operator docs. Tests include actual process termination during quarantine and concurrent startup.
No production queue was migrated and no provider effect or action promotion occurred. Actual validation is recorded in
`docs/re-artha-implementation-status.md`; the broader completion effort remains active.

### Added / Fixed — Goal completion integration, 2026-10-21

| Area | Change |
|---|---|
| Goal progress | Evidence-backed milestone counts; correction/retraction recomputation; Connector/account-scoped obligation metrics and exact rolling windows |
| Review | Previewed completion withdrawal with `--reopen`; prior-version binding, idempotent retry and atomic protection against concurrent corrections |
| Publication | Goal/planning completion Fact dependencies pinned through draft resume/publication and archived correction notices |
| Sprints | One captured Goals/Sprints snapshot; visible duplicate identity, active conflict and date/reference gaps |
| Action migration | SQLite backup includes committed WAL, verifies integrity and publishes atomically without overwriting a concurrent winner |

Modified: `scripts/catchup_core/{cli,commands,coordinator,curated,goals,ledger,runtime,views,capabilities}.py`, Goal and
release-boundary tests, `scripts/action_queue.py`, action durability tests, generated registers and technical/UX/operator docs. The tests include a real competing-process
Fact update. Verification and remaining acceptance are recorded in `docs/re-artha-implementation-status.md`.

### Fixed — completion boundary pass, 2026-10-21

| Area | Change |
|---|---|
| Permissions | Operation-specific correction checks before side effects; completion confirmation inherits co-subject access |
| Recovery | Calendar cancellation journal and atomic support/history withdrawal; separate-process termination regression |
| Consistency | Scoped calendar identity, CalDAV tombstones/instances, metadata-bound versions, publication-time input rechecks |
| Commitments | Visible malformed schedules/records and invalid dates; owner-confirmed, version-bound completion command |
| Extraction | UTF-8 evidence contract with explicitly labelled legacy coordinates; arbitrary model CLI refusal |
| Quality | Named acceptance fixtures, 95% combined/line/changed-line coverage, separate branch floor; scoped inspection preflight |

Modified: `scripts/catchup_core/**`, `scripts/action_executor.py`, `scripts/connectors/caldav_calendar.py`,
`config/actions.yaml`, `Makefile`, coverage tooling/CI and the corresponding synthetic unit/integration tests.
The canonical technical/UX contracts and active local feature spec record the new behavior. Verification outcomes and
uncompleted engineering/owner acceptance remain in `docs/re-artha-implementation-status.md`. No cutover, production
ingestion, external provider effect, remote push or owner UAT is implied.


### Fixed — release boundaries (WP-15), 2026-10-21

| Area | Change |
|---|---|
| Evidence | Explicit source heads (migration 0010); ambiguous/partial version recovery; conservative enum sentence grounding |
| Privacy | Artifact-aware export authorization and generation guard; query-only inspection; no key/profile enrollment or Goal writes on read |
| Planning | Valid completion references, milestone graph checks, visible scheduling/coverage failures and deferred records |
| Execution | Exact preview hash and expiring receipt, single-use provider dispatch claim, staged reverse proposals, independent emergency pause/erasure |
| Readiness | Runtime footprint checks, calendar cancellation/classification, MCP framing limits/freshness and scorecard v2 cohort gates |

Modified files: `scripts/catchup_core/**`, action executor/queue/orchestrator and child process, Google/Graph calendar
handlers, footprint checks, affected unit/integration tests, and generated spec manifest. Regression coverage lives in
`tests/catchup_core/test_release_boundaries.py` and the action/calendar/footprint tests. Validation and remaining
engineering/acceptance work are recorded in `docs/re-artha-implementation-status.md`; full compliance is not claimed.

Validation on local implementation commit `42e05ae`: 5,167 isolated tests passed (660 core); core line/branch
coverage 96.33% / 90.33%; strict mypy, Ruff, syntax/import/state-write/schema/register and privacy checks passed.
The synthetic core processed 4,000 Signals in 4.62 s cold / 1.55 s warm, excluding real model/provider latency.
No new live UAT, cutover, remote push or CI run was performed.

### Added / Changed — re-artha pilot core (`scripts/catchup_core/`), 2026-10-11 to 2026-10-21

Work packages WP-00 to WP-13 of `specs/re-artha.md` v0.8.0 (the spec is local-only and **not archived**: work continues).
The incumbent Catch-up stays authoritative; the new core runs in shadow until a signed release block exists.

| Area | What changed |
|---|---|
| Delivery foundation (WP-00) | Hermetic test bootstrap and isolated runner, trustworthy `make lint`/`ruff`/`import-check`, ratcheted state-write lint, one privacy-scan policy for pre-commit, pre-push, `make` and CI (gitleaks + owner/household denylist), coverage floors, strict mypy |
| Containment (WP-01..03) | Calendar record contract and truthful connector coverage, stale-on-open banner, plaintext pipeline output removed from the sync root, memory extraction off by default in code, Open Items lint, fail-closed action bridge, retired job definitions |
| Core (WP-04..08) | Typed Catch-up state machine, Domain packs (Kids/School pilot), encrypted ledger and migrations 0001-0009, in-memory `age` vault, reconciliation by per-predicate authority, deterministic triage and ranking, cited Briefing composer, session lock, backups and restore, forgetting with sealed deletion checkpoint |
| Actions (WP-09) | Compare-and-set claims, payload-bound approval, `unknown` outcomes with provider reconciliation, deterministic policy guard, isolated action child process with provider-only egress |
| Retirements (D-03) | Telegram/Slack/Discord channel code, always-on engine, nudge daemon and background job definitions removed |
| Surfaces | `catchup`, `beliefs`, `why`, `forget`, `feedback`, `correct`, `health`, `history`, `items`, `goals`, `capture`, `export`, `restore`, `activity`, `pause`, `reconcile`, `resolve`, `bundle-enroll`, `bundle-export`, read-only `mcp` (stdio) |
| Evidence and records | Ingest bundles (signed, encrypted, immutable), text-PDF ingestion, Goal plans, standing responsibilities, household coordination, replay cases, model-request disclosure log, run metrics, cost estimate, OTLP-shaped traces |
| Harness | PreToolUse guard, Stop-hook omission record, in-memory `vault.py write`, generated capability and traceability registers with drift check |
| Performance | Synthetic benchmark (`make bench`); quadratic reconcile fixed (8,000 signals: 5.6 s cold, linear scaling) |

**Modified/added:** `scripts/catchup_core/**`, `scripts/lib/{action_policy,action_process}.py`, `scripts/{action_executor,action_queue,tool_guard,session_stop,stale_banner,vault}.py`, `scripts/actions/reminder_create.py`, `scripts/preflight*`, `config/{Artha.core,commands,glossary}.md`, `config/workflow/*`, `docs/*`, `tools/*`, `Makefile`, `.github/workflows/ci.yml`, `.gitignore` (secret-file patterns added in the finalization audit).
**Tests:** 5,091 isolated tests pass (594 in the core suite, 96.7% line / 91.1% branch coverage, 96.5% on hook and policy modules); strict mypy and ruff clean; privacy scan clean.
**Not done (recorded in the spec log):** consented real-data shadow run, labeled corpus and release scorecard (cutover stays off), owner UAT, measured latency/cost on the real workload, Windows validation, OCR/vision extraction, live action-database status migration, incumbent in-memory vault.

### Hardened — AR-9 External Agent Composition (Safety Audit)
- `scripts/lib/prompt_composer.py`: C-1 template injection defense — user query braces escaped before `str.format()`; 8,000 char hard cap with truncation indicator
- `scripts/lib/knowledge_extractor.py`: C-2 atomic cache writes — `tempfile.mkstemp()` + `os.replace()` prevents partial-write corruption on crash or power loss
- `scripts/lib/context_scrubber.py`: C-3 PII guard fail-safety — strict mode now blocks delegation when PII guard is unavailable or raises an exception (previously passed through)
- `scripts/lib/agent_registry.py`: M-1 dead import cleanup (`copy`, `json`); silent YAML parse `except: pass` replaced with `_log.warning("Skipping malformed…")`
- `scripts/lib/agent_health.py`: M-4 quality score clamped to `[0.0, 1.0]` before health tracking; bare `except Exception: pass` narrowed to `except OSError` with debug log
- `tests/ext_agents/test_safety_invariants.py`: 16 new tests — 5 template injection, 3 atomic write, 4 PII boundary, 2 quality clamping, 1 query truncation, 1 registry resilience
- Spec: Tech Spec v3.24.0 §23.5; AR-9 design spec v1.4.0
- 270 AR-9 tests passing (254 baseline + 16 safety invariants)

### Added — AFW-10 Domain Training & Feedback Loop
- `scripts/domain_training.py`: per-domain accuracy tracking over successive catch-up runs, correction compounding detection, training suggestions for underperforming domains, self-model writer integration
- Config gate: `harness.agentic.domain_training.enabled` (default: true); activation requires minimum 5 catch-up runs
- CLI: `python scripts/domain_training.py` (full report), `--json` (structured output), `--domain X` (single domain)
- Ref: specs/agent-fw.md §3.10, AFW-10

### Added — EV-12 Golden-Set Eval Framework
- `tests/eval/golden_set/test_golden_set.py`: parametrized regression tests loading fixtures from YAML
- `tests/eval/golden_set/fixtures.yaml` (v1.0.0): 10 briefing quality fixtures (4 golden, 6 anti-golden) covering actionability, specificity, completeness, signal_purity, calibration dimensions
- All fixture content is 100% fictional (DD-5 compliant)
- Ref: specs/eval.md EV-12, tests/eval/rubric.yaml

### Added — `/work code` Command (CodeSearch MCP Integration)
- `config/commands.md`: `/work code <question>` routes code-level questions to CodeSearch MCP for ADO repo search (Service-ProductD, Service-ProductC)
- Supports natural-language code search and symbol lookup (class, method, function)
- Falls back to golden query catalog when CodeSearch MCP is unavailable

### Added — Agent Framework v1 (AFW, v3.21.0)
- AFW-1: Tripwire Guardrail System — 7 runtime-enforced blockers (PII leak, vault mis-access, prompt injection, scope creep, data exfil, disallowed tool call, undefined domain write); zero silent failures
- AFW-2: Progressive Domain Loading — 6 always-load domains, 80% token savings for targeted queries, `scripts/domain_index.py`
- AFW-3: Middleware Pipeline — 3-hook lifecycle (pre-read, pre-write, post-write); 5 middleware components (guard, audit, versioning, tracing, compaction)
- AFW-4: Context Compaction — sliding window (last 4 messages), artifact offload for large state blobs, CompactionPolicy dataclass
- AFW-5: Workflow Checkpointing — 4h TTL, pickle-based checkpoint, resume prompt, phase-level granularity
- AFW-6: Session Rewind/Undo — `/undo [domain]`, snapshot-before-write model, diff-before-confirm, high-score item safety warning
- AFW-7/ADR-001: Flat-File Memory Provider — YAML frontmatter, synonym expansion, scoped recall (session/user/repo), dedup, schema migrations
- AFW-9: Composite Signal Scoring — urgency × impact × freshness formula, configurable weights, suppress threshold 0.20, promote threshold 0.66
- AFW-11: Structured Tracing — trace_id propagation, JSONL schema, `scripts/log_digest.py`, 7-day retention policy
- Spec updates: Tech Spec v3.21.0 §25 (10 subsections), PRD §9.10, UX Spec §14.7 + `/undo` command entry, README "What You Get" expanded
- 4,872 tests passing across all AFW waves

### Added — Eval Spec v1.4.0 (specs/eval.md)
- DD-17: Schema versioning policy — all eval artifacts carry `schema_version` with semver, validated by MetricStore on load
- DD-18: Dynamic domain expectations — `_load_expected_domains()` reads `config/domain_registry.yaml` instead of hardcoded list
- §7.5: Eval Data Privacy Classification — artifact-level PII/sensitivity table, `.gitignore` audit checklist
- EV-0d: Eval dashboard — `print_summary()` function with `--summary` / `--summary --json` CLI flags
- Reading guide at top of TOC for audience-specific navigation
- Risks R21 (schema version drift across devices) and R22 (domain registry out of sync)
- Assumptions A-23 (`domain_registry.yaml` exists) and A-24 (no existing `schema_version` fields)
- `schema_version: '1.0.0'` added to all Appendix B schema examples (B.1–B.4)
- 10 new tests (3 EV-0d + 4 DD-17 + 3 DD-18) — total 127 tests across 26 files

### Added — Action Layer v1.4: Orchestrator + Signal Extraction (specs/actions-reloaded.md)
- `scripts/action_orchestrator.py`: new top-level CLI combining email signal extraction, pattern engine, AI signals, dedup, compose/propose, approve/reject/defer/expire lifecycle, and audit log into one command (`--run`, `--list`, `--show`, `--approve`, `--reject`, `--defer`, `--approve-all-low`, `--expire`, `--health`)
- `scripts/email_signal_extractor.py`: expanded from 1-pattern to 9-category signal coverage — RSVP deadlines, appointment confirmed, bill due, form deadline (+ cancellation forms), delivery arrival (including "arriving [date]" variants), security alerts, subscription renewals, school action needed (missing assignments, surveys, events), financial alerts (INR/NEFT/UPI transactions)
- `scripts/action_queue.py`: `find_by_prefix()` method — resolves 8-char ID prefix across all non-terminal statuses, enabling `--reject` and `--show` on deferred items; `deferred → rejected` added to state machine transitions (§2.4)
- `scripts/action_composer.py`: `school_action_needed` remapped `email_reply → reminder_create` (eliminates spurious `thread_id` handler validation failure); added `bill_due`, `financial_alert`, `subscription_renewal` entries to `_FALLBACK_SIGNAL_ROUTING`
- `config/signal_routing.yaml`: `school_action_needed` updated to `reminder_create`, `bill_due` friction lowered to `standard`, `financial_alert` entry added
- `scripts/email_signal_extractor.py`: `_extract_text()` now scans `body` field in addition to `body_preview` (captures Amazon order "Arriving April 8" and other body-only content)
- E2E validated on 7-day real pipeline data (750 records: WhatsApp, Gmail, Outlook, OneNote, HomeAssistant, RSS): 14 signals extracted, 8 unique after dedup, 4 proposals queued
- `tests/unit/test_action_orchestrator.py`, `tests/unit/test_action_orchestrator_security.py`, `tests/integration/test_action_orchestrator.py`: full test coverage for new orchestrator (4,014 tests total, all passing)
- `specs/actions-reloaded.md`: canonical spec for Action Layer v1.4 — now committed to repo

### Fixed — Action Layer
- `_resolve_id()` previously returned raw 8-char prefix when action was deferred, causing `transition()` to raise "Action not found"; now uses `find_by_prefix()` to search all active statuses
- `cmd_show()` had separate duplicate prefix-matching logic; consolidated to use `_resolve_id()` — deferred items now visible via `--show`
- State machine blocked `deferred → rejected`; adding `rejected` to allowed transitions from `deferred` enables users to discard deferred items without un-deferring first
- Pipeline `--output` write timing issue investigated and resolved: full multi-connector run (750 records, 135s) confirmed to write atomically

### Added — Kusto Live Data Pipeline (Tier 1)
- `scripts/kusto_runner.py`: `run_refresh_set()` batch API — runs curated golden queries (GQ-001, 002, 010, 012, 050, 051) and returns structured results for pipeline consumption
- `scripts/work_loop.py`: Kusto provider integration — `_check_kusto_available()` in preflight, `_run_kusto_metrics()` in fetch stage, wired into `_write_domain_files_from_connector_data()`
- `scripts/work_domain_writers.py`: `write_kusto_metrics_state()` — in-place regex update of xpf-program-structure.md metric values (M01 fleet size, M04/M05 velocity, M06 throughput, M20 incidents) with Kusto-live timestamps
- `ProviderAvailability.kusto` field added to work loop data model

### Added — PF Join Pattern & Testing (Tier 3)
- PF cross-cluster join pattern for 12 Env_Machines golden queries (GQ-021, 024, 041, 060, 062–066, 068, 091, 121) — `let PF_Clusters = cluster('xdeployment...').database('Deployment').TenantCatalogSnapshot | where IsPF == true | distinct ClusterName` then `| where AutoGen_Cluster in (PF_Clusters)`
- 55 new tests: `test_kusto_metrics_writer` (19), `test_program_metrics` (10), `test_pulse_health` (5), `test_meeting_context` (12), `test_kusto_refresh_set` (9) — total suite now 1,060 passing
- `/work notes [meeting-id]` command wired into `work_reader.py` dispatch — reads `work-notes.md`, supports meeting-id search, suggests WorkIQ for missing notes
- ProgramX staleness advisory in data freshness footer — `_xpf_staleness_hours()` and `_xpf_staleness_advisory()` in `scripts/work/helpers.py`; surfaces when `xpf-program-structure.md` is >18h stale (PULL > PUSH pattern)
- WorkIQ lookup hint in `/work people` when person not found locally

### Changed — Program-Aware Narrative Output (Tier 2)
- `scripts/narrative_engine.py`: `_load_program_metrics()` parser — extracts signal summary, risk posture, per-workstream signals, and red metrics from xpf-program-structure.md
- `scripts/narrative_engine.py`: `generate_newsletter()` — new `program_metrics` section with per-WS status, top red risks, risk posture; enriched `executive_summary` with program posture
- `scripts/narrative_engine.py`: `generate_deck()` — exec summary shows program risk, `_section_metrics` renders per-WS status, `_section_risks` surfaces red metrics
- `scripts/narrative_engine.py`: `generate_connect_summary()` — new "Quantitative Evidence (Kusto-validated)" section with green metrics as wins
- `scripts/narrative_engine.py`: `_extract_ws_metrics()` helper for per-workstream metric extraction by signal filter
- `scripts/work/briefing.py`: `cmd_pulse()` — new program health one-liner: `MEDIUM (R:7 Y:24 G:7)`
- `scripts/work/meetings.py`: `cmd_prep()` — ProgramX program context injection for meetings matching ProgramX keywords (risk posture + workstream metrics)
- Freshness footer now includes xpf-program-structure.md timestamp across all narrative outputs

### Fixed
- `.vscode/mcp.json`: deduplicated `kusto-xdataanalytics` key (renamed second to `kusto-xdataanalytics-cogs`)
- `scripts/work_domain_writers.py`: cross-platform date formatting (replaced `%-m/%-d` with `f"{now.month}/{now.day}"` for Windows compatibility)
- 12 golden queries: `IsPF_TODO_NEEDS_JOIN` → cross-cluster PF join via ClusterName (validated live — GQ-060 confirmed)
- 12 golden queries: `TIMESTAMP` → `AutoGen_TimeStamp` for all Env_Machines table queries

### Updated — Specs & Status
- `config/implementation_status.yaml`: added `work_os_kusto_integration` entry; updated `work_os_domain_writers` from partial→implemented (7 writers + 3 appenders); updated notes for pulse/prep/newsletter/deck/connect-prep commands to reflect program metrics integration

---

## [8.5.0] — 2026-03-27

### Changed — PAY-DEBT v1.0: Structural Integrity Plan (specs/pay-debt.md)

Eliminated three 1,800–3,500-line god files, added structured observability, hardened registries, schemas, and the PII guard pipeline. All 2,886 baseline tests preserved; ~328 new tests added.

**Phase 1 — Foundation Hardening (TD-5, TD-10, TD-11)**
- `foundation.py`: added `get_config()` accessor — safe alternative to module-level aliases frozen at import time
- `scripts/health_check_updater.py` (new): extracted `update_channel_health_md()` from `lib/common.py`; deprecated stub re-export kept for backward compatibility
- `middleware/audit_middleware.py`: replaced silent `except OSError: pass` with `print("[WARN] audit write failed: ...", file=sys.stderr)` — audit trail gaps now visible

**Phase 2 — Observability Layer (TD-4)**
- `scripts/lib/logger.py` (new, ~120 LOC): structured JSONL logger with daily rotation, 30-day pruning, PII-safe design, and trace_id/correlation_id support
- Integrated into `pipeline.py` (connector.fetch events), `action_executor.py` (action.executed events), and `channel_listener.py` (command.received/completed events)
- Log sink: `~/.artha-local/logs/artha.YYYY-MM-DD.log.jsonl` (machine-local, never cloud-synced)

**Phase 3 — Work OS Restructure (TD-2) ★ HIGHEST PRIORITY**
- `scripts/work_reader.py`: reduced from 3,146 LOC to ~313 LOC facade + `main()`
- Decomposed into 8 focused submodules under `scripts/work/`: `helpers`, `briefing`, `meetings`, `health`, `decisions`, `discovery`, `career`, `narrative`
- All 284 existing work reader tests preserved via strangler-fig facade
- `scripts/narrative_engine.py`: removed 6 duplicate helper functions; now imports from `work.helpers`

**Phase 4 — Channel Listener Restructure (TD-1)**
- `scripts/channel_listener.py`: reduced from 3,543 LOC to ~592 LOC entry point
- Decomposed into 8 focused submodules under `scripts/channel/`: `router`, `handlers`, `catchup`, `llm_bridge`, `security`, `formatters`, `state_readers`, `stage`
- Old `test_channel_listener.py` (24 tests) retained in parallel until behavioral parity confirmed

**Phase 5 — Preflight Restructure (TD-1)**
- `scripts/preflight.py` (1,848 LOC) → `scripts/preflight/` package
- Decomposed into 5 category modules: `vault_checks`, `oauth_checks`, `api_checks`, `state_checks`, `integration_checks`
- `config/Artha.core.md` Step 0 updated from `python3 scripts/preflight.py` to `python3 -m preflight`
- ⚠️ **Breaking change:** If you call `python3 scripts/preflight.py` directly in custom scripts, update to `python3 -m preflight`

**Phase 6 — Registry Consolidation (TD-3)**
- `pipeline.py`: `_HANDLER_MAP` now derived from `connectors.yaml` at startup via `_derive_handler_map()`; `_FALLBACK_HANDLER_MAP` + `_ALLOWED_MODULES` frozenset provide fail-degraded safety + security gate
- `action_executor.py`: same pattern for `_derive_action_map(connectors.yaml)`
- `action_composer.py`: added `_validate_routing_table()` — catches unknown action_type at import time, not signal-fire time
- `connectors.rss_feed` added to `_ALLOWED_MODULES` (feed connector was deployed but missing from security allowlist)
- Handler map derivation fixed: reads `fetch.handler` path stem instead of falling back to YAML connector name (resolves gmail → google_email mismatch)

**Phase 7 — Hardening (TD-7, TD-8)**
- `scripts/lib/state_schema.py` (new, ~80 LOC): lightweight required-field validator for state file frontmatter
- `middleware/write_guard.py`: `before_write()` now calls `validate_frontmatter()` — blocks writes missing required fields in registered schemas
- `action_executor.py`: PII check now calls `pii_guard.scan()` in-process instead of `subprocess.Popen`; saves ~100ms per action invocation

### Fixed
- `_derive_handler_map` false SECURITY warnings for legitimate connectors (7 → 0) with real `connectors.yaml`
- `connectors.rss_feed` was enabled in `connectors.yaml` and the script existed, but absent from `_ALLOWED_MODULES` — would have caused runtime rejection
- Unused `from pathlib import Path` import removed from `health_check_updater.py`



### Added — Memory Pipeline Activation (MEM v1.3.0, specs/mem.md)

Fixed 4 independent breaks that left Artha's 9-script memory subsystem (3,274 lines) fully coded but operationally inert — `state/memory.md` had `facts: []`, `state/self_model.md` was template-only, and `state/learned_procedures/` was empty after 10+ catch-ups.

**Break 1 — Deterministic pipeline trigger (`scripts/post_catchup_memory.py`, new ~240 LOC):**
- `run(briefing_path, artha_dir, dry_run) -> dict` — 5-step pipeline invoked deterministically from Step 11c
- Extracts facts DIRECTLY from briefing (bypasses lossy `SessionSummary.to_markdown()` round-trip)
- Steps: write session history (search/recovery) → extract facts → persist → update self-model → log to `state/memory_pipeline_runs.jsonl`
- CLI: `--briefing PATH` (required), `--dry-run`, `--discover` (bootstrap/manual only), `--rebuild-self-model`
- Config kill switch: `harness.agentic.post_catchup_memory.enabled` (default: true)

**Break 2 — Dual-path briefing parser (`scripts/fact_extractor.py` extended):**
- `_parse_briefing_md(content)` handles both Telegram (`━━` box-drawing delimiters) and Markdown (`## CRITICAL/URGENT/BY DOMAIN` headings)
- `extract_facts_from_summary()` renamed `summary_path→input_path`; 3-branch format auto-detection
- Previously returned 0 facts on any briefing file

**Break 3 — Health-check run parser (`scripts/self_model_writer.py` extended):**
- `_parse_catchup_runs_from_markdown(content)` reads `## Catch-Up Run History` freeform section
- `SelfModelWriter.update()` tries YAML frontmatter first, falls back to markdown parser
- Previously: `catch_up_runs = []` → `len < 5` gate → `update()` always returned `False`

**Break 4 — Briefing-aware extraction signals (Phase 1b, `scripts/fact_extractor.py`):**
- 4 new `_EXTRACTION_SIGNALS`: deadline (30d TTL), decision-pending (60d), `$N/mo` threshold (90d, finance), OI-NNN pattern (90d)
- Previously: 0 `preference`-type facts across all briefings; Effective Strategies section permanently empty

**Domain sensitivity tiering:**
- `_HIGH_STAKES_DOMAINS = frozenset({"finance", "health", "immigration", "insurance", "legal"})`
- `_apply_domain_sensitivity_ttl()` — caps TTL at 90d for non-correction/preference high-stakes facts

**Bootstrap (`scripts/bootstrap_memory.py` + `scripts/bootstrap_seeds.yaml`, new):**
- Batch-seeds `state/memory.md` from all historical briefings (single `persist_facts()` call — order-independent)
- `bootstrap_seeds.yaml` — declarative procedure manifest; initial seed: `digital-onenote-parallel-fetch`

**Wiring:** `config/workflow/finalize.md` Step 11c rewritten to deterministic script invocation; `config/artha_config.yaml` updated.

**Validated state:** `state/memory.md` populated, `state/self_model.md` live, procedure seeded, 18/18 E2E checks pass, 1546 tests passing.

---

## [8.3.0] — 2026-03-21

### Added — ACT-RELOADED: Sense–Reason–Act–Learn (E1–E16, specs/act-reloaded.md v1.3.0)

Evolved Artha from a **read–reason–act** system to a **sense–reason–act–learn** system with 16 enhancements across 3 waves.

**Wave 1 — New signal pipelines:**
- **`scripts/email_signal_extractor.py`** — Deterministic Step 6.5 signal extraction from email body: 8 categories (RSVP, appointment, payment notice, form deadline, shipment arrival, security alert, renewal notice, school action needed). 6 new `_SIGNAL_ROUTING` entries in `action_composer.py`.
- **`scripts/attachment_router.py`** — Filename-pattern-based attachment routing to domains (finance, health, immigration, kids, insurance, employment, home). `AttachmentSignal` dataclass. PII scrubbing from filenames. `to_domain_signals()` converter.
- **`scripts/pattern_engine.py`** + **`config/patterns.yaml`** — Deterministic cross-domain pattern engine with 8 named patterns and 6 operators (`days_until_lte`, `lt/gt/eq`, `exists`, `has_item_within_days`, `contains`, `stale_days`). Replaces prose rules in Step 8. Cooldown-state isolation per `root_dir`.

**Wave 2 — Intelligence loop:**
- **`scripts/briefing_adapter.py`** — Adaptive briefing with `BriefingConfig` dataclass; 6 rules (R1–R6); 10-run cold-start gate; transparency footer.
- **`scripts/nudge_daemon.py`** — Vault-watchdog bridge for proactive notifications: 5 nudge types; 3/day cap; marker-file dedup at `tmp/nudge_*.marker`; no vault access when session inactive.
- **`channel_listener.py`** `/remember` command — Async knowledge capture: PII-guarded writes to `state/inbox.md`; 5/hour write rate limit; full-scope only; `CHANNEL_REMEMBER` audit event.

**Wave 3 — Activation enhancements:**
- **`scripts/self_model_writer.py`** — Memory→self-model pipeline; 3000-char bounded `state/self_model.md`.
- **`scripts/decision_tracker.py`** — `capture_from_command()`, `persist_proposal()`, `_load_decisions()`.
- **`scripts/relationship_pulse_view.py`** + **`state/relationships.md`** — View backing the existing `/relationships` command.
- **`scripts/power_half_hour_view.py`** — Powers the `/power` command: top OI by impact×urgency, today's calendar, intention prompt.
- **`scripts/retrospective_view.py`** — Monthly retrospective generator reading `summaries/` + state files.
- **`scripts/coaching_engine.py`** — `select_nudge(goals, memory_facts, health_history, preferences)`; 4 strategy types; moved from Step 19 → Step 8.
- **`scripts/skills/subscription_monitor.py`** extended — 4 new lifecycle detectors (trial_ending, cancellation_window, annual_review, duplicate_subscription); 5 new signal types.
- **`scripts/actions/whatsapp_cloud_send.py`** — WhatsApp Cloud API Phase 2: `SUPPORTED_TEMPLATES` frozenset; `urllib`-based HTTP; `validate()` → `tuple[bool, str]`.
- **`channel_push.py`** `_build_family_flash()` — Condensed family-scope Telegram digest.
- **`scripts/cost_tracker.py`** + `/cost` command — Per-session API cost estimation.
- **`state/inbox.md`** + **`state/relationships.md`** — New state files.
- **`config/artha_config.yaml`** `enhancements.*` flags — Feature flag namespace for all 16 enhancements.

### Fixed
- **`scripts/pattern_engine.py`** `_load_yaml_file()` — Switched from `yaml.safe_load()` to `next(yaml.safe_load_all(), None)` to handle Markdown files with YAML frontmatter without raising `ComposerError`.
- **`scripts/power_half_hour_view.py`** `_parse_open_items()` — Correct YAML list item extraction with `yaml.safe_load(block)[0]` instead of broken `re.sub` stripping.
- **`scripts/cost_tracker.py`** `_load_pipeline_metrics()` — Handle list-formatted JSON output (wraps list in `{"runs": [...]}` dict).

**Tests:** 1520 passed, 0 failures (5 skipped, 20 xfailed). +151 new tests across 16 new test files.

---

## [8.2.0] — 2026-03-21

### Added — Home Assistant IoT Integration (ARTHA-IOT, PRD F7.4 + F12.5)

Full Wave 1 + Wave 2 implementation of the IoT/Home Assistant integration spec (`specs/iot.md` v1.4.0).

**Wave 1 — Connector (read-only data ingestion):**
- **`scripts/connectors/homeassistant.py`** (new, ~450 LOC) — HA REST API connector. Calls `GET /api/states`, applies hard-floor privacy filtering (`_EXCLUDED_DOMAINS`: camera, media_player, tts, stt, conversation, persistent_notification, update), strips PII attributes (ip_address, mac_address, entity_picture, access_token), sanitizes device_tracker entities to `home`/`not_home`/`unknown`. LAN self-gating via RFC 1918 IP detection + TCP reachability probe — raises `ConnectorOffLAN` (silent skip) when not on home network. Atomic cache write to `tmp/ha_entities.json` via `tempfile.mkstemp` + `os.replace()`.
- **`scripts/setup_ha_token.py`** (new, ~340 LOC) — Interactive 7-step setup wizard: URL validation → token input (masked) → connectivity test → keyring storage → `connectors.yaml` update → `.nosync` creation → success summary. Atomic YAML write. Rollback instructions printed on every failure.
- **`config/connectors.yaml`** — Added `homeassistant` connector block (`enabled: false`, `auth.method: api_key`, `auth.credential_key: artha-ha-token`, `requires_lan: true`).
- **`scripts/preflight.py`** — Added `check_ha_connectivity()` P1 non-blocking preflight check. Handles off-LAN gracefully.
- **`prompts/home.md`** — Added "Smart Home / IoT" section: activation conditions, briefing formats for CRITICAL/MONITORED/energy signals, signal-to-action routing table, privacy notes.

**Wave 2 — Skill (deterministic alerting):**
- **`scripts/skills/home_device_monitor.py`** (new, ~560 LOC) — Deterministic device health monitoring skill. Reads `tmp/ha_entities.json`, applies threshold checks: device offline >2h (`security_device_offline` for Ring/lock/alarm, `device_offline` for monitored), printer supply <20% (`supply_low`), swim spa temp variance >5°F (`spa_maintenance`), power spike >30% above weekly average (`energy_anomaly`). Constructs `DomainSignal` objects directly — bypasses LLM mediation for deterministic signals. Serializes via `dataclasses.asdict()`. Atomic `state/home_iot.md` write.

**Framework wiring:**
- **`scripts/pipeline.py`** — `"connectors.homeassistant"` added to `_HANDLER_MAP`.
- **`scripts/skill_runner.py`** — `"home_device_monitor"` added to `_ALLOWED_SKILLS` frozenset.
- **`scripts/action_composer.py`** — 6 IoT signal routing entries: `device_offline`, `security_device_offline`, `energy_anomaly`, `supply_low`, `spa_maintenance`, `security_travel_conflict`.
- **`config/skills.yaml`** — `home_device_monitor` entry (`enabled: false`, `priority: P1`, `cadence: every_run`).
- **`config/user_profile.yaml`** — `integrations.homeassistant` consent/preference flags section.

**Auth fix (G1):**
- **`scripts/lib/auth.py`** — Fixed `api_key` branch in `load_auth_context()`: now loads actual credential from keyring when `credential_key` is present. Canvas LMS (per-child key pattern) is unaffected.

**Tests (126 new, 1369 total):**
- `tests/unit/test_homeassistant_connector.py` — 36 tests (LAN detection, privacy, fetch, health_check, cache write, G1 regression)
- `tests/unit/test_home_device_monitor.py` — 67 tests (classification, offline detection, all thresholds, serialization, state write, DomainSignal round-trip)
- `tests/unit/test_setup_ha_token.py` — 23 tests (URL validation, connectivity, token storage, .nosync)

**New spec:** `specs/iot.md` v1.4.0 — all gap resolutions documented (G1 auth, G2 ha_url ownership).

> Feature gated behind `enabled: false` — no runtime impact until activated via `python scripts/setup_ha_token.py`.

---

## [8.1.2] — 2026-03-21

### Fixed — Linux CI: `SystemExit` from keyring not caught in action key helpers

- **`scripts/action_executor.py`** — `_get_privkey()` and `_get_pubkey()` now catch `(Exception, SystemExit)` instead of `Exception` alone. On Ubuntu CI (no D-Bus / no SecretService backend), `keyring.get_password()` raises `NoKeyringError`; `foundation.get_private_key()` caught that as `Exception` then called `die()` → `sys.exit(1)` → `SystemExit(1)`. Because `SystemExit` is a `BaseException` subclass (not an `Exception`), it escaped the existing guard and surfaced in pytest as 8 test failures with `"SystemExit: 1"`. Fixed by widening the guard.
- **`scripts/action_queue.py`** — Same `(Exception, SystemExit)` guard applied to `_get_age_pubkey()`.
- **`scripts/action_executor.py`** — `ActionExecutor.__init__`: `detect()` now called with `skip_network=True` to avoid 3 × 3 s TCP probes per fixture instantiation in tests. Fixed `env_info.get("filesystem_writable")` → `env_info.capabilities.get("filesystem_writable")` (`EnvironmentManifest` is a dataclass, not a dict).

**CI impact**: `pytest (3.11)` and `pytest (3.12)` CI jobs had been failing since the Action Layer commit `0900fcc` (2026-03-19). All 4 intermediate commits (`0900fcc`, `fc0b5a5`, `d08f9ac`, `5aad9dd`) had red CI. All 8 CI jobs green as of `5a3ccf6`.

---

## [8.1.1] — 2026-03-20

### Fixed — Action Handler Module Paths + Mock-patch Compatibility

- **`scripts/actions/__init__.py` + `scripts/action_executor.py`** — `_HANDLER_MAP` paths corrected from `scripts.actions.*` to `actions.*`. Because `scripts/` is on `sys.path`, `importlib.import_module("scripts.actions.email_send")` loaded a *separate* module object from the `actions.email_send` the tests imported. This broke all `patch("actions.email_send.build_service", ...)` mocks and the security allowlist assertion (`v.startswith("actions.")`). Fixed. `specs/act.md §4.3` updated accordingly.
- **`scripts/actions/calendar_create.py`, `calendar_modify.py`, `email_send.py`** — `build_service` and `check_stored_credentials` promoted from lazy (deferred `from google_auth import ...` inside functions) to module-level `try/except ImportError` imports. Required for test mock-patching to work correctly via `patch("actions.X.build_service", ...)`.
- **`scripts/actions/calendar_modify.py`** — `validate()` now returns `"empty updates dict"` instead of the generic missing-parameter message when `updates={}`, matching test expectations (`assert "empty" in reason.lower()`).
- **`tests/unit/test_action_executor.py`** — `test_propose_validation_failure_raises` match string updated from `"Validation"` to `"Handler validation failed"` to match the actual error message raised by `ActionExecutor.propose()`.

---

## [8.1.0] — 2026-03-20

### Added — Messaging Connectors (WhatsApp + iMessage)

Artha's catch-up pipeline now includes local messaging data from WhatsApp Desktop and iMessage without any API keys or network access.

**WhatsApp connector (`scripts/connectors/whatsapp_local.py`)**
- **macOS**: reads `ChatStorage.sqlite` (full message text via CoreData timestamps)
- **Windows**: reads Chromium IndexedDB (LevelDB) via `ccl_chromium_reader`. Message body is AES-encrypted at rest; connector surfaces metadata — sender, group, direction, message type, timestamp
- Dual `_find_db()` paths, automatic platform detection
- Status-broadcast filtering, deduplication by `rowId`, sorted by timestamp descending
- Contact/group name resolution from IndexedDB stores (contacts, chats, group-metadata)
- `health_check()` returns `True` on both platforms when local DB is reachable

**iMessage connector (`scripts/connectors/imessage_local.py`)**
- macOS-only; reads `~/Library/Messages/chat.db`
- Handles nanosecond + second timestamp formats (Apple clock epoch conversion)
- Graceful skip (0 records, no error) on non-macOS platforms
- Requires Full Disk Access for the terminal app (System Settings → Privacy & Security)

**Other changes**
- `scripts/skills/whatsapp_last_contact.py` — added missing `get_skill()` factory (was causing `skill_runner.py` AttributeError)
- `scripts/skills/uscis_status.py` — `get_skill()` now skips approved/closed receipt numbers using 7-line context window + `_TERMINAL_STATUSES` regex; avoids polling USCIS for resolved cases
- `scripts/detect_environment.py` — filesystem write probe: `unlink()` is now best-effort (FUSE mounts on some VMs allow write but not delete)
- `config/connectors.yaml` — `whatsapp_local` + `imessage_local` registrations
- `config/routing.yaml` — `source_routes` section routing messaging sources to comms domain
- `prompts/comms.md` — messaging extraction rules and briefing format template
- `pyproject.toml` — new `messaging` optional dependency group (`ccl_chromium_reader`)
- `.gitignore` — `state/*.db` (actions.db etc.) and `test-write.txt` excluded

---

## [8.0.0] — 2026-03-19

### Added — Action Layer v1.3 (specs/act.md)

Artha evolves from **read–reason–report** to **read–reason–act**. Every alert now resolves to an executable action with a human-gated approval queue, full audit trail, and Telegram inline keyboards.

**Core Infrastructure (Phase 0)**
- **`scripts/action_queue.py`** — SQLite-backed persistent approval queue (`state/actions.db`). WAL mode, ACID transitions, 10-state machine (`pending → approved → executing → succeeded`), immutable audit log, age encryption for sensitive parameters at rest.
- **`scripts/action_executor.py`** — Core engine: `propose()`, `approve()`, `reject()`, `defer()`, `undo()`, `run_health_checks()`. Validation gates (trust + PII + rate limit) checked **before** any state transition to prevent queue corruption. New methods: `propose_direct()`, `get_action()`, `list_pending()`, `list_history()`, `queue` property.
- **`scripts/trust_enforcer.py`** — Trust level gate (0=observe, 1=propose, 2=pre-approve). Hard-coded autonomy floor: `autonomy_floor: true` actions always require human approval regardless of trust level, configuration, or user override. `evaluate_elevation()` and `apply_demotion()` with optional args.
- **`scripts/action_composer.py`** — DomainSignal → ActionProposal mapping (17 signal types). `ActionComposer(artha_dir=artha_dir)` loads config automatically. `compose()` and `compose_workflow()` for multi-step scenarios (address change, tax prep).
- **`scripts/action_rate_limiter.py`** — Per-action-type sliding-window rate limiting from `actions.yaml` config.
- **`scripts/schemas/action.py`** — JSON Schema validation for ActionProposal serialization.
- **`scripts/actions/base.py`** — `ActionHandler` Protocol with 5 methods: `validate`, `dry_run`, `execute`, `health_check`, `build_reverse_proposal`. `ActionProposal`, `ActionResult`, `DomainSignal` dataclasses. State machine constants.
- **`scripts/actions/__init__.py`** — `_HANDLER_MAP` allowlist (8 Phase 1 handlers). No arbitrary module loading.
- **`scripts/foundation.py`** — Added `age_encrypt_string()` and `age_decrypt_string()` wrappers for in-memory string encryption (used by action queue for sensitive parameter storage).
- **`state/health-check.md`** — Added `autonomy:` block with full schema (trust_level, days_at_level, acceptance_rate_90d, critical_false_positives, pre_approved_categories, last_demotion, last_elevation).

**Phase 1 Action Handlers (8 handlers)**
- **`scripts/actions/email_send.py`** — Gmail `drafts.create` / `drafts.send` / `messages.trash`. Draft-first by default. 30-second undo window.
- **`scripts/actions/email_reply.py`** — Reply-to-thread with auto In-Reply-To / References headers. Draft-first.
- **`scripts/actions/calendar_create.py`** — Google Calendar `events.insert`. All-day support, attendees, reminders. 1-hour undo window.
- **`scripts/actions/calendar_modify.py`** — Google Calendar `events.patch` with original-value snapshot for undo.
- **`scripts/actions/reminder_create.py`** — Microsoft Graph To Do `tasks` API. Auto-creates list if not found.
- **`scripts/actions/whatsapp_send.py`** — Phase 1: `wa.me/{phone}?text={encoded}` URL scheme. Phase 2: Cloud API (future).
- **`scripts/actions/todo_sync_action.py`** — Subprocess wrapper for `todo_sync.py --push / --pull / --both`.
- **`scripts/actions/instruction_sheet.py`** — Pure text markdown guide generation, saved to `tmp/instructions/`.

**Workflow Integration**
- **`scripts/preflight.py`** — Added `check_action_handlers()` (P1 check): sweeps expired actions, runs handler health checks at Step 0c.
- **`scripts/channel_listener.py`** — Added `/queue`, `/approve`, `/reject`, `/undo` commands. `_handle_callback_query()` handles `act:APPROVE:{id}`, `act:REJECT:{id}`, `act:DEFER:{id}` inline keyboard responses.
- **`config/actions.yaml`** — Migrated to schema v2.0: all 8 Phase 1 handlers with `min_trust`, `sensitivity`, `timeout_sec`, `retry`, `reversible`, `undo_window_sec`, `rate_limit`, `autonomy_floor`, `pii_allowlist`. Disabled Phase 2 stubs (bill_pay, appointment_book) and run_pipeline (no handler).
- **`config/workflow/finalize.md`** — Added Steps 12.5 (compose proposals from signals), 14.5 (push to Telegram with inline keyboards), 19.5 (action layer summary in audit + briefing footer).

**Tests (110 tests, 7 files)**
- `tests/unit/test_action_queue.py` — Queue lifecycle, state machine, dedup, expiry, audit trail
- `tests/unit/test_action_executor.py` — Propose/approve/reject/defer lifecycle, autonomy floor, handler allowlist
- `tests/unit/test_trust_enforcer.py` — Trust gates, autonomy floor, demotion, elevation
- `tests/unit/test_email_send_handler.py` — Gmail API mock tests, draft/send/undo flows
- `tests/unit/test_calendar_handler.py` — Calendar create/modify handler protocol tests
- `tests/unit/test_pii_firewall_actions.py` — SSN/CC detection, block-not-redact contract
- `tests/unit/test_safety_redteam.py` — Adversarial: prompt injection, trust bypass, handler injection, SQL injection, state machine violations, undo deadline, callback validation

### Changed
- **`config/actions.yaml`** — `run_pipeline` disabled (`enabled: false`): invoke `pipeline.py` directly; no handler in action framework.
- **`scripts/trust_enforcer.py`** — `check()` signature updated to `check(proposal, approved_by, action_config)` for ergonomic use; autonomy floor is enforced structurally regardless of `action_config` contents.

### Security
- Autonomy floor is a **hard-coded structural rule** — not bypassable by config, trust level, or `approved_by` value
- PII firewall runs at enqueue AND at execution (double-scan for modified proposals)
- Handler allowlist (`_HANDLER_MAP`) prevents arbitrary module loading
- All sensitive action parameters encrypted at rest with age
- SQL injection resistance: all queue operations use parameterized queries
- State machine enforced server-side: no client can skip states or transition from terminal states



## [7.0.6] — 2026-03-15

### Fixed — Post-audit runtime fixes

**Skill Runtime (get_skill() factories)**
- **`scripts/skills/relationship_pulse.py`** — Added `get_skill(artha_dir=None)` factory function required by `skill_runner.py` for dynamic loading. Was present as a `BaseSkill` subclass but missing the module-level factory, causing `AttributeError` on every `skill_runner` invocation.
- **`scripts/skills/occasion_tracker.py`** — Same fix: added `get_skill()` factory.
- **`scripts/skills/bill_due_tracker.py`** — Same fix: added `get_skill()` factory.
- **`scripts/skills/credit_monitor.py`** — Same fix: added `get_skill()` factory.
- **`scripts/skills/school_calendar.py`** — Same fix: added `get_skill()` factory.

**Skill Runner Allowlist**
- **`scripts/skill_runner.py`** — Added all 5 new skills to `_ALLOWED_SKILLS` frozenset: `relationship_pulse`, `occasion_tracker`, `bill_due_tracker`, `credit_monitor`, `school_calendar`. Previously they were blocked with `"Skill X is not in the allowlist"` ERROR on every run.

**Location Coordinates**
- **`config/user_profile.yaml`** *(gitignored)* — Added `lat` and `lon` coordinates to the `location` section. Previously missing, causing NOAA weather skill to raise `ValueError: NOAA weather skill not configured`.

**Briefing Format — Occasions & Wishes (U-2.5)**
- **`config/briefing-formats.md`** — Added dedicated `━━ 🎂 OCCASIONS & WISHES` section with 🔴/🟠/🟡 urgency windows, greeting suggestion format, and source attribution. Previously occasions data had no dedicated briefing placement.

**Finalize Workflow — Skill Output Integration (U-2.5)**
- **`config/workflow/finalize.md`** — Extended Step 12 with explicit skill-to-section mapping: `relationship_pulse` → RELATIONSHIP PULSE, `occasion_tracker` → OCCASIONS & WISHES (with core_family escalation to 🔴), `bill_due_tracker` + `credit_monitor` → Finance section, `school_calendar` → Kids section.

**Channels Config**
- **`config/channels.yaml`** *(gitignored)* — Created from `channels.example.yaml` template with `telegram.enabled: true`, `bot_username: artha_bot`, `push_enabled: false` (pending bot token). Chat IDs left empty pending `@userinfobot` lookup.

---

## [7.0.5] — 2026-03-14

### Added — Utilization Uplift (specs/util.md U-1–U-9)

**U-5: Privacy Hardening**
- **`config/connectors.yaml`** — All 8 personal-data connectors (`google_gmail`, `google_calendar`, `microsoft_outlook`, `microsoft_todo`, `apple_icloud_calendar`, `apple_reminders`, `workiq`, `health_kit`) now have `prefer_mcp: false` with comment `# PRIVACY: no official MCP — use direct API only`. Previously all 8 were `prefer_mcp: true`, routing personal data through unapproved 3rd-party MCP proxies.

**U-6: Profile Scaffold**
- **`config/user_profile.yaml`** — Rebuilt from a 4-line stub to a 130+ line comprehensive scaffold with all known values: full family members (primary user, spouse, 2 children with school info), Washington state location, household info, 17 enabled domains, integration preferences, encryption key preserved. 8 `# FILL:` markers left for user to supply (Gmail address, iCloud, city, parcel ID, Canvas URLs, VINs, briefing email, Telegram IDs). Passes `generate_identity.py --validate` with 17 domains enabled.

**U-7: Routing Generation**
- **`config/routing.yaml`** — Generated from enriched user profile via `generate_identity.py --with-routing`. Was `key: value` (completely broken). Now contains `system_routes` (immigration/USCIS, finance institutions, estate, kids/school, marketing suppression) and `user_routes` (school domains, finance institutions list).

**U-8: Pipeline Step 21**
- **`config/Artha.core.md`** — Step 21 added after Step 20 (channel push): runs `fact_extractor.py` on `tmp/session_history_*.json` to extract 6 fact types (correction, pattern, preference, contact, schedule, threshold) and persist to `state/memory.md`. Non-blocking (failure never breaks catch-up). Gated on `harness.agentic.fact_extraction.enabled`.

**U-2: Occasions Enrichment**
- **`state/occasions.md`** — Schema bumped to v1.1. Added an extended-family birthdays table (contacts with dates extracted from `state/contacts.md`). Added a "Cultural & Religious Occasions" table of festivals and observances. Added "US Public Holidays (2026)" table with 12 holidays. Fixed all existing entries: ISO date format, corrected ages, standardized tables.

**U-1: Circles Schema**
- **`state/contacts.md`** — YAML frontmatter replaced with full circles classification (schema v1.1): 6 circles defined — `core_family` (nudge: false), `extended_family_india` (14-day cadence), `best_friends` (30-day cadence), `us_friends` (30-day cadence), `spiritual` (weekly), `professional` (as-needed). Each circle has label, members list, cadence, nudge flag, signal_sources, and description.

**U-9: New Relationship & Life Skills**
- **`scripts/skills/relationship_pulse.py`** (new) — Reads circle definitions from `state/contacts.md` YAML frontmatter; extracts last-contact ISO dates from table rows; computes `days_since_contact` vs `_CADENCE_DAYS` threshold per circle; returns top-10 most overdue contacts sorted by `overdue_by`. Only processes circles with `nudge: true`.
- **`scripts/skills/occasion_tracker.py`** (new) — Parses `state/occasions.md` for birthdays, festivals, anniversaries, and holidays. `_this_year_occurrence()` handles Feb-29 leap-year edge case; `_parse_date_flexible()` handles ISO-8601. Alert windows: birthday 🔴 ≤3d / 🟠 ≤7d / 🟡 ≤14d; festival/holiday 7d window. Generates contextual WhatsApp greeting suggestions per event type.
- **`scripts/skills/bill_due_tracker.py`** (new) — Extracts bill rows from `state/occasions.md` Financial/Legal/Deadline sections. `_parse_bill_date()` handles 4 formats: ISO, "Month Day, Year", "Monthly (Nth)", "Semi-annual (Mo & Mo)". Alert severities: 🔴 ≤1d, 🟠 ≤3d, 🟡 ≤7d; also detects overdue items.
- **`scripts/skills/credit_monitor.py`** (new) — Scans `state/digital.md` and `state/finance.md` for credit monitoring signals using 4 regex patterns: `_FRAUD_PATTERN` (🔴), `_INQUIRY_PATTERN` (🟠), `_NEW_ACCOUNT_PATTERN` (🟠), `_SCORE_CHANGE_PATTERN` (🟡). Deduplicates by excerpt key; sorts fraud first; gracefully degrades when vault-encrypted files unavailable.
- **`scripts/skills/school_calendar.py`** (new) — Scans `state/calendar.md`, `state/occasions.md`, and `state/kids.md` for school-district events. `_SCHOOL_KEYWORDS` regex covers PTC, parent-teacher, school and grade terms, no-school days, breaks, graduation. `_GRADE_PATTERN` detects failing/incomplete/missing assignments. Deduplicates events by `(date, event[:40])` key.
- **`config/skills.yaml`** — 5 new skills registered: `relationship_pulse`, `occasion_tracker`, `bill_due_tracker`, `credit_monitor`, `school_calendar`. Total: 13 skills (was 8).

**U-2.4: Occasion-Aware Social Prompt**
- **`prompts/social.md`** — Added "Occasion-Aware Intelligence" section (schema v1.1): 3-day priority lane (🔴 URGENT block for imminent birthdays/festivals/anniversaries), circle cross-reference protocol (channel + tone per circle type), 8 WhatsApp message templates (birthday peer, birthday elder, birthday child, festival greetings, reconnect), and structured briefing output format with 🔴/🟠/🟡 windows.

### Fixed
- **`tests/conftest.py`** — Added `_PROJECT_ROOT` (project root directory) to `sys.path` alongside the existing `_SCRIPTS_DIR`. Previously, tests using `from scripts.skills.*` imports only worked when run as part of the full suite (order-dependent via other imports); isolated runs (`pytest tests/unit/test_util_skills.py`) failed with `ModuleNotFoundError: No module named 'scripts.skills'`. Both `from skills.X import Y` and `from scripts.skills.X import Y` import styles now work in all invocation modes.

### Tests
- Added **27** new tests in `tests/unit/test_util_skills.py`:
  - `TestRelationshipPulse` (6 tests) — stale contact detection, fresh contact exclusion, `nudge: false` circle skip, never-contacted contact inclusion, missing file graceful degradation, `to_dict()` summary format.
  - `TestOccasionTracker` (7 tests) — upcoming birthday detection, past birthday exclusion, festival window detection, anniversary 30-day window, empty occasions graceful return, 🔴 severity within 3 days, `to_dict()` summary.
  - `TestBillDueTracker` (5 tests) — 🟠 severity 3-day bill, 🔴 severity 1-day bill, 30-day bill not surfaced, `_parse_bill_date()` monthly format, missing occasions file graceful return.
  - `TestCreditMonitor` (4 tests) — hard inquiry detection, fraud alert prioritization, clean content no alerts, missing state files empty return.
  - `TestSchoolCalendar` (5 tests) — school keyword event detection, non-school event exclusion, grade alert detection from kids.md, missing files empty return, `to_dict()` summary.

**Total test count: 1069 (+27 from baseline 1042)**

---

## [7.0.4] — 2026-03-15

### Added — Catch-Up Quality Hardening (P0/P1/P2)

**P0: Critical Reliability Fixes**
- **`scripts/preflight.py`** — `check_msgraph_token()` now attempts proactive refresh when the token is already expired (`secs_left < 0`), not just within 5 minutes; previously an expired token silently skipped the refresh branch and caused the entire catch-up to fail with 401 errors.
- **`scripts/preflight.py`** — Added `_is_bootstrap_stub(path)` helper that detects un-populated YAML template stubs (files containing `# Content\nsome: value` fingerprint). `check_state_templates()` now reports and, with `--fix`, replaces stubs — in addition to creating missing files. Summary line now reads "created N + replaced M bootstrap stubs".
- **`scripts/migrate_oi.py`** (new) — Idempotent one-time backfill scanner: reads all `state/*.md` files, extracts `OI-NNN` references, and appends any missing IDs to `state/open_items.md` as `-[ ] OI-NNN (backfilled)` entries. Reports the highest OI-NNN seen so users can update the next-ID counter in `state/memory.md`. Supports `--dry-run`.

**P1: Workflow Script Coverage**
- **`scripts/health_check_writer.py`** (new) — Atomic writer for `state/health-check.md` frontmatter. Non-blocking 3-second file lock (`state/.health-check.lock`); detects and replaces bootstrap stubs; upserts individual YAML keys while preserving unknown fields; rotates connector log blocks older than 7 days to `tmp/connector_health_log.md`; writes via `os.replace()` for atomicity. CLI: `python3 scripts/health_check_writer.py --last-catch-up ISO --email-count N --domains-processed a,b,c --mode normal`.
- **`config/workflow/finalize.md`** — Step 16 rewritten: replaces the AI-manual YAML instruction with a direct `health_check_writer.py` CLI call, eliminating frontmatter corruption from hand-edited YAML.
- **`config/workflow/finalize.md`** — Step 11c updated to include `python3 scripts/calendar_writer.py --input tmp/pipeline_output.jsonl` for automatic calendar state persistence.
- **`scripts/preflight.py`** — Added 48-hour advance advisory for MS Graph token expiry (`TOKEN_ADVANCE_WARN_SECONDS = 172800`). When `0 < secs_left < 172800`, emits a P1 advisory "expires in ~Nh; run --reauth before your next session" so users act proactively rather than hitting an expired token mid-catch-up.
- **`scripts/calendar_writer.py`** (new) — Reads pipeline JSONL output (stdin, `--input PATH`, or auto-detects `tmp/pipeline_output.jsonl`), filters for calendar/event records, deduplicates events via SHA-256 fingerprint (`<!-- dedup:KEY -->`), and appends new events to `state/calendar.md`. Recognises `google_calendar`, `gcal`, `outlook_calendar`, `msgraph_calendar`, `caldav_calendar`, and `workiq_calendar` connector output. Detects and replaces bootstrap stubs with a proper calendar YAML schema.

**P2: Noise Reduction & Context Quality**
- **`scripts/email_classifier.py`** (new) — Rule-based marketing email tagger. Whitelist-first: `_IMPORTANT_SENDER_DOMAINS` (USCIS, financial institutions, HR platforms, government agencies, etc.) and `_IMPORTANT_SUBJECT_PATTERNS` (order/shipment, security alerts, immigration, tax) always override marketing signals. Classifies remaining emails by sender patterns and subject keywords into `marketing`, `newsletter`, `promotional`, `social`, or `transactional` categories; sets `marketing: true` on records that are noise. Configurable custom domain whitelist via `artha_config.yaml` `email_classifier.whitelist_domains`.
- **`scripts/pipeline.py`** — Added `_classify_email_lines()` helper that is invoked post-fetch per connector. Falls back silently if `email_classifier` is not importable (safe for fresh installs). Eliminates ~52 % context noise from routine marketing emails.
- **`scripts/session_summarizer.py`** — `get_context_card()` now auto-invokes `fact_extractor.extract_facts_from_summary()` + `persist_facts()` for catch-up commands via `_auto_extract_facts_if_catchup()`. Appends "Facts persisted to memory: N" to the context card when facts are saved. Gated on `harness.agentic.fact_extraction.enabled` config flag; fails silently if `fact_extractor` is unavailable.
- **`config/workflow/process.md`** — Step 5a updated: documents that `pipeline.py` now auto-calls `email_classifier.py`; lists trusted-domain override rules for AI fallback. New Step 5d: context offloading instruction using `context_offloader.offload_artifact('pipeline_output', ...)` referencing `tmp/pipeline_output.jsonl`.

### Tests
- Added 27 new tests in `tests/unit/test_catchup_quality_fixes.py`:
  - `TestEmailClassifier` (10 tests) — whitelist overrides, marketing sender/subject/header detection, category labels, `classify_records` batch method.
  - `TestHealthCheckWriter` (3 tests) — file creation from template, key upsert with existing file, stub detection.
  - `TestCalendarWriter` (5 tests) — calendar record detection, dedup key generation, dedup key scan, writer integration, non-calendar record passthrough.
  - `TestMigrateOI` (4 tests) — OI reference extraction, skip-file list, already-present deduplication, dry-run mode.
  - `TestPreflightBootstrapStub` (5 tests) — stub fingerprint detection, non-stub passthrough, check_state_templates stub reporting, auto-fix stub replacement, fix-count summary line format.
- Total: **1042 tests** (+27 from 1015).

### Specs
- **`specs/artha-prd.md`** → v7.0.4: version table row added.
- **`specs/artha-tech-spec.md`** → v3.9.4: version table row added; §8.12 "Catch-Up Quality Hardening" added (§8.12.1 Email Classifier, §8.12.2 Health Check Writer, §8.12.3 Calendar Writer, §8.12.4 OI Migration, §8.12.5 Preflight Fixes).
- **`specs/artha-ux-spec.md`** → v2.6: version table row added.

---

## [7.0.3] — 2026-03-15

### Added — Agentic Reloaded (specs/agentic-reloaded.md AR-1–AR-8)

**AR-1: Bounded Memory & Consolidation Discipline**
- **`scripts/fact_extractor.py`** — Added `MAX_MEMORY_CHARS = 3_000` and `MAX_FACTS_COUNT = 30` constants; `_load_harness_config()` reads capacity from `artha_config.yaml`; `_consolidate_facts()` enforces dual limits via TTL expiry followed by lowest-confidence eviction; `correction` and `preference` fact types are protected and never evicted.
- **`config/workflow/finalize.md`** — Step 11c expanded with Memory Capacity Check (AR-1) instructions.
- **`config/artha_config.yaml`** — Added `harness.agentic.memory_capacity` config block (`enabled`, `max_chars`, `max_facts`).

**AR-2: Self-Model (AI Metacognition)**
- **`state/templates/self_model.md`** — New template for agent self-model state file (domain confidence, effective strategies, blind spots, interaction patterns; capped at 1500 chars).
- **`config/Artha.core.md`** — Added AR-2 Self-Model protocol section (frozen-layer loading, session-boundary update discipline, 1500-char cap).
- **`config/artha_config.yaml`** — Added `harness.agentic.self_model` config block.

**AR-3: Pre-Eviction Memory Flush**
- **`scripts/session_summarizer.py`** — Added `should_flush_memory(context_text)` function (AR-3 pre-compression flush trigger at `threshold_pct/2`); added `pre_flush_facts_persisted: int = 0` field to both Pydantic and fallback `SessionSummary` classes; `create_session_summary()` accepts `pre_flush_facts_persisted` parameter.
- **`config/Artha.core.md`** — Added AR-3 Pre-Compression Memory Flush protocol section.
- **`config/artha_config.yaml`** — Added `harness.agentic.pre_eviction_flush` config block.

**AR-4: Cross-Session Search & Recall**
- **`scripts/session_search.py`** (new) — Grep-based full-text search over `briefings/` and `summaries/`; `SearchResult` dataclass; relevance scoring (`match_count / sqrt(line_count)`); PII-safe excerpts; `format_results_for_context()` renderer; CLI entry point.
- **`scripts/artha_context.py`** — Added `session_recall_available: bool = False` field to `ArthaContext`.
- **`config/workflow/reason.md`** — Added Pre-OODA Cross-Session Recall block with `session_search.py` instructions.
- **`config/artha_config.yaml`** — Added `harness.agentic.session_search` config block.

**AR-5: Procedural Memory**
- **`scripts/procedure_index.py`** (new) — Scan `state/learned_procedures/*.md` frontmatter; `ProcedureMatch` dataclass; `_decay_confidence()` with 90-day decay interval and 0.5 floor; `find_matching_procedures()` with min_confidence/min_relevance filters; `format_procedures_for_context()`; CLI entry point.
- **`state/learned_procedures/README.md`** (new) — Directory for agent-discovered reusable procedures (git-tracked, not backup-tracked).
- **`config/workflow/finalize.md`** — Step 11c Procedure Extraction (AR-5) instructions.
- **`config/workflow/reason.md`** — Pre-OODA Procedure Lookup (AR-5) instructions.
- **`config/artha_config.yaml`** — Added `harness.agentic.procedural_memory` config block.

**AR-6: Prompt Stability Architecture**
- **`scripts/generate_identity.py`** — Added `PROMPT STABILITY` frozen-layer comment to generated `config/Artha.md` header.
- **`config/Artha.core.md`** — Added AR-6 Prompt Stability Architecture section (frozen vs ephemeral layer classification, 4 usage rules).

**AR-7: Delegation Protocol**
- **`scripts/delegation.py`** (new) — `DelegationRequest` / `DelegationResult` dataclasses; `should_delegate()` (step threshold ≥5, parallel, isolated); `compose_handoff()` with context compression to ≤500 chars; `evaluate_for_procedure()` to surface delegation patterns as AR-5 candidates; `is_delegation_enabled()`; CLI smoke-test.
- **`config/Artha.core.md`** — Added Delegation Protocol (AR-7) section.
- **`config/artha_config.yaml`** — Added `harness.agentic.delegation` config block (`default_budget`, `max_budget`, `fallback_mode`).

**AR-8: Root-Cause Before Retry**
- **`config/Artha.core.md`** — Added Root-Cause Before Retry section (4-step diagnosis, anti-pattern contrast, AR-5 evaluation trigger).
- **`config/workflow/fetch.md`** — Added AR-8 Connector Failure Root-Cause Protocol (6-step classify/retry/log).

**Audit & Observability**
- **`scripts/audit_compliance.py`** — Added `_check_memory_capacity()` (weight 5, verifies `state/memory.md` ≤30 facts AND ≤3000 chars; advisory pass when file absent) and `_check_prompt_stability()` (weight 5, verifies `PROMPT STABILITY` marker in `config/Artha.md`; advisory pass when file absent); both checks added to `audit_latest_briefing()`.

### Tests
- Added 72 new tests across 4 test files:
  - `tests/unit/test_fact_extractor.py` — 8 AR-1 tests for `_consolidate_facts()` and `persist_facts()` capacity enforcement (protects `correction`/`preference` types, evicts expired + lowest-confidence).
  - `tests/unit/test_session_search.py` (new) — 14 tests for `session_search.py` (match, no-match, empty query, ranking, excerpts, multi-term, disabled flag, `format_results_for_context`).
  - `tests/unit/test_procedure_index.py` (new) — 19 tests for `procedure_index.py` (`list_procedures`, `_decay_confidence`, `find_matching_procedures`, `format_procedures_for_context`).
  - `tests/unit/test_delegation.py` (new) — 31 tests for `delegation.py` (`should_delegate`, `compose_handoff`, `DelegationRequest.to_prompt`, `evaluate_for_procedure`, `_compress_context`, `is_delegation_enabled`).
- Total: **1015 tests** (+72 from 943).

---

## [7.0.2] — 2026-03-15

### Fixed
- **`scripts/vault.py`** — `do_health()` now uses a 3-exit-code model: exit 0 (fully healthy), exit 1 (hard failure — age binary, key, or state dir), exit 2 (soft warnings only — orphaned `.bak` files, GFS never validated, key never exported). Previously `.bak` files set `ok=False` → exit 1, which blocked catch-up entirely.
- **`scripts/preflight.py`** — `check_vault_health()` handles vault exit 2 as a P1 advisory (non-blocking). Extracts the `⚠ .bak` warning line for accurate display; fix hint now correctly reads `python3 scripts/vault.py encrypt`.
- **`scripts/preflight.py`** — `check_vault_lock()` auto-clears stale locks unconditionally (no `--fix` required). Checks PID liveness in addition to age threshold. All error messages now show the actual lock file path instead of the generic `~/.artha-decrypted`.
- **`scripts/detect_environment.py`** — `detect_json()` emits compact single-line JSON when stdout is not a TTY (pipeline-safe), and indented JSON when printing to a terminal. Added `--pretty` CLI flag for explicit formatting.
- **`scripts/connectors/google_calendar.py`** — `_parse_event()` no longer includes raw attendee email addresses in JSONL output. Attendees are now `{name, self}` only; display name falls back to the username portion of the email address.
- **`config/Artha.core.md` + `config/Artha.md`** — All `python scripts/` references updated to `python3 scripts/` for macOS/Linux consistency; added prescriptive `alias python=python3` note in §1; vault §1 note updated for cross-platform; Step 0 clarified: stale locks and orphaned `.bak` files are crash-state that auto-resolves and never constitutes a blocking P0.

### Tests
- Added 7 targeted regression/contract tests: `test_vault_health_bak_files_exit_2_not_1`, `TestVaultHealthExitCodes` (3 tests covering exit 0/1/2 → P0/P1 mapping), `TestVaultLockAutoClean` (3 tests covering stale-by-age, stale-by-dead-PID, no-lock). Total: 943 tests (+7).

---

## [7.0.1] — 2026-03-15

### Fixed
- **`scripts/middleware/__init__.py`** — `_ComposedMiddleware.before_write` now accepts and forwards the `ctx` optional parameter to all child middlewares. Previously `ctx` was silently dropped, causing `TypeError` when callers passed `ctx=` to a composed chain.
- **`tests/unit/test_middleware.py`** — Updated all 5 test-mock `before_write` signatures to include `ctx=None`, matching the `StateMiddleware` Protocol contract.

---

## [7.0.0] — 2026-03-15

### Added — Agentic Intelligence (specs/agentic-improve.md Phases 1–5)

- **Phase 1 — OODA Reasoning Protocol** (`config/workflow/reason.md` Step 8, `scripts/audit_compliance.py`):
  - Step 8 rewritten as structured Boyd OODA loop: **8-O OBSERVE** → **8-Or ORIENT** → **8-D DECIDE** → **8-A ACT**.
  - OBSERVE reads `state/memory.md` correction/pattern/threshold facts from prior sessions.
  - ORIENT builds 8-domain cross-connection matrix + compound-signal detection.
  - DECIDE applies U×I×A scoring (1–3 scale) to rank every item; selects ONE THING.
  - ACT includes consequence forecasting (8-A-2), FNA pipeline (8-A-3), dashboard rebuild (8-A-4), PII stats (8-A-5).
  - `audit_compliance.py`: added `_check_ooda_protocol()` (weight=10); briefings must contain ≥3/4 OODA phase markers to pass. 6 new audit tests.

- **Phase 2 — Tiered Context Eviction** (`scripts/context_offloader.py`):
  - `EvictionTier(IntEnum)`: PINNED=0 (never evict), CRITICAL=1 (1.0×threshold), INTERMEDIATE=2 (1.0×threshold), EPHEMERAL=3 (0.4×threshold — aggressive).
  - `_ARTIFACT_TIERS` dict: 8 predefined artifact-to-tier mappings; unknown artifacts → INTERMEDIATE.
  - `offload_artifact()` gains `tier` param; feature-flagged via `harness.agentic.tiered_eviction.enabled`.
  - `.checkpoint.json` added to `OFFLOADED_FILES`.
  - `config/artha_config.yaml`: `harness.agentic:` namespace with 4 sub-flags (all `enabled: true`).
  - 9 new eviction tests.

- **Phase 3 — ArthaContext Typed Runtime Carrier** (`scripts/artha_context.py` — new):
  - `ContextPressure(str, Enum)`: GREEN / YELLOW / RED / CRITICAL.
  - `ConnectorStatus(BaseModel)` + `ArthaContext(BaseModel)`: 9 fields, `connectors_online`/`connectors_offline` properties, `health_summary()` method.
  - `build_context(command, artha_dir, env_manifest, preflight_results) → ArthaContext` builder.
  - `scripts/middleware/__init__.py`: `StateMiddleware.before_write()` gains `ctx: Any | None = None` (backward compatible).
  - Feature flag: `harness.agentic.context.enabled`. 25 new context + middleware tests.

- **Phase 4 — Implicit Step Checkpoints** (`scripts/checkpoint.py` — new):
  - `write_checkpoint()` / `read_checkpoint()` / `clear_checkpoint()` utilities.
  - Writes `tmp/.checkpoint.json` after Steps 4, 7, 8; clears in Step 18 cleanup.
  - 4-hour TTL: stale checkpoints (>4h old) ignored on resume.
  - `config/workflow/preflight.md`: Step 0a "Check for Resumable Session" added — auto-resumes in pipeline mode; interactive prompt otherwise.
  - `config/workflow/finalize.md` Step 18: `.checkpoint.json` added to `rm -f` cleanup + programmatic `clear_checkpoint()` call.
  - Feature flag: `harness.agentic.checkpoints.enabled`. 21 new checkpoint tests.

- **Phase 5 — Persistent Fact Extraction** (`scripts/fact_extractor.py` — new):
  - Extracts 5 fact types (correction, pattern, preference, threshold, schedule) from `tmp/session_history_*.md` summaries via regex signal detectors.
  - PII stripped (phone/email/SSN) before persistence; SHA-256 dedup (last 16 hex chars as ID).
  - Persists to `state/memory.md` v2.0 YAML frontmatter (`schema_version: '2.0'`, `facts: []`).
  - `config/workflow/finalize.md`: Step 11c "Persistent Fact Extraction" added (before Step 12).
  - `state/memory.md`: upgraded from placeholder to v2.0 schema.
  - `state/templates/memory.md`: new template file.
  - Feature flag: `harness.agentic.fact_extraction.enabled`. 38 new fact tests.

- **`config/Artha.core.md`**: `harness_metrics:` block extended with `agentic:` sub-section (eviction_tiers, session, checkpoints, facts, ooda telemetry).

### Tests
- +120 new tests; **936 total** (was 816, 5 skipped, 20 xfailed)
- New test files: `tests/unit/test_artha_context.py`, `tests/unit/test_checkpoint.py`, `tests/unit/test_fact_extractor.py`
- Updated test files: `tests/unit/test_context_offloader.py`, `tests/unit/test_audit_compliance.py`

### Specs
- PRD v6.1 → **v7.0** (F15.128–F15.132 added)
- Tech Spec v3.8 → **v3.9** (§8.10 Agentic Intelligence Modules added)
- UX Spec v2.2 → **v2.3** (Implements PRD v7.0, Tech Spec v3.9)

---

## [6.1.0] — 2026-03-15

### Fixed / Hardened
- **`scripts/skill_runner.py`** — Agentic CLI hardening (PRD F15.124):
  - Added `if __name__ == "__main__": main()` entrypoint (was inert when run as script from CLI agents like Gemini/Claude).
  - Restructured imports: stdlib → path setup → `reexec_in_venv()` → third-party (`yaml`). Eliminates `ImportError: PyYAML not installed` when run outside venv.
  - `importlib.util` moved to module scope (was inside `run_skill()`, causing potential `UnboundLocalError` on user-plugin path).

- **`scripts/pipeline.py`** — Venv bootstrap + unambiguous health output (PRD F15.125):
  - Added `reexec_in_venv()` call (same pattern as `skill_runner.py`). Prevents silent `ImportError` on bare Python.
  - `run_health_checks()` now always prints `[health] ✓ name` per connector and `All N connectors healthy.` summary — previously these were gated on `--verbose`, making automated health gates (preflight `check_script_health()`) see an empty stderr that fell back to a generic `OK ✓` note.

- **`scripts/skills/noaa_weather.py`** — Unconfigured coordinates guard (PRD F15.126):
  - `get_skill()` raises `ValueError` when `lat==lon==0.0` (placeholder defaults). Previously the skill silently issued a request to `api.weather.gov/points/0.0,0.0` which returns HTTP 404 from the mid-Atlantic Ocean, appearing as an API failure rather than a configuration problem.

- **`scripts/skills/uscis_status.py`** — Actionable 403 IP-block message (PRD F15.127):
  - HTTP 403 now returns `{"blocked": True, "error": "USCIS API is blocking requests from this IP address or network (common on cloud/VPN). Check status manually at https://egov.uscis.gov/casestatus/..."}`. Previous generic `{"error": "HTTP 403", "text": <large HTML>}` was not actionable.
  - Other non-200 responses truncate `response.text` to 500 chars (prevents log bloat).

- **`scripts/preflight.py`** — CI fix: cold-start profile check now uses `ARTHA_DIR` constant instead of `__file__`-relative path. `config/user_profile.yaml` is gitignored so CI never has it; the `__file__`-relative path bypassed the test mock, causing 3 `TestAdvisoryJsonOutput` tests to hit exit 3 (cold start) in CI.

### Tests
- **+12 new tests** (816 total, 5 skipped, 20 xfailed):
  - `tests/unit/test_pipeline.py`: `test_healthy_connector_always_printed_without_verbose`, `test_summary_line_always_printed_on_success`
  - `tests/unit/test_skill_runner.py`: `test_main_block_executes_without_error`, `test_importlib_util_accessible_at_module_scope`
  - `tests/unit/test_skills.py` *(new file)*: `TestNOAAUnconfiguredCoordinates` (2 tests), `TestUSCIS403ErrorMessage` (6 tests)

### Specs
- PRD v6.0 → **v6.1**, features F15.124–F15.127 added
- Tech Spec v3.7 → **v3.8**, §8.9 updated for skill_runner, noaa_weather, uscis_status changes
- UX Spec v2.1 → **v2.2**, agentic CLI hardening UX patterns added

### Deferred (spec entry required before implementation)
- `pipeline.py` exit 1 when `--source` filter matches no configured connector (changes observable exit contract)
- Vault lock PID-aware auto-clear (requires `vault.py` lock file format change)

---

## [6.0.0] — 2026-03-15

### Added
- **Cowork VM & Operational Hardening** (PRD v6.0, Tech Spec v3.7, specs/vm-hardening.md, F15.119–F15.123): addresses 15 failures found during March 15, 2026 Cowork VM diagnostic + 1 silent token expiry failure identified in post-mortem.

- **`scripts/detect_environment.py`** (Phase 1): multi-signal runtime environment detection. 7 probes: cowork marker (`/var/cowork` dir + `$COWORK_SESSION_ID` env var), filesystem writability, `age` installation, keyring functionality, TCP to Google/Microsoft/Apple. Returns `EnvironmentManifest` with `environment` (cowork_vm | local_mac | local_linux | local_windows | unknown), `capabilities` dict, `degradations` list. 5-minute TTL cache in `tmp/.env_manifest.json`. `--debug` flag for raw probe output.

- **`scripts/preflight.py` hardening** (Phase 2):
  - `--advisory` flag: P0 failures become `⚠️ [ADVISORY]` (non-blocking, exit always 0). For use only in sandboxed/VM environments. JSON output includes `advisory_mode: true` and `degradation_list`.
  - `check_profile_completeness()` (P1 check): fires when profile has ≤10 YAML keys; validates `family.primary_user.name`, emails, timezone, ≥1 enabled domain.
  - `check_msgraph_token()` rewrite — 3-layer fix: proactive refresh when near expiry; 60-day cliff warning reading `_last_refresh_success` timestamp; dual-failure message when token expired AND network blocked.
  - `check_state_templates()` — health-check.md only seeded when absent or `last_catch_up:` not present (never overwrites real data).

- **`scripts/setup_msgraph_oauth.py`**: writes `_last_refresh_success` ISO-8601 timestamp to token file after every successful silent refresh. Feeds the 60-day cliff warning in `check_msgraph_token()`.

- **`state/templates/health-check.md`**: new template with `schema_version: '1.1'`, `last_catch_up: never`, `catch_up_count: 0`. Auto-seeded by preflight on first run.

- **`config/Artha.core.md`**: "Read-Only Environment Protocol" block added — 8-step procedure for VM/sandboxed runs, token+network dual-failure subsection.

- **`scripts/generate_identity.py` compact mode** (Phase 3):
  - Default output: ~15KB `config/Artha.md` — extracts §1/§4/§5/§6/§7 from `Artha.core.md` + injects §R command router table (`_COMMAND_ROUTER_TABLE` constant) pointing to `config/workflow/*.md`.
  - `--no-compact` flag: legacy ~78KB full-core output for rollback.
  - `_extract_sections()` parser, `_COMMAND_ROUTER_TABLE` constant.

- **5 `config/workflow/*.md` files rewritten** (Phases 3+4): all stub content replaced with canonical step content + compliance gates:
  - `preflight.md`: Steps 0–2b, read-only exceptions per step, dual OAuth failure rule, environment detection Step 0a.
  - `fetch.md`: Steps 3–4e, mandatory Tier A state file loading checklist, MCP retry protocol (3 tries), Google Calendar IDs warning, offline/degraded mode detection.
  - `process.md`: Steps 5–7b, **CRITICAL email body mandate** (snippet-only PROHIBITED; `[snippet — verify]` tagging required), net-negative write guard, post-write verification steps.
  - `reason.md`: Steps 8–11, URGENCY×IMPACT×AGENCY scoring, consequence forecasting (IF YOU DON'T chain), FNA (Fastest Next Action) scoring, required cross-domain pairings.
  - `finalize.md`: Steps 12–19b, read-only skip list (Steps 7/7b/14–19), **mandatory Connector & Token Health table** (every briefing, even all-green, with Impact + Fix Command columns).
  - Each file: YAML frontmatter, `⛩️ PHASE GATE` prerequisite checklist, `✅ Phase Complete → Transition` footer.

- **`scripts/audit_compliance.py`** (Phase 5): post-catch-up compliance auditor.
  - 7 weighted checks: preflight_executed (20pt), connector_health_block_present (25pt), state_files_referenced (15pt), pii_footer_present (15pt), email_bodies_not_snippets (10pt), domain_sections_present (10pt), one_thing_present (5pt).
  - Degraded-mode auto-detection from `## Session Metadata` footer or `READ-ONLY MODE` header.
  - `--threshold N`: exit 1 if score below N (for CI/pipeline gates).
  - `--json`: machine-readable output; default when stdout is non-TTY.
  - Targets: local catch-up ≥80, VM degraded ≥60.

- **New tests** (106 total across 5 files):
  - `tests/unit/test_detect_environment.py` (29 tests)
  - `tests/unit/test_preflight_advisory.py` (17 tests)
  - `tests/unit/test_token_lifecycle.py` (11 tests)
  - `tests/unit/test_audit_compliance.py` (37 tests)
  - `tests/integration/test_vm_degraded.py` (12 tests — IT-4 through IT-8 from spec)

### Changed
- **`config/Artha.md`** now generated in compact mode by default (~15KB vs 78KB previously). Run `python scripts/generate_identity.py` to regenerate. Use `--no-compact` for legacy behavior.

### Total tests: 804 (698 baseline + 106 new), 0 failures

---

### Added (previously unreleased)
  - `scripts/skills/financial_resilience.py` — `FinancialResilienceSkill`: parses `state/finance.md` for monthly burn rate, emergency fund runway, and single-income stress scenario; registered in `config/skills.yaml` (cadence: weekly, requires_vault: true)
  - `config/domain_registry.yaml`: gig income routing keywords (Stripe, PayPal, Venmo, Upwork, Fiverr, Etsy, DoorDash, Uber earnings, 1099-K, 1099-NEC)
  - `prompts/finance.md`: "Gig & Platform Income Tracking (1099-K)" section with alert thresholds (🟡 >$5K, 🟠 >$20K, 🔴 Q4); "Financial Resilience" briefing section
  - `prompts/shopping.md`: "Purchase Interval Observation" section — recurring purchase pattern tracking
  - `prompts/social.md`: structured contact profiles (9-field template), pre-meeting context injection (📅 briefing block), passive fact extraction (date-annotated, high-confidence only)
  - `prompts/estate.md`: complete "Digital Estate Inventory" — 5 tables (legal documents, password/access recovery, beneficiary designations, auto-renewing services, emergency contacts); stale alerts at 6/12 months
  - `config/actions.yaml`: `cancel_subscription` and `dispute_charge` instruction-sheet actions
  - `prompts/digital.md`: "Subscription Action Proposals" section — price increase, trial conversion, and already-converted trial alert formats
  - `setup.ps1` — Windows PowerShell onboarding script: [1/5] prerequisites, [2/5] venv at `$HOME\.artha-venvs\.venv-win`, [3/5] pip install, [4/5] PII hook, [5/5] demo + wizard; `Write-Host -ForegroundColor` (no ANSI)
  - `artha.py --doctor` — `do_doctor()`: 11-point diagnostic (Python ≥3.11, venv active, core packages, age binary, age key in keyring, age_recipient, Gmail token, Outlook token, state dir file count, PII hook, last catch-up recency); `━━ ARTHA DOCTOR ━━` banner; exits 0 for warnings-only, 1 for failures
  - `scripts/connectors/apple_health.py` — local Apple Health export parser: ZIP and bare XML input, `iterparse + elem.clear()` streaming, 16 `HKQuantityTypeIdentifier` types, `since` relative/absolute date filter; `enabled: false` by default (opt-in)
  - `prompts/health.md`: "Longitudinal Lab Results" section — date-keyed table, flag codes (✅🟡🟠🔴), trend arrows (↑↓→), Apple Health mapping
  - **Bug fix**: `passport_expiry` and `subscription_monitor` added to `_ALLOWED_SKILLS` frozenset in `skill_runner.py` (both skills existed but were missing from allowlist)
  - `README.md`: updated Windows section to reference `setup.ps1`; `--doctor` in dev commands; Apple Health + financial_resilience + `--doctor` in "What You Get"
  - 56 new tests (`test_financial_resilience.py`: 21, `test_doctor.py`: 14, `test_apple_health.py`: 21); 541 total, all passing, PII scan clean

- **OOBE polish audit — first-impression redesign** (PRD v5.7, Tech Spec v3.4, F15.95–F15.99):
  - `setup.sh`: branded header `A R T H A  —  Personal Intelligence OS`, `[1/4]`–`[4/4]` step counters, `--disable-pip-version-check` (suppresses internal path leakage in pip upgrade notices)
  - `artha.py`: `_detect_ai_clis()` + `_print_ai_cli_status()` — detects `claude`, `gemini`, `code` (VS Code/Copilot) via `shutil.which`; shows tailored "Your next step:" after wizard and on welcome; shows install URLs if no CLI found
  - `artha.py do_setup()` completion: redesigned bordered success box with privacy assurance (`🔒 Your data stays on this machine. Artha never phones home.`); followed by AI CLI detection block
  - `scripts/demo_catchup.py`: ANSI colorized output (yellow `ACTION:`, green good-news bullets, red alert bullets, bold section headers) gated on `sys.stdout.isatty()`; removed dead "Fast way: bash setup.sh" footer; added privacy line
  - `README.md`: compressed 624 → 142 lines — hero tagline "Your life, organized by AI.", quick start (3 commands), "What You Get" bullet list, docs table; detailed content removed to `docs/`
  - `docs/backup.md`: new file — full Backup & Restore reference (GFS tiers, CLI commands, cold-start rebuild, key backup, validation checks) moved from README
  - `specs/README.md`: new file — disclaimer that all personal names/data in specs/ are fictional examples (Patel family), not real individuals
  - `Makefile`: `start` target added (`@bash setup.sh`); added to `.PHONY`
  - Fixed duplicate `if __name__ == "__main__"` block in `artha.py`
  - 485 tests passing, PII scan clean

- **Interactive setup wizard + first-run friction fixes** (PRD v5.6, Tech Spec v3.3, F15.89–F15.94):
  - `config/user_profile.starter.yaml` — minimal 45-line first-run template (blank name/email forces real data entry; replaces 234-line example as default for new users)
  - `artha.py do_setup()` — interactive wizard collecting name, email, timezone (ET/PT/IST shortcuts), household type, children; writes clean YAML, auto-runs `generate_identity.py`
  - `artha.py --no-wizard` flag — copies starter profile for manual editing
  - Configured path now calls `do_welcome()` only — removed `do_preflight()` auto-call that caused ✅→⛔ cognitive whiplash
  - `generate_identity._collect_warnings()` — non-blocking advisory for placeholder child names and cities
  - `generate_identity._print_validate_summary()` — identity preview on `--validate` success
  - `preflight.py --first-run` flag — Setup Checklist view with `○ not yet configured` for expected OAuth items; exit 0 when only setup steps remain
  - `setup.sh` wizard integration: removed 234-line profile copy; prompts "Run the 2-minute setup wizard now?"; non-interactive CI path silently copies starter profile
  - 11 new tests (`TestCollectWarnings`, `TestPrintValidateSummary`); 485 total, all passing; PII scan clean

- **10-layer defense-in-depth for state data protection** (PRD v5.5, Tech Spec v3.2, F15.88):
  - Advisory file lock (`flock`/`msvcrt`) prevents concurrent encrypt/decrypt
  - Cloud sync fence detects OneDrive/Dropbox/iCloud in flight, waits for quiescence
  - Post-encrypt verification: `.age` output ≥ plaintext size, aborts on truncation
  - Deferred plaintext deletion: `.md` files only removed after all domains succeed
  - Encrypt-failure lockdown: `chmod 000` on remaining plaintext to block cloud sync
  - Auto-lock mtime guard: skips encryption if any `.md` modified within 60 seconds
  - Net-negative override: `ARTHA_FORCE_SHRINK` env var with `.pre-shrink` pin
  - GFS prune protection: sole-carrier ZIPs pinned from deletion
  - Confirm gate: `restore`/`install` require `--confirm` or `--dry-run`
  - Pre-restore safety backup of live files to `backups/pre-restore/`
  - Key health monitoring: validates `AGE-SECRET-KEY-` format and export status
  - `backup.py`: domain checksums stored in manifest for prune-safe rotation
  - 66 new tests (26 vault, 7 backup defense tests + updated existing);
    total 501 tests, all passing

- **Novice UX deep audit round 2 — 15 issues resolved across 3 commits** (PRD v5.4, Tech Spec v3.1, F15.78–F15.87):
  - Quick Start time estimate updated to `~30 minutes` with per-step breakdown
  - Demo mode callout added immediately after `pip install` (before Step 2)
  - `generate_identity._validate()` now rejects example placeholder values
    (`"Alex Smith"`, `"alex.smith@gmail.com"`) with actionable error messages
  - `user_profile.example.yaml` gains explicit `household:` section
    (type, tenure, adults) matching domain registry filter contract
  - `vault.py` now accepts `--help`/`-h`/`help` → prints usage, exits 0;
    unknown command now writes to stderr
  - Step 6 preflight note replaced with expected-results table (4 checks,
    fresh-install state, when-it-resolves)
  - "Which AI CLI?" callout updated with per-tool free/paid tier details
  - Google "app isn't verified" callout expanded with full safety explanation
  - `docs/security.md` §6 Mosaic PII Risk added (cultural_context + immigration
    = demographic fingerprint; guidance for forkers)
  - 17 new tests (2 placeholder guard, 5 vault help/usage, 10 across round-2
    batches); total 435 passed

### Changed
- venv creation wrapped in OS `<details>` block: `python3` (macOS/Linux) and
  `python` (Windows) — fixes hard failure on Windows PATH
- `git config core.hooksPath .githooks` moved from main bash block to
  "Contributors/forkers only" blockquote
- Preflight `NO-GO` caveat callout moved to appear *before* the command
- `_print_usage()` extracted in `vault.py` as shared helper; no-args exits 1,
  help exits 0, unknown command to stderr

---

  - Multi-LLM Q&A via Telegram: free-form questions routed through
    Claude → Gemini → Copilot failover chain with workspace context
  - Ensemble mode (`aa` prefix): all CLIs called in parallel, consolidated
    via Claude Haiku
  - HCI command redesign: 45+ aliases, single-letter shortcuts
    (`s/a/t/q/d/g/?`), slash optional, hyphens optional
  - Write commands: `items add` (with priority/domain/deadline parsing),
    `done OI-NNN` (marks items resolved with audit logging)
  - `/goals` command (aliases: `g`, `goal`, `goals`)
  - `/diff` command: state file changes since last catch-up (supports
    `diff 7d`, `diff 24h`)
  - Domain listing: `d` without args shows categorised domain list
    (direct-read vs. encrypted/AI-routed)
  - Encrypted domain routing: `d finance` routes through LLM with vault
    decrypt/relock cycle
  - Thinking ack: "💭 Thinking…" sent immediately for long-running commands,
    auto-deleted after response arrives
  - `send_message_get_id()` and `delete_message()` methods in Telegram adapter
  - Structured LLM output: numbered lists, Unicode bullets, one-line direct
    answers, no Markdown
  - CLI workspace context: all LLM CLIs invoked from Artha workspace dir
    (access to CLAUDE.md, skills, vault)
  - Vault auto-relock: `_vault_relock_if_needed()` in finally block after
    every CLI call
  - Content quality pipeline: `_extract_section_summaries()`,
    `_filter_noise_bullets()`, `_clean_for_telegram()`
  - CLI priority order benchmarked: Claude (~16.5s) → Gemini (~26.1s) →
    Copilot (~39.1s) with model args
- `scripts/demo_catchup.py` — Tier 1 demo mode using fictional Patel family
  fixtures; no accounts required ([standardization.md §8])
- `scripts/local_mail_bridge.py` — zero-auth local mail reader for Apple Mail
  (`.emlx`) and UNIX mbox; no OAuth required
- `docs/` directory with six reference documents: `quickstart.md`,
  `domains.md`, `skills.md`, `security.md`, `supported-clis.md`,
  `troubleshooting.md`
- `/bootstrap quick`, `/bootstrap validate`, `/bootstrap integration` modes
  documented in `config/Artha.core.md`
- `prompts/README.md` — prompt file contract and schema documentation
- `scripts/skills/README.md` — skill development reference

### Changed
- All scripts now use `_bootstrap.py` instead of ~30-line inline venv boilerplate
  (`setup_google_oauth.py`, `preflight.py`, and 12 others)
- `gcal_fetch.py` — `--calendars` default now reads from
  `user_profile.yaml:integrations.google_calendar.calendar_ids` instead of
  hardcoded personal calendar IDs
- `scripts/skills/noaa_weather.py` — fallback coordinates changed from
  hardcoded personal location to neutral `0.0, 0.0`
- `scripts/pii_guard.sh` and `scripts/safe_cli.sh` — deprecation banners added;
  Python equivalents are now the canonical versions

---

## [5.0.0] — 2026-03-11

### Summary
First public open-source release. Full rewrite for privacy-first, generic
deployment — no personal PII in any tracked file.

### Added
- `config/user_profile.yaml` and `config/user_profile.example.yaml` — all
  personal configuration externalized from code and prompts
- `scripts/profile_loader.py` — dot-notation config accessor with `lru_cache`
- `scripts/_bootstrap.py` — centralized venv re-exec helper
  (`reexec_in_venv(mode)`) replacing ~30 lines of copy-paste boilerplate
- `scripts/generate_identity.py` — generates `config/Artha.identity.md` from
  `user_profile.yaml`
- `scripts/pii_guard.py` — Layer 1 pre-write PII filter (Python rewrite of
  `pii_guard.sh`)
- `scripts/safe_cli.py` — Python rewrite of `safe_cli.sh`
- `config/Artha.core.md` — genericized system prompt (zero PII)
- `config/Artha.identity.md` — generated per-user identity context
- `config/routing.example.yaml` — example email routing rules (no PII)
- `config/settings.example.md` — example settings file (no PII)
- `config/user_profile.example.yaml` — example profile (fictional Patel family)
- All 17 domain prompt files genericized (zero PII grep hits)
- 128 tests passing (`tests/unit/`, `tests/integration/`)

### Changed
- System prompt split into `Artha.core.md` (generic) + `Artha.identity.md`
  (user-generated); `Artha.md` now imports both
- All hardcoded email addresses removed from scripts and prompts
- All hardcoded family names, coordinates, and account IDs removed

### Security
- PII defense documented in `docs/security.md`
- Three-layer defense-in-depth: regex filter → semantic verification → at-rest encryption

---

## [4.x] — 2025 (pre-open-source, personal use only)

v4.x was a functional but PII-embedded personal deployment. Not released publicly.
Migration guide: see `scripts/migrate.py`.

---

## Spec Version History

Detailed per-version changes previously maintained inline in spec headers.
Relocated here during hardening v5.1 to reduce spec context overhead.

### PRD Versions

- **v4.1** (2026-03): WorkIQ Work Calendar Integration — F8.8–F8.13, employment domain activation
- **v4.0** (2026-03): Intelligence Amplification — 29 enhancements (goal sprints, Canvas LMS, Apple Health, `/diff`, coaching engine)
- **v3.9** (2026-02): Supercharge — data integrity guard, dashboard, coaching, bootstrap, pattern detection, consequence forecasting
- **v3.8** (2026-02): Phase 2A — relationship intelligence, tiered context, decision graphs, digest mode, accuracy pulse
- **v3.7** (2026-01): Operational robustness — pre-flight gate, open items, To Do sync, email coverage matrix
- **v3.6** (2026-01): Critical assessment hardening — 18 items from independent review
- **v3.5** (2025-12): Multi-LLM orchestration, action execution framework
- **v3.4** (2025-12): Governance & evolution framework
- **v3.3** (2025-11): Pre-flight PII guardrails, Claude Code capabilities
- **v3.2** (2025-11): OneDrive sync layer for cross-device state
- **v3.1** (2025-10): Data sensitivity classification, document repository model
- **v3.0** (2025-10): Architectural pivot from push daemon to pull model
- **v2.x** (2025): Household coverage audit, expert reviews, daemon architecture

### Tech Spec Versions

- **v2.2** (2026-03): WorkIQ Calendar MCP, work calendar state schema, parallel fetch
- **v2.1** (2026-03): Intelligence amplification — Canvas LMS, `/diff`, monthly retrospective, Apple Health
- **v2.0** (2026-02): Supercharge — data integrity, bootstrap workflow, coaching, email volume scaling
- **v1.9** (2026-02): Phase 2A — relationship graph, decisions, scenarios, tiered context
- **v1.8** (2026-01): MS Graph direct integration replacing hub-and-spoke forwarding
- **v1.7** (2026-01): Pre-flight gate, open items, To Do sync, email coverage
- **v1.6** (2025-12): Critical assessment hardening, safe_cli, contacts encryption
- **v1.5** (2025-12): Multi-LLM orchestration, action framework
- **v1.4** (2025-11): Governance framework
- **v1.3** (2025-11): PII guardrails, Claude Code capabilities
- **v1.2** (2025-10): OneDrive sync layer

### UX Spec Versions

- **v1.5** (2026-03): WorkIQ calendar UX, merged view, Teams join actions
- **v1.4** (2026-03): Intelligence amplification UX — `/diff`, weekend planner, coaching display
- **v1.3** (2026-02): Supercharge UX — flash briefing, bootstrap interview, dashboard, scorecard
- **v1.2** (2026-02): Phase 2A — digest mode, relationship pulse, leading indicators
- **v1.1** (2026-01): Pre-flight gate errors, `/items` command
