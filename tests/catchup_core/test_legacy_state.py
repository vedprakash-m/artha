from __future__ import annotations

from datetime import datetime, timezone

from catchup_core.contracts import CoverageStatus
from catchup_core.incumbent import GuardedIncumbent
from catchup_core.legacy_state import LegacyStateAdapter

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def write(d, name, domain, updated):
    (d / f"{name}.md").write_text(f"---\ndomain: {domain}\nlast_updated: '{updated}'\n---\n# {name}\nbody\n")


def test_reports_only_non_pilot_curated_domains_with_labels(tmp_path):
    write(tmp_path, "finance", "finance", "2026-10-01")
    write(tmp_path, "kids", "kids_school", "2026-10-10")
    write(tmp_path, "home", "home", "2026-07-01")
    write(tmp_path, "audit", "audit", "2026-10-11")
    out = LegacyStateAdapter(tmp_path, ["kids_school"], NOW).run([], budget_seconds=5)
    byd = {c.domain: c for c in out.conclusions}
    assert set(byd) == {"finance", "home"}
    assert "from your records, dated 2026-10-01" in byd["finance"].text
    assert "103 days old" in byd["home"].text
    assert all(c.provenance == "legacy_state" and len(c.state_file_version) == 16 for c in out.conclusions)


def test_version_changes_with_content(tmp_path):
    write(tmp_path, "finance", "finance", "2026-10-01")
    v1 = LegacyStateAdapter(tmp_path, [], NOW).run([], budget_seconds=5).conclusions[0].state_file_version
    (tmp_path / "finance.md").write_text("---\ndomain: finance\nlast_updated: '2026-10-02'\n---\nchanged\n")
    v2 = LegacyStateAdapter(tmp_path, [], NOW).run([], budget_seconds=5).conclusions[0].state_file_version
    assert v1 != v2


def test_encrypted_domains_are_declared_not_read(tmp_path):
    (tmp_path / "immigration.md.age").write_bytes(b"x")
    (tmp_path / "finance.md.age").write_bytes(b"x")
    write(tmp_path, "finance", "finance", "2026-10-01")          # plaintext copy exists: not "locked"
    out = LegacyStateAdapter(tmp_path, [], NOW).run([], budget_seconds=5)
    entry = out.coverage[0]
    assert entry.status is CoverageStatus.NOTIFIED_NOT_FETCHED and entry.blind_spots == ("immigration",)
    assert not entry.required


def test_missing_dir_and_odd_files(tmp_path):
    assert LegacyStateAdapter(tmp_path / "nope", [], NOW).run([], budget_seconds=1).coverage[0].status \
        is CoverageStatus.UNAVAILABLE
    (tmp_path / "weird.md").write_text("no frontmatter")
    (tmp_path / "undated.md").write_text("---\ndomain: undated\n---\n")
    out = LegacyStateAdapter(tmp_path, [], NOW).run([], budget_seconds=1)
    texts = {c.domain: c.text for c in out.conclusions}
    assert "undated" in texts["undated"] and "update date unknown" in texts["undated"]
    assert "weird" in texts


def test_works_behind_the_guard(tmp_path):
    write(tmp_path, "finance", "finance", "2026-10-01")
    out = GuardedIncumbent(LegacyStateAdapter(tmp_path, [], NOW)).run([])
    assert out.conclusions and not out.failures
