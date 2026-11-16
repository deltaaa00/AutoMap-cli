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

"""DeepSeek transport and credential isolation; no real API keys or requests."""
import io
from unittest.mock import Mock

import pytest
import requests
from dotenv.main import dotenv_values

from map import functions


@pytest.fixture(autouse=True)
def no_deepseek_server_key(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)


def reply(body, status=200):
    return Mock(status_code=status, json=Mock(return_value=body))


@pytest.mark.parametrize('use_server_key', [False, True])
@pytest.mark.parametrize('model', ['deepseek-flash', 'deepseek-v4-pro'])
def test_deepseek_generates_parseable_map_json(monkeypatch, model, use_server_key):
    if use_server_key:
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'deepseek-server-key')
    response = reply({'choices': [{'message': {'content': '{"ukraine":0.4,"poland":"2M"}'}, 'finish_reason': 'stop'}]})
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, 'post', post)
    result = functions.ai_request('Population', 'default', model,
                                  None if use_server_key else 'deepseek-browser-key', temperature=0.4)
    assert result == {'ukraine': 0.4, 'poland': '2M'}
    assert post.call_args.args == ('https://api.deepseek.com/chat/completions',)
    options = post.call_args.kwargs
    assert options['headers']['Authorization'] == 'Bearer ' + ('deepseek-server-key' if use_server_key else 'deepseek-browser-key')
    assert options['allow_redirects'] is False
    assert options['timeout'] == (10, 45)
    assert options['json']['model'] == model
    assert options['json']['temperature'] == 0.4
    assert options['json']['response_format'] == {'type': 'json_object'}
    assert options['json']['thinking'] == {'type': 'disabled'}
    assert 'Population' in options['json']['messages'][1]['content']
    assert 'ukraine' in options['json']['messages'][1]['content']
    response.close.assert_called_once()
    functions.genai.Client.assert_not_called()


def test_deepseek_never_falls_back_to_gemini_credentials(monkeypatch):
    post = Mock()
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setenv('GEMINI_API_KEY', 'gemini-only')
    with pytest.raises(functions.AIServiceError, match='DEEPSEEK_API_KEY'):
        functions.ai_request('Population', 'default', 'deepseek-flash')
    post.assert_not_called()


@pytest.mark.parametrize('status,expected,phrase', [
    (401, 400, 'API key'), (403, 400, 'API key'), (402, 402, 'balance'),
    (429, 429, 'limit'), (503, 503, 'overloaded'), (400, 400, 'model'), (302, 400, 'model'),
])
def test_deepseek_http_errors_are_actionable_without_leaking_keys(monkeypatch, status, expected, phrase):
    response = reply({'error': {'message': 'secret-key-in-upstream-error'}}, status)
    monkeypatch.setattr(requests, 'post', Mock(return_value=response))
    with pytest.raises(functions.AIServiceError, match=phrase) as failure:
        functions.ai_request('Population', 'world', 'deepseek-flash', 'secret-key')
    assert failure.value.status_code == expected
    assert 'secret-key' not in str(failure.value)
    response.close.assert_called_once()


@pytest.mark.parametrize('failure,status', [(requests.Timeout('secret'), 504), (requests.ConnectionError('secret'), 503)])
def test_deepseek_network_errors(monkeypatch, failure, status):
    monkeypatch.setattr(requests, 'post', Mock(side_effect=failure))
    with pytest.raises(functions.AIServiceError) as caught:
        functions.ai_request('Population', 'world', 'deepseek-flash', 'key')
    assert caught.value.status_code == status
    assert 'secret' not in str(caught.value)


@pytest.mark.parametrize('body', [
    [], {}, {'error': {'message': 'failure after HTTP200'}}, {'choices': []},
    {'choices': [{'message': {'content': '   '}}]},
    {'choices': [{'message': {'content': '{}'}, 'finish_reason': 'length'}]},
    {'choices': [{'message': {'content': None}}]},
])
def test_deepseek_invalid_or_incomplete_responses(monkeypatch, body):
    monkeypatch.setattr(requests, 'post', Mock(return_value=reply(body)))
    with pytest.raises(functions.AIServiceError) as caught:
        functions.ai_request('Population', 'default', 'deepseek-flash', 'key')
    assert caught.value.status_code == 502


