# UAT scenario scripts (re-artha)

These scripts produce bounded engineering evidence for requirements with existing implementation and tests.
Some have recorded live-safe observations; a passing report does not imply full live acceptance.
Each script runs the documented `catchup_core` CLI commands in order, checks each acceptance condition, and
prints a JSON report:

```json
{"requirement": "RA-06", "title": "...", "target": "scratch|live", "run_at": "...",
 "steps": [{"command": "...", "kind": "...", "expected": "...", "observed": "...", "pass": true}],
 "skipped": 0, "limitations": ["..."], "pass": true}
```

The exit code is 0 only if every step that ran passed. Each script's docstring lists every command it runs, the
step kind, and what that command writes.

## Modes

- `--scratch --workspace DIR`: builds a synthetic workspace (synthetic config, in-memory keyring, fixed clock
  2026-10-12 09:00 UTC, viewer `owner`) in a temporary directory under `DIR`, runs every step, then deletes it
  (`--keep` keeps it). No real data, keyring or network is used. `tests/uat/test_uat_scripts.py` runs every
  script this way.
- `--workspace CHECKOUT` (live): runs the real CLI (`python3 scripts/catchup_core/cli.py ...`) as subprocesses
  against that checkout's configuration and local storage. All commands use `--shadow`.

Step kinds and whether they run live:

| Kind | Live | Meaning |
|---|---|---|
| `read` | yes | read-only view (beliefs, why, health, history, review, MCP reads) |
| `preview` | yes | preview or refusal; writes nothing |
| `local-write` | only with `--allow-local-writes` | writes encrypted files in local storage or a temporary directory; never sends, uploads or changes connectors |
| `scratch-only` | no, recorded as skipped | changes the ledger or configuration (apply, forget, feedback, review labels) |
| `library` | no, recorded as skipped | calls a `catchup_core` function where the CLI has no surface |

Scratch-mode deviation: mutating commands normally run a host footprint check (the `ArthaEngine` scheduled task).
A synthetic workspace has no scheduled task, so scratch replaces only that check with a passing stub; the storage,
integrity and privacy checks still run. `scripts/catchup_core` is not modified.

## Scripts

Run on the Mac live host from the checkout (`~/Artha`); keep reports outside the checkout:

```sh
mkdir -p ~/uat && cd ~/Artha
python3 tools/uat/<script> --workspace ~/Artha --output ~/uat/<ra>.json [--allow-local-writes]
```

| Script | Requirement | Live command | Evidence to record |
|---|---|---|---|
| `ra_06_corrections.py` | RA-06 Corrections and deliberate memory | `python3 tools/uat/ra_06_corrections.py --workspace ~/Artha --output ~/uat/ra06.json --allow-local-writes` | Report; beliefs and why output for one real Fact; the correct preview (before/after) with beliefs unchanged; the stale-version refusal; the encrypted `inbox/cap_*.ae1` from `capture` |
| `ra_09_privacy_mcp.py` | RA-09 Privacy, Work isolation, read-only MCP | `python3 tools/uat/ra_09_privacy_mcp.py --workspace ~/Artha --output ~/uat/ra09.json` | Report; MCP `tools/list` (all `readOnlyHint`, none destructive); `propose_correction`/`propose_feedback` return `applied: false` and beliefs are unchanged; the `viewer` argument and unknown tool are refused; the CLI `--actor` mismatch is refused |
| `ra_15_onboarding_feedback.py` | RA-15 Onboarding, correction and feedback | `python3 tools/uat/ra_15_onboarding_feedback.py --workspace ~/Artha --output ~/uat/ra15.json` | Report; health/history/items/goals output; configure preview with the config SHA-256 unchanged; shadow-apply and invalid-value refusals; MCP feedback inert |
| `ra_19_household.py` | RA-19 Household coordination (read-only) | `python3 tools/uat/ra_19_household.py --workspace ~/Artha --output ~/uat/ra19.json` | Report; health RA-19 state; refusal for an unknown task. Completion, reopen and visibility run in scratch only |
| `ra_20_export_emergency.py` | RA-20 Portable export, emergency access | `python3 tools/uat/ra_20_export_emergency.py --workspace ~/Artha --output ~/uat/ra20.json --allow-local-writes` | Report; snapshot v1 not dispatchable and without plaintext; interchange `--read` (`executes_nothing`); CLI restore refused ("governed checkpoint"); emergency view not dispatchable |
| `ra_21_documents.py` | RA-21 Document ingestion, text and local OCR | `python3 tools/uat/ra_21_documents.py --workspace ~/Artha --output ~/uat/ra21.json --allow-local-writes` | Report; encrypted `documents/doc_*.ae1` without plaintext; identical re-ingest; type, size and unknown-subject refusals; health RA-21 "last succeeded"; a hand-run OCR of one real scan |
| `ra_23_beliefs_forget.py` | RA-23 Beliefs view, forget receipts, third-party data | `python3 tools/uat/ra_23_beliefs_forget.py --workspace ~/Artha --output ~/uat/ra23.json` | Report; beliefs with `--domain`/`--subject` filters; why; no third-party leak. Forget and its receipt run in scratch only |

