"""Check Google OAuth and background upload contracts without real accounts.

Fake OAuth flows, credentials and YouTube API objects exercise state/PKCE, CSRF,
render-token ownership, encrypted storage, resumable uploads and slot cleanup.
Two-browser tests ensure tokens remain isolated across restarts. Failures must
never expose provider tokens or secrets to the client.
"""
from webapp import http_client
import io
import json
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from webapp.youtube import client_config, metadata

CLIENT = {'web': {'client_id': 'test.apps.googleusercontent.com', 'client_secret': 'test-secret',
                  'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                  'token_uri': 'https://oauth2.googleapis.com/token',
                  'redirect_uris': ['http://localhost/api/youtube/callback']}}


def setup_client(web, tmp_path):
    web.app.config['YOUTUBE_DATA_DIR'] = tmp_path / 'youtube'
    client = web.app.test_client()
    status = client.get('/api/youtube/status').json
    return client, {'X-YouTube-CSRF': status['csrf_token']}


def authorize(web, client):
    uploads = web.app.extensions['youtube_uploads']
    with client.session_transaction() as stored:
        owner = stored['youtube_owner']
        identity = stored.get('web_identity', '')
    import hashlib
    owner = hashlib.sha256((owner + identity).encode()).hexdigest()
    uploads.save_secret(uploads.token_path(owner), {
        'refresh_token': 'refresh-test', 'token': 'token-test',
        'client_id': CLIENT['web']['client_id'], 'client_secret': 'test-secret',
        'token_uri': CLIENT['web']['token_uri'], 'expiry': '2099-01-01T00:00:00Z',
    })
    return owner


@pytest.mark.parametrize('settings', [
    {'title': ''}, {'title': 'a' * 101}, {'title': '<bad>'},
    {'title': 'ok', 'description': '🌍' * 1251},
    {'title': 'ok', 'tags': ['a' * 501]}, {'title': 'ok', 'tags': [3]},
    {'title': 'ok', 'privacy': 'friends'}, {'title': 'ok', 'made_for_kids': 'false'},
])
def test_metadata_rejects_invalid_values(settings):
    with pytest.raises(ValueError):
        metadata(settings)


def test_metadata_preserves_unicode_and_normalizes_tags():
    body = metadata({'title': '  Карта світу  ', 'description': 'Опис\n🌍',
                     'tags': 'географія, maps, maps, ', 'privacy': 'unlisted'})
    assert body['snippet'] == {'title': 'Карта світу', 'description': 'Опис\n🌍',
                               'tags': ['географія', 'maps'], 'categoryId': '22'}
    assert body['status'] == {'privacyStatus': 'unlisted', 'selfDeclaredMadeForKids': False}


def test_untrusted_oauth_endpoints_are_rejected():
    with pytest.raises(ValueError):
        client_config({'web': {**CLIENT['web'], 'token_uri': 'https://example.test/token'}})


def test_status_and_csrf_do_not_expose_credentials(web, tmp_path):
    uploads = web.app.extensions['youtube_uploads']
    client, headers = setup_client(web, tmp_path)
    assert not client.get('/api/youtube/status').json['connected']
    owner = authorize(web, client)
    response = client.get('/api/youtube/status')
    assert response.json['connected'] and response.headers['Cache-Control'] == 'no-store'
    assert b'token-test' not in response.data and b'test-secret' not in response.data
    assert 'refresh-test' not in uploads.token_path(owner).read_text()
    assert 'test-secret' not in uploads.token_path(owner).read_text()
    assert uploads.token_path(owner).stat().st_mode & 0o777 == 0o600
    assert client.post('/api/youtube/disconnect').status_code == 403
    assert client.post('/api/youtube/disconnect', headers=headers).status_code == 200
    assert not client.get('/api/youtube/status').json['connected']


