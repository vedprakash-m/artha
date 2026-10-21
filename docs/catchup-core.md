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
