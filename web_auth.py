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

"""Password gate and persistent browser sessions for the web interface."""
import hashlib
import hmac
import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import jsonify, redirect, render_template, request, session, url_for


def _session_secret(app, settings):
    """Load or atomically create the Flask session signing secret."""
    configured = os.environ.get('WEB_SECRET_KEY') or settings.get('WEB_SECRET_KEY')
    if configured:
        return configured
    secret_path = Path(os.environ.get('WEB_AUTH_SECRET_FILE') or settings.get('WEB_AUTH_SECRET_FILE')
                       or Path(app.instance_path) / 'web-session-secret')
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if not secret_path.exists():
        # Publish a completely written file atomically for multiple WSGI workers.
        with tempfile.NamedTemporaryFile(mode='w', dir=secret_path.parent, delete=False) as stream:
            stream.write(secrets.token_hex(32))
            temporary = Path(stream.name)
        try:
            os.link(temporary, secret_path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    secret = secret_path.read_text().strip()
    if not secret:
        raise RuntimeError('The web session secret file is empty.')
    return secret


def _safe_next(value):
    """Validate a post-login redirect target so it stays on the same origin."""
    if (not value or not value.startswith('/') or value.startswith('//') or '\\' in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return '/'
    parts = urlsplit(value)
    return value if not parts.scheme and not parts.netloc else '/'


def init_web_auth(app, env_values=None):
    """Install the login gate without storing credentials in the browser cookie."""
    settings = env_values or {}
    server_mode = os.environ.get('SERVER', settings.get('SERVER') or 'FALSE') == 'TRUE'
    app.config['WEB_AUTH_ENABLED'] = server_mode
    login = os.environ.get('WEB_LOGIN', settings.get('WEB_LOGIN') or 'deltaaa')
    password = os.environ.get('WEB_PASSWORD', settings.get('WEB_PASSWORD') or 'deltaaa00!')
    app.secret_key = _session_secret(app, settings)
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=365),
        # Read-only requests must not overwrite a newer session cookie from
        # a parallel OAuth/status request.
        SESSION_REFRESH_EACH_REQUEST=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
    )
    identity = hmac.new(str(app.secret_key).encode(),
                        (login + '\0' + password).encode(), hashlib.sha256).hexdigest()

    def authenticated():
        value = session.get('web_identity', '')
        return isinstance(value, str) and hmac.compare_digest(value, identity)

    @app.before_request
    def require_web_login():
        if not server_mode:
            return None
        if request.endpoint in {'web_login', 'static'} or authenticated():
            return None
        if request.path.startswith('/api/'):
            return jsonify(status='error', error='Authentication required.',
                           login_url=url_for('web_login')), 401
        return redirect(url_for('web_login', next=request.full_path.rstrip('?')))

    @app.route('/login', methods=['GET', 'POST'])
    def web_login():
        if not server_mode:
            return redirect('/')
        target = _safe_next(request.values.get('next'))
        if authenticated():
            return redirect(target)
        error = None
        if request.method == 'POST':
            supplied_login = request.form.get('login', '')
            supplied_password = request.form.get('password', '')
            valid_login = hmac.compare_digest(supplied_login.encode(), login.encode())
            valid_password = hmac.compare_digest(supplied_password.encode(), password.encode())
            if valid_login and valid_password:
                session.clear()
                session.permanent = True
                session['web_identity'] = identity
                session['logout_csrf'] = secrets.token_urlsafe(32)
                return redirect(target)
            error = 'Invalid username or password.'
        response = app.make_response((render_template('login.html', error=error, next_url=target),
                                      401 if error else 200))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/logout')
    def web_logout():
        csrf = request.form.get('csrf_token', '')
        if not csrf or not hmac.compare_digest(csrf.encode(), session.get('logout_csrf', '').encode()):
            return jsonify(error='Invalid logout token.'), 400
        session.clear()
        return redirect(url_for('web_login'))
