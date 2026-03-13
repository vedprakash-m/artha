import requests
import logging
from typing import Dict, Any, List
from pathlib import Path
import yaml
import re
from datetime import datetime, timezone
from .base_skill import BaseSkill

class NOAAWeatherSkill(BaseSkill):
    """
    Skill to fetch weather forecasts from NOAA/NWS.
    Features a 'Concierge' mode for outdoor activities.
    """
    
    BASE_URL = "https://api.weather.gov"
    
    def __init__(self, lat: float, lon: float, email: str, trigger_keywords: List[str]):
        super().__init__(name="noaa_weather", priority="P1")
        self.lat = lat
        self.lon = lon
        self.email = email
        self.trigger_keywords = trigger_keywords

    @property
    def compare_fields(self) -> List[str]:
        # Weather is always changing, but we only care about the short-term forecast
        return ["today_forecast"]

    def _get_forecast_url(self) -> str:
        """Step 1 of NOAA API: Get the forecast URL for coordinates."""
        headers = {"User-Agent": f"(Artha Personal OS, {self.email})"}
        url = f"{self.BASE_URL}/points/{self.lat},{self.lon}"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json().get("properties", {}).get("forecast")
        except Exception as e:
            logging.error(f"NOAA Step 1 failed: {e}")
            return ""

    def pull(self) -> Dict[str, Any]:
        """Step 2: Fetch the actual forecast."""
        forecast_url = self._get_forecast_url()
        if not forecast_url:
            raise Exception("Could not retrieve forecast URL from NOAA")
            
        headers = {"User-Agent": f"(Artha Personal OS, {self.email})"}
        response = requests.get(forecast_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def parse(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract relevant forecast periods."""
        periods = raw_data.get("properties", {}).get("periods", [])
        if not periods:
            return {"status": "no_data"}
            
        # Extract first 3 periods (Today, Tonight, Tomorrow)
        forecast_summary = []
        for p in periods[:3]:
            forecast_summary.append({
                "name": p.get("name"),
                "temperature": f"{p.get('temperature')} {p.get('temperatureUnit')}",
                "summary": p.get("shortForecast"),
                "detailed": p.get("detailedForecast")
            })
            
        return {
            "today_forecast": forecast_summary[0].get("summary") if forecast_summary else "unknown",
            "periods": forecast_summary,
            "is_friday": datetime.now(timezone.utc).weekday() == 4
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "keywords": self.trigger_keywords
        }

def get_skill(artha_dir: Path) -> NOAAWeatherSkill:
    # Load lat/lon/email from user profile
    import sys as _sys
    _sys.path.insert(0, str(artha_dir / "scripts"))
    try:
        from profile_loader import get as _pget, has_profile as _has_profile
        if _has_profile():
            email = _pget("family.primary_user.emails.gmail", "artha@example.com")
            lat = float(_pget("location.lat", 0.0))
            lon = float(_pget("location.lon", 0.0))
        else:
            email = "artha@example.com"
            lat, lon = 0.0, 0.0  # populate location.lat / location.lon in user_profile.yaml
    except Exception:
        email = "artha@example.com"
        lat, lon = 0.0, 0.0  # populate location.lat / location.lon in user_profile.yaml

    # Load keywords from skills.yaml
    keywords = ["hike", "summit", "trail", "peak"]
    skills_cfg = artha_dir / "config" / "skills.yaml"
    if skills_cfg.exists():
        with open(skills_cfg, "r") as f:
            cfg = yaml.safe_load(f)
            keywords = cfg.get("skills", {}).get("noaa_weather", {}).get("trigger_keywords", keywords)

    return NOAAWeatherSkill(lat=lat, lon=lon, email=email, trigger_keywords=keywords)
