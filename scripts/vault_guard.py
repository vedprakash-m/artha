#!/usr/bin/env python3
# F-C2 shim — contents merged into vault.py. Remove after 2026-06-16.
"""vault_guard.py — F-C2 shim. Use `vault.py check` or import from vault."""
from __future__ import annotations
import warnings
warnings.warn(
    "vault_guard.py is deprecated; functions are now in vault.py.",
    DeprecationWarning,
    stacklevel=2,
)
from vault import check_file_readable, check_all_sensitive  # noqa: F401,E402
# Re-export constants so existing tests that patch vault_guard._ARTHA_DIR still work.
from vault import ARTHA_DIR as _ARTHA_DIR, STATE_DIR as _STATE_DIR, LOCK_FILE as _LOCK_FILE  # noqa: F401,E402
from vault import _STATIC_SENSITIVE  # noqa: F401,E402

import json
import sys

__all__ = ["check_file_readable", "check_all_sensitive"]


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "Usage: vault_guard.py <filepath> | --all"}))
        sys.exit(1)
    if args[0] == "--all":
        results = check_all_sensitive()
        not_readable = [r for r in results if not r["readable"]]
        print(json.dumps({"files": results, "all_readable": len(not_readable) == 0}))
        sys.exit(0 if not not_readable else 2)
    else:
        result = check_file_readable(args[0])
        print(json.dumps(result))
        sys.exit(0 if result["readable"] else (1 if result.get("reason") == "file_missing" else 2))


if __name__ == "__main__":
    main()
