from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging
from datetime import datetime, timezone
from pathlib import Path
import importlib
import sys


def read_state_text(artha_dir: Path, domain: str) -> str:
    """Return a domain's State File text, or "" when it is unavailable.

    Uses a plaintext ``state/<domain>.md`` if one exists; otherwise decrypts ``state/<domain>.md.age`` in memory
    (the vault never writes plaintext to disk), so skills keep working with the in-memory vault model.
    """
    plain = Path(artha_dir) / "state" / f"{domain}.md"
    try:
        if plain.exists():
            return plain.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not (Path(artha_dir) / "state" / f"{domain}.md.age").exists():
        return ""
    try:
        scripts = str(Path(artha_dir) / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        foundation = importlib.import_module("foundation")   # loaded lazily: importing it can re-exec into the venv
        return foundation.get_age_vault(state_dir=Path(artha_dir) / "state").read(domain)
    except BaseException:  # noqa: BLE001 - a missing key or vault must degrade, never crash the sweep
        return ""


class BaseSkill(ABC):
    """
    Abstract Base Class for Artha Data Fidelity Skills.
    All skills must implement pull(), parse(), and to_dict().
    """
    
    def __init__(self, name: str, priority: str = "P1"):
        self.name = name
        self.priority = priority
        self.last_run = None
        self.status = "idle"
        self.error = None

    @abstractmethod
    def pull(self) -> Any:
        """Fetch raw data from the source (e.g., HTML, JSON)."""
        pass

    @abstractmethod
    def parse(self, raw_data: Any) -> Dict[str, Any]:
        """Extract structured data from the raw source."""
        pass

    def execute(self) -> Dict[str, Any]:
        """Orchestrate pull and parse, handling exceptions."""
        self.status = "running"
        self.last_run = datetime.now(timezone.utc).isoformat()
        try:
            raw = self.pull()
            data = self.parse(raw)
            self.status = "success"
            return {
                "name": self.name,
                "status": self.status,
                "timestamp": self.last_run,
                "data": data
            }
        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logging.error(f"Skill {self.name} failed: {self.error}")
            return {
                "name": self.name,
                "status": self.status,
                "timestamp": self.last_run,
                "error": self.error
            }

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Return a dictionary representation of the skill results."""
        pass

    @property
    @abstractmethod
    def compare_fields(self) -> list:
        """Return list of field names used to detect changes between runs."""
        pass
