"""
scripts/connectors/base.py — ConnectorHandler Protocol for Artha declarative connectors.

Every connector handler module must implement this interface so pipeline.py
can call it uniformly regardless of provider.

Ref: supercharge.md §5.4
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, Protocol, runtime_checkable


@runtime_checkable
class ConnectorHandler(Protocol):
    """Protocol for declarative connector handler modules.

    Each module in scripts/connectors/ must expose two top-level functions
    that conform to these signatures. The module does NOT need to subclass
    anything — structural subtyping (duck typing) applies.

    Example minimal handler::

        # scripts/connectors/my_source.py
        from typing import Iterator

        def fetch(*, since: str, max_results: int, auth_context: dict,
                  source_tag: str = "", **kwargs) -> Iterator[dict]:
            ...  # yield standardized records

        def health_check(auth_context: dict) -> bool:
            ...  # return True if auth is valid and API is reachable
    """

    def fetch(
        self,
        *,
        since: str,
        max_results: int,
        auth_context: Dict[str, Any],
        source_tag: str = "",
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        """Yield standardized records from the data source.

        Args:
            since:        ISO-8601 timestamp or relative offset ("7d", "24h").
                          Fetch only records after this point.
            max_results:  Maximum number of records to return.
            auth_context: Dict from lib/auth.load_auth_context() for this connector.
            source_tag:   Optional string to add as "source" field on each record
                          (e.g. "outlook", "icloud"). Empty string = omit field.
            **kwargs:     Connector-specific params from connectors.yaml fetch block
                          (e.g. calendars, folder, include_personal).

        Yields:
            Dicts conforming to the connector's output.fields schema.
        """
        ...  # pragma: no cover

    def health_check(self, auth_context: Dict[str, Any]) -> bool:
        """Test auth validity and API connectivity.

        Returns:
            True if the connector is healthy and auth is valid.
            False if connectivity fails or credentials are expired.
        Should not raise — return False and log to stderr instead.
        """
        ...  # pragma: no cover
