# re-artha implementation status — 2026-11-04

## Full isolated aggregate — 2026-11-04

The current 1,102-file public source workspace completed
`tools/run_tests_isolated.py -q` on Windows Python 3.12.10: **10,423 passed,
183 skipped, 20 xfailed, nine warnings**, exit 0 in 6,091.14 seconds
(1:41:31). The process-local PATH included the previously verified GNU Make
4.4.1 and Git for Windows POSIX utilities; `PYTHONUTF8=1` kept subprocess
decoding deterministic. All public development canaries ran; no test or gate
was skipped or weakened to obtain this result. This is not the private Work
runtime selection or live/provider acceptance.

After this aggregate, the Windows authority-retirement module was rerun with
all four abrupt process-death cases enabled: **38 passed**. The authority
transfer protocol module was then rerun with its five abrupt process-death
cases enabled: **86 passed**. The Windows quarantine restore module was then
rerun with all four abrupt process-death cases enabled: **59 passed**. The
Windows checkpoint capture module also passed **69 tests**, including the
separate-process input-change race; Windows checkpoint restore integrity
passed **48 tests, two platform-specific skips**, including native sharing
denial and restore-directory custody checks. A combined Windows recovery and
authority cohort covering six modules then passed **382 tests, 70
platform-specific skips**. The Windows quarantine review and recovery modules
then passed **230 tests, five platform-specific skips**, and **128 tests**,
respectively. The later full aggregate result is recorded in the current
validation continuation below.

The preceding current-source attempt had **10,399 passed, 185 skipped, 20
xfailed, 22 failed**. One failure was a spend-seal fixture that omitted the
active shadow seal; the fixture was corrected and the checkpoint/seal
selection passed **300 tests**. The other 21 failures were development
canaries lacking GNU Make on the process PATH. Rerunning the aggregate with the
verified session-local GNU Make resolved those environment failures.

Tracked working-tree, staged, and unpushed-history privacy scans passed with
checksum-verified Gitleaks 8.30.1 on this checkout before the latest uncommitted
changes; those results do not cover the current working tree.

## Current Windows validation continuation — 2026-11-04

A later full isolated aggregate on the current source ran **10,462 passed,
177 skipped, 20 xfailed, 22 failed** in 5,882.15 seconds. The failures were
one Windows test assertion that depended on pytest storing a restored bound
method in the instance dictionary, missing GNU Make/POSIX utilities for the
development canaries, and missing local `mypy`/`msal` test dependencies. The
test now checks the restored bound method through its public binding, `mypy`
is declared in the `dev` extra, and the missing tools/dependency were installed
in the local environment. The isolated affected cohort then passed **96 tests,
six skipped**. The final full isolated aggregate on this current source state
completed with **10,484 passed, 177 skipped, 20 xfailed, eight warnings**,
exit 0 in 5,691.40 seconds (1:34:51) on Windows Python 3.13.17. It ran the
default public `tests/` selection; the reported skips and expected failures
remain visible.

The separate CalDAV cursor/lifecycle selection passed **137 tests** after its
declared Apple connector dependencies were installed into the local
environment. Ruff, the generated capability-register check and `git diff
--check` pass. The current working-tree privacy scan subsequently passed with
checksum-verified Gitleaks 8.30.1, run once without concurrent Gitleaks
scans. Earlier concurrent/retried invocations returned native scanner errors
for some files; direct one-file reruns were clean. No scanner errors or
findings remained in the successful complete working-tree scan.

A combined follow-up cohort reported one failure in the Windows
storage-adoption process-death test (`before-legacy`): the post-termination
preview reported `private` rather than `legacy`. The exact case then passed
alone, and the complete isolated storage-adoption module passed **62 tests**.
The failure did not recur in the complete aggregate above; no code change was
made for this non-reproducing result.

An earlier frozen baseline before the subsequent November 3 repairs recorded
**10,377 passed, 185 skipped, 20 xfailed**, nine warnings, exit 0 in
6,934.48 seconds. It is not a full-suite validation of the current HEAD.

Operational acceptance remains incomplete. The owner-approved GFS version-3
contract is implemented in the current worktree. Its focused isolated
backup/vault suite passed 217 tests with one skip; changed executable-line
coverage is 562/587 (95.7%), and a synthetic native-age/NTFS-stream round-trip
matched file and stream bytes. The 10,423-test aggregate above predates these
GFS and ADO changes. The local ADO ownership-query repair passed the isolated
159-test connector suite and Ruff, but has not been installed in Artha runtime;
Work refresh remains stale. The previously passing local working-tree privacy gate scanned repository
files, not private runtime state, and predates the latest uncommitted changes.
No production restore, off-host recovery, owner UAT or release/cutover was
performed. The WorkIQ display-only contract still requires owner approval;
live Connector prerequisites, provider/UAT evidence, independent recovery
acceptance and release/cutover approval remain outstanding. Passing tests do
not clear those gates.

## Checkpoint dependency inventory failure reporting — 2026-11-03

Bundle and acknowledgement discovery now blocks checkpoint capture when a scan
directory is unavailable or not a directory, or when a matching artifact is
non-regular, unreadable, malformed, or missing a valid producer identifier.
The recursive scan and subsequent artifact listing use error-reporting directory
operations because `Path.rglob()` skipped a simulated nested access denial,
the second path-glob listing skipped a simulated permission change, and
`Path.is_dir()` treated root metadata denial as absence. Filename matching
remains case-insensitive for Windows compatibility. A missing optional machine
runtime root is allowed, but metadata failures on existing roots block inventory.
Synthetic regressions cover
malformed JSON, wrong JSON shape, missing/invalid producer identifiers, denied
root/nested/repeated reads, non-regular artifacts, invalid scan paths and
uppercase artifact names. The isolated checkpoint CLI suite passes all 284
tests, including canonical registry selection with an absent optional runtime
root. This is a source-level inventory repair only; no
checkpoint, credential or production State File was read or written, and it
does not close native recovery acceptance.

## Registered spend-seal absence detection — 2026-11-03

Personal checkpoint inventory now blocks when the recovery registry has an active
spend-seal entry but the corresponding seal file or the spend-seal directory is
missing. Pending custody and unreadable or invalid registry metadata continue to
block rather than being treated as an absent seal. A synthetic regression enrolls
an active authoritative seal, removes its file and verifies that inventory refuses
to proceed. The isolated checkpoint CLI suite passes all 284 tests.

## Preflight dependency bootstrap repair — 2026-11-03

`_bootstrap.py` preflight mode now exits nonzero when pip installation fails,
`scripts/requirements.txt` is missing, or required modules remain unavailable
after a successful installer exit. This prevents preflight callers from
continuing with an unverified environment. Five synthetic dependency-bootstrap
cases cover the ready, repair-success, installer-failure, missing-file and
post-install-missing paths. The isolated bootstrap test file passes 19 tests;
overall `_bootstrap.py` module coverage in that selection is 68%, not a
full-module coverage pass. No dependencies were installed and no provider or
State File was accessed for these tests.

## Memory bootstrap reporting repair — 2026-11-03

Historical briefing extraction failures now make `scripts/bootstrap_memory.py`
exit nonzero, including dry runs, while retaining the current behavior of
persisting facts extracted successfully from other briefings. The command states
that partial facts may have been persisted. Procedure-seed parsing and write
failures now propagate instead of being reported as zero procedures seeded.
Seed-derived domain and trigger fields are restricted to safe filename
components, and resolved procedure paths are checked against the expected
Artha-local directory to prevent symlink-based escapes. The isolated synthetic
`tests/unit/test_memory_policy.py` selection passes all 27 tests with 99%
combined line/branch coverage for the bootstrap module. This focused result
does not include these changes in the earlier full-suite aggregate. The
generated implementation fingerprint check passes. No production State Files
or briefing data were used.

## Post-catch-up self-model rebuild error reporting — 2026-11-03

`post_catchup_memory.py --rebuild-self-model` previously converted writer
exceptions into `False`, so the CLI printed "unchanged" and exited 0 after a
failed rebuild. Writer exceptions now reach the command boundary, which reports
the failure and exits 2; updated and genuinely unchanged outcomes still exit 0.
Synthetic CLI regression coverage verifies all three outcomes. No production
State Files were read or changed.

## Evaluation-alert history preservation — 2026-11-03

The post-catch-up and retrospective writers previously interpreted malformed
or wrong-shaped `eval_alerts.yaml` as empty history and could replace it while
publishing anomalies. Both now validate the shared mapping/list schema,
preserve invalid history and report publication failures. Preflight now fails
at P1 on missing or malformed alert lists rather than reporting a healthy
check. The isolated memory/preflight/retrospective selection passes all 43
tests, including synthetic malformed-YAML and invalid-shape cases across both
writers and preflight; all test data is isolated.

The follow-up Windows static-gate run found that `tools/import_check.py` raised
`UnicodeEncodeError` when its checkmark was written to CP1252 output. A direct
subprocess regression failed before repair; changing only the success text to
ASCII makes both legacy-encoding and normal clean-tree cases pass. The updated
isolated `tests/dev` selection passes **222 tests, six skipped**. Repository
syntax, Ruff, import, state-write ratchet, strict typing (70 core files and
three credential files) and example-schema gates pass. The 10,377-test
aggregate above predates this tooling-only change.

## Live readiness continuation — 2026-11-03

Installed preflight repairs recognize encrypted State Files, expand registry globs,
retain registry-read errors, scope skill advisories to the host without changing the
global inventory, and distinguish SLO evaluation/no-sample failures from healthy budgets.
The installed strict preflight exits 0 with every P0 check passing. Missing SLO
observations remain a visible P2 advisory; no measurements were invented.
Public code/tests and their generated implementation fingerprint are committed
locally as `91c1751`; the actual pre-commit privacy hook passed. Nothing was pushed.

The preflight/skill compatibility selection passed 140 tests in the isolated runner.
The final state-check selection passed 30 tests, including the additional registry I/O
case. An earlier in-place compatibility invocation passed its assertions but failed the
hermeticity gate after a legacy skill entrypoint test created nine files in the worktree.
Only those nine newly generated paths were removed; the production runtime was not
touched by that test. The passing rerun used the existing isolated runner.
All 28 changed executable lines across the two preflight implementations are covered
by the final state-check selection (100%); this is not full-module coverage.

Two more installed source files were fingerprinted, with all original NTFS streams
preserved. Before Work publication, a same-host encrypted snapshot captured all 185
state/config files and 16 named streams. Decrypting it in memory reproduced the exact
manifest, payloads and streams, with no plaintext or private-key files written. This
is a bounded operational recovery copy, not GFS cadence, an off-host backup or a core
checkpoint/recovery acceptance result.

The restored v1.2 agent reflection was executed and published in its required order.
All six source slots were accounted for: Q1/Q2 succeeded, Q3 used the broader live
ownership/RAMPP1 queries, Q4 retained the indexed incident's evidence date, Q5 explicitly
remained unavailable, and Q6 excluded protected contents. One attributable output and
one Open Item were added; no old Open Item was closed and no meeting-routing decision
or people card was persisted without confirmation. The blocking audit and all seven
final file/stream fingerprints passed. Actual counts are 375 accomplishments and 197
WOI rows. The pre-publication count was 196, not 186: the latter omitted ten named
legacy IDs. The source window ends at 2026-11-03 00:54:48 PST; daily/weekly closes
advance to that cutoff, while monthly/quarterly closes remain unchanged.

The missing Chromium dependency was restored. A real headless Chromium process
rendered a 16,958-byte synthetic PDF in memory; no production document was used.
The production career workflow and owner UAT were not exercised.

The broader installed `pipeline.py --health` probe exits 1: eight Connector checks
pass, while the enabled legacy Outlook CLI bridge has no executable, Home Assistant
has no stored credential, and SharePoint references an unallowlisted local handler.
Several MCP-only entries have no local health handler and are explicitly skipped.
This result is not overridden by the P0-only preflight pass; all-enabled-Connector
readiness remains incomplete.
The SharePoint module does exist, but its dedicated KB-sync signature/output differs
from the generic Pipeline fetch contract. Adding it to the allowlist alone is not
a correct repair and was not done.
Neither the separately authorized Work Graph credential nor the personal Graph
credential is enrolled. The dedicated SharePoint synchronizer already uses the
separate Work credential; the generic runtime entry still specifies the personal
token selector. No personal fallback, scope grant or external document fetch was
performed.

The Outlook bridge also suppressed those actual missing-CLI failures as empty
success. Eleven failing isolated regressions reproduced its missing/process/response
failures and the resulting false complete-coverage outcome. The repaired bridge
now propagates them; valid empty listings and non-Windows skipping remain unchanged.
The isolated Work Connector/Pipeline selection passes 140 tests, with 100% line and
branch coverage of the Outlook module (the 95% module gate passes).
Its public repair/tests are committed locally as `3523074`; the actual privacy
hook passed. Nothing was pushed.
The installed repair is the tenth unique runtime source repair. Its exact
predecessor and stream inventory were verified and retained. An installed
`fetch_single` check now returns the real missing-CLI error with no records rather
than empty success. This repairs reporting, not the absent Outlook prerequisite;
Classic Outlook/COM and a successful production calendar fetch remain unverified.

The dedicated SharePoint synchronizer had a separate false-completion defect:
missing/invalid configuration and missing Work credentials could leave an empty
error list, while cached Work tokens bypassed the existing freshness/refresh helper.
Eleven initially failing isolated regressions reproduced these paths. The repair
retains the Work-only credential boundary, reports prerequisite errors and exits
nonzero with a `failed` label. Explicitly disabled/absent entries remain no-ops.
The SharePoint/credential compatibility selection passes 127 tests with two existing
skips. All 23 changed executable sync lines are covered (100%); full-module combined
coverage is 81%, not 95%. The installed eleventh unique source repair preserves its
verified predecessor and stream inventory. Its actual CLI now exits 1 with zero
documents fetched/ingested and the real Work-credential error. No document fetch,
new scope, enrollment, generic-Pipeline routing or KB publication was performed.
Code/tests and the generated implementation fingerprint are committed locally as
`f5ddf2d`; its actual staged privacy hook passed.

A combined isolated preflight/Work Connector/Pipeline/SharePoint/credential cohort
passes 297 tests with two existing skips and three warnings. This is a targeted
compatibility result, not a replacement for the older full public aggregate.
An independent final verification confirms all eleven installed source files match
both current working code and their latest deployment receipts, including every
original named stream.

The full working-file and unpushed-history privacy scans completed successfully.
They were not a frozen source cohort; exact repair commits are separately checked
by the actual staged privacy hook. The owner was unavailable for the Home Assistant
scope question, so no enabled Connector was disabled and no consent was inferred.

Recovery continuation removes the legacy file-decryption helper's temporary private-key
file. The identity now reaches native age through stdin with a 60-second timeout; its
caller-designated plaintext output API is unchanged. Backup validation no longer advances
global success freshness after a partial failure or a successful domain-only check.
Partial failures record their count and exit nonzero; full success still clears old errors.
The older partial-success compatibility test explicitly expected the false health refresh
and was updated to assert the corrected fail-closed contract.
The isolated recovery selection passes 179 tests with one existing skip, including a real
native-age synthetic binary round-trip. All 11 changed executable recovery lines are
covered. Code/tests are committed locally as `8e78e1b`; the actual privacy hook passed.
Both installed files match source and exact predecessor/stream-preserving deployment
receipts; backup is the twelfth unique repaired runtime source file.

A separate native Windows restore exercise first proved binary/default data and empty
and nonempty synthetic stream preservation, tamper rejection and overwrite refusal.
It then restored all 11 encrypted State Files from the earlier protected snapshot into
a new private session folder, compared ciphertext/stream bytes against the captured
generation and successfully decrypted each recovered file in memory. Those encrypted
files have no named streams; the synthetic stream cases do not establish production
stream restore acceptance. No production files were modified, and no plaintext State
Files or private-key files were written. This exercise covers only the same-host encrypted
state subset, not the snapshot's plaintext Work/config files, off-host recovery or a
cold-start without the current label/stream seed.

Legacy GFS remains unsuitable for a production acceptance run: its ZIP format does not
preserve named streams, local SQLite copies are not online snapshots, and its validator
still stages plaintext and applies markdown checks to some non-markdown types.
These limitations were not cleared by the key-file or validation-freshness repairs.

The continued validator walkthrough found additional false-success paths: no backups,
empty/incomplete inventories, absent integrity checksums and inconsistent ZIP membership
could return success or refresh full-validation health. Nine initially failing regressions
reproduced them; additional cases cover duplicate entries, malformed JSON/metadata, a
boolean catalog count and corrupt ZIP data. The installed repair requires complete,
nonempty declared coverage and both checksums before any successful outcome.
Code/tests are committed locally as `c0f11e7`; its actual staged privacy hook passed.

The same trace exposed a coupled catalog hazard: the existing loader treated corrupt or
unreadable catalogs as empty history. Recording a validation failure could then replace
the corrupt catalog with an empty one. Seven failing regressions reproduced this and
silent atomic-publication failure. The loader now distinguishes missing from invalid,
leaves invalid catalog bytes unchanged and propagates publication failures. A real
invalid-UTF-8 file is also covered. This changes every backup catalog reader, including
health/prune/snapshot/restore paths: invalid history is an explicit failure, not zero
backups or reconstructed success. Code/tests are committed locally as `08c136e`;
the actual staged privacy hook passed.

The final combined isolated recovery/preflight/skill/Connector selection passes
536 tests with two existing skips and three warnings. All 32 changed executable
completeness/catalog lines are covered (100%); this is not full-module coverage.
Both later backup deployments retain their exact predecessors and original streams.
The started new full public aggregate was deliberately stopped before acceptance when
the additional validator defects were found. It is recorded as cancelled, not passing.
No passing result is attributed to that cancelled attempt.

The remaining archive-format repair is specified in the local draft
`specs/operational-recovery.md`. It proposes version-3 ZIP payload/stream descriptors,
version-2 read compatibility, consistent SQLite images and in-memory typed validation.
The proposal remains unapproved and unimplemented; no GFS format, schedule, off-host
destination or production restore was changed.

The owner was unavailable for that archive-format decision. The draft was preserved
as a review-ready document attachment, not treated as approved; routine source auditing
continued without implementing version 3.

The WorkIQ bridge had another false-empty boundary: failed brief/rich structured
queries returned normally, failed rich narratives and channel queries could disappear
from coverage, and partial channel results could be cached as complete. Twenty-seven
failing synthetic regressions reproduced these paths, including the actual generic
fetch wrapper returning zero records with no error. The repaired bridge propagates
structured failures, reports failed/truncated scopes to an active collector and yields
usable partial records with processing gaps without caching them. Missing people names
fail before cache access. Unusable nonempty calendar responses after retry fail rather
than populating an empty cache. Existing successful response fields are preserved.

The final combined isolated operational cohort now passes 570 tests with two existing
skips and three warnings. All 36 changed executable WorkIQ lines are covered (100%);
this is not full-module coverage or a live M365/provider outcome. Code/tests and the
generated source fingerprint are committed locally as `3cccb60`; the actual privacy
hook passed. Its installed predecessor/stream receipt is verified, making thirteen
unique runtime source repairs. The producer-content hash naturally invalidates older
WorkIQ cache entries, so subsequent requests refresh rather than replay the old producer.
No live WorkIQ query, new scope, credential enrollment, reflection publication or
core activation was performed for this repair.

Legacy WorkIQ record identities/timestamps and generic-Pipeline compatibility remain
unsettled; no upstream identifiers or dates were invented. Active-collector scope reports
and raw partial-record metadata are not proof that every downstream legacy presentation
consumes those gaps. The canonical's obsolete synced-workspace Pipeline output example
was also corrected to match the enforced machine-local path and orchestrator reader.

The subsequent frozen full public-source aggregate at
`d717d10536846bd3e8df0b8710f2a0317b5f442e` completed successfully on
2026-11-03 at 12:30:05 UTC: **10,185 passed, 185 skipped, 20 xfailed**, nine
warnings, 7,114.59 seconds. Both pytest and the core coverage checker exited 0.
Core coverage is 93.82% line, 89.34% branch and 92.61% combined, exceeding the
existing 85% floors but not establishing a 95% full-module claim. The checkout
remained clean at the frozen revision through completion. Evidence is retained in
the session's `ops-current-operational-aggregate` receipt, log, JUnit XML and coverage
XML. This run includes the public recovery and WorkIQ source repairs above; it excludes
private Work producers and does not establish live provider, GFS/off-host recovery,
owner UAT, release or core-cutover acceptance.

The subsequent Work Connector caller trace reproduced six more failures: standalone
`fetch_single()` hid schema rejections and failed/truncated scopes, while a configured
or caller-supplied `source_tag` collided with the dispatcher's keyword before the handler
ran. Local repair `e8fc4b6` collects per-call coverage without persisting shared history,
returns explicit degraded errors with usable partial lines retained, and forwards the
tag once with existing caller-over-configuration precedence. Handler errors, strict
record validation, authentication selection and the security allowlist remain unchanged.
Legacy Work callers that branch on an error now choose fallback instead of logging
false success; this does not establish that they display partial records.

The six-file isolated Pipeline/WorkIQ/security-boundary cohort passes 251 tests with
three warnings; all six changed executable Pipeline lines are covered. A separate
installed-source synthetic exercise passed complete, valid-empty, rejected-record and
partial-scope cases without provider queries or business-state writes. Exact predecessor
and named-stream deployment checks passed, and the actual staged privacy hook passed.
This updates the existing Pipeline repair; the unique installed source count remains
thirteen. The 10,185-test frozen aggregate above predates this repair and is not
relabelled as a full aggregate of the new revision.

The real WorkIQ handler is covered by a synthetic wrapper regression: dispatch now
reaches it, but its legacy calendar output is explicitly rejected for missing generic
record identity. No random identifier or current-clock event timestamp was fabricated.
Canonical §38.8 requires display-only WorkIQ output; the current legacy shapes and
strict generic ingestion contract still need reconciliation before this batch route
is operational. Passing synthetic checks are not live WorkIQ acceptance.

The continued private Work caller trace found false degradation on normal side-effect
provider returns, terminal HTTP errors retried through the `OSError` branch, and partial
WorkIQ failures recorded as breaker success. The source-pinned synthetic selection
against the exact retained predecessor reproduces 16 failed cases (26 pass).
The repaired selection passes all 42 cases; all 19 changed executable Work lines are
covered. A final run also exercises the actual ADO method through the wrapper.
WorkIQ mode errors and open-breaker skips are retained in the result, partial failure
cannot close the breaker, and handled Kusto exceptions remain explicitly degraded.
This does not validate the pending WorkIQ display contract or the breaker implementation
itself: the query and breaker dependencies are synthetic in these tests.

The private source/test bytes are retained in the session's reviewed-source folder,
not force-added to public Git. Exact predecessor and named-stream installation
checks passed for `work_loop.py`; its DLP stream remains byte-identical.
The installed-source synthetic exercise passed normal completion, terminal/transient
HTTP boundaries and partial WorkIQ failure with no provider queries or business-state
writes. Public isolation/architecture/Pipeline safeguards separately pass 92 tests
with three warnings. The installed unique source count remains thirteen.
The WorkIQ display-only proposal remains unapproved after the owner-unavailable
response; no contract bypass, new access or production acceptance is inferred.

The summary writer trace then found that READ mode and failed/degraded refreshes
rewrote `work-summary.md` as newly refreshed, and freshness trusted filesystem mtime.
Fourteen synthetic cases fail against the exact saved predecessor. The repaired
selection passes 58 cases with all 20 changed executable freshness lines covered.
READ and incomplete refreshes retain the prior summary bytes/timestamp; only complete
collected output can publish a fresh summary, including valid empty provider listings.
Returned freshness changes after publication succeeds, not before. Recorded aware
timestamps determine age; missing/invalid/naive/future values remain unknown.
Per-domain provenance is still separate from the summary's refresh timestamp.

The private reviewed source/test bytes are retained separately, and the installed
freshness continuation passed exact predecessor and DLP-stream verification.
A native installed-code exercise used temporary synthetic files to confirm recorded
age versus mtime, byte/mtime preservation on reads, failed-refresh preservation and
successful empty-fetch publication. It touched no production State Files or providers.
The installed unique source count remains thirteen; the public full aggregate above
does not include these private Work repairs.

The bridge walkthrough reproduced another false-health chain: 0..100 Work scores
were clamped into a 0..1 schema without normalization, zero/missing values became
healthy defaults, and the day view expected a nonexistent `payload` envelope.
The Personal Pipeline also injected fields outside the Work-owned pulse schema.
Two public ownership regressions and nine numeric/object-schema regressions initially
fail. The retained private predecessors reproduce 21 failed producer/reader cases.

Public repair `f5c419c` removes the Personal pulse mutation and rejects boolean,
nonfinite and nonobject bridge values before publication. The private producer now
normalizes scores, preserves zero and source timestamps, and retains the old pulse
with explicit errors for missing/invalid/stale provenance. The day view validates
the flat schema and reports invalid artifacts as unavailable rather than displaying
perfect scores; unsupported Personal timestamps are no longer consumed. No production
artifact was migrated or overwritten during this repair.

The public bridge/Pipeline/isolation cohort passes 168 tests with three warnings;
all five changed executable schema-validator lines are covered. The final private
producer selection passes 84 cases, with all 31 changed executable producer/reader
lines covered. These are local synthetic results, not provider or owner UAT.
All four installed source files passed exact predecessor and named-stream checks.
A native installed-code exercise confirms 40 becomes 40/100 (not 100/100), zero
focus is retained, missing metrics preserve the earlier pulse and invalid artifacts
are unavailable. It used temporary synthetic files only. The reviewed private source
and test bytes remain outside public Git; the installed unique source count is fifteen.

The refresh persistence trace then found that Agency discovery suppressed direct ADO
even when enrolled publication later refused Agency, and complete empty calendar/ADO
results skipped their writers, retaining obsolete snapshot rows. Preflight now keeps
ADO independent. Explicit collected empty lists reach the existing snapshot writers;
missing keys retain cached domains. This does not clear append-only history or
manufacture missing email/Teams results.

The same trace found summary `setdefault` calls preserving previous derived scores
instead of current metrics, missing domain metrics fabricating perfect delivery, and
future domain timestamps appearing fresh. Current validated metrics now replace
cached values, recovered boundary risk is explicitly false, unavailable inputs remove
those derived fields with errors, and future/naive/invalid domain ages remain unknown.
Incomplete refreshes still preserve the earlier summary generation.

The exact retained predecessor fails 26 of the final 117 synthetic cases; the repaired
selection passes all 117, with all 30 changed executable Work lines covered. The
selection includes actual journaled calendar/project empty-snapshot publication in
an isolated synthetic enrolled installation. A separate installed-code exercise
confirms ADO independence, missing versus explicit-empty writer calls, fresh metric
replacement, invalid metric omission and future-age rejection. It does not query
providers or write business state. Reviewed private source/test bytes are retained
outside public Git. Exact predecessor and DLP-stream installation checks pass, and
all fifteen installed source files independently match current source/latest receipts
with every named stream intact. The frozen public aggregate above predates these
private repairs; none of these results establishes live provider or owner acceptance.

The event-publication trace found unsupported activity claims: high-importance
received mail became `replied` evidence and large calendar invitations became
`presented_about` evidence. Those automatic inferences are removed. The explicit
visibility-event writer retains its existing five event types but now rejects
missing/blank fields, invalid/noncanonical dates, unknown types and non-Work sources
before publication instead of defaulting the date or claiming a reply. This validates
the explicit input shape; it does not independently verify the truth of caller-supplied
evidence. Production journals were not inspected, rewritten or auto-migrated.

The corrected final synthetic selection reproduces 15 failures against the exact
saved predecessors and passes all 137 cases after repair. All 21 added/changed
executable writer lines are covered; deletion of the inference path is exercised
by the incoming-mail/invitation nonpublication assertions. Supported explicit events
publish through actual journaled synthetic Work storage, while rejected events
preserve the prior bytes. An earlier intermediate run had a missing synthetic test
import; the final baseline and passing receipts supersede it.
Installed-code inference and unwrapped validator/body checks pass separately without
production enrollment, provider queries or business-state writes. Both source
installations preserve predecessor bytes and named streams. All sixteen unique
installed source files independently match current source/latest receipts and every
named stream. Private implementation/tests remain in reviewed session artifacts,
not public Git. These checks are not live-provider or owner UAT.

The display-only WorkIQ contract was requested again after local documentation
commit `b928040`; the owner-unavailable response is not approval. Its implementation
and the recovery-format proposal remain pending, without a generic-schema bypass,
new access, cutover or inferred acceptance.

The historical-evidence trace reproduced eleven failures in the then-current private
selection: batched journey/visibility writes lost staged rows, repeated rows were
duplicated, prefix project headings or a later project's table misrouted evidence,
and missing milestone dates became current dates. Both table writers now use the
existing transactional text-update API, see staged bytes, match the exact section
and skip identical rows without advancing a generation. Existing rows are retained.
Journey project/date/milestone fields must be explicit, and the ADO producer requires
an aware source date, preserving its source month. Publication failures are visible.

The expanded final selection passes 152 synthetic cases with all 38 changed executable
lines covered (6 loop, 32 writer). It exercises actual enrolled journal transactions
and batch staging, plus legacy nonenrolled compatibility. This is not encrypted-state
acceptance: earlier descriptions of these synthetic publication checks as encrypted
were inaccurate and are corrected above. The separate protected-state restore
evidence is unchanged. No production historical journal was read or migrated.
The final expanded predecessor run fails 15 cases (137 pass). Reviewed private
implementation/tests are retained outside Git. Two installed sources preserve their
predecessor bytes and streams. A separate installed-code exercise uses a synthetic
transaction adapter and temporary journal to confirm row preservation, deduplication,
exact section routing and visible missing-date refusal; the real journal protocol is
covered by the isolated selection, not that installed adapter exercise. All sixteen
installed source files and every named stream match current source/latest receipts.

The Kusto writer trace found that a refresh with only unrelated successful queries
could replace the incident snapshot with "No active incidents." Missing or failed
GQ-050 is no longer treated as an empty listing. The prior snapshot survives with a
visible writer error; verified empty active-incident output can publish zero, while
missing optional incident queries remain labelled unavailable. Failed/malformed
metric-query rows are rejected before update. Extractors preserve numeric zero,
including severity zero. Empty outcome maps and query failures are explicit loop
errors/degradation rather than empty success.

The exact predecessors reproduce 31 failures (152 cases pass). The repaired private
selection passes all 183 synthetic cases with all 29 changed executable lines covered.
Tests exercise actual journaled preservation and valid-empty publication; they do not
query Kusto or production State Files. Installed-code query/formatter/body decisions
pass with a mocked publication adapter, not production enrollment. Reviewed private
sources/tests remain outside public Git, and both installed sources preserve original
streams and predecessor backups. All sixteen unique installed source files and streams
independently match current source/latest receipts. Live cluster/query acceptance,
the larger audit and the pending contract/access decisions remain unfinished.

