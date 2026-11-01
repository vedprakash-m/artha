"""
scripts/connectors/rss_feed.py — RSS/Atom feed connector for Artha.

Fetches items from RSS 2.0 and Atom 1.0 feeds using only Python stdlib
(xml.etree.ElementTree + urllib). No third-party dependencies required.

Output record format (matches email-adjacent schema for pipeline compatibility):
  - id:           <guid> or <link>
  - subject:      <title>
  - from:         feed title + feed URL
  - date_iso:     <pubDate> or <updated> as ISO-8601 UTC string
  - body:         <description> or <content> (HTML tags stripped)
  - source:       "rss"
  - feed_url:     the source feed URL
  - link:         item link for opening in browser

Connector registry entry (config/connectors.yaml):
  rss_feed:
    type: feed
    provider: rss
    enabled: false   # set to true and configure feeds below
    description: "RSS/Atom feed reader — no auth required"
    auth:
      method: none
    fetch:
      handler: "scripts/connectors/rss_feed.py"
      feeds:
        - url: "https://feeds.uscis.gov/updates/uscis-en.xml"
          tag: "uscis"
          domain: "immigration"
        - url: "https://feeds.finance.yahoo.com/news/..."
          tag: "finance_news"
          domain: "finance"
      default_max_results: 25
      timeout_seconds: 10
    output:
      format: jsonl
      source_tag: "rss"

Ref: specs/enhance.md §1.9
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterator
from pathlib import Path

# XML namespace maps
_ATOM_NS = "http://www.w3.org/2005/Atom"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
_DC_NS = "http://purl.org/dc/elements/1.1/"

_TAG_STRIP = re.compile(r"<[^>]+>")
_WHITESPACE_COLLAPSE = re.compile(r"\s{2,}")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not text:
        return ""
    text = _TAG_STRIP.sub(" ", text)
    text = _WHITESPACE_COLLAPSE.sub(" ", text)
    return text.strip()


def _parse_rfc2822_date(date_str: str) -> str | None:
    """Parse RFC 2822 date (used in RSS pubDate) to ISO-8601 UTC string."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str.strip())
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _parse_iso_date(date_str: str) -> str | None:
    """Parse ISO-8601 date (used in Atom <updated>) to UTC ISO-8601 string."""
    if not date_str:
        return None
    date_str = date_str.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:19], fmt[:len(date_str[:19].rsplit("T")[0]) + len("T%H:%M:%S")])
            # Treat as UTC if no tzinfo
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def _fetch_feed_text(url: str, timeout: int = 10) -> str:
    """Fetch a feed URL and return the response body as text."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Artha-RSS/1.0 (personal intelligence OS; https://github.com/ArthaOS/artha)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset("utf-8")
        return resp.read().decode(charset, errors="replace")


def _parse_rss_channel(root: ET.Element, feed_url: str, max_results: int, since_dt: datetime | None) -> list[dict]:
    """Parse RSS 2.0 channel items."""
    items: list[dict] = []
    channel = root.find("channel")
    if channel is None:
        channel = root

    feed_title = (channel.findtext("title") or "").strip()

    for item in channel.findall("item"):
        if len(items) >= max_results:
            break

        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = item.findtext("pubDate") or ""
        guid = item.findtext("guid") or link
        description = item.findtext("description") or ""
        # content:encoded is richer when available
        content_encoded = item.findtext(f"{{{_CONTENT_NS}}}encoded") or ""
        body = _strip_html(content_encoded or description)[:2000]

        date_iso = _parse_rfc2822_date(pub_date_raw)

        # Respect `since` filter
        if since_dt and date_iso:
            try:
                item_dt = datetime.fromisoformat(date_iso)
                if item_dt.tzinfo is None:
                    item_dt = item_dt.replace(tzinfo=timezone.utc)
                if item_dt < since_dt:
                    continue
            except ValueError:
                pass

        items.append({
            "id": guid,
            "subject": title,
            "from": f"{feed_title} <{feed_url}>",
            "date_iso": date_iso or datetime.now(timezone.utc).isoformat(),
            "body": body,
            "source": "rss",
            "feed_url": feed_url,
            "link": link,
        })

    return items


def _parse_atom_feed(root: ET.Element, feed_url: str, max_results: int, since_dt: datetime | None) -> list[dict]:
    """Parse Atom 1.0 feed entries."""
    items: list[dict] = []
    feed_title_el = root.find(f"{{{_ATOM_NS}}}title")
    feed_title = (feed_title_el.text or "").strip() if feed_title_el is not None else ""

    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        if len(items) >= max_results:
            break

        title_el = entry.find(f"{{{_ATOM_NS}}}title")
        title = (title_el.text or "").strip() if title_el is not None else ""

        link_el = entry.find(f"{{{_ATOM_NS}}}link[@rel='alternate']") or entry.find(f"{{{_ATOM_NS}}}link")
        link = link_el.get("href", "") if link_el is not None else ""

        id_el = entry.find(f"{{{_ATOM_NS}}}id")
        guid = (id_el.text or link or "").strip() if id_el is not None else link

        updated_el = entry.find(f"{{{_ATOM_NS}}}updated")
        updated_raw = (updated_el.text or "") if updated_el is not None else ""
        date_iso = _parse_iso_date(updated_raw)

        content_el = entry.find(f"{{{_ATOM_NS}}}content")
        summary_el = entry.find(f"{{{_ATOM_NS}}}summary")
        content_raw = (content_el.text or "") if content_el is not None else ""
        summary_raw = (summary_el.text or "") if summary_el is not None else ""
        body = _strip_html(content_raw or summary_raw)[:2000]

        # `since` filter
        if since_dt and date_iso:
            try:
                item_dt = datetime.fromisoformat(date_iso)
                if item_dt.tzinfo is None:
                    item_dt = item_dt.replace(tzinfo=timezone.utc)
                if item_dt < since_dt:
                    continue
            except ValueError:
                pass

        items.append({
            "id": guid,
            "subject": title,
            "from": f"{feed_title} <{feed_url}>",
            "date_iso": date_iso or datetime.now(timezone.utc).isoformat(),
            "body": body,
            "source": "rss",
            "feed_url": feed_url,
            "link": link,
        })

    return items


def _parse_feed(xml_text: str, feed_url: str, max_results: int, since_dt: datetime | None) -> list[dict]:
    """Detect feed format and parse accordingly."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in feed {feed_url}: {exc}") from exc

    tag = root.tag.lower()
    # Atom: root tag is {namespace}feed or "feed"
    if "atom" in tag or tag.endswith("}feed") or tag == "feed":
        return _parse_atom_feed(root, feed_url, max_results, since_dt)
    # RSS 2.0: root tag is "rss", "rdf:RDF", or similar
    return _parse_rss_channel(root, feed_url, max_results, since_dt)


