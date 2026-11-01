"""An adult household member records their own consent, in person (CN-01..CN-08)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from catchup_core import consent_cli, crypto, ids
from catchup_core import ledger as lg
from catchup_core.ledger import PermissionDenied

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
SCOPES = ("outlook_inbox:personal", "school_email:personal")


@pytest.fixture
def ledger(tmp_path):
    led = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="owner",
                    consent_subjects=frozenset({"adult"})).open()
    led.migrate()
    yield led
    led.close()


def grant(ledger, typed="Adult Example", *, interactive=True, subject="adult", days=90, scopes=SCOPES):
    return consent_cli.grant_own_consent(
        ledger, subject_id=subject, subject_name="Adult Example", scopes=scopes, days=days, now=NOW,
        readline=lambda prompt: typed, interactive=interactive)


def test_typing_their_own_name_records_a_grant_per_source(ledger):
    ids_ = grant(ledger)
    assert len(ids_) == 2                                                                            # CN-01
    for scope in SCOPES:
        for operation in ("ingest", "disclose", "view"):
            assert ledger.consent_active("adult", operation, "owner", scope=scope)


def test_the_prompt_describes_curated_records_and_sources_as_what_they_are(ledger):
    seen = []
    consent_cli.grant_own_consent(
        ledger, subject_id="adult", subject_name="Adult Example", scopes=("curated:open_items",), days=30, now=NOW,
        readline=lambda prompt: seen.append(prompt) or "Adult Example", interactive=True)
    assert "open items" in seen[0] and "mail" not in seen[0] and "calendar" not in seen[0]
    seen.clear()
    consent_cli.grant_own_consent(
        ledger, subject_id="adult", subject_name="Adult Example", scopes=("outlook_inbox:personal", "curated:open_items"),
        days=30, now=NOW, readline=lambda prompt: seen.append(prompt) or "Adult Example", interactive=True)
    assert "mail and calendar items" in seen[0] and "outlook_inbox:personal" in seen[0] and "open items" in seen[0]


def test_the_grant_is_made_by_the_subject_not_the_owner(ledger):
    grant(ledger)
    rows = [dict(r) for r in ledger.conn.execute("SELECT subject_id, grantor_id, audience FROM consent")]
    assert rows and all(r["grantor_id"] == "adult" == r["subject_id"] and r["audience"] == "owner" for r in rows)  # CN-02


@pytest.mark.parametrize("typed", ["", "adult", "Other Person", "yes", "I consent"])
def test_anything_but_their_name_records_nothing(ledger, typed):
    with pytest.raises(PermissionDenied, match="name"):
        grant(ledger, typed)
    assert ledger.conn.execute("SELECT COUNT(*) FROM consent").fetchone()[0] == 0                    # CN-03


def test_the_name_match_ignores_case_and_surrounding_space(ledger):
    assert grant(ledger, "  adult EXAMPLE ")                                                           # CN-04


def test_a_non_interactive_session_cannot_grant(ledger):
    with pytest.raises(PermissionDenied, match="interactive"):
        grant(ledger, interactive=False)                                                              # CN-05
    assert ledger.conn.execute("SELECT COUNT(*) FROM consent").fetchone()[0] == 0


def test_only_a_subject_who_needs_their_own_consent_can_use_it(ledger):
    with pytest.raises(ValueError, match="own consent"):
        grant(ledger, subject="owner")                                                                # CN-06


def test_the_grant_expires_after_the_chosen_days(ledger):
    grant(ledger, days=30)
    expiry = ledger.conn.execute("SELECT expires_at FROM consent").fetchone()[0]
    assert datetime.fromisoformat(expiry) == NOW + timedelta(days=30)                                 # CN-07


def test_listing_shows_state_without_message_content(ledger):
    grant(ledger)
    rows = consent_cli.list_consents(ledger)
    assert {r["scope"] for r in rows} == set(SCOPES) and all(r["revoked"] is False for r in rows)     # CN-08
    assert all(set(r) == {"consent_id", "subject_id", "scope", "operations", "expires_at", "revoked"} for r in rows)


def test_granting_consent_opens_storage_read_write_but_listing_does_not():
    """Given the CLI, a grant writes the ledger so it must not open it read-only; list stays read-only."""
    from argparse import Namespace

    from catchup_core.cli import _mutates
    assert _mutates(Namespace(command="consent", action="grant", apply=None, label=None))
    assert not _mutates(Namespace(command="consent", action="list", apply=None, label=None))