def test_oauth_connect_pkce_callback_and_replay(web, tmp_path, monkeypatch):
    from google_auth_oauthlib.flow import Flow
    client, headers = setup_client(web, tmp_path)
    response = client.post('/api/youtube/connect', headers=headers, data={
        'client_secret': (io.BytesIO(json.dumps(CLIENT).encode()), 'secret.json')})
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.json['authorization_url']).query)
    assert query['scope'] == ['https://www.googleapis.com/auth/youtube.upload']
    assert set(query['prompt'][0].split()) == {'consent', 'select_account'}
    assert query['access_type'] == ['offline'] and query['code_challenge_method'] == ['S256']
    with client.session_transaction() as cookie:
        assert 'test-secret' not in str(dict(cookie))
    fake = Mock()
    fake.credentials.refresh_token = 'refresh-test'
    fake.credentials.to_json.return_value = json.dumps({'refresh_token': 'refresh-test'})
    monkeypatch.setattr(Flow, 'from_client_config', Mock(return_value=fake))
    assert client.get('/api/youtube/callback?state=wrong&code=test').location.endswith('youtube=error')
    url = '/api/youtube/callback?state=' + query['state'][0] + '&code=code-test'
    assert client.get(url).location.endswith('youtube=connected')
    fake.fetch_token.assert_called_once_with(code='code-test')
    assert client.get(url).location.endswith('youtube=error')
    assert client.get('/api/youtube/status').json['connected']


def test_desktop_client_requires_localhost(web, tmp_path):
    client, headers = setup_client(web, tmp_path)
    response = client.post('/api/youtube/connect', base_url='https://host.test', headers=headers, data={
        'client_secret': (io.BytesIO(json.dumps({'installed': CLIENT['web']}).encode()), 'secret.json')})
    # Session cookies are scoped to localhost; host change also invalidates CSRF.
    assert response.status_code == 401
    client.post('/login', base_url='https://host.test', data={'login': 'deltaaa', 'password': 'deltaaa00!'})
    token = client.get('/api/youtube/status', base_url='https://host.test').json['csrf_token']
    response = client.post('/api/youtube/connect', base_url='https://host.test', headers={'X-YouTube-CSRF': token}, data={
        'client_secret': (io.BytesIO(json.dumps({'installed': CLIENT['web']}).encode()), 'secret.json')})
    assert response.status_code == 400 and 'localhost' in response.json['error']


def test_upload_rejects_forged_render_token(web, tmp_path):
    client, headers = setup_client(web, tmp_path)
    authorize(web, client)
    response = client.post('/api/youtube/upload', headers=headers, json={
        'upload_token': '/etc/passwd', 'youtube': {'title': 'Test'}})
    assert response.status_code == 400


def test_background_upload_metadata_progress_and_deduplication(web, tmp_path, monkeypatch):
    uploads = web.app.extensions['youtube_uploads']
    from googleapiclient import discovery
    client, headers = setup_client(web, tmp_path)
    authorize(web, client)
    youtube = Mock()
    upload = youtube.videos.return_value.insert.return_value
    upload.next_chunk.side_effect = [(Mock(progress=lambda: .5), None), (None, {'id': 'video_test'})]
    monkeypatch.setattr(discovery, 'build', Mock(return_value=youtube))
    upstream = Mock()
    upstream.__enter__ = Mock(return_value=upstream)
    upstream.__exit__ = Mock(return_value=False)
    upstream.iter_content.return_value = [b'fake rendered video']
    monkeypatch.setattr(http_client.worker_http, 'get', Mock(return_value=upstream))
    with web.app.test_request_context():
        # Copy the same signed session into this context to generate its render token.
        with client.session_transaction() as cookie:
            from flask import session
            session.update(cookie)
        token = uploads.render_token('/renders/test.mp4')
    payload = {'upload_token': token, 'youtube': {'title': 'Test', 'description': 'My description', 'tags': 'maps,earth', 'privacy': 'private'}}
    response = client.post('/api/youtube/upload', headers=headers, json=payload)
    assert response.status_code == 202
    job_id = response.json['job_id']
    for _ in range(100):
        result = client.get('/api/youtube/jobs/' + job_id)
        if result.json['status'] != 'uploading':
            break
        time.sleep(.01)
    assert result.json == {'status': 'complete', 'phase': 'Uploaded', 'progress': 100, 'url': 'https://www.youtube.com/shorts/video_test'}
    body = youtube.videos.return_value.insert.call_args.kwargs['body']
    assert body['snippet']['description'] == 'My description' and body['snippet']['tags'] == ['maps', 'earth']
    assert body['status']['privacyStatus'] == 'private'
    assert client.post('/api/youtube/upload', headers=headers, json=payload).json['job_id'] == job_id
    assert youtube.videos.return_value.insert.call_count == 1
    stranger, stranger_headers = setup_client(web, tmp_path)
    authorize(web, stranger)
    assert stranger.get('/api/youtube/jobs/' + job_id).status_code == 404
    assert stranger.post('/api/youtube/upload', headers=stranger_headers, json=payload).status_code == 400


