"""
tests/unit/test_plaid_privacy.py — Privacy contract verification for Plaid connector.

These tests specifically validate the privacy contract:
  - Raw transactions NEVER stored or returned
  - Only aggregated summaries exit the connector
  - No sensitive fields (transaction_id, account_id, etc.) appear in output

This is a SECURITY-CRITICAL test suite.
Run: pytest tests/unit/test_plaid_privacy.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from connectors.plaid_connector import _aggregate_transactions, fetch


# ---------------------------------------------------------------------------
# Constants: fields that MUST NOT appear in output
# ---------------------------------------------------------------------------

_FORBIDDEN_RAW_FIELDS = {
    "transaction_id",
    "account_id",
    "name",           # raw merchant name (may contain store+address)
    "pending",
    "payment_channel",
    "personal_finance_category",
    "location",
    "payment_meta",
    "authorized_date",
    "authorized_datetime",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sensitive_tx(**kwargs) -> dict[str, Any]:
    """Create a transaction with many sensitive fields."""
    today = str(date.today())
    return {
        "transaction_id": "SENSITIVE_TX_12345",
        "account_id": "ACCOUNT_ID_ABC",
        "name": "RAW MERCHANT NAME AND STORE LOCATION",
        "merchant_name": "Safe Merchant",
        "category": ["Food and Drink", "Groceries"],
        "amount": 55.00,
        "iso_currency_code": "USD",
        "date": today,
        "pending": True,
        "payment_channel": "in store",
        "location": {"address": "123 Main St", "city": "Springfield"},
        "payment_meta": {"reference_number": "REF999"},
        **kwargs,
    }


def _make_auth_ctx() -> dict[str, Any]:
    return {
        "client_id": "cid",
        "secret": "sec",
        "access_token": "tok",
        "environment": "sandbox",
    }


# ---------------------------------------------------------------------------
# _aggregate_transactions privacy tests
# ---------------------------------------------------------------------------

class TestAggregatePrivacyContract:
    def test_forbidden_fields_absent_from_output(self):
        """All forbidden raw fields must be absent from aggregate output."""
        txs = [_make_sensitive_tx() for _ in range(5)]
        result = _aggregate_transactions(txs)
        result_json = json.dumps(result)

        for field in _FORBIDDEN_RAW_FIELDS:
            assert field not in result_json, (
                f"PRIVACY VIOLATION: forbidden field '{field}' found in aggregate output"
            )

    def test_sensitive_transaction_id_not_in_output(self):
        tx = _make_sensitive_tx(transaction_id="SECRET_TX_ID_99999")
        result = _aggregate_transactions([tx])
        result_json = json.dumps(result)
        assert "SECRET_TX_ID_99999" not in result_json

    def test_account_id_not_in_output(self):
        tx = _make_sensitive_tx(account_id="SECRET_ACCOUNT_ABC")
        result = _aggregate_transactions([tx])
        result_json = json.dumps(result)
        assert "SECRET_ACCOUNT_ABC" not in result_json

    def test_raw_merchant_name_not_in_output(self):
        """The 'name' field (raw merchant + location string) must be stripped."""
        tx = _make_sensitive_tx(name="WHOLE FOODS #1234 123 MAIN ST SEATTLE WA")
        result = _aggregate_transactions([tx])
        result_json = json.dumps(result)
        # Full raw name with address must not appear
        assert "123 MAIN ST" not in result_json

    def test_location_data_not_in_output(self):
        tx = _make_sensitive_tx(location={"address": "PRIVATE HOME ADDRESS", "city": "Seattle"})
        result = _aggregate_transactions([tx])
        result_json = json.dumps(result)
        assert "PRIVATE HOME ADDRESS" not in result_json

    def test_merchant_group_is_capped_at_2_words(self):
        """merchant_group should be max 2 words, preventing full name leakage."""
        tx = _make_sensitive_tx(merchant_name="Whole Foods Market Inc USA Corporation")
        result = _aggregate_transactions([tx])
        merchant_group = result[0]["merchant_group"]
        assert len(merchant_group.split()) <= 2

    def test_output_contains_only_allowed_fields(self):
        """Output records contain only the approved aggregated fields."""
        _ALLOWED_FIELDS = {
            "category",
            "merchant_group",
            "month",
            "transaction_count",
            "total_amount",
            "currency",
            "source",
            "source_tag",   # added by fetch()
        }
        txs = [_make_sensitive_tx()]
        result = _aggregate_transactions(txs)
        for record in result:
            unexpected = set(record.keys()) - _ALLOWED_FIELDS
            assert not unexpected, f"Unexpected fields in aggregate output: {unexpected}"


# ---------------------------------------------------------------------------
# fetch() privacy tests (with mock Plaid API)
# ---------------------------------------------------------------------------

class TestFetchPrivacyContract:
    def _plaid_sync_body(self, txs: list[dict]) -> bytes:
        return json.dumps({
            "added": txs,
            "modified": [],
            "removed": [],
            "has_more": False,
            "next_cursor": "c1",
        }).encode()

    def test_fetch_output_contains_no_raw_transactions(self):
        """Verifies end-to-end that fetch() never returns raw transaction data."""
        txs = [_make_sensitive_tx(transaction_id=f"FORBIDDEN_ID_{i}") for i in range(10)]

        sync_body = self._plaid_sync_body(txs)
        with patch("urllib.request.urlopen", return_value=MagicMock(
            read=lambda: sync_body,
            __enter__=lambda s: s,
            __exit__=MagicMock(return_value=False),
        )):
            records = list(fetch(auth_context=_make_auth_ctx()))

        records_json = json.dumps(records)
        for i in range(10):
            assert f"FORBIDDEN_ID_{i}" not in records_json, (
                f"PRIVACY VIOLATION: raw transaction_id FORBIDDEN_ID_{i} found in fetch() output"
            )

    def test_fetch_output_contains_no_account_ids(self):
        txs = [_make_sensitive_tx(account_id="ACCOUNT_MUST_NEVER_APPEAR")]
        sync_body = self._plaid_sync_body(txs)
        with patch("urllib.request.urlopen", return_value=MagicMock(
            read=lambda: sync_body,
            __enter__=lambda s: s,
            __exit__=MagicMock(return_value=False),
        )):
            records = list(fetch(auth_context=_make_auth_ctx()))

        records_json = json.dumps(records)
        assert "ACCOUNT_MUST_NEVER_APPEAR" not in records_json

    def test_fetch_yields_aggregated_counts(self):
        """fetch() must yield aggregated count fields — confirms aggregation occurred."""
        txs = [_make_sensitive_tx() for _ in range(7)]
        sync_body = self._plaid_sync_body(txs)
        with patch("urllib.request.urlopen", return_value=MagicMock(
            read=lambda: sync_body,
            __enter__=lambda s: s,
            __exit__=MagicMock(return_value=False),
        )):
            records = list(fetch(auth_context=_make_auth_ctx()))

        total_count = sum(r["transaction_count"] for r in records)
        assert total_count == 7  # All 7 transactions aggregated

    def test_empty_transaction_list_yields_no_records(self):
        """Empty Plaid response yields nothing."""
        sync_body = self._plaid_sync_body([])
        with patch("urllib.request.urlopen", return_value=MagicMock(
            read=lambda: sync_body,
            __enter__=lambda s: s,
            __exit__=MagicMock(return_value=False),
        )):
            records = list(fetch(auth_context=_make_auth_ctx()))
        assert records == []
