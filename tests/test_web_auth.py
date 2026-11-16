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

"""Check the login boundary using a real Flask session cookie."""
from pathlib import Path

import pytest
from flask import Flask

from web_auth import init_web_auth


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    monkeypatch.setenv('SERVER', 'TRUE')
    for name in ('WEB_LOGIN', 'WEB_PASSWORD', 'WEB_SECRET_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('WEB_AUTH_SECRET_FILE', str(tmp_path / 'secret'))

    def create(settings=None):
        app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / 'templates'))
        app.config['TESTING'] = True
        init_web_auth(app, settings)
        app.add_url_rule('/', 'home', lambda: 'home')
        app.add_url_rule('/studio', 'studio', lambda: 'studio')
        app.add_url_rule('/api/status', 'status', lambda: {'ok': True})
        return app
    return create


@pytest.mark.parametrize('value', [None, 'FALSE', 'true', '1', ''])
def test_development_does_not_require_login(auth_app, monkeypatch, value):
    if value is None:
        monkeypatch.delenv('SERVER', raising=False)
    else:
        monkeypatch.setenv('SERVER', value)
    client = auth_app().test_client()
    assert client.get('/').status_code == 200
    assert client.get('/studio').status_code == 200
    assert client.get('/api/status').status_code == 200
    assert client.get('/login').location == '/'


def test_unauthenticated_pages_and_api(auth_app):
    client = auth_app().test_client()
    assert client.get('/').location == '/login?next=/'
    assert client.get('/studio').status_code == 302
    assert client.get('/api/status').status_code == 401
    page = client.get('/login')
    assert page.status_code == 200
    assert b'<html lang="en">' in page.data
    assert b'Sign in' in page.data and b'Username' in page.data and b'Password' in page.data
    failure = client.post('/login', data={'login': 'deltaaa', 'password': 'wrong'})
    assert failure.status_code == 401
    assert b'Invalid username or password.' in failure.data
    assert client.get('/api/status').status_code == 401


def test_default_login_cookie_survives_new_client_and_restart(auth_app):
    first = auth_app().test_client()
    response = first.post('/login', data={'login': 'deltaaa', 'password': 'deltaaa00!', 'next': '/studio'})
    assert response.location == '/studio'
    cookie_header = response.headers['Set-Cookie']
    assert 'Expires=' in cookie_header and 'HttpOnly' in cookie_header and 'SameSite=Lax' in cookie_header
    cookie = first.get_cookie('session')
    restarted = auth_app().test_client()
    restarted.set_cookie('session', cookie.value)
    assert restarted.get('/studio').status_code == 200
    assert restarted.get('/api/status').json == {'ok': True}
    with restarted.session_transaction() as session:
        assert 'password' not in session and 'login' not in session
        csrf = session['logout_csrf']
    assert restarted.post('/logout').status_code == 400
    assert restarted.post('/logout', data={'csrf_token': csrf}).location == '/login'
    assert restarted.get('/api/status').status_code == 401


def test_environment_credentials_and_rotation(auth_app, monkeypatch):
    first = auth_app().test_client()
    first.post('/login', data={'login': 'deltaaa', 'password': 'deltaaa00!'})
    old_cookie = first.get_cookie('session').value
    monkeypatch.setenv('WEB_LOGIN', 'owner')
    monkeypatch.setenv('WEB_PASSWORD', 'новий-secret')
    client = auth_app().test_client()
    client.set_cookie('session', old_cookie)
    assert client.get('/api/status').status_code == 401
    assert client.post('/login', data={'login': 'deltaaa', 'password': 'deltaaa00!'}).status_code == 401
    assert client.post('/login', data={'login': 'owner', 'password': 'новий-secret'}).status_code == 302
    assert client.get('/api/status').status_code == 200


@pytest.mark.parametrize('target', ['https://evil.example', '//evil.example', '/\\evil.example'])
def test_login_rejects_external_redirects(auth_app, target):
    response = auth_app().test_client().post('/login', data={
        'login': 'deltaaa', 'password': 'deltaaa00!', 'next': target,
    })
    assert response.location == '/'


def test_dotenv_credentials_and_env_precedence(auth_app, monkeypatch):
    monkeypatch.setenv('WEB_LOGIN', 'environment-user')
    client = auth_app({'WEB_LOGIN': 'dotenv-user', 'WEB_PASSWORD': 'dotenv-password'}).test_client()
    assert client.post('/login', data={
        'login': 'environment-user', 'password': 'dotenv-password',
    }).status_code == 302
