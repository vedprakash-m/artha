"""scripts/skills/portal_scanner.py — ATS portal scanner for career-ops.

Scans Greenhouse, Ashby, and Lever job board APIs for new listings matching
the active career campaign. Deduplicates via URL fingerprint and token-set
similarity. Appends new matches to the ## Pipeline section of career_search.md.

Architecture:
  pull()   — load portals config, check 72h TTL per portal, fetch APIs concurrently
  parse()  — filter by archetype title keywords, dedup, build Pipeline entries
  to_dict()— {new_matches, errors, portals_scanned, total_found, filtered, duplicates}

Caching:
  ~/.artha-local/career/scan_ttl.json         — per-portal last-success timestamp
  ~/.artha-local/career/scan_fingerprints.json — dedup: seen URLs + company::role keys

Security:
  - No credentials stored or transmitted
  - All APIs are public read-only Greenhouse/Ashby/Lever endpoints
  - Timeout: 15s per request; concurrent via threading.ThreadPoolExecutor

Ref: specs/career-ops.md §9.2, FR-CS-2
"""
from __future__ import annotations

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

_SKILLS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _SKILLS_DIR.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from .base_skill import BaseSkill
from lib.career_state import is_campaign_active, _read_frontmatter, fingerprint_posting

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_STATE_FILE = _REPO_ROOT / "state" / "career_search.md"
_PORTALS_CONFIG = _REPO_ROOT / "config" / "career_portals.yaml"
_LOCAL_CAREER_DIR = Path.home() / ".artha-local" / "career"
_TTL_FILE = _LOCAL_CAREER_DIR / "scan_ttl.json"
_FINGERPRINTS_FILE = _LOCAL_CAREER_DIR / "scan_fingerprints.json"

_SCAN_TTL_SECONDS = 72 * 3600        # 72h — don't re-scan within this window
_REQUEST_TIMEOUT = 15                 # seconds per HTTP request
_MAX_CONCURRENT_PORTALS = 4
_PIPELINE_SECTION = "## Pipeline"
_USER_AGENT = "Artha-CareerScanner/1.0 (personal; non-commercial)"

# Jaccard threshold for company::role dedup (token-set ratio)
_JACCARD_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# ATS URL detection
# ---------------------------------------------------------------------------

def _detect_ats(careers_url: str) -> tuple[str | None, str | None]:
    """Return (ats_type, api_url) from a company careers URL, or (None, None) if unsupported."""
    url = careers_url.rstrip("/")

    # Greenhouse
    m = re.search(r"boards\.greenhouse\.io/([^/?#]+)", url)
    if m:
        slug = m.group(1)
        return "greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

    # Lever
    m = re.search(r"jobs\.lever\.co/([^/?#]+)", url)
    if m:
        slug = m.group(1)
        return "lever", f"https://api.lever.co/v0/postings/{slug}?mode=json"

    # Ashby
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", url)
    if m:
        slug = m.group(1)
        return "ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    return None, None


# ---------------------------------------------------------------------------
# API parsers (normalize to common schema)
# ---------------------------------------------------------------------------

def _parse_greenhouse(data: list) -> list[dict]:
    """Parse Greenhouse jobs API response to [{title, url, location, company}]."""
    results = []
    for job in data:
        title = job.get("title", "") or ""
        url = job.get("absolute_url", "") or ""
        location = ""
        if isinstance(job.get("location"), dict):
            location = job["location"].get("name", "") or ""
        elif isinstance(job.get("offices"), list) and job["offices"]:
            location = job["offices"][0].get("name", "") or ""
        results.append({"title": title, "url": url, "location": location})
    return results


def _parse_lever(data: list) -> list[dict]:
    """Parse Lever postings API response."""
    results = []
    for job in data:
        title = job.get("text", "") or ""
        url = job.get("hostedUrl", "") or ""
        categories = job.get("categories", {}) or {}
        location = categories.get("location", "") or ""
        results.append({"title": title, "url": url, "location": location})
    return results


def _parse_ashby(data: dict) -> list[dict]:
    """Parse Ashby job board API response."""
    results = []
    for job in data.get("jobPostings", []):
        title = job.get("title", "") or ""
        url = job.get("jobPostingUrls", {}).get("candidateFacingUrl", "") or ""
        location = job.get("locationName", "") or ""
        if not url:
            job_id = job.get("id", "")
            if job_id:
                url = f"https://jobs.ashbyhq.com/unknown/{job_id}"
        results.append({"title": title, "url": url, "location": location})
    return results


