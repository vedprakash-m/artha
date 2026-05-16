# F-C1 shim — contents merged into lib.metrics_collection. Remove after 2026-06-16.
# noqa: F401
import warnings as _w
_w.warn(
    "Importing from this module is deprecated; use lib.metrics_collection instead.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.metrics_collection import write_invocation_metric, write_invocation_trace, write_routing_margin, write_routing_decision  # noqa: F401,E402
__all__ = ['write_invocation_metric', 'write_invocation_trace', 'write_routing_margin', 'write_routing_decision']
