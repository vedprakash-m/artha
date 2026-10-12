"""Acceptance scenarios for the Kids/School pilot pack on synthetic data (re-artha §12.6)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from catchup_core import coordinator as co
from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode, RunStatus
from catchup_core.scorecard import Case, score

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kids_school" / "scenarios.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(DATA["now"])
KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "kids_school.yaml")


def model_for(mapping):
    def run(prompt: str) -> str:
        message = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
        items = []
        for quote, (pred, value, subject) in mapping.items():
            if quote in message:
                i = message.index(quote)
                items.append({"predicate": pred, "value": value, "subject_id": subject, "quote": quote,
                              "span": [i, i + len(quote)]})
        return json.dumps({"candidates": items})
    return run


def run_scenario(sc, tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="owner").open()
    ledger.migrate()
    try:
        records = [co.ConnectorRecord(m["id"], m["text"]) for m in sc["messages"]]
        status = CoverageStatus(sc["calendar"])
        fetchers = {
            "school_email": lambda: co.ConnectorFetch("school_email", "acct", tuple(records)),
            "calendar": lambda: co.ConnectorFetch("calendar", "acct", (), status),
        }
        deps = co.Deps(
            ledger=ledger, pack=PACK, blobs=EncryptedBlobStore(tmp_path / "b", CIPHER, KEY), id_key=KEY,
            fetchers=fetchers, extractor=model_for({k: tuple(v) for k, v in sc["extractions"].items()}),
            subjects=["kid1", "kid2"], viewer="owner", now=lambda: NOW,
            source_class={"school_email": "school_email", "calendar": "calendar"},
            coverage_line=lambda: "Coverage: scenario.")
        texts: list[str] = []
        deps.publisher = lambda cid, text, v: texts.append(text)
        result = co.run_catchup(CatchupRequest(sc["id"], "u", "cli", mode=ExecutionMode.AUTHORITATIVE, host="h"),
                                deps)
        keys = set()
        for a in result.artifact_ids:
            row = ledger.conn.execute("SELECT predicate, primary_subject_id, domain FROM facts WHERE fact_id=?",
                                      (a,)).fetchone()
            keys.add(f"{row['domain']}:{row['primary_subject_id']}:{row['predicate']}")
        return result, keys, texts[0] if texts else ""
    finally:
        ledger.close()


@pytest.mark.parametrize("sc", DATA["scenarios"], ids=[s["id"] for s in DATA["scenarios"]])
def test_scenario(sc, tmp_path):
    result, keys, text = run_scenario(sc, tmp_path)
    exp = sc["expect"]
    assert keys == set(exp["alerts"])
    assert result.status is RunStatus(exp["status"])
    quiet = "Nothing urgent in the sources checked." in text
    assert quiet is exp["quiet"]
    if "horizon" in exp:
        assert text.count("COMING UP") == 1 and text.count("signup deadline") == exp["horizon"]
    assert "attacker" not in text and "IGNORE" not in text


def test_whole_suite_passes_the_release_scorecard(tmp_path):
    cases = []
    for i, sc in enumerate(DATA["scenarios"]):
        result, keys, text = run_scenario(sc, tmp_path / str(i))
        gaps = any(c.required and not c.status.quiet_claim_allowed for c in result.coverage)
        cases.append(Case(sc["id"], expected=frozenset(sc["expect"]["alerts"]), surfaced=frozenset(keys),
                          quiet_claim="Nothing urgent in the sources checked." in text,
                          coverage_complete=not gaps, citations_resolve="MISSING" not in text))
    s = score(cases)
    assert s.passes, s.failures()
    assert s.cases == len(DATA["scenarios"]) and s.false_quiet == 0
