# F-C1 shim — contents merged into lib.context_guard. Remove after 2026-06-16.
# noqa: F401
import warnings as _w
_w.warn(
    "Importing from this module is deprecated; use lib.context_guard instead.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.context_guard import ContextScrubber, ScrubResult  # noqa: F401,E402
from lib.context_guard import _get_pii_guard  # noqa: F401,E402 — re-export so tests can patch lib.context_scrubber._get_pii_guard
__all__ = ['ContextScrubber', 'ScrubResult']