def test_auto_upload_starts_only_after_successful_render(web, tmp_path, monkeypatch):
    uploads = web.app.extensions['youtube_uploads']
    client, headers = setup_client(web, tmp_path)
    authorize(web, client)
    post = Mock()
    monkeypatch.setattr(http_client.worker_http, 'post', post)
    start = Mock(return_value='job-test')
    monkeypatch.setattr(uploads, 'start', start)
    data = {'map_file': 'map', 'background_video': 'bg', 'video_title': 'Map', 'youtube': {'title': 'YouTube title'}}
    assert client.post('/api/render_video', json=data).status_code == 403
    assert client.post('/api/render_video', headers=headers, json={**data, 'youtube': {'title': ''}}).status_code == 400
    post.assert_not_called()
    for status, payload in [(500, {'status': 'error'}), (200, {'status': 'success', 'file_path': '/renders/test.mp4'})]:
        post.return_value = Mock(status_code=status, ok=status == 200)
        post.return_value.json.return_value = payload
        response = client.post('/api/render_video', headers=headers, json=data)
        if status == 500:
            start.assert_not_called()
        else:
            assert response.json['youtube_job_id'] == 'job-test'
            assert start.call_args.args[1]['snippet']['title'] == 'YouTube title'
            assert 'youtube' not in post.call_args.kwargs['json']


def test_two_people_keep_separate_encrypted_tokens_across_server_restart(web, tmp_path):
    uploads = web.app.extensions['youtube_uploads']
    from flask import Flask
    from webapp.youtube import YouTubeUploads
    first, first_headers = setup_client(web, tmp_path)
    second, second_headers = setup_client(web, tmp_path)
    first_owner = authorize(web, first)
    second_owner = authorize(web, second)
    second_secret = uploads.load_secret(uploads.token_path(second_owner))
    second_secret['refresh_token'] = 'second-person-refresh'
    uploads.save_secret(uploads.token_path(second_owner), second_secret)
    assert first_owner != second_owner
    restarted = Flask('youtube-restart')
    restarted.secret_key = web.app.secret_key
    restarted.config['YOUTUBE_DATA_DIR'] = tmp_path / 'youtube'
    service = YouTubeUploads(restarted, 'http://worker.test', Mock())
    assert service.credentials(first_owner).refresh_token == 'refresh-test'
    assert service.credentials(second_owner).refresh_token == 'second-person-refresh'
    first.post('/api/youtube/disconnect', headers=first_headers)
    assert not first.get('/api/youtube/status').json['connected']
    assert second.get('/api/youtube/status').json['connected']
    assert service.credentials(second_owner).refresh_token == 'second-person-refresh'


