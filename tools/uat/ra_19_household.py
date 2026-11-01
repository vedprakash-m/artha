"""RA-19 UAT: household coordination (read-only).

Commands, in order (``--shadow --json`` on every CLI command):

  health                                      read          RA-19 capability state
  complete household:uat-missing             preview       refused: no such task (nothing written)
  complete household:H1                      scratch-only  preview: before 'unverified', apply token
  complete household:H1 --apply T            scratch-only  records the owner's completion evidence Fact
  complete household:H1 --reopen             scratch-only  preview of withdrawing it
  complete household:H1 --reopen --apply T  scratch-only  withdraws the completion evidence
  complete household:H2                      scratch-only  refused: another person's task
  complete household:H3                      scratch-only  refused: not visible to this viewer
  (library) runtime._coordination_lines      library       Briefing lines for the owner and for a stranger

Scratch writes a synthetic ``state/household_tasks.md`` (H1 owned by the viewer and marked done without evidence, H2 refused by a spouse, H3 visible
only to the spouse).  Nothing is assigned, sent or scheduled: coordination is read-only and completion is the
viewer's own evidence.  Live (no ``--scratch``): only the health and missing-task steps run; the live task IDs are
the owner's and are not guessed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

TASKS = """---
household_tasks:
  - {id: H1, obligation: Book dentist, subject: kid1,   owner: owner, status: done, assigned_on: '2026-10-01',
     visible_to: [owner]}
  - {id: H2, obligation: RSVP party, subject: kid1, owner: spouse, status: refused, assigned_on: '2026-10-01',
     visible_to: [owner, spouse]}
  - {id: H3, obligation: Spouse private task, subject: kid1, owner: spouse, status: accepted,
     assigned_on: '2026-10-01', visible_to: [spouse]}
---
"""
LIMITATIONS = (
    "Consented household UAT needs a second real adult granting consent on the live host; scratch uses a "
    "synthetic spouse.",
    "Coordination lines appear only inside a Catch-up Briefing; the CLI has no separate view, so they are shown "
    "through the library function the Briefing uses.",
)


def setup(runner: h.ScratchRunner) -> None:
    (runner.root / "state" / "household_tasks.md").write_text(TASKS, encoding="utf-8")


def scenario(runner, report: h.Report) -> None:
    h.run_step(runner, report, ["health", h.SHADOW], "health reports the RA-19 capability state",
               lambda o: o.ok and any(c["id"] == "RA-19" for c in o.data["capabilities"]))
    h.run_step(runner, report, ["complete", "household:uat-missing", h.SHADOW], "an unknown task is refused",
               lambda o: h.refused(o, "missing or ambiguous"), kind=h.PREVIEW)
    if not runner.live:
        runner.seed(lambda deps: _lines(deps, runner, report, "H1: Book dentist", True,
                                        "H1 is marked done without evidence, so the Briefing flags it"))
    preview = h.run_step(runner, report, ["complete", "household:H1", h.SHADOW],
                         "preview: currently unverified; completion needs the owner's explicit confirmation",
                         lambda o: o.ok and o.data["before"] == "unverified" and o.data["apply_token"],
                         kind=h.SCRATCH_ONLY)
    if preview is not None and preview.ok:
        h.run_step(runner, report, ["complete", "household:H1", "--apply", preview.data["apply_token"], h.SHADOW],
                   "the owner's confirmation is recorded as evidence",
                   lambda o: o.ok and o.data["completed"] == "household:H1", kind=h.SCRATCH_ONLY)
        if not runner.live:
            runner.seed(lambda deps: _lines(deps, runner, report,             "H1: Book dentist", False,
                                            "after confirmation the Briefing no longer flags H1 for review"))
        reopen = h.run_step(runner, report, ["complete", "household:H1", "--reopen", h.SHADOW],
                            "withdrawal preview from the recorded completion",
                            lambda o: o.ok and o.data["before"] == "current"
                            and o.data["after"] == "completion withdrawn", kind=h.SCRATCH_ONLY)
        if reopen is not None and reopen.ok:
            h.run_step(runner, report, ["complete", "household:H1", "--reopen", "--apply",
                                        reopen.data["apply_token"], h.SHADOW],
                       "completion withdrawn", lambda o: o.ok and o.data["reopened"] == "household:H1",
                       kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["complete", "household:H2", h.SHADOW], "another person's task cannot be completed",
               lambda o: h.refused(o, "accountable owner"), kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["complete", "household:H3", h.SHADOW], "a task not shared with the viewer is absent",
               lambda o: h.refused(o, "missing or ambiguous"), kind=h.SCRATCH_ONLY)
    if h.gate(runner, report, "(library) runtime._coordination_lines", "visible lines only", h.LIBRARY):
        runner.seed(lambda deps: _visibility(deps, runner, report))


def _lines(deps, runner, report: h.Report, prefix: str, present: bool, expected: str) -> None:
    from catchup_core import runtime  # noqa: PLC0415
    lines = runtime._coordination_lines(runner.root / "state", today=h.NOW.date(), viewer=deps.viewer,
                                        ledger=deps.ledger).lines
    report.check("(library) runtime._coordination_lines(viewer=owner)", expected, lines,
                 any(line.startswith(prefix) for line in lines) is present, kind=h.LIBRARY)


def _visibility(deps, runner, report: h.Report) -> None:
    from catchup_core import runtime  # noqa: PLC0415
    state = runner.root / "state"
    owner = runtime._coordination_lines(state, today=h.NOW.date(), viewer=deps.viewer, ledger=deps.ledger).lines
    stranger = runtime._coordination_lines(state, today=h.NOW.date(), viewer="uat-stranger").lines
    report.check("(library) runtime._coordination_lines(viewer=owner)",
                 "the spouse's refusal is surfaced for review; the spouse-only task is not shown",
                 owner, any(line.startswith("H2:") and "refus" in line for line in owner)
                 and not any(line.startswith("H3:") for line in owner), kind=h.LIBRARY)
    report.check("(library) runtime._coordination_lines(viewer=uat-stranger)",
                 "a person outside the household sees no coordination lines", stranger, stranger == [],
                 kind=h.LIBRARY)


if __name__ == "__main__":
    sys.exit(h.main("RA-19", "Household coordination (read-only)", scenario, setup=setup, limitations=LIMITATIONS))
