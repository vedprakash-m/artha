#!/usr/bin/env python3
# pii-guard: raw transaction data NEVER stored; only aggregated summaries persist
"""
scripts/connectors/plaid_connector.py — Plaid financial data connector for Artha.

PRIVACY CONTRACT
================
Raw transaction data is NEVER written to disk, logged, or stored in state files.
Only aggregated summaries (merchant category totals, monthly spend bands) are
returned from fetch(). This is enforced by the connector — not configurable.

ConnectorHandler protocol: module-level fetch() + health_check() functions.
Dependencies: stdlib only (urllib.request, json, http.server, threading).

Authentication flow (Plaid Link):
  1. Create link_token via POST /link/token/create
  2. Open browser to hosted Plaid Link (localhost:7777/plaid-link)
  3. Plaid redirects with public_token via query param
  4. Exchange public_token for access_token via POST /item/public_token/exchange
  5. Store access_token in keyring as "artha-plaid-access-token"

Environment selection:
  sandbox     — test environment (no real bank data)
  development — up to 5 real accounts
  production  — unlimited (requires Plaid approval)

Ref: specs/connect.md §9
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Plaid environment base URLs
_PLAID_URLS: dict[str, str] = {
    "sandbox":     "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production":  "https://production.plaid.com",
}
_DEFAULT_ENV = "sandbox"
_REQUEST_TIMEOUT = 30
_RETRY_MAX = 3
_RETRY_BASE_DELAY = 1.5
_MAX_SYNC_PAGES = 20
_REQUEST_DEADLINE_SECONDS = 90

# Plaid Link local callback server settings
_LINK_SERVER_HOST = "127.0.0.1"
_LINK_SERVER_PORT = 7777
_LINK_CALLBACK_TIMEOUT = 300  # 5 minutes max for user to complete Link flow


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _plaid_post(
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    client_id: str,
    secret: str,
    timeout: float = _REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """POST to Plaid API with exponential backoff on 5xx errors."""
    url = f"{base_url}/{endpoint.lstrip('/')}"
    payload_with_auth = {
        "client_id": client_id,
        "secret": secret,
        **payload,
    }
    body = json.dumps(payload_with_auth).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Artha/1.0"},
        method="POST",
    )
    from lib.retry import with_retry  # type: ignore[import]

    def request_once() -> dict[str, Any]:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    try:
        return with_retry(
            request_once,
            max_retries=_RETRY_MAX - 1,
            base_delay=_RETRY_BASE_DELAY,
            max_delay=30,
            deadline=time.monotonic() + _REQUEST_DEADLINE_SECONDS,
            context=f"plaid.{endpoint}",
        )
    except urllib.error.HTTPError as exc:
        code = f"HTTP {exc.code}"
        if exc.code == 400:
            try:
                body = exc.read(4096)
                err = json.loads(body.decode()) if body else {}
                code = str(err.get("error_code") or code)
            except (UnicodeError, json.JSONDecodeError):
                pass
        add_note = getattr(exc, "add_note", None)
        if add_note is not None:
            add_note(f"plaid error classification={code}")
        raise


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _load_credentials(
    auth_context: dict[str, Any] | None = None,
) -> tuple[str, str, str, str]:
    """Resolve Plaid credentials: (client_id, secret, access_token, environment).

    Resolution order:
      1. auth_context dict (injected at runtime by pipeline.py)
      2. Keyring (service names: artha-plaid-client-id, artha-plaid-secret, artha-plaid-access-token)
      3. Environment variables: ARTHA_PLAID_CLIENT_ID, ARTHA_PLAID_SECRET,
                                ARTHA_PLAID_ACCESS_TOKEN, ARTHA_PLAID_ENVIRONMENT
    """
    def _resolve(ac_key: str, keyring_service: str, env_var: str) -> str:
        if auth_context:
            val = auth_context.get(ac_key, "")
            if val:
                return val
        try:
            import keyring  # type: ignore[import]
            val = keyring.get_password(keyring_service, "value")
            if val:
                return val
        except ImportError:
            pass
        return os.environ.get(env_var, "")

    client_id    = _resolve("client_id",    "artha-plaid-client-id",    "ARTHA_PLAID_CLIENT_ID")
    secret       = _resolve("secret",       "artha-plaid-secret",       "ARTHA_PLAID_SECRET")
    access_token = _resolve("access_token", "artha-plaid-access-token", "ARTHA_PLAID_ACCESS_TOKEN")
    environment  = _resolve("environment",  "artha-plaid-environment",  "ARTHA_PLAID_ENVIRONMENT") or _DEFAULT_ENV

    return client_id, secret, access_token, environment


# ---------------------------------------------------------------------------
# Aggregation (Privacy contract enforcement)
# ---------------------------------------------------------------------------

def _aggregate_transactions(raw_tx: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate raw transactions into category-level summaries.

    PRIVACY: Raw transaction data is NEVER returned — only aggregates.
    Output schema per record:
      category         — Plaid primary category name
      merchant_group   — First 2 words of merchant name (anonymized)
      month            — YYYY-MM
      transaction_count — Number of transactions in group
      total_amount     — Total spend in group (positive = spending, negative = income)
      currency         — ISO 4217 code (e.g. USD)
      source           — "plaid"
    """
    # Group by (category, merchant_prefix, month, currency)
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"transaction_count": 0, "total_amount": 0.0}
    )

    for tx in raw_tx:
        category_list = tx.get("category") or ["Uncategorized"]
        category = category_list[0] if category_list else "Uncategorized"
        amount = float(tx.get("amount", 0.0))  # Plaid: positive = spending/debit
        currency = (tx.get("iso_currency_code") or tx.get("unofficial_currency_code") or "USD").upper()
        merchant_name = tx.get("merchant_name") or tx.get("name") or ""
        # Anonymize: take only first 2 words, max 20 chars each
        merchant_parts = merchant_name.split()[:2]
        merchant_group = " ".join(p[:20] for p in merchant_parts) or "Other"
        tx_date_str = tx.get("date", "")
        if not isinstance(tx_date_str, str) or len(tx_date_str) < 7:
            continue
        month = tx_date_str[:7]  # YYYY-MM

        key = (category, merchant_group, month, currency)
        groups[key]["transaction_count"] += 1
        groups[key]["total_amount"] += amount

    records = []
    for (category, merchant_group, month, currency), totals in sorted(groups.items()):
        records.append({
            "category": category,
            "merchant_group": merchant_group,
            "month": month,
            "transaction_count": totals["transaction_count"],
            "total_amount": round(totals["total_amount"], 2),
            "currency": currency,
            "source": "plaid",
        })
    return records


