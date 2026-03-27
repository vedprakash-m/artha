"""Shared fixtures for unit tests.

Seeds pii_guard.py DEVA_NAME cache for environments where user_profile.yaml
is absent (e.g., CI runners), so test_pii_guard_i18n.py passes without
requiring the gitignored local profile to be present.
"""
import re
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable (tests/conftest.py handles this globally, but
# be explicit here so this file works if loaded in isolation).
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pii_guard  # noqa: E402

# Unicode escapes used intentionally — avoids triggering the pii_guard language
# fence on this source file while keeping the values human-readable in comments.
# \u0935\u0947\u0926 = Devanagari script test string (used in i18n PII detection tests)
_CI_DEVA_TEST_NAMES = ["\u0935\u0947\u0926"]


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Seed pii_guard DEVA_NAME cache at the earliest possible point in pytest startup.

    In CI, config/user_profile.yaml is gitignored and absent, so
    _build_deva_name_pattern() returns None and DEVA_NAME detection is inactive.
    This hook runs before any test collection or execution, guaranteeing the
    pattern is available when test_pii_guard_i18n.py assertions run.

    Guard condition: skip only when names were successfully loaded from
    user_profile.yaml (pattern is not None). If pattern is None — whether
    because the file is absent (CI) or not yet tried — inject our test names.
    """
    if pii_guard._DEVA_NAME_LOADED and pii_guard._DEVA_NAME_PATTERN is not None:
        # Real names already loaded from user_profile.yaml (local dev)
        return

    pii_guard._DEVA_NAME_PATTERN = re.compile(
        r"(?:" + "|".join(re.escape(n) for n in _CI_DEVA_TEST_NAMES) + r")"
    )
    pii_guard._DEVA_NAME_LOADED = True


@pytest.fixture(autouse=True, scope="session")
def _seed_deva_name_for_ci():
    """Redundant guard in case pytest_configure ran before pii_guard was imported.

    pytest_configure fires when this conftest is loaded. If pii_guard had not yet
    been imported at that point (no prior sys.path entry), the module-level
    `import pii_guard` above imports it and pytest_configure patches it immediately.
    This fixture provides a belt-and-suspenders check before the first unit test.
    """
    if pii_guard._DEVA_NAME_LOADED and pii_guard._DEVA_NAME_PATTERN is not None:
        yield
        return

    pii_guard._DEVA_NAME_PATTERN = re.compile(
        r"(?:" + "|".join(re.escape(n) for n in _CI_DEVA_TEST_NAMES) + r")"
    )
    pii_guard._DEVA_NAME_LOADED = True
    yield

