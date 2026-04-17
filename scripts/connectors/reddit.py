"""
scripts/connectors/reddit.py — Reddit top-posts connector for Artha.

Fetches top posts from configured subreddits using the public Reddit JSON API.
Uses stdlib only (urllib) — no third-party dependencies required.

Security note (R9 — injection sanitization):
  Every post title is sanitized before leaving this module:
  1. Truncated to 80 characters maximum.
  2. Stripped of markdown/HTML special characters: < > * [ ] ` (backtick).
  3. Rejected if the sanitized title matches any pattern in
     config/claw_bridge.yaml → injection_filter → blocked_patterns.
     Rejected items are logged as REDDIT_SANITIZE_REJECT in state/audit.md.

Output record format (pipeline-compatible):
  {
    "id":           "reddit_t3_abc123",
    "source":       "reddit",
    "subreddit":    "LocalLLama",
    "title":        "New Qwen model released",
    "score":        342,
    "num_comments": 87,
    "url":          "https://reddit.com/r/LocalLLama/comments/...",
    "created_utc":  "2026-04-16T14:30:00Z",
    "source_tag":   "reddit_localllama"
  }

Config block in config/connectors.yaml (append after existing connectors):
  reddit:
    type: feed
    provider: reddit
    enabled: true
    run_on: all
    fetch:
      handler: scripts/connectors/reddit.py
      subreddits:
        - {name: LocalLLama, tag: reddit_localllama}
        - {name: MachineLearning, tag: reddit_ml}
        - {name: immigration, tag: reddit_immigration}
        - {name: KiaEV6, tag: reddit_ev6}
      max_per_sub: 10
      min_score: 20
    rate_limit:
      delay_sec: 2.0

Ref: specs/ac-int.md §7.2
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator

# ── Path setup ───────────────────────────────────────────────────────────────
_CONNECTOR_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR   = _CONNECTOR_DIR.parent
_ARTHA_DIR     = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_USER_AGENT = "Artha/1.0 (personal intelligence OS; +https://github.com/)"

# Characters stripped from titles during injection sanitization (R9)
_STRIP_CHARS = re.compile(r"[<>*\[\]`]")

# ── Sanitization helpers ─────────────────────────────────────────────────────

def _load_blocked_patterns() -> list[str]:
    """Load injection_filter.blocked_patterns from config/claw_bridge.yaml.

    Returns an empty list on any error so the connector degrades gracefully.
    IMPORTANT: patterns come from claw_bridge.yaml → injection_filter →
    blocked_patterns, NOT lint_rules.yaml (see specs/ac-int.md §7.2 note).
    """
    try:
        import yaml  # type: ignore[import]
        cfg_path = _ARTHA_DIR / "config" / "claw_bridge.yaml"
        with cfg_path.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        return list(cfg.get("injection_filter", {}).get("blocked_patterns", []))
    except Exception:
        return []


def _write_audit(event_type: str, **kwargs: str | int | bool | None) -> None:
    """Best-effort append to state/audit.md; never raises."""
    try:
        import logging
        from datetime import datetime, timezone
        from pathlib import Path

        ts = datetime.now(timezone.utc).isoformat()
        parts = [f"[{ts}] {event_type}"]
        for k, v in kwargs.items():
            parts.append(f"{k}: {v}")
        entry = " | ".join(parts)
        audit_path = Path(__file__).resolve().parents[2] / "state" / "audit.md"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        logging.getLogger(__name__).debug("Audit: %s", entry)
    except Exception:
        pass


def _sanitize_title(
    title: str,
    blocked: list[str],
    *,
    subreddit: str = "",
    post_id: str = "",
) -> str | None:
    """Apply injection sanitization to a post title.

    Returns the sanitized title, or None if the title must be rejected.
    Logs REDDIT_SANITIZE_REJECT to audit.md for rejected items.
    """
    # Step 1: Truncate to 80 chars
    sanitized = title.strip()[:80]
    # Step 2: Strip special chars
    sanitized = _STRIP_CHARS.sub("", sanitized).strip()
    # Step 3: Check against blocked_patterns
    lower = sanitized.lower()
    for pattern in blocked:
        if pattern.lower() in lower:
            _write_audit(
                "REDDIT_SANITIZE_REJECT",
                post_id=post_id,
                subreddit=subreddit,
                pattern=str(pattern)[:50],
            )
            return None
    return sanitized


# ── Reddit API helpers ───────────────────────────────────────────────────────

def _fetch_top(subreddit: str, limit: int, timeout: int = 15) -> list[dict]:
    """Fetch top posts for the day from a subreddit via the public JSON API.

    Returns a list of raw post dicts from the Reddit JSON response.
    """
    url = f"https://www.reddit.com/r/{subreddit}/top.json?t=day&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError(f"Subreddit r/{subreddit} not found (404)") from exc
        if exc.code == 429:
            raise RuntimeError(f"Reddit rate-limited (429) for r/{subreddit}") from exc
        raise
    data = json.loads(raw)
    return [child["data"] for child in data.get("data", {}).get("children", [])]


def _build_record(
    post: dict,
    *,
    subreddit: str,
    source_tag: str,
    blocked: list[str],
    min_score: int,
) -> dict | None:
    """Convert a raw Reddit post dict to an Artha output record.

    Returns None if the post is filtered by score or sanitization.
    """
    score = int(post.get("score", 0))
    if score < min_score:
        return None

    raw_title = post.get("title", "")
    if not raw_title:
        return None

    post_id = post.get("name", post.get("id", ""))  # e.g. "t3_abc123"
    sanitized_title = _sanitize_title(raw_title, blocked, subreddit=subreddit, post_id=post_id)
    if sanitized_title is None:
        return None

    # Build canonical URL from permalink
    permalink = post.get("permalink", "")
    if permalink:
        url = f"https://www.reddit.com{permalink}"
    else:
        url = post.get("url", "")

    created_ts = post.get("created_utc", 0.0)
    created_iso = (
        datetime.fromtimestamp(float(created_ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if created_ts
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    return {
        "id":           f"reddit_{post_id}" if post_id else f"reddit_{subreddit}_{created_iso}",
        "source":       "reddit",
        "subreddit":    subreddit,
        "title":        sanitized_title,
        "score":        score,
        "num_comments": int(post.get("num_comments", 0)),
        "url":          url,
        "created_utc":  created_iso,
        "source_tag":   source_tag,
    }


# ── Public connector API ─────────────────────────────────────────────────────

def fetch(
    *,
    subreddits: list[dict] | None = None,
    max_per_sub: int = 10,
    min_score: int = 20,
    delay_sec: float = 2.0,
    auth_context: Dict[str, Any] | None = None,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield top Reddit posts from configured subreddits.

    Args:
        subreddits:  List of dicts: {"name": "LocalLLama", "tag": "reddit_localllama"}.
                     Falls back to auth_context["subreddits"] if not provided directly.
        max_per_sub: Maximum posts to fetch per subreddit (default: 10).
        min_score:   Minimum Reddit score; lower-scored posts are skipped (default: 20).
        delay_sec:   Seconds to wait between subreddit requests (default: 2.0).
        auth_context: Optional context dict from pipeline (not used; Reddit is public).
    """
    if subreddits is None:
        subreddits = (auth_context or {}).get("subreddits", [])

    if not subreddits:
        return

    blocked = _load_blocked_patterns()

    for idx, sub_cfg in enumerate(subreddits):
        sub_name   = sub_cfg.get("name", "")
        source_tag = sub_cfg.get("tag", f"reddit_{sub_name.lower()}")
        if not sub_name:
            continue

        # Rate limit: delay between requests (skip before first request)
        if idx > 0:
            time.sleep(delay_sec)

        try:
            posts = _fetch_top(sub_name, limit=max_per_sub)
        except Exception as exc:
            # Log and continue to next subreddit — single failure must not abort run
            sys.stderr.write(f"[reddit] r/{sub_name} fetch error: {exc}\n")
            continue

        for post in posts:
            record = _build_record(
                post,
                subreddit=sub_name,
                source_tag=source_tag,
                blocked=blocked,
                min_score=min_score,
            )
            if record is not None:
                yield record