Live `--allow-local-writes` writes only: one encrypted capture (RA-06), one synthetic encrypted document that then
waits for review at the next Catch-up (RA-21), and encrypted snapshot/interchange files in a temporary directory
that the script deletes (RA-20, RA-23). Without the flag these steps are recorded as skipped.
Do not enable it merely to improve a completion percentage: it creates synthetic records in the live inbox.
Live correction apply, forget, feedback and restore/replay remain excluded where the report says scratch-only.

## WP-19 Work cache checks

On Windows PowerShell, `python tools\uat\wp_19_work_cache.py --scratch --workspace <empty-dir>` exercises migration and the WorkIQ health
receipt with synthetic inputs. It has no live mode and does not replace owner UAT. The separate isolated test
`python tools\run_tests_isolated.py tests\catchup_core\test_state_checkpoint_cli.py -q -p no:cacheprovider -k work_inventory_separates_all_known_artifacts_without_disclosing_names`
checks synthetic personal/Work inventory separation. The live inventory CLI reports only counts, not file
membership; its output alone is not proof of live separation. A read-only Work Briefing does not guarantee a
health receipt: the WorkIQ preflight check may reuse a fresh receipt or probe the provider and write one.
Running a live probe requires separate authorization.

The local-only `python tools\run_tests_isolated.py --private-work --evidence <new absolute JSON path outside
the checkout and sync roots>` option runs only the reviewed synthetic Work producer selection. It fingerprints
the public and allowlisted private source bytes, suppresses pytest output and writes metadata-only evidence.
It is not shared CI, live provider verification or owner UAT.

## Coverage

All seven requirements are partly exercised: every acceptance condition reachable through the CLI passes in
scratch; the rest is listed in each report's `limitations`.

| Requirement | Exercised | Not exercisable through the CLI without code changes |
|---|---|---|
| RA-06 | beliefs, why, correct preview/apply (`user_confirmed`), stale-version refusal, history, review case, encrypted capture | Replay anti-resurrection needs a Catch-up with connector data; no replay command |
| RA-09 | read-only MCP tool list and inert proposals, argument and tool refusals, actor mismatch, third-party hiding in CLI and MCP | Work isolation has no `catchup_core` CLI surface (covered by synthetic tests); live Work migration/recovery acceptance remains open |
| RA-15 | health, history, items, goals; configure preview/apply and refusals; feedback, review label, health feedback fields; MCP feedback inert | Accessible first-use is a human UAT; bootstrap to a first Catch-up needs real credentials |
| RA-19 | complete preview/apply/reopen, owner-only completion, visibility filtering, coordination lines (library) | Consent by a second real adult; coordination lines appear only inside a Catch-up Briefing |
| RA-20 | export, interchange write/read, third-party omission, emergency view, restore refusal | Custodian with grants needs a second configured viewer; second-host recovery and accessibility need live hosts and a person; scratch restore is shown through the library |
| RA-21 | PDF text-layer ingestion, encryption, identical re-ingest, refusals, extracted page (library), health | Local OCR needs tesseract and a scanned image (step skipped); accuracy floors need a labelled set; Facts appear only after the next Catch-up |
| RA-23 | beliefs and filters, why, forget receipt, Fact removal, suppression and tombstone (library), restore replay (library), third-party hiding | Governed checkpoint/quarantine restore is live-only; a consented member's own view needs a second person |

Observed behaviour worth knowing before a live run: `export` refuses the whole snapshot ("owner may not view
fct_...") when any Fact is not viewable by the owner, for example an unconsented adult's Fact, while `interchange`
omits such Facts and counts them in `omitted_facts`. A live RA-20 or RA-23 export can therefore fail on a
household with such Facts; RA-20 records either outcome, as long as nothing leaks.
