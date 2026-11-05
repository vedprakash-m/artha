# Authorized development

1. Read the requested specification, applicable contracts and the current implementation before editing.
2. Work only in the canonical OneDrive checkout on `main`, as required by `.github/copilot-instructions.md`; do not create worktrees or task branches. If launched elsewhere, stop and continue in a main-checkout session. Preserve unique work and unrelated changes, never force-switch a dirty checkout, and keep private production payloads out of fixtures.
3. State assumptions and verifiable acceptance cases. The owner's implementation request authorizes routine local fixes;
   it does not manufacture another adult's consent, external-action approval, live UAT or a release decision.
4. Reproduce defects with synthetic, isolated tests, then repair the relevant boundary. Use `tools/run_tests_isolated.py`
   for the full suite; never exercise production persistence to validate code.
5. Run the applicable quality gates, generated-register checks and privacy scan. Record actual results and remaining gaps.
6. Keep cutover and unreviewed action types disabled until their release evidence exists. Do not replace missing outcomes
   with test counts or inferred capability success. Follow the standing Git delivery instructions once checks pass.
