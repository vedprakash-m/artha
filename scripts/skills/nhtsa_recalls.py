import requests
from typing import Dict, Any, List
from pathlib import Path
import re
from .base_skill import BaseSkill

class NHTSARecallSkill(BaseSkill):
    """
    Skill to check for vehicle recalls using the NHTSA API.
    """
    
    VIN_URL = "https://api.nhtsa.dot.gov/recalls/recallsByVIN/{vin}?format=json"
    VEHICLE_URL = "https://api.nhtsa.dot.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}&format=json"
    
    def __init__(self, vehicles: List[Dict[str, str]]):
        super().__init__(name="nhtsa_recalls", priority="P1")
        self.vehicles = vehicles

    @property
    def compare_fields(self) -> List[str]:
        return ["recall_count", "recall_ids"]

    def pull(self) -> Dict[str, Any]:
        results = {}
        for vehicle in self.vehicles:
            v_id = vehicle.get("vin") or f"{vehicle.get('make')}_{vehicle.get('model')}"
            
            try:
                if vehicle.get("vin") and vehicle.get("vin") != "unknown":
                    url = self.VIN_URL.format(vin=vehicle["vin"])
                else:
                    url = self.VEHICLE_URL.format(
                        make=vehicle.get("make"),
                        model=vehicle.get("model"),
                        year=vehicle.get("year")
                    )
                
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    results[v_id] = response.json()
                else:
                    results[v_id] = {"error": f"HTTP {response.status_code}"}
            except Exception as e:
                results[v_id] = {"error": str(e)}
                
        return results

    def parse(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        parsed = {}
        for v_id, info in raw_data.items():
            if "error" in info:
                parsed[v_id] = {"status": "error", "message": info["error"]}
                continue
            
            recalls = info.get("results", [])
            parsed[v_id] = {
                "recall_count": info.get("Count", 0),
                "recall_ids": [r.get("NHTSACampaignNumber") for r in recalls],
                "details": [
                    {
                        "id": r.get("NHTSACampaignNumber"),
                        "summary": r.get("Summary"),
                        "component": r.get("Component"),
                        "consequence": r.get("Conequence") # Note: typo in NHTSA API is 'Conequence'
                    } for r in recalls
                ]
            }
        return parsed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "vehicles": self.vehicles
        }

def get_skill(artha_dir: Path) -> NHTSARecallSkill:
    # Read vehicle data from user profile
    import sys as _sys
    _sys.path.insert(0, str(artha_dir / "scripts"))
    vehicles: list = []
    try:
        from profile_loader import get as _pget, has_profile as _has_profile
        if _has_profile():
            profile_vehicles = _pget("domains.vehicle.vehicles", []) or []
            # Normalize to NHTSA format: {make, model, year, vin}
            for v in profile_vehicles:
                if isinstance(v, dict) and (v.get("make") or v.get("vin")):
                    vehicles.append({
                        "make": v.get("make", "").upper(),
                        "model": v.get("model", "").upper(),
                        "year": str(v.get("year", "")),
                        "vin": v.get("vin", "unknown"),
                    })
    except Exception:
        pass

    return NHTSARecallSkill(vehicles=vehicles)
