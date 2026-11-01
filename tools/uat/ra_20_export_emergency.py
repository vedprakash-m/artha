"""RA-20 UAT: portable export and emergency access.

Commands, in order (``--shadow --json`` on every CLI command):

  beliefs                                  read          nothing written
  export <tmp>/snapshot.ae1                local-write   one encrypted snapshot in a temporary directory
  interchange <tmp>/facts.ae1              local-write   one encrypted interchange file in the same directory
  interchange <tmp>/facts.ae1 --read       read          validates and summarises; applies nothing
  emergency                                read          nothing written
  restore <tmp>/snapshot.ae1 <tmp>/restored  (live)      local-write  refused: legacy restore is blocked
  (library) interchange.read_interchange   library       file carries no consent, actions, credentials or keys
  (library) backup.restore_encrypted_snapshot            library  refused; destination not created
  (library) interchange.emergency_view(viewer=uat-custodian)  library  a person with no grant sees nothing
  (scratch) add a Fact about a consent-required person, then export and interchange again
                                           scratch-only  that Fact's plaintext is never exported

The temporary directory is deleted at the end.  Live (no ``--scratch``): the export, interchange and restore
attempts run only with ``--allow-local-writes``; nothing is uploaded or sent.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

THIRD_PARTY_VALUE = "2026-11-02-UAT"
LIMITATIONS = (
    "A designated custodian's own view needs a second person as the configured viewer with emergency grants; the "
    "CLI always uses the configured viewer, so only the no-grant case is shown (library).",
    "Recovery onto a second host and the accessibility of the emergency view need the live hosts and a person.",
    "In scratch the restore refusal is shown through the library; the CLI restore uses the OS keyring and runs live.",
)


def setup(runner: h.ScratchRunner) -> None:
    runner.seed(h.seed_school_fact)


def scenario(runner, report: h.Report) -> None:
    fact = h.first_fact(runner, report)
    if fact is None:
        return
    directory = Path(tempfile.mkdtemp(prefix="artha_uat_", dir=None if runner.live else runner.base))
    try:
        _portable(runner, report, directory, fact)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    h.run_step(runner, report, ["emergency", h.SHADOW], "emergency view is read-only and not dispatchable",
               lambda o: o.ok and o.data["dispatchable"] is False and isinstance(o.data["facts"], list))
    if h.gate(runner, report, "(library) interchange.emergency_view(viewer=uat-custodian)", "nothing", h.LIBRARY):
        runner.seed(lambda deps: _custodian(deps, report))


def _portable(runner, report: h.Report, directory: Path, fact: dict) -> None:
    snapshot, facts = directory / "snapshot.ae1", directory / "facts.ae1"
    value = str(fact["value"])

    def leaks(path: Path) -> bool:
        return len(value) >= 8 and h.contains(path, value)  # shorter values can match ciphertext by chance

    exported = h.run_step(runner, report, ["export", str(snapshot), h.SHADOW],
                          "encrypted snapshot (artha.snapshot.v1), not dispatchable; Fact values not readable",
                          lambda o: o.ok and o.data["format"] == "artha.snapshot.v1"
                          and o.data["dispatchable"] is False and snapshot.exists()
                          and not leaks(snapshot), kind=h.LOCAL_WRITE)
    written = h.run_step(runner, report, ["interchange", str(facts), h.SHADOW],
                         "encrypted interchange (artha.interchange.v1) with at least one Fact, not dispatchable",
                         lambda o: o.ok and o.data["format"] == "artha.interchange.v1"
                         and o.data["exported_facts"] >= 1 and o.data["dispatchable"] is False
                         and not leaks(facts), kind=h.LOCAL_WRITE)
    if written is not None and written.ok:
        count = written.data["exported_facts"]
        h.run_step(runner, report, ["interchange", str(facts), "--read", h.SHADOW],
                   "reading validates and summarises the same Facts and executes nothing",
                   lambda o: o.ok and o.data["facts"] == count and o.data["executes_nothing"] is True)
    if exported is not None and exported.ok and runner.live:
        restored = directory / "restored"
        h.run_step(runner, report, ["restore", str(snapshot), str(restored)],
                   "legacy snapshot restore is refused; nothing is restored",
                   lambda o: h.refused(o, "governed checkpoint") and not restored.exists(), kind=h.LOCAL_WRITE)
    if not runner.live:
        runner.seed(lambda deps: _library_checks(deps, report, snapshot, facts, directory))
        runner.seed(_third_party)
        again = directory / "again.ae1"
        h.run_step(runner, report, ["export", str(again), h.SHADOW],
                   "a Fact the owner may not view is never exported (refused, or absent from the file)",
                   lambda o: (not o.ok and not again.exists()) or (o.ok and not h.contains(again, THIRD_PARTY_VALUE)),
                   kind=h.SCRATCH_ONLY)
        h.run_step(runner, report, ["interchange", str(facts), h.SHADOW],
                   "interchange omits that Fact and counts the omission",
                   lambda o: o.ok and o.data["omitted_facts"] >= 1 and not h.contains(facts, THIRD_PARTY_VALUE),
                   kind=h.SCRATCH_ONLY)


def _library_checks(deps, report: h.Report, snapshot: Path, facts: Path, directory: Path) -> None:
    from catchup_core.backup import restore_encrypted_snapshot  # noqa: PLC0415
    from catchup_core.interchange import EXCLUDED, read_interchange  # noqa: PLC0415
    document = read_interchange(facts, deps.ledger._cipher)
    report.check("(library) interchange.read_interchange", "no consent, actions, credentials or keys are carried",
                 {"keys": sorted(document), "excluded": document.get("excluded")},
                 not any(name in document for name in EXCLUDED) and document["dispatchable"] is False,
                 kind=h.LIBRARY)
    destination = directory / "restored"
    try:
        restore_encrypted_snapshot(snapshot, destination, deps.ledger._cipher, deps.ledger._sealed)
        observed = "restored"
    except Exception as exc:  # noqa: BLE001 - the refusal type is part of the observation
        observed = f"{type(exc).__name__}: {exc}"
    report.check("(library) backup.restore_encrypted_snapshot", "legacy restore refused; destination not created",
                 observed, "governed checkpoint" in observed and not destination.exists(), kind=h.LIBRARY)


def _custodian(deps, report: h.Report) -> None:
    from catchup_core.interchange import emergency_view  # noqa: PLC0415
    view = emergency_view(deps.ledger, "uat-custodian")
    report.check("(library) interchange.emergency_view(viewer=uat-custodian)",
                 "a person without emergency grants sees no Facts", view,
                 view["facts"] == [] and view["dispatchable"] is False, kind=h.LIBRARY)


def _third_party(deps) -> None:
    from catchup_core.ledger import FactInput, SignalInput  # noqa: PLC0415
    ref = deps.blobs.put("mail_m3", f"Adult checkup booked {THIRD_PARTY_VALUE}")
    sid, _ = deps.ledger.upsert_signal(SignalInput("school_email", "personal", "m3", "v1", h.NOW.isoformat(),
                                                   "h_m3", ref))
    deps.ledger.set_signal_subjects(sid, ["adult1"])
    deps.ledger.put_fact(FactInput(domain="health", predicate="checkup_date", value=THIRD_PARTY_VALUE,
                                   primary_subject_id="adult1", observed_at=h.NOW.isoformat(),
                                   evidence=((sid, 0, 10),)))


if __name__ == "__main__":
    sys.exit(h.main("RA-20", "Portable export and emergency access", scenario, setup=setup,
                    limitations=LIMITATIONS))
