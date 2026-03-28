"""
tests/work/test_work_products.py — Unit tests for Product Knowledge domain (FW-18).

Tests: domain writers, reader commands, meeting context injection,
       state schema validation, bootstrap questions.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# Ensure scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    """Create a temporary work state directory with products/ subdir."""
    d = tmp_path / "state" / "work"
    d.mkdir(parents=True)
    (d / "products").mkdir()
    return d


@pytest.fixture()
def sample_products() -> list[dict[str, Any]]:
    return [
        {
            "name": "xStore",
            "slug": "xstore",
            "layer": "data-plane",
            "status": "active",
            "team": "xStore Core",
            "active_projects": "XPF, Sustainability",
            "summary": "[Organization] blob/table/queue data plane engine.",
        },
        {
            "name": "Direct Drive",
            "slug": "direct-drive",
            "layer": "data-plane",
            "status": "active",
            "team": "DD Engineering",
            "active_projects": "DD-XPF",
            "summary": "Managed disk data plane for Ultra/Premium disks.",
        },
        {
            "name": "PilotFish",
            "slug": "pilotfish",
            "layer": "control-plane",
            "status": "active",
            "team": "Platform Fleet",
            "active_projects": "XPF",
            "summary": "Next-gen control plane replacing Fabric.",
        },
    ]


@pytest.fixture()
def sample_product_deep() -> dict[str, Any]:
    return {
        "name": "xStore",
        "slug": "xstore",
        "layer": "data-plane",
        "team": "xStore Core",
        "summary": "[Organization] blob/table/queue data plane engine.",
        "components": [
            {"name": "BlobFE", "purpose": "Blob frontend", "owner": "Team A", "status": "active"},
        ],
        "dependencies": [
            {"name": "PilotFish", "type": "control-plane", "direction": "upstream", "notes": "XPF migration"},
        ],
        "stakeholders": [
            {"role": "PM Lead", "person": "Alice", "context": "Program management"},
        ],
        "data_sources": [],
        "metrics": [
            {"name": "Fleet Size", "value": "90 clusters", "source": "GQ-001", "updated": "2026-03-28"},
        ],
        "projects": [
            {"name": "XPF", "relationship": "Control plane migration", "status": "active"},
        ],
    }


# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------

class TestWriteProductsIndex:
    """Tests for write_products_index()."""

    def test_writes_valid_frontmatter(self, work_dir: Path, sample_products: list):
        from work_domain_writers import write_products_index
        dest = work_dir / "work-products.md"
        write_products_index(sample_products, dest)

        assert dest.exists()
        text = dest.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "domain: work-products" in text
        assert "schema_version: '1.0'" in text or 'schema_version: "1.0"' in text
        assert "product_count: 3" in text

    def test_writes_all_products(self, work_dir: Path, sample_products: list):
        from work_domain_writers import write_products_index
        dest = work_dir / "work-products.md"
        write_products_index(sample_products, dest)

        text = dest.read_text(encoding="utf-8")
        assert "## xStore" in text
        assert "## Direct Drive" in text
        assert "## PilotFish" in text

    def test_writes_taxonomy_tree(self, work_dir: Path, sample_products: list):
        from work_domain_writers import write_products_index
        dest = work_dir / "work-products.md"
        write_products_index(sample_products, dest)

        text = dest.read_text(encoding="utf-8")
        assert "data-plane" in text
        assert "control-plane" in text

    def test_atomic_write_no_partial(self, work_dir: Path, sample_products: list):
        from work_domain_writers import write_products_index
        dest = work_dir / "work-products.md"
        write_products_index(sample_products, dest)
        assert not (dest.with_suffix(".md.tmp")).exists()

    def test_empty_products_skips(self, work_dir: Path):
        from work_domain_writers import write_products_index
        dest = work_dir / "work-products.md"
        write_products_index([], dest)
        assert not dest.exists()

    def test_layer_summary_in_frontmatter(self, work_dir: Path, sample_products: list):
        from work_domain_writers import write_products_index
        dest = work_dir / "work-products.md"
        write_products_index(sample_products, dest)

        text = dest.read_text(encoding="utf-8")
        assert "data-plane" in text
        # 2 data-plane, 1 control-plane
        assert "layer_summary" in text

    def test_slug_and_deep_file_pointer(self, work_dir: Path, sample_products: list):
        from work_domain_writers import write_products_index
        dest = work_dir / "work-products.md"
        write_products_index(sample_products, dest)

        text = dest.read_text(encoding="utf-8")
        assert "- Slug: xstore" in text
        assert "- Deep File: products/xstore.md" in text


class TestWriteProductDeep:
    """Tests for write_product_deep()."""

    def test_creates_deep_file(self, work_dir: Path, sample_product_deep: dict):
        from work_domain_writers import write_product_deep
        dest_dir = work_dir / "products"
        write_product_deep(sample_product_deep, dest_dir)

        deep_path = dest_dir / "xstore.md"
        assert deep_path.exists()

    def test_deep_file_frontmatter(self, work_dir: Path, sample_product_deep: dict):
        from work_domain_writers import write_product_deep
        dest_dir = work_dir / "products"
        write_product_deep(sample_product_deep, dest_dir)

        text = (dest_dir / "xstore.md").read_text(encoding="utf-8")
        assert "domain: work-product-deep" in text
        assert "product: xStore" in text
        assert "slug: xstore" in text

    def test_deep_file_sections(self, work_dir: Path, sample_product_deep: dict):
        from work_domain_writers import write_product_deep
        dest_dir = work_dir / "products"
        write_product_deep(sample_product_deep, dest_dir)

        text = (dest_dir / "xstore.md").read_text(encoding="utf-8")
        assert "## Architecture Overview" in text
        assert "## Components" in text
        assert "## Dependencies" in text
        assert "## Team & Stakeholders" in text
        assert "## Key Metrics" in text
        assert "## Knowledge Log" in text

    def test_deep_file_components(self, work_dir: Path, sample_product_deep: dict):
        from work_domain_writers import write_product_deep
        dest_dir = work_dir / "products"
        write_product_deep(sample_product_deep, dest_dir)

        text = (dest_dir / "xstore.md").read_text(encoding="utf-8")
        assert "BlobFE" in text
        assert "Blob frontend" in text

    def test_deep_file_atomic(self, work_dir: Path, sample_product_deep: dict):
        from work_domain_writers import write_product_deep
        dest_dir = work_dir / "products"
        write_product_deep(sample_product_deep, dest_dir)
        assert not (dest_dir / "xstore.md.tmp").exists()

    def test_empty_product_creates_stub(self, work_dir: Path):
        from work_domain_writers import write_product_deep
        dest_dir = work_dir / "products"
        write_product_deep({"name": "TestProduct", "slug": "test-product"}, dest_dir)

        text = (dest_dir / "test-product.md").read_text(encoding="utf-8")
        assert "# TestProduct" in text
        assert "## Knowledge Log" in text


# ---------------------------------------------------------------------------
# Reader command tests
# ---------------------------------------------------------------------------

class TestCmdProducts:
    """Tests for cmd_products() reader command."""

    def test_empty_index_shows_create_hint(self, work_dir: Path, monkeypatch):
        import work.discovery as disc
        monkeypatch.setattr(disc, "_WORK_STATE_DIR", work_dir)
        result = disc.cmd_products()
        assert "No product knowledge index" in result
        assert "/work products add" in result

    def test_lists_products_from_index(self, work_dir: Path, monkeypatch, sample_products: list):
        from work_domain_writers import write_products_index
        import work.discovery as disc

        write_products_index(sample_products, work_dir / "work-products.md")
        monkeypatch.setattr(disc, "_WORK_STATE_DIR", work_dir)

        result = disc.cmd_products()
        assert "xStore" in result
        assert "Direct Drive" in result
        assert "PilotFish" in result
        assert "Products tracked: 3" in result

    def test_query_loads_deep_file(self, work_dir: Path, monkeypatch, sample_product_deep: dict):
        from work_domain_writers import write_products_index, write_product_deep
        import work.discovery as disc

        write_products_index([{
            "name": "xStore", "slug": "xstore", "layer": "data-plane",
            "status": "active", "team": "xStore Core",
            "active_projects": "XPF", "summary": "Data plane engine.",
        }], work_dir / "work-products.md")
        write_product_deep(sample_product_deep, work_dir / "products")
        monkeypatch.setattr(disc, "_WORK_STATE_DIR", work_dir)
        monkeypatch.setattr(disc, "_PRODUCTS_DIR", work_dir / "products")

        result = disc.cmd_products(query="xstore")
        assert "Deep Knowledge" in result
        assert "Architecture Overview" in result

    def test_query_not_found(self, work_dir: Path, monkeypatch, sample_products: list):
        from work_domain_writers import write_products_index
        import work.discovery as disc

        write_products_index(sample_products, work_dir / "work-products.md")
        monkeypatch.setattr(disc, "_WORK_STATE_DIR", work_dir)
        monkeypatch.setattr(disc, "_PRODUCTS_DIR", work_dir / "products")

        result = disc.cmd_products(query="nonexistent")
        assert "No deep file found" in result


class TestCmdProductsAdd:
    """Tests for cmd_products_add() write command."""

    def test_creates_index_and_deep_file(self, work_dir: Path, monkeypatch):
        import work.discovery as disc
        monkeypatch.setattr(disc, "_WORK_STATE_DIR", work_dir)
        monkeypatch.setattr(disc, "_PRODUCTS_DIR", work_dir / "products")

        result = disc.cmd_products_add("Blob Storage", layer="offering", team="Storage Services")
        assert "PRODUCT REGISTERED" in result
        assert "blob-storage" in result

        # Index was created
        index = work_dir / "work-products.md"
        assert index.exists()
        text = index.read_text(encoding="utf-8")
        assert "## Blob Storage" in text
        assert "- Slug: blob-storage" in text

        # Deep file was created
        deep = work_dir / "products" / "blob-storage.md"
        assert deep.exists()

    def test_rejects_empty_name(self):
        import work.discovery as disc
        result = disc.cmd_products_add("")
        assert "Usage" in result

    def test_rejects_duplicate(self, work_dir: Path, monkeypatch):
        import work.discovery as disc
        monkeypatch.setattr(disc, "_WORK_STATE_DIR", work_dir)
        monkeypatch.setattr(disc, "_PRODUCTS_DIR", work_dir / "products")

        disc.cmd_products_add("TestProd")
        result = disc.cmd_products_add("TestProd")
        assert "already exists" in result


# ---------------------------------------------------------------------------
# Meeting context injection tests
# ---------------------------------------------------------------------------

class TestProductMeetingContext:
    """Tests for _product_meeting_context()."""

    def test_no_index_returns_empty(self, work_dir: Path, monkeypatch):
        from work.meetings import _product_meeting_context
        import work.meetings as meet
        monkeypatch.setattr(meet, "_WORK_STATE_DIR", work_dir)
        assert _product_meeting_context("xStore sync") == []

    def test_matches_product_name(self, work_dir: Path, monkeypatch, sample_products: list):
        from work_domain_writers import write_products_index
        from work.meetings import _product_meeting_context
        import work.meetings as meet

        write_products_index(sample_products, work_dir / "work-products.md")
        monkeypatch.setattr(meet, "_WORK_STATE_DIR", work_dir)

        result = _product_meeting_context("xStore Architecture Review")
        assert len(result) > 0
        assert "xStore" in result[0]

    def test_no_match_returns_empty(self, work_dir: Path, monkeypatch, sample_products: list):
        from work_domain_writers import write_products_index
        from work.meetings import _product_meeting_context
        import work.meetings as meet

        write_products_index(sample_products, work_dir / "work-products.md")
        monkeypatch.setattr(meet, "_WORK_STATE_DIR", work_dir)

        result = _product_meeting_context("Team All-Hands Standup")
        assert result == []

    def test_caps_at_4_lines(self, work_dir: Path, monkeypatch, sample_products: list):
        from work_domain_writers import write_products_index
        from work.meetings import _product_meeting_context
        import work.meetings as meet

        write_products_index(sample_products, work_dir / "work-products.md")
        monkeypatch.setattr(meet, "_WORK_STATE_DIR", work_dir)

        result = _product_meeting_context("Direct Drive Status Update")
        assert len(result) <= 4


# ---------------------------------------------------------------------------
# Briefing schema validation tests
# ---------------------------------------------------------------------------

class TestBriefingSchema:
    """Tests that work-products is in the state schema."""

    def test_products_in_state_schema(self):
        from work.briefing import _WORK_STATE_SCHEMA
        assert "work-products" in _WORK_STATE_SCHEMA
        assert "domain" in _WORK_STATE_SCHEMA["work-products"]
        assert "last_updated" in _WORK_STATE_SCHEMA["work-products"]


# ---------------------------------------------------------------------------
# Bootstrap questions tests
# ---------------------------------------------------------------------------

class TestBootstrapQuestions:
    """Tests that product bootstrap questions are registered."""

    def test_product_questions_present(self):
        from work_bootstrap import QUESTIONS
        ids = [q["id"] for q in QUESTIONS]
        assert "products" in ids
        assert "product_layer" in ids

    def test_product_questions_optional(self):
        from work_bootstrap import QUESTIONS
        for q in QUESTIONS:
            if q["id"] in ("products", "product_layer"):
                assert q.get("optional", False) is True, f"{q['id']} should be optional"
