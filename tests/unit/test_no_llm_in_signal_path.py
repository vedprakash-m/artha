"""tests/unit/test_no_llm_in_signal_path.py — DEBT-021: No LLM imports in signal path.

Invariant: The email→signal extraction path must never call an LLM.
This test uses AST scanning to enforce the invariant statically.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# Modules that form the signal extraction path — none may import LLM clients.
_SIGNAL_PATH_MODULES = [
    _REPO / "scripts" / "email_signal_extractor.py",
    _REPO / "scripts" / "pattern_engine.py",
    _REPO / "scripts" / "actions" / "base.py",
]

_LLM_KEYWORDS = frozenset([
    "openai",
    "anthropic",
    "claude",
    "langchain",
    "litellm",
    "llm",
    "together",
    "cohere",
    "completions",
])


def _scan_llm_imports(path: Path) -> list[str]:
    """Return list of LLM-related import module names found in *path*."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(kw in alias.name.lower() for kw in _LLM_KEYWORDS):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if any(kw in mod.lower() for kw in _LLM_KEYWORDS):
                hits.append(mod)
    return hits


class TestNoLLMInSignalPath:
    @pytest.mark.parametrize("module_path", _SIGNAL_PATH_MODULES)
    def test_no_llm_imports(self, module_path: Path):
        """Each signal-path module must have zero LLM-related imports."""
        assert module_path.exists(), f"Signal-path module not found: {module_path}"
        hits = _scan_llm_imports(module_path)
        assert hits == [], (
            f"{module_path.name} imports LLM libraries — violates no-LLM-in-signal-path invariant: {hits}"
        )

    def test_deterministic_output_for_identical_input(self):
        """Identical input records produce identical output across 3 runs (A2)."""
        if str(_REPO / "scripts") not in sys.path:
            sys.path.insert(0, str(_REPO / "scripts"))

        from email_signal_extractor import EmailSignalExtractor  # type: ignore[import]

        email_records = [
            {
                "id": "e001",
                "subject": "Your bill is due — please pay by December 31",
                "from": "billing@utility.com",
                "snippet": "Your electricity bill of $120 is due on 2026-12-31.",
            }
        ]

        results = []
        for _ in range(3):
            extractor = EmailSignalExtractor()
            signals = extractor.extract(email_records)
            # Normalise: strip detected_at timestamp (non-deterministic) for comparison
            normalised = [
                {k: v for k, v in s.__dict__.items() if k != "detected_at"}
                for s in signals
            ]
            results.append(normalised)

        assert results[0] == results[1] == results[2], (
            "EmailSignalExtractor produced different output for identical input"
        )


# ---------------------------------------------------------------------------
# DEBT-EVAL-003: Module-load boundary test — runtime import interception
# ---------------------------------------------------------------------------
# Patches sys.modules so any dynamic anthropic/openai import raises ImportError,
# then verifies signal-path modules can still be loaded and run correctly.
# Catches __import__ / importlib.import_module calls missed by AST scanning.
# ---------------------------------------------------------------------------

class TestNoLLMModuleBoundary:
    """Verify signal-path modules don't touch LLM clients at module load or runtime (DEBT-EVAL-003)."""

    def test_email_extractor_loads_without_llm_modules(self, monkeypatch):
        """EmailSignalExtractor must load and run when anthropic/openai are absent."""
        import importlib
        import types
        from unittest.mock import patch

        # Build a mock that raises AttributeError on any attribute access
        class _BlockedModule(types.ModuleType):
            def __getattr__(self, name: str):
                raise ImportError(f"LLM client '{self.name}.{name}' accessed in signal path — DEBT-EVAL-003 violation")

        _anthropic_block = _BlockedModule("anthropic")
        _openai_block    = _BlockedModule("openai")

        signal_path_mods = [
            k for k in list(sys.modules.keys())
            if k.startswith("email_signal_extractor") or k.startswith("pattern_engine")
        ]
        for mod in signal_path_mods:
            del sys.modules[mod]

        with patch.dict("sys.modules", {"anthropic": _anthropic_block, "openai": _openai_block, "litellm": _BlockedModule("litellm")}):
            if str(_REPO / "scripts") not in sys.path:
                sys.path.insert(0, str(_REPO / "scripts"))

            # Must not raise — no LLM client should be accessed
            from email_signal_extractor import EmailSignalExtractor  # type: ignore[import]
            extractor = EmailSignalExtractor()
            signals = extractor.extract([{
                "id": "llm_boundary_test",
                "subject": "Rent is due tomorrow",
                "from": "landlord@test.com",
                "snippet": "Please pay your rent by 2026-12-31.",
            }])
            # Result may be empty (no patterns triggered) — that's fine
            assert isinstance(signals, list), "extract() must return a list"

    def test_pattern_engine_loads_without_llm_modules(self, monkeypatch):
        """pattern_engine must be importable even when anthropic is absent."""
        import types
        from unittest.mock import patch

        class _BlockedModule(types.ModuleType):
            def __getattr__(self, name: str):
                raise ImportError(f"LLM client '{self.name}.{name}' accessed — DEBT-EVAL-003 violation")

        sig_mods = [k for k in list(sys.modules.keys()) if k.startswith("pattern_engine")]
        for mod in sig_mods:
            del sys.modules[mod]

        with patch.dict("sys.modules", {"anthropic": _BlockedModule("anthropic"), "openai": _BlockedModule("openai")}):
            if str(_REPO / "scripts") not in sys.path:
                sys.path.insert(0, str(_REPO / "scripts"))
            import pattern_engine  # type: ignore[import] # noqa: F401
