"""Owner scope-review terminal flow — specs/refix-artha.md §B.1 requirements 1, 2, 6 and R-1..R-4.

Runs without the synthetic admission fixture: it exercises the production hard stops.
"""
from __future__ import annotations

import argparse
import json

import pytest

from catchup_core import crypto, ids
from catchup_core import ledger as lg
from catchup_core import scope_review_cli as src
from catchup_core.contracts import Confidentiality

KEY = ids.HmacKey(1, b"q" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
OWNER = "ved"
CANARY = "CANARY-7f3a payload text"


@pytest.fixture
def led(tmp_path):
    state = {"n": 0}

    def tick() -> str:
        state["n"] += 1
        return f"2026-11-18T00:{state['n'] // 60:02d}:{state['n'] % 60:02d}Z"

    ledger = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id=OWNER,
                       consent_subjects=frozenset(), clock=tick).open()
    ledger.migrate()
    yield ledger
    ledger.close()


def signal(led, upstream, connector="home_email"):
    sid, _ = led.upsert_signal(lg.SignalInput(
        connector, "acct", upstream, "v1", f"2026-11-1{len(upstream)}T00:00:00Z", f"h-{upstream}",
        f"blob:{upstream}", Confidentiality.ORDINARY))
    led.set_signal_subjects(sid, [OWNER])
    led.set_source_subjects(connector, "acct", [OWNER])
    return sid


def fact(led, sid):
    return led.put_fact(lg.FactInput(
        domain="home", predicate="hvac_service_due", value={"date": "2026-12-01"}, primary_subject_id=OWNER,
        observed_at="2026-11-17T00:00:00Z", confidentiality=Confidentiality.ORDINARY, evidence=((sid, 0, 4),)))


class Terminal:
    def __init__(self, answers):
        self.answers = list(answers)
        self.shown: list[str] = []

    def readline(self, prompt: str) -> str:
        self.shown.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def write(self, text: str) -> None:
        self.shown.append(text)

    @property
    def text(self) -> str:
        return "".join(self.shown)


def text_of(ref: str) -> str:
    return f"Subject: furnace\n<!-- hidden --> see https://example.invalid/x {CANARY} " + "z" * 600


def run(led, terminal, *, viewer=OWNER, interactive=True, connector="home_email"):
    return src.run(led, viewer=viewer, connector=connector, text_of=text_of, readline=terminal.readline,
                   write=terminal.write, interactive=interactive)


# ── requirement 1: owner-only, interactive ────────────────────────────────

def test_non_interactive_caller_is_refused_before_anything_is_shown(led):
    signal(led, "m1")
    terminal = Terminal(["1", "p"])
    with pytest.raises(lg.PermissionDenied):
        run(led, terminal, interactive=False)
    assert terminal.shown == []
    assert led.conn.execute("SELECT count(*) FROM scope_reviews").fetchone()[0] == 0


def test_non_owner_is_refused_before_anything_is_shown(led):
    signal(led, "m1")
    terminal = Terminal(["1", "p"])
    with pytest.raises(lg.PermissionDenied):
        run(led, terminal, viewer="guest")
    assert terminal.shown == []


# ── R-1: selected batches from a metadata list ────────────────────────────

def test_list_shows_metadata_only_and_blank_selection_cancels(led):
    sid = signal(led, "m1")
    terminal = Terminal([""])
    result = run(led, terminal)
    assert sid in terminal.text and "acct" in terminal.text
    assert CANARY not in terminal.text
    assert result["reviewed"] == {} and result["listed"] == 1


def test_only_the_requested_source_is_listed(led):
    signal(led, "m1")
    other = signal(led, "m2", connector="work_mail")
    terminal = Terminal([""])
    run(led, terminal)
    assert other not in terminal.text


def test_selected_signal_is_bound_individually(led):
    first, second = signal(led, "m1"), signal(led, "m22")
    terminal = Terminal(["2", "p"])
    result = run(led, terminal)
    assert result["reviewed"] == {"personal": 1}
    assert led.require_personal_signal(second)["signal_id"] == second
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(first)


def test_already_reviewed_signals_are_not_listed_again(led):
    sid = signal(led, "m1")
    run(led, Terminal(["1", "p"]))
    terminal = Terminal([""])
    assert run(led, terminal)["listed"] == 0
    assert sid not in terminal.text


@pytest.mark.parametrize("selection", ["0", "3", "x", "1-", "2-1", "1,,2"])
def test_invalid_selection_is_refused_and_writes_nothing(led, selection):
    signal(led, "m1")
    signal(led, "m22")
    with pytest.raises(ValueError, match="selection"):
        run(led, Terminal([selection]))
    assert led.conn.execute("SELECT count(*) FROM scope_reviews").fetchone()[0] == 0


def test_range_selection(led):
    for upstream in ("m1", "m22", "m333"):
        signal(led, upstream)
    result = run(led, Terminal(["1-2,3", "p", "w", "m"]))
    assert result["reviewed"] == {"personal": 1, "work": 1, "mixed": 1}


# ── R-2: metadata plus the stripped 400-character excerpt ─────────────────

def test_review_shows_stripped_excerpt_and_authorization_inputs(led):
    signal(led, "m1")
    terminal = Terminal(["1", "s"])
    run(led, terminal)
    assert CANARY in terminal.text
    assert "https://example.invalid" not in terminal.text and "hidden" not in terminal.text
    assert "z" * 401 not in terminal.text
    assert f'"{OWNER}"' in terminal.text or OWNER in terminal.text


