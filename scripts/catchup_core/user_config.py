"""Exact-change configuration review; enrollment never grants consent or release."""
from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from catchup_core.session_lock import require_active_installation, session_lock

SETTINGS: dict[str, tuple[int, int]] = {'bootstrap_days': (1, 3660)}
HELD = frozenset({'cutover', 'viewer', 'consent_required', 'sources', 'subjects', 'extractor_cmd', 'release',
                  'domains', 'household_from_profile', 'snapshot_retention_days', 'retention'})
_HANDLERS = {'gmail': 'google_email', 'outlook_email_personal': 'msgraph_email', 'icloud_email': 'imap_email',
             'google_calendar': 'google_calendar', 'outlook_calendar_personal': 'msgraph_calendar',
             'icloud_calendar': 'caldav_calendar'}
_SOURCE_FIELDS = {'connector', 'account_scope', 'subjects', 'required', 'class', 'kwargs',
                  'calendar_kinds', 'namespace'}
_OPTIONS = {'google_email': {'query', 'label_filter'}, 'msgraph_email': {'folder'}, 'imap_email': {'folder'},
            'google_calendar': {'calendar_ids', 'window_days'},
            'msgraph_calendar': {'calendar_ids', 'window_days'}, 'caldav_calendar': {'calendar_filter', 'window_days'}}
_MAX_BYTES = 1_000_000


class _UniqueLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError('configuration has duplicate or non-text keys')
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _data(text: str) -> dict[str, Any]:
    try:
        if any(isinstance(token, yaml.tokens.AnchorToken | yaml.tokens.AliasToken | yaml.tokens.TagToken)
               for token in yaml.scan(text)):
            raise ValueError('configuration aliases, anchors and explicit tags are not supported')
        data = yaml.load(text, Loader=_UniqueLoader) or {}  # noqa: S506 - SafeLoader subclass rejects aliases/duplicates
    except yaml.YAMLError:
        raise ValueError('configuration YAML cannot be read') from None
    if not isinstance(data, dict):
        raise ValueError('config must be a mapping')
    return data


def _safe_path(path: Path) -> None:
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except OSError:
            raise ValueError('configuration path is missing or unavailable') from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('configuration paths must not contain links')
        if current == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError('configuration must be an ordinary unlinked file')


def _read(path: Path) -> tuple[str, dict[str, Any]]:
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('configuration must be an ordinary unlinked file')
        raw = handle.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError('configuration exceeds its size bound')
    text = raw.decode('utf-8')
    return text, _data(text)


def _checked(key: str, value: str) -> int:
    if key in HELD:
        raise ValueError(f'{key} is held; this workflow cannot change identity, consent or release')
    if key not in SETTINGS:
        raise ValueError(f'unknown setting {key}; use bootstrap_days or source.<declared name>')
    low, high = SETTINGS[key]
    if not re.fullmatch(r'[0-9]{1,6}', value or '') or not low <= int(value) <= high:
        raise ValueError(f'{key} must be a whole number from {low} to {high}')
    return int(value)


def _text(value: Any, label: str, maximum: int = 1024, *, search_text: bool = False) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or (not search_text and '://' in value)):
        raise ValueError(f'{label} requires bounded text without URLs or control characters')
    return value


def _options(handler: str, value: Any) -> None:
    if not isinstance(value, dict) or set(value) - (_OPTIONS[handler] | {'max_results', 'sync_changes'}):
        raise ValueError('unknown source options; credentials, paths, endpoints and cursors are held')
    for key, option in value.items():
        if key in {'max_results', 'window_days'}:
            maximum = 1000 if key == 'max_results' else 366
            if type(option) is not int or not 1 <= option <= maximum:
                raise ValueError(f'{key} requires an integer from 1 to {maximum}')
        elif key == 'sync_changes':
            if type(option) is not bool or option:
                raise ValueError('sync_changes requires false; true is held until separately accepted provider parity')
        elif key in {'calendar_ids', 'calendar_filter'}:
            if (not isinstance(option, list) or not 1 <= len(option) <= 64
                    or any(not isinstance(item, str) for item in option) or len(set(option)) != len(option)):
                raise ValueError('calendar selectors require a bounded distinct list')
            for item in option:
                _text(item, key, 256)
                if any(char in item for char in '/\\?#%'):
                    raise ValueError('calendar selectors must be stable IDs or names, not URLs or paths')
        else:
            _text(option, key, 4096 if key == 'query' else 256, search_text=key == 'query')
            if key == 'folder' and not re.fullmatch(r'[A-Za-z0-9_. -]{1,120}', option):
                raise ValueError('folder needs a plain provider mailbox name')


