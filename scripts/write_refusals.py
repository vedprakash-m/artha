#!/usr/bin/env python3
"""Inspect or clear the Light-mode machine-local write-refusal marker."""
from __future__ import annotations

import argparse
import json
import sys

from lib.light_mode import require_owner_terminal
from lib.write_refusals import clear_refusals, list_refusals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Light-mode State File write refusals")
    parser.add_argument("command", choices=("list", "clear"))
    args = parser.parse_args(argv)
    if args.command == "clear":
        if not require_owner_terminal("write_refusals.py clear"):
            return 20
        clear_refusals()
        print("write refusals cleared")
        return 0
    try:
        refusals = list_refusals()
    except Exception as exc:
        print(f"write-refusal marker unknown: {exc}", file=sys.stderr)
        return 30
    print(json.dumps(refusals, indent=2, sort_keys=True))
    return 0 if not refusals else 20


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