def test_skip_and_unknown_answers_bind_nothing(led):
    sid = signal(led, "m1")
    result = run(led, Terminal(["1", "personal please"]))
    assert result["reviewed"] == {} and result["skipped"] == 1
    assert led.scope_review_item("signal", sid)["current_scope"] is None


# ── R-4: Facts grouped with their source Signals, reviewed explicitly ─────

def test_fact_is_offered_after_its_signal_and_bound_on_its_own(led):
    sid = signal(led, "m1")
    fid = fact(led, sid)
    terminal = Terminal(["1", "p", "p"])
    result = run(led, terminal)
    assert result["reviewed"] == {"personal": 2}
    assert terminal.text.index(sid) < terminal.text.index(fid)
    assert "hvac_service_due" in terminal.text
    assert led.get_fact(fid, OWNER).fact_id == fid


def test_fact_scope_is_never_derived_from_its_signal(led):
    sid = signal(led, "m1")
    fid = fact(led, sid)
    run(led, Terminal(["1", "p", "s"]))
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, OWNER)


def test_fact_value_is_never_shown(led):
    sid = signal(led, "m1")
    fact(led, sid)
    terminal = Terminal(["1", "s", "s"])
    run(led, terminal)
    assert "2026-12-01" not in terminal.text


# ── requirement 3: a change between display and answer is refused ─────────

def test_object_changed_while_owner_reads_is_refused(led):
    sid = signal(led, "m1")
    terminal = Terminal(["1"])

    def answer(prompt: str) -> str:
        terminal.shown.append(prompt)
        if prompt.startswith("Scope"):
            led.set_signal_subjects(sid, [OWNER, "someone"])
            return "p"
        return terminal.answers.pop(0)

    with pytest.raises(lg.LedgerError):
        src.run(led, viewer=OWNER, connector="home_email", text_of=text_of, readline=answer,
                write=terminal.write, interactive=True)
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


# ── requirement 6: no payload in the returned record ──────────────────────

def test_result_carries_counts_only(led):
    signal(led, "m1")
    result = run(led, Terminal(["1", "p"]))
    assert CANARY not in json.dumps(result)


# ── CLI wiring ────────────────────────────────────────────────────────────

def test_cli_parses_scope_review_as_a_ledger_write():
    from catchup_core import cli
    args = cli.parse_args(["scope-review", "home_email"])
    assert args.command == "scope-review" and args.source == "home_email"
    assert cli._mutates(args)


def test_mcp_does_not_offer_scope_review():
    from catchup_core import mcp
    assert "scope-review" not in mcp._READ
    assert all(tool["name"] != "scope-review" for tool in mcp.TOOLS)


def test_command_refuses_shadow_mode(led):
    from catchup_core import commands

    class Deps:
        ledger, viewer = led, OWNER

        def validate_inputs(self):
            return None

    args = argparse.Namespace(command="scope-review", source="home_email", shadow=True)
    with pytest.raises(lg.LedgerError):
        commands.execute(args, Deps(), led._path.parent)


def test_command_refuses_a_pipe(led, monkeypatch):
    import io

    from catchup_core import commands

    class Deps:
        ledger, viewer = led, OWNER
        blobs = argparse.Namespace(get=text_of)

        def validate_inputs(self):
            return None

    signal(led, "m1")
    monkeypatch.setattr("sys.stdin", io.StringIO("1\np\n"))
    args = argparse.Namespace(command="scope-review", source="home_email", shadow=False)
    with pytest.raises(lg.PermissionDenied):
        commands.execute(args, Deps(), led._path.parent)
    assert led.conn.execute("SELECT count(*) FROM scope_reviews").fetchone()[0] == 0


def test_unreadable_excerpt_is_reported_without_failing_the_review(led):
    sid = signal(led, "m1")

    def missing(_ref: str) -> str:
        raise OSError("blob missing")

    terminal = Terminal(["1", "p"])
    result = src.run(led, viewer=OWNER, connector="home_email", text_of=missing, readline=terminal.readline,
                     write=terminal.write, interactive=True)
    assert "(excerpt unavailable)" in terminal.text and result["reviewed"] == {"personal": 1}
    assert led.require_personal_signal(sid)["signal_id"] == sid


def test_fact_with_two_selected_signals_is_offered_once(led):
    first, second = signal(led, "m1"), signal(led, "m22")
    fid = led.put_fact(lg.FactInput(
        domain="home", predicate="hvac_service_due", value={"date": "2026-12-01"}, primary_subject_id=OWNER,
        observed_at="2026-11-17T00:00:00Z", confidentiality=Confidentiality.ORDINARY,
        evidence=((first, 0, 4), (second, 0, 4))))
    terminal = Terminal(["1,2", "p", "s", "p"])
    result = run(led, terminal)
    assert terminal.text.count(f"Fact from this Signal: {fid}") == 1
    assert result == {"connector": "home_email", "listed": 2, "reviewed": {"personal": 2}, "skipped": 1}


def test_review_writes_nothing_to_logs(led, caplog):
    import logging
    signal(led, "m1")
    with caplog.at_level(logging.DEBUG):
        run(led, Terminal(["1", "p"]))
    assert CANARY not in caplog.text