def _source(  # noqa: PLR0912, PLR0915 - explicit source enrollment refusals
    path: Path, data: dict[str, Any], name: str, value: str, known: frozenset[str] | None,
            required: frozenset[str]) -> tuple[Any, dict[str, str]]:
    from catchup_core.domain_pack import load_pack_file  # noqa: PLC0415
    from catchup_core.runtime import RuntimeConfigError, _validate_runtime_sources, load_runtime_config  # noqa: PLC0415
    if not known or not required <= known or any(not isinstance(subject, str) or not subject for subject in known):
        raise ValueError('source review requires current resolved household identities')
    runtime_config = load_runtime_config(path)
    root = path.parent.parent
    domains = runtime_config.domains
    dependencies: dict[str, str] = {}
    declared: set[str] = set()
    classes: set[str] = set()
    for domain in domains:
        pack_path = root / f'scripts/catchup_core/packs/{domain}.yaml'
        raw, _ = _read(pack_path)
        dependencies[str(pack_path)] = hashlib.sha256(raw.encode()).hexdigest()
        pack = load_pack_file(pack_path)
        declared.update(source.connector for source in pack.sources)
        classes.update(token for predicate in pack.predicates.values() for token in predicate.authority)
    if name not in declared:
        raise ValueError('source name must be declared by an active Domain pack')
    registry_path = root / 'config/connectors.yaml'
    registry_text, registry = _read(registry_path)
    dependencies[str(registry_path)] = hashlib.sha256(registry_text.encode()).hexdigest()
    if not isinstance(value, str) or len(value.encode('utf-8')) > 65536:
        raise ValueError('source JSON exceeds its size bound')
    try:
        after = json.loads(value, object_pairs_hook=_json_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    except (ValueError, TypeError, RecursionError):
        raise ValueError('source value must be a strict JSON object or null') from None
    removing = after is None
    if removing:
        if name not in data['sources']:
            raise ValueError('the source does not exist; review a current declaration')
        if len(data['sources']) <= 1:
            raise ValueError('cannot remove the last source; runtime requires a nonempty source inventory')
        after = data['sources'][name]
    if not isinstance(after, dict) or set(after) - _SOURCE_FIELDS:
        raise ValueError('unknown source fields; identity, credentials, endpoints and commands are held')
    connector = after.get('connector')
    if not isinstance(connector, str) or connector not in _HANDLERS:
        raise ValueError('source requires a registered personal connector; Work sources remain separate')
    entry = registry.get('connectors', {}).get(connector)
    if (not isinstance(entry, dict) or entry.get('enabled') is not True
            or not isinstance(entry.get('fetch'), dict)
            or entry['fetch'].get('handler') != f'scripts/connectors/{_HANDLERS[connector]}.py'):
        raise ValueError('source connector is disabled or its registered handler is unsupported')
    scope = _text(after.get('account_scope'), 'account_scope', 128)
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+', scope)
            or re.search(r'(^|[_.-])(work|employer|corporate|office)($|[_.-])', scope, re.IGNORECASE)
            or after.get('namespace', 'personal') != 'personal'):
        raise ValueError('Work/employer source enrollment into personal context is held')
    subjects = after.get('subjects')
    if (not isinstance(subjects, list) or not subjects or any(not isinstance(subject, str) for subject in subjects)
            or len(set(subjects)) != len(subjects) or not set(subjects) <= known):
        raise ValueError('source subjects must be an explicit distinct list of resolved household identities')
    if 'class' in after and _text(after['class'], 'class', 128) not in classes:
        raise ValueError('source class is not declared by the active Domain policy')
    handler = _HANDLERS[connector]
    _options(handler, after.get('kwargs', {}))
    if 'calendar_kinds' in after:
        if handler not in {'google_calendar', 'msgraph_calendar', 'caldav_calendar'}:
            raise ValueError('calendar kinds require a calendar connector')
        selectors = after.get('kwargs', {}).get('calendar_ids', after.get('kwargs', {}).get('calendar_filter', []))
        if not isinstance(after['calendar_kinds'], dict) or not set(after['calendar_kinds']) <= set(selectors):
            raise ValueError('calendar kinds must name explicitly reviewed calendar selectors')
    try:
        _validate_runtime_sources({name: after})
    except RuntimeConfigError as exc:
        raise ValueError(str(exc)) from None
    return None if removing else after, dependencies


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON fields')
        result[key] = value
    return result


def _replace(text: str, key: str, after: Any) -> str:
    document = yaml.compose(text)
    if isinstance(document, yaml.MappingNode):
        for key_node, value_node in document.value:
            if key_node.value == key:
                replacement = str(yaml.safe_dump({key: after}, sort_keys=False, allow_unicode=True))
                if '\r\n' in text:
                    replacement = replacement.replace('\n', '\r\n')
                end = int(value_node.end_mark.index)
                if text[end:end + 2] == '\r\n':
                    end += 2
                elif text[end:end + 1] == '\n':
                    end += 1
                return text[:int(key_node.start_mark.index)] + replacement + text[end:]
    return text + ('' if not text or text.endswith('\n') else '\n') + str(yaml.safe_dump({key: after}, sort_keys=False))


