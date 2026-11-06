"""Local user-started commands over the same evidence and permission boundary."""
from __future__ import annotations

import hmac
import json
import sqlite3
from argparse import Namespace
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from catchup_core import ids, views
from catchup_core.backup import atomic_bytes, database_bytes, encrypted_snapshot
from catchup_core.coordinator import Deps
from catchup_core.ledger import LedgerError

_CODE_ROOT = Path(__file__).resolve().parents[2]      # where the capability code lives, not the user workspace


def _authorize_export_artifact(deps: Deps, name: str, data: bytes) -> None:
    from catchup_core.ledger import PermissionDenied  # noqa: PLC0415
    from catchup_core.policy import authorize_source  # noqa: PLC0415
    led = deps.ledger
    relative = Path(name)
    if name == "ledger.db":
        return  # All retained Facts, Signals and coverage were checked before capture.
    if relative.parent.name == "blobs":
        if not led.conn.execute("SELECT 1 FROM signals WHERE evidence_blob_ref=?", (relative.stem,)).fetchone():
            raise PermissionDenied("export contains evidence with unknown provenance")
        return
    if led._cipher is None:
        raise PermissionDenied("artifact export requires its encryption key")
    if relative.parent.name == "documents":
        payload = json.loads(led._cipher.decrypt(data, aad=relative.stem.encode()))
        subject = payload.get("subject")
        if (not isinstance(subject, str) or not subject
                or (led._known_subjects is not None and subject not in led._known_subjects)):
            raise PermissionDenied("document export has unknown subject provenance")
        authorize_source(led, (subject,), led._consent_subjects, "documents:manual", deps.viewer,
                         operations=("export",))
        return
    if relative.parent.name == "inbox":
        payload = json.loads(led._cipher.decrypt(data, aad=relative.stem.encode()))
        if payload.get("actor") != deps.viewer or payload.get("id") != relative.stem:
            raise PermissionDenied("capture export has unknown provenance")
        authorize_source(led, tuple(led._consent_subjects), led._consent_subjects,
                         f"capture:{deps.viewer}", deps.viewer, operations=("export",))
        return
    if relative.parent.name == "replay" and led.conn.execute(
            "SELECT 1 FROM replay_cases WHERE feedback_id=?", (relative.stem,)).fetchone():
        return  # These inputs are retained Signals and Facts, already checked above.
    if relative.parent.name == "corpus":
        payload = json.loads(led._cipher.decrypt(data, aad=relative.stem.encode()))
        if payload.get('case_id') != relative.stem or not isinstance(payload.get('signal_id'), str):
            raise PermissionDenied("corpus export has unknown provenance")
        led.require_signal_operation(payload['signal_id'], deps.viewer, 'export')
        return
    raise PermissionDenied("export contains an artifact with unknown provenance")


def _signal_visible(ledger: Any, signal_id: str, viewer: str) -> bool:
    try:
        ledger.require_signal_operation(signal_id, viewer)
    except LedgerError:
        return False
    return True


