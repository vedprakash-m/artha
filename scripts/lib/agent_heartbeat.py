# F-C1 shim — contents merged into lib.agent_health. Remove after 2026-06-16.
# noqa: F401
import warnings as _w
_w.warn(
    "Importing from this module is deprecated; use lib.agent_health instead.",
    DeprecationWarning,
    stacklevel=2,
)
from lib.agent_health import AgentHeartbeat, HealthAlert  # noqa: F401,E402
__all__ = ['AgentHeartbeat', 'HealthAlert']
