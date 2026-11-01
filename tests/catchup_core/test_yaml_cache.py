"""Repeated authority checks must not re-parse unchanged YAML, yet never serve stale or shared data."""
import yaml
import pytest

from catchup_core import yaml_cache


def test_same_text_is_parsed_once_and_changed_text_is_reparsed(monkeypatch):
    calls = []
    real = yaml.safe_load
    monkeypatch.setattr(yaml, 'safe_load', lambda text: calls.append(text) or real(text))
    assert yaml_cache.safe_load('a: 1\n') == {'a': 1}
    assert yaml_cache.safe_load('a: 1\n') == {'a': 1}
    assert yaml_cache.safe_load('a: 2\n') == {'a': 2}
    assert calls == ['a: 1\n', 'a: 2\n']


def test_callers_cannot_mutate_the_cached_document():
    first = yaml_cache.safe_load('a: [1]\n')
    first['a'].append(2)
    first['b'] = 3
    assert yaml_cache.safe_load('a: [1]\n') == {'a': [1]}


def test_parse_errors_are_not_cached_as_values():
    with pytest.raises(yaml.YAMLError):
        yaml_cache.safe_load('a: [\n')
    with pytest.raises(yaml.YAMLError):
        yaml_cache.safe_load('a: [\n')
