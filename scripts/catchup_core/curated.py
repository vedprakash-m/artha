"""Read canonical curated records without decrypting a State File to disk."""
from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from catchup_core.ledger import FactView, Ledger, PermissionDenied
from catchup_core.policy import authorize_source

CompletionVerifier = Callable[[Any, str], bool]


def milestone_record(goal: Mapping[str, Any], milestone: Mapping[str, Any]) -> dict[str, Any]:
    return {**milestone, 'goal_id': goal.get('id'), 'goal_owner': goal.get('owner'),
            'goal_subject': goal.get('subject'), 'goal_co_subjects': goal.get('co_subjects', []),
            'goal_domain': goal.get('domain'),
            **({'goal_subject_id': goal['subject_id']} if 'subject_id' in goal else {})}


def completion_subjects(record: Mapping[str, Any], owner: str) -> tuple[str, tuple[str, ...]]:
    """Co-subjects inherit the same correction and disclosure boundary as the primary subject."""
    if 'subject' in record and 'subject_id' in record and record['subject'] != record['subject_id']:
        raise ValueError('completion primary subjects conflict; review the record')
    if (record.get('goal_subject') is not None and 'goal_subject_id' in record
            and record['goal_subject'] != record['goal_subject_id']):
        raise ValueError('parent Goal primary subjects conflict; review the record')
    parent = record.get('goal_subject') or record.get('goal_subject_id')
    subject = record.get('subject') or record.get('subject_id') or parent or owner
    others = record.get('co_subjects', [])
    inherited = record.get('goal_co_subjects', [])
    if (not isinstance(subject, str) or not subject or not isinstance(others, list)
            or not isinstance(inherited, list) or any(not isinstance(v, str) or not v for v in others + inherited)
            or (parent is not None and (not isinstance(parent, str) or not parent))):
        raise ValueError('completion subjects need resolved identities')
    return subject, tuple(sorted(set(others + inherited + ([parent] if parent else [])) - {subject}))


def completion_key(kind: str, record: Mapping[str, Any]) -> str:
    """Bind confirmation to the actual commitment, excluding its changing lifecycle fields."""
    content = {k: v for k, v in record.items() if k not in {'status', 'evidence', 'completion_evidence'}}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
    return f"{kind}:{digest}"


def has_completion_evidence(value: Any, key: str = "", verify: CompletionVerifier | None = None) -> bool:
    """An authored label is a reference to check, not proof of completion."""
    return bool(verify and verify(value, key))


def completion_fact(ledger: Any, viewer: str, reference: Any, key: str) -> Any:
    """Resolve an exact current, permission-filtered explicit confirmation version."""
    from catchup_core.ledger import LedgerError  # noqa: PLC0415
    if isinstance(reference, str) and reference.startswith('fact:') and '@' in reference:
        fid, version = reference[5:].split('@', 1)
    else:
        row = ledger.conn.execute(
            "SELECT fact_id,recorded_at FROM facts WHERE predicate='commitment_completed' "
            "AND obligation_key=? AND status='current' ORDER BY recorded_at DESC LIMIT 1", (key,)).fetchone()
        if row is None:
            return None
        fid, version = row
    try:
        fact = ledger.get_fact(fid, viewer)
    except LedgerError:
        return None
    valid = bool(fact.recorded_at == version and fact.status == 'current'
        and fact.predicate == 'commitment_completed' and fact.provenance_class == 'user_confirmed'
        and isinstance(fact.value, dict) and fact.value.get('record_key') == key
        and fact.value.get('completed') is True)
    return fact if valid else None


def verify_completion(ledger: Any, viewer: str, reference: Any, key: str) -> bool:
    return completion_fact(ledger, viewer, reference, key) is not None