def test_failed_upload_never_exposes_secrets_and_releases_slot(web, tmp_path, monkeypatch):
    uploads = web.app.extensions['youtube_uploads']
    from google.oauth2.credentials import Credentials
    client, headers = setup_client(web, tmp_path)
    authorize(web, client)
    credentials = Credentials(token='valid-token')
    monkeypatch.setattr(uploads, 'credentials', lambda owner: credentials)
    monkeypatch.setattr(http_client.worker_http, 'get', Mock(side_effect=RuntimeError('SENSITIVE-secret-token')))
    with web.app.test_request_context():
        with client.session_transaction() as cookie:
            from flask import session
            session.update(cookie)
        token = uploads.render_token('/renders/failure.mp4')
    result = client.post('/api/youtube/upload', headers=headers, json={'upload_token': token, 'youtube': {'title': 'Test'}})
    job_id = result.json['job_id']
    for _ in range(100):
        status = client.get('/api/youtube/jobs/' + job_id)
        if status.json['status'] == 'error':
            break
        time.sleep(.01)
    assert status.json['status'] == 'error' and b'SENSITIVE' not in status.data
    assert uploads.slots.acquire(blocking=False)
    assert uploads.slots.acquire(blocking=False)
    uploads.slots.release()
    uploads.slots.release()


def test_studio_initializes_youtube_session_before_parallel_requests(web, tmp_path):
    web.app.config['YOUTUBE_DATA_DIR'] = tmp_path / 'youtube'
    client = web.app.test_client()
    assert client.get('/studio').status_code == 200
    with client.session_transaction() as cookie:
        owner = cookie['youtube_owner']
        token = cookie['youtube_csrf']
    # A late response from a read-only API must not replace a newer cookie.
    response = client.get('/api/youtube/status')
    assert response.json['csrf_token'] == token
    assert 'Set-Cookie' not in response.headers
    with client.session_transaction() as cookie:
        assert cookie['youtube_owner'] == owner
    response = client.post('/api/youtube/disconnect', headers={'X-YouTube-CSRF': token})
    assert response.status_code == 200


def test_csrf_rejection_identifies_safe_to_retry_requests(web, tmp_path):
    client, headers = setup_client(web, tmp_path)
    response = client.post('/api/youtube/disconnect', headers={'X-YouTube-CSRF': 'stale'})
    assert response.status_code == 403 and response.json['code'] == 'youtube_csrf_invalid'
    assert client.post('/api/youtube/disconnect', headers=headers).status_code == 200


@pytest.mark.parametrize('origin', ['http://localhost:3000', 'http://127.0.0.1:5000'])
def test_local_mode_uses_local_callback_even_with_hosted_override(web, monkeypatch, origin):
    uploads = web.app.extensions['youtube_uploads']
    web.app.config['WEB_AUTH_ENABLED'] = False
    web.app.config['YOUTUBE_REDIRECT_URI'] = 'https://hosted.example/api/youtube/callback'
    monkeypatch.setenv('YOUTUBE_REDIRECT_URI', 'https://old-host.example/api/youtube/callback')
    with web.app.test_request_context(base_url=origin):
        assert uploads.callback_uri() == origin + '/api/youtube/callback'


def test_server_mode_keeps_external_callback_behind_reverse_proxy(web, monkeypatch):
    uploads = web.app.extensions['youtube_uploads']
    web.app.config['WEB_AUTH_ENABLED'] = True
    monkeypatch.setenv('YOUTUBE_REDIRECT_URI', 'https://auto-map.duckdns.org/api/youtube/callback')
    with web.app.test_request_context(base_url='http://localhost:5000'):
        assert uploads.callback_uri() == 'https://auto-map.duckdns.org/api/youtube/callback'
    web.app.config['YOUTUBE_REDIRECT_URI'] = 'https://configured.example/api/youtube/callback'
    with web.app.test_request_context(base_url='http://localhost:5000'):
        assert uploads.callback_uri() == 'https://configured.example/api/youtube/callback'
