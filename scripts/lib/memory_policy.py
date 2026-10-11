"""
scripts/lib/memory_policy.py — automatic personal-fact extraction policy (WP-03)
================================================================================
re-artha RA-06 / ADR-006: no automatic extraction of personal facts from
Briefings or transcripts until a deliberate comparison against manual
curation passes.  Observed 2026-10-11: the live post-Catch-up step saved facts
from a Briefing, and an unreadable config *enabled* it.

Rules enforced here, in code, not in instructions:

* The default is **off**.
* Only a literal ``True`` at ``harness.memory_extraction.allow_automatic``
  enables it.  The legacy ``harness.agentic.post_catchup_memory.enabled`` key is
  ignored on purpose — an old explicit ``enabled: true`` must not carry over.
* Any error reading or interpreting configuration means **off**.

Every call site that persists extracted facts consults :func:`extraction_allowed`;
the config file is git-ignored, so the policy must live in tracked code.
"""
from __future__ import annotations

from typing import Any

OPT_IN_PATH = ("harness", "memory_extraction", "allow_automatic")


def extraction_allowed(config: dict[str, Any] | None = None) -> bool:
    """True only when memory extraction was deliberately and explicitly enabled."""
    try:
        if config is None:
            from lib.config_loader import load_config  # noqa: PLC0415
            config = load_config("artha_config")
        node: Any = config
        for key in OPT_IN_PATH:
            if not isinstance(node, dict):
                return False
            node = node.get(key)
        return node is True
    except Exception:  # noqa: BLE001 — a config failure must never enable extraction
        return False
