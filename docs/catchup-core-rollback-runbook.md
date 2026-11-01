# Catch-up core rollback runbook — first slice (kids_school)

Scope: the reduced-gate narrow-slice cutover approved 2026-10-28. The incumbent stays the writer of Domain Markdown,
Goals and Open Items, so rollback of this slice is "stop the core writer, keep the incumbent, preserve the core's data".
Procedure follows `docs/catchup-core.md` §4. Nothing here overwrites a live installation.

## Triggers (any one)
A P1/P2 the incumbent found and the core missed; a record missing or overwritten unexpectedly; two writers on one record
type; a critical command broken; an unexpected external effect; a failed integrity or single-writer check; a Catch-up
that fails, is incomplete, or has p95 above 3 minutes during the 14-day window.

## Steps
1. **Stop the core writer.** No Catch-up, scheduled job or action dispatch from the core; leave the incumbent running.
2. **Preserve the current generation first.** Take a quiesced checkpoint of the core's data (`checkpoint_cli create
   --profile personal --dest <new folder>`) before changing anything; record its generation id.
3. **Fence authority.** Disable promotion for the slice; confirm only the incumbent holds the writer role.
4. **Check the incumbent.** Confirm Markdown State Files, Goals and Open Items are unchanged by the core (their
   hashes match the pre-cutover checkpoint's inventory; `checkpoint_cli diff` against the baseline).
5. **Restore the last verified checkpoint to a separate, new folder** (`checkpoint_cli restore <ack> --to <new folder>
   --profile personal`). It must not overlap the live tree; ingestion, effects, schedulers and actions stay disabled.
   Expect about 45–50 minutes on this Mac.
6. **Reconcile post-cutover changes.** Review what the core recorded after the baseline from the step 2 checkpoint; replay
   or discard each reviewed change explicitly. Later forgets and revocations must still hold (restore replays privacy
   history; byte equality with an old checkpoint is not permission to resurrect erased data).
7. **Do not re-execute approvals.** Held effect types stay preview-only; reconcile provider outcomes instead of retrying.
8. **Resume only after** integrity, data reconciliation and single-writer checks pass.

## Limits
Filesystem rollback does not undo external effects (none are enabled for this slice). The restore proves recoverability
of the checkpoint; it does not turn the restored copy into the live installation.

## Evidence
Mac restore from the original checkpoint 49 min and from the OneDrive copy 44m36s (both 6,428 files verified).
The later-forget replay on restore is covered by the synthetic test
`test_restore_replays_later_forgets_revocations_and_archived_briefings`; it was not exercised on real privacy history.
