"""Read-only incumbent adapter over curated State Files (re-artha §6.14, §7.4).

The incumbent workflow keeps authority for non-pilot Domains.  This adapter exposes
its *current curated conclusions* to the combined Briefing without running that
workflow: for each non-pilot State File it reports the file's own domain, last update
and content version, labelled ``legacy_state`` ("from your records, dated …").  It
reads plaintext files only; an encrypted ``.md.age`` file is reported as not
unlocked instead of being decrypted to disk, and its absence of detail is never
presented as "nothing there".
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from catchup_core.contracts import CoverageEntry, CoverageStatus
from catchup_core.incumbent import IncumbentConclusion, IncumbentOutput

__all__ = ["LegacyStateAdapter"]

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
_SKIP = {"audit", "lessons", "memory", "health-check", "dashboard", "open_items", "catch_up_runs",
         "checkpoint", "telemetry", "skills_cache", "content_stage", "self_model"}
STALE_DAYS = 30


def _fields(text: str) -> dict[str, str]:
    m = _FRONTMATTER.match(text)
    out: dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep and not line.startswith((" ", "-")):
                out[key.strip()] = value.strip().strip("\"'")
    return out


class LegacyStateAdapter:
    def __init__(self, state_dir: Path | str, pilot_domains: Sequence[str], now: datetime):
        self._dir = Path(state_dir)
        self._pilot = set(pilot_domains)
        self._now = now

    def run(self, signals: Sequence[str], *, budget_seconds: float) -> IncumbentOutput:
        conclusions: list[IncumbentConclusion] = []
        coverage: list[CoverageEntry] = []
        locked: list[str] = []
        if not self._dir.is_dir():
            return IncumbentOutput(coverage=(CoverageEntry(
                "incumbent", "state", CoverageStatus.UNAVAILABLE, False, detail="state directory missing"),))
        for path in sorted(self._dir.glob("*.md")):
            name = path.stem
            if name in _SKIP or name in self._pilot or name.startswith((".", "_")):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fields = _fields(text)
            domain = fields.get("domain", name)
            if domain in self._pilot:
                continue
            updated = fields.get("last_updated") or fields.get("last_activity") or ""
            label = f"dated {updated[:10]}" if updated else "undated"
            stale = ""
            try:
                age = (self._now.date() - datetime.fromisoformat(updated[:10]).date()).days
                if age > STALE_DAYS:
                    stale = f"; {age} days old"
            except ValueError:
                stale = "; update date unknown"
            version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            conclusions.append(IncumbentConclusion(
                domain, f"{domain}: from your records, {label}{stale} — not re-verified from a source", version))
        for path in sorted(self._dir.glob("*.md.age")):
            name = path.name[:-len(".md.age")]
            if name not in self._pilot and not (self._dir / f"{name}.md").exists():
                locked.append(name)
        if locked:
            coverage.append(CoverageEntry(
                "incumbent", "encrypted-state", CoverageStatus.NOTIFIED_NOT_FETCHED, False,
                blind_spots=tuple(locked), detail=f"{len(locked)} encrypted domain(s) not unlocked in this session"))
        return IncumbentOutput(tuple(conclusions), tuple(coverage))
