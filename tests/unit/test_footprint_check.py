"""WP-01 / §6.12: Artha owns no background footprint (scheduler entry, daemon, listener)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from preflight.footprint_checks import check_background_footprint  # noqa: E402


class _Runner:
    """Scripted command runner: maps the first two argv tokens to stdout."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        for key, out in self.outputs.items():
            if list(argv[: len(key)]) == list(key):
                return 0, out
        return 1, ""


def test_clean_mac_passes(tmp_path):
    runner = _Runner({("launchctl", "list"): "PID\tStatus\tLabel\n-\t0\tcom.apple.thing\n",
                      ("crontab", "-l"): "", ("lsof",): ""})
    res = check_background_footprint(runner=runner, platform="darwin", home=tmp_path)
    assert res.passed, res.message


def test_loaded_launchd_job_is_a_footprint(tmp_path):
    runner = _Runner({("launchctl", "list"): "-\t0\tartha.hermes-context\n"})
    res = check_background_footprint(runner=runner, platform="darwin", home=tmp_path)
    assert not res.passed and "artha.hermes-context" in res.message


def test_vault_watchdog_is_the_documented_exception(tmp_path):
    runner = _Runner({("launchctl", "list"): "-\t0\tcom.artha.vault-watchdog\n"})
    res = check_background_footprint(runner=runner, platform="darwin", home=tmp_path)
    assert res.passed
    assert "watchdog" in res.message.lower()


def test_installed_but_unloaded_definition_is_reported(tmp_path):
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "artha.hermes-context.plist").write_text("<plist/>")
    (agents / "com.apple.other.plist").write_text("<plist/>")
    runner = _Runner({("launchctl", "list"): ""})
    res = check_background_footprint(runner=runner, platform="darwin", home=tmp_path)
    assert not res.passed
    assert "artha.hermes-context.plist" in res.message
    assert "com.apple.other" not in res.message


def test_cron_entry_is_a_footprint(tmp_path):
    runner = _Runner({("launchctl", "list"): "", ("crontab", "-l"): "*/5 * * * * python3 /x/artha/scripts/nudge_daemon.py\n"})
    res = check_background_footprint(runner=runner, platform="darwin", home=tmp_path)
    assert not res.passed and "cron" in res.message.lower()


def test_listening_artha_process_is_a_footprint(tmp_path):
    lsof = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\npython3 123 ved 5u IPv4 0 0t0 TCP 127.0.0.1:8765 (LISTEN)\n"
    ps = "123 python3 /Users/x/Artha/scripts/channel_listener.py\n"
    runner = _Runner({("launchctl", "list"): "", ("lsof",): lsof, ("ps",): ps})
    res = check_background_footprint(runner=runner, platform="darwin", home=tmp_path)
    assert not res.passed and "channel_listener" in res.message


def test_linux_timer_detected(tmp_path):
    out = "NEXT LEFT LAST PASSED UNIT ACTIVATES\nMon n/a n/a n/a artha-channel.timer artha-channel.service\n"
    runner = _Runner({("systemctl",): out})
    res = check_background_footprint(runner=runner, platform="linux", home=tmp_path)
    assert not res.passed and "artha-channel.timer" in res.message


def test_windows_task_detected_watchdog_allowed(tmp_path):
    out = "TaskName: \\ArthaChannelListener\nStatus: Ready\n\nTaskName: \\ArthaVaultWatchdog\nStatus: Ready\n"
    runner = _Runner({("schtasks",): out})
    res = check_background_footprint(runner=runner, platform="win32", home=tmp_path)
    assert not res.passed
    assert "ArthaChannelListener" in res.message and "ArthaVaultWatchdog" not in res.message.split("exception")[0]


def test_inventory_tool_failure_is_reported_not_passed(tmp_path):
    def broken(argv):
        raise OSError("launchctl missing")
    res = check_background_footprint(runner=broken, platform="darwin", home=tmp_path)
    assert not res.passed and "could not" in res.message.lower()
