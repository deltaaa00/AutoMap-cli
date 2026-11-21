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

"""Check browser-to-worker audio upload and cut forwarding.

The HTTP session is patched so tests assert payloads, upstream status propagation
and recoverable network/JSON failures. Audio bytes are not decoded here; real
normalization and restored-track cuts are covered in test_audio_upload.
"""

from webapp import http_client
from unittest.mock import Mock

import requests


def test_upload_proxy_forwards_file_and_worker_status(web, monkeypatch):
    client = web.app.test_client()
    payload = {'filename': 'My song.wav', 'audio_file': 'data:audio/wav;base64,d2F2'}
    for status in (200, 400, 504):
        response = Mock(status_code=status)
        response.json.return_value = {'status': 'success' if status == 200 else 'error'}
        post = Mock(return_value=response)
        monkeypatch.setattr(http_client.worker_http, 'post', post)
        result = client.post('/api/music_upload', json=payload)
        assert result.status_code == status
        assert result.json == response.json.return_value
        post.assert_called_once_with(f'{web.app.config['WORKER_URL']}/api/music_upload', json=payload, timeout=90)


def test_missing_audio_is_rejected(web):
    client = web.app.test_client()
    for audio in (None, '', 12, []):
        assert client.post('/api/music_upload', json={'audio_file': audio}).status_code == 400, audio


def test_upload_proxy_failure(web, monkeypatch):
    client = web.app.test_client()
    for failure in (requests.Timeout(), ValueError('not JSON')):
        post = (
            Mock(side_effect=failure)
            if isinstance(failure, requests.RequestException)
            else Mock(return_value=Mock(json=Mock(side_effect=failure)))
        )
        monkeypatch.setattr(http_client.worker_http, 'post', post)
        result = client.post('/api/music_upload', json={'audio_file': 'd2F2'})
        assert result.status_code == 502


def test_cut_proxy_forwards_inline_source(web, monkeypatch):
    post = Mock(return_value=Mock(status_code=200, json=lambda: {'file':'cut'}))
    monkeypatch.setattr(http_client.worker_http, 'post', post)
    payload = {'title':'uploaded', 'start_time':'00:01', 'duration':3, 'audio_file':'data:audio/mpeg;base64,bXAz'}
    assert web.app.test_client().post('/api/music_cut', json=payload).status_code == 200
    assert post.call_args.kwargs['json'] == payload
