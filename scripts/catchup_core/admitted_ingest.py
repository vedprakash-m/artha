"""Metadata-first admitted Connector ingestion for B.8's enrolled source path."""
from __future__ import annotations

import base64
import inspect
import json
import time
from collections.abc import Mapping
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any

from catchup_core import ids
from catchup_core.contracts import CoverageStatus
from catchup_core.coordinator import ConnectorFetch, ConnectorHold, ConnectorRecord
from catchup_core.ledger import Ledger, PersonalScopeUnavailable

GMAIL_RAW_SCHEMA = "artha.gmail.raw.v1"
MAX_GMAIL_RAW_BYTES = 16 * 1024 * 1024
GMAIL_ADMISSION_TIMEOUT_SECONDS = 90.0


def _admission_call(fn: Any, *args: Any, deadline: float, **kwargs: Any) -> Any:
    try:
        accepts_deadline = any(
            parameter.name == "deadline"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in inspect.signature(fn).parameters.values()
        )
    except (TypeError, ValueError):
        accepts_deadline = False
    if accepts_deadline:
        kwargs["deadline"] = deadline
    return fn(*args, **kwargs)


def _addresses(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {address.casefold() for _name, address in getaddresses([value]) if address and "@" in address}


def _domains(addresses: set[str]) -> set[str]:
    return {address.rsplit("@", 1)[1] for address in addresses}


def classify_metadata(  # noqa: PLR0911
        metadata: Mapping[str, Any], policy: Mapping[str, Any], provider_account: str) -> str | None:
    required = ("allowed_sender_domains", "work_label_ids", "work_recipient_domains", "unknown_metadata")
    if any(key not in policy for key in required):
        return "policy_incomplete"
    sender = _addresses(metadata.get("from"))
    recipients = _addresses(metadata.get("to")) | _addresses(metadata.get("cc")) \
        | _addresses(metadata.get("bcc")) | _addresses(metadata.get("delivered_to"))
    labels = metadata.get("labels")
    if (not sender or not recipients or not isinstance(labels, list)
            or not all(isinstance(label, str) and label for label in labels)):
        return "metadata_ambiguous"
    if provider_account.strip().casefold() not in recipients:
        return "recipient_not_owner_account"
    allowed = {str(domain).casefold().lstrip(".") for domain in policy["allowed_sender_domains"]}
    if not allowed or not _domains(sender) <= allowed:
        return "sender_domain_not_enrolled"
    work_labels = {str(label).casefold() for label in policy["work_label_ids"]}
    if {label.casefold() for label in labels} & work_labels:
        return "work_label"
    work_domains = {str(domain).casefold().lstrip(".") for domain in policy["work_recipient_domains"]}
    if work_domains and _domains(recipients) & work_domains:
        return "work_recipient"
    return None


def _metadata_marker(key: ids.HmacKey, metadata: Mapping[str, Any]) -> str:
    material = json.dumps(dict(metadata), sort_keys=True, separators=(",", ":"))
    return ids.content_hmac(key, "gmail-metadata:v1:" + material)


def gmail_raw_record(raw: bytes, metadata: Mapping[str, Any], marker: str) -> str:
    return json.dumps({
        "schema": GMAIL_RAW_SCHEMA,
        "data": base64.b64encode(raw).decode("ascii"),
        "message_id": metadata["id"],
        "raw_marker": marker,
    }, sort_keys=True, separators=(",", ":"))


def decode_gmail_record(envelope: str) -> dict[str, Any]:
    try:
        data = json.loads(envelope)
    except (json.JSONDecodeError, TypeError):
        return {"text": envelope}
    if not isinstance(data, dict) or data.get("schema") != GMAIL_RAW_SCHEMA:
        return {"text": envelope}
    raw = base64.b64decode(str(data["data"]), validate=True)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body_parts = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() in ("text/plain", "text/html") and not part.get_filename():
                try:
                    body_parts.append(part.get_content())
                except (LookupError, UnicodeError):
                    body_parts.append("")
                if part.get_content_type() == "text/plain":
                    break
    else:
        body_parts.append(str(message.get_content()))
    return {
        "id": str(data["message_id"]),
        "subject": str(message.get("Subject", "")),
        "from": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "body": "\n".join(body_parts),
    }


def decode_signal_text(envelope: str) -> str:
    record = decode_gmail_record(envelope)
    if "text" in record:
        return str(record["text"])
    return "\n".join(value for value in (
        str(record.get("subject", "")), str(record.get("body", ""))) if value)


def fetch_gmail(  # noqa: PLR0912,PLR0915
        ledger: Ledger, key: ids.HmacKey, handler: Any, auth: Mapping[str, Any], *,
        source: Mapping[str, Any], enrollment: Mapping[str, Any], since: str,
        max_results: int) -> ConnectorFetch:
    policy = dict(source.get("admission") or {})
    if not policy:
        raise PersonalScopeUnavailable("source admission policy is unavailable")
    source_name = str(source["source_name"])
    deadline = time.monotonic() + GMAIL_ADMISSION_TIMEOUT_SECONDS
    provider_account = _admission_call(
        handler.account_identity, dict(auth), deadline=deadline)
    current = ledger.require_source_enrollment(
        source_name=source_name, connector=str(source["connector"]),
        provider_account=provider_account, account_scope=str(source["account_scope"]),
        query=str(source["query"]), subjects=tuple(source["subjects"]), policy=policy)
    if int(current["enrollment_id"]) != int(enrollment["enrollment_id"]):
        raise PersonalScopeUnavailable("source enrollment changed during intake")
    provider_account_id = str(current["provider_account_id"])
    listing = _admission_call(
        handler.list_message_ids, dict(auth), deadline=deadline,
        query=str(source["query"]), since=since, max_results=max_results)
    if hasattr(listing, "message_ids"):
        listed_ids = list(listing.message_ids)
        cursor = getattr(listing, "cursor", None)
        listing_complete = getattr(listing, "complete", None)
        if type(listing_complete) is not bool:
            listing_complete = None
        listing_truncation = getattr(listing, "truncation_reason", None)
        if not isinstance(listing_truncation, str):
            listing_truncation = None
    else:
        listed_ids, cursor = listing
        listing_complete = None
        listing_truncation = None
    message_ids = list(dict.fromkeys([
        str(row["upstream_id"])
        for row in ledger.coverage_holds(source_name, state="held")
        if row["provider_account_id"] == provider_account_id
    ] + list(listed_ids)))
    records: list[ConnectorRecord] = []
    holds: list[ConnectorHold] = []
    for message_id in message_ids:
        if not isinstance(message_id, str) or not message_id:
            continue
        try:
            metadata = _admission_call(
                handler.message_metadata, dict(auth), message_id, deadline=deadline)
        except (OSError, RuntimeError, TimeoutError, ValueError):
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="metadata_unavailable"))
            continue
        before = _metadata_marker(key, metadata)
        if metadata.get("id") != message_id:
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="metadata_identity_changed"))
            continue
        reason = classify_metadata(metadata, policy, provider_account)
        if reason is not None:
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason=reason))
            continue
        if (type(metadata.get("size")) is not int or int(metadata["size"]) < 0
                or int(metadata["size"]) > MAX_GMAIL_RAW_BYTES):
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="raw_size_limit"))
            continue
        if ledger.is_tombstoned(source_name, str(source["account_scope"]), message_id):
            continue
        try:
            rechecked = ledger.require_source_enrollment(
                source_name=source_name, connector=str(source["connector"]),
                provider_account=provider_account, account_scope=str(source["account_scope"]),
                query=str(source["query"]), subjects=tuple(source["subjects"]), policy=policy)
        except PersonalScopeUnavailable:
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="enrollment_revoked"))
            continue
        if int(rechecked["enrollment_id"]) != int(enrollment["enrollment_id"]):
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="enrollment_changed"))
            continue
        try:
            raw = _admission_call(
                handler.raw_message, dict(auth), message_id, deadline=deadline)
        except (OSError, RuntimeError, TimeoutError, ValueError):
            raw = None
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_GMAIL_RAW_BYTES:
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="raw_size_limit"))
            continue
        try:
            after_metadata = _admission_call(
                handler.message_metadata, dict(auth), message_id, deadline=deadline)
        except (OSError, RuntimeError, TimeoutError, ValueError):
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id,
                reason="classification_metadata_unavailable"))
            continue
        if before != _metadata_marker(key, after_metadata):
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="classification_metadata_changed"))
            continue
        current = ledger.require_source_enrollment(
            source_name=source_name, connector=str(source["connector"]),
            provider_account=provider_account, account_scope=str(source["account_scope"]),
            query=str(source["query"]), subjects=tuple(source["subjects"]), policy=policy)
        if int(current["enrollment_id"]) != int(enrollment["enrollment_id"]):
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="enrollment_changed"))
            continue
        raw_marker = ids.content_hmac(key, raw.decode("latin1"))
        known = ledger.source_content(
            source_name, str(source["account_scope"]), message_id, message_id)
        if known is not None and known != raw_marker:
            holds.append(ConnectorHold(
                source_name=source_name, connector=str(source["connector"]),
                account_scope=str(source["account_scope"]), provider_account_id=provider_account_id,
                upstream_id=message_id, upstream_version=message_id, reason="content_marker_changed"))
            continue
        records.append(ConnectorRecord(
            upstream_id=message_id, text=gmail_raw_record(raw, after_metadata, raw_marker),
            provider_version=message_id, raw_content_hmac=raw_marker,
            occurred_at=str(after_metadata.get("date_iso") or after_metadata.get("date") or ""),
            source_name=source_name, enrollment_id=int(enrollment["enrollment_id"]),
            metadata_marker=before, access_subjects=tuple(source["subjects"]),
            provider_account_id=provider_account_id))
    blind_spots = [f"{hold.upstream_id}:{hold.reason}" for hold in holds]
    if listing_complete is False:
        blind_spots.append(f"listing:{listing_truncation or 'incomplete'}")
    return ConnectorFetch(
        connector=source_name, account_scope=str(source["account_scope"]), records=tuple(records),
        status=CoverageStatus.DEGRADED if holds or listing_complete is False
        else CoverageStatus.COMPLETE,
        blind_spots=tuple(blind_spots), required=True, cursor=cursor, holds=tuple(holds),
        listing_complete=listing_complete, listing_truncation=listing_truncation)
