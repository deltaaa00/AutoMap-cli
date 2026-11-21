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

"""Malformed untrusted login/logout input must not raise server errors."""
from test_web_auth import auth_app


def test_unicode_logout_token_is_rejected(auth_app):
    client = auth_app().test_client()
    client.post('/login', data={'login': 'test-admin', 'password': 'test-only-password'})
    assert client.post('/logout', data={'csrf_token': 'невірний-токен'}).status_code == 400
    assert client.get('/api/status').status_code == 200


def test_control_characters_in_next_do_not_crash_login(auth_app):
    client = auth_app().test_client()
    response = client.post('/login', data={
        'login': 'test-admin', 'password': 'test-only-password', 'next': '/\t/[invalid-ipv6',
    })
    assert response.status_code == 302
    assert response.location == '/'


def test_cross_origin_login_does_not_set_session(auth_app):
    client = auth_app().test_client()
    credentials = {'login': 'test-admin', 'password': 'test-only-password'}
    response = client.post('/login', data=credentials, headers={'Origin': 'https://other.example'})
    assert response.status_code == 403
    assert client.get('/api/status').status_code == 401
    response = client.post('/login', data=credentials, headers={'Origin': 'http://localhost'})
    assert response.status_code == 302
    assert client.get('/api/status').status_code == 200


def test_local_mode_accepts_only_configured_hosts(auth_app, monkeypatch):
    monkeypatch.setenv('SERVER', 'FALSE')
    client = auth_app().test_client()
    assert client.get('http://localhost/').status_code == 200
    assert client.get('http://external.example/').status_code == 400
