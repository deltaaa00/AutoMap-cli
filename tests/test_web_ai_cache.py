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

"""Verify AI-answer reuse without sharing credentials or losing numeric precision.

Fake render buffers carry the same map_meta shape as real generation. Requests
change visual settings, prompt/provider inputs, research budgets and short form
to check cache identity, refresh and repaint behavior. This module also checks
the multipart-to-worker encoding contract without external network calls.
"""

from webapp import generation, http_client
import io
import json
from unittest.mock import Mock

import pytest

import main as web
from webapp.cache import AIResponseCache


@pytest.fixture(autouse=True)
def fresh_cache():
    generation.ai_cache.clear()
    yield
    generation.ai_cache.clear()


def _buffer(answer):
    buf = io.BytesIO(b'png')
    buf.map_meta = {'ai_answer': dict(answer)}
    return buf


def _body(**extra):
    body = {'topic': 'GDP', 'mode': 'text', 'map_type': 'default', 'model': generation.AVAILABLE_MODELS[0],
            'font': 'Arial', 'api_key': 'secret-key-123'}
    body.update(extra)
    return body


def test_visual_change_reuses_cached_ai_answer(monkeypatch):
    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        return _buffer(kwargs.get('ai_answer_override') or {'france': 'Paris'})

    monkeypatch.setattr(generation, 'auto_map', render)
    client = web.app.test_client()
    first = client.post('/api/generate', json=_body(palette_theme='ocean')).get_json()
    second = client.post('/api/generate', json=_body(palette_theme='forest', label_style='x')).get_json()
    assert first['ai_cached'] is False and second['ai_cached'] is True
    assert 'ai_answer_override' not in calls[0]
    assert calls[1]['ai_answer_override'] == {'france': 'Paris'}


def test_refresh_and_request_changes_bypass_cache(monkeypatch):
    render = Mock(side_effect=lambda **kw: _buffer({'a': 1}))
    monkeypatch.setattr(generation, 'auto_map', render)
    client = web.app.test_client()
    client.post('/api/generate', json=_body())
    client.post('/api/generate', json=_body(refresh_ai=True))
    client.post('/api/generate', json=_body(topic='Population'))
    client.post('/api/generate', json=_body(map_type='world'))
    client.post('/api/generate', json=_body(api_key='other-user-key'))
    assert all('ai_answer_override' not in c.kwargs for c in render.call_args_list)


def test_cache_never_stores_api_key_and_expires(monkeypatch):
    cache = AIResponseCache(ttl_seconds=10)
    key = cache.make_key(cache.scope('', 'secret-key-123'), prompt='x')
    cache.put(key, {'a': 1})
    assert 'secret-key-123' not in repr(cache._data)
    assert cache.get(key) == {'a': 1}
    now = generation.time.monotonic()
    monkeypatch.setattr('webapp.cache.time.monotonic', lambda: now + 11)
    assert cache.get(key) is None


def test_search_settings_bypass_cache_but_visual_changes_reuse_it(monkeypatch):
    render = Mock(side_effect=lambda **kw: _buffer({'france': 1}))
    monkeypatch.setattr(generation, 'auto_map', render)
    client = web.app.test_client()
    for settings in (
        {'web_search': False},
        {'web_search': True, 'max_search_requests': 3},
        {'web_search': True, 'max_search_requests': 7},
        {'web_search': True, 'max_search_requests': 7, 'palette_theme': 'forest'},
    ):
        response = client.post('/api/generate', json=_body(**settings))
        assert response.status_code == 200
    assert all('ai_answer_override' not in call.kwargs for call in render.call_args_list[:3])
    assert render.call_args_list[3].kwargs['ai_answer_override'] == {'france': 1}


def test_toggling_short_form_abbreviates_cached_large_numbers_on_regenerate(monkeypatch):
    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        ans = kwargs.get('ai_answer_override') or {'ukraine': 38_000_000, 'germany': 84_500_000}
        return _buffer(ans)

    monkeypatch.setattr(generation, 'auto_map', render)
    client = web.app.test_client()
    first = client.post('/api/generate', json=_body(mode='number', extra_option='none')).get_json()
    second = client.post('/api/generate', json=_body(mode='number', extra_option='short_form')).get_json()
    assert first['map_values'] == {'ukraine': 38_000_000, 'germany': 84_500_000}
    assert second['ai_cached'] is True
    assert second['map_values'] == {'ukraine': '38M', 'germany': '84.5M'}
    assert calls[1]['ai_answer_override'] == {'ukraine': 38_000_000, 'germany': 84_500_000}


def test_render_video_accepts_binary_multipart(monkeypatch):
    upstream = Mock(status_code=200)
    upstream.json.return_value = {'status': 'success'}
    post = Mock(return_value=upstream)
    monkeypatch.setattr(http_client.worker_http, 'post', post)
    client = web.app.test_client()
    response = client.post('/api/render_video', data={
        'payload': json.dumps({'video_title': 'T', 'duration': 8}),
        'map_file': (io.BytesIO(b'\x89PNG'), 'map.png', 'image/png'),
        'background_video': (io.BytesIO(b'vid'), 'bg.mp4', 'video/mp4'),
    }, content_type='multipart/form-data')
    assert response.status_code == 200
    sent = post.call_args.kwargs['json']
    assert sent['map_file'] == 'data:image/png;base64,iVBORw=='
    assert sent['background_video'].startswith('data:video/mp4;base64,')
    assert sent['video_title'] == 'T' and sent['duration'] == 8


def test_explicit_map_values_bypass_cache_without_replacing_it(monkeypatch):
    original = {'france': 10}
    supplied = {'france': 20}
    render = Mock(side_effect=lambda **kw: _buffer(kw.get('ai_answer_override', original)))
    monkeypatch.setattr(generation, 'auto_map', render)
    client = web.app.test_client()

    assert client.post('/api/generate', json=_body()).json['map_values'] == original
    response = client.post('/api/generate', json=_body(ai_answer_override=supplied))
    assert response.json['map_values'] == supplied
    assert response.json['ai_cached'] is False
    cached = client.post('/api/generate', json=_body()).json
    assert cached['map_values'] == original
    assert cached['ai_cached'] is True


def test_combined_features_keep_precision_across_cached_requests_and_repaint(monkeypatch):
    calls = []
    raw = {'france': 1001, 'germany': 1049}

    def render(**kwargs):
        calls.append(kwargs)
        values = kwargs.get('ai_answer_override', raw)
        buf = _buffer({k: generation._format_short_number(v) for k, v in values.items()})
        buf.map_meta['raw_ai_answer'] = dict(values)
        return buf

    monkeypatch.setattr(generation, 'auto_map', render)
    client = web.app.test_client()
    body = _body(mode='number', extra_option='show_most_least,short_form')
    for _ in range(2):
        response = client.post('/api/generate', json=body)
        assert response.status_code == 200
        assert response.json['map_values'] == raw
    assert calls[1]['ai_answer_override'] == raw
    response = client.post('/api/restyle_map', json={**body, 'map_values': response.json['map_values']})
    assert response.status_code == 200
    assert calls[2]['ai_answer_override'] == raw
