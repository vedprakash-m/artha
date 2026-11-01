"""
tests/unit/test_plaid_connector.py — Unit tests for scripts/connectors/plaid_connector.py

Tests cover: _load_credentials, _plaid_post, _aggregate_transactions, fetch(),
health_check(), privacy contract enforcement, and error handling.
All Plaid API calls are mocked — no real network calls.

Run: pytest tests/unit/test_plaid_connector.py -v
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import connectors.plaid_connector as plaid_mod
from connectors.plaid_connector import (
    _plaid_post,
    _load_credentials,
    _aggregate_transactions,
    fetch,
    health_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_urlopen(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_transaction(**kwargs) -> dict[str, Any]:
    today = str(date.today())
    defaults = {
        "transaction_id": "tx_001",
        "name": "WHOLE FOODS",
        "merchant_name": "Whole Foods",
        "category": ["Food and Drink", "Groceries"],
        "amount": 42.50,
        "iso_currency_code": "USD",
        "date": today,
        "pending": False,
    }
    defaults.update(kwargs)
    return defaults


def _plaid_sync_response(
    added: list[dict],
    has_more: bool = False,
    next_cursor: str = "cursor_001",
) -> bytes:
    return json.dumps({
        "added": added,
        "modified": [],
        "removed": [],
        "has_more": has_more,
        "next_cursor": next_cursor,
        "request_id": "req_001",
    }).encode()


# ---------------------------------------------------------------------------
# _load_credentials
# ---------------------------------------------------------------------------

class TestLoadCredentials:
    def test_auth_context_primary(self):
        ctx = {
            "client_id": "ctx_id",
            "secret": "ctx_sec",
            "access_token": "ctx_tok",
            "environment": "production",
        }
        cid, sec, tok, env = _load_credentials(ctx)
        assert cid == "ctx_id"
        assert sec == "ctx_sec"
        assert tok == "ctx_tok"
        assert env == "production"

    def test_env_fallback(self):
        with patch.dict(os.environ, {
            "ARTHA_PLAID_CLIENT_ID": "env_id",
            "ARTHA_PLAID_SECRET": "env_sec",
            "ARTHA_PLAID_ACCESS_TOKEN": "env_tok",
            "ARTHA_PLAID_ENVIRONMENT": "development",
        }, clear=True):
            with patch("keyring.get_password", return_value=None):
                cid, sec, tok, env = _load_credentials(None)
        assert cid == "env_id"
        assert sec == "env_sec"
        assert tok == "env_tok"
        assert env == "development"

    def test_default_environment_is_sandbox(self):
        with patch.dict(os.environ, {
            "ARTHA_PLAID_CLIENT_ID": "id",
            "ARTHA_PLAID_SECRET": "sec",
            "ARTHA_PLAID_ACCESS_TOKEN": "tok",
        }, clear=True):
            with patch("keyring.get_password", return_value=None):
                _, _, _, env = _load_credentials(None)
        assert env == "sandbox"

    def test_missing_credentials_return_empty_strings(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                cid, sec, tok, env = _load_credentials(None)
        assert cid == ""
        assert sec == ""
        assert tok == ""


# ---------------------------------------------------------------------------
# _aggregate_transactions (PRIVACY contract)
# ---------------------------------------------------------------------------

class TestAggregateTransactions:
    def test_returns_aggregated_not_raw(self):
        """CRITICAL: raw transactions must NOT appear in output."""
        txs = [
            _make_transaction(transaction_id="tx_001", name="AMAZON.COM", merchant_name="Amazon", amount=29.99),
            _make_transaction(transaction_id="tx_002", name="AMAZON.COM", merchant_name="Amazon", amount=15.00),
        ]
        result = _aggregate_transactions(txs)
        # Must NOT contain raw transaction fields
        for record in result:
            assert "transaction_id" not in record
            assert "name" not in record
        # Must contain aggregate fields
        assert all("transaction_count" in r for r in result)
        assert all("total_amount" in r for r in result)

    def test_groups_by_category_merchant_month(self):
        """Same category+merchant+month items are collapsed into one record."""
        today = str(date.today())
        txs = [_make_transaction(amount=10.0, date=today) for _ in range(5)]
        result = _aggregate_transactions(txs)
        assert len(result) == 1
        assert result[0]["transaction_count"] == 5

    def test_sums_amounts(self):
        today = str(date.today())
        txs = [_make_transaction(amount=10.0, date=today) for _ in range(3)]
        result = _aggregate_transactions(txs)
        assert result[0]["total_amount"] == pytest.approx(30.0)

    def test_merchant_group_anonymized(self):
        """merchant_group should be max 2 words from merchant name."""
        tx = _make_transaction(merchant_name="Whole Foods Market Inc USA")
        result = _aggregate_transactions([tx])
        merchant_group = result[0]["merchant_group"]
        assert len(merchant_group.split()) <= 2

    def test_source_tag(self):
        result = _aggregate_transactions([_make_transaction()])
        assert result[0]["source"] == "plaid"

    def test_empty_input(self):
        result = _aggregate_transactions([])
        assert result == []

    def test_currency_preserved(self):
        tx = _make_transaction(iso_currency_code="EUR")
        result = _aggregate_transactions([tx])
        assert result[0]["currency"] == "EUR"

    def test_month_extracted(self):
        tx = _make_transaction(date="2024-03-15")
        result = _aggregate_transactions([tx])
        assert result[0]["month"] == "2024-03"

    def test_different_categories_separate_records(self):
        txs = [
            _make_transaction(category=["Food and Drink"], merchant_name="Cafe", date="2024-03-15"),
            _make_transaction(category=["Travel"], merchant_name="Uber", date="2024-03-15"),
        ]
        result = _aggregate_transactions(txs)
        categories = {r["category"] for r in result}
        assert "Food and Drink" in categories
        assert "Travel" in categories

    def test_raw_transaction_id_never_in_output(self):
        """Strongest privacy check: transaction_id must never appear."""
        txs = [_make_transaction(transaction_id="SENSITIVE_TX_ID")]
        result = _aggregate_transactions(txs)
        result_str = json.dumps(result)
        assert "SENSITIVE_TX_ID" not in result_str


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------

class TestFetch:
    def test_missing_credentials_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                with pytest.raises(RuntimeError, match="Plaid"):
                    list(fetch())

    def test_fetches_and_aggregates(self):
        """fetch() yields aggregated records, not raw transactions."""
        txs = [
            _make_transaction(transaction_id=f"tx_{i}", amount=10.0 * i)
            for i in range(1, 6)
        ]
        sync_body = _plaid_sync_response(txs, has_more=False)

        auth_ctx = {
            "client_id": "cid",
            "secret": "sec",
            "access_token": "tok",
            "environment": "sandbox",
        }

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(sync_body)):
            records = list(fetch(auth_context=auth_ctx, max_results=100))

        # Must be aggregated
        assert len(records) >= 1
        for r in records:
            assert "transaction_id" not in r
            assert "transaction_count" in r
            assert r["source"] == "plaid"

    def test_fetch_applies_source_tag(self):
        txs = [_make_transaction()]
        sync_body = _plaid_sync_response(txs)
        auth_ctx = {
            "client_id": "cid", "secret": "sec",
            "access_token": "tok", "environment": "sandbox",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(sync_body)):
            records = list(fetch(auth_context=auth_ctx, source_tag="my_plaid"))
        assert all(r.get("source_tag") == "my_plaid" for r in records)

    def test_http_error_propagates(self):
        """API errors should propagate rather than silently returning empty."""
        auth_ctx = {
            "client_id": "cid", "secret": "sec",
            "access_token": "bad_tok", "environment": "sandbox",
        }
        err_body = json.dumps({"error_code": "INVALID_ACCESS_TOKEN", "error_message": "bad token"}).encode()
        exc = urllib.error.HTTPError(url="", code=400, msg="Bad Request", hdrs=MagicMock(), fp=MagicMock(read=lambda: err_body))
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(Exception):
                list(fetch(auth_context=auth_ctx))


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_valid_credentials_returns_true(self):
        item_response = json.dumps({"item": {"institution_id": "ins_001"}}).encode()
        auth_ctx = {
            "client_id": "cid", "secret": "sec",
            "access_token": "tok", "environment": "sandbox",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(item_response)):
            result = health_check(auth_ctx)
        assert result is True

    def test_missing_credentials_returns_false(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = health_check(None)
        assert result is False

    def test_api_error_returns_false(self):
        auth_ctx = {
            "client_id": "cid", "secret": "sec",
            "access_token": "bad", "environment": "sandbox",
        }
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = health_check(auth_ctx)
        assert result is False

    def test_item_missing_from_response_returns_false(self):
        empty_body = json.dumps({"request_id": "req_001"}).encode()
        auth_ctx = {
            "client_id": "cid", "secret": "sec",
            "access_token": "tok", "environment": "sandbox",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(empty_body)):
            result = health_check(auth_ctx)
        assert result is False
