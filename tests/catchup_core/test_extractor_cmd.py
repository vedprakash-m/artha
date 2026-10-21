from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from catchup_core import domain_pack as dp, extract as ex
from scripts.catchup_core import extractor_cmd

PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "kids_school.yaml")


def test_parse_prompt():
    prompt = """You extract structured candidate facts.
Allowed subjects: kid1, kid2

Allowed predicates (name: type [allowed values]):
- exam_registration_deadline: date (YYYY-MM-DD)
- test_date: date (YYYY-MM-DD)
- accommodation_status: enum [none, requested, approved, expired]

Schema:
{"candidates": []}

<untrusted>
Message text here.
</untrusted>
"""
    subjects, predicates, message = extractor_cmd._parse_prompt(prompt)
    assert subjects == ["kid1", "kid2"]
    assert "exam_registration_deadline" in predicates
    assert "test_date" in predicates
    assert "accommodation_status" in predicates
    assert message == "Message text here."


def test_rule_based_appointment_time():
    text = "Appointment confirmed for 2026-10-25T14:00:00-07:00 at the main clinic."
    candidates = extractor_cmd._extract_rule_based(text, ["kid1"], ["appointment_time"])
    assert len(candidates) == 1
    c = candidates[0]
    assert c["predicate"] == "appointment_time"
    assert c["value"] == "2026-10-25T14:00:00-07:00"
    assert text[c["span"][0]:c["span"][1]] == c["quote"]


def test_rule_based_accommodation_status():
    text = "Notice: accommodation status: approved for semester."
    candidates = extractor_cmd._extract_rule_based(text, ["kid1"], ["accommodation_status"])
    assert len(candidates) == 1
    c = candidates[0]
    assert c["predicate"] == "accommodation_status"
    assert c["value"] == "approved"
    assert text[c["span"][0]:c["span"][1]] == c["quote"]


def test_rule_based_no_match_returns_empty():
    text = "Just a general school announcement without dates."
    candidates = extractor_cmd._extract_rule_based(text, ["kid1"], ["appointment_time", "test_date"])
    assert candidates == []


def test_align_candidates_fixes_span():
    text = "Important note: SAT test date is 2026-11-05 for all juniors."
    cands = [{
        "predicate": "test_date",
        "value": "2026-11-05",
        "subject_id": "kid1",
        "quote": "2026-11-05",
        "span": [0, 10],  # deliberately wrong span
    }]
    aligned = extractor_cmd._align_candidates(cands, text, ["kid1"], ["test_date"])
    assert len(aligned) == 1
    assert aligned[0]["span"] == [33, 43]
    assert text[aligned[0]["span"][0]:aligned[0]["span"][1]] == "2026-11-05"


def test_extractor_cmd_main_end_to_end(monkeypatch):
    prompt = """Allowed subjects: kid1

Allowed predicates (name: type [allowed values]):
- appointment_time: datetime (ISO-8601 with offset)

Schema:
{"candidates": []}

<untrusted>
Doctor appointment on 2026-10-28T09:30:00-07:00.
</untrusted>
"""
    monkeypatch.setattr(sys, "stdin", io.StringIO(prompt))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    rc = extractor_cmd.main()
    assert rc == 0
    data = json.loads(out.getvalue())
    assert "candidates" in data
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["predicate"] == "appointment_time"
    assert data["candidates"][0]["value"] == "2026-10-28T09:30:00-07:00"


def test_extractor_cmd_validates_with_catchup_core_extract():
    msg = "Dr. checkup on 2026-10-29T10:00:00-07:00."
    sig = ex.NormalizedSignal("sig_test", "calendar", msg)
    req = ex.build_request(PACK, sig, ["kid1"])

    # Run extractor_cmd directly as subprocess
    script_path = Path(extractor_cmd.__file__).resolve()
    proc = subprocess.run([sys.executable, str(script_path)], input=req.prompt,
                          capture_output=True, text=True, check=True)
    parsed = ex.parse_output(proc.stdout, PACK, sig, ["kid1"])
    assert parsed.ok
    assert len(parsed.candidates) == 1
    assert parsed.candidates[0].predicate == "appointment_time"
