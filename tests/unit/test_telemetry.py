

def test_chain_forks_before_the_reviewed_date_are_excused_and_later_ones_are_not(tmp_path):
    import json  # noqa: PLC0415

    from lib import telemetry  # noqa: PLC0415

    def entry(ts, prev):
        body = {"ts": ts, "event": "x", "prev_hash": prev}
        return {**body, "entry_hash": telemetry._compute_entry_hash(body)}

    first = entry("2026-04-23T00:00:00+00:00", telemetry._GENESIS_HASH)
    fork = entry("2026-04-24T00:00:00+00:00", "0" * 64)                 # does not follow `first`
    late = entry("2026-09-01T00:00:00+00:00", "1" * 64)                 # a fork after the review window
    log = tmp_path / "t.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in (first, fork)) + "\n", encoding="utf-8")
    assert telemetry.chain_breaks(log) == ["2026-04-24T00:00:00+00:00"] and telemetry.unreviewed_breaks(log) == []
    log.write_text("\n".join(json.dumps(e) for e in (first, fork, late)) + "\n", encoding="utf-8")
    assert telemetry.unreviewed_breaks(log) == ["2026-09-01T00:00:00+00:00"]
    tampered = {**first, "event": "changed"}
    log.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    import pytest  # noqa: PLC0415
    with pytest.raises(ValueError):
        telemetry.chain_breaks(log)
