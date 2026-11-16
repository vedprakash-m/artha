#!/usr/bin/env python3
"""One-time reviewed adoption; subsequent delivery uses the preserved copy of this entry point."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from catchup_core.renewal_entry import main

if __name__ == '__main__':
    raise SystemExit(main())