def _fetch_portal(portal: dict) -> tuple[str, list[dict], str | None]:
    """Fetch a single portal and return (company, postings, error_or_None)."""
    company = portal.get("company", portal.get("name", "unknown"))
    careers_url = portal.get("careers_url", portal.get("url", ""))
    ats_type, api_url = _detect_ats(careers_url)

    if not ats_type:
        return company, [], f"unsupported ATS for {company}: {careers_url}"

    try:
        req = Request(api_url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        return company, [], f"{company} HTTP {e.code}: {api_url}"
    except URLError as e:
        return company, [], f"{company} fetch failed: {e.reason}"
    except (json.JSONDecodeError, Exception) as e:
        return company, [], f"{company} parse error: {e}"

    if ats_type == "greenhouse":
        jobs_raw = raw.get("jobs", raw) if isinstance(raw, dict) else raw
        postings = _parse_greenhouse(jobs_raw if isinstance(jobs_raw, list) else [])
    elif ats_type == "lever":
        postings = _parse_lever(raw if isinstance(raw, list) else [])
    elif ats_type == "ashby":
        postings = _parse_ashby(raw if isinstance(raw, dict) else {})
    else:
        postings = []

    for p in postings:
        p["company"] = company
        p["ats"] = ats_type

    return company, postings, None


# ---------------------------------------------------------------------------
# Title filter (keyword matching against archetypes)
# ---------------------------------------------------------------------------

def _title_matches(title: str, archetype_keywords: list[list[str]]) -> bool:
    """Return True if title contains at least one keyword from any archetype."""
    title_lower = title.lower()
    for keywords in archetype_keywords:
        if any(kw.lower() in title_lower for kw in keywords):
            return True
    # Also match generic seniority/level indicators for all archetypes
    seniority_hits = ["senior", "staff", "principal", "head of", "director", "lead", "manager"]
    ai_hits = ["ai", "ml", "llm", "machine learning", "platform", "agent"]
    if any(s in title_lower for s in seniority_hits) and any(a in title_lower for a in ai_hits):
        return True
    return False


# ---------------------------------------------------------------------------
# Dedup helpers (Jaccard token-set ratio)
# ---------------------------------------------------------------------------

def _token_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _jaccard(a: str, b: str) -> float:
    sa, sb = _token_set(a), _token_set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _is_duplicate(company: str, title: str, url: str, fingerprints: dict) -> bool:
    """Return True if this posting is already in fingerprints (URL or Jaccard dedup)."""
    # Exact URL match
    if url in fingerprints.get("urls", set()):
        return True

    # Token-set ratio dedup for same company
    company_key = company.lower()
    for seen_key in fingerprints.get("role_keys", {}).get(company_key, []):
        if _jaccard(title, seen_key) >= _JACCARD_THRESHOLD:
            return True

    return False


# ---------------------------------------------------------------------------
# Pipeline entry formatter
# ---------------------------------------------------------------------------

def _format_pipeline_entry(company: str, title: str, url: str, ats: str, discovered: str) -> str:
    """Return structured markdown line + hidden metadata comment for Pipeline section."""
    meta = json.dumps({
        "company": company,
        "role": title,
        "url": url,
        "discovered": discovered,
        "portal": ats,
    }, separators=(",", ":"))
    return (
        f"<!-- PORTAL-MATCH: {meta} -->\n"
        f"- [ ] [{company} — {title}]({url}): {ats.capitalize()} · {discovered[:10]}\n"
    )


# ---------------------------------------------------------------------------
# TTL / fingerprint cache helpers
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _is_ttl_fresh(ttl_data: dict, company: str) -> bool:
    last_str = ttl_data.get(company)
    if not last_str:
        return False
    try:
        last = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - last).total_seconds()
        return age < _SCAN_TTL_SECONDS
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# PortalScanner skill
# ---------------------------------------------------------------------------

