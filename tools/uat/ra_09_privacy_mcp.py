"""RA-09 UAT: privacy, Work isolation and the bounded read-only MCP answer interface.

Commands, in order:

  cli mcp  (stdin: initialize, tools/list)              read  nothing written; lists tools and their annotations
  cli mcp  tools/call beliefs                           read  nothing written
  cli mcp  tools/call propose_correction <fact> <value> read  draft only (applied: false)
  beliefs --shadow                                      read  the Fact is unchanged after the proposal
  cli mcp  tools/call propose_feedback                  read  draft only (applied: false)
  cli mcp  tools/call beliefs {viewer: ...}             read  refused: unknown argument
  cli mcp  tools/call forget                            read  refused: unknown tool (-32602)
  beliefs --shadow --actor uat-other-person             read  refused: actor is not the configured viewer
  beliefs --shadow --subject adult1                     scratch-only  consent-required person shows nothing
  cli mcp  tools/call beliefs {subject: adult1}         scratch-only  the same through MCP

Live (no ``--scratch``): every step except the two seeded third-party checks runs; none writes anything.  The MCP
server is started as a subprocess with the requests on stdin and exits at end of input (no listener).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

READ_TOOLS = {"beliefs", "why", "items", "goals", "health", "activity", "history", "propose_correction",
              "propose_feedback"}
LIMITATIONS = (
    "Work isolation (Work OS reads only state/work/ and catchup_core never reads it) has no catchup_core CLI "
    "surface; it is covered by tests, and Work migration/recovery remains an open gap.",
)


def setup(runner: h.ScratchRunner) -> None:
    from catchup_core.ledger import FactInput, SignalInput  # noqa: PLC0415
    runner.seed(h.seed_school_fact)

    def third_party(deps) -> None:
        ref = deps.blobs.put("mail_m3", "Adult checkup booked 2026-11-02")
        sid, _ = deps.ledger.upsert_signal(SignalInput("school_email", "personal", "m3", "v1", h.NOW.isoformat(),
                                                       "h_m3", ref))
        deps.ledger.set_signal_subjects(sid, ["adult1"])
        deps.ledger.put_fact(FactInput(domain="health", predicate="checkup_date", value="2026-11-02",
                                       primary_subject_id="adult1", observed_at=h.NOW.isoformat(),
                                       evidence=((sid, 0, 10),)))
    runner.seed(third_party)


def scenario(runner, report: h.Report) -> None:  # noqa: PLR0915
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    listed = runner.mcp([init, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    info = listed[0].get("result", {}) if listed else {}
    report.check("cli mcp: initialize", "server identifies itself as read-only",
                 info, "read-only" in str(info.get("instructions", "")).lower())
    tools = (listed[1].get("result", {}).get("tools", []) if len(listed) > 1 else [])
    annotations = {t["name"]: t.get("annotations", {}) for t in tools}
    report.check("cli mcp: tools/list", "only the bounded read/proposal tools; all readOnlyHint, none destructive",
                 annotations, bool(tools) and set(annotations) <= READ_TOOLS and all(
                     a.get("readOnlyHint") is True and a.get("destructiveHint") is False
                     for a in annotations.values()))
    fact = h.first_fact(runner, report)
    if fact is None:
        return
    fid, value = fact["fact_id"], fact["value"]
    proposal = "2026-10-21" if not runner.live else value
    responses = runner.mcp([init, h.call(3, "beliefs"),
                            h.call(4, "propose_correction", {"fact_id": fid, "value": proposal}),
                            h.call(5, "propose_feedback", {"obligation": "uat", "response": "useful",
                                                           "reason": "UAT proposal; nothing is applied"}),
                            h.call(6, "beliefs", {"viewer": "uat-other-person"}),
                            h.call(7, "forget", {})])
    by_id = {r.get("id"): r for r in responses}
    error, payload = h.tool_payload(by_id.get(3, {}))
    report.check("cli mcp: tools/call beliefs", "answers from the same permission-filtered view as the CLI",
                 payload, not error and any(r["fact_id"] == fid for r in payload.get("beliefs", [])))
    error, payload = h.tool_payload(by_id.get(4, {}))
    report.check("cli mcp: tools/call propose_correction", "returns a draft diff; applied is false",
                 payload, not error and payload.get("applied") is False and payload["draft"]["after"] == proposal)
    h.beliefs(runner, report, expected="the proposal changed nothing",
              check=lambda rows: any(r["fact_id"] == fid and r["value"] == value for r in rows))
    error, payload = h.tool_payload(by_id.get(5, {}))
    report.check("cli mcp: tools/call propose_feedback", "returns a draft; applied is false",
                 payload, not error and payload.get("applied") is False)
    error, payload = h.tool_payload(by_id.get(6, {}))
    report.check("cli mcp: tools/call beliefs {viewer}", "a caller cannot choose the viewer: unknown argument",
                 payload, error and "unknown argument" in str(payload))
    forget = by_id.get(7, {})
    report.check("cli mcp: tools/call forget", "no write tool exists: JSON-RPC error -32602",
                 forget, forget.get("error", {}).get("code") == -32602)
    h.run_step(runner, report, ["beliefs", h.SHADOW, "--actor", "uat-other-person"],
               "an actor other than the configured viewer is refused",
               lambda o: h.refused(o, "actor does not match"))
    h.run_step(runner, report, ["beliefs", h.SHADOW, "--subject", "adult1"],
               "a consent-required person without a grant shows nothing (or is refused)",
               lambda o: (o.ok and o.data["beliefs"] == []) or not o.ok, kind=h.SCRATCH_ONLY)
    if h.gate(runner, report, "cli mcp: tools/call beliefs {subject: adult1}", "nothing shown", h.SCRATCH_ONLY):
        error, payload = h.tool_payload(runner.mcp([init, h.call(8, "beliefs", {"subject": "adult1"})])[-1])
        report.check("cli mcp: tools/call beliefs {subject: adult1}",
                     "MCP applies the same consent filter: nothing shown (or refused)", payload,
                     error or payload.get("beliefs") == [], kind=h.SCRATCH_ONLY)


if __name__ == "__main__":
    sys.exit(h.main("RA-09", "Privacy, Work isolation and the read-only MCP answer interface", scenario,
                    setup=setup, limitations=LIMITATIONS))
