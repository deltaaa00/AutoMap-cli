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

"""Keep malformed login/logout input from escaping the authentication boundary.

Uses the isolated app factory from test_web_auth. Unicode logout tokens and
control characters in redirect targets must produce controlled responses
without crashing the server or clearing a valid session unexpectedly.
"""
from test_web_auth import auth_app


def test_unicode_logout_token_is_rejected(auth_app):
    client = auth_app().test_client()
    client.post('/login', data={'login': 'deltaaa', 'password': 'deltaaa00!'})
    assert client.post('/logout', data={'csrf_token': 'невірний-токен'}).status_code == 400
    assert client.get('/api/status').status_code == 200


def test_control_characters_in_next_do_not_crash_login(auth_app):
    client = auth_app().test_client()
    response = client.post('/login', data={
        'login': 'deltaaa', 'password': 'deltaaa00!', 'next': '/\t/[invalid-ipv6',
    })
    assert response.status_code == 302
    assert response.location == '/'