def execute(args: Namespace, deps: Deps, workspace: Path) -> dict[str, Any]:  # noqa: PLR0911, PLR0912, PLR0915
    # A flat command router: each branch is independent and returns one typed projection.
    deps.validate_inputs()
    led, viewer = deps.ledger, deps.viewer
    from catchup_core.contracts import ExecutionMode  # noqa: PLC0415
    shadow = getattr(args, "shadow", False) or getattr(deps, "mode", None) is ExecutionMode.SHADOW
    if shadow and args.command in ("pause", "resume", "reconcile", "resolve", "activity", "transfer-authority",
                                   "retire-authority"):
        raise LedgerError("shadow mode cannot access or change live action controls")
    if args.command == "consent":
        import sys  # noqa: PLC0415

        from catchup_core import consent_cli  # noqa: PLC0415
        if shadow:
            raise LedgerError("consent is recorded in the live ledger, not in shadow mode")
        if args.action == "list":
            return {"consents": consent_cli.list_consents(led)}
        wanted = (args.subject or "").casefold()
        matches = [sid for sid, name in deps.subject_labels.items() if name.casefold() == wanted]
        if len(matches) != 1 or not args.scope:
            raise LedgerError("consent grant needs --subject (full name) and at least one --scope")
        granted = consent_cli.grant_own_consent(
            led, subject_id=matches[0], subject_name=deps.subject_labels[matches[0]], scopes=args.scope,
            days=args.days, now=datetime.now(UTC), readline=input,
            interactive=sys.stdin.isatty() and sys.stdout.isatty())
        return {"granted": len(granted), "scopes": list(args.scope), "days": args.days}
    if args.command == 'field-review':
        from catchup_core.field_confirmation import field_command  # noqa: PLC0415
        return field_command(args, deps)
    if args.command == 'cancel-extraction':
        if viewer != led._owner or shadow:
            raise LedgerError('extraction cancellation requires the local owner outside shadow mode')
        identity = args.catchup_id
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 512:
            raise ValueError('invalid Catch-up identity')
        with led.transaction() as db:
            db.execute('INSERT OR IGNORE INTO resource_cancellations VALUES(?,?)', (identity, viewer))
        return {'catchup_id': identity, 'extraction_cancelled': True,
                'uncertain_usage_released': False, 'completed_effects_undone': False}
    if args.command == 'usage':
        if viewer != led._owner or shadow:
            raise LedgerError('usage reporting requires the local owner outside shadow mode')
        if deps.admissions is None:
            raise LedgerError('resource policy is not configured; there are no admitted requests to report on')
        deps.admissions.record_usage(args.catchup_id, args.request_key, args.input_tokens, args.output_tokens,
                                     args.source, deps.now(), getattr(args, 'zero_reason', None))
        return {'usage_recorded': True, 'catchup_id': args.catchup_id, 'request_key': args.request_key,
                'unknown_requests': deps.admissions.uncertain_requests(args.catchup_id)}
    if args.command == 'retention':
        from dataclasses import asdict  # noqa: PLC0415

        from catchup_core.retention import RetentionPlanner, RetentionPolicy  # noqa: PLC0415
        if getattr(args, 'apply', None) is not None:
            raise LedgerError('retention application is held until version-scoped expiry and restore replay are proven')
        from catchup_core.runtime import load_runtime_config  # noqa: PLC0415
        policy = RetentionPolicy.from_config(load_runtime_config(workspace / 'config/catchup_core.yaml').policy)
        instant = datetime.fromisoformat(args.at.replace('Z', '+00:00')) if args.at else deps.now()
        planner = RetentionPlanner(led, policy, viewer, instant, led._path.parent)
        preview = planner.preview()
        return {**asdict(preview), 'application_allowed': False,
                'limitation': 'expiry application requires version-scoped history and restore replay'}
    if args.command == "interchange":
        from catchup_core import interchange  # noqa: PLC0415
        path = Path(args.path).expanduser()
        if getattr(args, "read", False):
            if led._cipher is None:
                raise LedgerError("interchange requires the encryption key")
            document = interchange.read_interchange(path, led._cipher)
            return {"facts": len(document["facts"]), "omitted_facts": document["omitted_facts"],
                    "executes_nothing": True, "dispatchable": False}
        return interchange.write_interchange(path, led, viewer)
    if args.command == "emergency":
        from catchup_core import interchange  # noqa: PLC0415
        return interchange.emergency_view(led, viewer)
    if args.command == "configure":
        from catchup_core import user_config  # noqa: PLC0415
        from catchup_core.policy import evaluation_cohort  # noqa: PLC0415
        from catchup_core.runtime import load_runtime_config  # noqa: PLC0415
        config_path = workspace / "config" / "catchup_core.yaml"
        source_change = args.key.startswith('source.')
        if source_change and (viewer != led._owner or shadow):
            raise LedgerError('source configuration requires the local owner outside shadow mode')
        def review_context() -> dict[str, Any]:
            deps.validate_inputs()
            config = load_runtime_config(config_path)
            cohort = evaluation_cohort(config.policy, config.domains, workspace=workspace)
            return {'actor': deps.viewer, 'owner': led._owner,
                    'permission_generation': led._permission_generation(), 'cohort': cohort}
        if getattr(args, "apply", None) is None:
            return user_config.propose(config_path, args.key, args.value, known_subjects=led._known_subjects,
                                       consent_subjects=led._consent_subjects, review_context=review_context)
        if viewer != led._owner or shadow:
            raise LedgerError("configuration changes require the local owner outside shadow mode")
        return user_config.apply(config_path, args.key, args.value, args.apply, known_subjects=led._known_subjects,
                                 consent_subjects=led._consent_subjects, review_context=review_context)
    if args.command == "beliefs":
        return {"beliefs": views.beliefs(led, viewer, domain=args.domain, subject=args.subject)}
    if args.command == "why":
        return views.safe_projection(views.explain(led, deps.blobs, viewer, args.fact_id, as_of=args.as_of))
    if args.command == "forget":
        # The authenticated local owner can remove retained data after consent is revoked.
        if viewer != led._owner:
            raise LedgerError("source deletion requires the local owner")
        receipt = led.forget(args.connector, args.scope, args.upstream)
        return {"deleted_signals": receipt.deleted_signals, "deleted_facts": receipt.deleted_facts,
                "deleted_alerts": receipt.deleted_alerts, "minimal_audit": receipt.minimal_audit,
                "encrypted_backups_pending_expiry": receipt.backup_copies_pending_expiry}
    if args.command == "feedback":
        now = deps.now()
        fid = views.feedback(led, viewer, args.obligation, args.response, args.reason, now, days=args.days)
        case = views.replay_case(led, deps.blobs, viewer, args.obligation, args.response, args.reason, fid, now,
                                 led._path.parent / "replay")
        return {"feedback_id": fid, "replay_case": case.name, "expected_outcome": "pending_review"}
    if args.command == "correct":
        fact = led.require_fact_operation(args.fact_id, viewer, "correct")
        predicate = deps.pack.predicates[fact.predicate]
        from catchup_core.extract import _typed_value  # noqa: PLC0415
        typed = _typed_value(predicate, args.value)
        value = typed.isoformat() if hasattr(typed, "isoformat") else typed
        if args.apply is None:
            return {"before": fact.value, "after": value, "apply_version": fact.recorded_at,
                    "instruction": "Review this diff, then repeat with --apply <apply_version>."}
        if args.apply != fact.recorded_at:
            raise ValueError("Fact changed since the correction preview")
        # Capture the old evidence before the correction replaces its active spans.
        correction_key = f"correction:{fact.fact_id}:{fact.recorded_at}"
        led.upsert_alert(correction_key, domain=fact.domain, primary_subject_id=fact.primary_subject_id,
                         severity="P4", fact_ids=(fact.fact_id,))
        fid = views.feedback(led, viewer, correction_key, "useful", "Explicit correction", deps.now())
        views.replay_case(led, deps.blobs, viewer, correction_key, "correct", "Explicit correction", fid,
                          deps.now(), led._path.parent / "replay")
        with led.transaction() as db:
            db.execute("UPDATE feedback SET response='correct' WHERE feedback_id=?", (fid,))
        views.correct(led, viewer, fact.fact_id, value, args.apply)
        return {"corrected": fact.fact_id, "provenance": "user_confirmed"}
    if args.command == "complete":
        return _confirm_completion(args, deps, workspace)
    if args.command == "health":
        return {"integrity": led.integrity_check(), "schema": led.schema_version(),
            "capabilities": _capability_states(led, workspace, deps),
            "performance": _performance(led, workspace, implementation_version=deps.input_version),
            "coverage": [{"connector": r["connector"], "status": r["status"],
                          "last_verified": r["completed_at"]} for r in led.visible_coverage(viewer)
                         if not deps.expected_scopes or deps.expected_scopes.get(r['connector']) == r['account_scope']],
            "uninterpreted_signals": sum(_signal_visible(led, sid, viewer) for sid in led.unresolved_signals()),
            "feedback": views.precision(led, viewer),
            "reviewed_alert_classes": views.class_precision(led, viewer)}
    if args.command == "review":
        return {"review": views.review_feedback(led, viewer, led._path.parent / "replay",
                                                args.feedback_id, args.label, deps.now())}
    if args.command == "history":
        return {"briefings": [dict(r) for r in led.conn.execute(
            "SELECT b.catchup_id,b.version,b.published_at,b.status,(SELECT count(*) FROM correction_notices n "
            "WHERE n.catchup_id=b.catchup_id) AS correction_notices "
            "FROM briefings b ORDER BY published_at DESC LIMIT 30")]}
    if args.command == "export":
        permission_generation = database_bytes(led._path)
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        for row in led.conn.execute("SELECT fact_id,primary_subject_id,domain FROM facts"):
            led.get_fact(row["fact_id"], viewer)
            authorize_source(led, (row["primary_subject_id"],), led._consent_subjects,
                             row["domain"], viewer, operations=("export",))
            for co_subject in led.conn.execute("SELECT subject_id FROM fact_subjects WHERE fact_id=?",
                                               (row["fact_id"],)):
                authorize_source(led, (co_subject[0],), led._consent_subjects,
                                 row["domain"], viewer, operations=("export",))
        for row in led.conn.execute("SELECT signal_id FROM signals"):
            led.require_signal_operation(row[0], viewer, 'export')
        for row in led.coverage_rows():
            attribution = led.conn.execute("SELECT subjects FROM source_access WHERE connector=? AND account_scope=?",
                                          (row['connector'], row['account_scope'])).fetchone()
            if not attribution or not json.loads(attribution[0]):
                raise LedgerError("export contains coverage with unknown subject provenance")
            authorize_source(led, json.loads(attribution[0]), led._consent_subjects,
                             f"{row['connector']}:{row['account_scope']}", viewer, operations=("export",))
        cipher = led._cipher
        if cipher is None:
            raise LedgerError("export requires the encryption key")
        path = Path(args.path).expanduser()
        encrypted_snapshot(path, led._path, cipher, blobs=deps.blobs._root,
                           authorize=lambda name, data: _authorize_export_artifact(deps, name, data),
                           guard=lambda: database_bytes(led._path) == permission_generation)
        return {"export": str(path), "format": "artha.snapshot.v1", "dispatchable": False}
    if args.command == "capture":
        cipher = led._cipher
        if cipher is None:
            raise LedgerError("capture requires the encryption key")
        capture_id = ids.new_id("cap")
        payload = json.dumps({"id": capture_id, "text": args.text, "actor": viewer,
                              "observed_at": deps.now().isoformat()}).encode()
        atomic_bytes(led._path.parent / "inbox" / f"{capture_id}.ae1",
                     cipher.encrypt(payload, aad=capture_id.encode())[0])
        return {"captured": capture_id, "next": "Reviewed during the next Catch-up."}
    if args.command == "ingest":
        from catchup_core.documents import ingest  # noqa: PLC0415
        names = [sid for sid, name in deps.subject_labels.items() if name.casefold() == args.subject.casefold()]
        subject = names[0] if len(names) == 1 else args.subject
        return {"document": ingest(Path(args.path).expanduser(), subject, led, viewer, deps.now()),
                "next": "Page evidence will be reviewed at the next Catch-up; unread pages remain unverified."}
    if args.command in ("items", "goals"):
        from catchup_core.curated import goal_open_items, read_authorized_state, records  # noqa: PLC0415
        from catchup_core.ledger import PermissionDenied  # noqa: PLC0415
        generation = led._permission_generation()
        name = "open_items" if args.command == "items" else "goals"
        text = read_authorized_state(workspace / 'state', name, led, viewer, deps.subjects)
        authored = records(text, name)
        if name == "goals":
            from catchup_core.runtime import _goal_lines  # noqa: PLC0415
            linked = goal_open_items(workspace / 'state', led, viewer, authored)
            lines = _goal_lines(workspace / "state", reader=lambda _name: text,
                                ledger=led, viewer=viewer, now=deps.now(),
                                today=deps.now().date(), persist=False,
                                weekly=getattr(args, 'weekly', False), open_items=linked,
                                required_sources=tuple(s.connector for s in deps.pack.sources if s.required))
            result = {"goals": authored, "observations": list(lines.lines), "gaps": list(lines.gaps)}
            if getattr(args, 'detail', False):
                result['evidence_versions'] = dict(lines.fact_versions)
        else:
            result = {args.command: authored, 'accountable_owner': led._owner}
        deps.validate_inputs()
        current = read_authorized_state(workspace / 'state', name, led, viewer, deps.subjects)
        if current != text or generation != led._permission_generation():
            raise PermissionDenied('curated records or permissions changed during query; retry')
        if name == 'goals':
            if linked != goal_open_items(workspace / 'state', led, viewer, authored):
                raise PermissionDenied('linked Open Items changed during query; retry')
            if any(led.get_fact(fid, viewer).recorded_at != version for fid, version in lines.fact_versions.items()):
                raise PermissionDenied('Goal evidence changed during query; retry')
        return result
    if args.command == "activity":
        from catchup_core.runtime import action_database_path  # noqa: PLC0415
        path = action_database_path(workspace)
        paused = (workspace / "config" / "actions.paused").exists()
        if not path.is_file():
            return {"activity": [], "actions_paused": paused}
        from catchup_core.action_mapping import describe  # noqa: PLC0415
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM actions ORDER BY created_at DESC LIMIT 50").fetchall()
        return {"activity": [describe(dict(r)) for r in rows], "actions_paused": paused}
    if args.command in ("reconcile", "resolve"):
        return _action_resolution(args, viewer, workspace)
    if args.command in ("pause", "resume"):
        path = workspace / "config" / "actions.paused"
        if args.command == "pause":
            atomic_bytes(path, b"Actions paused by the local owner.\n")
        else:
            from catchup_core.authority_transfer import resume_effects  # noqa: PLC0415
            resume_effects(led, workspace, actor=viewer)
        return {"actions_paused": path.exists()}
    if args.command == "transfer-authority":
        return _transfer_authority(args, deps, workspace)
    if args.command == "retire-authority":
        return _retire_authority(args, deps, workspace)
    raise ValueError("unsupported command")


