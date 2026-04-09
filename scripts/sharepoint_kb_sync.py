#!/usr/bin/env python3
"""scripts/sharepoint_kb_sync.py — SharePoint / OneDrive → KB sync pipeline.

Pulls documents from SharePoint and OneDrive (via scripts/connectors/msgraph_sharepoint.py),
then ingests each document into the knowledge graph using the standard pipeline:

    fetch()  →  add_episode()  →  DocumentExtractor  →  upsert_entity()  →  write_markdown_stub()

Security: pii_guard.scan() is enforced inside fetch(); by the time a doc reaches
this script it has already passed the PII gate. No double-scanning needed.

Usage:
    python scripts/sharepoint_kb_sync.py [--dry-run] [--verbose]
    python scripts/sharepoint_kb_sync.py --full-sync     # ignore delta links
    python scripts/sharepoint_kb_sync.py --status        # show current stats
    python scripts/sharepoint_kb_sync.py --help

Ref: specs/kb-graph.md §10.7, §10.10–§10.12
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — scripts/  is one level below the Artha root
# ---------------------------------------------------------------------------
_SCRIPTS = Path(__file__).resolve().parent
_ARTHA   = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.knowledge_graph import get_kb          # noqa: E402
from lib.document_extractor import (            # noqa: E402
    DocumentExtractor,
    write_markdown_stub,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CONFIG_FILE   = _ARTHA / "config" / "connectors.yaml"
_TOKEN_FILE    = _ARTHA / ".tokens" / "msgraph-token.json"
_NOTES_DIR     = _ARTHA / "state" / "connectors" / "sharepoint_notes"

# Domain inference keywords (site_name / library_path / filename)
_DOMAIN_KEYWORDS: list[tuple[str, str]] = [
    ("finance",        "finance"),
    ("budget",         "finance"),
    ("legal",          "legal"),
    ("contract",       "legal"),
    ("hr ",            "hr"),
    ("people",         "people"),
    ("engineering",    "engineering"),
    ("dev",            "engineering"),
    ("arch",           "engineering"),
    ("design",         "engineering"),
    ("infra",          "infrastructure"),
    ("deploy",         "deployment"),
    ("ops",            "operations"),
    ("platform",       "platform"),
    ("product",        "product"),
    ("roadmap",        "product"),
    ("strategy",       "strategy"),
    ("executive",      "strategy"),
    ("meeting",        "operations"),
    ("notes",          "operations"),
    ("wiki",           "knowledge"),
    ("knowledge",      "knowledge"),
    ("learn",          "learning"),
    ("training",       "learning"),
]


# ---------------------------------------------------------------------------
# Config / token helpers
# ---------------------------------------------------------------------------

def _load_sp_config() -> dict | None:
    """Load sharepoint_docs connector config.  Returns None if disabled/absent."""
    try:
        import yaml
        with open(_CONFIG_FILE, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        top = raw if "sharepoint_docs" in raw else raw.get("connectors", raw)
        sp  = top.get("sharepoint_docs", {})
        if not sp.get("enabled", False):
            _log.info("sharepoint_docs connector disabled — nothing to do.")
            return None
        return sp
    except FileNotFoundError:
        _log.error("connectors.yaml not found at %s", _CONFIG_FILE)
        return None
    except Exception as exc:
        _log.error("Failed to load connectors.yaml: %s", exc)
        return None


def _load_token() -> str | None:
    """Read the Graph API access token from the token cache."""
    try:
        data = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        token = data.get("access_token")
        if not token:
            _log.error("No access_token found in %s", _TOKEN_FILE)
            return None
        # Warn if expired (non-fatal; requests will fail naturally)
        exp = data.get("expires_at")
        if exp and datetime.fromtimestamp(float(exp), tz=timezone.utc) < datetime.now(timezone.utc):
            _log.warning("Graph token has expired — re-authenticate with setup_msgraph_oauth.py")
        return token
    except FileNotFoundError:
        _log.error(
            "Graph token file not found: %s\n"
            "Run: python scripts/setup_msgraph_oauth.py",
            _TOKEN_FILE,
        )
        return None
    except Exception as exc:
        _log.error("Failed to load token: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------

def _infer_domain(doc: dict) -> str:
    """Infer the Artha domain from site/library/filename metadata."""
    haystack = " ".join([
        doc.get("site_name", ""),
        doc.get("library_path", ""),
        doc.get("name", ""),
    ]).lower()
    for kw, domain in _DOMAIN_KEYWORDS:
        if kw in haystack:
            return domain
    return "knowledge"


# ---------------------------------------------------------------------------
# Delta-link reset (--full-sync)
# ---------------------------------------------------------------------------

def _clear_delta_links() -> None:
    """Remove persisted delta links so the next run does a full scan."""
    try:
        import yaml
        from connectors.msgraph_sharepoint import _SP_STATE_FILE

        if not _SP_STATE_FILE.exists():
            return
        with open(_SP_STATE_FILE, encoding="utf-8") as fh:
            state = yaml.safe_load(fh) or {}
        state.pop("delta_links", None)
        state.pop("shared_with_me", None)
        tmp = _SP_STATE_FILE.with_suffix(".yaml.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(state, fh, default_flow_style=False, allow_unicode=True)
        tmp.replace(_SP_STATE_FILE)
        _log.info("Delta links cleared — next run will perform a full scan.")
    except Exception as exc:
        _log.warning("Could not clear delta links: %s", exc)


# ---------------------------------------------------------------------------
# Main sync routine
# ---------------------------------------------------------------------------

def sync(
    *,
    dry_run: bool = False,
    verbose: bool = False,
    artha_dir: Path | None = None,
) -> dict:
    """Run the full SharePoint → KB sync pipeline. Returns stats dict."""
    stats: dict = {
        "docs_fetched":    0,
        "docs_skipped":    0,
        "docs_ingested":   0,
        "entities_added":  0,
        "episodes_created": 0,
        "stubs_written":   0,
        "errors":          [],
    }

    sp_cfg = _load_sp_config()
    if sp_cfg is None:
        return stats

    token = _load_token()
    if not token:
        return stats

    fetch_cfg   = sp_cfg.get("fetch", {})
    auth_ctx    = {"access_token": token}

    from connectors.msgraph_sharepoint import fetch as sp_fetch, health_check

    # Health-check before heavy work
    if not health_check(auth_ctx, sp_cfg):
        _log.error("SharePoint connector health_check failed — aborting sync.")
        stats["errors"].append("health_check failed")
        return stats

    kg     = get_kb(artha_dir or _ARTHA)
    root   = artha_dir or _ARTHA
    notes  = _NOTES_DIR
    notes.mkdir(parents=True, exist_ok=True)

    for doc in sp_fetch(auth_ctx, fetch_cfg):
        stats["docs_fetched"] += 1

        name         = doc.get("name", "untitled")
        content_text = doc.get("content_text", "")
        content_hash = doc.get("content_hash", hashlib.sha256(content_text.encode()).hexdigest())
        web_url      = doc.get("web_url", "")
        domain       = _infer_domain(doc)
        drive_key    = doc.get("drive_item_id", doc["id"])

        # Stable episode key for idempotency
        episode_key  = f"sharepoint:{drive_key}"

        if verbose:
            _log.info(
                "  [%s] %s (%s chars, domain=%s)",
                doc.get("source", "sharepoint"), name, len(content_text), domain,
            )

        if dry_run:
            # Dry-run: extract but don't write
            try:
                extractor = DocumentExtractor.from_text(
                    content_text,
                    source_ref=web_url or f"sharepoint:{name}",
                    domain=domain,
                    episode_key=episode_key,
                )
                extractor.run()
                stats["docs_ingested"]  += 1
                stats["entities_added"] += len(extractor.entities)
            except Exception as exc:
                _log.warning("DRY-RUN extract error for '%s': %s", name, exc)
                stats["errors"].append(f"[dry-run] {name}: {exc}")
            continue

        try:
            # Step 1: add_episode (idempotent on episode_key)
            ep_id = kg.add_episode(
                episode_key=episode_key,
                source_type=doc.get("source", "sharepoint"),
                raw_content=content_text[:2000],
            )
            stats["episodes_created"] += 1

            # Step 2: extract entities from document text
            extractor = DocumentExtractor.from_text(
                content_text,
                source_ref=web_url or f"sharepoint:{name}",
                domain=domain,
                episode_key=episode_key,
            )
            extractor.run()

            # Step 3: upsert entities (source_episode_id links them to this episode)
            for entity in extractor.entities:
                entity.setdefault("source_type", "sharepoint")
                kg.upsert_entity(
                    entity,
                    source=web_url or f"sharepoint:{name}",
                    confidence=float(entity.get("confidence", 0.65)),
                    source_episode_id=ep_id,
                )
                stats["entities_added"] += 1

            # Step 4: write markdown stub for KB bootstrap pick-up
            safe_stem = _safe_stem(name)
            hash8     = content_hash[:8]
            stub_path = notes / f"{safe_stem}-{hash8}.md"

            write_markdown_stub(
                stub_path,
                title=name,
                source_ref=web_url or f"sharepoint:{name}",
                source_type=doc.get("source", "sharepoint"),
                content_hash=content_hash,
                domain=domain,
                extracted_text=content_text,
                entities=[e.get("name", "") for e in extractor.entities],
                script_name="sharepoint_kb_sync.py",
            )
            stats["stubs_written"] += 1
            stats["docs_ingested"] += 1

        except Exception as exc:
            _log.error("Failed to ingest '%s': %s", name, exc)
            stats["errors"].append(f"{name}: {exc}")
            if verbose:
                import traceback
                traceback.print_exc()

    if not dry_run:
        try:
            kg.close()
        except Exception:
            pass

    return stats


def _safe_stem(filename: str) -> str:
    """Convert a filename to a filesystem-safe stem."""
    import re
    stem = Path(filename).stem
    stem = re.sub(r"[^A-Za-z0-9_\-]", "_", stem)
    return stem[:60].strip("_") or "sharepoint_doc"


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def show_status(artha_dir: Path | None = None) -> None:
    """Print current SharePoint notes stats."""
    try:
        notes = list(_NOTES_DIR.glob("*.md")) if _NOTES_DIR.exists() else []
        print(f"SharePoint notes stubs:  {len(notes)}")
        print(f"Notes directory:         {_NOTES_DIR}")

        from connectors.msgraph_sharepoint import _SP_STATE_FILE, _load_state
        state = _load_state()
        ingested = len(state.get("ingested_items", {}))
        cursor   = state.get("shared_with_me", {}).get("last_seen_shared_at", "(none)")
        deltas   = len(state.get("delta_links", {}))
        last_run = state.get("last_run_at", "(never)")
        print(f"Connector state:")
        print(f"  ingested items:  {ingested}")
        print(f"  delta links:     {deltas}")
        print(f"  sharedWithMe cursor: {cursor}")
        print(f"  last run:        {last_run}")
    except Exception as exc:
        print(f"Could not read SharePoint state: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Artha SharePoint → KB sync pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dry-run",    action="store_true", help="Fetch and parse but do not write to KB")
    p.add_argument("--full-sync",  action="store_true", help="Clear delta links and re-sync all documents")
    p.add_argument("--status",     action="store_true", help="Show current sync state and exit")
    p.add_argument("--verbose","-v", action="store_true", help="Enable INFO logging")
    return p


def main() -> None:
    p    = _build_parser()
    args = p.parse_args()

    level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)-8s %(message)s")

    if args.status:
        show_status()
        return

    if args.full_sync:
        _clear_delta_links()

    stats = sync(dry_run=args.dry_run, verbose=args.verbose)

    prefix = "DRY-RUN " if args.dry_run else ""
    print(f"\nSharePoint KB sync {prefix}complete:")
    for k, v in stats.items():
        if k == "errors":
            continue
        print(f"  {k}: {v}")
    if stats.get("errors"):
        print(f"\n  Errors ({len(stats['errors'])}):")
        for e in stats["errors"][:20]:
            print(f"    - {e}")
    if stats["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
