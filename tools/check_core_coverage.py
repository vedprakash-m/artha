#!/usr/bin/env python3
"""Enforce line and branch floors independently; a combined average can hide gaps."""
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--minimum", type=float, default=90)
    args = parser.parse_args()
    root = ET.parse(args.path).getroot()
    rates = {kind: float(root.attrib[f"{kind}-rate"]) * 100 for kind in ("line", "branch")}
    print("Core coverage: " + ", ".join(f"{kind} {value:.2f}%" for kind, value in rates.items()))
    return int(any(value < args.minimum for value in rates.values()))


if __name__ == "__main__":
    raise SystemExit(main())
