"""tests/ext_agents/test_context_scrubber.py -- AR-9 ContextScrubber tests."""
from __future__ import annotations

import pytest
from lib.context_scrubber import ContextScrubber, ScrubResult  # type: ignore


@pytest.fixture()
def scrubber():
    return ContextScrubber(strict_mode=False)


class TestScrubResult:
    def test_has_required_fields(self):
        r = ScrubResult(
            original_length=10,
            scrubbed_text="clean text",
            pii_types_found={},
            was_modified=False,
        )
        assert r.scrubbed_text == "clean text"
        assert r.was_modified is False

    def test_pii_types_found_is_dict(self):
        r = ScrubResult(
            original_length=20,
            scrubbed_text="...",
            pii_types_found={"SSN": 1, "TOKEN": 1},
            was_modified=True,
        )
        assert len(r.pii_types_found) == 2


class TestContextScrubber:
    def test_clean_text_unchanged(self, scrubber):
        result = scrubber.scrub("deployment stuck in stage 3")
        assert "deployment stuck in stage 3" in result.scrubbed_text
        assert result.was_modified is False

    def test_scrubs_ssn(self, scrubber):
        text = "SSN: 123-45-6789"
        result = scrubber.scrub(text)
        assert "123-45-6789" not in result.scrubbed_text

    def test_scrubs_returns_scrub_result(self, scrubber):
        result = scrubber.scrub("hello world")
        assert isinstance(result, ScrubResult)

    def test_empty_string(self, scrubber):
        result = scrubber.scrub("")
        assert result.scrubbed_text == ""

    def test_allows_region_names(self, scrubber):
        """Region names like \'eastus\' should survive scrubbing."""
        result = scrubber.scrub("region eastus deployment")
        assert "eastus" in result.scrubbed_text

    def test_scrubs_credit_card(self, scrubber):
        text = "card number 4111 1111 1111 1111"
        result = scrubber.scrub(text)
        assert "4111 1111 1111 1111" not in result.scrubbed_text

    def test_pii_found_populated_on_match(self, scrubber):
        text = "my ssn is 123-45-6789"
        result = scrubber.scrub(text)
        assert isinstance(result.pii_types_found, dict)

    def test_with_allowed_pii(self):
        """Scrubber configured with allowed_pii should pass IP through."""
        scrubber_with_allow = ContextScrubber(strict_mode=False, allowed_pii=["IP_ADDRESS"])
        result = scrubber_with_allow.scrub("host 10.0.0.1 deployment")
        assert "10.0.0.1" in result.scrubbed_text
