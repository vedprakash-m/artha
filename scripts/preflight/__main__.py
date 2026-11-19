"""Thin ``python -m preflight`` entry point for the single preflight CLI."""
import sys
import os

# Ensure scripts/ is on the path before bootstrap
_scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from _bootstrap import reexec_in_venv
_LIGHTWEIGHT = "--read-only" in sys.argv or "--help" in sys.argv or "-h" in sys.argv
reexec_in_venv(mode="lightweight" if _LIGHTWEIGHT else "preflight")

from preflight import main
main()