def test_deepseek_invalid_json_response(monkeypatch):
    response = reply({})
    response.json.side_effect = ValueError('bad json')
    monkeypatch.setattr(requests, 'post', Mock(return_value=response))
    with pytest.raises(functions.AIServiceError, match='invalid response'):
        functions.ai_request('Population', 'default', 'deepseek-flash', 'key')
    response.close.assert_called_once()


def test_deepseek_model_discovery(monkeypatch):
    get = Mock(return_value=reply({'data': [{'id': 'deepseek-flash'}, {'id': 'deepseek-v4-pro'},
                                          {'id': 'other-provider'}, {}, None]}))
    monkeypatch.setattr(requests, 'get', get)
    assert functions.list_deepseek_models('key') == ['deepseek-flash', 'deepseek-v4-pro']
    assert get.call_args.args == ('https://api.deepseek.com/models',)
    assert get.call_args.kwargs['timeout'] == (10, 15)


@pytest.mark.parametrize('source', ['browser', 'environment', 'file'])
def test_web_deepseek_key_sources_are_independent(web, monkeypatch, tmp_path, source):
    monkeypatch.setattr(web, 'auto_map', Mock(return_value=io.BytesIO(b'png')))
    body = dict(topic='Population', mode='number', map_type='default', model='deepseek-flash', provider='deepseek')
    if source == 'browser':
        body['api_key'] = 'deepseek-only-key'
    elif source == 'environment':
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'deepseek-only-key')
    else:
        env = tmp_path / '.env'
        env.write_text('DEEPSEEK_API_KEY=deepseek-only-key\nGEMINI_API_KEY=your_gemini_key\n')
        monkeypatch.setattr(web, 'env', env)
        monkeypatch.setattr(web, 'dotenv_values', dotenv_values)
    client = web.app.test_client()
    assert client.get('/api/status?provider=deepseek').json == {'needs_api_key': source == 'browser'}
    assert client.get('/api/status').json == {'needs_api_key': True}
    response = client.post('/api/generate', json=body)
    assert response.status_code == 200
    assert web.auto_map.call_args.kwargs['api_key'] == 'deepseek-only-key'
    assert 'deepseek-only-key' not in response.json['command']


def test_web_provider_mismatch_cannot_send_a_key_to_another_provider(web, monkeypatch):
    render = Mock()
    monkeypatch.setattr(web, 'auto_map', render)
    result = web.app.test_client().post('/api/generate', json={
        'topic': 'Population', 'model': 'deepseek-flash', 'provider': 'gemini', 'api_key': 'gemini-key',
    })
    assert result.status_code == 400
    assert 'different AI provider' in result.json['error']
    render.assert_not_called()


def test_web_deepseek_discovery_uses_its_key(web, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'deepseek-server')
    listing = Mock(return_value=['deepseek-flash'])
    monkeypatch.setattr(web, 'list_deepseek_models', listing)
    result = web.app.test_client().post('/api/models', json={'provider': 'deepseek'})
    assert result.json == {'status': 'success', 'models': ['deepseek-flash']}
    listing.assert_called_once_with('deepseek-server')


def test_deepseek_batch_does_not_forward_browser_key_to_gemini(web, monkeypatch):
    monkeypatch.setattr(web, 'auto_map', Mock(return_value=io.BytesIO(b'png')))
    result = web.app.test_client().post('/api/generate_batch', json={
        'provider': 'deepseek', 'model': 'deepseek-flash', 'api_key': 'deepseek-browser',
        'filename': 'ideas.csv', 'ideas_text': 'prompt,model\nOne,deepseek-flash\nTwo,gemini-3.8-flash\n',
    })
    assert result.status_code == 200
    assert result.json['succeeded'] == 1
    assert result.json['results'][0]['provider'] == 'deepseek'
    assert result.json['results'][1]['status'] == 'error'
    web.auto_map.assert_called_once()
    assert web.auto_map.call_args.kwargs['api_key'] == 'deepseek-browser'
