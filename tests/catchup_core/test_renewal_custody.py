import hashlib

import pytest

from catchup_core.renewal_custody import preserve, restore
from catchup_core.renewal_store import ArtifactStore, RenewalError


def test_AR23_exact_evaluator_bytes_recovered_and_alias_refused(tmp_path):
    store = ArtifactStore(tmp_path / 'store', b'a' * 32)
    code = {'scripts/example.py': b'print("synthetic")\n'}
    expected = {'scripts/example.py': hashlib.sha256(code['scripts/example.py']).hexdigest()}
    identity = preserve(store, code, expected)
    directory = restore(store, identity, expected, tmp_path / 'runner')
    assert (directory / 'scripts/example.py').read_bytes() == code['scripts/example.py']
    (directory / 'scripts/example.py').write_bytes(b'changed')
    with pytest.raises(RenewalError):
        restore(store, identity, expected, directory)


def test_AR23_missing_or_different_baseline_refuses(tmp_path):
    store = ArtifactStore(tmp_path / 'store', b'a' * 32)
    with pytest.raises(RenewalError):
        preserve(store, {'scripts/example.py': b'x'}, {'scripts/example.py': 'a' * 64})


def test_AR23_incomplete_and_invalid_custody_records_refuse(tmp_path):
    import base64
    store = ArtifactStore(tmp_path / 'store', b'a' * 32)
    expected = {'scripts/example.py': hashlib.sha256(b'x').hexdigest()}
    with pytest.raises(RenewalError):
        preserve(store, {}, expected)
    for value in ({'schema': 'unsupported'}, {'schema': 'artha.renewal-code.v1', 'bytes': 'invalid'},
                  {'schema': 'artha.renewal-code.v1', 'bytes': base64.b64encode(b'changed').decode()}):
        record = store.put(value)
        index = store.put({'schema': 'artha.renewal-custody.v1', 'files': {'scripts/example.py': record}})
        with pytest.raises(RenewalError):
            restore(store, index, expected, tmp_path / 'runner')
    invalid = store.put({'schema': 'unsupported'})
    with pytest.raises(RenewalError):
        restore(store, invalid, expected, tmp_path / 'runner')
