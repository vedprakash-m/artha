"""API-based content discovery connector for AI Trend Radar.

Fetches developer-tryable AI content from structured, trusted APIs:
  1. Hacker News API — community-validated stories (score-gated)
  2. dev.to API — top articles by tag with reaction counts
  3. GitHub API — trending repos + releases for watched orgs

No open web crawling. All endpoints are known, authenticated (or free),
and return structured data with built-in community quality signals
(HN score, dev.to reactions, GitHub stars).

Record schema matches rss_feed.py / google_email.py for pipeline compat:
  {id, subject, from, date_iso, body, source, feed_url, link}
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator

_log = logging.getLogger(__name__)

_UA = "Artha/1.0 (api-discovery connector)"
_DEFAULT_TIMEOUT = 10


# ── Hacker News API ──────────────────────────────────────────────────────────

def _fetch_hn_stories(
    *,
    query_type: str = "top",
    min_score: int = 30,
    max_results: int = 15,
    since_dt: datetime | None = None,
    ai_keywords: list[str] | None = None,
) -> list[dict]:
    """Fetch HN stories via Firebase API, filtered by score and AI relevance."""
    endpoint_map = {
        "top": "topstories",
        "new": "newstories",
        "best": "beststories",
        "show": "showstories",
        "ask": "askstories",
    }
    endpoint = endpoint_map.get(query_type, "topstories")
    url = f"https://hacker-news.firebaseio.com/v0/{endpoint}.json"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
            story_ids = json.loads(resp.read())
    except Exception as exc:
        _log.warning("HN API %s failed: %s", endpoint, exc)
        return []

    keywords = [k.lower() for k in (ai_keywords or [])]
    results: list[dict] = []

    for sid in story_ids[:80]:  # scan up to 80 to find enough AI-relevant ones
        if len(results) >= max_results:
            break
        try:
            item_url = f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
            req = urllib.request.Request(item_url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=5) as resp:
                item = json.loads(resp.read())
        except Exception:
            continue

        if not item or item.get("type") != "story":
            continue

        score = item.get("score", 0)
        if score < min_score:
            continue

        title = item.get("title", "")
        item_url_link = item.get("url", "")
        text = item.get("text", "") or ""
        hn_time = item.get("time", 0)

        # Date filter
        if hn_time:
            item_dt = datetime.fromtimestamp(hn_time, tz=timezone.utc)
            if since_dt and item_dt < since_dt:
                continue
            date_iso = item_dt.isoformat()
        else:
            date_iso = datetime.now(timezone.utc).isoformat()

        # AI relevance filter
        search_text = f"{title} {text} {item_url_link}".lower()
        if keywords and not any(kw in search_text for kw in keywords):
            continue

        hn_link = f"https://news.ycombinator.com/item?id={sid}"
        prefix = "Show HN: " if query_type == "show" else ""

        results.append({
            "id": f"hn-{sid}",
            "subject": f"{prefix}{title}",
            "from": f"Hacker News ({query_type}) <{hn_link}>",
            "date_iso": date_iso,
            "body": _strip_html(text)[:1500] if text else f"Score: {score} | {item_url_link}",
            "source": "api_discovery",
            "feed_url": hn_link,
            "link": item_url_link or hn_link,
            "community_score": score,
        })

    return results


# ── dev.to API ───────────────────────────────────────────────────────────────

def _fetch_devto_articles(
    *,
    tags: list[str] | None = None,
    top_period: str = "7",
    max_per_tag: int = 8,
    max_total: int = 20,
    min_reactions: int = 10,
) -> list[dict]:
    """Fetch top dev.to articles by tag, sorted by reactions."""
    if not tags:
        tags = ["ai", "llm", "machinelearning"]

    seen_ids: set[int] = set()
    results: list[dict] = []

    for tag in tags:
        if len(results) >= max_total:
            break
        url = f"https://dev.to/api/articles?tag={tag}&top={top_period}&per_page={max_per_tag}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": _UA,
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                articles = json.loads(resp.read())
        except Exception as exc:
            _log.warning("dev.to API tag=%s failed: %s", tag, exc)
            continue

        for article in articles:
            if len(results) >= max_total:
                break
            aid = article.get("id", 0)
            if aid in seen_ids:
                continue
            seen_ids.add(aid)

            reactions = article.get("positive_reactions_count", 0)
            if reactions < min_reactions:
                continue

            results.append({
                "id": f"devto-{aid}",
                "subject": article.get("title", ""),
                "from": f"dev.to/{tag} <https://dev.to>",
                "date_iso": article.get("published_at", datetime.now(timezone.utc).isoformat()),
                "body": (article.get("description", "") or "")[:1500],
                "source": "api_discovery",
                "feed_url": f"https://dev.to/t/{tag}",
                "link": article.get("url", ""),
                "community_score": reactions,
            })

    return results


# ── GitHub API — Trending Repos & Releases ───────────────────────────────────

def _fetch_github_trending(
    *,
    topics: list[str] | None = None,
    days_back: int = 7,
    min_stars: int = 50,
    max_results: int = 10,
    auth_token: str = "",
) -> list[dict]:
    """Search GitHub repos by topic, recently created/pushed, sorted by stars."""
    if not topics:
        topics = ["llm", "ai-agent", "mcp"]

    headers: dict[str, str] = {
        "User-Agent": _UA,
        "Accept": "application/vnd.github.v3+json",
    }
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    seen: set[str] = set()
    results: list[dict] = []

    for topic in topics:
        if len(results) >= max_results:
            break
        q = f"topic:{topic} pushed:>{cutoff} stars:>{min_stars}"
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page=5"
        )
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            _log.warning("GitHub API topic=%s failed: %s", topic, exc)
            continue

        for repo in data.get("items", []):
            if len(results) >= max_results:
                break
            full_name = repo.get("full_name", "")
            if full_name in seen:
                continue
            seen.add(full_name)

            desc = (repo.get("description") or "")[:500]
            stars = repo.get("stargazers_count", 0)
            pushed = repo.get("pushed_at", "")
            lang = repo.get("language", "")

            results.append({
                "id": f"gh-{repo.get('id', 0)}",
                "subject": full_name,
                "from": f"GitHub Trending ({topic}) <https://github.com>",
                "date_iso": pushed or datetime.now(timezone.utc).isoformat(),
                "body": f"{desc}\n\nLanguage: {lang} | ⭐ {stars} stars | github.com/{full_name}",
                "source": "api_discovery",
                "feed_url": f"https://github.com/topics/{topic}",
                "link": repo.get("html_url", ""),
                "community_score": stars,
            })

    return results


def _fetch_github_releases(
    *,
    repos: list[str] | None = None,
    max_per_repo: int = 2,
    since_dt: datetime | None = None,
    auth_token: str = "",
) -> list[dict]:
    """Fetch recent releases from watched repos."""
    if not repos:
        return []

    headers: dict[str, str] = {
        "User-Agent": _UA,
        "Accept": "application/vnd.github.v3+json",
    }
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"

    results: list[dict] = []
    for repo in repos:
        url = f"https://api.github.com/repos/{repo}/releases?per_page={max_per_repo}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                releases = json.loads(resp.read())
        except Exception as exc:
            _log.warning("GitHub releases %s failed: %s", repo, exc)
            continue

        for rel in releases:
            pub = rel.get("published_at", "")
            if since_dt and pub:
                try:
                    rel_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    if rel_dt < since_dt:
                        continue
                except ValueError:
                    pass

            tag_name = rel.get("tag_name", "")
            body = (rel.get("body") or "")[:1500]
            name = rel.get("name") or tag_name

            results.append({
                "id": f"gh-rel-{repo}-{tag_name}",
                "subject": f"{repo} {name}",
                "from": f"GitHub Release ({repo}) <https://github.com/{repo}>",
                "date_iso": pub or datetime.now(timezone.utc).isoformat(),
                "body": body,
                "source": "api_discovery",
                "feed_url": f"https://github.com/{repo}/releases",
                "link": rel.get("html_url", ""),
                "community_score": 0,
            })

    return results


# ── Helpers ──────────────────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text).strip()


# ── Public Connector API ─────────────────────────────────────────────────────

def fetch(
    *,
    since: str = "7d",
    max_results: int = 50,
    auth_context: Dict[str, Any] | None = None,
    source_tag: str = "api_discovery",
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield AI-relevant items from structured APIs.

    Config (via auth_context):
      hn:          {enabled, types, min_score, max_results}
      devto:       {enabled, tags, top_period, min_reactions, max_results}
      github:      {enabled, topics, min_stars, watched_repos, max_results, token}
      ai_keywords: list[str]  (shared AI relevance filter for HN)
    """
    ctx = auth_context or {}
    hn_cfg = ctx.get("hn", {})
    devto_cfg = ctx.get("devto", {})
    gh_cfg = ctx.get("github", {})
    ai_keywords = ctx.get("ai_keywords", [])

    # Parse since into a datetime
    since_dt = _parse_since(since)
    total = 0

    # 1. Hacker News
    if hn_cfg.get("enabled", True):
        for story_type in hn_cfg.get("types", ["top", "show"]):
            if total >= max_results:
                break
            stories = _fetch_hn_stories(
                query_type=story_type,
                min_score=hn_cfg.get("min_score", 30),
                max_results=hn_cfg.get("max_results", 10),
                since_dt=since_dt,
                ai_keywords=ai_keywords,
            )
            for s in stories:
                if total >= max_results:
                    break
                yield s
                total += 1

    # 2. dev.to
    if devto_cfg.get("enabled", True):
        articles = _fetch_devto_articles(
            tags=devto_cfg.get("tags", ["ai", "llm", "machinelearning", "claude", "openai"]),
            top_period=devto_cfg.get("top_period", "7"),
            max_per_tag=devto_cfg.get("max_per_tag", 8),
            max_total=devto_cfg.get("max_results", 15),
            min_reactions=devto_cfg.get("min_reactions", 10),
        )
        for a in articles:
            if total >= max_results:
                break
            yield a
            total += 1

    # 3. GitHub trending + releases
    if gh_cfg.get("enabled", True):
        token = gh_cfg.get("token", "")
        # Trending repos
        repos = _fetch_github_trending(
            topics=gh_cfg.get("topics", ["llm", "ai-agent", "mcp", "claude"]),
            days_back=7,
            min_stars=gh_cfg.get("min_stars", 50),
            max_results=gh_cfg.get("max_results", 10),
            auth_token=token,
        )
        for r in repos:
            if total >= max_results:
                break
            yield r
            total += 1

        # Watched releases
        releases = _fetch_github_releases(
            repos=gh_cfg.get("watched_repos", []),
            since_dt=since_dt,
            auth_token=token,
        )
        for r in releases:
            if total >= max_results:
                break
            yield r
            total += 1


def health_check(auth_context: Dict[str, Any] | None = None) -> bool:
    """Quick check that at least one API is reachable."""
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json?limitToFirst=1&orderBy=%22$key%22",
            headers={"User-Agent": _UA},
        )
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


def _parse_since(since: str) -> datetime | None:
    if not since:
        return None
    m = re.fullmatch(r"(\d+)([dhm])", since.lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
        return datetime.now(timezone.utc) - delta
    try:
        dt = datetime.fromisoformat(since)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
