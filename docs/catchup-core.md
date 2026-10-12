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
   subjects: [kid1, kid2]
   viewer: owner
   ```
2. Choose the extraction model deliberately and point `ARTHA_EXTRACTOR_CMD` at a command that
   reads the prompt on stdin and prints the JSON on stdout. Without it, extraction fails closed
   per Signal and the run reports itself degraded.
3. Record consent for any adult household member's restricted data (`Ledger.grant_consent`).
4. Run `python scripts/catchup_core/cli.py catchup --shadow --json` (from `scripts/` on `PYTHONPATH`,
   i.e. `cd scripts && python -m catchup_core.cli catchup --shadow --json`). Exit codes: 0 complete,
   10 degraded, 20 blocked, 30 usage error.
5. Compare with the incumbent's Briefing using `shadow.incumbent_items` / `shadow.compare`, label
   the items, and feed the cases to `scorecard.score`. The pilot may be considered for cutover only
   when `Scorecard.passes` is true over the agreed number of Catch-ups and you have signed off.

## Guarantees you can rely on
* Restricted values and evidence excerpts exist only as authenticated ciphertext; a missing key blocks the
  operation instead of writing plaintext.
* Forgetting writes a tombstone, seals a deletion checkpoint outside the restorable set, deletes derived
  Facts and Alerts, and blocks re-fetch and old-snapshot restore.
* One Catch-up ID publishes at most one logical Briefing; a different text needs an explicit correction.