def _transfer_authority(args: Namespace, deps: Deps, workspace: Path) -> dict[str, Any]:
    led, viewer = deps.ledger, deps.viewer
    return led.transfer_authority(
        args.new_host, actor=viewer, checkpoint_path=Path(args.checkpoint).expanduser(),
        workspace=workspace, expected_generation=args.expected_generation, cipher=led._cipher,
        former_host_quiesced=getattr(args, "former_quiesced", False),
        credentials_revoked=getattr(args, "credentials_revoked", False),
        retirement_receipt=getattr(args, "retirement_receipt", None),
        former_host_key_fingerprint=getattr(args, "former_host_key", None),
    )


def _retire_authority(args: Namespace, deps: Deps, workspace: Path) -> dict[str, Any]:
    import base64  # noqa: PLC0415

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    from catchup_core.authority_transfer import issue_retirement_receipt, key_fingerprint  # noqa: PLC0415
    from catchup_core.backup import atomic_bytes as write_bytes  # noqa: PLC0415
    receipt = issue_retirement_receipt(deps.ledger, workspace, actor=deps.viewer, new_host=args.new_host,
                                       checkpoint_path=Path(args.checkpoint).expanduser())
    write_bytes(Path(args.output).expanduser(), json.dumps(receipt, sort_keys=True).encode("utf-8"))
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(receipt["public_key"]))
    return {"retired": True, "former_host_key_fingerprint": key_fingerprint(key), "receipt": str(args.output)}


