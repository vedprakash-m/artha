"""WP-01 / §6.11: background job definitions and installers are retired."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

RETIRED_FILES = [
    "scripts/service/artha-listener.service",
    "scripts/service/artha-listener.xml",
    "scripts/service/com.artha.channel-listener.plist",
    "scripts/register_engine_task.ps1",
    "scripts/scheduled_brief.sh",
]


@pytest.mark.parametrize("rel", RETIRED_FILES)
def test_job_definition_is_gone(rel):
    assert not (REPO / rel).exists(), f"{rel} defines a background job and must be retired"


def test_no_job_installer_text_remains_in_channel_docs():
    text = (REPO / "docs" / "channels.md").read_text(encoding="utf-8")
    for needle in ("launchctl load", "launchctl start", "systemctl --user enable", "systemctl --user start"):
        assert needle not in text, f"docs/channels.md still tells users to install a job: {needle}"
    assert "retired" in text.lower()
