"""
property_tax.py — Generic property tax status skill

Generalized version of king_county_tax.py. Reads the parcel ID and
provider URL from the user profile (location.property_tax_url) or falls
back to state/home.md for backward compatibility.

Ref: standardization.md §7.5.3, T-2.2.x
"""

import re
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

from .base_skill import BaseSkill


class PropertyTaxSkill(BaseSkill):
    """
    Generic property tax lookup skill.

    Fetches assessed value and payment due dates from the local county/city
    assessor web portal. The scraping logic targets the King County eReal
    Property portal by default; override `provider_url` for other counties.
    """

    _DEFAULT_URL = "https://blue.kingcounty.com/Assessor/eRealProperty/Dashboard.aspx?ParcelNbr={parcel_id}"

    def __init__(self, parcel_id: str, provider_url: str = ""):
        super().__init__(name="property_tax", priority="P1")
        self.parcel_id = parcel_id.replace("-", "")
        self.provider_url = provider_url or self._DEFAULT_URL

    @property
    def compare_fields(self) -> List[str]:
        return ["next_due_date", "amount_due", "assessed_value"]

    def pull(self) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        url = self.provider_url.format(parcel_id=self.parcel_id)
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text

    def parse(self, raw_data: str) -> Dict[str, Any]:
        soup = BeautifulSoup(raw_data, "html.parser")
        parsed: Dict[str, Any] = {
            "parcel_id": self.parcel_id,
            "next_due_date": None,
            "amount_due": None,
            "assessed_value": None,
            "last_sale_price": None,
            "status": "not_found",
        }
        try:
            body_text = soup.get_text()

            date_matches = re.findall(r"(?:April 30|October 31),?\s*202[6-9]", body_text)
            if date_matches:
                parsed["next_due_date"] = date_matches[0]

            amount_matches = re.findall(r"\$\s*([0-9,]+\.[0-9]{2})", body_text)
            if amount_matches:
                parsed["amount_due"] = amount_matches[0]
                parsed["status"] = "extracted"

            val_match = re.search(
                r"Assessed Value.*?\$([0-9,]+)", body_text, re.IGNORECASE | re.DOTALL
            )
            if val_match:
                parsed["assessed_value"] = val_match.group(1).replace(",", "")

            sale_match = re.search(
                r"Sale Price.*?\$([0-9,]+)", body_text, re.IGNORECASE | re.DOTALL
            )
            if sale_match:
                parsed["last_sale_price"] = sale_match.group(1).replace(",", "")

        except Exception as exc:
            parsed["status"] = "parse_error"
            parsed["error"] = str(exc)

        return parsed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "parcel_id": self.parcel_id,
            "provider_url": self.provider_url,
        }


def get_skill(artha_dir: Path) -> PropertyTaxSkill:
    """Factory: resolve parcel_id and optional provider URL from profile or state."""
    import sys as _sys
    _sys.path.insert(0, str(artha_dir / "scripts"))

    parcel_id = ""
    provider_url = ""

    # 1. Try profile first
    try:
        from profile_loader import get as _pget, has_profile as _has_profile
        if _has_profile():
            parcel_id = _pget("location.parcel_id", "") or ""
            provider_url = _pget("location.property_tax_url", "") or ""
    except Exception:
        pass

    # 2. Fall back to state/home.md for backward compatibility
    if not parcel_id:
        home_state = artha_dir / "state" / "home.md"
        if home_state.exists():
            try:
                text = home_state.read_text()
                match = re.search(r"(?:King County Account|Parcel ID|Parcel #):\s*([\d-]+)", text, re.IGNORECASE)
                if match:
                    parcel_id = match.group(1)
            except Exception:
                pass

    return PropertyTaxSkill(parcel_id=parcel_id, provider_url=provider_url)


# Backward-compatibility alias (for existing skills.yaml references to "king_county_tax")
KingCountyTaxSkill = PropertyTaxSkill
