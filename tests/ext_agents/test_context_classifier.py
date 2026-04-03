"""tests/ext_agents/test_context_classifier.py -- AR-9 ContextClassifier tests."""
from __future__ import annotations

import pytest
from lib.context_classifier import (  # type: ignore
    ContextTier,
    ClassificationResult,
    classify_context,
    is_tier_allowed,
    filter_context_fragments,
)


class TestContextTier:
    def test_enum_values_exist(self):
        assert ContextTier.PUBLIC is not None
        assert ContextTier.SCOPED is not None
        assert ContextTier.PRIVATE is not None
        assert ContextTier.SENSITIVE is not None

    def test_ordering_public_lt_sensitive(self):
        """PUBLIC should be lower risk than SENSITIVE."""
        assert ContextTier.PUBLIC.value < ContextTier.SENSITIVE.value


class TestClassifyContext:
    def test_empty_string_returns_public(self):
        result = classify_context("")
        assert result.tier == ContextTier.PUBLIC

    def test_deployment_keyword_returns_scoped_or_public(self):
        result = classify_context("deployment stuck in SDP")
        assert result.tier in (ContextTier.PUBLIC, ContextTier.SCOPED, ContextTier.PRIVATE)

    def test_ssn_returns_sensitive(self):
        result = classify_context("my ssn is 123-45-6789")
        assert result.tier == ContextTier.SENSITIVE

    def test_returns_classification_result_type(self):
        result = classify_context("some text")
        assert isinstance(result, ClassificationResult)

    def test_plain_work_phrase(self):
        result = classify_context("sprint planning meeting")
        assert result.tier != ContextTier.SENSITIVE

    def test_insurance_id_returns_sensitive(self):
        result = classify_context("insurance id: HI-9981234")
        assert result.tier == ContextTier.SENSITIVE


class TestIsTierAllowed:
    def test_public_allowed_for_external(self):
        assert is_tier_allowed(ContextTier.PUBLIC, "external") is True

    def test_sensitive_blocked_for_external(self):
        assert is_tier_allowed(ContextTier.SENSITIVE, "external") is False

    def test_private_blocked_for_external(self):
        assert is_tier_allowed(ContextTier.PRIVATE, "external") is False

    def test_scoped_allowed_for_trusted(self):
        assert is_tier_allowed(ContextTier.SCOPED, "trusted") is True

    def test_private_allowed_for_owned(self):
        assert is_tier_allowed(ContextTier.PRIVATE, "owned") is True


class TestFilterContextFragments:
    def test_removes_sensitive_fragments(self):
        fragments = [
            # Public path, public content — should pass external filter
            ("region: eastus", ""),
            # Finance path typically classified sensitive by keyword
            ("my ssn is 123-45-6789", ""),
        ]
        result = filter_context_fragments(fragments, agent_trust_level="external")
        texts = [f[0] for f in result]
        assert "region: eastus" in texts
        assert "my ssn is 123-45-6789" not in texts

    def test_empty_list_returns_empty(self):
        assert filter_context_fragments([], agent_trust_level="external") == []

    def test_all_public_returned_for_external(self):
        fragments = [
            ("region: eastus", ""),
            ("cluster: xpf-a01", ""),
        ]
        result = filter_context_fragments(fragments, agent_trust_level="external")
        # Public fragments should be allowed
        assert isinstance(result, list)
        assert len(result) == 2
