#!/usr/bin/env python3
"""Enforce line and branch floors independently; a combined average can hide gaps."""
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--minimum", type=float, default=85)
    parser.add_argument("--branch-minimum", type=float, default=None)
    args = parser.parse_args()
    root = ET.parse(args.path).getroot()
    rates = {kind: float(root.attrib[f"{kind}-rate"]) * 100 for kind in ("line", "branch")}
    print("Core coverage: " + ", ".join(f"{kind} {value:.2f}%" for kind, value in rates.items()))
    branch_floor = args.minimum if args.branch_minimum is None else args.branch_minimum
    return int(rates['line'] < args.minimum or rates['branch'] < branch_floor)


if __name__ == "__main__":
    raise SystemExit(main())
