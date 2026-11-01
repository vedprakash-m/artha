"""pr_stage — Content Stage package (PR-2).

Bounded context for managed, personalized social content lifecycle.
Exposes the public API used by scripts/pr_manager.py Step 8 integration.

Public surface:
    from pr_stage.service import ContentStage
    from pr_stage.domain import ContentCard, CardStatus, PlatformDraftStatus
    from pr_stage.repository import GalleryRepository, GalleryMemory
    from pr_stage.personalizer import DraftPersonalizer
    from pr_stage.telemetry import StageLogger
"""

from pr_stage.domain import ContentCard, CardStatus, PlatformDraftStatus
from pr_stage.service import ContentStage

__all__ = [
    "ContentCard",
    "CardStatus",
    "PlatformDraftStatus",
    "ContentStage",
]
