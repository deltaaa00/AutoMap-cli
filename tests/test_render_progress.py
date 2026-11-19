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

"""Progress is isolated per request and follows actual MoviePy callbacks."""
from unittest.mock import Mock
from uuid import uuid4

import pytest
from flask import Flask, jsonify
import requests

from render_progress import RenderProgress


def test_progress_tracks_encoding_and_completes_only_after_response():
    app = Flask(__name__)
    tracker = RenderProgress()
    render_id = str(uuid4())
    seen = []

    @app.post('/render')
    @tracker.track
    def render():
        logger = tracker.logger()
        for _ in logger.iter_bar(chunk=range(4)):
            pass
        seen.append(tracker.snapshot(render_id))
        for _ in logger.iter_bar(frame_index=range(10)):
            pass
        seen.append(tracker.snapshot(render_id))
        return jsonify(status='success')

    assert app.test_client().post('/render', json={'render_id': render_id}).status_code == 200
    assert seen[0]['phase'] == 'Encoding audio'
    assert 0 < seen[0]['progress'] < seen[1]['progress'] < 100
    assert seen[1]['status'] == 'rendering'
    assert tracker.snapshot(render_id) == {'status': 'success', 'progress': 100, 'phase': 'Complete'}
    assert app.test_client().post('/render', json={'render_id': render_id}).status_code == 409


def test_worker_records_validation_failures_and_rejects_bad_ids(worker):
    client = worker.app.test_client()
    render_id = str(uuid4())
    assert client.get(f'/api/render_progress/{render_id}').status_code == 404
    assert client.post('/api/render_video', json={'render_id': render_id}).status_code == 400
    result = client.get(f'/api/render_progress/{render_id}')
    assert result.json['status'] == 'error'
    assert result.json['progress'] == 0
    assert 'map_file' in result.json['error']
    assert client.post('/api/render_video', json={'render_id': 'invalid'}).status_code == 400


def test_completed_records_do_not_block_new_renders_at_capacity():
    app = Flask(__name__)
    tracker = RenderProgress()
    active_id, oldest_finished_id = str(uuid4()), str(uuid4())
    tracker.records[active_id] = dict(status='rendering', progress=0, updated_at=0)

    @app.post('/render')
    @tracker.track
    def render():
        return jsonify(status='success')

    with app.test_client() as client:
        assert client.post('/render', json={'render_id': oldest_finished_id}).status_code == 200
        for _ in range(255):
            assert client.post('/render', json={'render_id': str(uuid4())}).status_code == 200
        assert client.post('/render', json={'render_id': active_id}).status_code == 409

    assert tracker.snapshot(oldest_finished_id) is None
    assert tracker.snapshot(active_id)['status'] == 'rendering'
    assert len(tracker.records) == 256


def test_failed_render_does_not_claim_completion():
    app = Flask(__name__)
    tracker = RenderProgress()
    render_id = str(uuid4())

    @app.post('/render')
    @tracker.track
    def render():
        tracker.update(render_id, progress=50, phase='Encoding video')
        return jsonify(error='Encoder failed'), 500

    result = app.test_client().post('/render', json={'render_id': render_id})
    assert result.status_code == 500
    assert tracker.snapshot(render_id)['status'] == 'error'
    assert tracker.snapshot(render_id)['progress'] == 50
    assert tracker.snapshot(render_id)['error'] == 'Encoder failed'


def test_render_ids_are_isolated_and_percentages_monotonic():
    tracker = RenderProgress()
    tracker.records = {'first': {'progress': 0, 'status': 'rendering'}, 'second': {'progress': 0, 'status': 'rendering'}}
    tracker.update('first', progress=70)
    tracker.update('first', progress=30)
    tracker.update('unknown', progress=100)
    assert tracker.snapshot('first')['progress'] == 70
    assert tracker.snapshot('second')['progress'] == 0
    assert tracker.snapshot('unknown') is None


@pytest.mark.parametrize('status', [200, 404])
def test_web_progress_proxy(web, monkeypatch, status):
    render_id = str(uuid4())
    upstream = Mock(status_code=status)
    upstream.json.return_value = {'status': 'rendering', 'progress': 63, 'phase': 'Encoding video'}
    get = Mock(return_value=upstream)
    monkeypatch.setattr(requests, 'get', get)
    result = web.app.test_client().get(f'/api/render_progress/{render_id}')
    assert result.status_code == status
    assert result.json == upstream.json.return_value
    get.assert_called_once_with(f'{web.WORKER_URL}/api/render_progress/{render_id}', timeout=5)
    upstream.close.assert_called_once()


@pytest.mark.parametrize('failure', ['connection', 'json'])
def test_web_progress_errors_are_recoverable(web, monkeypatch, failure):
    upstream = Mock()
    upstream.json.side_effect = ValueError('bad json')
    get = Mock(side_effect=requests.Timeout()) if failure == 'connection' else Mock(return_value=upstream)
    monkeypatch.setattr(requests, 'get', get)
    result = web.app.test_client().get(f'/api/render_progress/{uuid4()}')
    assert result.status_code == 502


def test_render_progress_id_is_forwarded_to_worker(web, monkeypatch):
    render_id = str(uuid4())
    post = Mock(return_value=Mock(status_code=200, json=lambda: {'status': 'success'}))
    monkeypatch.setattr(requests, 'post', post)
    result = web.app.test_client().post('/api/render_video', json={'video_title': 'Test', 'render_id': render_id})
    assert result.status_code == 200
    assert post.call_args.kwargs['json']['render_id'] == render_id
