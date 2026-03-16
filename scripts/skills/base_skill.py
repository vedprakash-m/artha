from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging
from datetime import datetime, timezone

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
