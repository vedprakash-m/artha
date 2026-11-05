"""RA-06 UAT: corrections and deliberate memory.

Commands, in order (``--shadow --json`` on every CLI command):

  beliefs                                   read         nothing written
  why <fact>                                read         nothing written
  correct <fact> --value V                  preview      nothing written (diff + apply_version)
  beliefs                                   read         value unchanged after the preview
  correct <fact> --value V --apply 0        local-write  refused: stale version, nothing written
  correct <fact> --value V --apply <ver>    scratch-only writes a user_confirmed Fact version, a P4 correction
                                                         Alert, feedback and an encrypted replay case
  beliefs / why <fact>                      scratch-only read: new value and provenance, prior version in history
  review                                    read         lists the correction as an unreviewed replay case
  capture "<synthetic text>"                local-write  one encrypted inbox/cap_*.ae1 in local storage

Live (no ``--scratch``): read and preview steps run; ``local-write`` steps run only with ``--allow-local-writes``
(the stale-version attempt is refused before writing, capture adds one synthetic inbox note that the next Catch-up
reviews); ``scratch-only`` steps never run live because they change existing owner records.  No step contacts a
provider, sends anything or schedules work.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

NEW_VALUE = "2026-10-21"
CAPTURE = "UAT synthetic capture: call the dentist on Friday"
LIMITATIONS = (
    "Replay anti-resurrection (a later Catch-up re-extracting the corrected Signal must not overwrite the "
    "user_confirmed value) needs a Catch-up with connector data; the CLI has no replay command.",
)


def setup(runner: h.ScratchRunner) -> None:
    runner.seed(h.seed_school_fact)


def scenario(runner, report: h.Report) -> None:
    fact = h.first_fact(runner, report, shadow=True)
    if fact is None:
        return
    fid, old = fact["fact_id"], fact["value"]
    new = NEW_VALUE if not runner.live else old
    h.run_step(runner, report, ["why", fid, h.SHADOW], "evidence or provenance explains the Fact",
               lambda o: o.ok and o.data["fact_id"] == fid and (o.data["evidence"] or o.data["provenance"]))
    preview = h.run_step(runner, report, ["correct", fid, "--value", new, h.SHADOW],
                         "preview shows before/after and an apply_version; nothing changes",
                         lambda o: o.ok and o.data["before"] == old and o.data["after"] == new
                         and o.data["apply_version"], kind=h.PREVIEW)
    h.beliefs(runner, report, expected="value unchanged after the preview", shadow=True,
              check=lambda rows: any(r["fact_id"] == fid and r["value"] == old for r in rows))
    h.run_step(runner, report, ["correct", fid, "--value", new, "--apply", "0", h.SHADOW],
               "a stale apply_version is refused", lambda o: h.refused(o, "changed since"), kind=h.LOCAL_WRITE)
    if preview is not None and preview.ok:
        version = preview.data["apply_version"]
        h.run_step(runner, report, ["correct", fid, "--value", new, "--apply", version, h.SHADOW],
                   "correction applied with user_confirmed provenance",
                   lambda o: o.ok and o.data["corrected"] == fid and o.data["provenance"] == "user_confirmed",
                   kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["beliefs", h.SHADOW], "beliefs shows the corrected value as user_confirmed",
               lambda o: o.ok and any(r["fact_id"] == fid and r["value"] == new
                                      and r["provenance"] == "user_confirmed" for r in o.data["beliefs"]),
               kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["why", fid, h.SHADOW], "history keeps the prior source value",
               lambda o: o.ok and any(v["value"] == old for v in o.data["history"]), kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["review", h.SHADOW],
               "review lists an unreviewed case" + ("" if runner.live else " for the correction"),
               lambda o: o.ok and isinstance(o.data["review"], list) and (runner.live or any(
                   c["response"] == "correct" and c["status"] == "unreviewed" for c in o.data["review"])))
    captured = h.run_step(runner, report, ["capture", CAPTURE, h.SHADOW], "capture recorded for the next Catch-up",
                          lambda o: o.ok and o.data["captured"].startswith("cap_"), kind=h.LOCAL_WRITE)
    if captured is not None and captured.ok and not runner.live:
        path = runner.local() / "inbox" / f"{captured.data['captured']}.ae1"
        report.check(f"inspect {path.name}", "capture stored encrypted; plaintext absent",
                     {"exists": path.exists()}, path.exists() and not h.contains(path, "dentist"), kind=h.LIBRARY)


if __name__ == "__main__":
    sys.exit(h.main("RA-06", "Corrections and deliberate memory", scenario, setup=setup, limitations=LIMITATIONS))
