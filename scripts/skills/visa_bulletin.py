import requests
from bs4 import BeautifulSoup
import re
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from .base_skill import BaseSkill

class VisaBulletinSkill(BaseSkill):
    """
    Skill to parse the monthly Visa Bulletin and USCIS authorized chart status.
    """
    
    VB_INDEX_URL = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
    USCIS_CHART_URL = "https://www.uscis.gov/green-card/green-card-processes-and-procedures/visa-availability-priority-dates/adjustment-of-status-filing-charts-from-the-visa-bulletin"
    
    # Validation regex: 01JAN22 or C or U
    DATE_REGEX = r"(\d{2}[A-Z]{3}\d{2}|C|U)"

    def __init__(self, preference_category: str = "EB-2", country: str = "INDIA"):
        super().__init__(name="visa_bulletin", priority="P0")
        self.preference_category = preference_category
        self.country = country

    @property
    def compare_fields(self) -> List[str]:
        return ["final_action_date", "filing_date", "authorized_chart"]

    def _get_latest_vb_url(self) -> str:
        """Find the URL for the current month's bulletin."""
        try:
            response = requests.get(self.VB_INDEX_URL, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            # Look for links containing "visa-bulletin-for"
            links = soup.find_all('a', href=re.compile(r"visa-bulletin-for-"))
            if links:
                # Assuming the first one is the latest
                return "https://travel.state.gov" + links[0]['href']
        except Exception as e:
            logging.error(f"Failed to find latest Visa Bulletin URL: {e}")
        return ""

    def _parse_vb_table(self, soup: BeautifulSoup, table_title_pattern: str) -> Optional[str]:
        """Extract priority date from a specific Table A or B."""
        # Tables are often preceded by text or in specific IDs
        # We look for "EMPLOYMENT-BASED PREFERENCE"
        # Then find the EB-2 row and INDIA column (usually column 3 or 4)
        
        # This is a heuristic parse as the HTML is notoriously messy
        tables = soup.find_all('table')
        for table in tables:
            text = table.get_text()
            if "EMPLOYMENT-BASED" in text.upper():
                rows = table.find_all('tr')
                for row in rows:
                    cols = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
                    if self.preference_category in cols[0] if cols else False:
                        # Find India column. Usually EB tables have headers: 
                        # [Category, All Chargeability, China, India, Mexico, Philippines]
                        # India is typically index 3
                        try:
                            val = cols[3]
                            if re.match(self.DATE_REGEX, val):
                                return val
                        except IndexError:
                            continue
        return None

    def pull(self) -> Dict[str, Any]:
        results = {
            "final_action_date": None,
            "filing_date": None,
            "authorized_chart": None,
            "vb_url": self._get_latest_vb_url()
        }
        
        if not results["vb_url"]:
            raise Exception("Could not determine latest Visa Bulletin URL")

        # 1. Fetch Visa Bulletin
        vb_resp = requests.get(results["vb_url"], timeout=10)
        vb_resp.raise_for_status()
        vb_soup = BeautifulSoup(vb_resp.text, 'html.parser')
        
        # Table A is usually the first EB table, Table B is the second
        # But titles are better markers
        results["final_action_date"] = self._parse_vb_table(vb_soup, "FINAL ACTION")
        results["filing_date"] = self._parse_vb_table(vb_soup, "DATES FOR FILING")
        
        # 2. Fetch USCIS Authorized Chart
        try:
            uscis_resp = requests.get(self.USCIS_CHART_URL, timeout=10)
            uscis_resp.raise_for_status()
            u_text = uscis_resp.text.upper()
            if "DATES FOR FILING" in u_text and "FOR THE CURRENT MONTH" in u_text:
                results["authorized_chart"] = "B"
            else:
                results["authorized_chart"] = "A"
        except Exception as e:
            logging.warning(f"Failed to parse USCIS chart authorization: {e}")
            results["authorized_chart"] = "A" # Conservative default

        return results

    def parse(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        # Validation
        for field in ["final_action_date", "filing_date"]:
            val = raw_data.get(field)
            if not val or not re.match(self.DATE_REGEX, str(val)):
                logging.error(f"Validation failed for Visa Bulletin field {field}: {val}")
                # We don't raise here, we let the runner handle the missing data
        
        return raw_data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "category": self.preference_category,
            "country": self.country
        }

def get_skill(artha_dir: Path) -> VisaBulletinSkill:
    # Read preference from immigration.md if possible
    imm_file = artha_dir / "state" / "immigration.md"
    category = "EB-2"
    if imm_file.exists():
        text = imm_file.read_text()
        match = re.search(r"visa_preference_category:\s*(\S+)", text)
        if match:
            category = match.group(1)
            
    return VisaBulletinSkill(preference_category=category)
