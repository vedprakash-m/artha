import requests
import json
from typing import Dict, Any, List
import re
from pathlib import Path
from .base_skill import BaseSkill

class USCISStatusSkill(BaseSkill):
    """
    Skill to check USCIS case status using the public API.
    """
    
    API_URL = "https://egov.uscis.gov/csol-api/case-statuses/{receipt_number}"
    
    def __init__(self, receipt_numbers: List[str]):
        super().__init__(name="uscis_status", priority="P0")
        self.receipt_numbers = receipt_numbers

    def pull(self) -> Dict[str, Any]:
        """Fetch status for all receipt numbers."""
        results = {}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://egov.uscis.gov/casestatus/mycasestatus.do"
        }
        
        for receipt in self.receipt_numbers:
            url = self.API_URL.format(receipt_number=receipt)
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    results[receipt] = response.json()
                elif response.status_code == 403:
                    results[receipt] = {
                        "error": (
                            "HTTP 403 — USCIS API is blocking requests from this IP address "
                            "or network (common on cloud/VPN). Check status manually at "
                            "https://egov.uscis.gov/casestatus/mycasestatus.do"
                        ),
                        "blocked": True,
                    }
                else:
                    results[receipt] = {
                        "error": f"HTTP {response.status_code}",
                        "text": response.text[:500],
                    }
            except Exception as e:
                results[receipt] = {"error": str(e)}
        
        return results

    def parse(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract status and text from the API response."""
        parsed = {}
        for receipt, info in raw_data.items():
            if "error" in info:
                parsed[receipt] = {"status": "error", "message": info["error"]}
                continue
            
            # The API returns a CaseStatusResponse object
            # Path: CaseStatusResponse -> caseStatus -> statusAttributes -> [statusDetail, statusType]
            try:
                cs = info.get("CaseStatusResponse", {}).get("caseStatus", {})
                status_attr = cs.get("statusAttributes", {})
                
                parsed[receipt] = {
                    "status_type": status_attr.get("statusType"),
                    "status_details": status_attr.get("statusDetail"),
                    "last_updated": cs.get("statusDate"),
                    "form_type": cs.get("formType")
                }
            except Exception as e:
                parsed[receipt] = {"status": "parse_error", "message": str(e)}
                
        return parsed

    @property
    def compare_fields(self) -> list[str]:
        # A change in status_type or status_details is meaningful
        return ["status_type", "status_details"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "receipt_numbers": self.receipt_numbers
        }

def get_skill(artha_dir: Path) -> USCISStatusSkill:
    """Factory function to instantiate the skill with data from state files."""
    immigration_state = artha_dir / "state" / "immigration.md"
    receipts = []
    
    if immigration_state.exists():
        text = immigration_state.read_text()
        # Regex to find receipt numbers: IOE, SRC, LIN, etc. followed by 10 digits
        matches = re.findall(r"(?:IOE|SRC|LIN|EAC|WAC|NBC|MSC|ZLA)\d{10}", text)
        receipts = list(set(matches)) # unique
        
    return USCISStatusSkill(receipt_numbers=receipts)
