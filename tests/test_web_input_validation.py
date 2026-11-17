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


def test_non_object_json_is_a_client_error(web):
    client = web.app.test_client()
    paths = (
        '/api/generate', '/api/generate_batch', '/api/analyze_map', '/api/restyle_map',
        '/api/music_download', '/api/music_cut', '/api/render_video', '/api/models',
        '/api/video_title_preview',
    )
    for path in paths:
        for body in ('[1]', '"text"', '12', 'null', '{broken'):
            response = client.post(path, data=body, content_type='application/json')
            assert response.status_code == 400, (path, body)
            assert response.json['status'] == 'error', (path, body)


def test_upload_limit_returns_json(web):
    client = web.app.test_client()
    web.app.config['MAX_CONTENT_LENGTH'] = 16
    response = client.post('/api/generate', json={'topic': 'x' * 100})
    assert response.status_code == 413
    assert response.json['status'] == 'error'


def test_invalid_string_fields_are_client_errors(web):
    client = web.app.test_client()
    for path, field in (('/api/generate', 'api_key'), ('/api/music_download', 'url')):
        for value in (42, True, ['value'], {'value': 'text'}):
            response = client.post(path, json={field: value})
            assert response.status_code == 400, (path, field, value)
            assert field in response.json['error'], (path, field, value)


def test_video_proxy_releases_connection(web, monkeypatch):
    client = web.app.test_client()
    for status in (200, 404):
        upstream = Mock(status_code=status, headers={'Content-Type': 'application/json'})
        upstream.json.return_value = {'error': 'missing'}
        upstream.iter_content.return_value = iter([b'video'])
        monkeypatch.setattr(web.requests, 'get', Mock(return_value=upstream))
        response = client.get('/api/download_video?path=clip.mp4', buffered=True)
        assert response.status_code == status
        if status == 200:
            assert response.data == b'video'
        assert upstream.close.called