The direct-provider audit found a hard-separation defect in the Work Outlook path:
it spawned the full Personal Pipeline with `--source outlookctl_bridge`, but that
source filter does not suppress Personal state merges, health writes, context export
or action processing. The code path violates the existing FR-19 isolation contract;
this finding is not evidence that those effects ran on production in this audit.
The path now uses the existing allowlisted strict single-Connector adapter, with
explicit coverage/parse errors instead of skipping malformed output. Work Graph
authentication errors now recommend Work-specific reauthorization rather than the
Personal enrollment command.

The original six synthetic dispatch/remediation cases fail against the exact saved
predecessor. The repaired selection passes 190 cases, including an additional actual
standalone-adapter test that rejects legacy Outlook records without provider identity;
all six changed executable lines are covered. No generic schema was weakened and
no identity/date was fabricated. Native Outlook prerequisites and legacy record
compatibility remain unresolved, independently of this repaired dispatch boundary.
Installed-code synthetic complete/empty/failure/parse/remediation checks pass, and
all sixteen unique installed sources and named streams match current source/latest
receipts. Private reviewed source/tests remain outside public Git. Neither the old
Personal Pipeline subprocess nor a live provider/production State File was exercised
by these synthetic checks.

Native Graph tracing found that calendar parsing appended `Z` to negative offsets,
producing invalid timestamps, and mail parsing omitted read status, importance, folder
and received fields expected by Work. Public repair `5e41f71` preserves explicit offsets
and exposes those actual provider fields without inferring response intent. The private
comms writer counts only explicit false as unread, separately labels unknown read status
and no longer claims the inbox is clear from this limited result.

Four public cases initially fail; the isolated public native/calendar/Connector cohort
passes 183 tests with one dependency warning. Both changed executable calendar lines
are covered; additive dictionary fields have no separate executable coverage lines
and are verified by exact output assertions. Three private consumer cases fail against
the writer predecessor (with the repaired native parser), and the expanded private
selection passes 193 cases with all five changed writer lines covered. The actual
parser-to-loop-to-journaled-writer chain verifies read/unread/unknown and body exclusion.
Installed parsing/rendering checks use synthetic payloads and a publication mock,
not provider access or production enrollment. All eighteen unique installed sources
and named streams match current source/latest receipts; private reviewed implementation
and test bytes remain outside public Git. No credentials or production State Files
were changed, and live Graph readiness remains separately unresolved.

Work health had an independent legacy path accepting Personal token files as Work
Graph readiness, using file mtime as token age, and unconditionally claiming enforced
separation and validated bridge schemas. It now reuses the Work-only `msgraph.work`
platform-store validator and explicitly distinguishes an unexpired credential from
untested provider access. Unperformed enforcement/artifact checks remain unperformed;
missing breaker state is reported as not observed rather than healthy.

The final exact isolated predecessor selection fails both new credential-scope cases;
the repaired private selection passes 195 cases, covering all seven changed executable
health lines. Provider subprocesses, credentials and State Files are synthetic/mocked,
so these checks do not establish native provider reachability or production enrollment.
Reviewed private source/test bytes are retained outside public Git, and the final
stream-preserving installation plus independent verification matches all nineteen
unique installed sources and every named stream against source/latest receipts.

The explicit meeting pre-read marker bypassed the Work publication journal with a
direct notes write. It now reuses the existing transaction-aware evidence-table
appender, retaining both staged rows in a batch, refusing stale sessions and matching
the exact section rather than another section's divider. An identical rendered mark
is a no-op, existing content is retained and missing notes cannot claim success.
The command records an explicit user assertion, not inferred review.

The expanded exact predecessor selection fails five cases; the repaired isolated
private selection passes 201 cases with all four changed executable meeting lines
covered. Actual synthetic journal publication, stale-head refusal, batch retention,
no-op behavior and the unenrolled legacy path are exercised. This is not encrypted
State File or real meeting acceptance. Reviewed private source/test bytes are retained
outside public Git; all twenty unique installed sources and named streams match
current source/latest deployment receipts after stream-preserving replacement.

Shared Work read helpers also used file-copy mtime as missing freshness evidence,
silently treated naive times as host-local times, and rendered future ages as fresh.
They now retain unknown age for missing/malformed/naive/future metadata, use only
recorded timestamps, and disclose unknown freshness for an existing XPF snapshot.
Absent optional XPF files keep the existing no-advisory behavior. The coupled
public file-quality condition (`3abb186`) denies future timestamps freshness credit;
it does not change provenance weights or nonfuture timestamp/TTL behavior.

Four private predecessor cases and one public future-quality case reproduce the
defects. The repaired shared-reader/journal/producer selection passes 209 private
synthetic cases, covering all twelve changed executable helper lines. The public
recorded-time and existing quality/KB selection passes 51 cases (18 private-dependent
cases deselected); the changed quality condition is covered on both branches.
The installed public quality/private helper sources and all named streams were
verified, bringing the installed unique-source inventory to twenty-two. Private
reviewed implementation/tests remain outside public Git. These are isolated synthetic
and source/stream checks, not provider, encrypted-state or owner acceptance.

The warm-start provenance trace found that refresh added `human_authored` to six
historical State Files merely because their filenames matched and `providers_used`
was missing. The import code records an automated generator, not evidence of human
authorship. Removed the sole automatic caller and stamping helper under the existing
accuracy-first contract. Unknown provenance stays unknown; existing labels and
generators are not rewritten. The removed helper's comment also overstated its effect:
the current quality gate does not award the claimed accuracy boost for that label.

Two exact predecessor refresh cases fail for unknown/imported files. The repaired
private selection passes 212 cases, including actual synthetic journal generation
and byte-preservation checks over all six files, with existing provider labels retained.
The implementation is removal-only, so there are no added executable lines to credit.
Six public tests requiring invented stamps were retired in favor of these actual
refresh checks; the remaining public quality selection passes 51 cases with twelve
private-dependent skips. Private reviewed source/tests remain outside public Git;
all twenty-two installed sources and every named stream match current source/latest
receipts. No production State Files were changed, and historical provenance labels
have not been individually verified or migrated.

Graph token preflight could pass a future expiry without an actual access token,
including an expiry-only refresh response, while unknown expiry skipped the existing
refresh recovery path. It now reuses the OAuth access-token/lifetime validator before
and after refresh; unknown/invalid lifetime attempts existing refresh and cannot pass
without a usable unexpired token. Eleven public cases reproduce the defects; the
repaired readiness/lifecycle/credential cohort passes 91 cases with all nine changed
executable lines covered. Installed helper parity and all twenty-three unique source
files/named streams are independently verified. These tests are isolated and synthetic.

A separate installed metadata-only prerequisite observation at 2026-11-03 14:37 UTC
confirms all three Graph credential records (Personal, Work, Work-MCP) are absent,
and HA has no enrolled credential. Existing Google read-only grants refreshed
successfully with exact read-only scopes and unchanged scope metadata. Outlook CLI
is absent; Agency, npx and Azure CLI resolve on PATH. The core configuration is still
absent. No business API or State File was accessed by this probe; Google credential
refresh is the only requested credential effect. Azure's existing bearer acquisition
succeeds but the Work availability check returns false; this mismatch is under
investigation rather than relabelled as an authentication failure.

The ADO prerequisite mismatch was a reproduced Windows executable-resolution failure:
bare `az` raises `FileNotFoundError`, while resolved `az.cmd` returns an active account.
Work provider preflight and Work health now resolve Azure CLI before their existing
subprocess account checks. Five predecessor cases fail; the repaired isolated private
cohort passes 216 cases, including exact resolved argv, empty/failed account output
and missing CLI. Both separately executable changed health lines are covered; the
loop's changed argument continuation has no separate coverage statement and is
verified by exact argv assertions. Existing scope and authentication logic is retained.

At 2026-11-03 14:46 UTC, the repaired installed Work availability check returns true,
and an authenticated read-only ADO project-metadata request returns HTTP 200 with the
expected response shape. No work items, meeting content, credential enrollment or
business State Files were requested/changed; raw responses and account identities are
not persisted. This establishes CLI and project-metadata access, not full work-item
ingestion, refresh completeness or owner UAT. All twenty-three unique installed
sources and every named stream match current source/latest receipts; private reviewed
implementation/tests are retained outside public Git.

Work availability and Work health also used unresolved npx launches and selectors
that could install a package during diagnosis. They now reuse the existing Connector
resolver and run version-only checks with `--offline --no-install`. Health explicitly
separates CLI availability from untested authentication and record compatibility.
Nine original predecessor cases fail; the repaired synthetic selection passes 223
cases with all eleven initially changed executable lines covered.

The installed offline check initially failed because npm reported the required
`@microsoft/workiq@0.4.1` package missing. A deliberate dependency restore succeeded,
but npm 11's cached-range handling still rejected the existing package requested as
`0.x` under `--no-install`. Exact-version restoration and invocation succeeded.
The Connector, script preflight and package preflight now share the exact `0.4.1`
release rather than divergent `0.x` and `latest` selectors. Four public reproducers
fail before this repair; the repaired public cohort passes 212 cases, with all three
changed executable lines covered. The private compatibility selection also passes.

Installed verification then exposed a distinct startup false negative: the CLI takes
about 5.6 seconds on this host, longer than the loop's five-second timeout. The loop
now uses the existing ten-second Work health limit. Two exact predecessor cases fail;
the repaired private selection passes all 223 cases. The changed argument continuation
has no separately counted executable statement; exact subprocess timeout assertions
verify it. Reviewed private source/tests remain outside public Git.

At 2026-11-03 15:14 UTC, Artha's actual installed offline/no-install availability
path returns true for `0.4.1` in 5.64 seconds. The version command reports CLI build
`0.4.1.19742+d4efecc4df8abb308c4d2cda78313dcc3023cebc`. Dependency restoration was an
explicit separate operation following the missing-package failure, not a side effect
of the diagnostic. No M365 authentication/business query, scope enrollment, license
acceptance or business State File write was performed. All twenty-four unique
installed sources and every named stream match current source/latest receipts.
Public code/tests are committed locally as `fd16529`; no push occurred.
This closes CLI readiness only, not the unapproved WorkIQ display contract or actual
M365 ingestion. The recent 585-pass public readiness cohort predates this exact pin;
the 212-case selection is current targeted evidence, not a new full aggregate.

HA preflight had another false-success boundary: missing/unreadable configuration
could pass as a skip, malformed mapping/enablement/fetch values were not consistently
validated, and selected connectivity exceptions passed as off-LAN skips. The package
surface additionally used a best-effort cached loader with a different configuration
root. Both surfaces now read the checked installation explicitly and retain these
unknown/unavailable outcomes as failed P1 advisories. Valid absent/disabled Connectors
still skip; missing credentials and unavailable credential storage remain distinct.
Error labels do not echo parser/provider payloads, and no new P0 gate was added.

The initial isolated selection reproduces 27 failures. Expanded HA/Pipeline checks
pass 147 cases, covering all 33 changed executable lines. The all-preflight/WorkIQ
selection passes 322 cases. Separate synthetic HTTP assertions confirm both existing
HA request paths already authenticate with the supplied token; there was no
authentication-header defect and no Connector authentication edit.

At 2026-11-03 15:25 UTC, both installed preflight surfaces report failed P1 for the
actually absent HA credential. A guard verifies that neither calls the device
provider. No household payload, device API, credential enrollment or business State
File was read/written. All twenty-five unique installed sources and every named
stream match current source/latest receipts. Public code/tests are committed locally
as `1afe99e`; no push occurred. This repairs prerequisite reporting, not missing
access or live HA/device acceptance.

The fresh full isolated public regression at `32aa62d` completed with 10,292
passes, 179 skips, 20 expected failures and one failure: the HA repair introduced
a direct YAML read in the preflight package, violating the shared-loader architecture
gate. Combined core coverage was 92.61%; the failed aggregate is not acceptance.
The original log/XML/coverage/receipt remain preserved, not overwritten.

The correction routes both HA surfaces through the existing shared loader with
opt-in `strict=True`. Strict reads propagate missing/unreadable/invalid-root errors
instead of turning them into disabled configuration, retain successful-read caching,
shallow-copy and invalidation semantics, and use cache keys separate from best-effort
reads. Exceptions are not cached or logged with parser payloads in strict mode.
Default callers retain the existing best-effort contract; no architecture exemption
was added. Nine strict-loader cases and the architectural gate fail before correction;
the repaired loader/architecture/all-preflight/HA/WorkIQ selection passes 398 cases,
with all seventeen changed executable lines covered.

At 2026-11-03 17:32 UTC, both corrected installed entry surfaces still report the
actual missing HA credential as failed P1 without calling the device provider.
All twenty-six unique installed sources and every named stream match current source
and deployment receipts. Code/tests are committed locally as `17cf63b`; no push,
credential enrollment or business State File changes occurred. A new complete
regression is required for this corrected snapshot; the earlier failed aggregate
and targeted green selection cannot substitute for it.

Two attempts against the corrected snapshot were terminated with Windows
`0x40010004` (`DBG_TERMINATE_PROCESS`) without a pytest failure result. The first
preserved partial JUnit XML for 2,369 tests with zero failures/errors before
termination; the second stopped at 16% with no JUnit file. Their logs and receipts
are retained and marked interrupted, not passed. No test process remains. A smaller
final isolated preflight/architecture/HA/Graph/WorkIQ cohort passes 354 tests with
one existing requests dependency warning in 71 seconds. This is targeted evidence;
the complete aggregate remains unverified, and the termination cause is unknown.

The external-agent discovery and health checks had a parallel false-success boundary:
configuration read/shape failures and malformed existing registries could become
successful skips or healthy-empty results. Both preflight entry surfaces now use the
shared strict configuration loader and opt-in strict registry parsing; unknown
readiness is a failed P1 advisory, while valid disabled settings and the existing
optional-registry behavior remain unchanged. An initial installed synthetic probe
also caught the monolithic script passing a string to the registry's `Path` API;
the script now passes a `Path`, with a valid-empty-registry regression for both
entry surfaces. The source-pinned isolated external-agent/preflight/config-loader/
architecture selection passes 692 tests with 4 skips.

All three changed source files were installed using exact predecessor fingerprints
and verified receipts/backups. Installed package and script surfaces each passed
synthetic missing/bad configuration, disabled-feature, malformed-registry and
valid-empty-registry cases; no live agent configuration or registry was read.
Existing runtime named streams were preserved exactly. The source-only named stream
on `agent_registry.py` was not copied over a runtime file that had no named streams
before installation.

Planning-signal preflight also treated a missing `last_updated` as current because the
loader synthesized today's date for existing files, and accepted future dates as fresh.
Loading now preserves absent metadata, validation rejects invalid/future dates, and
both preflight surfaces use one active-signal age check. A missing date remains a
preflight warning until an explicit successful save records it; loading alone cannot
freshen the file. The initial paired-surface and write-path regressions failed in five
cases; the final planning-signal/preflight/architecture selection passes 127 tests,
covers all changed executable lines, and passes Ruff/whitespace checks. This does not
refresh stale state or prove that Step 8t ran.

An earlier direct in-place expanded test run violated the test suite's hermeticity
guard by creating generated agent artifacts in `state/work/` and `tmp/`; the exact
newly created files were removed. The isolated rerun above is the valid test evidence.
That in-place run also exposed a direct connector import in the ignored local
`scripts/work_loop.py`; it was not part of this source change and remains an audit
item. The selected architecture suite passes in the isolated tracked-source copy.

Remaining gaps include GFS/off-host recovery, genuinely missing or unpopulated state,
absent optional credentials/MCP registrations, independent provider/held-out outcomes,
the incomplete canonical/code audit and core activation.
The full passing aggregate recorded below predates these preflight repairs; it is
not relabeled as a full aggregate of the new source. Cutover and external actions
remain disabled. Local operational evidence is not owner or release approval.

## Operational takeover repairs — 2026-11-02

The operational audit remains in progress; the canonical review and complete code walkthrough are
not finished. Source repairs are not full-product acceptance or permission to enable cutover.

| Boundary | Actual outcome |
|---|---|
| ADO | Date-only WIQL, schema-valid records, explicit failures, complete batch validation and cap disclosure repaired. An installed read-only probe authenticated and returned a valid empty listing; it did not establish nonempty provider-record coverage or reflection scope. |
| Protected readers | Shared vault factory, selected-directory binding and portable Windows age discovery repaired; invalid Windows vault names refused. |
| Encrypted action fields | Installed Windows helper uses ciphertext-only temporary input, key stdin and plaintext stdout. Native synthetic round-trip passed without using the production identity or State Files; no temporary files remained. Legacy bulk decryption is unchanged. |
| Pipeline | Only complete fetches advance freshness. Successful retry snapshots replace failed prefixes; exhausted retries retain distinct partials with an error. |
| Public packaging | Explicit package declarations and core prompt data repaired. A clean public wheel built and its core resources/CLI were exercised. Optional private Work implementations are not distributed or provisioned by that wheel. |
| Work refresh | Removed an empty authentication override in private source. Reviewed synthetic producer verification is source-pinned and local-only; private source/tests remain ignored. |
| Skill platforms | Six initially failing regressions repaired. Platform eligibility precedes cadence/P0 execution, including forced single-skill mode. Native installed selection skips Mac-only skills on Windows; their absent Mac implementation is not supplied. |

Seven runtime source files are installed and independently fingerprinted, including the subsequent
foundation continuation. Original bytes and every named NTFS stream were retained in verified backups.
An initial staging refusal exposed native stream loss with `WriteAllBytes`; no target was replaced
then. Synthetic checks established stream-preserving writes and atomic replacement before deployment.
No runtime configuration, State File, enrollment, cutover, listener or external action was changed.

Verification on Windows Python 3.12.10:

| Gate | Actual result |
|---|---|
| Integrated Connector/Pipeline/vault/packaging/encryption selection | 430 passed, one skipped; ADO/vault combined line+branch coverage 99.36% against the unchanged 95% selection floor. |
| Skill routing, cadence, health and Catch-up compatibility | 123 passed, two skipped. All 13 changed executable routing lines are covered (100%); this is not full-module coverage. |
| Real development canaries | Initially failed because GNU Make was missing. A manifest-verified session-local GNU Make and process-only POSIX/tool PATH restore all 35 canaries; no test or gate was weakened. |
| Static checks | Repository/core Ruff, strict typing of 70 core files, imports, existing writer ratchet and generated-register checks pass. |
| Full isolated public suite | Frozen source `71457fe`: 10,075 passed, 185 skipped, 20 xfailed, nine warnings; exit 0 in 7,235.28 seconds. Core coverage: 93.82% line, 89.34% branch, 92.61% combined; both existing 85% floors pass. Completed 2026-11-03 at 08:54 UTC. |
| Original-source baseline | Source `8d7ad5b`: 9,940 passed, 191 skipped, 20 xfailed, 21 failed and one setup error. All 21 failures required missing GNU Make. The separate native registry publication refusal did not recur in the focused three-case selection or final aggregate; its cause remains unestablished. |
| Privacy | Full working-tree and unpublished-commit scans and actual staged hooks passed. The initial scan failed closed after two native scanner access violations; its failed observation is retained, not retroactively cleared. |

Public code/tests are committed locally as `96fb12e`. All 15 staged paths matched tested working
bytes or Git's LF normalization only; the actual pre-commit privacy hook passed. The separate
privacy-policy selection passed 50 tests with four skips. The full working-tree scan was not a frozen
source cohort; the staged hooks checked the exact committed repair paths. Source-pinned private producer verification passed again after
the skill repair; private code and runtime receipts are not included in the public commit.
The full public suite excludes private Work tests by existing policy; its passing result does not
replace the separately source-pinned private producer evidence or establish live operational acceptance.

The earlier incumbent Catch-up remains a degraded observation, not fresh acceptance of these repairs.
At this checkpoint, exact agent reflection was restored but its evidence synthesis, ordered writes
and close were unfinished; the Nov 3 continuation above records their later execution.
Core runtime configuration is absent and cutover stays disabled. Incumbent combined
Catch-up instructions conflict with the technical spec's numeric-only Work bridge; no silent routing
change was made. Independent recovery, provider/held-out outcomes, supported-platform acceptance,
owner UAT and release approval remain separate requirements. Nothing is pushed.

## Current-source Windows run — 2026-10-31

Source `75bb7ec` (Windows privacy fix for `core_items`); Python 3.11.15; shared CPU. Details: `specs/re-artha.md`, "(late night)" entry.

| Gate | Actual result |
|---|---|
| Full isolated suite (`tests --ignore=tests/work`) | **9,770 passed, 187 skipped, 20 xfailed, 26 failed**; 4,276 seconds. All 26 failures need GNU Make and Git POSIX tools on PATH; with both on a process-only PATH the affected files pass **87/87** |
| Core line/branch | **94.25% / 89.87%** (85% floors pass) |
| Mandatory security selector | **1,688 passed, 78 skipped**; **88.04%**; 2,722 seconds |
| Changed core lines since `a5cd4d6` | **91%** (456 lines, 38 missing) |
| Changed boundaries on Python 3.12.15 and 3.13.16 | **29 passed** each |

Later local commits `1706e79` and `67ca197` change preflight only; their targeted tests pass.

## Current verified Windows checkpoint

The final frozen 1,047-file source set passes both serial aggregate gates with **no source drift**:

| Gate | Actual result |
|---|---|
| Full isolated Windows Python 3.11.15 | **9,573 passed, 186 skipped, 20 xfailed**, ten warnings; exit 0; 3,756.05 seconds |
| Core combined/line/branch | **93.13% / 94.31% / 89.92%**, all existing 85% floors pass; same full invocation, no report union |
| Mandatory security selector | **1,684 passed, 77 skipped**, two warnings; **88.04%**, exit 0; 2,417.99 seconds |
| Changed core lines | **10,922/11,738 (93.05%)**, including untracked native modules through an alternate index; real index unchanged and alternate cleaned |
| Additional native Python 3.12.15 boundaries | **219 passed, one existing skip**, 22.41 seconds |
| Private Work verification | Exact opted-in local public/private source/runtime/selection evidence exits 0 with cleanup; no provider/UAT inference |

Both Ruff configurations and strict typing of all 65 core files pass. Earlier failed snapshots are
retained, not reclassified. Older intermittent native registry/ciphertext refusals remain unexplained.
The numerical/test gates do not establish supported-platform parity or independent product outcomes.

**Not cutover-ready:** historical inventory review, Work enrollment, real native recovery, independent
held-out/document/Goal/cross-Domain/workload acceptance and owner UAT/release remain unperformed.
The owner separately approved the exact storage preview and stopped-writer attestation on 2026-10-28.
The live two-object adoption exits 0 at 12:17:42-07:00; readback reports private/eligible and inventory
not enrolled, with unchanged identities/owners/sizes/LastWriteTime. This verifies the authorized
operation, not whole-store hardening or owner post-observation release sign-off. The previously
authorized registry previews still refuse (both exit 20). Read-only diagnosis confirms missing profile
inventory is deliberately refused by the non-initializing reader, not an adoption/bootstrap defect.
No directory/key/inventory was created. Real owner-reviewed per-profile dependency metadata and
separate declaration/exact-digest review authorization are needed; missing registry files cannot
establish that no installed or historical dependencies exist. Same-host Work recovery
does not require a new independent custody component, while personal cloud recovery remains required.
Effects/sync and approved degraded Work capabilities remain held. Final Ruff, strict typing (65 core/
three credential files), imports (430), unchanged writer ratchet (146/147), schema, generated-register
and whitespace checks pass. Actual working/unpublished privacy and the staged pre-commit hook pass.
Public code/tests/delivery are committed locally on `main` as **`a5b49f2`**; documentation follows
separately. Nothing is pushed or activated, and active private specs remain ignored/unarchived.

The actual hook runs using the existing validated Python through a temporary process-local `python3`
alias; no hook bypass or runtime installation. Git normalizes ten staged paths from CRLF to LF;
105 code/delivery paths match tested working bytes exactly and no other content difference exists.
Evidence fingerprints bind actual working bytes. A different deployment checkout must establish its
own exact cohort/platform evidence; the commit label alone is not that proof. The later authorized
filesystem metadata operation changes no implementation bytes or cutover setting.

## Mac Python 3.14.6 baseline — 2026-10-28

Full isolated suite on the Mac before repair: **1 failed, 9,441 passed, 317 skipped, 20 xfailed** (486 s).
The failure was `test_work_restore_and_key_installation_remain_held`, which assumed the Windows host:
on macOS the Work host gate refuses first, with a different message. The test now passes
`host_platform='win32'` to reach the unbound-checkpoint refusal; production code is unchanged and Work
stays refused on non-Windows hosts. The file then passes (10 tests). Mac rerun after the repair (Python 3.14.6): core coverage target passes (4,067 passed, 229 skipped;
88.94% combined, 89.93% line, 86.27% branch), changed-line coverage passes (86%), working/unpushed privacy passes.
The suite's one failure was the then-stale generated register, since regenerated and rechecked (3 tests pass).
The security-harness coverage target **fails on macOS at 74.36%** (1,543 passed, 218 skipped) because
Windows-only modules and tests are skipped; it is not weakened here, and passes at 88.04% on native Windows.
It needs the owner-reviewed cross-platform coverage protocol, not a Mac exclusion. Full isolated suite on the regenerated register,
Python 3.11.15 (external venv `~/.artha-venvs/.venv311`): **9,425 passed, 318 skipped, 20 xfailed**, exit 0, 424 s.
Read-only personal `inventory` on this Mac: 6,414 files, 206 excluded, 0 blocking, ready. Personal
`registry-preview` refuses (historical review absent), as on Windows; the owner must declare and attest the
historical inventory. No checkpoint, key or registry write occurred. Static gates (lint, Ruff, imports, write ratchet, schema, strict typing of 65+3 files) pass.
Not a recovery, evaluation or UAT result.

## Release-completion continuation — 2026-10-27

The renewed local-main swarm separates native integrity diagnostics, corpus provenance and same-host
Work integration from integrator-owned release evidence and aggregate gates. No live enrollment,
credentials, provider effects, recovery, release approval or cutover is authorized by implementation.

Outer release-boundary synthetic reproducers found raw container exceptions and accepted malformed
counts, precision floors, Domain lists and owner-review values: 17 failures/11 passes in the first
selection and eight failures/five passes in the scope selection. The repair retains v4 and existing
numerical policy. Initial repaired boundary/integrated verification passes 304 tests; extended
verification and source-frozen aggregates remain pending. Strict core typing passes all 64 files
and focused policy/test lint passes. These results do not replace native or independent acceptance.

All production slices are now frozen. Extended release/artifact/cutover integration passes **444
tests**, 218.39 seconds. Corpus repairs reproduce 12 provenance/permission defects and pass the
parent's isolated **40-test** selection, 4.25 seconds; labeling cannot attest live origin or replace
separately reviewed held-out evidence. Work batch teardown serializes commit/discard with joined
copied-context workers; its dedicated privacy/publication selection passes **94 tests**, 63.83
seconds. No second writer or new Work authority was introduced.

The bounded native investigation passes **114 tests, one skip** plus concurrent read, metadata
mutation and 1,000-lifecycle probes. A Work CLI fixture reported registry publication refusal;
six fresh nonsynchronized reproductions pass. Both observations are retained; synchronized ancestry
and other native causes remain hypotheses. No native source fix or weakened check was justified.
Final source-frozen aggregate gates remain pending. Repository/core Ruff, syntax, strict typing
(64 core/three credential files), imports, unchanged write ratchet and example schema pass.

First-wave frozen aggregate results on native Windows Python 3.11.15 now pass:

| Gate | Actual result |
|---|---|
| Mandatory security selector/floor | 1,622 passed, 77 skipped, two warnings; 87.82% combined versus unchanged 85%; exit 0; 3,046.65 seconds |
| Core tests and separate coverage floors | 4,123 passed, 109 skipped, two warnings; 92.99% combined, 94.22% line, 89.66% branch; exit 0; 3,515.83 seconds |
| Existing Make/privacy delivery canaries | 85 passed; 107.88 seconds; no tool installation or permanent PATH change |
| Actual working-byte/unpublished-history privacy | Both pass, exit 0 |

Raw logs, source manifests and coverage databases/XML are retained in the nonsynchronized session
directory. Earlier intermittent native failures remain unexplained, not retroactively cleared by a
passing rerun. Separately authorized metadata-only registry previews exposed an existing-host storage
adoption prerequisite; no secret backend or live permission changes occurred. The owner approved a
bounded opt-in adoption workflow in WP-18. That additional source slice is being implemented and is
not certified by the preceding frozen snapshots. Real recovery/independent acceptance/UAT remain open.

The approved adoption slice is now frozen after 166 native/CLI tests (one existing skip), 100%
new-module line/branch coverage, and 12 failing-then-passing configured-root exclusions. It reuses
checkpoint path policy and includes named process-death and descendant preservation evidence.
Parent integration initially passed 81 tests before the additional boundary cases; final expanded
security and full-suite/core-coverage invocations follow. Precise inherited-ACE presentation and
access limitations are recorded in WP-18 and the core documentation, not relabeled as live UAT.

The expanded final security invocation exits 1: **one failed, 1,673 passed, 77 skipped**, two
warnings, 2,768.17 seconds; coverage **88.00%** reaches the unchanged floor but does not clear
failed tests. The adoption unknown-native-error injection does not observe refusal under aggregate
order. It passes in the focused selection; native singleton/module state is only a hypothesis.
The serial full-suite step did not start. Diagnosis is assigned without retries/skips or weakened
refusal semantics. Earlier first-wave passes remain valid for their earlier source, not this gate.

The exact native predecessor/adoption pair reproduces one failure/one pass: monkeypatch undo leaves
a bound instance method shadowing a later class patch. Actual-instance fault injection now asserts
call identity/count and exact error propagation; its ordered selection passes three tests and the
native/adoption files pass 165 tests, one existing skip, at 100% new-module coverage. Predecessor
cleanup is being repaired as well before rerunning aggregate gates. No runtime refusal was relaxed,
and this cause is not attributed to the older intermittent registry/ciphertext failures.

The predecessor now restores original attribute absence after fault scope and asserts exact initial
state. Its new assertion failed before repair; ordered verification passes three tests. Additional
machine-local drive cases reproduce four failures/five passes before `GetDriveTypeW` refusal is wired
ahead of resolution/target access. Remote cases are injected, not real connections; native local
positives remain. Final native/adoption selection passes **174 tests, one existing skip**, with
100% adoption coverage and passing lint/typing/syntax. Source is frozen again; prior failed gate
artifacts are retained, and final security/full-suite gates still need passing reruns.

