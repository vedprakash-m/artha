"""scripts/skills/watch_monitor.py — Watch Monitor Skill (P4.2)

Reads:  tmp/watch_snapshot.jsonl        (pipeline output — all connectors)
Writes: state/watch_backlog.yaml        (persistent keyword-match log)
Sends:  Telegram alert (keyring)        (high urgency + score > 50 only)

Deterministic keyword-filter pipeline (DP-3 — zero LLM calls).
Routing logic (ref: specs/ac-int.md §7.3):

  urgency=high, score>50   → immediate Telegram alert (max 3/watch/day)
                              + write to backlog
  urgency=high, score 20-50 → daily digest (backlog only)
  urgency=medium            → daily digest (backlog only)
  urgency=low               → weekly digest (backlog only)

Rate limit: _MAX_ALERTS_PER_WATCH_PER_DAY (3) tracked in
tmp/watch_alert_counts.yaml (ephemeral, resets at UTC midnight).

Failure modes:
  • watch_snapshot.jsonl absent  → skip silently (not an error)
  • config/watches.yaml absent   → return empty summary (not an error)
  • keyring missing credentials  → log warning, skip Telegram; continue
  • Telegram POST fails          → log warning; continue (backlog still written)
  • zero matches                 → valid; empty backlog is not an error
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import keyring  # type: ignore[import]
import yaml

from skills.base_skill import BaseSkill  # type: ignore[import]

# ── Paths ─────────────────────────────────────────────────────────────────────

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_SNAPSHOT_FILE = _ARTHA_DIR / "tmp" / "watch_snapshot.jsonl"
_BACKLOG_FILE = _ARTHA_DIR / "state" / "watch_backlog.yaml"
_WATCHES_FILE = _ARTHA_DIR / "config" / "watches.yaml"
_ALERT_COUNTS_FILE = _ARTHA_DIR / "tmp" / "watch_alert_counts.yaml"

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_ALERTS_PER_WATCH_PER_DAY = 3
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# ── Private helpers ───────────────────────────────────────────────────────────


def _load_watches() -> dict[str, Any]:
    """Load watches config. Returns empty dict if file absent."""
    if not _WATCHES_FILE.exists():
        return {}
    with _WATCHES_FILE.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg.get("watches", {})


def _load_backlog() -> dict[str, list]:
    """Load existing watch backlog. Returns empty dict if file absent."""
    if not _BACKLOG_FILE.exists():
        return {}
    with _BACKLOG_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_alert_counts() -> dict[str, int]:
    """Return today's per-watch alert send counts.

    Returns empty dict if file absent or date has rolled over.
    """
    if not _ALERT_COUNTS_FILE.exists():
        return {}
    try:
        with _ALERT_COUNTS_FILE.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if data.get("date") != date.today().isoformat():
            return {}
        return data.get("counts", {})
    except Exception:
        return {}


def _save_alert_counts(counts: dict[str, int]) -> None:
    """Persist today's per-watch alert send counts."""
    _ALERT_COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": date.today().isoformat(), "counts": counts}
    _ALERT_COUNTS_FILE.write_text(
        yaml.dump(payload, default_flow_style=False),
        encoding="utf-8",
    )


