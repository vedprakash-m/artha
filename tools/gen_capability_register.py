#!/usr/bin/env python3
"""Write (or --check) docs/capability-register.md from scripts/catchup_core/capabilities.py."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import json  # noqa: E402

from catchup_core.capabilities import manifest, render_register, render_traceability  # noqa: E402

TARGET = ROOT / "docs" / "capability-register.md"
MANIFEST = ROOT / "docs" / "spec-manifest.json"
TRACE = ROOT / "docs" / "traceability-register.md"


def main(argv: list[str]) -> int:
    text = render_register(ROOT)
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    current = manifest(ROOT, previous)
    if "--check" in argv:
        stale = not TARGET.exists() or TARGET.read_text(encoding="utf-8") != text
        stale = stale or not TRACE.exists() or TRACE.read_text(encoding="utf-8") != render_traceability(ROOT)
        # The local-only spec digest is recorded but never compared: a CI checkout does not have the spec.
        stale = stale or {k: v for k, v in previous.items() if k != "spec_sha256"} != {
            k: v for k, v in current.items() if k != "spec_sha256"}
        if stale:
            print("docs/capability-register.md or docs/spec-manifest.json is out of date: "
                  "run tools/gen_capability_register.py")
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8")
    TRACE.write_text(render_traceability(ROOT), encoding="utf-8")
    MANIFEST.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
