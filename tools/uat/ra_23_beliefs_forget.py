"""RA-23 UAT: beliefs view, forget receipts and third-party data.

Commands, in order (``--shadow --json`` on every CLI command):

  beliefs                                   read         nothing written
  beliefs --domain kids_school              read         nothing written
  beliefs --subject <subject>               read         nothing written
  why <fact>                                read         nothing written
  export <tmp>/before.ae1                   local-write  one encrypted snapshot in a temporary directory, deleted
  (scratch) add a Fact about adult1, a consent-required person with no grant
  beliefs --subject adult1 / beliefs        scratch-only read: that person's Fact is not shown
  forget --connector C --scope S --upstream U
                                            scratch-only deletes the Signal, its Facts and Alerts; keeps a tombstone
  beliefs / why <forgotten fact>            scratch-only the Fact is gone and cannot be explained
  (library) re-insert the forgotten Signal  library      refused by the tombstone (anti-resurrection)
  (library) replay privacy history onto the pre-forget snapshot
                                            library      the forgotten Signal and Fact do not come back on recovery

Live (no ``--scratch``): read steps run; the export runs only with ``--allow-local-writes`` and is written to a
temporary directory that is deleted; forget and the seeded third-party checks never run live.  Nothing is sent.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

FORGET = ("school_email", "personal", "m2")
LIMITATIONS = (
    "Restore-time anti-resurrection is shown with the library replay (backup.apply_privacy_history); the CLI "
    "restore path refuses legacy snapshots, and a governed checkpoint/quarantine restore needs the live host.",
    "Third-party consent is shown with a synthetic consent-required person; a consented household member's own "
    "view needs a second real person (RA-19 household UAT).",
)


def setup(runner: h.ScratchRunner) -> None:
    runner.seed(h.seed_school_fact)
    ids = runner.seed(lambda d: h.seed_school_fact(d, upstream="m2", text="Field trip form due 2026-10-20",
                                                   value="2026-10-20", obligation="trip"))
    runner.forget_fact = ids["fact_id"]


def third_party(deps) -> None:
    """A Fact about a consent-required person who has granted nothing."""
    from catchup_core.ledger import FactInput, SignalInput  # noqa: PLC0415
    ref = deps.blobs.put("mail_m3", "Adult checkup booked 2026-11-02")
    sid, _ = deps.ledger.upsert_signal(SignalInput("school_email", "personal", "m3", "v1", h.NOW.isoformat(),
                                                   "h_m3", ref))
    deps.ledger.set_signal_subjects(sid, ["adult1"])
    deps.ledger.put_fact(FactInput(domain="health", predicate="checkup_date", value="2026-11-02",
                                   primary_subject_id="adult1", observed_at=h.NOW.isoformat(),
                                   evidence=((sid, 0, 10),)))


def scenario(runner, report: h.Report) -> None:
    fact = h.first_fact(runner, report, shadow=True)
    if fact is None:
        return
    h.beliefs(runner, report, expected="every row states status, provenance and observation time", shadow=True,
              check=lambda rows: all(r["status"] and r["provenance"] and r["observed_at"] for r in rows))
    domain, subject = fact["domain"], fact["subject"]
    h.beliefs(runner, report, "--domain", domain, expected=f"only {domain} rows", shadow=True,
              check=lambda rows: bool(rows) and all(r["domain"] == domain for r in rows))
    h.beliefs(runner, report, "--subject", subject, expected=f"only rows about {subject}", shadow=True,
              check=lambda rows: bool(rows) and all(r["subject"] == subject for r in rows))
    h.run_step(runner, report, ["why", fact["fact_id"], h.SHADOW], "why quotes evidence or states provenance",
               lambda o: o.ok and (o.data["evidence"] or o.data["provenance"]))
    if runner.live:
        _live_export(runner, report)
        return
    target = runner.forget_fact
    snapshot = runner.base / "before.ae1"
    h.run_step(runner, report, ["export", str(snapshot), h.SHADOW], "snapshot taken before forgetting",
               lambda o: o.ok and snapshot.exists(), kind=h.LOCAL_WRITE)
    runner.seed(third_party)
    h.run_step(runner, report, ["beliefs", "--subject", "adult1", h.SHADOW],
               "a consent-required person without a grant shows nothing (or is refused)",
               lambda o: (o.ok and o.data["beliefs"] == []) or not o.ok, kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["beliefs", h.SHADOW], "no third-party row leaks into the unfiltered view",
               lambda o: o.ok and all(r["subject"] != "adult1" for r in o.data["beliefs"]), kind=h.SCRATCH_ONLY)
    connector, scope, upstream = FORGET
    h.run_step(runner, report, ["forget", "--connector", connector, "--scope", scope, "--upstream", upstream,
                                h.SHADOW],
               "receipt counts deleted Signal, Fact and Alert and names the minimal audit kept",
               lambda o: o.ok and o.data["deleted_signals"] == 1 and o.data["deleted_facts"] >= 1
               and o.data["deleted_alerts"] >= 1 and "tombstone" in o.data["minimal_audit"]
               and o.data["encrypted_backups_pending_expiry"] is True, kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["beliefs", h.SHADOW], "the forgotten Fact is no longer shown",
               lambda o: o.ok and all(r["fact_id"] != target for r in o.data["beliefs"]), kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["why", target, h.SHADOW], "the forgotten Fact cannot be explained",
               lambda o: h.refused(o, "unknown fact"), kind=h.SCRATCH_ONLY)
    runner.seed(lambda deps: _anti_resurrection(deps, report, snapshot))


def _anti_resurrection(deps, report: h.Report, snapshot: Path) -> None:
    from catchup_core.backup import apply_privacy_history, read_snapshot  # noqa: PLC0415
    from catchup_core.ledger import SignalInput, Suppressed  # noqa: PLC0415
    connector, scope, upstream = FORGET
    led = deps.ledger
    try:
        led.upsert_signal(SignalInput(connector, scope, upstream, "v2", h.NOW.isoformat(), "h_new", "x"))
        observed = "re-inserted"
    except Suppressed as exc:
        observed = f"Suppressed: {exc}"
    report.check("(library) ledger.upsert_signal(forgotten upstream, new version)",
                 "a forgotten upstream object cannot be re-inserted", observed,
                 observed.startswith("Suppressed") and led.is_tombstoned(connector, scope, upstream), kind=h.LIBRARY)
    contents = read_snapshot(snapshot, led._cipher)
    with closing(sqlite3.connect(":memory:")) as replay:
        replay.deserialize(contents["ledger.db"])
        query = "SELECT count(*) FROM signals WHERE upstream_id=?"
        before = replay.execute(query, (upstream,)).fetchone()[0]
        removed: list[str] = []
        apply_privacy_history(replay, removed.append, led._sealed.read(), led.privacy_history())
        after = replay.execute(query, (upstream,)).fetchone()[0]
    report.check("(library) backup.apply_privacy_history(pre-forget snapshot)",
                 "recovering an older snapshot replays the forget: the Signal and its evidence stay deleted",
                 {"signals_before": before, "signals_after": after, "removed_files": len(removed)},
                 before == 1 and after == 0 and any(r.startswith("blobs/") for r in removed), kind=h.LIBRARY)


def _live_export(runner, report: h.Report) -> None:
    if not h.gate(runner, report, "export <tmp>/before.ae1 --shadow", "encrypted snapshot written", h.LOCAL_WRITE):
        return
    directory = Path(tempfile.mkdtemp(prefix="artha_uat_"))
    try:
        path = directory / "before.ae1"
        h.run_step(runner, report, ["export", str(path), h.SHADOW], "encrypted snapshot written",
                   lambda o: o.ok and path.exists() and o.data["dispatchable"] is False, kind=h.LOCAL_WRITE)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(h.main("RA-23", "Beliefs view, forget receipts and third-party data", scenario, setup=setup,
                    limitations=LIMITATIONS))
