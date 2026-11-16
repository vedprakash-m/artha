"""Preserve executable evaluator bytes, independently of the mutable candidate checkout."""
from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

from catchup_core.renewal_inputs import SourceFile
from catchup_core.renewal_store import ArtifactStore, RenewalError, _chain, private_directory
from catchup_core.renewal_workspace import source_file


def preserve(store: ArtifactStore, files: Mapping[str, bytes], expected: Mapping[str, str]) -> str:
    if not expected or set(files) != set(expected):
        raise RenewalError('approved evaluator custody is incomplete')
    records = {}
    for path, raw in files.items():
        SourceFile(path, expected[path], '100644')
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != expected[path]:
            raise RenewalError('approved evaluator bytes differ from the baseline')
        records[path] = store.put({'schema': 'artha.renewal-code.v1',
                                   'bytes': base64.b64encode(raw).decode('ascii')})
    return store.put({'schema': 'artha.renewal-custody.v1', 'files': records})


def restore(store: ArtifactStore, identity: str, expected: Mapping[str, str], directory: Path) -> Path:
    record = store.read(identity)
    if (set(record) != {'schema', 'files'} or record['schema'] != 'artha.renewal-custody.v1'
            or not isinstance(record['files'], dict) or set(record['files']) != set(expected)):
        raise RenewalError('approved evaluator custody is incomplete')
    _chain(directory)
    private_directory(directory, create=True)
    for name, sha256 in expected.items():
        SourceFile(name, sha256, '100644')
        value = store.read(record['files'][name])
        try:
            if set(value) != {'schema', 'bytes'} or value['schema'] != 'artha.renewal-code.v1':
                raise ValueError
            raw = base64.b64decode(value['bytes'], validate=True)
            if hashlib.sha256(raw).hexdigest() != sha256:
                raise ValueError
        except (ValueError, TypeError):
            raise RenewalError('approved evaluator artifact is invalid') from None
        path = directory / name
        _chain(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.exists():
            # An interrupted write is never overwritten or accepted without its pinned digest.
            with path.open('xb') as handle:
                os.chmod(path, 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        if source_file(path, name).sha256 != sha256:
            raise RenewalError('approved evaluator cache changed')
    return directory
