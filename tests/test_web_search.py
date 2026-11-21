"""Verify research evidence and bounded tool conversations with fake services.

Checks provider selection, credential redaction, result normalization, required
searches and budget exhaustion across the model tool loop. Web forwarding uses
request-local Tavily keys and must not change process environment or echo secrets.
"""
from webapp import generation
import copy
import io
import json
from unittest.mock import Mock

import pytest
import requests

from map import functions as ai
from map import web_search as search


@pytest.fixture(autouse=True)
def search_environment(monkeypatch):
    for name in ('WEB_SEARCH_PROVIDER', 'TAVILY_API_KEY', 'BRAVE_SEARCH_API_KEY'):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize('provider', ['tavily', 'brave'])
def test_real_search_transport_returns_source_evidence(monkeypatch, provider):
    monkeypatch.setenv('WEB_SEARCH_PROVIDER', provider)
    monkeypatch.setenv('TAVILY_API_KEY' if provider == 'tavily' else 'BRAVE_SEARCH_API_KEY', 'search-secret')
    items = [{'title': '<b>Official statistics</b>', 'url': 'https://data.example.org/population',
              'content': 'Population &amp; year', 'description': 'Population &amp; year',
              'published_date': '2026-11-20', 'page_age': '2026-11-20'},
             {'url': 'javascript:bad'}, {'url': 'https://user:pass@example.org/'},
             {'url': 'https://data.example.org/population'},
             {'url': 'https://other.example.org/report', 'title': 'Another source'}]
    response = Mock(status_code=200)
    response.json.return_value = {'results': items} if provider == 'tavily' else {'web': {'results': items}}
    transport = Mock(return_value=response)
    monkeypatch.setattr(requests, 'post' if provider == 'tavily' else 'get', transport)
    evidence = json.loads(search.search_web('population 2026', 2))
    assert len(evidence['results']) == 2
    assert evidence['results'][0] == {'title': 'Official statistics', 'url': 'https://data.example.org/population',
                                      'snippet': 'Population & year', 'date': '2026-11-20'}
    assert 'search-secret' not in json.dumps(evidence)
    options = transport.call_args.kwargs
    assert options['allow_redirects'] is False
    assert options['timeout'] == (5, 20)
    if provider == 'tavily':
        assert transport.call_args.args == ('https://api.tavily.com/search',)
        assert options['json']['search_depth'] == 'basic'
        assert options['headers']['Authorization'] == 'Bearer search-secret'
    else:
        assert transport.call_args.args == ('https://api.search.brave.com/res/v1/web/search',)
        assert options['headers']['X-Subscription-Token'] == 'search-secret'
    response.close.assert_called_once()


@pytest.mark.parametrize('body', [{}, {'results': None}, {'results': {}}, [], {'results': ['bad', {}]}])
def test_malformed_results_do_not_crash_or_fabricate(monkeypatch, body):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-secret')
    monkeypatch.setattr(requests, 'post', Mock(return_value=Mock(status_code=200, json=Mock(return_value=body))))
    if body == {'results': ['bad', {}]}:
        assert json.loads(search.search_web('topic'))['results'] == []
    else:
        with pytest.raises(search.SearchError, match='invalid response'):
            search.search_web('topic')


@pytest.mark.parametrize('status', [302, 401, 403, 429, 500])
def test_upstream_failures_are_explicit_and_redacted(monkeypatch, status):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-secret')
    response = Mock(status_code=status, text='search-secret')
    monkeypatch.setattr(requests, 'post', Mock(return_value=response))
    with pytest.raises(search.SearchError, match=f'HTTP {status}') as error:
        search.search_web('topic')
    assert 'search-secret' not in str(error.value)
    response.close.assert_called_once()


@pytest.mark.parametrize('failure', [requests.Timeout('search-secret'), requests.ConnectionError('search-secret'), ValueError('search-secret')])
def test_transport_errors_do_not_leak_credentials(monkeypatch, failure):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-secret')
    monkeypatch.setattr(requests, 'post', Mock(side_effect=failure))
    with pytest.raises(search.SearchError) as error:
        search.search_web('topic')
    assert 'search-secret' not in str(error.value)


def test_missing_search_key_fails_before_model_request(monkeypatch):
    model = Mock()
    monkeypatch.setattr(ai, '_deepseek_json', model)
    with pytest.raises(ai.AIServiceError, match='BRAVE_SEARCH_API_KEY'):
        ai._deepseek_request('Population', 'deepseek-flash', api_key='model-key', web_search=True)
    model.assert_not_called()


def test_default_provider_and_explicit_choice(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'tavily-key')
    monkeypatch.setenv('BRAVE_SEARCH_API_KEY', 'brave-key')
    assert search.search_config() == ('tavily', 'tavily-key')
    monkeypatch.setenv('WEB_SEARCH_PROVIDER', 'brave')
    assert search.search_config() == ('brave', 'brave-key')
    monkeypatch.setenv('WEB_SEARCH_PROVIDER', 'unknown')
    with pytest.raises(search.SearchError, match='must be'):
        search.search_config()


