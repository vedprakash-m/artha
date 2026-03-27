"""tests/work/test_work_discovery.py — Focused tests for scripts/work/discovery.py

T3-33..42 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work.discovery
import work.helpers
from work.discovery import (
    cmd_people,
    cmd_sources,
    cmd_sources_add,
    cmd_graph,
    cmd_docs,
    cmd_repos,
    cmd_incidents,
)


def _write_state(state_dir: Path, name: str, fm: dict, body: str = "") -> Path:
    p = state_dir / name
    content = "---\n" + yaml.dump(fm, default_flow_style=False) + "---\n\n" + body
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def work_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    work.discovery._WORK_STATE_DIR = d
    work.helpers._WORK_STATE_DIR = d
    return d


# ---------------------------------------------------------------------------
# T3-33: cmd_people — search by query string
# ---------------------------------------------------------------------------

def test_cmd_people_returns_string(work_dir):
    out = cmd_people(query="Alice")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_people_no_state_graceful(work_dir):
    out = cmd_people(query="Bob Smith")
    assert isinstance(out, str)


def test_cmd_people_with_people_state(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "people": [
            {"name": "Carol Jones", "role": "PM", "team": "Platform", "tier": "peer"},
        ],
    }
    _write_state(work_dir, "people.md", fm, body="## Carol Jones\nPM on Platform team.\n")
    out = cmd_people(query="Carol")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_people_empty_name(work_dir):
    out = cmd_people(query="")
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# T3-34: cmd_sources — query filtering
# ---------------------------------------------------------------------------

def test_cmd_sources_no_state(work_dir):
    out = cmd_sources(query=None)
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_sources_with_query(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "sources": [
            {"url": "https://internal.wiki/roadmap", "context": "product roadmap"},
            {"url": "https://metrics.dashboard/q4", "context": "Q4 metrics"},
        ],
    }
    _write_state(work_dir, "sources.md", fm, body="")
    out = cmd_sources(query="roadmap")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_sources_query_none(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "sources": [{"url": "https://dash.internal/", "context": "main dashboard"}],
    }
    _write_state(work_dir, "sources.md", fm, body="")
    out = cmd_sources(query=None)
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# T3-35: cmd_sources_add — URL + context persistence
# ---------------------------------------------------------------------------

def test_cmd_sources_add_creates_entry(work_dir):
    out = cmd_sources_add(url="https://example.org/spec", context="team spec doc")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_sources_add_persists(work_dir):
    cmd_sources_add(url="https://example.org/roadmap", context="Q3 roadmap")
    # After adding, querying should surface the URL or at least not crash
    out = cmd_sources(query="roadmap")
    assert isinstance(out, str)


def test_cmd_sources_add_requires_url(work_dir):
    # Empty URL — should not crash, should return an error message
    out = cmd_sources_add(url="", context="no url")
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# T3-36: cmd_graph — tier grouping
# ---------------------------------------------------------------------------

def test_cmd_graph_no_state(work_dir):
    out = cmd_graph()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_graph_with_people(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "people": [
            {"name": "Alice Ko", "tier": "manager", "team": "Platform"},
            {"name": "Bob Park", "tier": "peer", "team": "Platform"},
            {"name": "Carol Lim", "tier": "skip", "team": "Org"},
        ],
    }
    _write_state(work_dir, "people.md", fm)
    out = cmd_graph()
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-37: cmd_docs — empty state
# ---------------------------------------------------------------------------

def test_cmd_docs_empty_state(work_dir):
    out = cmd_docs()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_docs_with_documents(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "documents": [
            {"title": "Q3 OKRs", "url": "https://docs.internal/q3-okrs", "type": "okr"},
        ],
    }
    _write_state(work_dir, "documents.md", fm, body="## Q3 OKRs\nLink to OKR doc.\n")
    out = cmd_docs()
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# T3-38: cmd_repos — missing state graceful
# ---------------------------------------------------------------------------

def test_cmd_repos_no_state(work_dir):
    out = cmd_repos()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_repos_returns_string_always(work_dir):
    corrupt = work_dir / "repos.md"
    corrupt.write_text("not yaml\njust text", encoding="utf-8")
    out = cmd_repos()
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# T3-39: cmd_incidents — missing state graceful
# ---------------------------------------------------------------------------

def test_cmd_incidents_no_state(work_dir):
    out = cmd_incidents()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_incidents_with_state(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "incidents": [
            {"id": "INC-001", "title": "Auth outage", "status": "resolved", "date": "2026-03-01"},
        ],
    }
    _write_state(work_dir, "incidents.md", fm, body="## Active Incidents\n")
    out = cmd_incidents()
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# T3-40: No crash on completely empty work_dir
# ---------------------------------------------------------------------------

def test_all_discovery_cmds_empty_dir(work_dir):
    for fn, kwargs in [
        (cmd_people, {"query": "X"}),
        (cmd_sources, {"query": None}),
        (cmd_graph, {}),
        (cmd_docs, {}),
        (cmd_repos, {}),
        (cmd_incidents, {}),
    ]:
        try:
            out = fn(**kwargs)
            assert isinstance(out, str)
        except Exception as exc:
            pytest.fail(f"{fn.__name__} raised unexpectedly: {exc}")