def goal_open_items(directory: Path, ledger: Ledger, viewer: str,
                    goals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Retrieve canonical linked items under current permission, including confirmed/legacy closed records."""
    if not any(isinstance(goal.get('milestones'), list) and any(
            isinstance(item, dict) and 'open_item_ref' in item for item in goal['milestones']) for goal in goals):
        return []
    try:
        text = read_authorized_state(directory, 'open_items', ledger, viewer, include_closed=True)
        return records(text, 'open_items', include_closed=True)
    except (OSError, RuntimeError, ValueError):
        return []  # A missing/unreadable/unauthorized linked record remains unknown to the Goal.


def open_item_links(ledger: Ledger, viewer: str, goals: Sequence[Mapping[str, Any]],
                    items: Sequence[Mapping[str, Any]]) -> tuple[dict[str, bool | None], dict[str, FactView]]:
    """Explicit links reuse one completion only for the same owner, Domain and full subject scope."""
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        by_id.setdefault(str(item.get('id')), []).append(item)
    states: dict[str, bool | None] = {}
    facts: dict[str, FactView] = {}
    for goal in goals:
        children = goal.get('milestones')
        for child in children if isinstance(children, list) else ():
            if not isinstance(child, dict) or 'open_item_ref' not in child:
                continue
            record = milestone_record(goal, child)
            key = completion_key('milestone', record)
            states[key] = None
            reference = child['open_item_ref']
            matches = by_id.get(reference, []) if isinstance(reference, str) else []
            if len(matches) != 1:
                continue
            item = matches[0]
            owner = child.get('owner', goal.get('owner'))
            domain = child.get('domain') or goal.get('domain')
            if (not isinstance(owner, str) or not owner.strip() or owner != item.get('owner')
                    or not isinstance(domain, str) or not domain.strip()
                    or domain != (item.get('domain') or item.get('source_domain'))):
                continue
            try:
                if completion_subjects(record, owner) != completion_subjects(item, owner):
                    continue
            except ValueError:
                continue
            fact = completion_fact(ledger, viewer, item.get('completion_evidence'), completion_key('open_item', item))
            if fact is not None and fact.domain == domain and fact.value.get('actor') == owner:
                states[key] = True
                facts[key] = fact
            elif item.get('status') in {'open', 'pending', 'in_progress', 'waiting', 'blocked', 'deferred'}:
                states[key] = False
    return states, facts


def read_state(directory: Path, name: str) -> str:
    if not re.fullmatch(r"[a-z_]+", name):
        raise ValueError("invalid State File name")
    if (directory / f"{name}.md.age").exists():
        vault = importlib.import_module("foundation").get_age_vault(state_dir=directory)
        return str(vault.read(name))
    path = directory / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError("State File structure could not be interpreted") from exc


def records(text: str, kind: str, *, include_closed: bool = False) -> list[dict[str, Any]]:
    if len(text) > 2 * 1024 * 1024:
        raise ValueError("State File exceeds the review limit; coverage is incomplete")
    if not text:
        return []
    if kind == "open_items":
        output = []
        for block in re.findall(r"(?m)^- id:.*(?:\n(?:  .*|$))*", text):
            row = _yaml(block)
            if (not isinstance(row, list) or not row or not isinstance(row[0], dict)
                    or not isinstance(row[0].get('id'), str) or not row[0]['id'].strip()):
                raise ValueError('Open Item identity needs review; coverage is incomplete')
            if include_closed or row[0].get("status") not in ("done", "closed", "expired", "not_proceeding"):
                output.append(row[0])
        return output
    front = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
    data = _yaml(front.group(1)) if front else _yaml(text)
    if isinstance(data, dict):
        values = data.get(kind, [])
        if isinstance(values, dict) and all(isinstance(value, dict) for value in values.values()):
            return [{"id": key, **value} for key, value in values.items()]
        if isinstance(values, list) and all(isinstance(value, dict) for value in values):
            return list(values)
        raise ValueError("State File records must be objects; coverage is incomplete")
    if data is None:
        return []
    raise ValueError('State File structure needs review; coverage is incomplete')


def _scope_nodes(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Only typed scope-bearing children; free-form prose is never interpreted as identity metadata."""
    nodes = [record]
    metric = record.get('metric')
    if isinstance(metric, dict):
        nodes.append(metric)
    milestones = record.get('milestones')
    if isinstance(milestones, list):
        nodes.extend(child for child in milestones if isinstance(child, dict))
    return nodes


def _audience_visible(node: Mapping[str, Any], viewer: str) -> bool:
    if 'visible_to' in node:
        audience = node['visible_to']
        if not isinstance(audience, list) or any(not isinstance(v, str) or not v.strip() for v in audience):
            raise ValueError('curated record scope needs review')
        if viewer not in audience:
            return False
    return True


def _node_visible(node: Mapping[str, Any], ledger: Ledger, viewer: str, scope: str) -> bool:
    if not _audience_visible(node, viewer):
        return False
    subjects: set[str] = set()
    for field in ('subject', 'subject_id', 'owner'):
        if field in node:
            value = node[field]
            if field == 'owner' and isinstance(value, str) and not value.strip():
                continue  # Missing accountability stays visible when the subject scope is resolved.
            if not isinstance(value, str) or not value.strip():
                raise ValueError('curated record scope needs review')
            subjects.add(value)
    others = node.get('co_subjects', [])
    if not isinstance(others, list) or any(not isinstance(v, str) or not v.strip() for v in others):
        raise ValueError('curated record scope needs review')
    subjects.update(others)
    if ledger._known_subjects is not None and not subjects <= ledger._known_subjects | {ledger._owner}:
        raise PermissionDenied('curated record scope needs review')
    try:
        authorize_source(ledger, sorted(subjects), ledger._consent_subjects, scope, viewer)
    except PermissionDenied:
        return False
    return True


def _record_visible(record: Mapping[str, Any], ledger: Ledger, viewer: str, scope: str) -> bool:
    # Reject an unavailable parent's scope before inspecting private child metadata or consuming review bounds.
    return all(_node_visible(node, ledger, viewer, scope) for node in _scope_nodes(record))


def read_authorized_state(directory: Path, name: str, ledger: Ledger, viewer: str,
                          subjects: Sequence[str] = (), *, include_closed: bool = False) -> str:
    """Retrieve only allowed typed records inside the trusted kernel; canonical State Files stay unchanged.

    Explicit scopes are owner-authored metadata, not inferred from prose. Legacy untagged records retain whole-file
    authorization. A Goal and its nested metric/milestones are one permission unit; Sprints inherit that unit.
    """
    generation = ledger._permission_generation()
    scope = f'curated:{name}'
    fallback = tuple(set(subjects) | ledger._consent_subjects)
    if name not in {'goals', 'responsibilities', 'household_tasks', 'open_items'}:
        authorize_source(ledger, fallback, ledger._consent_subjects, scope, viewer)
        projection = read_state(directory, name)
    else:
        text = read_state(directory, name)
        all_authored = records(text, name, include_closed=include_closed)
        authored = [row for row in all_authored if _audience_visible(row, viewer)]
        if any('subject' not in row and 'subject_id' not in row
               and isinstance(row.get('owner', ''), str) and not row.get('owner', '').strip() for row in authored):
            authorize_source(ledger, fallback, ledger._consent_subjects, scope, viewer)
        allowed = [row for row in authored if _record_visible(row, ledger, viewer, scope)]
        if name == 'open_items':
            projected = []
            for row in allowed:
                fact = completion_fact(ledger, viewer, row.get('completion_evidence'), completion_key('open_item', row))
                projected.append({**row, 'status': 'done',
                                  'completion_evidence': f'fact:{fact.fact_id}@{fact.recorded_at}'} if fact else row)
            # Preserve the canonical parser's id-first block convention.
            projection = yaml.safe_dump([{'id': row.get('id'), **row} for row in projected], sort_keys=False)
        else:
            result = {name: allowed}
            if name == 'goals':
                result['sprints'] = _scoped_sprints(text, all_authored, allowed, ledger, viewer, fallback)
            projection = yaml.safe_dump(result, sort_keys=False)
    if generation != ledger._permission_generation():
        raise PermissionDenied('curated permissions changed during retrieval; retry')
    return projection


def _scoped_sprints(text: str, goals: list[dict[str, Any]], allowed: list[dict[str, Any]],
                    ledger: Ledger, viewer: str, fallback: Sequence[str]) -> list[dict[str, Any]]:
    all_ids = {row['id'] for row in goals if isinstance(row.get('id'), str)}
    visible_ids = {row['id'] for row in allowed if isinstance(row.get('id'), str)}
    output = []
    for row in records(text, 'sprints'):
        ref = row.get('goal_ref')
        if isinstance(ref, str) and ref in all_ids:
            if ref not in visible_ids:
                continue
        else:
            # No resolvable parent scope: keep legacy authorization and preserve the Sprint review gap.
            authorize_source(ledger, fallback, ledger._consent_subjects, 'curated:goals', viewer)
        if _record_visible(row, ledger, viewer, 'curated:goals'):
            output.append(row)
    return output
