"""Unit tests for individual skill modules.

Covers:
- noaa_weather: guard against 0.0/0.0 unconfigured coordinates
- uscis_status: 403 IP-blocked error message
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# NOAAWeatherSkill
# ---------------------------------------------------------------------------

class TestNOAAUnconfiguredCoordinates:
    """get_skill() must raise ValueError when coordinates are 0.0/0.0.

    Rationale: silently issuing a request to NOAA with (0.0, 0.0) returns
    a 404 (mid-ocean point) that looks like an API failure, not a config
    problem.  A loud, early ValueError surfaces the real cause.
    """

    def test_zero_coordinates_raise_value_error(self, tmp_path):
        """lat=0.0, lon=0.0 (the default) must raise ValueError."""
        from scripts.skills.noaa_weather import get_skill

        # Provide a minimal profile that has location.lat/lon defaulted to 0.0
        (tmp_path / "scripts").mkdir()
        profile_loader_src = (
            "def has_profile(): return True\n"
            "def get(key, default=None):\n"
            "    data = {'location.lat': 0.0, 'location.lon': 0.0,\n"
            "            'family.primary_user.emails.gmail': 'x@example.com'}\n"
            "    return data.get(key, default)\n"
        )
        (tmp_path / "scripts" / "profile_loader.py").write_text(profile_loader_src)
        sys.path.insert(0, str(tmp_path / "scripts"))
        try:
            with pytest.raises(ValueError, match="location.lat.*location.lon"):
                get_skill(tmp_path)
        finally:
            sys.path.remove(str(tmp_path / "scripts"))

    def test_configured_coordinates_do_not_raise(self, tmp_path):
        """Valid lat/lon must NOT raise — the skill object is returned."""
        import types
        from scripts.skills.noaa_weather import get_skill, NOAAWeatherSkill

        # Inject a fake profile_loader via sys.modules to bypass sys.path/cache
        # ordering issues (the real profile_loader is already loaded by the test
        # runner; a filesystem injection would be silently ignored).
        fake_pl = types.ModuleType("profile_loader")
        fake_pl.has_profile = lambda: True
        fake_pl.get = lambda key, default=None: {
            "location.lat": 47.6062,
            "location.lon": -122.3321,
            "family.primary_user.emails.gmail": "x@example.com",
        }.get(key, default)

        from unittest.mock import patch
        with patch.dict(sys.modules, {"profile_loader": fake_pl}):
            skill = get_skill(tmp_path)
        assert isinstance(skill, NOAAWeatherSkill)
        assert skill.lat == 47.6062
        assert skill.lon == -122.3321

    def _make_mock_response(self, status_code: int, text: str = "") -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        return resp

    def test_403_sets_blocked_flag(self):
        from scripts.skills.uscis_status import USCISStatusSkill

        skill = USCISStatusSkill(receipt_numbers=["EAC2190123456"])
        mock_resp = self._make_mock_response(403)

        with patch("scripts.skills.uscis_status.requests.get", return_value=mock_resp):
            result = skill.pull()

        assert result["EAC2190123456"].get("blocked") is True

    def test_403_message_mentions_ip(self):
        from scripts.skills.uscis_status import USCISStatusSkill

        skill = USCISStatusSkill(receipt_numbers=["EAC2190123456"])
        mock_resp = self._make_mock_response(403)

        with patch("scripts.skills.uscis_status.requests.get", return_value=mock_resp):
            result = skill.pull()

        error_msg = result["EAC2190123456"]["error"]
        assert "IP" in error_msg or "network" in error_msg.lower()

    def test_403_message_mentions_manual_url(self):
        from scripts.skills.uscis_status import USCISStatusSkill

        skill = USCISStatusSkill(receipt_numbers=["EAC2190123456"])
        mock_resp = self._make_mock_response(403)

        with patch("scripts.skills.uscis_status.requests.get", return_value=mock_resp):
            result = skill.pull()

        assert "egov.uscis.gov" in result["EAC2190123456"]["error"]

    def test_other_error_preserves_original_behaviour(self):
        from scripts.skills.uscis_status import USCISStatusSkill

        skill = USCISStatusSkill(receipt_numbers=["EAC2190123456"])
        mock_resp = self._make_mock_response(500, "Internal Server Error" * 100)

        with patch("scripts.skills.uscis_status.requests.get", return_value=mock_resp):
            result = skill.pull()

        entry = result["EAC2190123456"]
        assert "HTTP 500" in entry["error"]
        # text must be truncated to avoid log bloat
        assert len(entry.get("text", "")) <= 500

    def test_200_returns_json_payload(self):
        from scripts.skills.uscis_status import USCISStatusSkill

        skill = USCISStatusSkill(receipt_numbers=["EAC2190123456"])
        mock_resp = self._make_mock_response(200)
        mock_resp.json.return_value = {"CaseStatusResponse": {"caseStatus": {}}}

        with patch("scripts.skills.uscis_status.requests.get", return_value=mock_resp):
            result = skill.pull()

        assert "CaseStatusResponse" in result["EAC2190123456"]

    def test_no_receipt_numbers_returns_empty(self):
        from scripts.skills.uscis_status import USCISStatusSkill

        skill = USCISStatusSkill(receipt_numbers=[])
        with patch("scripts.skills.uscis_status.requests.get") as mock_get:
            result = skill.pull()
            mock_get.assert_not_called()
        assert result == {}
