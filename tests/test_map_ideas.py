"""Idea generation, provider errors and bounded research without external calls."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from map import functions as ai


@pytest.mark.parametrize('with_params', [False, True])
@pytest.mark.parametrize('model', ['deepseek-flash', 'gemini-2.5-flash'])
def test_idea_generation_through_both_providers(monkeypatch, model, with_params):
    content = json.dumps({'idea':'Where are the oldest cities?', 'mode':'number', 'map_type':'flag',
                          'palette_theme':'greens', 'font':'Bips.ttf', 'extra_option':'show_most_least', 'show_legend':True}) if with_params else '"Where are the oldest cities?"\nExplanation'
    deepseek = Mock(return_value={'choices':[{'message':{'content':content}}]})
    gemini = Mock()
    gemini.models.generate_content.return_value = SimpleNamespace(text=content)
    monkeypatch.setattr(ai, '_deepseek_json', deepseek)
    monkeypatch.setattr(ai.genai, 'Client', Mock(return_value=gemini))
    result = ai.generate_map_idea(model, 'test-key', with_params=with_params, temperature=.5)
    assert result['idea'] == 'Where are the oldest cities?'
    if with_params:
        assert result['params']['mode'] == 'number' and result['params']['palette_theme'] == 'greens'
    if model.startswith('deepseek'):
        assert deepseek.call_args.kwargs['payload']['temperature'] == .5
    else:
        assert gemini.models.generate_content.call_args.kwargs['model'] == model


@pytest.mark.parametrize('raw', ['bad json', '[]', '{}'])
def test_invalid_structured_ideas_are_reported(monkeypatch, raw):
    monkeypatch.setattr(ai, '_call_model_text', lambda *a, **k:raw)
    with pytest.raises(ai.AIServiceError):
        ai.generate_map_idea('deepseek-flash', with_params=True)


def test_idea_parameters_are_restricted_to_available_choices(monkeypatch):
    raw = '```json\n'+json.dumps({'idea':'Topic', 'mode':'invalid', 'map_type':'invalid', 'palette_theme':'invalid', 'font':'missing', 'extra_option':'invalid'})+'\n```'
    monkeypatch.setattr(ai, '_call_model_text', lambda *a, **k:raw)
    result = ai.generate_map_idea('deepseek-flash', with_params=True, available_themes=['greens'], available_fonts=['Bips.ttf'])
    assert result['params'] == {'mode':'text', 'map_type':'default', 'palette_theme':'greens', 'font':'Bips.ttf', 'extra_option':'none', 'show_legend':False}


@pytest.mark.parametrize('code,status', [(503,503),(429,429),(403,400),(418,503)])
def test_gemini_idea_api_failures(monkeypatch, code, status):
    client = Mock()
    client.models.generate_content.side_effect = ai.genai_errors.APIError(code, {'error':{'message':'failed'}})
    monkeypatch.setattr(ai.genai,'Client',Mock(return_value=client))
    with pytest.raises(ai.AIServiceError) as error:
        ai._call_model_text('system','topic','gemini-2.5-flash')
    assert error.value.status_code == status


@pytest.mark.parametrize('model', ['deepseek-flash', 'gemini-2.5-flash'])
@pytest.mark.parametrize('content',[None,''])
def test_empty_idea_provider_responses(monkeypatch, model, content):
    monkeypatch.setattr(ai,'_deepseek_json',lambda *a,**k:{'choices':[{'message':{'content':content}}]})
    client=Mock()
    client.models.generate_content.return_value=SimpleNamespace(text=content)
    monkeypatch.setattr(ai.genai,'Client',Mock(return_value=client))
    with pytest.raises(ai.AIServiceError):
        ai._call_model_text('system','topic',model)


def test_research_budget_prevents_unbounded_model_tool_calls(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'test-search-key')
    monkeypatch.setenv('WEB_SEARCH_PROVIDER', 'tavily')
    tool = lambda i,args:{'id':str(i),'function':{'name':'web_search','arguments':args}}
    first={'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[tool(1,'bad-json'),tool(2,'{}')]}}]}
    call=Mock(side_effect=[first,{'choices':[{'message':{'content':'{"ukraine":42}'}}]}])
    monkeypatch.setattr(ai,'_deepseek_json',call)
    search=Mock(return_value='search results')
    monkeypatch.setattr(ai,'_web_search',search)
    assert json.loads(ai._deepseek_request('Population','deepseek-flash',web_search=True,max_search_requests=1)) == {'ukraine':42}
    search.assert_called_once_with('Population')
    assert 'tools' not in call.call_args.kwargs['payload']
    assert any('budget reached' in m.get('content','') for m in call.call_args.kwargs['payload']['messages'])


@pytest.mark.parametrize('payload,status', [({'provider':'unknown'},400),({'provider':'gemini','model':'deepseek-flash'},400),({'temperature':3},400),({},200)])
def test_idea_endpoint_validates_and_forwards_settings(web,monkeypatch,payload,status):
    generate=Mock(return_value={'idea':'Example'})
    monkeypatch.setattr(web,'generate_map_idea',generate)
    response=web.app.test_client().post('/api/generate_idea',json={'api_key':'test-key', **payload})
    assert response.status_code==status
    if status==200:assert response.json['idea']=='Example'


@pytest.mark.parametrize('error,status',[(ai.AIServiceError('overloaded',503),503),(RuntimeError('failed'),500)])
def test_idea_endpoint_provider_errors(web,monkeypatch,error,status):
    monkeypatch.setattr(web,'generate_map_idea',Mock(side_effect=error))
    assert web.app.test_client().post('/api/generate_idea',json={'api_key':'test-key'}).status_code==status


@pytest.mark.parametrize('with_params', [False, True])
def test_multiple_ideas_use_one_model_request(monkeypatch, with_params):
    call = Mock(return_value=json.dumps({'ideas': [
        {'idea': 'Where are the tallest trees?', 'mode': 'number'},
        {'idea': 'What is the favorite breakfast?', 'mode': 'text'},
    ]}))
    monkeypatch.setattr(ai, '_call_model_text', call)
    result = ai.generate_map_idea('deepseek-flash', count=2, with_params=with_params)
    assert len(result['ideas']) == 2
    assert ('params' in result['ideas'][0]) == with_params
    call.assert_called_once()


@pytest.mark.parametrize('items', [[{'idea': 'Same'}, {'idea': 'Same'}], [{'idea': 'Only one'}], [{}, {}]])
def test_multiple_ideas_reject_invalid_results(monkeypatch, items):
    monkeypatch.setattr(ai, '_call_model_text', Mock(return_value=json.dumps({'ideas': items})))
    with pytest.raises(ai.AIServiceError):
        ai.generate_map_idea('deepseek-flash', count=2)


@pytest.mark.parametrize('count', [0, 6, True, '2', 2.5])
def test_idea_endpoint_rejects_invalid_counts(web, count):
    response = web.app.test_client().post('/api/generate_idea', json={'api_key': 'test-key', 'count': count})
    assert response.status_code == 400


def test_idea_endpoint_returns_multiple_candidates(web, monkeypatch):
    candidates = [{'idea': 'First'}, {'idea': 'Second'}]
    generate = Mock(return_value={'ideas': candidates})
    monkeypatch.setattr(web, 'generate_map_idea', generate)
    response = web.app.test_client().post('/api/generate_idea', json={'api_key': 'test-key', 'count': 2, 'with_params': True})
    assert response.json['ideas'] == candidates
    assert generate.call_args.kwargs['count'] == 2
    assert generate.call_args.kwargs['with_params'] is True
