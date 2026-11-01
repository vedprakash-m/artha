"""
scripts/schemas/agent_context.py
==================================
RD-15: Canonical schema for the context bundle passed to external agents.

IMPORTANT: Adding a new field here requires updating external-registry.yaml
for all agents (add to pii_profile.allow or pii_profile.block).
Run ``python scripts/validate_pii_profiles.py`` to confirm full coverage.

The context bundle is assembled by scripts/lib/prompt_composer.py and
scripts/channel/llm_bridge.py from domain state files, email signals,
and user profile fragments.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical list of PII-sensitive field/category labels that may appear in
# the context bundle sent to external agents.
#
# "coverage" means the field must appear in either allow or block of the
# agent's pii_profile. Fields not listed here are assumed safe and need
# not be explicitly allowed/blocked.
# ---------------------------------------------------------------------------

CONTEXT_BUNDLE_FIELDS: list[str] = [
    # Identity / biometric
    "NAME",                 # user's full or partial name
    "EMAIL_ADDRESS",        # email address
    "PHONE_NUMBER",         # phone number
    "HOME_ADDRESS",         # residential address
    "SSN",                  # social security number
    "PASSPORT_NUMBER",      # passport identifier
    "NATIONAL_ID",          # government-issued national ID

    # Financial
    "SALARY",               # base or total compensation figure
    "BANK_ACCOUNT",         # bank account numbers
    "TAX_ID",               # EIN / personal tax ID
    "CREDIT_CARD",          # payment card numbers

    # Work / professional
    "EMPLOYER_NAME",        # current or past employer
    "HOSTNAME",             # server or device hostname (may reveal infra)
    "CLUSTER_NAME",         # infrastructure cluster identifiers

    # Health
    "HEALTH_CONDITION",     # medical diagnoses, medications, procedures
    "INSURANCE_ID",         # health insurance member ID

    # Location / travel
    "LOCATION",             # current or travel location
    "TRAVEL_DATE",          # departure / arrival dates (reveals itinerary)

    # Communications
    "EMAIL_BODY",           # raw email body text
    "EMAIL_SUBJECT",        # email subject lines

    # Inferred / behavioral
    "CALENDAR_EVENT",       # meeting titles and attendees
    "OPEN_ITEM",            # unresolved action items (may contain PII)
]

# Short documentation note for external agents
COVERAGE_NOTE = (
    "All entries in CONTEXT_BUNDLE_FIELDS must appear in an external agent's "
    "pii_profile.allow or pii_profile.block list. Run "
    "scripts/validate_pii_profiles.py to audit coverage."
)
