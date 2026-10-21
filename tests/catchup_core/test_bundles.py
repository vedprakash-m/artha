"""Ingest bundles (re-artha §6.10): signed, encrypted, immutable, idempotent, honest about gaps."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from catchup_core.bundles import BundleError, ProducerKey, export_bundle, import_bundles
from catchup_core.contracts import CoverageStatus
from catchup_core.coordinator import ConnectorRecord

KEY = ProducerKey("win-host", b"w" * 32)
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def put(dirpath, seq=1, part=1, parts=1, records=None, **over):
    args = dict(namespace="personal", recipient="mac", account_scope="school", authority_generation=1, sequence=seq,
                records=records if records is not None else [ConnectorRecord(f"m{seq}{part}", f"Registration closes {seq}")],
                requested_since="2026-10-01T00:00:00Z", completed_until=(NOW - timedelta(hours=2)).isoformat(),
                created_at=NOW, part=part, parts=parts)
    args.update(over)
    return export_bundle(dirpath, KEY, **args)


def load(dirpath, **over):
    args = dict(name="school_email", namespace="personal", recipient="mac", account_scope="school",
                authority_generation=1, now=NOW)
    args.update(over)
    return import_bundles(dirpath, {"win-host": KEY}, **args)


def test_round_trip_is_complete_and_the_body_is_not_readable_at_rest(tmp_path):
    path = put(tmp_path)
    assert b"Registration" not in path.read_bytes()
    result = load(tmp_path)
    assert result.fetch.status is CoverageStatus.COMPLETE and [r.text for r in result.fetch.records] == ["Registration closes 1"]
    assert result.imported == ["win-host#1"]


def test_import_is_idempotent_and_acknowledged(tmp_path):
    put(tmp_path)
    load(tmp_path)
    again = load(tmp_path)
    assert again.fetch.records == () and again.imported == [] and again.fetch.status is CoverageStatus.COMPLETE
    assert list(tmp_path.glob("ack-*.json"))


def test_published_parts_are_immutable(tmp_path):
    put(tmp_path)
    with pytest.raises(BundleError, match="immutable"):
        put(tmp_path)


def test_forged_or_edited_bundles_are_quarantined_before_decoding(tmp_path):
    path = put(tmp_path)
    wrapper = json.loads(path.read_text())
    wrapper["header"]["account_scope"] = "other"                       # edit the signed header
    path.write_text(json.dumps(wrapper))
    result = load(tmp_path)
    assert result.fetch.records == () and result.quarantined[0][1] == "signature does not verify"
    assert (tmp_path / "quarantine" / path.name).exists() and result.fetch.status is not CoverageStatus.COMPLETE


def test_unenrolled_producer_and_garbage_are_rejected(tmp_path):
    put(tmp_path)
    (tmp_path / "bundle-x-00000001-p001.json").write_text("not json")
    result = import_bundles(tmp_path, {}, name="s", namespace="personal", recipient="mac", account_scope="school",
                            authority_generation=1, now=NOW)
    assert [why for _n, why in result.skipped] == ["producer is not enrolled here"]      # maybe another host's: untouched
    assert [why for _n, why in result.quarantined] == ["bundle is not readable"]


@pytest.mark.parametrize("over,why", [
    ({"namespace": "work"}, "namespace mismatch"),
    ({"recipient": "someone-else"}, "another recipient"),
    ({"account_scope": "other-scope"}, "another account scope"),
])
def test_bundles_for_another_importer_are_skipped_and_left_untouched(tmp_path, over, why):
    path = put(tmp_path, **over)
    result = load(tmp_path)
    assert result.fetch.records == () and why in result.skipped[0][1] and result.quarantined == []
    assert path.exists() and not (tmp_path / "quarantine").exists()       # the Work or second host can still import it


def test_a_stale_authority_generation_addressed_to_us_is_quarantined(tmp_path):
    path = put(tmp_path, authority_generation=0)
    result = load(tmp_path)
    assert "former authority generation" in result.quarantined[0][1] and not path.exists()


def test_missing_part_and_sequence_gap_leave_the_scope_visibly_incomplete(tmp_path):
    put(tmp_path, seq=1)
    put(tmp_path, seq=2, part=1, parts=2)                              # part 2 never arrives
    put(tmp_path, seq=4)                                               # sequence 3 is missing
    result = load(tmp_path)
    text = " ".join(result.fetch.blind_spots)
    assert "1 of 2 parts" in text and "missing [3]" in text
    assert result.fetch.status is CoverageStatus.DEGRADED
    assert [r.text for r in result.fetch.records] == ["Registration closes 1", "Registration closes 4"]


def test_multipart_sequence_imports_once_all_parts_are_present(tmp_path):
    put(tmp_path, seq=1, part=1, parts=2)
    put(tmp_path, seq=1, part=2, parts=2, records=[ConnectorRecord("m-b", "second")])
    result = load(tmp_path)
    assert len(result.fetch.records) == 2 and result.fetch.status is CoverageStatus.COMPLETE


def test_stale_secondary_data_is_stated_with_its_age(tmp_path):
    put(tmp_path, completed_until=(NOW - timedelta(hours=100)).isoformat())
    result = load(tmp_path)
    assert result.fetch.status is CoverageStatus.DEGRADED and "100h old" in " ".join(result.fetch.blind_spots)
    later = load(tmp_path, now=NOW + timedelta(hours=1))                # already acknowledged: freshness still known
    assert "101h old" in " ".join(later.fetch.blind_spots)


def test_no_bundle_ever_is_unavailable_not_quiet(tmp_path):
    result = load(tmp_path / "missing")
    assert result.fetch.status is CoverageStatus.UNAVAILABLE and "no bundle has ever been imported" in result.fetch.blind_spots[0]


def test_oversize_and_bad_keys_are_refused(tmp_path):
    with pytest.raises(ValueError):
        ProducerKey("x", b"short")
    with pytest.raises(BundleError, match="size limit"):
        export_bundle(tmp_path, KEY, namespace="personal", recipient="mac", account_scope="school",
                      authority_generation=1, sequence=1, records=[ConnectorRecord("m", "x" * 200_001)],
                      requested_since="a", completed_until="b", created_at=NOW)


class Keyring:
    def __init__(self):
        self.store = {}

    def get_password(self, s, n):
        return self.store.get((s, n))

    def set_password(self, s, n, v):
        self.store[(s, n)] = v


def test_enrollment_round_trips_through_the_credential_store_and_rejects_bad_secrets():
    from catchup_core.bundles import enroll_producer, load_producer_key
    kr = Keyring()
    assert load_producer_key("win-host", kr) is None
    secret = enroll_producer("win-host", kr)
    other = Keyring()
    assert enroll_producer("win-host", other, secret) == secret                  # the other machine imports it
    assert load_producer_key("win-host", kr).secret == load_producer_key("win-host", other).secret
    with pytest.raises(ValueError):
        enroll_producer("win-host", Keyring(), "c2hvcnQ=")
    kr.store[("artha-bundle-win-host", "secret")] = "!!!"
    assert load_producer_key("win-host", kr) is None


def test_only_a_complete_fetch_may_be_exported_and_sequences_advance(tmp_path):
    from catchup_core.bundles import export_fetch, next_sequence
    from catchup_core.coordinator import ConnectorFetch
    complete = ConnectorFetch("school_email", "school", (ConnectorRecord("m1", "t"),), CoverageStatus.COMPLETE)
    assert next_sequence(tmp_path, "win-host") == 1
    export_fetch(tmp_path, KEY, complete, namespace="personal", recipient="mac", authority_generation=1,
                 since="2026-10-01T00:00:00Z", now=NOW)
    assert next_sequence(tmp_path, "win-host") == 2
    partial = ConnectorFetch("school_email", "school", (), CoverageStatus.DEGRADED)
    with pytest.raises(BundleError, match="incomplete export is not published"):
        export_fetch(tmp_path, KEY, partial, namespace="personal", recipient="mac", authority_generation=1,
                     since="x", now=NOW)
    assert load(tmp_path, now=NOW + timedelta(minutes=1)).fetch.status is CoverageStatus.COMPLETE
