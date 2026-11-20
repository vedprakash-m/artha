# Authorized development

1. Read the requested specification, applicable contracts and the current implementation before editing.
2. Work only in the canonical OneDrive checkout on `main`, as required by `.github/copilot-instructions.md`; do not create worktrees or task branches. If launched elsewhere, stop and continue in a main-checkout session. Preserve unique work and unrelated changes, never force-switch a dirty checkout, and keep private production payloads out of fixtures.
3. State assumptions and verifiable acceptance cases. The owner's implementation request authorizes routine local fixes;
   it does not manufacture another adult's consent, external-action approval, live UAT or a release decision.
4. Reproduce defects with synthetic, isolated tests, then repair the relevant boundary. Use `tools/run_tests_isolated.py`
   for the full suite; never exercise production persistence to validate code.
5. Run the applicable quality gates, generated-register checks and privacy scan. Record actual results and remaining gaps.
6. For an owner-requested validated main delivery with an enrolled renewal delegation, use its preserved
   `tools/release_renewal.py` evaluator and `deliver` in the user-started session. Stage the exact candidate without
   unrelated work; use an existing exact task approval or an eligible enrolled routine scope. Pass `--quiescent`
   only after the owner confirms that the other machine's source writers are stopped.
7. If approval, preserved evaluator, native authority or applicable evidence is missing, report renewal held; do not
   implicitly enroll, re-sign or activate. A separately authorized Git push may still proceed after applicable delivery
   and privacy checks, but does not renew the release. Report Git, CI and renewal separately.
8. Keep cutover and unreviewed action types disabled until their release evidence exists. Do not replace missing outcomes
   with test counts or inferred capability success. Follow the standing Git delivery instructions once checks pass.

## Two machines, one synced checkout

The Mac and the Windows laptop share this working tree through OneDrive. Before editing, run `git status` and
look at recently modified files; never edit, stage or reformat another machine's uncommitted changes. Use
`handoff.py seal --owner-confirmed` only after OneDrive reports up to date on the outgoing laptop, then
`preflight.py --handoff-check` on the incoming laptop. The check cannot prove that an offline laptop has no pending
upload, so one laptop remains the supported writer. Keep commits local unless the owner separately directs a push.
Never run two Catch-ups or two vault/encrypt operations at once.