class PortalScanner(BaseSkill):
    """Scan ATS portals for new job listings matching the active career campaign."""

    def __init__(self, artha_dir: Path | None = None) -> None:
        super().__init__(name="portal_scanner", priority="P1")
        self._artha_dir = artha_dir or _REPO_ROOT

    @property
    def compare_fields(self) -> list[str]:
        return ["new_matches", "portals_scanned", "total_found", "duplicates"]

    def _load_portals_config(self) -> list[dict]:
        """Load portal list from config/career_portals.yaml (preferred) or career_search.md."""
        import yaml  # noqa: PLC0415

        # Preferred source: config/career_portals.yaml (unencrypted, version-controlled)
        if _PORTALS_CONFIG.exists():
            try:
                cfg = yaml.safe_load(_PORTALS_CONFIG.read_text(encoding="utf-8")) or {}
                return [p for p in cfg.get("companies", []) if p.get("enabled", False)]
            except Exception:
                pass

        # Fallback: career_search.md frontmatter portals section
        if _STATE_FILE.exists():
            try:
                fm = _read_frontmatter(_STATE_FILE)
                portals_raw = fm.get("portals", [])
                # Support both old list-of-{name,url} and new {companies, aggregators} formats
                if isinstance(portals_raw, dict):
                    return [p for p in portals_raw.get("companies", []) if p.get("enabled", False)]
                if isinstance(portals_raw, list):
                    return []  # old format — no ATS detection info; skip
            except Exception:
                pass

        return []

    def _load_archetypes(self) -> list[list[str]]:
        """Load archetype keyword lists from career_search.md or return defaults."""
        import yaml  # noqa: PLC0415

        if _STATE_FILE.exists():
            try:
                fm = _read_frontmatter(_STATE_FILE)
                archetypes = fm.get("archetypes", [])
                return [a.get("keywords", []) for a in archetypes if a.get("keywords")]
            except Exception:
                pass

        # Fallback archetype keywords
        return [
            ["agent", "agentic", "orchestration", "multi-agent", "autonomous"],
            ["AI platform", "LLMOps", "MLOps", "evals", "inference", "observability"],
            ["product manager", "PM", "roadmap", "PRD", "AI PM"],
            ["solutions architect", "technical advisor", "enterprise", "pre-sales"],
            ["forward deployed", "customer engineering", "field", "prototype"],
            ["transformation", "enablement", "adoption", "center of excellence"],
        ]

    # ── BaseSkill interface ───────────────────────────────────────────────

    def pull(self) -> dict[str, Any]:
        """Load portals config, check TTL, fetch APIs concurrently."""
        portals = self._load_portals_config()
        if not portals:
            return {"portals": [], "raw_postings": {}, "errors": [], "ttl_skipped": []}

        ttl_data = _load_json(_TTL_FILE)
        to_scan = []
        ttl_skipped = []

        for portal in portals:
            company = portal.get("company", portal.get("name", "unknown"))
            if _is_ttl_fresh(ttl_data, company):
                ttl_skipped.append(company)
            else:
                to_scan.append(portal)

        raw_postings: dict[str, list[dict]] = {}
        errors: list[str] = []

        if to_scan:
            with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_PORTALS) as pool:
                futures = {pool.submit(_fetch_portal, p): p for p in to_scan}
                for future in as_completed(futures):
                    company, postings, error = future.result()
                    if error:
                        errors.append(error)
                    else:
                        raw_postings[company] = postings
                        # Only advance TTL on success (per rubber-duck review)
                        with _cache_lock:
                            ttl_data[company] = datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            )

            _save_json(_TTL_FILE, ttl_data)

        return {
            "portals": portals,
            "raw_postings": raw_postings,
            "errors": errors,
            "ttl_skipped": ttl_skipped,
        }

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Filter, dedup, and append new matches to career_search.md Pipeline section."""
        raw_postings: dict[str, list[dict]] = raw_data.get("raw_postings", {})
        errors: list[str] = list(raw_data.get("errors", []))
        ttl_skipped: list[str] = raw_data.get("ttl_skipped", [])

        archetype_keywords = self._load_archetypes()
        fingerprints = _load_json(_FINGERPRINTS_FILE)
        if "urls" not in fingerprints:
            fingerprints["urls"] = []
        if "role_keys" not in fingerprints:
            fingerprints["role_keys"] = {}

        url_set: set[str] = set(fingerprints["urls"])
        role_keys: dict[str, list[str]] = fingerprints["role_keys"]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_entries: list[str] = []
        total_found = 0
        filtered_count = 0
        duplicates = 0

        for company, postings in raw_postings.items():
            total_found += len(postings)
            for posting in postings:
                title = posting.get("title", "")
                url = posting.get("url", "")
                ats = posting.get("ats", "unknown")

                if not title or not url:
                    filtered_count += 1
                    continue

                if not _title_matches(title, archetype_keywords):
                    filtered_count += 1
                    continue

                if _is_duplicate(company, title, url, {"urls": url_set, "role_keys": role_keys}):
                    duplicates += 1
                    continue

                # New match — record and format
                url_set.add(url)
                company_key = company.lower()
                role_keys.setdefault(company_key, []).append(title)

                new_entries.append(_format_pipeline_entry(company, title, url, ats, today))

        # Persist updated fingerprints
        fingerprints["urls"] = list(url_set)
        fingerprints["role_keys"] = role_keys
        _save_json(_FINGERPRINTS_FILE, fingerprints)

        # Append new matches to career_search.md ## Pipeline section
        if new_entries and _STATE_FILE.exists():
            self._append_pipeline_entries(new_entries)

        return {
            "new_matches": len(new_entries),
            "errors": errors,
            "portals_scanned": len(raw_postings),
            "ttl_skipped": len(ttl_skipped),
            "total_found": total_found,
            "filtered": filtered_count,
            "duplicates": duplicates,
            "entries": new_entries,
        }

    def _append_pipeline_entries(self, entries: list[str]) -> None:
        """Atomically append new Pipeline entries to career_search.md."""
        try:
            content = _STATE_FILE.read_text(encoding="utf-8")
        except OSError:
            return

        pipeline_header = f"\n{_PIPELINE_SECTION}\n"
        if _PIPELINE_SECTION in content:
            # Append before the next ## section (or at end)
            idx = content.index(_PIPELINE_SECTION)
            insert_at = len(content)
            next_section = content.find("\n## ", idx + len(_PIPELINE_SECTION))
            if next_section != -1:
                insert_at = next_section

            insertion = "\n" + "".join(entries)
            content = content[:insert_at] + insertion + content[insert_at:]
        else:
            # Create Pipeline section at end of file
            content = content.rstrip() + pipeline_header + "\n" + "".join(entries)

        # Atomic write via tmp file
        tmp = _STATE_FILE.with_suffix(".md.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(_STATE_FILE)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_matches": 0,
            "errors": [],
            "portals_scanned": 0,
            "ttl_skipped": 0,
            "total_found": 0,
            "filtered": 0,
            "duplicates": 0,
        }
