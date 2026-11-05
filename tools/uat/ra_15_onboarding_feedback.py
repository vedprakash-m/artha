"""RA-15 UAT: onboarding, correction and feedback.

Commands, in order (``--json`` on every CLI command):

  health --shadow                                  read          nothing written
  history --shadow / items --shadow / goals --shadow
                                                   read          nothing written
  configure bootstrap_days 45 --shadow             preview       a configure lock file only; config unchanged
  configure bootstrap_days 45 --shadow --apply D   local-write   refused: shadow mode cannot change configuration
  configure bootstrap_days 0                       preview       refused: invalid value
  configure bootstrap_days 45                      preview       a configure lock file only; config unchanged
  configure bootstrap_days 45 --apply D            scratch-only  rewrites config/catchup_core.yaml
  feedback exam dismiss --reason R --shadow        scratch-only  feedback row + encrypted replay case
  review --shadow                                  read          nothing written
  review <id> --label handled --shadow             scratch-only  labels the replay case
  health --shadow                                  read          nothing written
  cli mcp  tools/call propose_feedback             read          draft only (applied: false)
  review --shadow                                  read          the proposal added nothing

Live (no ``--scratch``): read and preview steps run (the configuration file is hashed before and after to show it
is unchanged); changing configuration, recording feedback and labelling never run live.  Nothing is sent.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

LIMITATIONS = (
    "Accessible first-use and setup (a person using a screen reader or keyboard only, end to end) is a human UAT; "
    "this script records the CLI half only.",
    "Bootstrap through a first Catch-up needs real connector credentials; it is not run here.",
)


def setup(runner: h.ScratchRunner) -> None:
    runner.seed(h.seed_school_fact)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def list_check(key: str, *, live: bool):
    """A list answer; live, a named scoped-consent refusal also counts (the shadow store holds no household consent)."""
    def check(o: h.Outcome) -> bool:
        if o.ok:
            return isinstance(o.data[key], list)
        return live and h.refused(o, "scoped ingest consent")
    return check


def scenario(runner, report: h.Report) -> None:
    config = runner.root / "config" / "catchup_core.yaml"
    h.run_step(runner, report, ["health", h.SHADOW], "integrity verified; capability states and freshness reported",
               lambda o: o.ok and o.data["integrity"] is True and isinstance(o.data["capabilities"], list)
               and "stale" in o.data["freshness"])
    for command, key in (("history", "briefings"), ("items", "items"), ("goals", "goals")):
        h.run_step(runner, report, [command, h.SHADOW],
                   f"{command} answers with a list, even on first use" + (
                       " (live: or names the missing scoped consent)" if runner.live else ""),
                   list_check(key, live=runner.live))
    before = _digest(config)
    shadow = h.run_step(runner, report, ["configure", "bootstrap_days", "45", h.SHADOW],
                        "preview shows before/after, a diff and an apply digest; applied is false",
                        lambda o: o.ok and o.data["applied"] is False and o.data["after"] == 45
                        and o.data["apply_digest"] and o.data["diff"], kind=h.PREVIEW)
    if shadow is not None and shadow.ok:
        h.run_step(runner, report, ["configure", "bootstrap_days", "45", h.SHADOW, "--apply",
                                    shadow.data["apply_digest"]],
                   "shadow mode cannot change configuration", lambda o: h.refused(o, "shadow"), kind=h.LOCAL_WRITE)
    h.run_step(runner, report, ["configure", "bootstrap_days", "0"], "an invalid value is refused",
               lambda o: not o.ok, kind=h.PREVIEW)
    preview = h.run_step(runner, report, ["configure", "bootstrap_days", "45"],
                         "owner preview shows the exact change", lambda o: o.ok and o.data["applied"] is False,
                         kind=h.PREVIEW)
    report.check("sha256 config/catchup_core.yaml", "previews leave the configuration file unchanged",
                 {"before": before[:16], "after": _digest(config)[:16]}, before == _digest(config))
    if preview is not None and preview.ok:
        h.run_step(runner, report, ["configure", "bootstrap_days", "45", "--apply", preview.data["apply_digest"]],
                   "the reviewed digest applies exactly that change",
                   lambda o: o.ok and o.data["applied"] is True and "bootstrap_days: 45" in config.read_text(),
                   kind=h.SCRATCH_ONLY)
    feedback = h.run_step(runner, report, ["feedback", "exam", "dismiss", "--reason", "already registered", h.SHADOW],
                          "feedback becomes a replay case pending review",
                          lambda o: o.ok and o.data["feedback_id"].startswith("fbk_")
                          and o.data["expected_outcome"] == "pending_review", kind=h.SCRATCH_ONLY)
    ident = feedback.data["feedback_id"] if feedback is not None and feedback.ok else None
    queue = h.run_step(runner, report, ["review", h.SHADOW],
                       "review lists unreviewed cases" + ("" if runner.live else ", including the new feedback"),
                       lambda o: o.ok and isinstance(o.data["review"], list) and (ident is None or any(
                           c["id"] == ident and c["status"] == "unreviewed" for c in o.data["review"])))
    if ident is not None:
        h.run_step(runner, report, ["review", ident, "--label", "handled", h.SHADOW],
                   "the owner labels the case", lambda o: o.ok and o.data["review"][0]["status"] == "handled",
                   kind=h.SCRATCH_ONLY)
    h.run_step(runner, report, ["health", h.SHADOW], "health reports feedback and reviewed alert classes",
               lambda o: o.ok and isinstance(o.data["feedback"], list)
               and isinstance(o.data["reviewed_alert_classes"], list))
    error, payload = h.tool_payload(runner.mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        h.call(2, "propose_feedback", {"obligation": "exam", "response": "useful", "reason": "UAT proposal"})])[-1])
    report.check("cli mcp: tools/call propose_feedback", "an assistant can only draft feedback; applied is false",
                 payload, not error and payload.get("applied") is False)
    if queue is not None and queue.ok:
        count = len([c for c in queue.data["review"] if c["id"] != ident])
        h.run_step(runner, report, ["review", h.SHADOW], "the MCP proposal recorded nothing",
                   lambda o: o.ok and len(o.data["review"]) == count)


if __name__ == "__main__":
    sys.exit(h.main("RA-15", "Onboarding, correction and feedback", scenario, setup=setup, limitations=LIMITATIONS))
