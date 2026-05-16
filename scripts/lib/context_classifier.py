# F-C1 shim — contents merged into lib.context_guard. Remove after 2026-06-16.
# noqa: F401
import warnings as _w
_w.warn(
    "Importing from this module is deprecated; use lib.context_guard instead.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.context_guard import ClassificationResult, ContextTier, classify_context, filter_context_fragments, is_tier_allowed  # noqa: F401,E402
__all__ = ['ClassificationResult', 'ContextTier', 'classify_context', 'filter_context_fragments', 'is_tier_allowed']
