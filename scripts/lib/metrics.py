# F-C1 shim — contents merged into lib.metrics_collection. Remove after 2026-06-16.
# noqa: F401
import warnings as _w
_w.warn(
    "Importing from this module is deprecated; use lib.metrics_collection instead.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.metrics_collection import CatchUpMetrics  # noqa: F401,E402
__all__ = ['CatchUpMetrics']