Parent ordered native/adoption/registration/Make verification passes **217 tests, one skip**,
41.69 seconds, at 100% adoption coverage. The repaired mandatory security gate passes **1,684 tests,
77 skipped**, 2,579.33 seconds, 88.04% combined versus unchanged 85%. Full isolated Windows 3.11
then completes **one failed, 9,572 passed, 186 skipped, 20 xfailed**, ten warnings, 3,907.61 seconds.
The sole failure is Domain-neutrality scanning of the new module's `home` name; no native refusal
occurred. Core rates are 93.13% combined/94.31% line/89.92% branch, numerically passing but on a
failed-suite snapshot. XML contains exactly all 65 real core classes with no extras/duplicates; the
synthetic missing `changed.py` warning does not contribute a class. Naming repair and final gate
reruns remain required; source-drift verification did not run after the suite failure.

The sole architecture failure is repaired by renaming the new module's local `home` variable to
`user_directory`. No scanner allowlist/vocabulary, constants, messages or runtime checks changed.
The exact test failed before repair; the whole Domain-pack/native/adoption selection then passes
181 tests, one existing skip, at 100% adoption coverage. Parent architecture/adoption/registration/
Make verification passes **112 tests**, 34.39 seconds; both Ruff policies and strict typing of
65 core files pass. All source is frozen for final security/full-suite reruns.

## Native continuation — 2026-10-27

The diagnostic full Python 3.11 rerun completed **eight failed, 9,438 passed, 186 skipped, 20 xfailed**,
11 warnings, 3,393.58 seconds. Its failures were one stale generated-register snapshot and seven nested
pytest failures caused by the session-only diagnostic plugin importing absent application code in synthetic
checkouts. No native registry/ciphertext refusal occurred in this invocation; the previous intermittent
refusals remain unexplained. Regenerated registers and outer-checkout-only diagnostic instrumentation pass
the complete affected development selection: **76 passed**, 39.68 seconds, with the plugin enabled.
The fresh full isolated Windows Python 3.11.15 suite exits 0: **9,446 passed, 186 skipped, 20 xfailed**,
11 warnings, 3,135.50 seconds, without a competing suite. Both Ruff checks and strict typing of all 64
core files pass. The unchanged mandatory security gate is now being rerun; core/security coverage gates
remain open. This test-only pass does not establish a cause for earlier intermittent native refusals.
Native recovery, independent acceptance, owner UAT and cutover remain unperformed.

The actual 3.12 native repair matrix with installed Make/Git utilities on process-only PATH reported
one failure and 375 passes: generated Bash hook Python decoded Unicode as `cp1252`. The producer now
selects `-X utf8` in both shell variants. Isolated hook verification with child `PYTHONUTF8=0` passes
14 tests each on actual Windows Python 3.11.15, 3.12.15 and 3.13.15, no skips. The full repaired 3.12
hook/credential/release/delivery/privacy matrix now passes **376 tests**, no skips, 152.78 seconds.
The minimum-runtime Work foundation/publication/cache/registration and existing isolated-runner selection
passes **171 tests, one skip**, 76.95 seconds. It does not include the separate private-verification tests.

Opted-in private source verification is implemented and hardened against synchronized evidence destinations,
inaccessible source metadata, symlinks/reparse points and nonregular sources. Its 65 focused tests pass with
95% runner coverage. It preserves default copying/ignore rules, captures metadata only, records failure
outcomes and refuses cleanup failures. Actual source-pinned private producer evidence is still pending final
source freeze; no ordinary public CI result claims those private paths. No live enrollment or recovery.
Parent review additionally found ignored scratch copies would still sit beneath the synchronized checkout;
Git ignore does not prevent sync. External non-synced temporary workspace validation is required and being
repaired before actual private-source verification.

That repair is complete: final tooling selection **72 passed, 96% coverage**, source frozen. Parent actual
opted-in private source verification ran twice and exits 0 on native Windows Python 3.13.15, binding source
hashes and the exact 17-case synthetic producer selection with verified cleanup. It does not expose pytest
contents or run provider calls. Parent minimum-runtime tooling/registration selection: **80 passed**.
The agent accidentally truncated nine newly authored private test cases, then reconstructed/reran them;
these are not recovered original cases or independent product acceptance.

Recovery source is frozen after the first-store lock-discovery repair, explicit reviewed lifecycle CLI,
narrow Work exception translation and cache-export/staging exclusions. Agent focused aggregate:
**996 passed, 80 skipped**; final regression selection: **12 passed**. Parent strict mypy passes **64
core source files** in the canonical validation environment. The agent's separate environment reported
three discrepancies in existing `user_config.py`; canonical typing did not reproduce them.

The owner explicitly approved holding Agency subprocess and DC-3 snapshot/rollback for initial enrolled
Work use, with degradation disclosed. Arbitrary host/model file tools are not confined; manual edits cause
subsequent drift refusal, not prevention. No live enrollment or recovery occurred.

The first integrated security snapshot fails at **84.17% versus 85%**, with **1,352 passed, 77 skipped**,
seven warnings, 2,067.99 seconds. Existing checkpoint-CLI tests exercising new Work recovery were missing
from the selector; a canary failed first, then that test file was appended without removing existing
selectors/floors. Registers are regenerated and checked. Full minimum-runtime Windows, expanded security
and core line/branch gates are running against copied source; early failures remain unclosed until complete
diagnostics. These invocations are not yet passing evidence. Current POSIX execution, independent native
recovery/evaluation/workload evidence, owner UAT and explicit cutover remain required.

Expanded frozen security invocation completed: **two failed, 1,618 passed, 77 skipped**, eight warnings,
2,747.82 seconds; **87.82%** coverage reaches the floor, but the gate exits 1. Failures are a native registry
read refusal during credential migration and changed physical ciphertext metadata while streaming a large
quarantine member. Both exact cases pass in a fresh isolated two-test reproduction. They remain unexplained
intermittent aggregate failures; no assertions or security checks were weakened. Complete affected suites
are being rerun with coverage. Canonical full-core strict mypy still passes all 64 files.

Frozen full Windows Python 3.11 result: **one failed, 9,444 passed, 186 skipped, 20 xfailed, one error**,
12 warnings, 3,355.03 seconds. The definite failure is an older Work quarantine CLI test that now reaches
authenticated inspection without an injected passphrase; its fixture must assert the real unbound-Work
refusal rather than an incidental captured-stdin error. The fixture error is the same intermittent native
registry read refusal during curated runtime construction. Complete credential/quarantine affected suites
with coverage pass **190 tests**, 473.01 seconds. Native low-level cause investigation is bounded and active;
no retry, skip, integrity-check removal or passing final aggregate result is claimed.

Frozen Windows core snapshot: **one failed, 4,060 passed, 109 skipped, one error**, 34 warnings,
3,446.29 seconds. Rates are **92.98% combined, 94.22% line, 89.64% branch**; the separate rate checker
exits 0, but failed tests prevent a passing core gate. Failures are the same outdated Work-CLI fixture
and intermittent native registry read refusal (this time in cutover-fence fixture construction).
No platform module was excluded and no coverage reports were merged.

Changed-line measurement against that failed-test core XML passes **10,766/11,583 lines (92.95%)**.
It includes untracked core source through a disposable alternate Git index; the real index is not used
for staging, and the alternate index is removed afterward. This is coverage evidence only, not a passing
test gate. Post-documentation actual working-tree and unpushed-commit privacy scans both exit 0.
Frozen register/security-selector canaries pass **11 tests**. The repaired Work CLI fixture is being
verified on actual Windows Python 3.11: **10 passed**, 247.67 seconds. Native intermittent-cause
investigation remains active; no final aggregate rerun is yet reported.

The bounded native investigation made no repository change: single-process loops, eight-process stress,
305 affected-file tests and six isolated loop tests all pass. Repeated reads did not change ChangeTime or
LastWriteTime, so removing metadata integrity comparisons is unsupported. Actual low-level failure causes
remain unknown. A diagnostic-only session pytest plugin captures hidden native exception chains and
per-file snapshot differences; copied-source import smoke passes. One repaired full Python 3.11 suite
is running with that instrumentation and no competing suite. No current passing aggregate outcome is claimed.

## Windows systematic continuation — 2026-10-26 (latest)

Work continues on existing local `main`; inherited modifications and untracked native source/tests
are retained. No push, production recovery, key operation, provider effect, owner UAT or cutover occurred.

| Stream | Current verified result and limit |
|---|---|
| Native hook encoding | Reproduced parent `cp1252` input failure, then child Unicode corruption after fixing only the parent. The fixture uses explicit UTF-8 and forces child UTF-8 mode off; generated PowerShell commands now select Python `-X utf8`. Agent's focused result: 13 passed, one skip. No real hook installation or host-agent UAT. |
| Release evidence | Malformed nested release JSON caused attribute errors and permissive label/boolean coercion. Narrow mapping/list/identity/boolean validation now refuses with `PermissionDenied`; numerical gates and valid v4 shape remain. Agent results: 215 acceptance tests and 41 adjacent tests passed. |
| Actual Windows Python 3.11 | Existing external validation environment is CPython 3.11.15; `uv pip check` verifies 88 compatible packages. Isolated hook, release-v4 and artifact-coherence selection: **273 passed, one skipped**, 94.88 seconds. This is focused minimum-runtime evidence, not a full native suite. |
| Synthetic workload | Windows 3.13: 30/120/480 Signals complete cold in 0.961/3.177/12.135 seconds and warm in 0.072/0.172/0.632 seconds. Every warm sample makes zero model requests. Largest/smallest cold per-Signal ratio: 0.79. Deterministic local model and generated input only; not provider latency, p95, monthly cost or real-world acceptance. |
| Work recovery decision | Owner explicitly approved same-host-only Work recovery with lost-host activation held. WP-18 and baseline dated amendments supersede the unapproved independent Work custody proposal. Existing current host privacy authority, profile/host/installation checks, installed keys, replay and inactive fences remain required. Implementation and synthetic acceptance are in progress. |

Deeper Work-source inspection corrected the initial assessment: there is no authenticated original
Windows-profile/installation binding or Work current-history replay path in the existing setup. Existing
personal privacy/quarantine primitives refuse Work. No restore hold was removed. The owner subsequently
selected building the missing **local-only Work identity/privacy-history protocol**, not a new independent
custody destination and not an inspection-only scope reduction. Its concrete contract is being designed;
same-host normal restoration remains held until that contract and implementation are verified.

The owner then approved the concrete local protocol: whole-member historical invalidation, journaled
supported writers, manual-drift refusal and review-gated encrypted quarantine. The new protocol foundation
is being implemented with injected native identity/key storage; writer inventory is complete but producer,
cache and recovery integration is not yet complete. No live enrollment occurred.

Actual full isolated Windows CPython 3.11.15 baseline: **42 failed, 9,220 passed, 187 skipped,
20 xfailed**, 11 warnings, 3,055.46 seconds. This is a failed baseline, not a cutover gate.
Thirty-five failures are native delivery/canary fixture/tool-path failures; GNU Make already exists at
the user-local GnuWin32 path but was absent from PATH. A package-manager check found the existing package;
no dependency was installed/upgraded. Other failures include explicit UTF-8 fixture/child-process handling,
stale generated registers in the copied snapshot and one credential-migration process-death race.
The Unicode fixtures were reproduced and repaired without weakening fence assertions; actual 3.11
legacy-writer/VM/credential regression selection now passes **93 tests, eight skipped**, two warnings.
The migration death case passes independently but remains under investigation; an isolated pass does not
close the full-suite failure. Generated registers were refreshed after the baseline copy; follow-up
delivery tests still fail until the native fixtures/tools are repaired.

Subsequent bounded closure: delivery/privacy fixtures and the scanner's actual `cp1252` reporting defect
are repaired; **85 tests pass on each of native Windows Python 3.11 and 3.13**, with no skips. The credential
fixture now identifies, terminates and reaps the actual Windows interpreter worker before closing stdin,
rather than killing only the venv launcher; **100 tests pass, eight skipped** in its bounded selection.
The failed full baseline is retained until an aggregate rerun. The repaired actual tracked privacy scan now
exits 0. The Work privacy foundation passes 68 focused tests; producer/cache/recovery integration remains
active. Both new authority modules are selected by the mandatory security harness; tracked producer bytes
join the public release fingerprint, but intentionally ignored employer wiring does not. No live enrollment
occurred. The owner approved retaining private wiring with separate local source-pinned integration evidence.
An explicitly opted-in, allowlisted isolated verification path is in development; shared CI is not evidence
for private producers. Enrolled agency/DC-3 reflection paths currently refuse unmediated writes, degrading
those capabilities rather than silently bypassing publication. Their limitations remain visible.

A direct registration selection passed eight assertions but exited 1: the production-path guard observed
another isolated workspace being cleaned beneath repository `tmp/`. Treat this as a failed invocation
and rerun using the isolated runner. Do not waive the guard or relabel exit 1 as a pass. A separate native
Windows CPython 3.12.15 environment is provisioned; installed dependencies pass `pip check`. Its isolated
protocol/registration selection now passes **76 tests**. The first integrated full-core mypy invocation
found seven new recovery-integration errors; repair is active, so typing is not yet a passing gate.

The earlier complete Windows 3.13 mandatory security snapshot exits 1: **one failed, 1,258 passed,
78 skipped**, seven warnings, 2,303.60 seconds. Combined coverage reaches **88.13%**, but its descendant
credential-worker death test fails. The snapshot copied that fixture before its deterministic fix and
predates new Work authority/integration bytes. No security module was excluded and no coverage reports
were merged; a final integrated harness run remains necessary.

Current intermediate static checks: repository Ruff and stricter core Ruff pass; Python compile checks
and example-profile JSON Schema validation pass. Import checking passes across 429 files. The write
ratchet fails on newly duplicated fallback writes in five producer files; repairs must reuse existing
writers, not increase its baseline. Both actual working-tree and unpushed-commit privacy scans exit 0.
There is no `scripts/verify_specs.py` in this checkout, so no specification-verifier result is claimed.
These observations precede the remaining integration fixes and do not replace final source-frozen gates.

The earlier interrupted security invocation produced no complete measurement. Its reproduced encoding
failure is repaired, but the complete current-source security/platform/changed-line gates still need
execution after the recovery changes. The 2026-10-25 failed Mac security measurement is historical,
not a passing gate. Cross-host union semantics remain unapproved. Mac-personal and independent personal
OneDrive recovery, independent held-out quality/document/Goal/cross-Domain evaluation, required workload
measurements, owner UAT and explicit release remain acceptance dependencies. Same-host Work scope
does not manufacture any of those observations or permit activation after losing current Work authority.

## Renewed blocker closure — 2026-10-25 (latest)

The owner requested another parallel completion pass. Existing local main, all inherited dirty work,
the real index and all release holds remain. The following is additional engineering/verification,
not a declaration that the preceding failed security gate, native recovery or owner acceptance passed.

| Stream | Current result and exact boundary |
|---|---|
| Mac minimum runtime | Provisioned a separate external `~/.artha-venvs/.venv-mac311-validation` using actual CPython 3.11.15 and normal verified TLS. All 88 packages passed `uv pip check`; the application venv/editor were unchanged. CI-matched contracts: **3,954 passed, 192 skipped**, two warnings, 311.23s. Log: `/tmp/re-artha-mac311-contracts.log`. |
| Mac 3.11 full suite | **9,042 passed, 264 skipped, 20 xfailed**, ten warnings, 431.75s. Log: `/tmp/re-artha-mac311-full-final.log`. This copied source after the POSIX output-size repair, but before the later capsule-load repair and portable test move. It is not a test of those later bytes. |
| POSIX recovery size | Reproduced unbounded serialized ledger publication despite the Windows output ceiling. Reject oversize before touching the original; equality accepted. Reproducer failed first, then passed; recovery-access file **100 passed, three native-Windows skips**. |
| Ciphertext reread | Added a valid same-payload/different-ciphertext final-read substitution regression. The combined recovery batch **101 passed, three skips**; no plaintext result can return after substituted bytes. This is a synthetic fence test, not native recovery acceptance. |
| Privacy replay bound | Authenticated capsule loads lacked the publication plaintext ceiling before JSON parsing. New over-limit test failed first; enforce the existing ceiling, allow equality, preserve head/ciphertext on refusal. **53 tests passed on 3.14**, including portable syntax cases, and **45 privacy tests passed on 3.11**. |
| Portable policy coverage | Moved eight unchanged pure Windows path-syntax rejection cases out of the native module's blanket skip; all eight pass on Mac without native initialization or platform stubs. Genuine DACL/handle/lock/death tests retain native-only execution. This does not close native acceptance or manufacture the 85% floor. |
| Mac 3.12 | Native CPython 3.12.13 in a separate external validation venv; 86 packages pass dependency checks. Full isolated suite: **9,052 passed, 256 skipped, 20 xfailed**, nine warnings, 442.56s. Log: `/tmp/re-artha-mac312-full-final.log`. Includes both production bounds repairs and portable test move; precedes the later selector/documentation update. An initially partial log view was not a completed failure or a proven hang; the final report and environment were verified directly. |
| Security registration and gate | The canary failed first, then passed after adding `privacy_history` and its existing capacity tests to the mandatory control selector. No module/floor was removed. Current harness: **1,179 passed, 156 skipped**, eight warnings, 156.26s; **FAILED at 73.53% combined versus 85%**, Make exit 2. Log/data: `/tmp/re-artha-blockers-security.{log,coverage}`; ignored XML: `coverage-harness.xml`. Windows controls remain selected; no union or exclusion was applied. |
| Current core coverage | **3,770 passed, 175 skipped**, 34 warnings, 298.35s; **89.97% combined, 90.81% line, 87.68% branch**, passing all existing 85% floors. Retained log/XML/data: `/tmp/re-artha-blockers-core.{log,xml,coverage}`. Includes both bounds repairs, portable tests and selector canary. |
| Current changed-line gate | **9,338/10,714 lines covered, 1,376 missing (87.16%)**, passing 85%. Uses current automatically normalized XML and a disposable alternate index explicitly including the untracked native backend. The real index hash is unchanged. Log: `/tmp/re-artha-blockers-diff.log`. This larger measured set supersedes the earlier 9,903-line result; do not rely on the earlier claim that its diff established native-module inclusion. |
| Final local static/evidence/privacy | **61 affected gate/register tests passed**; generated registers refreshed/checked. Syntax, both Ruff configurations, imports (427), write ratchet (147), strict typing (63 core/three credential), profile schema and whitespace checks pass. Current tracked/untracked-nonignored bytes, unpublished history and staged privacy pass with real gitleaks. |

Full 3.12 evidence does not certify subsequent selector/documentation edits; the final focused gates above
cover that follow-up. The core and expanded security measurements use the final production repairs.
Dependency/deprecation warnings remain visible; no runtime selection, Work state, native key or provider was
changed. Package/runtime downloads used verified TLS, with no privilege escalation or insecure fallback.

### Decisions are specified, not silently adopted

The renewed approval questions again returned only user unavailable. The existing active feature specs now
contain clearly **UNAPPROVED** Given/When/Then proposals, including data model, alternatives and risks:

- WP-17 native-platform evidence: identical source/test/configuration identities, retained raw native reports,
  explicit path maps and compatibility checks; any union requires separate approval. All selected modules and
  existing 85% combined/core line/core branch/changed-line floors remain; no new per-module floor is invented.
- WP-17 applicability: distinct purpose-specific exact signed owner review, finite prospective `[from, until)`
  bounds, version/evidence/consent binding and immutable history. Field review, assignment convention and action
  approval remain different operations. Token acceptance expiry is separate from an accepted assertion's interval.
- WP-18 Work privacy: independent employer-permitted custody and authenticated currentness/continuity outside
  the lost host/checkpoint, with CAS/crash/rollback rules. Destination remains **UNSELECTED**; old signatures,
  recent timestamps, personal heads and checkpoint possession cannot supply current Work authority.

These proposals authorize no implementation, live copy or activation. The existing capsule/head verification
primitives are reused and hardened; no duplicate generic Work verifier or second permission authority was added.
Current-source Windows/Ubuntu execution, reviewed coverage semantics, actual recovery/evaluation/workloads,
owner UAT and explicit cutover remain open. No commit, push, branch/worktree, consent change or release occurred.

Pilot scope clarification: **finite prospective applicability is optional, not a narrow-pilot release blocker**.
The approved preview-only/action-held pilot may retain unknown applicability and the existing consequential
effect/stop-condition refusal. Do not require a new owner-confirmation workflow merely to ship that scope.
Automatic intervals and later responsibility/effect workflows remain separate destination work; required
pilot quality outcomes still need their actual evidence. Coverage union is also optional as a method, but
passing the existing numerical/platform gates is not. Work recovery is a genuine required outcome: the
current verifier requires independently current Mac-personal, Windows-Work and personal-cloud-copy recovery
observations. Inspection-only Work containment cannot satisfy that receipt. Removing it requires an explicit
scope amendment, not a reinterpretation of the pilot or an unavailable-owner reply.

**Actual stop condition:** local authorized repairs and Mac 3.11/3.12 validation are complete for this pass;
the overall task is still blocked. Resume current-source native Windows/Ubuntu validation and review coverage
semantics if needed; then independently current Work custody/recovery, explicitly scoped personal/cloud
recovery, held-out quality/document/Goal/cross-Domain and workload/provider evidence, owner UAT and cutover.
The owner must identify a permitted independent Work privacy destination or explicitly amend recovery scope.
Do not invent that permission, a passing security gate, live outcomes or signatures while the owner is away.

## Mac continuation audit and repairs — 2026-10-25 (latest)

This section supersedes the execution allocations and outstanding-engineering lists below, not their
historical results. Takeover found local `main` at `a7d3686`, 79 unpublished commits, one branch/worktree,
78 modified tracked files and four untracked implementation/test files. All were preserved. This pass
created no branch/worktree, changed no real Git index, committed/pushed nothing, and accessed no live
household payload, credential store, provider or recovery dataset. The incumbent remains authoritative.

### What is implemented and what is not

The 25-row requirement matrix below remains the detailed disposition: typed evidence/coverage, processing
closure, encrypted persistence, corrections/forgetting, permissions, scoped inspection, Goals, household
coordination, source administration, provider lifecycle, resource admission, governed quarantine/recovery
and bounded actions have implementation and synthetic tests. The native Windows backend is present in
the inherited working tree; it is not still an unimplemented Mac task. None of this proves independent
quality, current-source native recovery, provider outcomes, owner UAT or release acceptance.

This was a bounded source/test audit, not a proof that no other defect exists. Parallel reviewers owned
core semantics, security/recovery and release/gates, followed by disjoint repair streams and independent
review. Only one stream executed terminal commands at a time after the initial read-only audit.

### Reproduced defects repaired

| Boundary | Defect and current repair |
|---|---|
| Fact precedence | A weak source revision erased stronger corroboration; equal-rank revision also erased independent equal-rank support. Arbitration now checks all competing values and relevant independent support. |
| User correction | A dispute erased `user_confirmed` provenance, and same-value promotion could bypass a conflicting confirmation. Preserve confirmation identity and evaluate conflicts before promotion. |
| Corroboration | Reliable same-value evidence left hypotheses unknown and stronger matching evidence could not resolve disputes. Resolve eligible values in place, preserve identity/history, and supersede defeated alternatives. |
| Appointments | Disputed datetime Facts became actionable cross-Domain conflicts. Surface disagreement without treating the appointment as established. |
| Source time | Reconciliation restamped observation with Catch-up time and dropped occurrence. Retain Signal source times, expose version-specific unknown validity, and leave unestablished applicability unknown. |
| Missing years | Extraction used ingestion time as an evidence-year anchor; document upload time impersonated occurrence. Only source occurrence anchors omitted years; explicit complete dates remain usable. Synthetic positive fixtures now state their intended source chronology. |
| Legacy restore | The old snapshot restore published without a trustworthy inactive/privacy fence. It now refuses before reads/writes and directs callers to governed checkpoint/quarantine recovery. Snapshot creation/reading and independent in-memory privacy replay remain tested. |
| Atomic publication | Predictable temporary names followed symlinks and truncated a sentinel. Exclusive unpredictable temporary descriptors retain fsync and atomic replacement. |
| Credential migration | An `AE1` prefix alone produced successful no-op migration. Authenticate every presealed input with its existing scope key and filename AAD; never manufacture replacement keys. This proves authenticated input, not live credential readiness. |
| Runtime drift | Connector bytes were in initial cohorts but absent from repeated runtime-input checks. Pin and recheck them alongside household configuration before effects/publication. |
| Privacy gate | Untracked nonignored working bytes escaped scanning. Include them, scan captured bytes through gitleaks stdin without disk payload copies, preserve exact reviewed path exemptions, and fail closed on unsupported policy/scanner errors. |
| History/CI | New-ref subtraction could omit the whole pushed history; first-main changed-line comparison could be empty. Scan reachable history for new refs and compare first-push coverage against the empty tree. Provision `age` in required native CI jobs. |
| Coverage fidelity | Untracked core code was omitted and isolated XML referred to deleted source paths. Refuse untracked omissions, normalize newly emitted XML source roots before cleanup, and reject empty coverage matches when changed Python lines exist. |
| Host fixtures | PowerShell on POSIX searched Windows-only venv paths; a Windows mock required an absent POSIX `O_BINARY`. Preserve real host-native interpreter execution and explicit native handle/flag assertions without new skips. |

Exact field confirmation remains reusable factual support, not proof of temporal applicability or action
approval. Unknown-validity evidence still refuses consequential action preconditions. Existing OCR evidence
can conservatively retain its exact-field-review requirement after corroboration; no automatic review
bypass or general validity-interval extraction protocol was introduced.

The suspected final quarantine check-to-replace race was narrowed by independent inspection: supported
writers hold directory/profile locks across publication. Lock-bypassing same-user substitution remains an
unproven interference concern, not a confirmed supported-writer exploit or a claimed atomic filesystem CAS.
No speculative native protocol patch was made on this Mac.

### Actual validation and limits

| Gate | Observed result |
|---|---|
| First aggregate | 8,989 passed, nine failed, 263 skipped, 20 xfailed. The failures were repaired and are not relabeled successful. |
| Final full isolated suite, macOS Python 3.14.6 | **9,042 passed, 263 skipped, 20 xfailed, nine warnings**, 627.39 seconds; exit 0. Log: `/tmp/re-artha-mac-suite-final.log`. Restricted Work tests remain excluded by repository policy. |
| Core isolated tests and coverage | **3,757 passed, 183 skipped, 34 warnings**; **89.86% combined, 90.71% line, 87.53% branch**; independent 85% floors pass. Log/XML/data: `/tmp/re-artha-mac-core-final.{log,xml,coverage}`. |
| Mandatory security harness | **FAILED: 72.49% combined versus 85%**; 1,123 passed, 164 skipped, eight warnings. Native Windows code remains selected and cannot execute natively on this Mac. Log/data: `/tmp/re-artha-mac-security-final.{log,coverage}`; ignored XML: `coverage-harness.xml`. No exclusion or cross-host combination applied. |
| Changed-line coverage | Original stock invocation falsely exited 0 with no matched lines; rejected as evidence. Path-only normalized retained XML plus guarded checker sees **9,903 changed lines, 759 missing (92.34%)**, passing 85%, including the untracked native module through a disposable alternate index. Real index unchanged. Report: `/tmp/re-artha-mac-core-final.normalized.xml`. |
| Static/evidence | Syntax, both Ruff configurations, import convention (427 files), write ratchet (147), strict typing (63 core/three credential modules), profile schema and regenerated cohort checks passed before the final suites. |
| Tooling follow-up | Coverage-path normalization/empty-match guard was added after those full/core/security snapshots; **51 focused development tests passed**, including real isolated report-to-diff checks, missing reports, docs-only changes and synthetic Windows/spaced paths. This is not another full-suite or native observation. |
| Final documentation/tooling/privacy | **61 affected development/register/manifest tests passed** after documentation and cohort regeneration. Syntax, both Ruff configurations, imports/write ratchet, strict typing and example schema passed again. Current tracked/untracked-nonignored bytes, unpublished commits and staged content passed with real gitleaks; whitespace check passed. |

The numerical override is 85%, not a waiver of platform controls. The Mac security gate remains failed;
the earlier passing Windows harness belongs to its earlier source cohort. Warnings include dependency,
deprecation and synthetic SQLite ResourceWarnings; their presence is not hidden or evidence of a live failure.

### Shortest remaining parallel plan

| Owner / order | Remaining work and exit evidence |
|---|---|
| Integrator / now | Preserve exact source, registers and private active specs; verify final tooling/documentation/privacy. Keep local main and the real index intact. No source change is authorized merely to manufacture green coverage. |
| Native validation / next | Execute these exact implementation/test bytes on Windows, including changed PowerShell fixtures and native security selectors; record module identities, skips and reports. Current-source macOS/Ubuntu 3.11/3.12/3.14 and required Windows selections remain the platform contract; this pass establishes Mac 3.14 only. |
| Coverage decision / owner review | Review an identical-source supported-platform coverage protocol before combining reports or changing host gate semantics. Until then the 72.49% Mac harness failure remains open. No historic report merging, platform exclusion or threshold reduction. |
| Work recovery design / owner review | Select employer-permitted independent current privacy custody, authenticated head/history, freshness/rollback/failure rules, and native acceptance scope. Normal Work restoration and lost-device activation remain held; personal OneDrive cannot substitute. |
| Temporal contract / owner review | Define evidence-backed validity intervals, missing/bound semantics and interval-aware reconciliation before adding them to model output. Current implementation preserves unknown applicability; it does not infer perpetual validity or backfill old source times. |
| Recovery acceptance / parallel after explicit scope | Review historical dependency/native-key inventory; perform Mac-personal, Windows-Work and independent personal cloud-copy recovery/rollback with current privacy continuity. No live/key/recovery authorization was inferred from this takeover. |
| Independent evaluator / parallel | Consented held-out/source-negative P1/P2, person/time/negation, Goal-correction and two named cross-Domain outcomes; separate consequential document dates/amounts/names >=99%, false confirmations <=0.5%. Bind actual outputs and labels to the frozen cohort. |
| Workload/provider evaluator / parallel | Normal/Digest/long-gap/reset/one-/two-year cases, >=95% completion, approved p95/cost/review-effort bounds and exact-approved supported reminder outcomes if effects are to be enabled. Admission quotas do not establish actual billing. |
| Owner / final | Review exact artifacts, UAT, preservation and rollback; explicitly authorize narrow-pilot cutover. Keep held action families, sync, broad expansion and automatic expiry disabled; keep active specs unarchived. |

Both the native-coverage and Work-privacy questions received only an unavailable-owner response. That is
not approval for a new custody protocol, coverage semantics, host spike or release. The current blocker is
not a need to rebuild all 25 requirements; it is the explicit unfinished contract/platform/acceptance work
above. Older plans below must not be used to duplicate completed Windows implementation or invent UAT.

Coverage review recommendation, **not adopted**: retain all control modules and the 85% floor; require
identical implementation/configuration/test hashes, native Windows and POSIX execution, original reports
plus auditable path mapping, and an explicit decision on union versus per-host denominators before a
combined report can count. Independently require every platform's tests and native controls to pass;
coverage union alone cannot establish platform acceptance. Until reviewed, do not change `make check`.

