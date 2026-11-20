# Artha Light mode: OneDrive conflict runbook

Status: active for file-based operation on one active laptop at a time.

OneDrive may preserve divergent edits as a sibling file, but that behavior is not
guaranteed. Local checks detect visible copies; the owner-attested handoff remains
the primary protection.

## Before a handoff

The seal is normally automatic: a Stop hook runs `handoff.py auto-seal` after each turn on the active laptop. It seals
only in Light mode, only when this laptop has accepted the current manifest and the in-scope files changed, and it
keeps every refusal (conflict copy, uninspected write refusal). It prints nothing unless it refused. To seal by hand
(first seal, or after a refusal):

1. Finish the session and stop scheduled/background Artha writers on the outgoing laptop.
2. Wait until OneDrive reports that the folder is up to date.
3. Run:
   ```bash
   python3 scripts/handoff.py seal --owner-confirmed
   ```
4. Do not claim a handoff when the command is refused or reports a conflict.

On the incoming laptop:

```bash
python3 scripts/preflight.py --handoff-check
python3 scripts/handoff.py verify
```

`verified` means only that this laptop's visible files match the sealed manifest. It
does not prove that an offline laptop has no pending upload.

## When a conflict exists

Do not select a winner from `updated_at`, filename, or "later modified" alone.

1. Stop writers on both laptops.
2. Run `preflight --handoff-check`; record the exact target and conflict-copy names.
3. Preserve the original and conflict copy outside the synced tree:
   ```bash
   cp "state/target.md" ~/.artha-local/conflict-quarantine/
   cp "state/target-HOST.md" ~/.artha-local/conflict-quarantine/
   ```
   On Windows, use the equivalent owner-private copy into the local Artha directory.
4. For encrypted files, inspect with `vault.py read <domain>` only. Do not decrypt
   a conflict copy to a plaintext file in OneDrive or paste sensitive content into a
   normal agent transcript.
5. Review the record-level differences. Choose no change, keep the original, keep
   the copy, or make one reviewed merge.
6. Publish the reviewed result through the normal safe writer:
   - plain Markdown: `lib.state_writer` / the owning command;
   - encrypted State File: `vault.py read <domain> --json`, then
     `vault.py write <domain> --expected-revision <token>`.
7. After publication and verification, move the reviewed conflict copy out of
   `state/`; retain it in `~/.artha-local/conflict-quarantine/`.
8. Run preflight again. Any new conflict copy during resolution stops the procedure.

## Recovery

- `state_restore.py list <state-file>` lists local recoverable revisions.
- `state_restore.py show <state-file> <snapshot>` displays a selected revision.
- `state_restore.py apply <state-file> <snapshot>` restores through the safe writer.
- A plaintext snapshot is encrypted and machine-local. Encrypted `.age.v1..v3`
  revisions are recovered on the laptop that has them.
- `state_guard.py snapshot|verify|drill` remains the off-repo disaster-recovery path.

## What not to do

- Do not use `git show HEAD:state/...`; ignored State Files are not recoverable from Git.
- Do not bulk-run `vault.py decrypt` in the OneDrive checkout.
- Do not silently delete both variants.
- Do not use filename timestamps as authority.
- Do not continue writes while the conflict state is unknown.
- Do not describe a local CAS success as prevention of an offline cross-laptop race.
