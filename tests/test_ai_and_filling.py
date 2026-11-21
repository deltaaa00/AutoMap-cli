# Copyright 2025-2026 deltaaa00
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test provider JSON parsing, failure translation and filling-file loading.

Fake Gemini clients expose request configuration and bounded errors. Temporary
filling files cover color/range conversion and template fallback. Use this suite
for map.functions contracts; provider-specific DeepSeek/research behavior has
its own suites and must not call real services.
"""

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
    options = client.call_args.kwargs
    assert options['http_options'].timeout == functions.AI_TIMEOUT_MS
    assert options['http_options'].retry_options.attempts == 1
    assert options.get('api_key') == key
    kwargs = generate.call_args.kwargs
    assert kwargs['model'] == 'test-model'
    assert 'Population' in kwargs['contents'] and region in kwargs['contents']


def test_ai_failure_is_reported(monkeypatch):
    monkeypatch.setattr(functions.genai, 'Client', Mock(side_effect=RuntimeError('service down')))
    assert functions._ai_request('topic', 'default', 'model') is None


@pytest.mark.parametrize('temperature', [0, 0.4, 2])
def test_temperature_reaches_gemini_generation_config(monkeypatch, temperature):
    generate = Mock(return_value=SimpleNamespace(text='{"ukraine":42}'))
    client = Mock(return_value=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(functions.genai, 'Client', client)
    assert functions.ai_request('Population', 'default', 'model', temperature=temperature) == {'ukraine': 42}
    assert generate.call_args.kwargs['config'].temperature == temperature


@pytest.mark.parametrize('temperature', [-0.1, 2.1, float('nan'), float('inf'), True, None, 'invalid', {}])
def test_invalid_temperature_is_rejected(temperature):
    with pytest.raises(ValueError, match='between 0 and 2'):
        functions.validate_temperature(temperature)


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


def test_missing_filling_uses_bundled_template(monkeypatch, tmp_path):
    monkeypatch.setattr(functions, '__file__', str(tmp_path / 'functions.py'))
    (tmp_path / 'filling.template.json').write_text(json.dumps({
        'filling_num': {'(0.1, 0.4)': [4, 5, 6, 255]},
    }))
    assert functions.load_filling() == ({}, {(0.1, 0.4): (4, 5, 6, 255)})


@pytest.mark.parametrize('code,status,phrase', [
    (503, 503, 'overloaded'), (429, 429, 'quota'), (404, 400, 'model access'),
])
def test_ai_service_errors_are_actionable(monkeypatch, code, status, phrase):
    from google.genai import errors
    failure = errors.APIError(code, {'error': {'message': 'Upstream error', 'code': code}})
    generate = Mock(side_effect=failure)
    monkeypatch.setattr(functions.genai, 'Client', Mock(return_value=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate))))
    with pytest.raises(functions.AIServiceError, match=phrase) as caught:
        functions.ai_request('topic', 'default', 'busy-model')
    assert caught.value.status_code == status
    generate.assert_called_once()


def test_ai_timeout_has_a_bounded_wait_and_actionable_error(monkeypatch):
    generate = Mock(side_effect=functions.httpx.ReadTimeout('timed out'))
    monkeypatch.setattr(functions.genai, 'Client', Mock(return_value=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate))))
    with pytest.raises(functions.AIServiceError, match='45 seconds') as caught:
        functions.ai_request('topic', 'default', 'slow-model')
    assert caught.value.status_code == 504
    generate.assert_called_once()
