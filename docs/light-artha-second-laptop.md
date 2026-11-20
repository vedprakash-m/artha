# Light Artha second-laptop checklist

Complete on the incoming laptop before it becomes an active writer.

1. Verify the canonical checkout is on `main` and preserve all uncommitted work.
2. Install `age`; run `python3 scripts/vault.py health`.
3. Provision the same age identity out of band with
   `python3 scripts/vault.py store-key <owner-approved-key-file>`.
4. Verify that identity by decrypting a synthetic artifact for the configured
   recipient. Do not use an `ARTHA_AGE_KEY` environment value as proof.
5. Sign in separately for each enabled provider connector. OAuth tokens are not
   shared between laptops.
6. Run the first machine-local State File baseline:
   `python3 scripts/state_guard.py snapshot`.
7. Report the effective Engine mode:
   `python3 scripts/catchup_core/cli.py health --json`.
8. Stop and confirm OneDrive "up to date" on the outgoing laptop, then run:
   `python3 scripts/handoff.py seal --owner-confirmed`.
9. On the incoming laptop run:
   `python3 scripts/preflight.py --handoff-check`.
10. Treat `unverified` as read-only. The local check cannot prove that an offline
    laptop has no pending upload.

The checklist is complete only when the owner records a real handoff result.