def _completion_record(args: Namespace, deps: Deps, workspace: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    from catchup_core.curated import milestone_record, read_authorized_state, records  # noqa: PLC0415
    led, viewer = deps.ledger, deps.viewer
    parts = args.record.split(':')
    files = {'milestone': 'goals', 'responsibility': 'responsibilities', 'household': 'household_tasks',
             'open_item': 'open_items'}
    if parts[0] not in files or len(parts) != (3 if parts[0] == 'milestone' else 2):
        raise ValueError('use milestone:<goal>:<item>, responsibility:<id>, household:<id> or open_item:<id>')
    name = files[parts[0]]
    text = read_authorized_state(workspace / 'state', name, led, viewer, deps.subjects,
                                include_closed=parts[0] == 'open_item')
    found = [r for r in records(text, name, include_closed=parts[0] == 'open_item') if r.get('id') == parts[1]]
    if len(found) != 1:
        raise ValueError('commitment identity is missing or ambiguous')
    parent = record = found[0]
    if parts[0] == 'milestone':
        children = parent.get('milestones')
        if not isinstance(children, list):
            raise ValueError('milestones need review')
        found = [r for r in children if isinstance(r, dict) and r.get('id') == parts[2]]
        if len(found) != 1:
            raise ValueError('milestone identity is missing or ambiguous')
        record = milestone_record(parent, found[0])
    return parts[0], record, parent


def _confirm_completion(args: Namespace, deps: Deps, workspace: Path) -> dict[str, Any]:
    """Preview and confirm or withdraw exact, version-bound owner evidence; authored fields stay unchanged."""
    from catchup_core.contracts import FactStatus, Provenance  # noqa: PLC0415
    from catchup_core.curated import completion_key, completion_subjects, open_item_subjects  # noqa: PLC0415
    from catchup_core.ledger import FactInput, PermissionDenied  # noqa: PLC0415
    from catchup_core.policy import authorize_source  # noqa: PLC0415
    led, viewer = deps.ledger, deps.viewer
    kind, record, parent = _completion_record(args, deps, workspace)
    record_owner = record.get('owner', parent.get('owner'))
    owner = led._owner if kind == 'open_item' else record_owner
    if owner != viewer:
        raise PermissionDenied('only the accountable owner may confirm completion')
    subject, co_subjects = (open_item_subjects(record, led._owner, tuple(set(deps.subjects) | led._consent_subjects))
                           if kind == 'open_item'
                           else completion_subjects(record, owner))
    if led._known_subjects is not None and not {subject, *co_subjects} <= led._known_subjects:
        raise PermissionDenied('completion subject is unresolved')
    domain = str(record.get('domain') or parent.get('domain') or deps.pack.domain)
    if kind == 'open_item':
        item_domain = record.get('domain') or record.get('source_domain')
        if (not isinstance(item_domain, str) or not item_domain.strip()
                or ('domain' in record and 'source_domain' in record and record['domain'] != record['source_domain'])):
            raise ValueError('Open Item Domain needs review')
        domain = item_domain
    authorize_source(led, (subject, *co_subjects), led._consent_subjects, domain, viewer,
                     operations=('correct', 'view'))
    key = completion_key(kind, record)
    reopening = bool(getattr(args, 'reopen', False))
    stored = led.conn.execute("SELECT fact_id FROM facts WHERE predicate='commitment_completed' "
        "AND obligation_key=? ORDER BY recorded_at DESC LIMIT 1", (key,)).fetchone()
    prior = led.get_fact(stored[0], viewer) if stored else None
    if reopening and prior is None:
        raise ValueError('no completion is recorded for this commitment')
    now = int(deps.now().timestamp())
    generation = led._permission_generation()
    def signature(stamp: int) -> str:
        payload = json.dumps([key, viewer, subject, co_subjects, domain, generation, stamp, reopening,
                              prior.recorded_at if prior else None])
        return ids.evidence_id(deps.id_key, payload)
    if args.apply is None:
        return {'record': record, 'accountable_owner': owner, 'before': prior.status if prior else 'unverified',
                'after': 'completion withdrawn' if reopening else 'completed by explicit owner confirmation',
                'apply_token': f'{now}:{signature(now)}', 'expires_in_seconds': 1800}
    receipt = ids.evidence_id(deps.id_key, args.apply)
    state = FactStatus.RETRACTED if reopening else FactStatus.CURRENT
    replay = bool(prior and isinstance(prior.value, dict) and prior.status == state.value
        and prior.value.get('review_receipt') == receipt and prior.value.get('permission_generation') == generation
        and prior.primary_subject_id == subject and set(co_subjects) == {row[0] for row in led.conn.execute(
            "SELECT subject_id FROM fact_subjects WHERE fact_id=?", (prior.fact_id,))})
    try:
        stamp_raw, mac = args.apply.split(':', 1)
        stamp = int(stamp_raw)
        valid = 0 <= now - stamp <= 1800 and (replay or hmac.compare_digest(mac, signature(stamp)))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('commitment, evidence or permissions changed, or preview expired; review again')
    if replay and prior is not None:
        fid = prior.fact_id
    else:
        fid = led.put_fact(FactInput(domain, 'commitment_completed',
            {'record_key': key, 'completed': True, 'actor': viewer,
             'review_receipt': receipt, 'permission_generation': generation}, subject, deps.now().isoformat(),
            provenance=Provenance.USER_CONFIRMED, status=state, obligation_key=key,
            co_subjects=tuple((person, 'co_subject') for person in co_subjects)),
            fact_id=prior.fact_id if prior else None, expected_recorded_at=prior.recorded_at if prior else None,
            expected_permission_generation=generation)
        if prior:
            led.correction_notice(fid, 'Owner revised completion evidence; inspect the current Goal before acting.')
    version = led.get_fact(fid, viewer).recorded_at
    return {'reopened' if reopening else 'completed': args.record, 'completion_reference': f'fact:{fid}@{version}'}


def _capability_states(led: Any, workspace: Path, deps: Deps) -> list[dict[str, str]]:
    """RA-10: disabled / not configured / unknown / last succeeded / degraded, plus whether measured."""
    from catchup_core.capabilities import runtime_states  # noqa: PLC0415
    from catchup_core.contracts import CatchupRequest, ExecutionMode  # noqa: PLC0415
    from catchup_core.policy import _validate_release_evidence  # noqa: PLC0415
    from catchup_core.runtime import RuntimeConfigError, load_runtime_config  # noqa: PLC0415
    try:
        config = load_runtime_config(workspace / "config" / "catchup_core.yaml")
    except RuntimeConfigError:
        return runtime_states(_CODE_ROOT, {}, led.conn, False, extractor_configured=False)
    try:
        _validate_release_evidence(
            CatchupRequest('health_probe', config.viewer, 'cli', mode=ExecutionMode.AUTHORITATIVE,
                           host=str(config.policy.get('release', {}).get('authority_host', ''))),
            config.policy, domains=config.domains, cohort=deps.evaluation_cohort)
        measured = True
        from catchup_core.policy import _approved_file, verify_rollback  # noqa: PLC0415
        verify_rollback(config.policy, led._cipher)
        scorecard = json.loads(_approved_file(config.policy["release"]["scorecard"], "scorecard"))
        reviewed = [cap for cap, review in scorecard.get("capability_reviews", {}).items()
                    if review.get("owner_signed_at") and review.get("outcome") == "passed"
                    and review.get("case_ids") and set(review["case_ids"]) <= {
                        c["case_id"] for c in scorecard["evaluations"] if c.get("consented_live") is True}]
    except Exception:
        measured = False
        reviewed = []
    return runtime_states(_CODE_ROOT, {"domains": list(config.domains), "reviewed_capabilities": reviewed},
                          led.conn, measured,
                          extractor_configured=True)


def _action_resolution(args: Namespace, viewer: str, workspace: Path) -> dict[str, Any]:
    """Close an ``unknown`` action: ask the provider (`reconcile`) or record a person's decision (`resolve`)."""
    import importlib  # noqa: PLC0415
    import sys  # noqa: PLC0415
    scripts = str(workspace / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    # Loaded dynamically: the incumbent action layer is outside the strictly typed surface.
    executor = importlib.import_module("action_executor").ActionExecutor(workspace)
    result = (executor.reconcile(args.action_id) if args.command == "reconcile"
              else executor.resolve_unknown(args.action_id, args.outcome, viewer))
    return {"action": args.action_id, "status": result.status, "message": result.message}


def _performance(led: Any, workspace: Path, *, implementation_version: str = '') -> dict[str, Any]:
    """Latency, complete-run ratio and an approximate cost from this machine's own runs."""
    from catchup_core.metrics import estimate_cost, read_runs, summarize  # noqa: PLC0415
    from catchup_core.runtime import RuntimeConfigError, load_runtime_config  # noqa: PLC0415
    try:
        runs = read_runs(led._path.parent / "metrics")
        current = ([r for r in runs if r.get('implementation_version') == implementation_version]
                   if implementation_version else list(runs))
        missing = _missing_metrics(led, current, implementation_version) if implementation_version else []
    except (OSError, sqlite3.Error, LedgerError):
        return {'runs': 0, 'verdict': 'metrics unavailable; history cannot be verified',
                'cost': {'history_complete': False, 'estimate': 'unavailable: metrics could not be read'}}
    try:
        pricing = load_runtime_config(workspace / "config" / "catchup_core.yaml").policy.get("pricing")
    except RuntimeConfigError:
        pricing = None
    return {**summarize(current + missing), 'excluded_other_versions': len(runs) - len(current),
            'implementation_version': implementation_version or None,
            "cost": estimate_cost(runs + missing, pricing if isinstance(pricing, dict) else None)}


def _missing_metrics(led: Any, current: list[dict[str, Any]], implementation_version: str) -> list[dict[str, Any]]:
    """A lost whole append remains visible from the pre-work ledger reservation."""
    try:
        oldest = min(datetime.fromisoformat(str(r['at']).replace('Z', '+00:00')) for r in current)
        cutoff = (oldest.astimezone(UTC) - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, KeyError):
        cutoff = ''
    missing = []
    for row in led.conn.execute('SELECT catchup_id,last_sequence FROM metric_sequences '
            'WHERE implementation_version=? AND updated_at>=? ORDER BY updated_at DESC LIMIT 100',
            (implementation_version, cutoff)):
        entries = [r for r in current if r.get('catchup_id') == row['catchup_id']]
        if any(r.get('status') in {'complete', 'degraded', 'blocked'} for r in entries):
            continue  # later replay attempts do not reopen a finished Catch-up
        observed = max((r['metric_sequence'] for r in entries if type(r.get('metric_sequence')) is int), default=0)
        if observed < row['last_sequence']:
            missing.append({'catchup_id': row['catchup_id'], 'implementation_version': implementation_version,
                'mode': entries[0].get('mode', 'unknown') if entries else 'unknown', 'status': 'unknown',
                'metric_sequence': row['last_sequence'], 'resumed': True, 'measurement_complete': False,
                'total_seconds': 0, 'stages': {}})
    return missing