Work recovery alternatives, **not selected**: (A) employer-approved independently retained authenticated
privacy history/head, outside the restorable Work set, with freshness and rollback refusal; (B) retain
inspection-only recovery and explicitly limit any future pilot's recovery scope. Recommend A only after
the owner identifies a permitted custody destination; otherwise keep the current hold. A personal cloud
copy, checkpoint contents or passphrase is not an alternative current-privacy authority. Selecting B does
not waive existing release requirements without a separate scope decision.

## Owner-approved coverage revision and expedited completion — 2026-10-25

The owner explicitly lowered coverage to **85%** to expedite completion. Core combined/line/branch,
changed-line and security-harness combined defaults now use 85%; the standalone checker, Makefile,
CI labels and canonical technical contract agree. All tests, control-module selectors and platform
requirements remain. No automatic coverage exclusions, cross-host combination or release bypass was added.
The pre-change regression had two failures/two passes; afterward all **22 development canaries passed**,
including exactly 85% acceptance, independent 84.99% line/branch rejection and native harness selection.
Applicable Ruff passed. Historical results below retain their original thresholds and dispositions.
Both complete Ruff configurations subsequently passed; generated registers were refreshed and checked.
The release-fence/artifact-coherence/register selection passed **134 tests** in **227.30 seconds**.
It confirms that lowering engineering coverage does not bypass missing/revoked recovery or release evidence.
The actual modified/untracked-byte privacy scan passed for **82 files**, and the tracked-file and
unpublished-history scans passed (exit 0, real gitleaks available). An initial test invocation used a nonexistent manifest-test
filename and collected no tests; the corrected invocation above is the passing evidence.

The latest security measurement of **87.69%** exceeds the revised numerical floor, but its original
95%-target invocation still failed; it is not relabeled a passing command. The retained report is usable
numerical evidence, with the already-recorded source snapshot and missing-XML limitations.
The clean core invocation has now finished: **3,786 passed, 109 skipped, 33 warnings**, in
**3,476.54 seconds**, with **93.18% combined** coverage. Its original 95%-floor command exited 1;
it remains recorded as failed against that former target. The retained XML was separately checked
with `--minimum=85 --branch-minimum=85`: **94.26% line, 90.23% branch**, exit 0.
Combined coverage also exceeds 85%. The unchanged changed-line tool, evaluated at the new
`--fail-under=85` against `origin/main`, passed: **656 uncovered of 9,876 changed lines**
(93% displayed), exit 0. It still omits the untracked native backend; that module's separately verified
97.28% line/91.12% branch evidence covers the omission, not the diff report itself.
The core snapshot includes current production repairs and recovery-boundary tests, but predates the
subsequent coverage-default/canary/documentation revision; the 22 canaries and regenerated-register
checks validate that revision separately. These measurements clear the revised numerical coverage
floors, not current-source POSIX/minimum-version execution, real recovery or release acceptance.

A native synthetic benchmark completed six cold/warm Catch-ups with no live data, network or real model:
30 Signals took **0.901/0.072 seconds**, 120 took **3.058/0.178 seconds**, and 480 took
**12.559/0.701 seconds** (cold/warm). Every result was complete; warm Catch-ups requested no extraction.
The measured per-Signal scaling ratio was **0.87**. This is local synthetic evidence, not a real workload
p95, actual provider cost, long-gap acceptance or independent product-quality evaluation.

### Continued release engineering — 2026-10-25

The `py` launcher was not a complete interpreter inventory. `uv python list --only-installed` found an
existing managed **Python 3.11.15**, confirmed by executing that exact binary. A separate external
`.venv-win311-validation` was created; the existing application venv and global interpreter were not changed.
The first uv dependency attempt failed TLS negotiation, as did retrying with OS certificate trust.
An offline attempt lacked the pinned PyYAML wheel. Existing pip used the configured trusted package feed;
its CalDAV/vobject metadata returned 404. Restoring those exact pinned cached wheels, then resolving the
complete requirements through the configured feed succeeded (exit 0). TLS was never disabled.
The existing Windows CI core/native credential/action selection was validated with Python 3.11.
The invocation finished with **4,004 passed, two failed, 111 skipped, two warnings** in
**3,087.29 seconds**, exit 1. Both failures were generated-hook subprocess fixtures: symlinking
only the managed base `python.exe` into an unrelated directory omitted its DLL/runtime layout
(shell exit 127 / native exit 3221225781). A focused rerun reproduced both failures.
The fixture now creates a real `venv --without-pip`; Git Bash on Windows uses a quoted shell
launcher into that venv, while PowerShell executes its native interpreter. No product hook
behavior changed. All **14 hook portability tests passed on Python 3.11.15**, then all **14 passed
on Python 3.13.15**; applicable Ruff and refreshed generated-cohort checks passed.
This closes the reproduced minimum-runtime fixture failures with focused evidence, not by
retroactively relabeling the failed aggregate. The worker did not retain the requested aggregate
log; its counts are the worker's returned result, while the focused reruns have direct tool evidence.
The separate venv's 88 installed packages pass `uv pip check`. The aggregate warnings were a
Requests dependency-combination warning and Google's future Python 3.11 support warning.

A failing canary reproduced the security harness's lost XML evidence. The harness now requests XML
inside pytest, before isolated-source cleanup, at an absolute path in the invoking directory.
An additional failing Windows path-with-spaces case required quoting that path. All **24 development
canaries passed** after both repairs, including report survival and 85% boundary checks. The report is
ignored by Git. Applicable Ruff and example-profile schema checks passed. Syntax, import checks
(427 files), state-write ratchet (147 legacy writes), strict typing of all 63 core files for Windows
and Linux platforms, and strict typing of the three credential modules also passed. Linux-platform
typing is not POSIX runtime execution.
Final generated cohorts were refreshed and checked after the harness-canary change; all seven
register/manifest tests and both full Ruff configurations passed. Privacy passed for all **83**
current modified/untracked files. The minimum-runtime validation copied source before that final
test-cohort regeneration; no runtime implementation changed during the regeneration.

After the hook-fixture repair, the exact Windows Python 3.11 CI selection was started again with a
retained absolute `windows-python311-contracts-final.log` (shell `windows311-final-contracts`).
The repaired Makefile security target was also started at its actual new 85% default, with retained
`security-final85.log`/coverage data and its now-persistent ignored XML (shell `security-final85`).
These invocations are in progress, not passed. They close the prior missing aggregate-log and
old-threshold-command evidence limitations only if their completed results support that conclusion.

The repaired security invocation subsequently **passed**, exit **0**: **1,180 passed, 77 skipped,
seven warnings**, **2,472.43 seconds**, **87.92% combined** against the actual 85% Makefile default.
The absolute log/data were retained and `coverage-harness.xml` survived disposable-source cleanup.
Its XML reports **89.32% line / 83.93% branch**. The existing harness gate enforces combined coverage
only, so this is a passing harness gate, not a claim of 85% branch coverage. The independent core
line/branch gate remains separately satisfied. Seven resource warnings remain recorded, not attributed
to a particular owner from garbage-collection allocation locations alone.
The repaired Python 3.11 aggregate subsequently **passed**, exit **0**: **4,006 passed,
111 skipped, two warnings**, **3,139.78 seconds**. The exact Windows CI core/native
credential/action selection now has a retained `windows-python311-contracts-final.log`.
The two warnings are the Requests dependency-combination notice and Google's future Python 3.11
support notice described above. This closes the minimum-supported Windows execution gap for that
selection; it is not a full-suite Python 3.11 pass, POSIX execution, native credential/recovery UAT
or owner release acceptance. No additional source repair was required by this final invocation.

An explicit request for real-host recovery scope and a POSIX host received no owner answer. No live
scope or host access is inferred. Real Work/current-privacy recovery and independent owner acceptance
remain blocked, not automatically waived by continued autonomous engineering.

Fastest remaining sequence:

1. Retain the passing revised numerical coverage/static/privacy evidence, finish source/test-cohort
   regeneration after the harness fix, and freeze the exact implementation. Do not rerun the entire suite
   merely to chase 95%.
2. Finish the in-progress minimum-Windows-Python invocation and execute the source on an approved real
   POSIX host. Existing Mac results are historical; host access is not inferred from the coverage override.
3. In parallel, obtain explicitly scoped native-key/dependency and Mac-personal/Windows-Work/cloud-copy
   recovery observations, and resolve independently current Work privacy before Work activation.
4. On the frozen cohort, complete consented independent quality/document/Goal/cross-Domain evaluation and
   real workload/cost observations, then owner UAT, rollback demonstration and explicit narrow-pilot cutover.

The numerical revision does not waive product-quality thresholds, privacy/permissions, recovery, platform
contracts or owner acceptance. Incumbent authority remains; no live recovery, provider effect, credential
installation, cutover, push or feature archival was performed.

## Windows takeover — 2026-10-25 (current)

The owner resumed this feature on the native Windows laptop and authorized parallel implementation of the
remaining approved work. This supersedes the Mac-side request for a Windows runner and the execution allocation
below. Baseline: local `main` at `a7d3686`, 79 commits ahead of `origin/main`, one branch/worktree. Inherited edits
to this document and `.github/copilot-instructions.md` are preserved. No pull into the dirty worktree, push,
production capture/restore, credential installation, consent change, provider effect or cutover is authorized
by these engineering checks.

### Audit conclusions and actual baseline

The canonical PRD, technical and UX contracts describe a broader destination than the first pilot. The accepted
re-artha pull-only, per-record authority and permission contracts take precedence over older scheduled-Digest,
earned-autonomy and ledger-owns-everything shorthand. The current goal is a safe narrow pilot, not all-Domain
migration. The latest dated evidence supersedes older missing-engineering tables in this document.

| Disposition | Current understanding and evidence limit |
|---|---|
| Implemented foundation, not release acceptance | Evidence/time/coverage, processing closure, one Briefing, corrections, permission/forgetting, scoped inspection, Goal completion/indicators/calibration, proposal-only tradeoffs, strict packs, source administration, scoped interchange and documentary confirmation are present in the inherited implementation. Do not rebuild them merely because older audit rows say missing. |
| Implemented but held | Provider cursor/reset/deletion mechanisms, exact action review/claims/reconciliation, authority transfer and resource accounting exist. Opt-in sync and unsupported action families remain disabled. Admission quotas do not establish actual cash billing. |
| Native implementation present; integrated gates pending | WIN-1/2 secure quarantine and its review/restore call chain, Windows filesystem/lock/process repairs and credential/profile/writer-fence integration have focused native evidence below. Current-source aggregate, coverage and platform parity still must pass; a POSIX pass or Windows skip cannot close these requirements. |
| Separate unresolved recovery work | Normal Work restoration is explicitly refused; independent current Work privacy and fresh-device activation are not implemented by passing a personal-profile Boolean. Passphrase-only inspection cannot install historical keys or grant authority. |
| Approved policy, not a new pilot blocker | Retain-all with automatic expiry disabled is explicit. General class-specific expiry and broader destination expansion are not requirements to silently add to the pilot. |
| Acceptance still required | Historical dependency review; Mac personal, Windows Work and independent personal OneDrive-web recovery; current privacy continuity; consented held-out/source-negative evaluation; document/Goal/cross-Domain outcomes; actual workloads/cost/provider results; owner UAT and explicit release decision. |

The first native isolated recovery baseline failed: **351 passed, 68 skipped, 30 failed, two setup errors**
(207.45 seconds). This is not a successful Windows gate. The checkpoint failures report unreadable inventory
items (`PermissionError`); Windows byte-range locking and capture must be investigated together, not bypassed.
The two setup errors came from a one-megabyte parameter ID overflowing Windows' 32,767-character environment
variable limit. Named compact fixture IDs fixed those setup errors; the complete credential-migration file then
passed **30 tests** on Windows. This changes fixture naming, not the oversized-input rejection.

Initial native quality checks also exposed missing local gate packages, POSIX-only attributes in Windows mypy
stubs, default-console encoding failure in the privacy command, and pre-existing generated-register drift.
Missing mypy/Ruff/coverage/diff-cover tools were restored to the existing external Windows venv after failed
checks; commands use `PYTHONUTF8=1`. No quality threshold, privacy exemption or runtime fallback was relaxed.

Additional reproduced Windows mistakes and verified repairs:

- The release implementation manifest omitted the new native filesystem module, and migration/prompt labels
  used host-specific path separators. Both new assertions failed before repair. The module/tests are now
  registered and manifest labels use portable repository-relative spelling. The register/capability slice
  passed 11 tests afterward. This intentionally invalidates older implementation cohorts; reevaluate rather
  than reusing old release evidence.
- Windows mypy did not narrow `os.name` guards around POSIX-only `getuid`/`fchmod`. Equivalent `sys.platform`
  guards preserve runtime behavior and make the registry pass its native strict type check. This does not
  add Windows registry DACL enforcement or prove native secret custody.
- New real Windows child-process cases terminate a synthetic registry writer before/after its synthetic
  effect, prove a contender and capture are excluded, preserve pending metadata, and require exact retry.
  The registry file passed 74 tests with 68 explicitly POSIX-only cases skipped. These are native synthetic
  process observations, not live credential/recovery UAT.
- Syntax, import convention and state-write ratchet checks passed. Tracked privacy passed. The unpublished
  commit scan initially refused a missing gitleaks executable; after installation through WinGet, the
  unchanged scan passed. No bypass was used.
- The registry/credential-only coverage attempt passed 141 tests with 68 skips but reached **93.36% combined**,
  below the unchanged 95% gate. This subset is not a passing coverage gate; final integrated coverage remains
  required. Credential migration alone measured 100%, credential storage 97%, registry 87% in that attempt.

### Current aggregate and follow-up evidence (not a release pass)

The first full Windows run finished with **8,249 passed, 476 failed, 21 errors, 181 skipped and
20 xfailed** in approximately 43 minutes. Its manually selected workspace was inside OneDrive; components
that deliberately reject synced storage therefore also failed. This result cannot certify Windows or be
discarded wholesale: quarantine, retention, native subprocess, development-gate and fixture defects were
reproduced independently. Future aggregate runs use the isolated runner's default system-temp workspace.

A focused checkpoint/curated-permission/source-configuration run in that default workspace passed 357 tests
with four failures and two oversized-ID errors; the earlier registry setup errors did not recur. The remaining
causes were Windows' valid refusal to rename an open authenticated checkpoint, a host-dependent absolute-path
test, OS-error payload exposure, CRLF translation during reviewed configuration publication and oversized
implicit pytest IDs. Repairs retain actual native rejection tests and exact authored-byte assertions rather
than skipping the Windows cases.

- A suite-wide bounded deterministic pytest-ID hook now hides large string/bytes payloads from test names,
  preserves ordinary pytest IDs and leaves parameter values unchanged. Its regression plus credential-migration
  slice passed **38 tests** after the failing reproducer.
- The integrated checkpoint CLI, curated-permission, source-configuration and payload-ID run subsequently
  passed **371 tests** in the default system-temp workspace. The final exact path/metadata-denial/native-rename
  subset passed **six tests** after the final OS-error redaction change.
- Configuration publication now preserves existing LF/CRLF bytes outside the reviewed replacement. The
  source-configuration and hook slice passed **102 tests, one skip** after explicit LF/CRLF tests and native
  PowerShell UTF-8 stdin repair. The skip is the unavailable POSIX shell, not a native PowerShell bypass.
- A separate native action/hook reproducer passed 66 tests with 21 failures and one skip. Action migration
  failures include `fsync` on a read-only descriptor; native subprocess failures remain under repair.
  The hook failures were separately reproduced and repaired in the passing slice above.
- Retention's focused native run now passed **134 tests, no skips**, including protected-DACL creation by new
  blob/replay producers, at-most-64-KiB same-handle streaming and identity/mutation checks. Historical unsafe
  files remain refused; no live migration was performed. Strict native typing/lint passed. Retention-only
  coverage measured **85%**, not the unchanged 95% gate; POSIX execution remains unverified here.
- Development canaries and synthetic Git privacy-hook tests now passed **35 tests** after installing the
  missing GNU make dependency and fixing native invocation/path handling. Actual Makefile gates and copied
  real privacy-hook bodies ran; no Windows skips or coverage/privacy waivers were added.
- Native shared/exclusive session locking now uses `LockFileEx`, including compatibility with incumbent
  byte-zero `msvcrt` locks. The combined lock/capture/retention/startup/refresh slice passed **221 tests,
  one POSIX-only skip**; the final native lock-only file passed **12 tests, no skips**. Both Windows and
  Linux-platform mypy checks passed for 63 core source files. Linux typing is not POSIX runtime acceptance.
- Action runtime/durability tests now passed **77 tests**; a separate credential/evidence slice passed
  **111 tests**. A writable snapshot descriptor retains `fsync`; stable Windows parent-process handles
  avoid false parent-loss caused by the venv launcher. Missing receipts remain uncertain, not successful.
- The runner now places copied code and machine-local test data in sibling directories inside one disposable
  outer container. Its earlier nested-test-temp layout caused legitimate repository-storage guards to refuse
  synthetic local data. A failing containment regression proved the issue before repair. Provider,
  registry-writer, Work-cache and runner tests then passed **155 tests, three skips**; new Work ciphertext
  passed real native owner-private DACL inspection, and unsafe old targets remain refused without migration.
- Windows CI is configured to cover the Python 3.11 minimum and 3.12, including core plus changed credential,
  action and hook surfaces. Its YAML/selector structure was checked locally; no remote CI was run or claimed.
  Harness coverage now includes the native filesystem and session-lock controls rather than omitting them.

The combined tree is still changing. Generated cohorts, full/core/security/changed-line coverage, final
typing/lint, privacy and platform parity must be rerun after source freeze. Nothing in this section closes
WIN-1 through WIN-6, signs owner UAT, implements independent current Work privacy, or authorizes cutover.

**Work recovery decision boundary:** re-artha §7.9 and approved WP-18 require a current consent/deletion
authority outside the restorable dataset. They do not select independent off-device Work-privacy custody
or a freshness protocol. The owner was unavailable for a protocol choice during this continuation. Preserve
the already-approved inspection-only lost-device hold; this is not new owner approval or a waiver of native
recovery acceptance. Independent Work-privacy continuity needs a reviewed employer-permitted destination,
authenticated head/journal, rollback/freshness/failure rules and separate implementation approval. A checkpoint
passphrase, complete dependency inventory or personal-cloud copy cannot substitute for that authority.

### Integrated native core result — subsequent snapshot

The current-source isolated core/coverage run completed with **3,503 passed, 81 failed, 109 skipped**
in **1,862.92 seconds**. Coverage **92.13% combined**, **93.47% line**, **88.49% branch** failed the
unchanged 95% combined/line and 90% branch gates. This is substantially narrower than the initial full-suite
failure set, but it is not a passing aggregate and does not supersede full-suite verification.

| Failing file cluster | Count | Observed cause / disposition |
|---|---:|---|
| Recovery access / quarantine CLI | 57 / 8 | Most report `unsafe restored privacy ledger`; native restored-output DACL publication is an actual producer/integration gap, not a permission check to weaken |
| Quarantine restore | 3 | Ciphertext-only assertions encounter native empty lock/temporary artifacts; distinguish coordination metadata from historical content without accepting historical plaintext |
| Checkpoint restore integrity | 3 | Two native pathname-substitution/sharing assumptions repaired in follow-up; actual restored-root owner-private DACL check still fails |
| Legacy writer fences | 3 | POSIX-only `fcntl` imports prevent the intended native capture/restored-tree refusal; bounded writer-portability repair in progress |
| Bounded preservation | 3 | Windows canonicalizes trailing dot/space names and interprets colon names as streams; literal-file test and source-inventory stream behavior require explicit resolution, not a blanket skip |
| Scoped configuration | 1 | Scalar CRLF terminator handling inserted an extra blank line; exact LF/CRLF mapping/scalar tests now pass 123 cases after repair |
| Recovery failure fixture | 1 | Obsolete `msvcrt.locking` stub did not implement the new native lock API; replaced with LockFileEx assertions in follow-up |
| Resource usage | 1 | Spend seal only had POSIX `0600` creation. New native publication now verifies protected DACLs; unsafe historical target is refused without overwrite |
| Vault crash scan | 1 | `SIGKILL` is absent on Windows. Abrupt native termination now uses Windows' terminating `os.kill(SIGTERM)` behavior and verifies the exit code at both crash injection points |

The spend continuity/resource/vault-crash follow-up passed **64 tests**, including preservation of committed
reservations when publication fails. The restore/lock follow-up passed **56 tests, two skips, one actual
restored-DACL failure**. These later targeted results repair particular causes; they do not retroactively
make the aggregate pass. Unresolved native registry DACL enforcement, minimum-version runtime execution,
POSIX runtime parity, final generated artifacts/privacy and independent release evidence remain visible.

The legacy writer follow-up now passed **27 tests, eight existing skips**. Decision and content writers use
the existing native sidecar session lock across complete read/modify/write operations, including positional
snapshot-path fencing. New real-process contention cases added no Windows skips. At this checkpoint both Ruff
configurations, strict Windows/Linux-platform typing of all 63 core files, syntax/import/state-write checks
and the repository PII/denylist scan of **55 current modified/new paths** passed. This working-byte scan is not
the final staged/gitleaks gate, and generated register/cohort files still need source-freeze regeneration.

### Native restored-output producer follow-up

After the native worker stopped, integration repaired the actual producer boundary in `checkpoint.py`:
new restored roots, inactive markers, member directories and files use the native protected-DACL APIs.
Member writes share one context-managed lifecycle with the existing POSIX writer; native handles are flushed
and closed before replay/cleanup. Existing unsafe destinations are refused, not silently permission-repaired.
An existing competing restore marker still refuses without deleting its directory. Native backend errors
are surfaced through the checkpoint's fail-closed error contract.

The final focused producer/integrity/quarantine-restore run passed **105 tests, six skips**, including actual
privacy-ledger replay through RecoveryAccess and the CLI, original input preservation, native private-root
inspection, unsafe existing-directory refusal and process-death containment. The exact empty native `.lock`
coordination file is asserted empty; historical catalog/chunk files still require ciphertext and all private
marker scans remain in place. Native pathname-substitution attacks prove sharing denial and successful
replacement only after the authenticated handle closes.

The earlier broader access/CLI attempt was stopped before completion and has **no current aggregate result**;
its snapshot also preceded the final shared writer-context refactor. Do not treat it as passing or combine
its partial output with later counts. The final 105-test slice and both-platform typing/both Ruff passes are
the actual evidence for the producer repair. Full recovery-access/review/CLI coverage, the complete native
suite and unchanged coverage thresholds still require reruns. Generated artifacts were subsequently
regenerated and passed 56 coherence tests; later runtime and source-inventory repairs invalidate that snapshot
and require another source-freeze regeneration. A separate current-consent/forget/export slice passed three
tests in 212.83 seconds; its diagnostic timeout stacks were not test failures or workload acceptance.

### Runtime cohort and native source-inventory follow-up

The runtime resume fingerprint independently mixed registered forward-slash paths with Windows backslash
discovery labels. One source could be hashed twice under different names, producing host-dependent cohorts.
A new independent-digest regression failed before repair; both Python and migration discovery now use
canonical labels. The runtime/register slice passed **69 tests**. Both Ruff configurations and strict
Windows/Linux-platform checks of 63 core source files passed. Older cohorts must be regenerated and
reevaluated, not relabeled. Repeated implementation hashing remains observable; no cache was added that
could hide changed control bytes, and no latency improvement is claimed.

The three bounded-preservation failures were not equivalent: trailing-dot/space fixtures had been
silently normalized by ordinary Windows path creation, while the colon fixture created a real NTFS alternate
data stream omitted by `os.walk`. Extended native paths now create actual trailing names for the existing
portability refusal tests. Additional tests reproduced omitted streams on a file, nested directory, root
directory and single-file root. Native capture now checks bounded `FileStreamInfo` through stable checked
handles during initial inventory and the pre-publication recheck. Named streams, unavailable/oversized
metadata and mutation refuse rather than producing an incomplete checkpoint; they are not silently dropped
or migrated. The capture format is unchanged and does not claim alternate-stream preservation.

The final preservation/checkpoint/session-lock slice passed **105 tests, two skips**, including an empty
stream added immediately before publication, actual oversized stream metadata and unavailable-query refusal.
The existing uncreatable-name and POSIX-only cases remain skips; no blanket native skip was added.
Both Ruff configurations and Windows/Linux-platform typing passed. These tests are included in capability
and security-harness evidence. The complete native access/CLI/integrity/restore chain is being rerun;
registry DACL repair is a separate bounded implementation stream, not a completed custody claim.

### Native registry custody and bootstrap integration

The bounded registry worker implemented native protected-DACL creation/validation for local authority
directories, registry/lock files and staging; bounded handle reads; and flushed atomic native publication.
Existing unsafe targets are refused without permission repair. The worker's registry slice passed
82 tests with 68 POSIX-only skips, but its broader writer integration initially failed 15 cases.
Integration reproduced the producer defect: credential publication obtains an installation/session lock
before registry mutation, and that lock producer created the native root and lock with POSIX mode bits.
Those mode bits do not establish the required Windows protected DACL.

New native lock directories/files now use the existing secure backend; the CRT lock descriptor is opened
while the checked native handle prevents replacement and its identity is verified before use. Existing
lock semantics, shared/exclusive byte-zero interoperability, owning-handle reads and capture fences remain.
A real DACL regression failed first. The registry/lock/writer integration then passed **142 tests,
69 skips**, including process-death/pending-intent/exact-retry cases. Both Ruff configurations and strict
Windows/Linux-platform checks of all 63 core files passed after a missing worker return annotation was fixed.
This proves synthetic native metadata custody, not native-secret installation or live recovery.

Three process-death fixtures used the pre-existing unprivate pytest root as the authority directory;
they now use a distinct local directory created by the real producer. The hermetic bootstrap likewise
no longer precreates an unprivate Windows authority root with mode bits before testing real producers.
The home-isolation assertion now checks the actual dedicated temporary parent and generated home prefix:
Windows system temp normally resides below the real user directory, so ancestor inequality was incorrect.
The resulting release-cohort/isolation/runner slice passed **85 tests**. Generated artifacts were
regenerated at that source checkpoint and still require final source/test freeze checking.

The complete access/CLI/integrity/restore invocation finished with **200 passed, three failed, six skipped**
in 1,367.84 seconds. All three remaining failures were adversarial tests manually constructing unprivate
native ledger parents/files, so they stopped before reaching the intended mutation/FK/affinity boundaries.
Those fixtures now create actual private native restored ledgers. The Windows mutation case observes
that a competing `wb` open can truncate before its write is denied; the replay snapshot detects the change
and refuses publication. After-close mutation remains possible and is asserted. The exact three cases
then passed; the 200/3/6 invocation is not retroactively a pass. A subsequent native core/coverage snapshot
was stopped after exposing additional native bootstrap failures; its obsolete partial output is not
aggregate evidence and its source predates the three fixture repairs.

### Additional producer and incumbent integration findings

The stricter native registry uncovered two more actual authority-root producers, not just unsafe fixtures.
Synthetic audit-hook traces showed `CoverageStore.record()` creating the default root through an ordinary
recursive mkdir, and importing the Pipeline initialized structured logging with the same behavior. Both
could poison subsequent native authority enrollment even when that enrollment worked in isolation.
Coverage persistence now shares the existing installation writer fence around read/modify/write;
new native log ancestors and machine-local output directories use the common checked private-directory
producer. Existing unsafe authority roots remain refused, not silently migrated. Logging preserves its
existing stderr fallback and existing destination semantics. Coverage's real separate-process capture
and restored-tree refusal is tested. New logger/output tests inspect actual native DACLs instead of mode bits.

The affected coverage/adapter/bundle/key/writer/credential/migration/action-evidence integration passed
**289 tests, eight skips**. Provider/Goal/output/logger/native-lock integration passed **171 tests, one skip**.
Credential/refresh process fixtures now use their own producer-created local directory rather than the
pre-existing pytest root; the Goal durability fixture reads exact authored LF/CRLF bytes using the writer's
reader. A fail-fast core follow-up reached **1,609 passed, 29 skips** before a native lock-call spy failed
because it replaced the entire kernel API used by the new file producer. That unit fixture now exercises
the lock-call boundary directly; real secure creation, process locking and identity checks remain covered
by native process tests. This stopped follow-up is not a passing core aggregate.

The separate non-core aggregate finished **5,115 passed, 60 failed, 16 errors, 76 skipped, 20 xfailed**
in 434.48 seconds before these producer/fixture repairs. It also reproduced actual telemetry loss:
children exited successfully but only 132/160 records persisted on native Windows. The telemetry worker
added native serialization and bounded stable-handle reuse with rotation/deletion checks; unchanged hash
serialization and the five-second performance threshold remain. Its 49-test slice passed, including three
160/160 process attempts; the reproduced 12.25-second/1,000-event case measured 0.28 seconds after repair.
Whole telemetry-module coverage was 80%, not a claimed 95% gate. All aggregate/static/generated/privacy
results still need verification against the final combined source; no native or release acceptance is inferred.

The later core tail reached **936 passed, 69 skips** before another unsafe spend-registry fixture; a following
spend-to-end invocation reached **85 passed, one skip** before a checkpoint CLI fixture setup error. Those
fixtures now create their separate authority roots through actual secure producers, rather than treating the
pre-existing pytest directory as private authority. The final CLI-to-end slice passed **476 tests, one skip**
in 266.99 seconds. These partial and overlapping counts are not added together as an aggregate.

The frozen combined implementation is now under one complete isolated `tests` invocation with core branch
coverage and the unchanged 95% combined threshold; the separate Makefile security coverage target is also
running with its unchanged selectors and 95% threshold. Both use system-temp isolated copies and separate
coverage files. The complete-suite invocation has no final result yet. The security target finished with
**1,052 passed, 77 skipped, zero test failures/errors** in 1,843.29 seconds, but failed its coverage gate:
**84.64% combined**, below 95%, Make exit code 2. Reported weak controls include quarantine review (71%),
quarantine storage (74%), session locks (74%) and recovery registry (76%). Passing tests do not close this
gate. The worker could not persist its requested output log; its returned result is the current evidence,
and its follow-up found no tool error establishing a file-output restriction. Coverage used a relative
`COVERAGE_FILE`, so the isolated runner removed it with the disposable checkout. Future coverage invocations
must use an absolute session evidence path. Seven warnings
include unclosed SQLite connections; the allocation sites alone do not establish which code owns them.
Static checks passed both Ruff configurations, strict
Windows/Linux-platform mypy for 63 core files, syntax, import checks (427 files), the State File write ratchet
(147/147), example-profile schema and regenerated-register checking. A subsequent working-byte privacy scan
passed all **77 modified/new paths** before this documentation update. It is not staged/gitleaks evidence.
Native execution here is Python **3.13.15**; configured Windows 3.11/3.12 CI, POSIX runtime parity and remote CI
remain unobserved. No commit, push or cutover has occurred during this continuation.

