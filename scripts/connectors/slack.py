# pii-guard: standard — fetches Slack message content; PII confined to yielded dicts
"""
scripts/connectors/slack.py — Read-only Slack message ingestion connector.

Fetches recent messages from configured Slack channels using the Web API.
Returns one record per message, suitable for the Artha pipeline.

Authentication:
  Bot Token (xoxb-…) stored in OS keyring under key "artha-slack-bot-token".
  Required OAuth scopes: channels:history, channels:read, groups:history,
  groups:read, im:history, mpim:history, users:read.

Rate limiting:
  conversations.history is Tier 3 (50 req/min). This connector enforces a
  conservative 1.2s floor between channel fetches to stay well under the limit.
  On HTTP 429 the Retry-After header is respected (up to _MAX_RETRY_SLEEP_SEC).

Pagination:
  Uses cursor-based pagination (next_cursor / response_metadata.next_cursor).
  Fetches up to _PAGE_LIMIT messages per page; stops when since_dt is reached.

Output record schema:
  {
    "id":          str   — "slack_{channel_id}_{ts_epoch_ms}",
    "title":       str   — "{channel_name}: {first_80_chars_of_text}",
    "body":        str   — plain-text message body (mrkdwn stripped),
    "author":      str   — display_name of the sender,
    "ts":          str   — ISO 8601 UTC timestamp,
    "url":         str   — deep-link to message in Slack (requires workspace slug),
    "source":      str   — source_tag parameter (default "slack"),
    "channel":     str   — channel name (e.g. "#general"),
    "thread_ts":   str   — thread parent ts, or "" for top-level messages,
    "reactions":   list  — [{"name": str, "count": int}, …],
  }

Ref: specs/connect.md §7.1 (Slack connector), §8 (ConnectorHandler protocol)
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator

# Ensure Artha root is on sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

log = logging.getLogger(__name__)

# Slack Web API base URL
_API_BASE = "https://slack.com/api"

# Maximum items fetched per pagination page (Slack max is 1000)
_PAGE_LIMIT = 200

# Conservative sleep between channel requests to respect Tier 3 rate limit
_INTER_CHANNEL_SLEEP = 1.2  # seconds

# Maximum seconds to sleep on a 429 Retry-After header
_MAX_RETRY_SLEEP_SEC = 60

# Maximum retry attempts on transient errors
_MAX_RETRIES = 3

# Module-level user display-name cache (avoids repeated users.info calls)
_USER_CACHE: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slack_get(
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """Make a Slack Web API GET request. Returns parsed response dict.

    Raises:
        RuntimeError: on HTTP error or Slack API error after retries.
    """
    qs_parts = []
    for k, v in (params or {}).items():
        qs_parts.append(f"{k}={urllib.request.quote(str(v), safe='')}")
    qs = ("?" + "&".join(qs_parts)) if qs_parts else ""
    url = f"{_API_BASE}/{method}{qs}"

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )

    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if not body.get("ok"):
                err = body.get("error", "unknown_error")
                # auth errors are not retryable
                if err in ("invalid_auth", "not_authed", "account_inactive", "token_revoked"):
                    raise RuntimeError(f"Slack API [{method}] auth error: {err}")
                raise RuntimeError(f"Slack API [{method}] error: {err}")
            return body
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = int(exc.headers.get("Retry-After", max(delay, 5)))
                wait = min(retry_after, _MAX_RETRY_SLEEP_SEC)
                log.warning("[slack] %s rate-limited (429) — sleeping %ds", method, wait)
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    time.sleep(wait)
                    delay = min(delay * 2, 30.0)
                    continue
            raise RuntimeError(f"Slack API [{method}] HTTP {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = min(delay, 30.0)
                log.warning("[slack] %s attempt %d/%d failed: %s — retry in %.0fs",
                            method, attempt + 1, _MAX_RETRIES, exc, wait)
                time.sleep(wait)
                delay = min(delay * 2, 30.0)
                continue
            raise RuntimeError(
                f"Slack API [{method}] failed after {_MAX_RETRIES + 1} attempts: {last_exc}"
            ) from last_exc

    raise RuntimeError(f"Slack API [{method}] failed: {last_exc}")  # unreachable guard


def _resolve_channels(
    token: str,
    channel_filter: list[str] | None,
) -> dict[str, str]:
    """Return {channel_id: channel_name} for channels the bot can read.

    Args:
        token:          Bot token.
        channel_filter: If provided, only return channels whose names are in this list.
                        Names may be prefixed with "#" (stripped automatically).
    """
    # Normalise filter list: strip "#" prefix, lowercase
    if channel_filter:
        wanted = {c.lstrip("#").lower() for c in channel_filter if c}
    else:
        wanted = set()

    results: dict[str, str] = {}
    cursor: str = ""

    while True:
        params: dict[str, Any] = {
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = _slack_get(token, "conversations.list", params)
        except RuntimeError as exc:
            log.error("[slack] conversations.list failed: %s", exc)
            break

        for ch in resp.get("channels", []):
            name = ch.get("name", "")
            cid = ch.get("id", "")
            if not name or not cid:
                continue
            if wanted and name.lower() not in wanted:
                continue
            results[cid] = name

        meta = resp.get("response_metadata", {})
        cursor = meta.get("next_cursor", "")
        if not cursor:
            break

    return results


def _fetch_channel_history(
    token: str,
    channel_id: str,
    oldest: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch messages from a channel, oldest-first, up to *limit* messages.

    Uses cursor pagination. Stops fetching when all remaining messages are
    older than *oldest* (Unix timestamp string) or limit is reached.

    Returns messages in oldest-first order.
    """
    all_messages: list[dict[str, Any]] = []
    cursor: str = ""

    while len(all_messages) < limit:
        params: dict[str, Any] = {
            "channel": channel_id,
            "limit": min(_PAGE_LIMIT, limit - len(all_messages)),
            "oldest": oldest,
            "inclusive": "false",
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = _slack_get(token, "conversations.history", params)
        except RuntimeError as exc:
            log.warning("[slack] conversations.history(%s) failed: %s", channel_id, exc)
            break

        messages = resp.get("messages", [])
        if not messages:
            break

        all_messages.extend(messages)

        # Pagination
        meta = resp.get("response_metadata", {})
        cursor = meta.get("next_cursor", "")
        has_more = resp.get("has_more", False)
        if not cursor or not has_more:
            break

        # Throttle between page requests
        time.sleep(0.5)

    # Slack returns newest-first — reverse to oldest-first for consistent output
    all_messages.reverse()
    return all_messages


def _resolve_user(token: str, user_id: str) -> str:
    """Return display_name for a user ID. Falls back to user_id on error.

    Results are cached in _USER_CACHE to avoid repeated API calls.
    """
    if not user_id or user_id.startswith("B"):
        # Bot IDs start with "B" — skip lookup
        return user_id

    if user_id in _USER_CACHE:
        return _USER_CACHE[user_id]

    try:
        resp = _slack_get(token, "users.info", {"user": user_id})
        profile = resp.get("user", {}).get("profile", {})
        name = (
            profile.get("display_name")
            or profile.get("real_name")
            or user_id
        )
    except RuntimeError:
        name = user_id

    _USER_CACHE[user_id] = name
    return name


def _mrkdwn_to_plain(text: str) -> str:
    """Convert Slack mrkdwn markup to approximate plain text.

    Handles the most common constructs; does not use a full parser.
    """
    if not text:
        return text
    # <@USERID> → @user  /  <@USERID|displayname> → @displayname
    text = re.sub(r"<@([A-Z0-9]+)\|([^>]+)>", r"@\2", text)
    text = re.sub(r"<@([A-Z0-9]+)>", r"@\1", text)
    # <!channel>, <!here>, <!everyone> → @channel etc.
    text = re.sub(r"<!(\w+)>", r"@\1", text)
    # <#CXXXXXX|channel-name> → #channel-name
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    # <URL|label> → label  /  <URL> → URL
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<([^>]+)>", r"\1", text)
    # Bold: *text* → text
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # Italic: _text_ → text
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Strikethrough: ~text~ → text
    text = re.sub(r"~([^~]+)~", r"\1", text)
    # Inline code: `text` → text
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


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


def _load_token(auth_context: dict[str, Any] | None, credential_key: str) -> str | None:
    """Load Slack bot token from keyring or auth_context."""
    # 1. auth_context override (useful for testing)
    if auth_context and auth_context.get("token"):
        return str(auth_context["token"])

    # 2. OS keyring
    try:
        import keyring  # type: ignore[import]
        token = keyring.get_password("artha", credential_key)
        if token:
            return token
    except Exception:
        pass

    # 3. Environment variable fallback (CI / test)
    import os
    return os.environ.get("ARTHA_SLACK_BOT_TOKEN") or None


# ---------------------------------------------------------------------------
# Public connector API (matches ConnectorHandler protocol)
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str = "24h",
    max_results: int = 100,
    auth_context: Dict[str, Any] | None = None,
    source_tag: str = "slack",
    channels: list[str] | None = None,
    credential_key: str = "artha-slack-bot-token",
    workspace_slug: str = "",
    include_replies: bool = False,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield recent Slack messages from configured channels.

    Args:
        since:          Fetch messages after this point. ISO-8601 or relative
                        format ("24h", "7d", "30m"). Default: "24h".
        max_results:    Maximum total messages to return across all channels.
        auth_context:   Optional dict with "token" key for testing/override.
                        May also contain "channels" and "workspace_slug".
        source_tag:     Tag applied as "source" field (default: "slack").
        channels:       List of channel names to fetch (e.g. ["#general"]).
                        Reads from auth_context["channels"] if not provided.
        credential_key: Keyring key for the bot token.
        workspace_slug: Workspace slug for deep-link URLs (e.g. "mycompany").
                        Reads from auth_context["workspace_slug"] if not provided.
        include_replies: Whether to include threaded reply messages (default False).
    """
    ctx = auth_context or {}

    token = _load_token(ctx, credential_key)
    if not token:
        log.error(
            "[slack] no bot token found (keyring key: %s, env: ARTHA_SLACK_BOT_TOKEN)",
            credential_key,
        )
        return

    if channels is None:
        channels = ctx.get("channels") or []
    if not workspace_slug:
        workspace_slug = ctx.get("workspace_slug", "")

    # Convert since string to Unix timestamp string for Slack API
    since_dt = _since_to_datetime(since)
    oldest_ts: str = "0"
    if since_dt is not None:
        oldest_ts = str(since_dt.timestamp())

    # Resolve channel IDs
    channel_map = _resolve_channels(token, channels if channels else None)
    if not channel_map:
        log.warning("[slack] no accessible channels found (check bot scopes)")
        return

    per_channel_limit = max(1, max_results // max(1, len(channel_map)))
    total_yielded = 0

    for channel_id, channel_name in channel_map.items():
        if total_yielded >= max_results:
            break

        try:
            messages = _fetch_channel_history(
                token, channel_id, oldest_ts, per_channel_limit,
            )
        except Exception as exc:
            log.warning("[slack] skipping #%s due to error: %s", channel_name, exc)
            continue

        for msg in messages:
            if total_yielded >= max_results:
                break

            # Skip sub-type messages (joins, leaves, bot messages without text)
            sub_type = msg.get("subtype", "")
            if sub_type in ("channel_join", "channel_leave", "channel_archive"):
                continue

            # Optionally skip threaded replies (thread_ts != ts means it's a reply)
            thread_ts = msg.get("thread_ts", "")
            msg_ts = msg.get("ts", "")
            if not include_replies and thread_ts and thread_ts != msg_ts:
                continue

            text = _mrkdwn_to_plain(msg.get("text", ""))
            user_id = msg.get("user", msg.get("bot_id", ""))
            display_name = _resolve_user(token, user_id) if user_id else "unknown"

            # Parse timestamp: Slack ts is "unixseconds.microseconds"
            try:
                ts_float = float(msg_ts)
                ts_iso = datetime.fromtimestamp(ts_float, tz=timezone.utc).isoformat()
                ts_ms = int(ts_float * 1000)
            except (ValueError, TypeError):
                ts_iso = ""
                ts_ms = 0

            # Deep-link URL (requires workspace slug; omit if not configured)
            if workspace_slug and msg_ts:
                ts_clean = msg_ts.replace(".", "")
                url = (
                    f"https://{workspace_slug}.slack.com/archives/"
                    f"{channel_id}/p{ts_clean}"
                )
            else:
                url = ""

            reactions = [
                {"name": r.get("name", ""), "count": r.get("count", 0)}
                for r in msg.get("reactions", [])
            ]

            record: dict[str, Any] = {
                "id": f"slack_{channel_id}_{ts_ms}",
                "title": f"#{channel_name}: {text[:80]}",
                "body": text,
                "author": display_name,
                "ts": ts_iso,
                "url": url,
                "source": source_tag,
                "channel": f"#{channel_name}",
                "thread_ts": thread_ts if thread_ts != msg_ts else "",
                "reactions": reactions,
            }
            yield record
            total_yielded += 1

        # Polite pause between channel fetches
        if total_yielded < max_results and len(channel_map) > 1:
            time.sleep(_INTER_CHANNEL_SLEEP)


def health_check(auth_context: Dict[str, Any] | None = None) -> bool:
    """Verify Slack bot token is valid via auth.test.

    Returns True if authentication succeeds, False otherwise (never raises).
    """
    ctx = auth_context or {}
    token = _load_token(ctx, ctx.get("credential_key", "artha-slack-bot-token"))
    if not token:
        log.warning("[slack] health_check: no token configured")
        return False

    try:
        resp = _slack_get(token, "auth.test", timeout=10)
        team = resp.get("team", "unknown")
        user = resp.get("user", "unknown")
        log.info("[slack] health_check OK — team: %s, bot: %s", team, user)
        return True
    except Exception as exc:
        log.warning("[slack] health_check failed: %s", exc)
        return False
