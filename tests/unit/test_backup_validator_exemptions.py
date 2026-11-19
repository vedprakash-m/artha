"""The GFS validator must accept the machine audit log and learned procedures (no YAML frontmatter)."""
from __future__ import annotations

import pytest

import backup

BODY = b"word " * 40


def test_audit_log_and_learned_procedures_do_not_need_frontmatter():
    backup._validate_content("state_encrypted", "state/audit.md.age", b"[2026-11-18] ACTION | ok\n" * 40)
    backup._validate_content("state_plain", "state/learned_procedures/p.md", b"# Procedure\n" + BODY)


def test_other_markdown_still_requires_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        backup._validate_content("state_plain", "state/goals.md", BODY)
    with pytest.raises(ValueError, match="frontmatter"):
        backup._validate_content("state_encrypted", "state/kids.md.age", BODY)


def test_markdown_with_frontmatter_passes():
    backup._validate_content("state_plain", "state/goals.md", b"---\ndomain: goals\n---\n" + BODY)
