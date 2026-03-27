"""preflight/__main__.py — CLI entry point: python -m preflight [options]"""
import sys
import os

# Ensure scripts/ is on the path before bootstrap
_scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from _bootstrap import reexec_in_venv
reexec_in_venv(mode="preflight")

from preflight import main
main()
