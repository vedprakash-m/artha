# Implementation status — 2026-11-20

## Operating mode

Light mode is the default and only operating mode: file-based Catch-up on either laptop, one active laptop at a time,
automatic sealed handoff (`handoff.py auto-seal`, Stop hook) checked on arrival (`preflight.py --handoff-check`).
`cutover` stays `false` in `config/catchup_core.yaml`. Details: `specs/artha-tech-spec.md` §43,
`docs/sync-conflicts.md`, `docs/light-artha-second-laptop.md`, `config/workflow/development.md`.

The Catch-up Engine (`scripts/catchup_core/`, `docs/catchup-core.md`) is dormant. There is no authority host, release
signature, renewal or retirement process, and no reversible Engine rollback; re-enabling the Engine would need its own
specification, evidence review and owner approval.

## Known limits

- Offline concurrent writes are detected best-effort, not prevented; the handoff cannot prove the other laptop stopped.
- Five writers outside the 44-file Markdown allowlist still publish directly (`pr_manager`, `ai_trend_radar`,
  `home_intelligence`, `home_device_monitor`, `career_state`).
- Held: the offline sync lab (S0a) and a recorded Windows to Mac return handoff.

## History

Earlier status, Windows checkpoints and the re-artha handoff are in git history and `CHANGELOG.md`; the closed
specifications are in the owner-local `.archive/specs/`.