def _send_telegram_alert(text: str) -> bool:
    """POST a plain-text message to the primary Telegram chat.

    Returns True on success. Never raises — logs to stderr and returns False
    on any failure (keyring miss, network error, API error).
    """
    token = keyring.get_password("artha", "artha-telegram-bot-token")
    chat_id = keyring.get_password("artha", "artha-telegram-primary-chat-id")
    if not token or not chat_id:
        print(
            "[watch_monitor] keyring: missing telegram credentials — alert skipped",
            file=sys.stderr,
        )
        return False
    url = _TELEGRAM_API.format(token=token)
    body = json.dumps({"chat_id": chat_id, "text": text[:4096]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return bool(result.get("ok"))
    except Exception as exc:
        print(f"[watch_monitor] telegram alert failed: {exc}", file=sys.stderr)
        return False


def _match_keywords(title: str, keywords: list[str]) -> list[str]:
    """Return list of matched keywords (case-insensitive substring match)."""
    lower = title.lower()
    return [kw for kw in keywords if kw.lower() in lower]


# ── Skill class ───────────────────────────────────────────────────────────────


class WatchMonitorSkill(BaseSkill):
    """Keyword-filter pipeline output; route to backlog or immediate Telegram alert."""

    def __init__(self, artha_dir: Path | None = None) -> None:
        super().__init__(name="watch_monitor", priority="P1")
        self.artha_dir = artha_dir or _ARTHA_DIR

    @property
    def compare_fields(self) -> list[str]:
        return ["total_matches", "immediate_alerts_sent", "watch_count"]

    # ── BaseSkill interface ───────────────────────────────────────────────

    def pull(self) -> dict[str, Any]:
        """Load watch_snapshot.jsonl items and watches config."""
        watches = _load_watches()

        items: list[dict] = []
        if _SNAPSHOT_FILE.exists():
            for line in _SNAPSHOT_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        return {"watches": watches, "items": items}

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Match items; write backlog; send immediate Telegram alerts as needed."""
        watches: dict[str, Any] = raw_data["watches"]
        items: list[dict] = raw_data["items"]

        backlog = _load_backlog()
        alert_counts = _load_alert_counts()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today_str = date.today().strftime("%Y%m%d")

        total_matches = 0
        immediate_sent = 0

        for watch_name, watch_cfg in watches.items():
            keywords: list[str] = watch_cfg.get("keywords", [])
            urgency: str = watch_cfg.get("urgency", "medium")
            tg_fmt: str = watch_cfg.get(
                "telegram_format", "{title} | Score: {score} | {url}"
            )

            existing_ids: set[str] = {
                e["id"] for e in backlog.get(watch_name, [])
            }
            seq = 0

            for item in items:
                title: str = item.get("title", "")
                score: int = int(item.get("score", 0) or 0)
                url: str = item.get("url", "")
                source: str = item.get("source_tag") or item.get("source", "")

                matched = _match_keywords(title, keywords)
                if not matched:
                    continue

                seq += 1
                entry_id = f"watch_{watch_name}_{today_str}_{seq:03d}"

                # Skip if already in backlog (idempotent on re-run)
                if entry_id in existing_ids:
                    continue

                entry: dict[str, Any] = {
                    "id": entry_id,
                    "watch_name": watch_name,
                    "urgency": urgency,
                    "title": title,
                    "url": url,
                    "source": source,
                    "score": score,
                    "matched_keywords": matched,
                    "matched_at": now_iso,
                    "status": "new",
                }
                backlog.setdefault(watch_name, []).append(entry)
                total_matches += 1

                # Immediate Telegram alert: high urgency + score > 50
                if urgency == "high" and score > 50:
                    daily_count = alert_counts.get(watch_name, 0)
                    if daily_count < _MAX_ALERTS_PER_WATCH_PER_DAY:
                        text = tg_fmt.format(title=title, score=score, url=url)
                        if _send_telegram_alert(text):
                            alert_counts[watch_name] = daily_count + 1
                            immediate_sent += 1

        # Persist backlog (OneDrive-synced)
        _BACKLOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BACKLOG_FILE.write_text(
            yaml.dump(
                backlog,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        _save_alert_counts(alert_counts)

        return {
            "total_matches": total_matches,
            "immediate_alerts_sent": immediate_sent,
            "watch_count": len(watches),
            "backlog_entry_count": sum(len(v) for v in backlog.values()),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "snapshot_file": str(_SNAPSHOT_FILE),
            "backlog_file": str(_BACKLOG_FILE),
            "watches_file": str(_WATCHES_FILE),
        }


# ── Factory ───────────────────────────────────────────────────────────────────


def get_skill(artha_dir: Path | None = None) -> WatchMonitorSkill:
    """Factory function — entry point for skill_runner.py."""
    return WatchMonitorSkill(artha_dir=artha_dir)
