import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from map import functions


@pytest.mark.parametrize('answer,expected', [
    ('{"ukraine": 42}', {'ukraine': 42}),
    ('  ```json\n{"a":"Так"}\n```  ', {'a': 'Так'}),
    ('```python\n{"a":false}\n```', {'a': False}),
    ('{}', {}), ('', None), (None, None), ('not json', None),
    ("{'a': 1}", None), (42, None),
])
def test_parse_ai_response(answer, expected, monkeypatch):
    monkeypatch.setattr(functions, '_ai_request', lambda *args: answer)
    assert functions.ai_request('topic', 'default', 'model') == expected


def test_supplied_answer_bypasses_ai(monkeypatch):
    remote = Mock(side_effect=AssertionError('Must not call AI'))
    monkeypatch.setattr(functions, '_ai_request', remote)
    assert functions.ai_request('topic', 'default', 'model', _ai_answer='{"a":1}') == {'a': 1}
    remote.assert_not_called()


@pytest.mark.parametrize('value', [[], [1, 2], 'text', 42, 1.5, True, False, None])
@pytest.mark.parametrize('fence', ['', 'json', 'python'])
def test_non_object_json_is_rejected(value, fence, monkeypatch):
    answer = json.dumps(value)
    if fence:
        answer = f'```{fence}\n{answer}\n```'
    error = Mock()
    monkeypatch.setattr(functions, '_error', error)
    assert functions.ai_request('topic', 'default', 'model', _ai_answer=answer) is None
    assert 'expected a JSON object' in error.call_args.args[1]


@pytest.mark.parametrize('mode,region', [('default', 'ukraine'), ('flag', 'poland'), ('world', 'africa')])
@pytest.mark.parametrize('key', [None, 'request-only-key'])
def test_ai_client_and_prompt(monkeypatch, mode, region, key):
    generate = Mock(return_value=SimpleNamespace(text='{"a":1}'))
    client = Mock(return_value=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(functions.genai, 'Client', client)
    assert functions._ai_request('Population', mode, 'test-model', key) == '{"a":1}'
    client.assert_called_once_with(**({'api_key': key} if key else {}))
    kwargs = generate.call_args.kwargs
    assert kwargs['model'] == 'test-model'
    assert 'Population' in kwargs['contents'] and region in kwargs['contents']


def test_ai_failure_is_reported(monkeypatch):
    monkeypatch.setattr(functions.genai, 'Client', Mock(side_effect=RuntimeError('service down')))
    assert functions._ai_request('topic', 'default', 'model') is None


def filling_path(monkeypatch, tmp_path, content):
    monkeypatch.setattr(functions, '__file__', str(tmp_path / 'functions.py'))
    path = tmp_path / 'filling.json'
    path.write_text(content)
    return path


def test_load_filling_converts_colors_and_ranges(monkeypatch, tmp_path):
    filling_path(monkeypatch, tmp_path, json.dumps({
        'filling_txt': {'Yes': [1, 2, 3, 255]},
        'filling_num': {'(0, 100)': [4, 5, 6, 255], 'broken key': [0, 0, 0, 0]},
    }))
    assert functions.load_filling() == ({'Yes': (1, 2, 3, 255)}, {(0, 100): (4, 5, 6, 255)})


@pytest.mark.parametrize('content', ['{}', 'not-json'])
def test_empty_or_invalid_filling(monkeypatch, tmp_path, content):
    filling_path(monkeypatch, tmp_path, content)
    assert functions.load_filling() == ({}, {})


def test_missing_filling(monkeypatch, tmp_path):
    monkeypatch.setattr(functions, '__file__', str(tmp_path / 'functions.py'))
    assert functions.load_filling() == ({}, {})