# ---------------------------------------------------------------------------
# ConnectorHandler protocol
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str | date | datetime | None = None,
    max_results: int = 500,
    auth_context: dict[str, Any] | None = None,
    source_tag: str = "plaid",
    **_kwargs: Any,
) -> Iterator[dict[str, Any]]:
    """Fetch aggregated financial transaction summaries from Plaid.

    PRIVACY: Raw transactions are fetched in-memory only and immediately
    aggregated. Aggregated records (category-level spend summaries) are
    yielded. No raw transaction data leaves this function.

    Parameters:
        since:        Earliest date to fetch transactions from (default: 30 days ago)
        max_results:  Max raw transactions to pull before aggregating (default: 500)
        auth_context: Dict with client_id, secret, access_token, environment (optional)
        source_tag:   Tag added to each output record (default: "plaid")
    """
    client_id, secret, access_token, environment = _load_credentials(auth_context)

    if not all((client_id, secret, access_token)):
        raise RuntimeError(
            "Plaid credentials not configured. Run 'python scripts/setup_plaid.py'."
        )
    if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results <= 0:
        raise ValueError("max_results must be a positive integer")

    base_url = _PLAID_URLS.get(environment, _PLAID_URLS["sandbox"])

    # Determine date range
    end_date = date.today()
    if isinstance(since, datetime):
        start_date = since.date()
    elif isinstance(since, date):
        start_date = since
    elif isinstance(since, str):
        try:
            start_date = datetime.fromisoformat(since.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                start_date = date.fromisoformat(since)
            except ValueError:
                raise ValueError("since must be an ISO-8601 date or datetime") from None
    else:
        start_date = end_date - timedelta(days=30)

    # Paginate through transactions using cursor-based sync API
    # Note: Plaid /transactions/sync is preferred for incremental fetches
    has_more = True
    cursor: str | None = None
    all_added: list[dict[str, Any]] = []
    fetched_count = 0

    seen_cursors: set[str] = set()
    pages = 0
    while has_more and fetched_count < max_results:
        if pages >= _MAX_SYNC_PAGES:
            raise RuntimeError("Plaid transaction pagination limit reached")
        payload: dict[str, Any] = {
            "access_token": access_token,
            "count": min(500, max_results - fetched_count),
        }
        if cursor:
            payload["cursor"] = cursor

        response = _plaid_post(base_url, "transactions/sync", payload, client_id, secret)
        added = response.get("added", [])
        all_added.extend(added)
        fetched_count += len(added)
        has_more = response.get("has_more", False)
        cursor = response.get("next_cursor")
        pages += 1

        if not added and has_more:
            raise RuntimeError("Plaid transaction pagination returned an empty page")
        if not cursor and has_more:
            raise RuntimeError("Plaid transaction continuation cursor is missing")
        if isinstance(cursor, str) and cursor in seen_cursors and has_more:
            raise RuntimeError("Plaid transaction pagination repeated a cursor")
        if isinstance(cursor, str):
            seen_cursors.add(cursor)
        if not added:
            break

    # Filter to requested date range (Plaid sync may return broader range)
    start_str = str(start_date)
    end_str = str(end_date)
    filtered = [
        tx for tx in all_added
        if start_str <= tx.get("date", "") <= end_str
    ]

    # Aggregate and yield — PRIVACY: no raw data leaves
    aggregated = _aggregate_transactions(filtered)
    for record in aggregated:
        identity = hashlib.sha256(
            f"{record['category']}\0{record['merchant_group']}\0"
            f"{record['month']}\0{record['currency']}".encode()
        ).hexdigest()[:16]
        record["id"] = f"plaid-{identity}"
        record["date_iso"] = f"{record['month']}-01"
        record["source"] = source_tag or "plaid"
        record["source_tag"] = source_tag
        record["subject"] = f"{record['category']}: {record['merchant_group']}"
        record["body"] = (
            f"{record['transaction_count']} transactions; "
            f"{record['currency']} {record['total_amount']:.2f}"
        )
        yield record


def health_check(auth_context: dict[str, Any] | None = None) -> bool:
    """Verify Plaid credentials by calling /item/get.

    Returns True if access token is valid and item is accessible.
    """
    try:
        client_id, secret, access_token, environment = _load_credentials(auth_context)
        if not all((client_id, secret, access_token)):
            return False
        base_url = _PLAID_URLS.get(environment, _PLAID_URLS["sandbox"])
        response = _plaid_post(
            base_url,
            "item/get",
            {"access_token": access_token},
            client_id,
            secret,
        )
        return "item" in response
    except Exception:
        return False
