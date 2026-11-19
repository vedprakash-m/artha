#!/usr/bin/env python3
"""Thin compatible CLI for the :mod:`preflight` check library."""
from __future__ import annotations

import sys
import types
from pathlib import Path

from _bootstrap import reexec_in_venv

_LIGHTWEIGHT = "--read-only" in sys.argv or "--help" in sys.argv or "-h" in sys.argv
reexec_in_venv(mode="lightweight" if _LIGHTWEIGHT else "preflight")

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import preflight as _library  # noqa: E402

_SUBMODULE_NAMES = (
    "integration_checks",
    "runtime_checks",
    "state_checks",
    "api_checks",
    "oauth_checks",
    "vault_checks",
    "coverage_checks",
    "footprint_checks",
    "_types",
)

# Materialize the legacy module surface so callers can inspect and patch these
# names exactly as before. Check definitions still have one package owner.
_exports = dict(vars(_library))
for _submodule in reversed(_SUBMODULE_NAMES):
    _module = sys.modules.get(f"preflight.{_submodule}")
    if _module is not None:
        _exports.update(vars(_module))
globals().update({
    _name: _value
    for _name, _value in _exports.items()
    if not _name.startswith("__") and _name != "_SUBMODULE_NAMES"
})


class _ScriptProxy(types.ModuleType):
    """Keep legacy module patch seams while the package owns every check."""

    def __getattr__(self, name: str) -> object:
        if hasattr(_library, name):
            return getattr(_library, name)
        for submodule in _SUBMODULE_NAMES:
            module = sys.modules.get(f"preflight.{submodule}")
            if module is not None and hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(_library, name):
            setattr(_library, name, value)
        else:
            for submodule in _SUBMODULE_NAMES:
                module = sys.modules.get(f"preflight.{submodule}")
                if module is not None and hasattr(module, name):
                    setattr(module, name, value)
                    break
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ScriptProxy

if __name__ == "__main__":
    _library.main()
