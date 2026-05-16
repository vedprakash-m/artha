# F-C1 shim — contents merged into lib.metrics_analysis. Remove after 2026-06-16.
# noqa: F401
import warnings as _w
_w.warn(
    "Importing from this module is deprecated; use lib.metrics_analysis instead.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.metrics_analysis import run_digest, _compute_digest, _render_markdown  # noqa: F401,E402
__all__ = ['run_digest', '_compute_digest', '_render_markdown']