The missing-line report includes genuinely platform-specific POSIX descriptor-relative quarantine,
registry and `fcntl` lock branches. Windows execution cannot establish those guarantees. It also identifies
native refusal/cleanup branches needing meaningful tests, now being extended separately. No platform branches
are excluded from coverage, and no lower release threshold is substituted. Additional lock tests passed
**22 cases** for actual hardlink/directory refusal, unavailable restored-marker metadata, writer-to-capture
upgrade refusal, exception cleanup and owning-handle reader metadata/position checks. That module-only
diagnostic measured **76% combined**, not a passing security gate. A current POSIX execution and a reviewed
cross-platform coverage/evidence strategy remain necessary; old Mac data cannot be combined with changed source.

The action-process file now has injected API-boundary cases for parent exit, expired deadline, wait/open
failure and thread-start failure, each asserting handle closure and termination behavior; these complement,
not replace, its real offline child-process tests. A missing reconciliation evidence-policy binding also
refuses before provider work. Its first follow-up passed 59 tests but missed coverage at **94.88%** despite
the rounded table displaying 95%. The final file passed **61 tests** with **96.99% combined** at the actual
95% threshold. The two new test files pass repository Ruff. Production source is unchanged by this coverage
follow-up, but test-bound cohort artifacts require regeneration after all new tests settle. The complete
suite already in progress copied the earlier test snapshot, so it will not certify the subsequently added tests.

The bounded native-filesystem test extension added 26 cases and reproduced a real cleanup defect:
`_open_directory_chain` checked the leaf DACL before recording its new handle for exception cleanup.
A refused open leaked that handle and retained its filesystem sharing restriction. The repair registers
the handle before checking its DACL, so the existing reverse-order cleanup closes leaf and ancestors.
The worker stopped with **49 passed, one skipped, one failed**; after the repair, the combined native
filesystem/session-lock slice passed **72 tests, one skip** in 9.87 seconds. Its diagnostic combined
coverage was **82%**, not a passing coverage gate. Both applicable Ruff configurations passed, generated
cohort artifacts were refreshed and checked, and all **seven manifest tests** passed. The long complete-suite
invocation copied source before this repair; even a successful result will need explicit current-source
follow-up rather than being presented as final validation.

### Complete Windows aggregate result and coverage stop

The complete isolated invocation finished in **3,408.72 seconds** with **8,815 passed, 185 skipped,
20 xfailed, zero test failures/errors and 181 warnings**. Its exit code was **1**, because core combined
coverage was **93.02%**, below the unchanged 95% floor. The independent XML checker also failed:
**94.16% line** versus 95%, **89.91% branch** versus 90%. This is a passing test collection, not a passing
quality gate or a current-source aggregate: the copied snapshot precedes the later handle-cleanup repair
and new boundary tests.

The unchanged changed-line command against `origin/main` also exited **1**: **686 uncovered of 9,876
changed lines**, with coverage displayed as **93%**. This uses the completed aggregate XML against the
working diff, so it is diagnostic only after the subsequent source edit. The newly untracked native
backend is included in aggregate/security coverage but is not listed by this Git-diff report; that omission
does not establish changed-line acceptance for the new file. No staging was performed to manufacture a pass.
The repaired backend subsequently passed strict Windows- and Linux-platform mypy; that is typing, not
POSIX execution. Warnings remain to attribute; no assertion is made that every SQLite warning originates
in the native filesystem allocation site where garbage collection happened.

The next efficient step is to establish an approved real POSIX execution path and agree how exact-source
Windows/POSIX coverage evidence is validated, without excluding native controls or lowering any floor.
Windows-only tests cannot cover the actual `fcntl`, descriptor-relative and POSIX ownership branches.
Finish meaningful native error-path tests and source/test freeze first, regenerate cohorts, then perform
the current-source platform checks and unchanged release gates. Do not repeatedly run the hour-long Windows
aggregate while the cross-platform evidence requirement is unresolved. Native dependency/privacy recovery,
independent product evaluation and owner release acceptance remain separate from this engineering stop.

### Continued Windows-local boundary validation

Without installing a new host environment or using remote access, the next bounded batch expanded real
native failure/cleanup tests. The filesystem worker's second tranche added 24 cases and the filesystem/lock
selection passed **96 tests, one skip**. Native filesystem diagnostic coverage reached **86.49% combined,
90.04% line and 73.15% branch**; this is not 95% and not combined with prior snapshots.

Migration tests now cover malformed/missing core-derived preconditions in every pending/deferred/draft/approved
stored state and both origin fields, plus valid structured/JSON evidence binding. They preserve required
review without mutating the input records: **41 passed, 100% line/branch/combined module coverage**.
Native private-publication tests inject write, flush and replace failure and disappearance of staging,
asserting unchanged previous ciphertext and complete staging cleanup; **seven selected tests passed**.
Real-Ledger command tests cover export provenance/key refusals, known subject checking and invalid/idempotent
extraction cancellation; **18 selected tests passed**. Lock descriptor open/metadata/type/stream-creation
failure tests assert owned-descriptor closure and successful subsequent acquisition; all **27 lock tests**
passed. The complete five affected migration/retention/command/lock/offline-action test files then passed
**300 tests** in 34.31 seconds, with applicable Ruff checks passing. These are meaningful Windows-local
improvements, not replacements for the blocked aggregate/coverage/POSIX/acceptance gates.

Further real reproducers found two additional native defects: a relative child handle escaped cleanup
when its absolute-chain DACL check failed, and leaf-only privacy validation incorrectly checked an ancestor
with the same basename. The first native tranche stopped at **127 passed, one skip, two failures**;
cleanup now owns the relative handle across chain acquisition, and leaf selection uses the final index.
A separate failing test then proved parent-handle leakage when security-descriptor allocation failed
during creation. That allocation is now inside the existing cleanup scope. A successful relative-child
test also exposed loss of writable access when reopening a non-private leaf; the returned chain now
preserves the write access already requested by the relative open. These repairs retain private-leaf,
no-reparse and identity checks, with no automatic permission repair.

After fixing the security-allocation test's double-counting of volume opens, all native filesystem/lock/
bounded-preservation tests passed: **170 passed, two skipped**, **95.21% combined native filesystem coverage**
at the actual unchanged 95% threshold, in 13.20 seconds. Tests also assert unchanged original ciphertext,
close/reacquire behavior, valid repeated-name traversal, malformed local/remote paths, wrong owner and a
real secure-directory creation race. Both full Ruff configurations and strict Windows/Linux-platform mypy
of all 63 core files passed. Cohorts were refreshed and checked. A new complete isolated **test-only**
Windows invocation is running against this source freeze; it deliberately does not repeat the expensive
coverage collection while the cross-platform coverage blocker remains. Its actual result is still pending.

Two further tests exercise wrong expected file identity and ancestry mismatch/closed-directory refusal,
including successful validation afterward and rename after handles close. The final native filesystem/
lock/bounded-preservation selection passed **172 tests, two skips** in 14.18 seconds. Native filesystem
coverage now passes all three unchanged numerical floors independently: **96.00% combined, 97.28% line,
91.12% branch**. This is the native filesystem module's gate, not the complete core/security gate.
The complete suite already running includes all production fixes but predates these last two tests;
their current-source focused result and regenerated seven-test manifest result are recorded separately.
Tracked and unpublished-history privacy both passed with real gitleaks after the source repairs.

The new complete isolated Windows **test-only** invocation subsequently finished successfully:
**8,986 passed, 185 skipped, 20 xfailed, zero test failures/errors, eight warnings**, **2,850.87 seconds**,
exit **0**. All eight warnings are incumbent deprecation notices, not SQLite resource warnings in this
invocation. This validates all current production repairs on native Windows Python 3.13.15. Its copied
snapshot predates the last two native identity tests and final generated test-cohort refresh, both separately
verified above. This invocation did not collect coverage and therefore does not supersede the failed full-core,
security or changed-line coverage reports. Native filesystem coverage alone now passes its numerical floors;
current-source POSIX execution, minimum-version Windows execution and separate release acceptance remain open.

The next Windows-local continuation adds **11 passing recovery-boundary tests**: a short native database
read refuses before deserialization, oversized serialized replay refuses before writing, and a mismatched
post-write reread never returns success, all with after-close rename checks. Pure affinity tests cover
document, capture and retained-replay erasures in authoritative/shadow namespaces and original-source/
content-HMAC matches without live-row backfill. No production behavior changed in this batch.

An exact-source Windows/Linux combined-coverage design was offered for owner review, with unchanged
95% combined/line and 90% branch floors and mandatory passing native platform jobs. The owner was unavailable;
that proposal is **not approved or implemented**. Existing gate semantics remain unchanged.
Read-only local probes found no WSL distribution/container engine, no usable Python 3.11 launcher,
no Python Install Manager, and no editor-selected interpreter available through Pylance. No OS/runtime
installation or remote host access was inferred from those probes. The current-source security target
finished with **1,169 passed, 77 skipped, seven warnings**, in **1,959.71 seconds**.
Its **87.69% combined coverage** fails the unchanged 95% floor; Make exited **2**.
The absolute coverage database and complete log were retained. Post-run XML generation failed because
the isolated runner had already removed the source checkout referenced by the dataset. No XML or
proven native/POSIX uncovered-line classification is available from that invocation. Future XML reports
must be generated before disposable-source cleanup, or use a verified source mapping. This snapshot
predates the latest 11 recovery-boundary tests and the development-canary isolation repair; it is not
silently combined with their results.

The earlier full-suite coverage report also contained a synthetic canary package outside the core.
A new reproducer proved that nested Makefile coverage canaries inherited `COVERAGE_FILE` and overwrote
the parent's dataset with their miniature package. The canary launcher now uses its own tree-local dataset
and removes inherited automatic coverage-start variables; it does not alter production coverage selectors
or floors. All **18 development canaries passed** under an actual parent coverage invocation. That
canary-only parent correctly collected no core data, instead of including the fake package. Consequently,
the earlier full-suite numerical report is retained as failed diagnostic history, not clean release
coverage. A fresh **core-only** isolated coverage invocation is running with the exact Makefile core
selection, absolute retained data/XML paths and unchanged 95% combined threshold; no fake canary package
is selected. Test-suite pass evidence is unaffected by this measurement-isolation defect.

### Efficient completion plan and exit evidence

| Order / parallel owner | Deliverable | Required check / stop condition |
|---|---|---|
| 1 — Native filesystem | Implement approved WIN-1/2 with handle-relative/no-reparse I/O, owner-private DACLs, stable identity, serialization and write-once publication; integrate catalog, chunk, review/export and restored-ledger access. | Native junction/reparse/hardlink/ACL/substitution/contention/death tests with synthetic data; preserve POSIX behavior. Unsupported guarantees must refuse, not fall back to path-based I/O. |
| 1 — Windows contracts | Reproduce and repair common lock/capture, subprocess and filesystem portability defects in isolated copies. | Current-source full suite and focused real-process checks; report every skip/failure. Windows must not silently exclude content or drop a fence to make capture succeed. |
| 1 — Recovery integrator | Preserve profile separation and inspection-only lost-device behavior; identify any remaining independently current Work-privacy protocol decisions. | Synthetic wrong-profile/missing-history refusals before native writes; no native secret enrollment or Work-to-personal promotion. An unsettled protocol stays blocked pending owner review. |
| 2 — Integrator after source freeze | Regenerate capability/manifest evidence; run syntax, both Ruff configs, import/state-write ratchets, strict typing, core/security/changed-line coverage, schema and privacy gates. | Record exact final commands/results against the combined tree. Earlier Mac counts and intermediate worker passes do not certify the final Windows source. |
| 3 — Owner/native hosts | Review historical dependencies, create complete profile-scoped checkpoints and exercise Mac personal/Windows Work and independent personal cloud-copy recovery, current privacy and rollback continuity. | Actual reviewed artifact hashes/generations/cohort and native observations; no synthetic/live/signed substitutions. Work stays on the Windows host outside repository/sync storage. |
| 3 — Independent evaluation, parallel with recovery | Held-out source-negative P1/P2, identity/time/negation and Goal-correction evaluation; two reviewed cross-Domain cases; document field outcomes. | Zero observed targeted P1 misses with denominator/uncertainty; dates, amounts and names separately >=99%; false confirmations <=0.5%; exact cohort and reviewed split. |
| 3 — Workload/provider evaluation | Stated-machine cold/warm, limited Digest, long-gap/reset and one-/two-year workloads; actual usage/cost/review effort and approved provider outcomes. | >=95% completed Catch-ups; approved normal p95 (<3 minutes unless revised), Digest <10 seconds, <$50/month at stated cadence; unaccepted sync/effects remain held. |
| 4 — Owner release review | UAT against approved acceptance IDs, exact release artifacts, preservation and former-executor retirement where applicable, then explicit cutover decision. | Keep incumbent authority, rollback and all held controls until every applicable gate is met. Active feature specs are not archived before UAT. |

Specification mistakes to reconcile without inventing requirements: re-artha's old `date_iso` failure example
conflicts with the later native-calendar-field WP-02 acceptance; its decision header still calls later-resolved
decisions open; ADR-008's snapshot-key/ledger-authority shorthand predates approved encrypted escrow and ADR-009;
some older archived packages lack recorded owner UAT. These are provenance/documentation defects, not permission
to waive acceptance or silently change authority. Current pilot scope is re-artha §12.6; per-package completion
and archive rules remain §12.7/12.8.

### Requirement-by-requirement disposition

This matrix reconciles the latest integration notes and canonical appendices; "present" is a local engineering
claim, not exhaustive source certification or owner acceptance. The generated capability/traceability registers
map executable evidence. Unknown exact acceptance clauses require that evidence, not speculative rewrites.

| ID | Current implementation / approved disposition | Still required for applicable acceptance |
|---|---|---|
| RA-01 | Scoped coverage, partial positives, provider cursor/reset/deletion mechanisms present; unaccepted sync held. | Consented provider parity, lifecycle, long-gap/reset and native common contracts. |
| RA-02 | Fact versions, source times, corrections and dependencies present. | Independent identity/time/negation/person/amount and consequential support evaluation. |
| RA-03 | Triage, exact reviewed completion/reopening and Goal linkage present; authored State File authority retained. | Deadline/deduplication/suppression/reopening and incumbent outcome parity. |
| RA-04 | One composer, scoped answers, provenance/history and publication guards present. | Semantic citation support, truthful quiet/degraded wording, usability and actual host isolation. |
| RA-05 | Exact action review/claim, UNKNOWN, evidence/permission preconditions and transfer present; pilot previews only. | Supported provider outcomes/reconciliation and former-executor retirement; unsupported types stay held. |
| RA-06 | Correction/forgetting lineage present; automatic personal-memory extraction deliberately disabled. | Real correction persistence and replay anti-resurrection; no automatic extraction to reintroduce. |
| RA-07 | Cohort/model policy, admission/cancellation, encrypted cache and usage/seal mechanisms present. | Actual model identity/disclosure/replay and cost; quotas alone are not cash-budget proof. |
| RA-08 | Versioned adapters/provider lifecycle mechanisms present; broad MCP replacement not required. | Saved/live parity and any separately selected MCP/client trial. |
| RA-09 | Encrypted local Work cache, technical-only health, personal exclusions and fences present. | Native migration, old synced-copy retirement, employer boundaries and separately current Work recovery. |
| RA-10 | Generated registers, logical measurements and v4 refusal controls present. | Current exact-source/cohort reviewed evidence to advance from code-present to exercised/measured. |
| RA-11 | Goal metrics/completion, indicators, authored calibration and proposal-only tradeoffs present. | Correction-sensitive usefulness and reviewed authored outcomes; broader forecasting remains destination scope. |
| RA-12 | Current consent, subjects and recovery role checks present; full family surfaces gated. | Actual consent and cross-surface revocation/disclosure UAT; assignments do not supply data consent. |
| RA-13 | Cross-Domain candidate/tradeoff and ambient-intent coexistence mechanisms present. | Two named reviewed cross-Domain outcomes, correction and nonduplication parity. |
| RA-14 | Strict packs/additional vehicle pack present; retain-all explicitly keeps automatic expiry off. | Complete additional-Domain lifecycle without core special cases; broad expansion/expiry not silently required. |
| RA-15 | Reviewed configuration and exact source administration present. | Accessible first-use disclosure, correction/revocation and setup UAT. |
| RA-16 | Processing gaps, partial retention, fair backlog and resource/failure controls present. | Attachment/forward/truncation/refusal/overload outcomes and no false quiet conclusion. |
| RA-17 | Pull-only horizon/freshness/coverage behavior present; calendar effects preview-only. | Native retired-job footprint, cadence/long-gap behavior and approved effect trial only if enabled. |
| RA-18 | Harness roles, output hygiene, hooks and scoped credentials present; native quarantine work active. | Windows ACL/reparse/race/death and crash/plaintext exposure; trusted Python is not an OS sandbox. |
| RA-19 | Accountable owners, deduplication and evidence-backed completion present in later-scope capability. | Consented household outcome/revocation UAT; no separate assignment-enrollment workflow. |
| RA-20 | Registry, checkpoint/quarantine/review/export and scoped interchange present; Windows integration incomplete. | WIN-1..6, separately governed Work/current-privacy recovery, historical inventory, both hosts/cloud rehearsal and accessibility/custodian UAT. |
| RA-21 | Text/OCR and exact field-confirmation mechanisms present; breadth is separately scoped. | Independent held-out dates/amounts/names >=99% each and false confirmations <=0.5%; privacy/grounding outcomes. |
| RA-22 | Corpus/labels/replay infrastructure present. | Reviewed source-negative gold labels and feedback-to-fixture traceability; exhaustive automation not inferred from summaries. |
| RA-23 | Reduced beliefs/forget/tombstone/replay mechanisms present; retain-all and no inferred relationship profiling. | Anti-resurrection across actual recovery/import and accurate forget receipts. |
| RA-24 | Activity/pause, exact claims, provider-scoped worker and uncertainty present; external execution optional. | Exact-approved supported reminder outcome/reconciliation and native isolation before enabling effects. |
| RA-25 | Source-aware responsibilities, stop evidence, prerequisite readiness and inert sheets present in later scope. | Owner-reviewed workflow usefulness and evidence-backed completion; no autonomous advancement. |

## Owner execution split — 2026-10-25

Windows development is explicitly assigned to the **Windows laptop after the Mac-addressable remainder is
finished here**. No remote access is expected. The controlling WIN-1..WIN-6 checklist is at the top of
`specs/re-artha.md`: native secure filesystem backend, real race/death/ACL/reparse validation, native
credential/privacy/profile recovery, separate Work recovery, common contract parity and actual release evidence.
These are unfinished Windows engineering/acceptance tasks, not completed work or a waived release gate.

This Mac continues the bounded non-Windows requirement disposition and any concrete approved gaps, then
validation and transfer preparation. Live/consented/native owner acceptance stays separate. The old Windows
runner request below is historical and superseded by this allocation. Cutover remains disabled.

## Active takeover — 2026-10-25

The owner resumed comprehensive implementation after the prior safe handoff. Local main `6f86c3c`, one
branch/worktree and all committed/uncommitted work are retained. No native keys, provider ingestion/effects,
production migration, consent change, activation or cutover has run. The older handoff below describes the
inherited checkpoint; its stop instruction is superseded by the owner's current request.

The previously verified resumed aggregate (8,301 isolated tests) predates encrypted recovery quarantine and
cannot validate it. Current source includes that feature plus completed source-administration, provider,
resource and release-evidence repairs. Three disjoint agents reviewed storage, current authority/review and
restore integration; the integrator owns CLI, evidence registration, documentation and aggregate validation.

| Reproduced mistake | Current repair / verification boundary |
|---|---|
| Extra restored grants could masquerade as current recovery permission. | Require exact independently trusted current consent history for content authority. |
| Metadata-only authority could read a previously released export, including after consent expiry. | Authenticate metadata/content role in the review/export binding and recheck current authorization. |
| Rolling back a valid old catalog could undo an erasure block after SQL source references were purged. | Capture authenticated original source-affinity before first catalog seal; recompute denial from current trusted history on every reopen and deny again on content reads. Never backfill from purged SQL. |
| A substituted catalog publication could falsely report success. | Verify encrypted bytes before publication and after link cleanup; actual process substitution regression. |
| Arbitrary SQLite paths or malformed database bytes could be released as a plaintext ledger. | Canonical personal/shadow paths only; bounded original schema/history, physical/FK and exact page-size preflight before destination creation. |
| Privacy replay could corrupt a ledger or append private trailing bytes yet report success. | Recheck physical/FK/page-image integrity after replay; real process-death tests preserve inactive output. |
| Recovery controls were absent from required security/cohort registration. | Register all three controls and explicit tests in capability mapping and mandatory security coverage; regenerate after source freeze. |
| Old CLI fixtures expected obsolete refusal wording and omitted current owner configuration. | Keep owner-first refusal; exercise wrong-profile authentication with an actual current-owner configuration, and use dedicated current-context quarantine fixtures for successful recovery. |
| Encrypted clean exports had no ordinary CLI reader after token expiry. | Add current-consent `quarantine-export-view`: bounded UTF-8 escaped JSON, no named plaintext files, no automatic view or activation. |

Exit sequence: close these reproduced failures → source freeze → isolated/static/security/changed-line/privacy
aggregate → separate local code/docs commits. Native/independent acceptance remains serial before cutover.
The Mac/POSIX integration is committed and verified below; supported-platform and native acceptance remain blocked.

**Hard platform blocker:** quarantine's secure filesystem backend is POSIX-only and explicitly refuses Windows.
Windows needs handle-relative/no-reparse I/O, private DACL checks, stable file identity, locking and write-once
publication, plus actual native race/death tests. A Mac mock cannot establish that behavior; no unsafe fallback
will be added. The owner has been asked for a native Windows runner/access method. Separate Work restoration
and lost-device normal activation remain held pending their current-privacy protocol. Independent historical
inventory, Mac/Windows/cloud rehearsal, held-out evaluation/workloads/provider outcomes, owner UAT and signed
release are still required. Explicit retain-all keeps automatic expiry disabled; unsupported action families stay held.

### Verified local recovery integration — `b24056d`, 2026-10-25

All inherited public implementation and new recovery/source tests are consolidated in local main `b24056d`
(34 files, 6,961 additions / 174 removals). Ignored private files remain in place and were not force-added.
One branch/worktree; no push or new remote CI. This completes the current POSIX repair batch, not the
entire specification or cutover readiness.

| Required component | Actual result |
|---|---|
| Full isolated suite, excluding restricted Work | 8,813 passed, 78 skipped, 20 expected failures; 495.76 seconds; `/tmp/artha-oct25-final-check.log` |
| Core coverage after fixture correction | 3,637 passed; 97.16% line / 93.27% branch, 96.10% combined; `/tmp/artha-oct25-remaining-gates.log` |
| Changed-line coverage | 9,238 / 9,515 lines, 97.09%; passing the unchanged 95% floor |
| Expanded mandatory security coverage | 1,053 passed, one skipped; 96.09% combined |
| Static/evidence | Syntax, both Ruff configurations, imports/state-write ratchet, strict typing (62 core / three credential modules), generated registers and profile schema passed |
| Privacy | Tracked/unpublished-commit checks, separate staged scan and enabled commit hook passed; no exclusion or bypass added |
| Focused/process evidence | Access/review323, storage integration228, restore174, CLI/checkpoint251; actual process catalog substitution and late SIGKILL boundaries; synthetic data only |

The first parallel `make check` run passed the full isolated suite but failed one core test fixture because it
omitted its fake backend and synthetic repository. The fixture was repaired, its two cases passed independently,
and every remaining `make check` component was rerun successfully. The implementation bytes were unchanged
between the full-suite pass and repaired-fixture coverage run. This is combined final gate evidence, not a claim
that the earlier failed invocation passed. Warnings remain dependency/deprecation notices and synthetic SQLite
ResourceWarnings. No production migration, native key operation, live provider/content operation, owner UAT or
cutover was performed. Original checkpoint bytes remain unchanged in synthetic recovery tests.

**Next work is blocked on owner/native input:** provide a native Windows runner/access method for the secure
backend and race/DACL/publication/death tests. Then complete current-privacy/dependency inventory and personal/Work
native recovery on both hosts plus independent cloud-copy rehearsal. Held-out quality/document/Goal evaluation,
real workload/cost/provider trials and owner UAT can proceed in parallel on the frozen cohort with explicit scope.
Only after their reviewed evidence and release decision can we jointly cut over. General class-specific expiry
remains unimplemented under the explicit retain-all pilot policy; unsupported action families remain held.

## Safe Stop And Handoff — 2026-10-25

The owner approved the historical-file recovery contract, then explicitly asked this session to stop so another
agent can finish. Work is preserved on the single local main checkout (`6f86c3c`, ahead 77), uncommitted. The new
encrypted storage, review/export and current-privacy access modules plus checkpoint/CLI wiring have focused tests,
but **the quarantine feature has not passed the full aggregate gate**. Latest v2 export changes still need the
combined command/restore test run, capability/security-gate registration, final documentation and full validation.

