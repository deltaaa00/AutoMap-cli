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

"""Malformed client requests and interrupted downloads stay controlled."""
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('path', [
    '/api/generate', '/api/generate_batch', '/api/analyze_map', '/api/restyle_map',
    '/api/music_download', '/api/music_cut', '/api/render_video', '/api/models',
    '/api/video_title_preview',
])
@pytest.mark.parametrize('body', ['[1]', '"text"', '12', 'null', '{broken'])
def test_non_object_json_is_a_client_error(web, path, body):
    response = web.app.test_client().post(path, data=body, content_type='application/json')
    assert response.status_code == 400
    assert response.json['status'] == 'error'


def test_upload_limit_returns_json(web):
    client = web.app.test_client()
    web.app.config['MAX_CONTENT_LENGTH'] = 16
    response = client.post('/api/generate', json={'topic': 'x' * 100})
    assert response.status_code == 413
    assert response.json['status'] == 'error'


@pytest.mark.parametrize('path,field', [('/api/generate', 'api_key'), ('/api/music_download', 'url')])
@pytest.mark.parametrize('value', [42, True, ['value'], {'value': 'text'}])
def test_invalid_string_fields_are_client_errors(web, path, field, value):
    response = web.app.test_client().post(path, json={field: value})
    assert response.status_code == 400
    assert field in response.json['error']


@pytest.mark.parametrize('status', [200, 404])
def test_video_proxy_releases_connection(web, monkeypatch, status):
    upstream = Mock(status_code=status, headers={'Content-Type': 'application/json'})
    upstream.json.return_value = {'error': 'missing'}
    upstream.iter_content.return_value = iter([b'video'])
    monkeypatch.setattr(web.requests, 'get', Mock(return_value=upstream))
    response = web.app.test_client().get('/api/download_video?path=clip.mp4', buffered=True)
    assert response.status_code == status
    if status == 200:
        assert response.data == b'video'
    assert upstream.close.called
