"""
scripts/narrative/_base.py — NarrativeEngineBase: shared state, caching, freshness.

Provides:
  - __init__: state_dir, profile, _cache
  - _fm / _body: cached domain reads
  - _freshness_footer: §3.8 data freshness
  - _load_program_metrics: xpf-program-structure.md parser (shared by multiple templates)
  - _extract_ws_metrics: per-workstream metric row extraction
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("artha.narrative_engine")

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_WORK_STATE_DIR = _REPO_ROOT / "state" / "work"

from work.helpers import (  # noqa: E402
    _parse_dt, _age_str, _read_frontmatter, _read_body, _load_profile,
)


class NarrativeEngineBase:
    """
    Shared state, caching, and data-access helpers for all narrative templates.

    All generate_* functions accept an instance of this class as their first
    argument (``base``).  NarrativeEngine in __init__.py subclasses this and
    delegates to the standalone functions via deferred relative imports.
    """

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        profile: Optional[dict[str, Any]] = None,
    ) -> None:
        self.state_dir = state_dir or _WORK_STATE_DIR
        self.profile = profile or _load_profile()
        self._cache: dict[str, Any] = {}

    def _fm(self, domain: str) -> dict[str, Any]:
        """Cached frontmatter read for a domain."""
        if domain not in self._cache:
            self._cache[domain] = _read_frontmatter(self.state_dir / f"{domain}.md")
        return self._cache[domain]

    def _body(self, domain: str) -> str:
        """Cached body read for a domain."""
        key = f"_body_{domain}"
        if key not in self._cache:
            self._cache[key] = _read_body(self.state_dir / f"{domain}.md")
        return self._cache[key]

    def _freshness_footer(self) -> str:
        """Build §3.8 data freshness footer from state file timestamps."""
        domains_checked = [
            "work-calendar", "work-comms", "work-projects",
            "work-performance", "xpf-program-structure",
        ]
        ages = []
        for d in domains_checked:
            fm = self._fm(d)
            last_updated = fm.get("last_updated")
            if last_updated:
                dt = _parse_dt(str(last_updated))
                ages.append((d.split("-", 1)[1], _age_str(dt)))

        if ages:
            age_parts = " | ".join(f"{name}: {age}" for name, age in ages[:5])
            return f"\n---\n_Data freshness: {age_parts}_\n"
        return "\n---\n_Data freshness: unknown — run `/work refresh`_\n"

    def _load_program_metrics(self) -> dict[str, Any]:
        """
        Parse xpf-program-structure.md for newsletter-ready metrics.

        Returns dict with:
          signal_summary: {red: int, yellow: int, green: int}
          risk_posture: str (e.g., "MEDIUM")
          risk_rationale: str
          workstreams: [{name, lt_surface, signals: {red, yellow, green}, top_metric}]
          key_metrics: [{id, name, value, signal}] — red and green items only
        """
        cache_key = "_program_metrics"
        if cache_key in self._cache:
            return self._cache[cache_key]

        result: dict[str, Any] = {
            "signal_summary": {"red": 0, "yellow": 0, "green": 0},
            "risk_posture": "",
            "risk_rationale": "",
            "workstreams": [],
            "key_metrics": [],
        }

        body = self._body("xpf-program-structure")
        if not body:
            self._cache[cache_key] = result
            return result

        # Parse signal summary counts
        for line in body.split("\n"):
            if "🔴 Red" in line and "|" in line:
                m = re.search(r"\|\s*(\d+)\s*\|", line.split("🔴")[1])
                if m:
                    result["signal_summary"]["red"] = int(m.group(1))
            elif "🟡 Yellow" in line and "|" in line:
                m = re.search(r"\|\s*(\d+)\s*\|", line.split("🟡")[1])
                if m:
                    result["signal_summary"]["yellow"] = int(m.group(1))
            elif "🟢 Green" in line and "|" in line:
                m = re.search(r"\|\s*(\d+)\s*\|", line.split("🟢")[1])
                if m:
                    result["signal_summary"]["green"] = int(m.group(1))
            elif "Overall Program Risk Posture" in line:
                pm = re.search(r":\s*🟡?\s*(\w+(?:[- ]\w+)?)", line)
                if pm:
                    result["risk_posture"] = pm.group(1).strip()
            elif line.startswith("Rationale:"):
                result["risk_rationale"] = line[len("Rationale:"):].strip()[:200]

        # Parse per-workstream data
        ws_pattern = re.compile(
            r"^### (WS\d+)\s*—\s*(.+)$", re.MULTILINE
        )
        lt_pattern = re.compile(r"\*\*LT Surface:\*\*\s*(.+)")

        for ws_match in ws_pattern.finditer(body):
            ws_id = ws_match.group(1)
            ws_name = ws_match.group(2).strip()
            # Get text until next WS or end
            start = ws_match.end()
            next_ws = ws_pattern.search(body, start)
            ws_text = body[start:next_ws.start()] if next_ws else body[start:]

            lt_m = lt_pattern.search(ws_text)
            lt_surface = lt_m.group(1).strip() if lt_m else ""

            # Count signals in metric tables
            signals = {"red": 0, "yellow": 0, "green": 0}
            top_metric = ""
            for tl in ws_text.split("\n"):
                if tl.startswith("| M") and "|" in tl:
                    cols = [c.strip() for c in tl.split("|") if c.strip()]
                    if len(cols) >= 5:
                        sig = cols[4]
                        if "🔴" in sig:
                            signals["red"] += 1
                            if not top_metric:
                                top_metric = f"🔴 {cols[1]}: {cols[2][:60]}"
                        elif "🟡" in sig:
                            signals["yellow"] += 1
                        elif "🟢" in sig:
                            signals["green"] += 1
                            if not top_metric:
                                top_metric = f"🟢 {cols[1]}: {cols[2][:60]}"

            result["workstreams"].append({
                "id": ws_id, "name": ws_name, "lt_surface": lt_surface,
                "signals": signals, "top_metric": top_metric,
            })

            # Collect red metrics for key_metrics list
            for tl in ws_text.split("\n"):
                if tl.startswith("| M") and "🔴" in tl:
                    cols = [c.strip() for c in tl.split("|") if c.strip()]
                    if len(cols) >= 5:
                        result["key_metrics"].append({
                            "id": cols[0], "name": cols[1],
                            "value": cols[2][:80], "signal": "🔴",
                        })

        self._cache[cache_key] = result
        return result

    def _extract_ws_metrics(self, ws_id: str, signal_filter: str = "") -> list[dict[str, str]]:
        """Extract metrics from a specific workstream, optionally filtered by signal emoji."""
        body = self._body("xpf-program-structure")
        if not body:
            return []

        ws_pattern = re.compile(r"^### (WS\d+)\s*—\s*(.+)$", re.MULTILINE)
        results: list[dict[str, str]] = []

        for ws_match in ws_pattern.finditer(body):
            if ws_match.group(1) != ws_id:
                continue
            ws_start = ws_match.end()
            next_ws = ws_pattern.search(body, ws_start)
            ws_text = body[ws_start:next_ws.start()] if next_ws else body[ws_start:]

            for tl in ws_text.split("\n"):
                if not tl.startswith("| M"):
                    continue
                if signal_filter and signal_filter not in tl:
                    continue
                cols = [c.strip() for c in tl.split("|") if c.strip()]
                if len(cols) >= 5:
                    results.append({
                        "id": cols[0], "name": cols[1],
                        "value": cols[2][:80], "signal": cols[4],
                    })
            break

        return results