@pytest.mark.parametrize('arguments', ['[]', 'null', '{"query":42}', 'bad-json', '{"query":"GDP 2026"}'])
def test_search_evidence_reaches_model_and_budget_removes_tools(monkeypatch, arguments):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-key')
    payloads = []
    def model(path, key, *, payload):
        payloads.append(copy.deepcopy(payload))
        if len(payloads) == 1:
            return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                    'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'web_search', 'arguments': arguments}}]}}]}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ukraine":42}'}}]}
    monkeypatch.setattr(ai, '_deepseek_json', model)
    evidence = '{"results":[{"url":"https://data.example.org/","snippet":"GDP: 42"}]}'
    lookup = Mock(return_value=evidence)
    monkeypatch.setattr(ai, '_web_search', lookup)
    assert json.loads(ai._deepseek_request('GDP', 'deepseek-flash', 'model-key', web_search=True, max_search_requests=1)) == {'ukraine': 42}
    lookup.assert_called_once_with('GDP 2026' if 'GDP 2026' in arguments else 'GDP')
    assert payloads[0]['tool_choice']['function']['name'] == 'web_search'
    assert 'tools' not in payloads[1] and 'tool_choice' not in payloads[1]
    tool_result = payloads[1]['messages'][-1]
    assert tool_result['role'] == 'tool' and tool_result['tool_call_id'] == 'call1'
    assert json.loads(tool_result['content']) == {'search': json.loads(evidence),
                                                 'search_budget': {'total': 1, 'used': 1, 'remaining': 0}}


def test_model_sees_total_and_remaining_budget_and_console_records_actual_queries(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-secret')
    payloads = []
    def model(path, key, *, payload):
        payloads.append(copy.deepcopy(payload))
        turn = len(payloads)
        if turn < 3:
            return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': f'call{turn}', 'type': 'function', 'function': {
                    'name': 'web_search', 'arguments': json.dumps({'query': 'GDP\n  2026 [bold] search-secret' if turn == 1 else 'Population 2026'})}}]}}]}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ukraine":42}'}}]}
    monkeypatch.setattr(ai, '_deepseek_json', model)
    lookup = Mock(return_value=json.dumps({'results': [{'url': 'https://example.org'}]}))
    monkeypatch.setattr(ai, '_web_search', lookup)
    ai._deepseek_request('GDP', 'deepseek-flash', 'model-secret', web_search=True, max_search_requests=2)
    assert 'total budget of 2' in payloads[0]['messages'][0]['content']
    assert '2 remain' in payloads[0]['messages'][0]['content']
    assert json.loads(payloads[1]['messages'][-1]['content'])['search_budget'] == {'total': 2, 'used': 1, 'remaining': 1}
    assert json.loads(payloads[2]['messages'][-1]['content'])['search_budget'] == {'total': 2, 'used': 2, 'remaining': 0}
    assert lookup.call_args_list[0].args == ('GDP 2026 [bold] search-secret',)
    assert 'tools' not in payloads[2]
    output = ai.log.console.file.getvalue()
    assert '1/2 query="GDP 2026 [bold] [REDACTED]" remaining=1' in output
    assert '2/2 query="Population 2026" remaining=0' in output
    assert 'Completed: results=1' in output
    assert 'Finished: used=2/2' in output
    assert 'search-secret' not in output and 'model-secret' not in output


def test_empty_search_is_explicit(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-key')
    monkeypatch.setattr(requests, 'post', Mock(return_value=Mock(status_code=200, json=Mock(return_value={'results': []}))))
    assert json.loads(search.search_web('topic'))['results'] == []


def test_model_cannot_silently_skip_required_search(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-key')
    monkeypatch.setattr(ai, '_deepseek_json', Mock(return_value={'choices': [
        {'finish_reason': 'stop', 'message': {'content': '{"ukraine":42}'}}]}))
    with pytest.raises(ai.AIServiceError, match='required web search'):
        ai._deepseek_request('GDP', 'deepseek-flash', 'model-key', web_search=True)


def test_search_failure_stops_generation(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'search-key')
    monkeypatch.setattr(ai, 'search_web', Mock(side_effect=search.SearchError('Search quota reached')))
    with pytest.raises(ai.AIServiceError, match='quota'):
        ai._web_search('GDP')


def test_browser_tavily_key_overrides_server_provider_without_mutating_env(monkeypatch):
    monkeypatch.setenv('WEB_SEARCH_PROVIDER', 'brave')
    monkeypatch.setenv('BRAVE_SEARCH_API_KEY', 'server-brave')
    response = Mock(status_code=200, json=Mock(return_value={'results': []}))
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, 'post', post)
    search.search_web('GDP', api_key='browser-tavily')
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer browser-tavily'
    assert search.search_config() == ('brave', 'server-brave')


def test_web_generation_forwards_tavily_key_without_returning_it(web, monkeypatch):
    monkeypatch.setattr(generation, 'auto_map', Mock(return_value=io.BytesIO(b'png')))
    response = web.app.test_client().post('/api/generate', json={
        'topic': 'GDP', 'mode': 'number', 'map_type': 'default',
        'model': 'deepseek-flash', 'provider': 'deepseek', 'api_key': 'model-secret',
        'web_search': True, 'tavily_api_key': 'browser-search-secret', 'refresh_ai': True,
    })
    assert response.status_code == 200
    assert generation.auto_map.call_args.kwargs['tavily_api_key'] == 'browser-search-secret'
    assert 'browser-search-secret' not in response.get_data(as_text=True)


def test_ai_request_forwards_browser_search_key(monkeypatch):
    request = Mock(return_value='{"ukraine":42}')
    monkeypatch.setattr(ai, '_deepseek_request', request)
    assert ai.ai_request('GDP', 'default', 'deepseek-flash', 'model-key',
                         web_search=True, tavily_api_key='browser-search-key') == {'ukraine': 42}
    assert request.call_args.kwargs['tavily_api_key'] == 'browser-search-key'