def _review_context(callback: Callable[[], Mapping[str, Any]] | None, *, required: bool) -> dict[str, Any] | None:
    if callback is None and not required:
        return None
    if not callable(callback):
        raise ValueError('source review requires a current authority context callback')
    context = callback()
    if not isinstance(context, Mapping) or set(context) != {'actor', 'owner', 'permission_generation', 'cohort'}:
        raise ValueError('review context requires actor, owner, permission_generation and cohort')
    actor, owner = context['actor'], context['owner']
    if (not isinstance(actor, str) or not actor.strip() or actor != actor.strip()
            or not isinstance(owner, str) or not owner.strip() or owner != owner.strip() or actor != owner):
        raise ValueError('review authority requires the current owner actor')
    generation, cohort = context['permission_generation'], context['cohort']
    if type(generation) is not int or generation < 0:
        raise ValueError('review permission_generation requires a current nonnegative integer')
    if (not isinstance(cohort, Mapping) or not cohort
            or any(not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
                   for key, value in cohort.items())):
        raise ValueError('review cohort requires a nonempty string map')
    return {'actor': actor, 'owner': owner, 'permission_generation': generation, 'cohort': dict(cohort)}


def _change(path: Path, key: str, value: str, known: frozenset[str] | None, required: frozenset[str],
            review_context: Callable[[], Mapping[str, Any]] | None
            ) -> tuple[dict[str, Any], str, str]:
    text, data = _read(path)
    dependencies: dict[str, str] = {}
    source_name = key.removeprefix('source.') if key.startswith('source.') else None
    if source_name is not None:
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}', source_name):
            raise ValueError('source name must be a declared Domain identifier')
        after, dependencies = _source(path, data, source_name, value, known, required)
        before = data['sources'].get(source_name)
        sources = dict(data['sources'])
        if after is None:
            del sources[source_name]
        else:
            sources[source_name] = after
        updated = _replace(text, 'sources', sources)
    else:
        after = _checked(key, value)
        before = data.get(key)
        updated = _replace(text, key, after)
    changed = _data(updated)
    expected = {**data, **({'sources': sources} if source_name is not None else {key: after})}
    if changed != expected or changed == data or updated == text:
        raise ValueError('configuration has no change or cannot preserve the exact reviewed values')
    context = _review_context(review_context, required=source_name is not None)
    payload = {'path': str(path.absolute()), 'key': key, 'updated': updated, 'dependencies': dependencies,
               'known_subjects': sorted(known or ()), 'consent_subjects': sorted(required)}
    if context is not None:
        payload['review_context'] = context
    file_part = hashlib.sha256(text.encode()).hexdigest()
    digest = file_part + '.' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result = {'key': key, 'before': before, 'after': after, 'applied': False, 'apply_digest': digest,
              'instruction': 'Review this exact change, then repeat with --apply <apply_digest>.',
              'diff': ''.join(difflib.unified_diff(text.splitlines(keepends=True), updated.splitlines(keepends=True),
                                                  fromfile='current', tofile='reviewed', n=0))}
    if source_name is not None:
        result.update(release_invalidated=True, consent_granted=False,
                      consent_required_subjects=sorted(set(after.get('subjects', []) if after else ()) & required))
    return result, text, updated


def propose(path: Path, key: str, value: str, *, known_subjects: frozenset[str] | None = None,
        consent_subjects: frozenset[str] = frozenset(),
        review_context: Callable[[], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    require_active_installation(path)
    return _change(path, key, value, known_subjects, consent_subjects, review_context)[0]


def apply(path: Path, key: str, value: str, digest: str, *, known_subjects: frozenset[str] | None = None,
          consent_subjects: frozenset[str] = frozenset(),
          review_context: Callable[[], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    require_active_installation(path)
    _safe_path(path)
    with session_lock(path.with_name(path.name + '.configure.lock')):
        result, text, updated = _change(path, key, value, known_subjects, consent_subjects, review_context)
        if not isinstance(digest, str) or not hmac.compare_digest(digest, result['apply_digest']):
            raise ValueError('the configuration or reviewed change changed; review again')
        identity = path.stat()
        descriptor, name = tempfile.mkstemp(prefix='.artha-config-', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            current, _ = _read(path)
            final = path.stat()
            rechecked, _, final_updated = _change(path, key, value, known_subjects, consent_subjects, review_context)
            if (current != text or (identity.st_dev, identity.st_ino, identity.st_mtime_ns, identity.st_size)
                    != (final.st_dev, final.st_ino, final.st_mtime_ns, final.st_size)
                    or rechecked['apply_digest'] != digest or final_updated != updated):
                raise ValueError('configuration changed during application; review again')
            os.replace(temporary, path)
            if os.name != 'nt':
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return {**result, 'applied': True}