def _since_to_datetime(since: str) -> datetime | None:
    """Convert a since string to a UTC-aware datetime, or None if unparseable."""
    if not since:
        return None
    # Relative: "7d", "24h", "30m"
    m = re.fullmatch(r"(\d+)([dhm])", since.lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        from datetime import timedelta  # noqa: PLC0415
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
        return datetime.now(timezone.utc) - delta
    # ISO-8601 absolute
    try:
        dt = datetime.fromisoformat(since)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public connector API (matches ConnectorHandler protocol)
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str = "7d",
    max_results: int = 25,
    auth_context: Dict[str, Any] | None = None,
    source_tag: str = "rss",
    feeds: list[dict] | None = None,
    timeout_seconds: int = 10,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield RSS/Atom feed items from configured feed URLs.

    Args:
        since:           Fetch items published after this point (ISO or relative like "7d").
        max_results:     Maximum total items to return across all feeds.
        auth_context:    Not used for RSS (public feeds require no auth).
        source_tag:      Tag to apply as "source" field (default: "rss").
        feeds:           List of feed dicts: {"url": "...", "tag": "...", "domain": "..."}.
                         Reads from auth_context["feeds"] if not provided directly.
        timeout_seconds: HTTP request timeout per feed.
    """
    if feeds is None:
        feeds = (auth_context or {}).get("feeds", [])

    since_dt = _since_to_datetime(since)
    per_feed_limit = max(1, max_results // max(1, len(feeds))) if feeds else max_results
    total_yielded = 0

    for feed_cfg in feeds:
        if total_yielded >= max_results:
            break

        url = feed_cfg.get("url", "").strip()
        if not url:
            continue

        feed_tag = feed_cfg.get("tag", "rss")
        domain_hint = feed_cfg.get("domain", "")

        try:
            xml_text = _fetch_feed_text(url, timeout=timeout_seconds)
            items = _parse_feed(xml_text, url, per_feed_limit, since_dt)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, Exception):
            # Non-blocking: skip failed feed, continue with others
            continue

        for item in items:
            if total_yielded >= max_results:
                break
            record = {**item, "source": source_tag or feed_tag, "domain_hint": domain_hint}
            yield record
            total_yielded += 1


def health_check(auth_context: Dict[str, Any] | None = None) -> bool:
    """Verify that at least one configured feed is reachable.

    Returns True if any configured feed can be fetched, False if all fail.
    When no feeds are configured, returns True (vacuously healthy).
    """
    feeds = (auth_context or {}).get("feeds", [])
    if not feeds:
        return True  # nothing to check

    for feed_cfg in feeds:
        url = feed_cfg.get("url", "").strip()
        if not url:
            continue
        try:
            _fetch_feed_text(url, timeout=5)
            return True  # at least one feed works
        except Exception:
            continue

    return False  # all feeds failed
