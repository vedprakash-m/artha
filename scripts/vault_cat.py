#!/usr/bin/env python3
"""vault_cat.py — print one encrypted State File without writing plaintext to disk.

Replaces the "decrypt, then read in a later tool call" pattern (WP-06, §10.6): the
private key comes from the OS credential store, decryption happens in memory and the
plaintext goes to stdout only.  Exit codes: 0 ok · 2 key/binary unavailable · 3 error.

Usage: python3 scripts/vault_cat.py <domain> [--state-dir DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catchup_core.vault import AgeVault, VaultError, VaultUnavailable  # noqa: E402


def main(argv: list[str] | None = None, vault: AgeVault | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    state_dir = Path(__file__).resolve().parent.parent / "state"
    if "--state-dir" in args:
        i = args.index("--state-dir")
        state_dir = Path(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: vault_cat.py <domain> [--state-dir DIR]", file=sys.stderr)
        return 3
    try:
        if vault is None:
            import foundation  # noqa: PLC0415
            vault = AgeVault(state_dir, identity=foundation.get_private_key, recipient=foundation.get_public_key())
        sys.stdout.write(vault.read(args[0]))
        return 0
    except VaultUnavailable as exc:
        print(f"vault unavailable: {exc}", file=sys.stderr)
        return 2
    except (VaultError, SystemExit) as exc:
        print(f"vault error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
