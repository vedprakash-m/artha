"""
scripts/skills/whatsapp_last_contact.py — WhatsApp last-contact enrichment (U-9.6)

Reads the WhatsApp ChatStorage.sqlite database from disk (macOS only) and:
  1. Refreshes "Last WA" dates in state/contacts.md for every circle member
  2. Computes per-person message frequency (90-day velocity) as a warmth proxy
  3. Surfaces contacts overdue per circle cadence as nudges
  4. Detects birthday-wishable dates (birthday wishes I sent → infer contact DOB)

The skill is read-only — it NEVER writes to contacts.md directly.
It returns structured data which the pipeline/catch-up logic uses to:
  - Update the Last WA column via state_writer (if enabled)
  - Produce relationship nudges in the briefing

macOS path:
  ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite

The DB is copied to /tmp/wa_chat_snap.sqlite before querying to avoid WAL locking.

Skills registry entry (config/skills.yaml):
  whatsapp_last_contact:
    enabled: true
    priority: P1
    cadence: every_run
    requires_vault: false
    description: "Refresh Last WA dates and relationship warmth from local WhatsApp DB (macOS)"

Ref: specs/util.md §U-9.6
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from .base_skill import BaseSkill

logger = logging.getLogger(__name__)

# Apple CoreData epoch offset (seconds between 1970-01-01 and 2001-01-01)
_APPLE_EPOCH_OFFSET = 978307200

# WhatsApp DB location on macOS
_WA_DB_SOURCE = Path.home() / "Library" / "Group Containers" / \
    "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite"

# Working copy to avoid locking the live DB
_WA_DB_COPY = Path("/tmp/wa_chat_snap.sqlite")

# contacts.md location relative to artha root
_CONTACTS_FILE = "state/contacts.md"

# Cadence → stale threshold in days (mirrors relationship_pulse)
_CADENCE_DAYS: dict[str, int] = {
    "daily_passive": 0,
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 90,
    "as_needed": 0,
}

# Window for "active in last N days" velocity metric
_VELOCITY_WINDOW_DAYS = 90


def _ts_to_date(ts: float | None) -> date | None:
    """Convert Apple CoreData timestamp to a Python date."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts + _APPLE_EPOCH_OFFSET, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


def _normalize_phone(jid: str) -> str:
    """Strip WhatsApp JID suffixes to get a bare phone number string."""
    return jid.replace("@s.whatsapp.net", "").replace("@lid", "").strip()