Read [the current handoff](re-artha-handoff.md#latest-handoff--stop-here-first), which supersedes the historical
handoff and dated backlog rows below for this feature. It records precise APIs, test evidence, failure/validation
limits and next commands. POSIX-only implementation currently fails closed on Windows; copied extracted-quarantine
directories are unsupported. These are real remaining implementation limits, not claims of native acceptance.
The approved contract is at the top of `specs/features/wp-18-state-checkpoint.md`. No live backup/key/provider
operation, UAT, activation or cutover occurred. Keep held controls disabled and feature specs active.

The specification is **not completely implemented or release-ready**. WP-15 repairs the reproduced local integrity,
permission and command-boundary defects. It does not close every product requirement or supply live acceptance evidence.
The incumbent remains authoritative. Keep cutover and held action types disabled. Keep this work local.

## Resumed swarm audit — 2026-10-24, current working tree

Takeover found local `main` at `6f86c3c`, 77 commits ahead of the remote, one branch and one worktree. All inherited
source-management edits and committed work were preserved. This pass created no branch/worktree, committed/pushed
nothing and performed no live ingestion, OS-key operation, provider action, native recovery, consent change,
owner UAT or cutover. Older dated rows below are historical, not the current outstanding-work list.

Four initial parallel streams reviewed source administration, resources, providers and release/recovery; three
follow-up streams repaired spend continuity, restore privacy and preservation acceptance. The integrator owns
shared cohort/command/cache wiring and aggregate gates. An early resource test attempt was unreliable because
of terminal contention; its two-case reproducer was rerun centrally and failed before repair. Only subsequent
focused/aggregate evidence counts.

### Reproduced current defects and repairs

| Severity / boundary | Actual defect and repair | Executable evidence |
|---|---|---|
| High — spend restore | UTC rollover discarded old spent identities. Preserve lifetime replay keys and monotonic serial while counters stay month-specific; old restores cannot admit the same prior request again. | `test_resources.py`, `test_resource_usage.py`, `test_resource_runtime.py` |
| High — release cohort | Household-profile and connector-registry bytes were absent from evaluation identity. Hash bounded effective workspace inputs before household binding; runtime supplies its actual workspace. | `test_integrated_surfaces.py` |
| High — source review | Actor/permission/cohort binding was missing and removal did not fully validate the existing declaration. Re-resolve current authority through final replacement, bind the digest, revalidate subjects/scope/options, serialize/CAS and flush publication. | `test_user_config_sources.py`, `test_commands_scoped.py` |
| High — held sync | Generic source administration could enable sync without separate live-parity acceptance. Refuse `sync_changes: true` in this workflow; false remains allowed. No provider or separate trial is enabled. | Source configuration negatives |
| High — restored copies | Ledger privacy replay could not prove arbitrary historical files excluded forgotten content. Normal CLI restore now refuses remaining unproven non-ledger content, removes staged plaintext and retains original checkpoint bytes. This is containment, not completed recovery. | `test_checkpoint_restore_integrity.py`, `test_state_checkpoint_cli.py` |
| High — acceptance | Generic recovery receipts omitted mandatory native/privacy/copy demonstrations. Require `artha.recovery-rehearsal.v2`, exact artifact/generation/cohort and explicitly reviewed Mac personal, Windows Work and personal OneDrive-web observations. | `test_cutover_fence.py`, `test_preservation_streaming.py` |
| High — cached evidence | Changing/deleting the separately referenced Work recovery artifact did not invalidate cached acceptance. Track its approved reference and physical revision. | `test_release_artifact_coherence.py` |
| Medium — provider omissions | CalDAV lost verified deletions after later failure; Gmail listing lacked repeat/page bounds and discarded earlier references; Graph delta exceeded result caps. Preserve scoped partial observations and prior cursors; bound/deduplicate pages and enforce declared caps. | Handler/adapter/runtime/Ledger lifecycle fixtures |
| Integration | Source administration hard-coded the pilot Domain. Reuse the owning runtime configuration rather than adding another default. | No-Domain-special-cases and source command tests |

The inherited resource/cache/cancellation/seal, provider cursor/reset, documentary confirmation, Goal indicators/
calibration/tradeoffs, strict Domain packs/additional Domain, scoped interchange/custodian/configuration and
authority/release mechanisms are present; they are not reopened as wholly missing features. The owner-approved
request limits and explicit retain-all/automatic-expiry-disabled policy remain unchanged. Unsupported action
families remain held. Synthetic fixture passes do not establish live provider/document/product outcomes.

### Shortest Remaining Completion Plan

| Order / parallel owner | Remaining deliverable and exit evidence |
|---|---|
| 1 — Integrator | Freeze current source, regenerate evidence and pass isolated/static/security/changed-line/privacy gates; record exact results, not earlier checkpoint totals. |
| 2 — Recovery engineering, pending design review | Review-gated encrypted quarantine and clean-file release for unproven historical files, bound to exact hashes/current privacy generation. No plaintext release or revival of forgotten content. Owner was unavailable; this proposal is not approved or implemented. Normal restore stays blocked for those artifacts. |
| 3 — Owner and native hosts | Exact historical dependency inventory; separate personal/Work checkpoint and Mac/Windows/independent-cloud rehearsals; independently current privacy continuity. Actual reviewed hashes/observations are required. Work content is not cloud-copied or made personal authority. |
| 4 — Independent evaluator, alongside recovery | Consented held-out/source-negative P1/P2, document, Goal-correction and cross-Domain outcomes on the frozen cohort. Review split/label adequacy; document dates/amounts/names >=99%, false confirmations <=0.5%. No synthetic results count as live. |
| 5 — Workload evaluator, alongside 3/4 | Stated-machine cold/warm, Digest, long-gap/reset and one-/two-year measurements; actual usage/cost and review effort. Scorecard v4 requires >=95% completion and approved bounds. Quotas alone do not prove cash billing or end-to-end latency. |
| 6 — Owner-scoped provider/action trials | Consented source parity and supported-host credentials/isolation; exact-approved reminder outcomes/reconciliation. Unaccepted sync/action families remain held. |
| 7 — Owner release decision | Exact cohort/artifact review, UAT, current preservation and former-host retirement where applicable, then explicit joint cutover. Preserve rollback and uncertainty; never enable retired background jobs. |

Automatic per-class expiry remains destination work under the explicit retain-all pilot policy. Arbitrary-file
privacy reconciliation and full lost-device activation are not claimed implemented. The first full run found one
Domain-abstraction regression (8,300 other tests passed); its 128 focused checks passed after repair. A subsequent
core run exposed 15 synthetic-workspace cohort mismatches; the fixture now selects its actual temporary workspace,
still using the real hash function, and all 127 affected release fixtures passed. Failed snapshots are not passes.

### Verified Resumed Aggregate

Final `make check` passed against frozen source on 2026-10-24. Exact log:
`/tmp/artha-resumed-swarm-verified-20261024.log`.

| Gate | Actual result |
|---|---|
| Full isolated suite, excluding restricted Work | 8,301 passed, 78 skipped, 20 xfailed; 272.99 seconds |
| Core | 3,125 passed; 97.10% line / 93.30% branch; 96.07% combined |
| Changed-line coverage against origin/main | 8,272 measured lines, 249 uncovered; passing the 95% gate |
| Security controls | 610 passed, one skipped; 96.09% combined |
| Static/evidence | Syntax, both Ruff configurations, imports/state-write ratchet, strict typing (59 core / 3 credential modules), generated registers and schema passed |
| Privacy | Tracked/unpublished-commit checks passed in the aggregate; separate untracked-file PII/identifier and gitleaks checks passed without exemptions |
| Synthetic benchmark | 30/120/480 Signals: cold 0.044/0.118/0.425 seconds; warm 0.013/0.034/0.126; roughly linear, with no provider/model/network latency |
| Delivery and acceptance | Existing local main `6f86c3c`, 77 inherited unpublished commits, current changes uncommitted; no branch/worktree/push, live key/provider operation, native UAT or cutover |

Dependency/deprecation notices and test SQLite ResourceWarnings remain visible. These are implementation checks,
not native recovery, owner UAT or independent product outcomes. This documentation-only evidence update follows
the frozen-source gate. The baseline and active features remain unarchived. The next engineering decision is the
review-gated encrypted historical-file quarantine/clean-file protocol; until approved and implemented, normal
restore of unproven non-ledger artifacts remains refused. Native and independent acceptance rows above also stay open.

## Fresh takeover audit and execution plan — 2026-10-24, current continuation

**Baseline:** local main `187fcb3`, with `3b659d4` retained and further uncommitted work preserved in place.
One branch, one worktree; no merge, reset or remote publication. Earlier sections below are historical and
must not be read as the current outstanding-work list. The new aggregate is under validation.

| Workstream | Implemented in the inherited aggregate | Current defects and completion check |
|---|---|---|
| Preservation/recovery | Reviewed profile registry, authenticated bounded capture/restore, portable paths, physical/SQLite verification, inspection-only lost-device recovery; inventory blocks missing active spend-seal artifacts; Windows checkpoint capture: 69 passed including a separate-process input-change race; checkpoint restore integrity: 48 passed, 2 platform-specific skips; quarantine review: 230 passed, 5 platform-specific skips; recovery quarantine: 128 passed; quarantine restore: 59 passed including four abrupt process-death boundaries; public legacy-writer fence suite: 21 passed, 8 private Work tests skipped because those sources are absent | Verify the omitted private Work writers and remaining process/race cases; native recovery remains separate. Large SQLite/manifest inputs still have explicit bounded limits. |
| Authority/actions | Queue-first transfer, durable pending state, signed former-host retirement, versioned calendar writes, exact Fact/permission/claim binding and provider-only worker credentials; transfer protocol: 86 passed on Windows, including all five abrupt process-death cases; retirement: 38 passed on Windows, including all four abrupt process-death cases | No observed credential revocation service or fence on a disconnected former host; provider-specific conditional writes remain unsupported for other action types, and native failover plus owner UAT remain outstanding. Work content checkpoints cannot promote the shared personal Ledger/action authority. |
| Providers | Opt-in scoped cursor/reset/deletion chains for mail/calendar, complete-listing occurrence withdrawal and partial-positive retention; synthetic handler→runtime→Ledger cursor/lifecycle suites: 137 passed | Native/live provider parity remains unperformed, so opt-in sync settings remain disabled. |
| Resources | Durable admissions, fair scope selection, cancellation, encrypted response cache, immutable usage receipts and anti-refund seal; fallback fairness uses SQLite Row column keys and runtime refuses missing registered spend history; resource accounting/runtime selections: 74 passed | Native-host validation remains outstanding. Owner-approved limits remain 2,000 reservations/month and 64 pending/selection; cash billing stays unknown without provider receipts. |
| Retention | Source/replay expiry via deletion journal, anti-resurrection preview and explicit forget | Owner chose explicit `retain_all`/automatic-expiry-disabled for cutover. General document/corpus/Briefing/history/trace expiry remains unsupported and is not claimed complete. Older backups require privacy replay/quarantine. |
| Product/surfaces | Leading indicators, authored-tolerance Sprint calibration, proposal-only tradeoffs; strict Domain contracts/additional vehicle pack; scoped interchange, custodian/configuration review, document field confirmation; synthetic UAT script suite: 14 passed | Repair remaining inherited fixtures against actual contracts; verify runtime wiring and correction/permission negatives. Independent usefulness, document accuracy, accessibility and owner UAT remain required. The UAT result used scratch data only. |
| Release/integration | Release acceptance v4, artifact-coherence and slice-release selections: 348 passed; generated-register check passed after latest code changes | Run the complete current aggregate suite and unpushed-history privacy scan. Historical passing totals do not validate the current full source snapshot; native, live and owner acceptance remain separate. |

### Reproduced mistakes repaired during this takeover

| Severity | Mistake | Repair / executable evidence |
|---|---|---|
| High | Retirement could race a dispatch claim, acknowledge an unverified checkpoint, lose its fence when a marker disappeared, or resume mismatched generations. | Queue transaction spans retirement, authenticated checkpoint preflight, durable database+marker receipt and epoch/profile/retry binding; authority protocol/retirement process tests. |
| High | Core-derived reconciliation could query a provider after current export consent was revoked; a missing worker capability could enter a native authorization path. | Current evidence checks before reconciliation, explicit capability refusal, uncertainty preserved; action evidence dispatch tests. |
| High | Calendar undo dropped evidence preconditions and lowered sensitivity. | Retain review/evidence/sensitivity and bind the resulting provider ETag; calendar version tests. |
| High | Missing registered spend history looked like a fresh budget; shadow registration could block first authoritative initialization. | Strict registry custody checks and distinct namespace identities; spend continuity registry/runtime tests. |
| High | Actual token receipts exceeding an estimate were lost on older restore because the external spend serial was unchanged. | Advance serial and seal before acknowledgement, exact-retry repair; resource usage restore/failure tests. |
| High | Legacy Pipeline and Work paths bypassed capture fences or wrote outside the configured captured root; failed State File publication could discard buffered input. | Whole-operation writer fences, local-root reader/writer parity, retain failed buffers; real-process legacy fence tests, including separately isolated private-code cases. |
| High | Release validation omitted mandatory complete-run/workload/held-out/document acceptance checks. | Scorecard v4 exact-count/cohort/split/scenario checks; missing or below-floor evidence refuses release. Actual acceptance remains pending. |
| Medium | Partial connector failure discarded already verified deleted-collection evidence. | Retain deletion/reset evidence while withholding cursor advancement and full-listing authority; adapter/provider lifecycle tests. |
| Medium | Fallback fairness tested membership in SQLite row values, so connector groups collapsed. | Check actual column keys; real SQLite-row regression. |
| Medium | Newer controls/providers were absent from the capability/implementation cohort mapping. | Register action evidence, mail connectors, resources, retention, authority and document/configuration/interchange modules; manifest regression and regeneration. |
| Integration | Inherited tests imported nonexistent BudgetLimits and used invalid Goal evidence prefixes/expectations. | Exercise the actual admission store and authored Goal-ID contract; preserve independent mechanisms rather than adding a second budget API. |

Parallel ownership: recovery agent owns preservation/registry/fences; authority agent owns retirement/transfer;
action agent owns evidence/worker/undo; integrator owns resource/product integration, capability register,
documentation and full validation. Each repair requires a reproduced failure, focused passing checks and
aggregate validation. Agents share local main and do not commit, push or operate live providers independently.

Remaining acceptance sequence after engineering: exact historical native dependency review → independent
checkpoint copy plus restore rehearsal on Mac and Windows → independently current privacy continuity →
held-out extraction/document/Goal review and workload/cost measurements → consented provider parity and
approved action trial → owner UAT and signed release evidence → joint cutover. These are explicit open
gates, not inferred from synthetic fixtures. No native spike, ingestion, key installation or cutover has run.

### Remaining work and shortest path to cutover

| Order | Remaining deliverable | Who / evidence needed | Parallelism and stopping condition |
|---|---|---|---|
| 1 | Finish current aggregate validation, privacy checks and local commits; keep baseline active. | Integrator; current frozen-source gate log and commit IDs. | Independent of native work. No readiness claim from an earlier source snapshot. |
| 2 | Decide reviewed source configuration scope; current `configure` supports look-back only, while source/permission administration remains owner-assisted. | Owner decision requested: typed exact-change source workflow versus explicitly narrower pilot. | Does not imply adult consent or enable cutover. Extend only the approved workflow and run focused/aggregate checks. |
| 3 | Historical key/dependency inventory and separate personal/Work checkpoint closure. | Owner-reviewed exact registry digest; actual OS-store records, current privacy/deletion heads and profile isolation. | Can prepare test corpus/workloads in parallel. Generic keyring cannot independently enumerate every historical dependency. |
| 4 | Mac/Windows restore and independent cloud-copy rehearsal, including former-host stop/retirement and fresh-host privacy continuity. | Separately authorized native observations, recovered copy digest, physical verification and current deletion/permission history. | Both hosts can be checked independently; no normal lost-device activation without independently current privacy history. |
| 5 | Independent held-out source-negative P1/P2, Goal/cross-Domain and document evaluation. | Consented corpus separated by obligation/thread/time; exact cohort; per-field dates/amounts/names >=99%, false confirmations <=0.5%; review sample adequacy. | Can run alongside recovery. Synthetic fixture counters are not acceptance evidence. |
| 6 | Stated-machine cold/warm, limited Digest, long-gap, reset and one-/two-year workload measurements; actual usage/cost and review effort. | Scorecard v4 scenarios, >=95% complete runs, freshness and approved latency/cost bounds. | Can run alongside independent evaluation after source/cohort freeze. Request quotas do not establish assistant dollar billing. |
| 7 | Consented provider parity and the approved reminder trial, including uncertainty/reconciliation. | Owner-approved scopes and action payloads; actual outcomes and supported-host credential behavior. | Cursor sync and held action families remain disabled until corresponding acceptance exists. General conditional-write support is intentionally refused outside supported families. |
| 8 | Owner UAT, exact artifact/cohort review and signed release; joint cutover. | Review all above evidence, then explicitly authorize authority promotion and release settings. | Serial final step. Preserve rollback checkpoint and old-host fence; do not infer absence of uncertain external effects. |

Explicit scope limitations remain visible: retain-all disables automatic expiry (general per-class expiry is
unfinished); unsupported provider conditional-write families remain held; Work content recovery does not create
a second Work authority store; configuration beyond the approved typed surface remains owner-assisted. These
must not be described as universal destination completion. Native observations, consent, labeled outcomes and
owner approvals cannot be produced by autonomous synthetic testing.

### Verified safety checkpoint — local commit `8cde320`

`make check` passed for the frozen safety/release aggregate on 2026-10-24. Exact log:
`/tmp/artha-takeover-verified-check-20261024.log`. This supersedes failed intermediate snapshots;
the source-management continuation approved afterward is a separate slice and is not covered by these counts.

| Gate | Actual result |
|---|---|
| Full isolated suite (excluding restricted Work) | 8,107 passed, 78 skipped, 20 expected failures; 187.88 seconds |
| Core | 2,935 passed; 97.05% line / 93.17% branch; 96.00% combined |
| Changed-line coverage against origin/main | 7,635 measured lines, 248 uncovered; passing 95% gate |
| Security controls | 610 passed, one skipped; 96.09% combined |
| Static | Syntax, both Ruff configurations, imports/state-write ratchet, strict typing (59 core / 3 credential modules), registers and schema passed |
| Privacy | Tracked/unpublished-commit scans, separate staged scan and enabled commit hook passed; no exclusion/bypass added |
| Process/private evidence | Authority143, action311 plus one revocation case, release326, root222 focused cases; preservation486/8 skips plus corrected17-case subset and six separately isolated private-code process cases |
| Synthetic scaling | 30/120/480 Signals: cold 0.064/0.184/0.678 seconds; warm 0.029/0.088/0.336; roughly linear; no provider/model/network latency measured |
| Delivery | Local main `8cde320`; no branch/worktree/push or remote CI run; ignored private Work edits retained locally |

Warnings include dependency/deprecation notices and synthetic SQLite ResourceWarnings. Native key stores,
Mac/Windows/cloud recovery, consented corpus/provider ingestion, production migrations, action effects,
owner UAT and cutover were not exercised. The baseline is active and destination-wide completion is not claimed.

The owner subsequently approved exact-change source add/update/remove configuration. That follow-on is being
implemented and validated separately; cutover/identity/action-release controls and other adults' consent remain
held. Final source-management verification will be recorded after its own source freeze.

## Completion rows 1-7 engineering pass — 2026-10-24 (later)

Seven parallel streams, an integration pass, an independent read-only review and a fix pass delivered the engineering for
rows 1-7 of the completion sequence on local `main` (no branch, worktree or push). Details are in `CHANGELOG.md`. Verified
`make check`: 7,953 isolated, 2,781 core (97.52% combined), 606 security-control passed.

Still open: native Mac/Windows/cloud recovery rehearsals; unfenced `work_domain_writers`, `work_notes`,
`post_work_refresh`, `work_warm_start` and `pipeline.py` home-directory writes; streaming manifest (v3);
spend-seal restore protection on a machine with no seal file; mail/Graph/CalDAV cursors untested against live providers and
all disabled; resource limits and retention periods unset (extraction shows `policy_needed`); document accuracy floors,
held-out corpus, consented ingestion, owner UAT and the signed release. The review's remaining notes: a supplied
extraction answer for a never-admitted request key is still checked against caps; raw readers of `facts` see
pre-review stored values.

## Approved recovery registry — 2026-10-24

The owner approved the profile-scoped registry and requested parallel sub-agents. Three disjoint implementation
streams built the metadata protocol, known native writer integration and checkpoint/review boundary; an independent
read-only review then supplied additional regression cases. All work remains in the existing dirty local `main`
checkout. No branch, worktree, commit, push, live key/store operation, historical review, checkpoint, migration or
cutover was performed.

`artha.recovery-registry.v1` records names, generations, lifecycle and exact historical-review metadata under the
canonical per-user `~/.artha-local/recovery-registry/{personal,work}.json`. It contains no secret values, payloads or
secret-value hashes. Native stores remain secret authority; the existing privacy anchor/journal remains consent
and deletion authority. Profiles reject crossing personal/Work identities, apart from the explicitly shared
`age-key`/`artha` vault dependency. Service/account is one logical identity regardless of kind.

Known key creation/rotation, credential head publication/deletion, client credential writers, bundle enrollment
and privacy-head publication now register pending intent before native mutation, then finalize after verified
readback. A per-logical-record process lock spans the effect; short profile locks serialize metadata only, so
different records remain independent. Failed/killed attempts stay pending. An explicit replacement can publish a
fresh generation after acquiring the record lock; acknowledgement-only retries require the exact pending native
generation. Read-only key/credential/privacy inspection never registers or repairs metadata.

Checkpoint capture uses a reviewed registry, not fake-backend `.store` enumeration. It requires registered native
dependencies, verifies generations and missing/deleted records, captures current credential heads plus retained
generation chunks, and pins/rechecks registry and native records through publication. Retired chunks have no
independent historical digest in the existing credential format; exact bytes/current head and repeated collection
provide consistency, not a new historical-integrity claim. Work creation may explicitly enroll its own checkpoint
key on the canonical registry root; inspection does not. Unsafe paths, pending records, absent historical review
and changed review digests block complete capture before it can claim readiness.

Operating steps are in [the core guide](catchup-core.md#recovery-dependency-registry). First declare only an exact
reviewed historical inventory, inspect its metadata digest, then explicitly attest that digest. This is an owner
attestation of inventory completeness, not native enumeration or custody proof; collection still checks actual
records. No agent signs it automatically. Restored registry metadata remains quarantined installation data and
cannot replace current-host review or current privacy proof. `recovery --restore-keys` remains held.

Repairs discovered during integration: normal-umask lock creation now creates private directories/files and opens
one no-follow descriptor; linked lock targets are not modified. Registry paths refuse unsafe existing metadata
instead of repairing it. Serialized records prevent a superseded native write from hiding behind another active
intent. Conflicting dependency kinds fail before declaration/review. Exact post-publication acknowledgements are
recoverable for keys, credentials and privacy heads. Normal refreshed credentials no longer permanently block
escrow merely because their heads retain prior chunks.

Focused synthetic checks passed (including 461 combined registry/writer/privacy/checkpoint cases, separate-process
contention/death, get/set/delete-only fake backends and ordinary-umask tests). Final aggregate verification:

| Check | Verified result |
|---|---|
| `make check` | Passed; `/tmp/artha-registry-final-check-20261024.log` |
| Full isolated suite, excluding restricted Work | 7,101 passed, 70 skipped, 20 xfailed; 170.63 seconds |
| Core | 2,013 passed; 97.01% line / 92.88% branch; 95.90% combined |
| Changed-line coverage | 4,506 / 4,662 lines, passing the 95% gate |
| Expanded security controls | 595 passed, one skipped; 98.31% combined |
| Static/evidence/privacy | Strict typing (54 core / 3 credential modules), lint, imports, schema, generated registers and tracked/unpushed scans passed; all 12 untracked files passed PII/identifier and gitleaks checks |

The scanner initially flagged two secret-shaped synthetic key literals; fixtures now generate those bytes explicitly,
and the unchanged scanner passed. No exclusion was added. Warnings remain dependency/deprecation notices and test
SQLite ResourceWarnings. Source was frozen before the final full gate; this entry is documentation-only.
Native historical inventory, Mac/Windows and cloud recovery, other unreviewed/manual writers,
full fresh-host privacy continuity and owner UAT remain external gates. This closes the approved registry mechanism,
not every recovery requirement or the broader engineering/acceptance backlog below.

## Current audit and completion plan — 2026-10-24

This continuation reviewed the canonical PRD, technical and UX contracts against re-artha and the controlling
implementation/tests. The later owner decisions govern conflicts: Python owns deterministic processing; storage
authority is per record type; extraction is model-neutral/session-mediated; assignments are accepted by household
convention; consent remains separate; no scheduler, inferred relationship profiling or earned automatic dispatch
is reintroduced. The intended product is evidence-backed household decisions and Goal progress, not merely a
deadline detector or a technically healthy Pipeline.

### Takeover and implemented foundation

Inventory found one branch/worktree (`main`), 73 unpublished commits and both staged and unstaged work. All were
preserved in place. This continuation created no branch/worktree, changed no release/consent setting, and performed
no live ingestion, credential migration, provider effect, key restoration, cutover or push. Staged and unstaged
content remain distinct; generated registers are derived from the final working tree, not the older staged index.

Implemented foundations include typed stages/journals, encrypted evidence and temporal Facts, scoped permissions
and forgetting, guarded publication, source/coverage history, model-neutral extraction exchange, partial-result
preservation, Goal metrics/completion links, responsibilities and convention-based coordination, scoped inspection,
action review/claims/uncertainty/credential brokering, and checkpoint/restore primitives. These are implementation
claims, not proof of complete provider coverage, independent extraction quality, user value or cutover readiness.

### Reproduced defects repaired in this continuation

| Boundary | Failure and repair | Named evidence |
|---|---|---|
| Restore | Substituted paths/frames, profile mismatch or damaged physical output could undermine authenticated restoration. Pin the input, authenticate profile/tree before output, re-read files/databases before transformations; deny dangling/unreadable quarantine markers. | `test_checkpoint_restore_integrity.py`, `test_installation_fence.py` |
| Escrow | A misspelled privacy-anchor schema skipped history replay; missing/corrupt capsules now block. Invalid key versions/suffixes, missing migrated aliases and external privacy dependencies refuse. | `test_state_checkpoint_cli.py` |
| Writer exclusion | Bundle enrollment and Google client writes/deletes bypassed the capture fence. Hold exclusion through verification; enrollment selects the native-only backend. iCloud writes are fenced before backend selection, with one boundary across both setup records. | `test_bundles.py`, `test_credentials_providers.py` |
| Action evidence | Queue validation/receipt consumption could race, reviewed preconditions could change, and preparation failure could misstate a consumed dispatch. Serialize queue operations, bind exact review bytes, recheck metadata generations and preserve uncertainty. | `test_action_evidence_preconditions.py`, `test_action_evidence_dispatch.py`, `test_action_process.py` |
| CalDAV | All-day values were emitted as datetimes; malformed siblings and unmatched requested calendars could disappear under complete coverage. Preserve date-only/exclusive-end values and positives, degrade omissions and refuse malformed filters. | `test_calendar_handlers_coverage.py` |
| OCR bounds | Failed/empty recognition did not consume the page allowance. Count attempted pages before image access, leaving unprocessed pages incomplete. | `test_documents.py` |
| Inspection | Empty/populated Goal text views discarded review gaps. Render bounded sanitized gaps and observations without changing permission filtering. | `test_user_surfaces.py` |
| iCloud setup | Setup mirrored the app password into repository plaintext and allowed file-only persistence. Use native-only keyed storage with verified writes and sanitized errors; do not migrate/delete existing copies implicitly. | `test_credentials_providers.py` |
| Integration evidence | Older fixtures bypassed queue initialization, attempted impossible interleaving inside a write lock, or patched the wrong keyring selector; a register named a removed test. Update fixtures/anchors without weakening runtime checks. | `test_action_bridge.py`, `test_action_hardening.py`, `test_guard_edges.py`, `test_register_manifest.py` |

### What is still missing

| Requirement | Actual engineering gap | Acceptance that cannot be inferred from unit tests |
|---|---|---|
| Preservation, RA-20 | Native OS records cannot be completely discovered via generic keyring. The current `.store` enumeration is a test-backend facility, not native inventory. Known/configured records are captured, but full historical key inventory and fresh-host current-privacy recovery remain incomplete. SQLite capture/reconciliation still holds bounded whole images in memory. | Explicit host inventory, Mac and Windows recovery, independent cloud-copy rehearsal, current deletion/permission continuity and post-switch-change reconciliation. |
| Authority, RA-05/20 | Queue-first transfer intent, matching epochs, crash recovery and pause are implemented. Quiescence/revocation are owner attestations, not an observed credential-revocation service or a fence on a disconnected former host's independent old database. | Stop the old executor or revoke its usable credentials; verify before promotion. Never describe a Boolean as proof. |
| Actions, RA-05/24 | Core-derived Fact/version/subject/permission/freshness checks are wired. Fresh provider-version verification remains unsupported for general action types and refuses rather than silently proceeding. | Provider-specific conditional writes/reconciliation, supported-platform isolation, approved trial outcomes and owner review. |
| Providers, RA-01/08/16 | Calendar pagination/cancellations/partial positives are not full delta synchronization. Opaque cursors, reset/full reconciliation, deleted collections and out-of-window/recurrence removals remain incomplete. | Same handler-to-ledger fixtures plus consented live parity; source omission must never mean completion. |
| Resources, RA-07/16 and NFRs | Extraction admission/backlog is not durably bounded/fair, cancellation is incomplete, and actual usage/hard budgets are not implemented. Size-based estimates remain estimates. | Owner-approved limits, normal/long-gap/reset workloads, real end-to-end latency/cost/review effort. |
| Retention, RA-14/23 | Snapshot pruning and explicit forget do not establish expiry across evidence, history, corpus, cache, traces and backups. | Class-specific periods/exceptions and replay/restore anti-resurrection; no silent deletion policy. |
| Intelligence, RA-11/13/14 | Weekly metrics/completion links are present; leading-indicator links, Sprint calibration and evidence-backed tradeoffs are incomplete. Packs omit retention/permission/jurisdiction/effective-interval/quantity/migration contracts. Several packs exist, but that alone does not prove extensibility without core special cases. | Explicit authored definitions, a complete additional-Domain lifecycle, reviewed cross-Domain and correction-sensitive Goal outcomes. |
| Documents/surfaces, RA-15/20/21 | Field-bound document confirmation, forms/tables/multilingual handling, reviewed conversational configuration, scoped interchange and designated custodian views remain incomplete. Snapshot export is all-or-deny, not scoped interchange. | Consented document accuracy floors, accessible owner workflows and explicitly granted custodian access. High OCR page confidence is not field confirmation. |
| Model/household/hosts, RA-02/04/09/12/18/22 | Source span/schema guards do not prove semantic entailment or arbitrary assistant isolation. Full family-channel revocation, encryption/crash exposure and native platform coverage remain unaccepted. | Held-out source-negative P1/P2 review, consented independent extraction, current host/provider identities, owner UAT and release evidence. |

### Efficient execution order and parallel ownership

Each row requires its short contract amendment/feature spec, named Given/When/Then cases and owner approval of
unsettled decisions before new behavior. Existing approved safety defects may be repaired test-first. Agents use
disjoint files on local `main`; one integrator owns runtime wiring, migrations, generated registers and full gates.
No agent stages, commits, merges, pushes, changes consent or runs providers independently.

| Order / workstream | Deliverable and smallest check | Dependency / completion condition |
|---|---|---|
| 1 — Recovery agent | Explicit dependency/key inventory; all writer fences; bounded capture/restore. Missing/changed/unclassified keys, concurrent rotation and killed processes must block incoherent publication. | Resolve inventory authority below. No key installation while latest privacy history is unverified. |
| 2 — Action agent | General evidence/provider preconditions and authority ceremony; reuse one queue/validator. Correction/revocation/provider conflict before claim/dispatch must refuse; post-dispatch death stays unknown. | May parallel recovery; promotion/release depends on verified preservation and real former-host quiescence/revocation. |
| 3 — Provider agent | One provider's versioned scoped cursor/reset/deletion chain first, then parity on the others. Commit cursor only after evidence/coverage; moved/deleted instances cannot survive silently. | Can run alongside 1/2 after connector-result contract review; integrator owns shared migrations. |
| 4 — Resource agent | Durable request reservations, resumable bounded chunks, fair unknown-content progress, cancellation and usage receipts. Two processes racing the last allowance admit one; missing usage stays unknown. | Approve limits/accounting policy first. Restore cannot refund uncertain spend or grant fresh disclosure. |
| 5 — Lifecycle agent | Retention with existing deletion journal and dependency invalidation; protect unreleased full checkpoints. Interrupted expiry and old restores cannot resurrect erased content. | Approve periods/exceptions first; coordinate ledger migration with 4, not parallel conflicting DDL. |
| 6 — Product agent | Strict pack policy, one authored leading-indicator/Sprint case, scoped interchange and field confirmations; preserve existing authorities. | Can draft contracts in parallel; implement in small approved slices after privacy/resource contracts. |
| 7 — Integrator | Isolated regression, security/changed-line coverage, strict typing/lint, register drift, privacy and coherent documentation. | After every accepted slice; no passing count substitutes for behavior/host acceptance. |
| 8 — Owner + independent evaluator | Held-out corpus, source scopes/consent, document fields, Goal/cross-Domain usefulness, workload/cost, both-host recovery/UAT and signed release. | All dependent engineering gates and preservation evidence; only then discuss cutover. |

### Decisions prepared, not adopted

At the earlier audit the owner was unavailable for the recovery-inventory question. The later approval above
authorizes the registry mechanism, not automatic recovery-key installation. Reviewed alternatives: (A) a durable profile-scoped dependency registry, recording
names/generations before future OS writes and blocking pending entries; (B) an explicit owner-reviewed per-host
inventory bound to code/configuration, re-reviewed after changes. Neither can retrospectively discover unknown
historical records. Native review and current privacy proof remain mandatory. Recommend A for maintained coverage,
with a one-time explicit historical inventory; the OS store stays secret authority and the existing privacy journal
stays permission/deletion authority. Option A is now approved and implemented; actual historical review is pending.

Resource proposal for review, not implemented defaults: at most 16 extraction requests/512 KiB per emitted bundle,
64 admitted attempts across a logical Catch-up and its resumes, three deadline slots then one oldest-unknown slot,
scope rotation and three attempts per work item. Persist admission before disclosure and retain uncertain usage
reservations. The existing $50/month target does not define an enforceable cap for unreported assistant billing.
Choose cash/token accounting, timezone and shadow/evaluation allocations explicitly. Retention needs periods and
dependency exceptions per artifact class; no new durations or automatic deletion are adopted here.

This audit is not a completion or cutover declaration; WP-17/WP-18 and the baseline remain active pending their
remaining engineering and acceptance. The approved repair aggregate passed the following local gate on 2026-10-24:

| Check | Actual final result |
|---|---|
| `make check` | Passed; log `/tmp/artha-completion-verified-check-20261024.log` |
| Full isolated suite, excluding restricted Work | 6,814 passed, 70 skipped, 20 xfailed; 165.47 seconds |
| Core | 1,774 passed; 97.13% line / 92.87% branch; 96.00% combined |
| Changed-line coverage against `origin/main` | 4,330 / 4,464 lines; 96.998% (tool rounds the headline to 96%) |
| Expanded security-control gate | 407 passed, one skipped; 98.69% combined; evidence broker 100% line/branch |
| Static/evidence | Syntax, repository/core Ruff, imports, state-write ratchet, strict typing (53 core / 3 credential modules), generated registers and profile schema passed |
| Privacy | Tracked/unpushed scans passed in `make check`; preserved staged scan and all eight untracked files passed PII/identifier checks and gitleaks separately; hooks path is `.githooks` |
| Host/release | No native-platform recovery, provider trial, owner UAT, consented ingestion, production migration, cutover, commit, push or new CI run |

Warnings remain dependency/deprecation notices and test SQLite ResourceWarnings. The new broker's focused suite also
passes with ResourceWarning and PytestUnraisableExceptionWarning promoted to errors. Earlier failed integration,
coverage and register runs were repaired and are not represented as passes. Source edits ended before the final
gate; this evidence entry is documentation only. All work remains on the single existing local `main` checkout,
with inherited staging preserved and no new commit. New contracts/owner gates above remain unresolved.

The first aggregate gate exposed a stale harness measurement boundary: new evidence-policy/worker code was not
covered by the old test selection, and the new broker was not measured at all. The gate now includes the broker
and its tests without lowering the 95% floor. Synthetic broker wiring/refusal cases reach 100% line/branch;
authority-transfer refusal/process cases reach 100% line/branch; checkpoint CLI combined coverage is 96.42%.
These targeted figures do not replace the aggregate gate or native/platform acceptance.

## Completion continuation — 2026-10-24

The owner authorized implementing the complete audit in this chat. This continues the same completion effort;
it does not grant live ingestion, another adult's consent, provider effects, owner UAT or cutover. The checklist
below remains open until each named requirement has implementation and acceptance evidence.

Local engineering commit `fbbd8a1` (synthetic validation recorded below):

| Audit case | Change and synthetic acceptance |
|---|---|
| Checkpoint completeness | Traversal errors block; final inventory re-hashes files and SQLite images, including same-size preserved-mtime edits and database writes after member capture. Foreign-key violations block capture. |
| Capture/publication | Installation fence covers runtime construction, Catch-up, inspection and action approval; checkpoint retains ledger locks throughout capture. Authenticated ciphertext is verified before write-once publication; directory publication is flushed. Real process death leaves uncommitted ciphertext for explicit review, not a successful backup. |
| Restore safety | Restored action databases use a non-runtime quarantine filename; cutover is disabled and effects paused. An ancestor restore marker blocks runtime construction, incumbent Pipeline, action queue, policy and worker entry points before live storage/credential use. No activation ceremony is supplied by deleting the marker. |
| Historical source times | Contract 0.7.0/migration 0016 records observation and occurrence time per Fact version. Only the current legacy version's proven times are adopted; older missing times stay unknown. Historical explanations cannot borrow current timestamps. |
| Calendar discovery | Graph calendar-list pagination is bounded and deduplicated. Failed, malformed or cyclic discovery retains known calendars with incomplete discovery coverage. |
| Lost keys | Existing ledgers cannot initialize replacement envelope/identity keys. Missing head or material requires recovery; inspection cannot enroll. |
| Release evidence | Scorecard v3 binds every case and measurement to exact implementation, prompts, packs, configuration, contract and pinned model. Extraction v2 checks separately reported response identity; anonymous shadow output is unverified, never new release evidence. |
| Recovery inspection | Owner-approved passphrase-only inspection authenticates the checkpoint in memory, installs no keys and never activates. Current privacy history is still required for normal restore. Escrow ciphertext is authenticated by the manifest, with strict member metadata/order/uniqueness and bounded parser input. |

Still active, in dependency order: complete profile-specific key recovery and fresh-host privacy continuity;
full provider reset/deletion semantics; host/provider identity and isolation acceptance; general action evidence preconditions
and authority transfer; actual usage/hard budgets, fair backlog and retention; the remaining product/extension
contracts below; then consented independent evaluation, Mac/Windows recovery, owner UAT and release decisions.
The local checkpoint tool is committed at the preceding baseline `3b76e55`; earlier "uncommitted" descriptions
are historical. Full-state recovery and cutover protection are not yet established by the current repairs.

Local validation on 2026-10-24 (`/tmp/re-artha-completion-final-check-20261024.log`): 6,198 isolated tests passed,
70 skipped, 20 xfailed; 1,363 core tests passed, 97.20% line / 92.97% branch coverage; 3,060/3,148 changed lines
covered (97.20%); controls 279 passed, one skipped, 98.19% combined. Strict typing, lint, generated registers,
schema and tracked/unpushed privacy checks all passed. The pre-commit privacy hook passed. Warnings include test database
ResourceWarnings, dependency-version and deprecated compatibility notices; passing tests are not owner UAT.

Synthetic preservation benchmark: 6,201 files, including a 5,000-row SQLite store, 43.8 MiB captured plaintext;
create 1.801 s, verify 0.122 s, restore 1.248 s, peak RSS 176.44 MiB. This measures a local synthetic fixture,
not production restore objectives, Windows performance or cloud durability. SQLite images are held in memory;
large-store and large-manifest limits remain explicit scalability work.

Local continuation `2e13660` closes further reproduced boundaries:

- Historical queries compare explicit instants at microsecond precision; equivalent offsets/fractions agree,
  ambiguous cutoffs are rejected, clock rollback preserves ordering, and current review uses the actual current
  version rather than text MAX. Persisted historical version identities remain unchanged.
- Google/Graph reject invalid explicit calendar scopes and nonpositive/noninteger result limits/windows. Graph
  follows empty intermediate pages and marks cycles, page bounds and within-page omissions incomplete. Malformed
  pages/items preserve valid positives and incomplete coverage, including Google data retained for other calendars.
- Owner-confirmed Work vault recovery uses the shared `age-key` / `artha` identity, with separate checkpoint envelope
  keys and passphrase. Work creation is Windows-only, excludes personal ledger keys, rejects repository/known sync
  destinations and copying before publication, and supports passphrase-only inspection without OS keys. Normal
  Work restore is blocked without current privacy proof, including older Work checkpoints passed as personal.
  Capture pins/rechecks escrow keys; recovery authenticates the payload. `ARTHA_LOCAL_DIR` is honoured.

Complete gate `/tmp/re-artha-work-temporal-calendar-check-20261024.log`: 6,262 isolated tests passed, 70 skipped,
20 xfailed; 1,388 core tests, 97.24% line / 93.04% branch; 3,190/3,279 changed lines covered (97.29%); controls
279 passed, one skipped, 98.19% combined. Typing, lint, schema, registers, tracked/unpushed scans and the commit
privacy hook passed. Work tests simulate the platform with synthetic keys/data; they are not native Windows UAT
or proof that the escrowed vault identity decrypts the live vault. No live keys, providers or household data were used.

### Canonical completion continuation — 2026-10-24

Local code `ca53592`, contract 0.8.0, adds owner-reviewed `complete open_item:<id>` and withdrawal through the
existing completion Fact/token mechanism. Current confirmations remove the exact item from open views without
rewriting its file. Changed commitments or withdrawn confirmation reopen the projection. Explicit milestone
`open_item_ref` links use the same current permitted Fact version for Goal metrics and plans, matching accountable
owner, Domain and complete subject scope. Unavailable/mismatched/ambiguous links stay unknown; duplicate links
cannot inflate one Goal's progress. Existing authored terminal statuses retain their legacy meaning but are not
automatic Goal evidence.

The same pass repairs parent/child completion scopes and preserves explicit `subject_id` aliases. Parent subjects
remain co-subjects when a milestone names someone else; their revocation applies to the completion evidence.
Contradictory primary identities require review. Earlier milestone confirmations that did not bind a parent's
`subject_id` need fresh confirmation. Permission generation is checked inside the write transaction; a separate
process revoking consent between review validation and write makes the completion fail. Goal query results recheck
linked records and Fact versions before return.

Complete gate `/tmp/re-artha-open-item-completion-final-check-20261024.log`: 6,291 isolated tests passed,
70 skipped, 20 xfailed; 1,417 core tests, 97.25% line / 93.09% branch; 3,273/3,362 changed lines covered (97.35%);
controls 279 passed, one skipped, 98.19% combined. Strict typing, lint, schema, registers, tracked/unpushed scans
and the pre-commit hook passed. The new file contains 29 synthetic acceptance cases, including a real-process
permission race. Full-suite warnings remain test ResourceWarnings, dependency/deprecation notices. No live
owner UAT, ingestion, external effect, production migration, cutover or push occurred.

### External checkpoint dependencies — 2026-10-24

Local code `d1b7158` inventories the configured action database through the runtime resolver, including an
external file and its committed SQLite WAL contents without unrelated siblings. Capture includes external privacy
capsules, excludes known Work OAuth/recovery artifacts from personal checkpoints, and refuses roots that cross
the Work boundary. Physical installation/action fences are deduplicated; executing or unreadable action state
blocks before key access. Dependency discovery repeats under the fences and before publication. Missing optional
files are distinct from denied metadata; denied metadata blocks without exposing private paths or backend text.

Complete gate `/tmp/re-artha-dependency-inventory-complete-check-20261024.log`: 6,308 isolated tests passed,
70 skipped, 20 xfailed; 1,434 core tests, 97.28% line / 93.15% branch; 3,318/3,406 changed lines covered (97.42%);
controls 279 passed, one skipped, 98.19% combined. Typing, lint, schema, registers and tracked/unpushed privacy
checks passed, as did the pre-commit hook. Fixtures cover separate-process fences, newly appearing dependencies,
path aliases and unreadable metadata. No live stores, keys, providers, migration, recovery or owner UAT were used.

The source audit also found that WorkIQ persists parsed Work records and full narratives in the repository's
`tmp/.workiq_cache.json`; preflight wrote identity-probe output to that same file. This was a source-code finding,
not a live-data observation. The local continuation below repairs those code paths; existing live cache content
has not been inspected or migrated. Full OAuth/signing/anchor recovery, other Work writer fences and native
recovery remain open.

### WorkIQ cache privacy continuation — 2026-10-24

Local code `de6df85` adds encrypted machine-local WorkIQ storage using the owner-confirmed shared OS age identity.
Source records and narratives remain in memory until age encryption; ciphertext is decrypt-verified before atomic
publication. Missing keys, corrupt/malformed/oversized artifacts, linked/reparse directory ancestry and restored
installations refuse without source text or backend details in diagnostics. Concurrent writers merge entries
under a process-lifetime lock; Work checkpoint capture shares it.

Preflight now writes a separate bounded technical receipt without the identity-probe response. Both preflight
entry points reject invalid/future timestamps and surface failed writes. Cache keys bind the connector bytes,
resolved redaction policy, scopes/depth and supplied account/tenant IDs. Policy/code changes cannot reuse old
answers. This does not attest the actual WorkIQ provider account/model. Rich calendar/email/Teams templates now
render their lookback parameters correctly. Read failure is a Connector failure; persistence failure retains
fresh positive records with a visible processing gap.

Legacy plaintext requires an exact reviewed digest and an explicit stopped-writer assertion. Apply preserves and
decrypt-verifies an encrypted archive before replacing the source with an inert read-only marker. Retry verifies
the archive, and old content is never imported as fresh evidence. Real-process contention, merged updates and
SIGKILL before retirement are covered. Native Windows behavior and actual old-writer shutdown remain unobserved;
the new lock cannot control already loaded old code. No live migration was run. Prior synced copies and complete
cache/archive consent, retention and deletion integration remain open.

Personal capture now excludes the Work namespace and known legacy cache/breaker files, including interrupted
cache temporaries. Work inventory includes known legacy cache/OAuth/recovery artifacts separately. A local root
containing the repository refuses, and Work exclusions apply relative to each captured physical root. The private
ignored breaker retains its technical-state location; remaining Work writers and full recovery-key inventory
still need completion. File preservation does not prove recovery of independent OS metadata or secrets.

Final gate `/tmp/re-artha-work-cache-release-check-20261024.log`: **6,360 isolated tests passed**, 70 skipped,
20 xfailed; **1,465 core tests**, **97.33% line / 93.24% branch**; **3,562/3,654 changed lines covered (97.48%)**;
controls 279 passed, one skipped, 98.19% combined. Strict typing (51 core / 3 credential modules), lint, schema,
registers, tracked/unpushed privacy scans and the code commit hook passed. This adds 52 synthetic acceptance cases
against the preceding 6,308-test baseline. Existing dependency/deprecation and test ResourceWarning notices remain.
No live data/key inspection, provider call, migration, owner UAT, cutover or push occurred. The full specification
is still unfinished; the ordered engineering and independent acceptance plan below remains active.

## Completion contract

### Next completion sequence and exit evidence — 2026-10-24

This is the remaining work plan, in dependency order. Finish the implementation and named negative/process
fixtures for each row before requesting its live acceptance. Reuse the current ledger, queue, writers and
privacy journal; do not introduce another authority or a background worker.

| Order | Deliverable | Exit evidence |
|---|---|---|
| 1 | Complete preservation/recovery inventory: every configured/platform store, bundle signing key, OAuth recovery record and privacy anchor; Work writers' fences; bounded large-store/manifest handling | A missing or unclassified dependency blocks capture. Competing writers/rotation/death cannot publish an incoherent checkpoint. Recover independently, replay current privacy history, keep actions non-dispatching, and preserve post-switch changes. Owner reviews native Mac/Windows/cloud-copy rehearsals. |
| 2 | Bind the complete preservation requirement to cutover, and implement authority transfer | A selective rollback snapshot cannot satisfy full-state preservation. Missing/withdrawn preservation or recovery evidence blocks activation. Prove former-executor quiescence or credential revocation before promotion; an old host cannot dispatch. |
| 3 | General action evidence/preconditions | Bind each core-derived proposal to relevant Fact versions, subjects, permissions and provider versions. Recheck at preview, atomic claim and child dispatch. Correction, revocation, expiry or provider conflict refuses before effect; real process deaths/races preserve uncertain outcomes and prevent replay. |
| 4 | Complete provider observation lifecycle | Persist opaque provider cursors only after scoped local commit. Exercise reset, deleted collections, recurrence removal, delayed/out-of-window changes, repeated/failed pages and revoked access through handler→validator→adapter→ledger. No omission becomes inferred completion. |
| 5 | Resource and lifecycle controls | Record actual provider usage separately from estimates; enforce durable request/session/month bounds, cancellation and fair bounded backlog. Apply approved retention across source content, derived/history/corpus/cache/backup artifacts with anti-resurrection checks. Measure sustained query and restore workloads. |
| 6 | Finish product contracts | Extend the implemented canonical completion linkage with leading indicators, Sprint calibration and evidence-backed tradeoffs; strict Domain retention/permissions/jurisdiction/unit/effective-interval/migration contracts; demonstrate an additional Domain without core special cases. |
| 7 | Finish user/recovery surfaces | Conversational setup/configuration, field-bound document confirmation, forms/tables/multilingual handling, accessible textual interactions, permission-scoped interchange and custodian/emergency views. Imports and recovery never grant execution or re-grant consent. |
| 8 | Independent acceptance and delivery | Owner-reviewed held-out corpus and actual workload/cost/latency, host/provider isolation, platform credentials/security, recovery/parity and owner UAT. Keep unknown results explicit, commits local and cutover/held effects disabled until these pass and the owner authorizes release. |

The source audit table below gives requirement IDs and further detail. Rows 1–7 include unfinished engineering;
they are not all waiting on owner UAT. Row 8 requires independent/live evidence that synthetic tests cannot supply.

Preservation follow-ups from the continuing source audit, in implementation order:

1. Finish one shared dependency/key inventory for capture and recovery. Known OS records beyond current escrow
   include `artha.credentials.v1`, `artha.credentials.work.v1`, profile-specific credential-recovery envelope keys,
   `artha-bundle-<producer>` signing material and `artha-privacy-<namespace>` heads. Capturing an OAuth file or replay
   capsule does not recover those independent records. Test missing records, chunk/version histories, concurrent
   rotation/revocation and exact profile separation with synthetic backends before native acceptance.
2. Source-level restore ordering is now verified: the requested profile is checked before extraction, input/member
   fingerprints are rechecked during extraction, and `_verify_restored` validates physical output before privacy
   replay. `tests/catchup_core/test_checkpoint_restore_integrity.py` passes 48 tests with two skips, including
   wrong-profile no-write, changed-input and physical-output mismatch cases. This closes those synthetic source
   gaps only; native platform behavior and owner recovery acceptance remain open.
3. Prove native path behavior: case/Unicode aliases, Windows reserved/trailing-dot names, reparse points and sync
   locations. Current synthetic tests and lexical resolution are not native filesystem acceptance. Exercise supported
   platform fixtures before claiming complete path isolation, and report intended permission transformations.
   The cache migration's generic reparse refusal is conservative: validate legitimate cloud-backed legacy source
   directories separately from unsafe path redirection before claiming Windows migration usability.
4. Complete fences for remaining Work/manual/queue writers and preserve generation through publication. Replace
   whole-database memory images and the single bounded manifest frame with bounded large-store handling, then measure
   sustained capture/restore and post-switch reconciliation. Keep the baseline and independent retained copy protected
   until owner recovery acceptance; do not use the current selective snapshot as cutover evidence.

The 2026-10-21 owner request is one completion effort against the full specification. This document is the
completion checklist; the generated traceability register supplies named fixtures. Code present, synthetic tests,
real-host observations, owner UAT and release acceptance remain separate. No broad completion percentage is asserted.
A discovered regression adds a named acceptance case rather than restarting the implementation as a new project.

### Cutover preservation requirement — 2026-10-22

The owner requires a verified backup of **all state and configuration before cutover**. The concrete sequence,
coverage, expected data impact, stop conditions and rollback rules are in
[the cutover plan](catchup-core.md#cutover-and-preservation-of-domain-state-owner-requirement-2026-10-22).
This is a blocking release requirement, not a claim that a complete checkpoint or restore rehearsal exists.
Current backups are selective; the incumbent GFS utility can publish partial results and copies live database
paths without SQLite online backup. Core encrypted snapshots cover selected artifacts, not recursive installation
state/configuration or OS key recovery. Complete-inventory/fail-closed capture, consistent cross-store generation,
independent retained copy, key/privacy/authority recovery and post-switch-change rollback rehearsal remain open.
Keep the incumbent and Windows Work authority, and cutover=false, until the full gates and owner decision pass.

### Local consolidation and handoff — 2026-10-22

The owner requested that all available branch work and pending engineering changes be committed to local main before
handoff. The inventory found only main, origin/main and one worktree; origin/main is an ancestor. No additional local
branch needed merging. Existing historical agent checkpoint refs are preserved. No remote was fetched or pushed.
The pending privacy implementation is committed as `bbec5de`; documentation and the handoff follow separately.
Read [the handoff](re-artha-handoff.md) before continuing. Ignored private state/configuration/local specifications
remain in place and outside Git; a clean tracked working tree is not a complete data backup or cutover authorization.

### Privacy anchor capacity and continuity — 2026-10-22

Local code `bbec5de` stores bounded OS checkpoint metadata plus authenticated encrypted replay capsules outside runtime
snapshots. It refuses rollback/equal-generation reinterpretation and lost or rewritten tombstone/consent history.
Legacy reads do not migrate; authorized writes verify continuity before publishing a capsule then its OS head.
Only verified OS-anchored predecessor ciphertext may be retired; unknown/orphan recovery files are preserved.
Synthetic Windows credential-size enforcement and real-process publication death/competing-writer cases passed.
The full checkpoint is O(history), with a 32 MiB plaintext limit; this is not a delta journal or emergency key recovery.
Native Windows/Linux credentials and recovery UAT remain open. No live migration, provider call or cutover occurred.
The local feature spec remains active pending owner/platform acceptance.

Full gate `/tmp/artha-wp17-privacy-anchor-check.log`: 6,082 isolated tests, 70 skipped, 20 xfailed (107.13s);
1,252 core, 97.12% line / 92.58% branch; 2,381/2,453 changed lines covered (97.06%); 279 controls passed,
one skipped, 98.18% combined. Strict typing (48 core / 3 credential modules), lint, schema and privacy passed.
Warnings include test database ResourceWarnings and a requests dependency-version warning; no failed check is hidden.

### Latest local closure pass

| Audit boundary | Implemented behavior / executable evidence |
|---|---|
| Correction authority | `require_fact_operation` checks primary and co-subject correction permission before feedback, replay or Fact writes; version checks remain. Corrections preserve validity metadata. |
| Calendar identity | Calendar-scoped event IDs for Google, Graph and CalDAV. CalDAV emits cancellations without dates and distinct expanded-instance IDs; trusted calendar-kind mapping uses stable collection IDs. Source-version hashes include calendar metadata. Legacy erasure continues across namespacing. |
| Cancellation and crashes | Cancellation withdraws only that source's support, preserves independent/user-confirmed support, and makes unsupported obligations unknown. Migration 0011 journals intent before projection changes. A separate process is killed during the write; reopening and recovery completes withdrawal. Evidence withdrawal and its history version share one transaction. |
| Coherent publication | Drafts bind composition and a keyed curated-input generation in addition to Fact versions and permissions. Publication rechecks after the last stage boundary. Ambiguous/live-plus-cancelled versions cannot choose a calendar projection. |
| Current runtime policy | `8c4afe0` checks exact configuration/profile/action-policy, pack, prompt and registered implementation bytes before execution, each stage/fetch/model dispatch and publication. Stale or unreadable inputs require a new Catch-up. Literal boolean, source-shape and bounded integer parsing rejects coercion that could widen access or enable cutover. |
| Migration integrity | `66e91b0` adds migration 0015, exact SQL checksum/name history and canonical schema checks on journaled open (including read-only), migration and integrity preflight. Legacy steps are labelled `legacy_schema_verified`; no historical execution is fabricated. Real process deaths before/after commit and a barrier-controlled two-process upgrade preserve data/version/history. This detects drift/corruption, not arbitrary same-user rewrites. |
| Live release artifacts | `81d7355` rechecks approved clearance/scorecard bytes through publication; verifies rollback against its approved bytes and rechecks changed local file revisions. Withdrawal at COMMIT blocks publication. Trusted local file revisions cache deep snapshot verification; model/code evaluation cohort binding remains open. |
| Kernel cutover fence | `deeec6a` requires literal configured cutover=true in the release validator and honours the runtime environment opt-out. Environment opt-in cannot override a configured false/missing flag. Read-only inspection/local owner safety authority remain available. Quality evidence can be inspected separately from permission to enable execution. |
| Commitment validation | Malformed operations/records, missing schedules and invalid supplied dates stay visible. Shape/type failures become scoped degradation. Unresolved completion strings cannot close work. |
| Completion workflow | `complete milestone:<goal>:<item>`, `complete responsibility:<id>` or `complete household:<id>` previews the exact commitment. An expiring, actor/permission/content-bound token records the accountable owner's explicit confirmation. Evidence resolves to a current permitted Fact version, binds the commitment's identity/content/Domain and subjects. `--reopen` withdraws confirmation through a fresh preview. Old review tokens cannot undo that withdrawal; concurrent Fact changes fail atomically. Authored fields are unchanged; the completion is a derived projection. |
| Goal and Sprint integration | Milestone metrics count current permitted confirmations; changes recompute progress and mark archived Briefings with correction notices. Goal/planning completion versions are pinned through publication and resume. Obligation metrics honor declared Connector/account scopes and exact rolling date windows. Duplicate Goal identities and conflicting/invalid Sprints are visible review gaps. |
| Evidence coordinates | Contract 0.2.0 and extractor v2 use UTF-8 byte offsets, including overlapping chunks, pattern extraction and `why`. Migration 0011 explicitly labels pre-upgrade character spans; history is never silently reinterpreted. |
| Model execution | Arbitrary model CLIs cannot receive private untrusted text through the command extractor. Only the bundled inert pattern extractor can launch, using isolated Python, temporary working directory and a minimal environment. Assistant exchange remains available; the assistant host's own isolation still needs its recorded acceptance. |
| Effect review | Every external effect needs a fresh exact preview even if per-type configuration opts out. To Do sync no longer advertises automatic approval. Held types remain held; instruction sheets remain preparation only. |
| Read-only inspection | Storage/privacy checks remain required. Scheduler inventory cannot block unrelated evidence inspection. Pending cancellation records are hidden until recovery. MCP distinguishes an explicit null request ID from a notification. |
| Legacy action migration | SQLite online backup includes committed WAL content, verifies integrity and publishes a private database atomically without overwriting a concurrent winner. Separate-process death before/after publication and a racing migration are tested. A killed process can leave a private staging file for cleanup. Historical-outcome quarantine now upgrades once with a verified recovery snapshot; authority transfer remains open. |
| Action outcome closure | Historical ambiguous failures and unclaimed executions become unknown; uncertain archives return to the active queue. Unbound approvals require review again. Upgrade marker, status and audit commit together, with separate-process rollback/race tests. Current partial/unrecognized results retain duplicate protection. Audit identities survive archiving. |
| Calendar mutation review | `--show` binds the current provider ETag into the exact queued payload and shows its diff. Dispatch rechecks it and sends `If-Match`; stale/412 responses refuse, uncertain receipts remain unknown, and undo binds the resulting version. Calendar holds remain. |
| Exact review rendering | A complete canonical payload follows the compact preview. Oversized payloads cannot receive a receipt. The hash and renderer share one field mapping; malformed child receipts remain unknown. |
| Verification | Standard `check` includes changed-line coverage. Core combined/line floor is 95%; branch floor remains at least 90%. CI fetches the comparison history. Named synthetic cases appear in the generated register and include their exact test functions. |

The implementation work below is still open; none of these repairs substitutes for those features or live acceptance.
Instruction sheets do not prove completed provider effects. Assigned household work uses the owner's assumed-acceptance
convention; data access remains consent-bound. Restricting a model command does not prove a tested provider integration.

### Live release-evidence coherence — 2026-10-22

Local implementation `81d7355` rechecks approved clearance/scorecard bytes at runtime boundaries and verifies the
rollback artifact at construction and after a changed local file revision. Rollback decoding consumes the exact bytes
whose digest passed, avoiding a second mutable-path read. Publication refuses evidence withdrawn at COMMIT. Inspection
and owner safety controls remain available. The snapshot cache trusts local device/inode/size/mtime/ctime; this is not
protection against a malicious filesystem or arbitrary same-user/root metadata rewriting. Evaluation cohort/model
binding, live release acceptance and the other engineering requirements remain open.

Full gate: 5,959 isolated tests, 70 skipped, 20 xfailed (122.21s); 1,192 core, 97.06% line / 92.41% branch; controls
216 passed, one skipped, 98.30% combined. Typing, lint, generated registers, schema and privacy passed. No provider
request, live ingestion, credential access, owner UAT, push or cutover occurred.

### Current dispatch policy and Windows hook recovery — 2026-10-22

Local code `8c02735` binds one typed actions.yaml revision to the durable execution claim. The parent checks before and
after credential capture; the child rechecks policy, pause, the queue approver, expiry and exact fresh preview before
handler import. Missing pre-upgrade bindings cannot dispatch. Read-only reconciliation retains the existing unknown
claim under revoked effect policy. Per-type hourly/daily limits share the winning queue transaction, use its actual
private database, count each attempt once and retain archived history. A real two-process last-slot race admits one
claim; separate process deaths before/after commit retain matching status, audit, operation and policy metadata.
This is current-policy freshness, not general Fact-version binding or a native-code sandbox. Held types remain held.
Full gate: 6,008 isolated / 1,192 core tests; 97.06% line / 92.41% branch; 2,227/2,294 changed lines covered (97.08%);
265 controls passed, one skipped, 98.30% combined. Typing, lint, registers, schema and privacy passed.

Local code `0d5d67f` repairs shared hook commands containing Mac-only interpreter/checkout paths. Copilot can load
.claude/settings.json and deny tools when its command cannot start. The portable installer preserves permissions,
custom hooks and a local settings backup; setup invokes it. The guard supports Copilot payloads and Windows paths.
Actual sh/PowerShell process tests cover Unicode and paths with spaces. On this host the repair checked 600 state,
Briefing and summary files: zero changed. Core cutover remained false. No live Work command, provider request or
production domain migration was run by the agent.

**Owner-reported Windows UAT:** after syncing/reloading VS Code and retrying a fresh Copilot chat, the owner replied
“Work runs now.” This verifies recovery of that entry point, not every Work command/provider or core platform acceptance.
Full hook gate: 6,022 isolated tests, 70 skipped, 20 xfailed (113.26s); 1,192 core, 97.06% line / 92.41% branch;
2,227/2,294 changed lines covered (97.08%); 279 controls passed, one skipped, 98.18% combined. All checks passed.
Commits remain local. The full re-Artha engineering and external acceptance list remains open.


### Runtime setup failure cleanup — 2026-10-22

Local code `723e165` closes the ledger for every failure/interruption after opening it during dependency construction;
success transfers ownership to Deps. The inner initializer also closes on interruption. Eleven reproduced leaks now
fail safely across fetcher/bundle/extractor/implementation/legacy/default-loader setup. Snapshot retention is checked
against the existing 1..365-day pruner contract at config load, before run work; bootstrap remains independently
1..3660 days. No actual configuration or retained snapshot was changed.
Full gate: 6,039 isolated tests, 70 skipped, 20 xfailed (106.29s); 1,209 core, 97.07% line / 92.45% branch;
2,255/2,320 changed lines covered (97.20%); controls 279 passed, one skipped, 98.18% combined. All checks passed.
No production ingestion, provider operation, data migration, owner UAT, cutover or push was performed by this pass.

### Full-state checkpoint tool (WP-18) — 2026-10-23, local, uncommitted

Streaming, fail-closed checkpoint/restore tool in `scripts/catchup_core/checkpoint.py` and `checkpoint_cli.py`,
per the owner-signed `specs/features/wp-18-state-checkpoint.md`. Synthetic tests only (49 new, 99% line/branch);
full `make check` passed. A read-only inventory of the real installation counted 6,220 personal files and found one
blocking symbolic link; no checkpoint, copy, restore or key escrow has been run on real data. Restore replays this host's current consent and tombstones into restored ledgers (erased Signals, blobs, replay/corpus
records, documents and archived Briefings are removed) and fails closed without a trusted anchor; the restore marker is
not yet enforced by the runtime, and Windows and Work-profile key handling are untested. This does not satisfy the cutover preservation requirement until
those items, the rehearsals and owner UAT are done. Also fixed: a rejected sibling candidate group no longer discards
a Signal's other valid candidates (`coordinator._extract_one`).

## Implemented in WP-15

| Boundary | Result |
|---|---|
| Source replacement | Migration 0010 stores explicit source heads. Opaque versions are never ordered by hash or version text. Ambiguous batches retain evidence and a durable gap. A fresh unique observation can select either version; incomplete observations surface supported positives but cannot withdraw omitted support. Complete replacements resolve obsolete extraction/content gaps. |
| Export | Checks every retained source scope and exact captured artifact, including unprocessed originals and captures. Unknown subject/blob/replay provenance fails closed. Publication rejects changed permission generations or artifact sets. This is an all-or-deny encrypted snapshot, not a permission-scoped interchange format. |
| Plans and responsibilities | Nulls, booleans, numbers and placeholder completion references stay unverified. Duplicate milestone IDs, malformed prerequisite lists and cycles cannot unlock work. Invalid schedules and deferred records remain visible. An authored reference alone never establishes completion; the local closure pass adds versioned owner confirmation. |
| Planning coverage | Read/permission/YAML failures become coverage gaps. Sections are evaluated once, limit omissions are disclosed, and commitments or gaps prevent a quiet verdict. Household visibility uses the configured primary identity. Shared curated files require adult-scoped consent before disclosure. |
| Extraction | Questions, revoked/historical/conditional enum assertions require review. Checks include the surrounding sentence so a selective quote cannot hide a question. These conservative lexical checks do not prove general semantic entailment. |
| MCP and inspection | Malformed names, IDs and arguments produce errors; frames are read with bounded allocations. SQLite inspection is query-only, with no migrations, privacy maintenance, key creation or profile enrollment. Goal queries compute observations without saving them. Responses include freshness metadata. |
| Safety controls | Emergency pause authenticates local authority without a database, keys or scorecard. Owner erasure does not require pilot quality approval. Resume and effect-capable controls retain release gates. |
| Runtime and calendar | Runtime checks ledger/privacy integrity, private directory permissions and background footprint. Failed inventory commands are unknown, not clear. Graph cancellations reach the ledger. Google/Graph records expose stable calendar IDs for trusted calendar-kind configuration. |
| Actions | Proposing cannot invent a preview. Rendering records a compare-and-set payload receipt; preview-gated approval requires its exact hash and a preview no older than 30 minutes. A provider child requires an approved payload-bound queue claim and consumes one dispatch receipt. Manual unapproved child envelopes fail before provider loading. Real undo prepares a reverse proposal for review. |
| Release evidence | Scorecard v2 requires distinct reviewed P1/P2 positives, source-negative review, two cross-Domain outcomes and a correction-sensitive Goal outcome. Boolean, negative and nonfinite numeric bounds/measurements cannot pass. Old v1 evidence must be re-reviewed. |

## Remaining engineering work

| Priority / requirement | Work still needed and acceptance evidence |
|---|---|
| High — RA-05/18/24 | Bind all proposals to relevant Fact versions and fresh authorization; extend provider preconditions beyond the implemented Google Calendar modification path. Action-scoped credentials are implemented in `7efe55f`: exact approved queue claim, one provider, private pipe, read-only normal Python credential APIs and no reusable grants/client secrets in the child. Python containment is not an OS sandbox against arbitrary native code. `8c02735` additionally binds current policy/pause/approver/preview/expiry at dispatch and enforces per-type limits atomically in the real queue. Complete cross-store crash/restore/authority-transfer demonstrations and supported-platform/provider acceptance remain open. |
| High — RA-12/19 | Assigned records use the owner-approved household convention (`b4b157d`). Record-level curated retrieval is implemented in `5aa2688`: resolved subject/co-subject/owner and nested metric/milestone scopes, inherited Sprint permissions, current consent, explicit visibility and conservative legacy authorization. Invisible records cannot consume bounds, inject child validation gaps or create duplicate legacy metadata. Finish full family-channel permission/erasure acceptance and owner UAT; no separate member enrollment/acceptance workflow is required. |
| High — RA-20 | Permission-scoped portable interchange, designated emergency/custodian access, key recovery and authority-transfer ceremonies with tested revocation. Existing snapshots require the trusted key and current privacy history; they are not an emergency recovery solution. |
| High — RA-01/08/17 | Full calendar delta/resync/deletion semantics across providers, reset/reconciliation and collection/recurrence deletion. `479f599` retains validated partial results and the completed watermark. `d8b0316` scopes volume history by configured account/filter and uses the current configured inventory for coverage/header/health; retired rows remain history. Prove provider-shaped saved fixtures and platform outcomes; actual calendar-kind classification remains owner configuration. |
| High — RA-02/04/07/16 | Independent citation-support/semantic evaluation, including selective spans, negation, temporal changes, amounts and people. Keep ambiguous interpretations in review. Exercise all supported failure/recovery transitions with real process kills and retained evidence; deterministic tests alone do not establish model quality. |
| Medium — RA-11 | Leading-indicator links, Sprint calibration, evidence-backed conflicts and other metric histories remain. `ca53592` adds canonical Open Item confirmation/reopening and explicit Goal milestone linkage with current scoped Fact versions. `b71047d` adds on-demand weekly quantity/habit comparisons from dated permitted Facts, exact decimals/windows, correction/forgetting lineage and read-only CLI/MCP access. Missing/conflicting prior evidence stays unknown independently of the current metric. `1cc5806` retains authored next actions/due status. Milestone/on-time metrics and Sprint validation are implemented; broader intelligence and owner acceptance remain open. |
| Medium — RA-25 | Source-aware checks, version-bound typed stop conditions and inert milestone instruction sheets are implemented in `4138b62`. Household ownership follows the owner-approved assumed-acceptance convention; owner acceptance of the full responsibility workflow and named outcome evidence remain open. No external action is authorized by a responsibility or sheet. |
| Medium — RA-14 | Extend Domain-pack contracts for retention, permission rules, jurisdictions/effective intervals, currencies/units and pack migrations. Demonstrate another Domain without adding special cases to the core. |
| Medium — RA-15/20/21 | Finish conversational configuration, accessible interactions and usable recovery/setup flows. Bounded PDF text and local OCR are implemented. Forms, tables, multilingual coverage, field-level confirmation and real document accuracy acceptance remain incomplete. |
| Medium — RA-10/16 and NFRs | Logical Catch-up accounting, active/wait-inclusive latency, durable missing-append detection and bounded telemetry reads are implemented in `bcb7def`. Actual provider usage/cost, bounded/fair ingestion backlog and concurrency, monthly budget enforcement, broader retention and sustained-volume restore/query benchmarks remain open. Cost is still an explicitly labeled size-based estimate. |
| Medium — RA-18 and D-27 | Verify full-disk encryption, core-dump/plaintext exposure controls, the complete in-memory consumer inventory and Windows/Linux host behavior. Incumbent in-memory readers and synthetic age crash scans are implemented. Scheduler inventory and POSIX private-directory checks cover only part of the security preflight. |
| Quality / traceability | Increase branch coverage beyond the current repository floor, especially runtime/recovery/triage paths; maintain the new changed-line check in standard verification. Replace broad requirement-to-file mappings with acceptance-case evidence. Preserve honest unknown/degraded capability states. |

## External acceptance gates

- Configure actual source scopes and identities; each adult grants their own explicit scoped ingestion, disclosure and
  export permissions. Development authorization supplies none of these grants.
- Obtain an owner-reviewed held-out release corpus, measured normal workload, P1/P2/source-negative outcomes,
  two useful cross-Domain cases, correction-sensitive Goal results and real action outcome evidence.
- Perform owner UAT, platform credential/recovery tests, and measured shadow/incumbent parity. This turn used synthetic
  isolated tests and did not contact providers, perform household ingestion, dispatch actions or run live owner UAT.
- Review local commits and run cross-platform CI when remote publication is authorized. No push is authorized here.

WP-15's active local feature specification remains under `specs/features/`; it must not be archived as owner-accepted.

## Local verification

Previous implementation baseline: `42e05ae` (kept local). The latest closure pass results are recorded below after verification.

| Check | Observed result |
|---|---|
| Full isolated suite, excluding Work | 5,167 passed; 80 skipped; 20 expected failures; 83.13 seconds |
| Core suite | 660 passed; 96.33% line coverage, 90.33% branch coverage |
| Hook/policy suite | 33 passed; 96.46% combined coverage |
| Typing and lint | Strict mypy passed for 42 core files; repository and core Ruff passed |
| Other checks | Syntax, import boundaries, state-write ratchet, example schema, generated registers and staged privacy checks passed |
| Synthetic core performance | 1,000 Signals: 0.96 s cold / 0.33 s warm; 4,000: 4.62 s cold / 1.55 s warm. No real model/provider latency; this does not establish live end-to-end p95. |
| Release/host UAT | Not performed; no provider dispatch, production ingestion, remote push or new CI run |

The earlier separate 90% line/branch floors passed; the standard check now requires 95% combined/line and 90% branch. Branch coverage is below 95%; broader runtime,
recovery and triage validation remains on the engineering list above. Passing tests do not close that work or the
external acceptance gates.


## Previous verified local checkpoint

Implementation commit: `1a53d53` (local only). Verified on 2026-10-21:

| Check | Actual result |
|---|---|
| Full isolated suite, excluding Work | 5,236 passed; 80 skipped; 20 expected failures; 87.25 s |
| Core | 725 passed; 95.14% combined; 96.56% line; 90.89% branch |
| Changed-line coverage against origin/main | 584 / 612 changed lines covered (95.42%) |
| Hook/policy suite | 33 passed; 96.46% combined |
| Static controls | Strict mypy for 42 core files, repository/core Ruff, syntax, import boundaries, state-write ratchet, example schema and generated registers passed |
| Privacy | Tracked and staged privacy scans passed; pre-commit hooks passed |
| Recovery | Synthetic separate-process death during cancellation, pending-data denial and successful replay verified |
| External acceptance | No production ingestion, provider dispatch, owner UAT, cutover, push or remote CI |

These results close the reproduced local boundary cases. Remaining engineering and external acceptance above remain
open. The active feature specification is not archived or represented as owner-accepted.


## Previous verified local checkpoint — Goal evidence and action migration

Implementation commit: `0854928` (local only). Verified on 2026-10-21:

| Check | Actual result |
|---|---|
| Full isolated suite, excluding Work | 5,273 passed; 80 skipped; 20 expected failures; 86.82 s |
| Core | 757 passed; 95.21% combined; 96.61% line; 91.02% branch |
| Changed-line coverage against origin/main | 736 / 768 changed lines covered (95.83%) |
| Hook/policy suite | 33 passed; 96.46% combined |
| Static controls | Strict mypy for 42 core files, repository/core Ruff, syntax, import boundaries, state-write ratchet, example schema and generated registers passed |
| Privacy | Tracked, unpushed and staged privacy scans passed; implementation pre-commit hook passed |
| Concurrency/recovery | Separate-process intervening Fact update cannot overwrite a reviewed version; migration process death before/after publication and a concurrent migration preserve complete data |
| Final migration check | Six isolated durability cases pass, including WAL retention, private destination, interrupted publication, racing publication and invalid source cleanup |
| External acceptance | No production ingestion, provider dispatch, owner UAT, cutover, push or remote CI |

This checkpoint adds verified milestone metrics, source-scoped obligation counts, Sprint read validation, reviewed
completion withdrawal and publication/archive dependencies. It also repairs the main-file-only legacy action copy.
It does not close broader outcome/habit intelligence, authenticated household lifecycle, action evidence/provider
preconditions or credential brokering, portable interchange/recovery, provider semantics or the other work listed
above. Forced termination may leave a private migration staging file. Keep the active feature specification open.


## Latest verified local checkpoint — action review and uncertainty

Implementation commit: `19b65bb` (local only), verified on 2026-10-21.

| Check | Actual result |
|---|---|
| Full isolated suite, excluding Work | 5,316 passed; 80 skipped; 20 expected failures; 111.52 s |
| Core | 760 passed; 95.36% combined; 96.74% line; 91.24% branch |
| Core changed-line coverage against origin/main | 760 / 795 changed executable lines covered (95.60%) |
| This pass's action-module changed-line coverage against prior checkpoint | 129 / 131 changed executable lines covered (98.47%); 197 focused tests passed |
| Hook/policy suite | 33 passed; 96.46% combined |
| Static controls | Strict mypy for 42 core files, repository/core Ruff, syntax, import boundaries, state-write ratchet, example schema and generated registers passed |
| Privacy | Tracked/staged privacy scans and implementation pre-commit hook passed |
| External acceptance | No production queue migration, provider call/effect, consent grant, owner UAT, action promotion, cutover, push or remote CI |

Historical quarantine, verified pre-upgrade recovery snapshot, audit-preserving archives, complete review rendering,
Calendar version conditions and partial/malformed outcome protection are implemented. These are synthetic/local
results. General Fact/authorization preconditions, credential brokering, authority transfer and the other engineering
and live acceptance work above remain open. The completion effort continues; its active feature spec stays open.


## Current verified checkpoint — corpus permissions and canonical writers

Implementation commit: `e1b072f` (local only), verified on 2026-10-21. This supersedes the older descriptions that
outcome/habit metrics, local OCR and incumbent in-memory readers were wholly unbuilt. The full specification remains
open; the remaining engineering and external acceptance gates above still apply.

| Check | Actual result |
|---|---|
| Full isolated suite, excluding Work | 5,475 passed; 69 skipped; 20 expected failures; 91.55 seconds |
| Core | 876 passed; 95.49% combined, 96.80% line, 91.61% branch coverage |
| Core changed-line coverage against origin/main | 1,369 / 1,429 changed executable lines covered (95.80%) |
| Hook/policy suite | 33 passed; 96.46% combined coverage |
| Static controls | Syntax, Ruff, imports, state-write ratchet, strict mypy for 46 core files, schema and registers passed |
| Privacy | Tracked/unpushed/staged checks and pre-commit hook passed |
| Host OCR tests | 17 synthetic tests passed outside the nested execution sandbox |

Migration 0013 records explicit Signal/source subjects and corpus lineage. Corpus and inspection coverage apply current
scope permission checks; unknown attribution stays denied. Corpus labels remain encrypted, are permission-rechecked,
participate in snapshots and journaled erasure, and cannot reappear from a restored old snapshot. Frozen returned-output
receipts bind evaluated Alerts to a named Catch-up and implementation/prompt/pack versions. Labels alone never establish
new outcomes. Goal correction labels require distinct before/after observations. `corpus_cli evaluate --catchup-id ID`
joins reviewed labels to that actual retained output; old runs need fresh evidence rather than inferred results.

The canonical Goal writer holds a shared local lock through read/validate/write, checks for intervening edits, flushes
publication, rejects malformed definitions and unknown CLI flags, and exposes owner/source definitions. A real competing
process is refused while the first writer is active; retry preserves both updates. Google cancellation output now retains
calendar ID, with the actual handler-to-adapter-to-ledger chain tested using colliding native IDs.

Test-isolation incident: the first unrestricted full-suite attempt exposed two pre-existing Reminders tests that patched
a platform flag without preventing the import-selected macOS implementation from using EventKit. They read local
reminders; no effects were dispatched. The runtime flag is now checked at entry, and test bootstrap denies native EventKit,
Contacts and ScriptingBridge imports unless a test supplies explicit fakes. The subsequent full isolated suite passed.

Credential relocation/brokering, authenticated household coordination, source-aware responsibilities/prepared artifacts,
scoped interchange/emergency recovery and the other remaining requirements continue in this completion effort. No owner
UAT, live extraction evaluation, provider effect, consent grant, cutover, push or new remote CI is claimed.


## Verified credential checkpoint — 2026-10-21

Implementation `8778994` is local only. Full isolated suite excluding Work: **5,553 passed, 70 skipped,
20 expected failures** (91.72 seconds). Core: **882 passed**, 95.51% combined, 96.81% line, 91.63% branch.
Changed-line coverage against origin/main: 1,413/1,469 (96.19%). Hook/credential gate: 104 passed, one skipped,
98.36% combined. Strict typing, lint, imports, schema, generated registers and tracked/unpushed/staged privacy passed.
Both credential modules reached 100% line/branch coverage in the focused synthetic suite before consumer integration.

OAuth consumers now use bounded, generation-verified OS records with no implicit plaintext fallback. Ledger keys and
privacy anchors select only platform backends; a chained backend cannot fall through to file storage. Personal, Work
Graph and Work MCP records have separate names/services. The shared Graph loader honors scope. Preflight reads actual
records and fails closed when freshness cannot be verified. Test children retain the null-keyring setting even when
they replace their entire environment. Registered provider/control changes invalidate resumed Catch-up generations.

Migration previews bind the exact input generation. OS records and encrypted recovery verify before any original is
sealed; recovery imports reviewed records into the OS store without recreating plaintext files or overwriting a newer
record. Real processes were killed around head publication and at three migration boundaries; retries preserved complete,
recoverable generations. Synthetic Work credential routing passed eight tests in an isolated copy containing only the
ignored Work code overlay, with no Work payloads/providers. That overlay stays local and is not part of the public suite.

**Recorded macOS observation:** the reviewed personal migration imported four OAuth token records and three client
credentials, then sealed all six original files. An immediate new preview reported all six as already sealed. The verified
encrypted recovery snapshot is under the local credential-recovery directory. No provider requests, credential rotation,
Work migration or remote-history deletion occurred. Existing OneDrive history may still contain earlier secret versions;
this operation does not erase it. Windows/Linux storage observations remain unperformed.

The reproduced personal OAuth plaintext fallback is closed. Action credential brokering remains open.
Emergency access, key recovery, authority transfer and household authentication also remain open.
Product outcome evidence and owner UAT are still required.
The active feature specification and the remaining requirements above remain open.


## Verified responsibility checkpoint — 2026-10-22

Implementation `4138b62`, local only. Full isolated suite excluding Work: **5,570 passed, 70 skipped, 20 expected
failures** (88.68 seconds). Core: **899 passed**, 95.57% combined, 96.85% line, 91.77% branch. Core changed-line
coverage against origin/main: 1,510/1,568 (96.30%). Hook/credential gate: 104 passed, one skipped, 98.36% combined.
Strict typing, lint, imports, schema, generated registers and privacy gates passed.

Responsibilities may declare exact `connector:account` sources and a bounded freshness limit. Checks use visible,
attributed complete coverage and refuse pending interpretation, stale/future coverage and unavailable scopes. A typed
stop condition contains `fact:identity@version` plus the exact expected value. It checks current status, provenance,
valid interval, freshness and viewer permission, then pins that Fact version through composition/publication. Changed,
revoked or unavailable proof becomes review-needed. Free-text stop conditions remain authored descriptions and are never
interpreted as executable rules. Duplicate identities, unknown statuses and malformed check definitions need review.

Ready Goal milestones produce inert instruction sheets in the existing encrypted Catch-up draft/Briefing. Authored
instructions are bounded to eight steps of 160 characters; without them the authored milestone is the one-step sheet.
Blocked or unverified milestones do not produce ready sheets. Preparation grants no approval and creates no completion
Fact or external queue effect. Commitment counts count commitments rather than the sheet's display lines. A verified
stop is informational; changing its evidence removes a previously valid quiet claim. Context loading stays read-only.

These are synthetic/local outcomes. Independent household authentication and real owner workflow acceptance remain
open. Logical run metrics, general action evidence/brokering and the other outstanding requirements continue.


## Verified logical measurement checkpoint — 2026-10-22

Implementation `bcb7def` is local only; contract 0.5.0 adds migration 0014. Full isolated suite excluding Work:
**5,590 passed, 70 skipped, 20 expected failures** (87.44 seconds). Core: **919 passed**, 95.61% combined,
96.88% line, 91.85% branch. Changed-line coverage against origin/main: 1,667/1,725 (96.64%). Hook/credential gate:
104 passed, one skipped, 98.36% combined. Strict typing, lint, imports, schema, registers and privacy passed.
The focused metrics module reached 100% line/branch coverage.

Metrics v2 records each invocation's identity, implementation/pack/policy versions, start/end, active time and attempted
stage durations. Before work, the ledger reserves a sequence per Catch-up and implementation. Health reconciles current
reservations against bounded recent telemetry: a wholly lost append stays unknown, a missing resume invalidates history,
and a replay after a terminal result adds no new Catch-up sample. Finished statuses, pending work and unknown records stay
distinct. Complete ratio includes pending/unknown Catch-ups; a partial sample cannot establish the completion target.

Latency aggregation includes prior paused work and waiting time, while reporting active processing separately. Current-code
health cohorts exclude older implementations; mixed or incomplete histories cannot establish a latency pass. Reads take a
bounded tail rather than loading an entire monthly file; the synthetic 8 MiB torn-prefix fixture used under 2 MiB peak
tracked allocation when requesting two entries. Invalid/nonfinite telemetry and prices cannot establish a pass.
Trace v2 gives invocations distinct spans within a shared Catch-up trace and separates shadow/authoritative identities.

Cost includes recorded paused work and remains approximate. `actual_usage_verified` and `budget_enforced` are false;
unknown history cannot establish a monthly-budget pass. This does not invent actual API/subscription usage, model identity,
real workload measurements, owner acceptance or platform evidence. Action evidence/brokering, household identity, portable
recovery and the remaining product/acceptance work continue.


## Verified Domain policy checkpoint — 2026-10-22

Implementation `75b9b5b`, local only. Full isolated suite excluding Work: **5,628 passed, 70 skipped, 20 expected
failures** (87.39 seconds). Core: **957 passed**, 95.67% combined, 96.92% line, 91.99% branch. Core changed-line
coverage: 1,747/1,813 (96.36%). Hook/credential gate: 104 passed, one skipped, 98.36% combined. Strict typing,
lint, imports, schema, generated registers and tracked/unpushed/staged privacy checks passed.

Pack booleans and integer policy fields are validated rather than coerced. A string `event: "false"` can no longer
silently hide an overdue obligation as a past event. Source freshness, lead times, names, authority/value lists,
confidentiality, hints, bindings and collection shapes fail clearly on malformed input; duplicate sources are refused.
Composite versions identify every component, required status is preserved and shared freshness uses the stricter limit.
Conflicting source classifications and ambiguous Domain/predicate ownership are configuration errors. Each predicate
retains its own component calendar, timezone and freshness policy during triage, including cross-Domain calendar checks.
A synthetic Tokyo deadline no longer inherits a school holiday or another Domain's local day.

Migration SQL participates in runtime generations. Future `last_checked` dates cannot postpone responsibility checks or
overflow date arithmetic. These repairs do not complete retention, permission-rule declarations, jurisdiction/effective
intervals, quantity/currency types or pack migration/rollback contracts; those RA-14 requirements remain open.

Household member authentication is awaiting the account/device constraint described in
`specs/features/household-identity-boundary.md`. Independent action/recovery/product work continues. No new owner UAT,
provider call, ingestion, release/cutover, remote CI or push is claimed.


## Verified key/authentication checkpoint — 2026-10-22

Local code `50f7e75` fixes a real two-process key-initialization overwrite, competing rotation and publication recovery.
Google and Graph integration tests exercise their actual authentication helpers without provider requests or real OS
credentials. Unknown freshness fails closed; normal Google use cannot initiate browser login; Work refresh stays scoped.
Full isolated suite: 5,669 passed, 70 skipped, 20 xfailed (89.36s). Core rerun: 979 passed, 95.70% combined, 96.94% line,
92.03% branch. Changed-line coverage: 1,795/1,858 (96.61%). Hook/credential gate: 123 passed, one skipped, 98.36% combined.
Typing, lint, generated-register, privacy and schema gates passed. Core rerun was required because an authorized lessons
update overlapped with its production-file snapshot; no test-caused production write was found. Concurrent refresh
publication and action-scoped credential capture remain open, along with the other completion requirements above.
No live credential/key rotation, provider request/effect, push, remote CI, cutover or owner UAT is claimed.


## Verified household/refresh checkpoint — 2026-10-22

Local code `b4b157d` implements the owner's assumed-acceptance convention; no separate enrollment/response ceremony or
acceptance-delay escalation is required. Explicit visibility is fail-closed and hidden records cannot consume the visible
review limit or disclose their count. Completion still needs verified evidence. This is not a new ingestion consent.
Silent OAuth refresh holds a per-record refresh lock, rereads the current publication and publishes only against its
exact revision. Login and revocation remain possible during refresh; delayed results cannot overwrite them. Six real
process cases cover Google/personal Graph/Work Graph against concurrent login or revocation. Default credential locks
are shared across runtime roots; production migration uses that same canonical scope.
Full local gate passed: 5,696 isolated tests, 70 skipped, 20 xfailed (90.54s); 989 core tests, 96.93% line / 92.03% branch;
1,815/1,879 changed lines covered (96.59%). Hook/credential gate: 140 passed, one skipped, 98.42% combined. All lint,
typing, generated-register, privacy and schema checks passed. No host credential/provider access, owner UAT, push or
cutover occurred. Record-level curated filtering, action credential capture and the other requirements above remain open.


## Verified action credential checkpoint — 2026-10-22

Local code `7efe55f` captures one registered provider credential only after exact queue-claim verification. The private
input pipe binds module, payload hash, operation, reconciliation time, nonce and expiry. The child verifies it before
consuming the claim/importing the handler. Normal credential APIs expose only that provider and deny writes/deletes;
no refresh grant or client secret enters the child. Provider token lifetime must cover the full worker interval plus
refresh margin; each parent authentication HTTP request has a ten-second timeout. Reconciliation retains uncertainty
when credentials are unavailable. Known credential values are redacted from returned receipts; handler diagnostics
are discarded. Handler/type checks precede capture and one shared registry serves executor/worker/broker.
This is registered-Python-handler containment, not an OS sandbox against arbitrary native code under the same user.
Real-child tests exercise the normal Google/Graph/keyring APIs with synthetic scoped tokens and no network, and real
worker death/replay tests prove timeout/parent-loss remain unknown and consume the dispatch once. No held type was lifted.

Corrected earlier typing evidence: the broad lib.* ignore_errors rule had suppressed errors despite CLI --strict. Exact
credential-module overrides now enable strict target checking; imports are followed silently and incumbent modules are
not claimed strictly converted. Three deliberately untyped-module regressions prove this control fails when it should.
Full local gate: 5,756 isolated tests, 70 skipped, 20 xfailed (93.25s); 989 core tests, 95.68% combined, 96.92% line,
92.00% branch; 1,811/1,876 changed lines covered (96.54%). Expanded control gate: 216 passed, one skipped, 98.30% combined;
broker 100% line/branch, worker 96% combined. Typing, lint, generated registers, privacy and schema gates passed. No host
credential access, provider request/effect, owner UAT, push, remote CI or cutover was performed by these tests. General
Fact/fresh-authorization/provider preconditions, curated record permissions, recovery and the other requirements remain.

## Mac personal restore rehearsal — 2026-10-28

Personal checkpoint `ck_20261029T001520Z_80036494` (6,428 files, 1,336,228,642 bytes) verified and restored into an
inactive folder: 6,428 files and 1,332,424,925 bytes verified, one SQLite image verified, ingestion, effects,
schedulers and actions disabled, 6,427 historical members quarantined. The rehearsal found and fixed three defects:
the system keyring was not opened on a real restore, renamed `shadow.*` ledger directories were classed as
authoritative, and each per-record authority check re-parsed three YAML files. Restore time fell from 2h25m to 49m.
An early authoritative ledger and four orphan-row KB backups were moved aside unchanged with SHA-256 manifests. Still
open: comparison of the restored files with live state, an independent second copy, and the Windows and cloud-copy
recovery rehearsals.

### Per-platform harness coverage — 2026-10-28

Owner-approved rule: the security-harness floor stays 85% on every host. Windows measures all selected modules with
`pyproject.toml`. POSIX hosts leave out `quarantine_fs_windows` and its test, and use `tools/coverage-posix.toml`, which
differs only by not measuring blocks guarded by an explicit `sys.platform == 'win32'` or `os.name == 'nt'` test.
No report is merged and no threshold changed. Mac Python 3.14.6 result: 1,546 passed, 105 skipped, **86.31%** combined
(previously 74.36%). Canaries in `tests/dev/test_check_canaries.py` pin both selections and the config difference.

### Independent copy and cloud-copy restore rehearsal — 2026-10-28

The checkpoint `ck_20261029T001520Z_80036494` was copied write-once, ciphertext only, to the OneDrive-synced folder
`ArthaCheckpointCopies` (outside the checkpointed tree); the copy's SHA-256 matches the original
(`43a49eb2…9799`). The owner confirmed the OneDrive upload completed. Verification and an isolated restore from the copy
passed: 6,428 files and 1,332,424,925 bytes verified, one SQLite image verified, ingestion/effects/schedulers/actions
disabled, 6,427 members quarantined, 44m36s. **Limit:** the file stayed present on this Mac, so the restore exercised
the copy as stored here, not a download from OneDrive; the cloud download path and recovery on a different host remain
unexercised. The owner reviews this evidence for the independent-copy requirement.
Full gate rerun on the committed source (Python 3.14: 9,466 passed, 317 skipped, 20 xfailed; 3.11: 9,449 passed,
318 skipped, 20 xfailed; the one failure on each was the stale generated register, since regenerated and committed as
`b6d537c`; the suites were not re-run in full after that fix). `mypy`, `ruff` and `pii-scan` pass.

Owner decision 2026-10-29: the OneDrive copy is accepted as the independent copy for the first slice (kids_school), with
the stated limit above (the cloud download path and recovery on a different host were not exercised).

### First-slice release artifacts signed — 2026-10-29

Owner approval (2026-10-29, "Sign if gates pass") was conditioned on a passing final gate rerun. At frozen commit `695a04c`
the isolated suites pass on Python 3.14 (9,505 passed, 317 skipped, 20 xfailed) and 3.11 (9,488 passed, 318 skipped,
20 xfailed); an earlier run at `4537362` failed one test (stale generated register), fixed by the docs-only `695a04c`
with the release-cohort fingerprint unchanged. Core coverage 88.95%, harness 86.32% (per-platform rule), typing, lint
and privacy scan pass. The slice artifacts (profile `slice`, Domain kids_school; waived gates named: held-out
labeling, document-field accuracy, cross-Domain and Goal cases, workload scenarios, Windows Work recovery, other-platform
observations; not run) were signed at 2026-10-29T21:58:22-07:00 in `~/.artha-release/first-slice/` (outside the repository):
`stage1_clearance.json` 5411b6c7b641…,`slice_waiver.json` 42e7d68c5bc9…,`recovery_rehearsal.json` 7523e9554c22…. They bind checkpoint `ck_20261029T001520Z_80036494` (SHA-256 `43a49eb2…9796`), the OneDrive web copy recovered and
hash-checked at 2026-10-29T21:58:14-07:00, and the pinned model `claude-sonnet-5-5`. `policy.validate_release` passes
against them in memory. **`cutover` remains false**, the release block is not in the config, and the incumbent is
authoritative. Not yet done: fresh pre-switch checkpoint, owner review of 3–5 real briefings, the 14-day observation window
and the explicit switch decision.

### Shadow briefing review and known limits — 2026-10-29

A shadow Catch-up for kids_school (`cu_677719651380`, extraction answered by the pinned `claude-sonnet-5-5` per the session
exchange) produced a degraded briefing. Two failure classes were caused by my first extraction answers (own-Domain
`other_obligations` are rejected) and cleared on resubmission. Owner decision 2026-10-29: **accept the degraded calendar for
the first slice.** Known limits, not defects found fixed: (1) all 23 calendar signals are unattributable (two candidate
subjects, no event names the child) so the core withholds them, the `permission` and `policy` failures remain, and the
ledger hides a source's coverage when any of its signals lacks a recorded subject, so the briefing reports calendar and
school-email coverage as unavailable although the fetch completed; (2) 6 of 13 school signals lack a recorded subject,
cause not yet found; (3) the incumbent's Open Items and 14 encrypted domains were unreadable in that session (vault
locked); (4) the incumbent's own Google Calendar connector drops events (separate known `date_iso` bug). A child-name alias
was tried in the local config and reverted: it attributed nothing, and any policy-config change alters the release cohort
(`configuration`), which would invalidate the signed artifacts; after the revert the cohort equals the signed waiver's and
`validate_release` passes in memory. The signed slice waiver is unchanged. The review so far is one
shadow briefing, not the 3–5 the plan calls for; further briefings accrue during the 14-day window.
