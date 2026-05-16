# F-C1 shim — contents merged into lib.metrics_analysis. Remove after 2026-06-16.
# noqa: F401
import warnings as _w
_w.warn(
    "Importing from this module is deprecated; use lib.metrics_analysis instead.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.metrics_analysis import _DEFAULT_PROMOTE_ABOVE, _DEFAULT_SUPPRESS_BELOW, partition_signals, rank_signals, score_signal  # noqa: F401,E402
__all__ = ['_DEFAULT_PROMOTE_ABOVE', '_DEFAULT_SUPPRESS_BELOW', 'partition_signals', 'rank_signals', 'score_signal']