class WhatsAppLastContact(BaseSkill):
    """
    Skill: WhatsApp Last Contact Enrichment.

    Returns a dict keyed by contact name with last-WA date, message counts,
    and stale-contact nudges — all sourced from the local ChatStorage.sqlite.
    """

    def __init__(self, artha_dir: Path | None = None):
        super().__init__(name="whatsapp_last_contact", priority="P1")
        self.artha_dir = artha_dir or Path.cwd()

    # ------------------------------------------------------------------
    # BaseSkill interface
    # ------------------------------------------------------------------

    def pull(self) -> sqlite3.Connection:
        """Copy the live WA DB to /tmp and return a connection."""
        if not _WA_DB_SOURCE.exists():
            raise FileNotFoundError(
                f"WhatsApp DB not found at {_WA_DB_SOURCE}. "
                "Skill is macOS-only and requires WhatsApp to be installed."
            )
        shutil.copy2(_WA_DB_SOURCE, _WA_DB_COPY)
        logger.info("WhatsApp DB copied to %s (%d bytes)", _WA_DB_COPY, _WA_DB_COPY.stat().st_size)
        return sqlite3.connect(str(_WA_DB_COPY))

    def parse(self, raw_data: sqlite3.Connection) -> dict[str, Any]:
        """Query the copied DB and build enrichment data."""
        con = raw_data
        try:
            return self._build_enrichment(con)
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _build_enrichment(self, con: sqlite3.Connection) -> dict[str, Any]:
        cur = con.cursor()
        today = date.today()
        velocity_cutoff = today - timedelta(days=_VELOCITY_WINDOW_DAYS)
        velocity_ts = (velocity_cutoff.toordinal() - date(2001, 1, 1).toordinal()) * 86400

        # ── 1. All individual chats with last-WA date and message counts ──
        cur.execute("""
            SELECT
                s.ZPARTNERNAME,
                REPLACE(REPLACE(s.ZCONTACTJID,'@s.whatsapp.net',''),'@lid','') AS phone,
                s.ZLASTMESSAGEDATE,
                COUNT(m.Z_PK) AS total_msgs,
                SUM(CASE WHEN m.ZMESSAGEDATE > ? THEN 1 ELSE 0 END) AS msgs_90d
            FROM ZWACHATSESSION s
            LEFT JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
            WHERE s.ZSESSIONTYPE = 0
            GROUP BY s.Z_PK
            ORDER BY s.ZLASTMESSAGEDATE DESC
        """, (velocity_ts,))

        by_phone: dict[str, dict] = {}
        by_name: dict[str, dict] = {}
        for name, phone, last_ts, total, msgs_90d in cur.fetchall():
            last = _ts_to_date(last_ts)
            entry = {
                "name": name,
                "phone": phone,
                "last_wa": last.isoformat() if last else None,
                "total_msgs": total or 0,
                "msgs_90d": msgs_90d or 0,
                "days_since": (today - last).days if last else None,
            }
            if phone:
                by_phone[phone] = entry
            if name:
                by_name[name] = entry

        # ── 2. Load circle definitions and member roster from contacts.md ──
        circles = self._load_circles()
        contact_phone_map = self._build_phone_index()

        # ── 3. Match and annotate circles ──
        nudges: list[dict] = []
        enriched: dict[str, dict] = {}

        for circle_key, circle in circles.items():
            cadence = circle.get("cadence", "monthly")
            threshold = _CADENCE_DAYS.get(cadence, 30)
            if threshold == 0:
                continue  # no nudge for this cadence

            for member_name in circle.get("members", []):
                # Try name-match first, then phone-match via contacts index
                entry = by_name.get(member_name)
                if entry is None:
                    phone = contact_phone_map.get(member_name)
                    if phone:
                        entry = by_phone.get(phone)

                if entry is None:
                    enriched[member_name] = {
                        "circle": circle_key,
                        "last_wa": None,
                        "found_in_wa": False,
                    }
                    continue

                days = entry.get("days_since")
                is_stale = days is not None and days > threshold

                enriched[member_name] = {
                    "circle": circle_key,
                    "last_wa": entry["last_wa"],
                    "total_msgs": entry["total_msgs"],
                    "msgs_90d": entry["msgs_90d"],
                    "days_since": days,
                    "is_stale": is_stale,
                    "found_in_wa": True,
                }

                if is_stale:
                    nudges.append({
                        "contact": member_name,
                        "circle": circle_key,
                        "last_wa": entry["last_wa"],
                        "days_since": days,
                        "cadence_threshold": threshold,
                        "msgs_90d": entry["msgs_90d"],
                        "phone": entry["phone"],
                    })

        # ── 4. Birthday inference: birthday wishes I sent → contact DOB ──
        inferred_dobs = self._infer_dobs_from_wishes(cur)

        # ── 5. Active group memberships ──
        groups = self._get_active_groups(cur, today)

        nudges.sort(key=lambda n: n["days_since"] or 0, reverse=True)

        return {
            "enriched_contacts": enriched,
            "nudges": nudges,
            "nudge_count": len(nudges),
            "inferred_dobs": inferred_dobs,
            "active_groups": groups,
            "total_chats": len(by_name),
            "db_path": str(_WA_DB_COPY),
            "snapshot_date": today.isoformat(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_circles(self) -> dict[str, Any]:
        """Load circle definitions from contacts.md YAML frontmatter."""
        path = self.artha_dir / _CONTACTS_FILE
        if not path.exists():
            return {}
        content = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not m:
            return {}
        try:
            data = yaml.safe_load(m.group(1))
            return data.get("circles", {}) if isinstance(data, dict) else {}
        except yaml.YAMLError:
            return {}

    def _build_phone_index(self) -> dict[str, str]:
        """
        Build name → bare phone mapping from contacts.md table rows.
        Looks for rows like: | Name | ... | +1 (NNN) NNN-NNNN | ...
        Returns dict[name] = digits-only phone.
        """
        path = self.artha_dir / _CONTACTS_FILE
        if not path.exists():
            return {}
        content = path.read_text(encoding="utf-8")
        index: dict[str, str] = {}
        phone_re = re.compile(r'[+\d][\d\s\-().]{7,}')
        for line in content.splitlines():
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) < 3:
                continue
            name = parts[0].strip()
            if not name or name.startswith("-") or name.lower() in {"name", ""}:
                continue
            for part in parts[1:]:
                if phone_re.match(part):
                    digits = re.sub(r'\D', '', part)
                    if len(digits) >= 10:
                        index[name] = digits
                        break
        return index

    def _infer_dobs_from_wishes(self, cur: sqlite3.Cursor) -> list[dict]:
        """
        Find messages I sent containing 'happy birthday' etc.
        The message date → probable birthday month-day for that contact.
        """
        cur.execute("""
            SELECT s.ZPARTNERNAME, m.ZMESSAGEDATE
            FROM ZWACHATSESSION s JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
            WHERE s.ZSESSIONTYPE = 0 AND m.ZISFROMME = 1 AND m.ZTEXT IS NOT NULL
              AND (lower(m.ZTEXT) LIKE '%happy birthday%'
                   OR lower(m.ZTEXT) LIKE '%janamdin%'
                   OR lower(m.ZTEXT) LIKE '%janmdin%')
            ORDER BY s.ZPARTNERNAME, m.ZMESSAGEDATE
        """)
        seen: dict[tuple, date] = {}
        for name, ts in cur.fetchall():
            d = _ts_to_date(ts)
            if d:
                key = (name, d.month, d.day)
                if key not in seen:
                    seen[key] = d

        return [
            {"name": name, "probable_dob_month": m, "probable_dob_day": day,
             "observed_date": dt.isoformat()}
            for (name, m, day), dt in seen.items()
        ]

    def _get_active_groups(self, cur: sqlite3.Cursor, today: date) -> list[dict]:
        """Return all groups active in the last 90 days with resolved member names."""
        cutoff_ts = (today - timedelta(days=90) - date(2001, 1, 1)).days * 86400
        cur.execute("""
            SELECT s.ZPARTNERNAME, s.ZCONTACTJID,
                s.ZLASTMESSAGEDATE,
                (SELECT COUNT(*) FROM ZWAGROUPMEMBER gm WHERE gm.ZCHATSESSION = s.Z_PK) AS member_count
            FROM ZWACHATSESSION s
            WHERE s.ZSESSIONTYPE = 1 AND s.ZLASTMESSAGEDATE > ?
            ORDER BY s.ZLASTMESSAGEDATE DESC
        """, (cutoff_ts,))
        groups = []
        for name, jid, last_ts, cnt in cur.fetchall():
            last = _ts_to_date(last_ts)
            # Resolve member names for small (<30) groups
            members: list[str] = []
            if cnt and cnt < 30:
                cur.execute("""
                    SELECT gm.ZMEMBERJID,
                        (SELECT ZPARTNERNAME FROM ZWACHATSESSION
                         WHERE ZCONTACTJID = gm.ZMEMBERJID AND ZSESSIONTYPE=0 LIMIT 1) as resolved
                    FROM ZWAGROUPMEMBER gm
                    JOIN ZWACHATSESSION gs ON gm.ZCHATSESSION = gs.Z_PK
                    WHERE gs.ZCONTACTJID = ?
                """, (jid,))
                for _, resolved in cur.fetchall():
                    if resolved and resolved not in ("‎You", "You"):
                        members.append(resolved)
            groups.append({
                "name": name,
                "jid": jid,
                "last_active": last.isoformat() if last else None,
                "member_count": cnt,
                "known_members": members,
            })
        return groups

    # ------------------------------------------------------------------
    # BaseSkill required abstract implementations
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        result = self.execute()
        data = result.get("data", {})
        nudges = data.get("nudges", [])
        return {
            "summary": (
                f"{len(nudges)} contact(s) overdue for a message"
                if nudges else "All tracked contacts within cadence"
            ),
            "nudge_count": len(nudges),
            "snapshot_date": data.get("snapshot_date"),
            "status": result.get("status"),
        }

    @property
    def compare_fields(self) -> list:
        return ["nudge_count", "snapshot_date"]

    # ------------------------------------------------------------------
    # Convenience: format for briefing output
    # ------------------------------------------------------------------

    def format_briefing_block(self, data: dict) -> str:
        """Format skill output as a markdown block for catch-up briefings."""
        lines = ["### 📱 WhatsApp Contact Status\n"]
        nudges = data.get("nudges", [])
        if not nudges:
            lines.append("✅ All tracked contacts within cadence.\n")
        else:
            lines.append(f"**{len(nudges)} contact(s) overdue for a message:**\n")
            for n in nudges[:12]:  # cap at 12 in briefing
                name = n["contact"]
                circle = n["circle"].replace("_", " ")
                days = n["days_since"]
                last = n["last_wa"] or "unknown"
                phone = n.get("phone", "")
                lines.append(
                    f"- **{name}** ({circle}) — {days}d since last WA "
                    f"(last: {last})"
                    + (f" 📞 +{phone}" if phone else "")
                )

        dobs = data.get("inferred_dobs", [])
        if dobs:
            lines.append(f"\n**Inferred birthdays from WA history ({len(dobs)} contacts):**")
            for d in dobs[:8]:
                lines.append(
                    f"- {d['name']}: ~{d['probable_dob_month']:02d}-{d['probable_dob_day']:02d} "
                    f"(observed {d['observed_date']})"
                )

        snap = data.get("snapshot_date", "?")
        total = data.get("total_chats", 0)
        lines.append(f"\n_Source: ChatStorage.sqlite snapshot {snap} · {total} chats scanned_")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Factory function for skill_runner.py
# ------------------------------------------------------------------

def get_skill(artha_dir: Path) -> WhatsAppLastContact:
    """Return a configured WhatsAppLastContact skill instance."""
    return WhatsAppLastContact(artha_dir=artha_dir)
