"""API Manager shares credentials without rendering server secrets."""
import time


def test_manager_page_exposes_status_without_keys(web, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'server-gemini-do-not-render')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'server-deepseek-do-not-render')
    monkeypatch.setenv('TAVILY_API_KEY', 'server-tavily-do-not-render')
    response = web.app.test_client().get('/api-manager')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    text = response.get_data(as_text=True)
    for secret in ('server-gemini-do-not-render', 'server-deepseek-do-not-render', 'server-tavily-do-not-render'):
        assert secret not in text
    assert text.count('data-server="true"') == 3
    for field in ('geminiKey', 'deepseekKey', 'tavilyKey', 'managerYoutubeSecret'):
        assert f'id="{field}"' in text


def test_oauth_cancellation_returns_to_manager(web, tmp_path):
    web.app.config['YOUTUBE_DATA_DIR'] = tmp_path / 'youtube'
    client = web.app.test_client()
    client.get('/api-manager')
    with client.session_transaction() as session:
        import hashlib
        owner = hashlib.sha256((session['youtube_owner'] + session.get('web_identity', '')).encode()).hexdigest()
    pending = web.youtube_uploads.token_path(owner).with_suffix('.pending')
    web.youtube_uploads.save_secret(pending, {'state': 'test-state', 'created': time.time(), 'return_to': '/api-manager'})
    response = client.get('/api/youtube/callback?state=test-state&error=access_denied')
    assert response.location == '/api-manager?youtube=denied'


def test_oauth_return_target_is_allowlisted(web, tmp_path):
    web.app.config['YOUTUBE_DATA_DIR'] = tmp_path / 'youtube'
    client = web.app.test_client()
    client.get('/api-manager')
    with client.session_transaction() as session:
        import hashlib
        owner = hashlib.sha256((session['youtube_owner'] + session.get('web_identity', '')).encode()).hexdigest()
    pending = web.youtube_uploads.token_path(owner).with_suffix('.pending')
    web.youtube_uploads.save_secret(pending, {'state': 'test-state', 'created': time.time(), 'return_to': 'https://evil.example'})
    response = client.get('/api/youtube/callback?state=test-state&error=access_denied')
    assert response.location == '/studio?youtube=denied'
