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

"""Shared isolation: tests never use real credentials or external services."""
import importlib.util
import io
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from flask.testing import FlaskClient
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]


class AuthenticatedWebClient(FlaskClient):
    """Existing API/UI tests exercise an explicitly signed-in browser."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        response = self.post('/login', data={
            'login': os.environ.get('WEB_LOGIN', 'test-admin'),
            'password': os.environ.get('WEB_PASSWORD', 'test-only-password'),
        })
        assert response.status_code == 302


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch):
    from map import functions, log

    # Fail immediately if a test reaches a real network or AI client by accident.
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key-never-send')
    monkeypatch.setattr(requests.sessions.Session, 'request', Mock(
        side_effect=AssertionError('Unexpected network request; mock the external service'),
    ))
    monkeypatch.setattr(functions.genai, 'Client', Mock(
        side_effect=AssertionError('Unexpected AI request; mock genai.Client'),
    ))
    monkeypatch.setattr(log, 'console', Console(file=io.StringIO(), color_system=None))
    monkeypatch.setattr(log, '_verbose', False)


@pytest.fixture
def worker(tmp_path, monkeypatch):
    # Load a fresh worker module because its filename cannot be imported normally.
    spec = importlib.util.spec_from_file_location('video_worker', ROOT / 'yt-dlp_serv.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Keep each test's media files separate from shared worker storage.
    for name in ('STORAGE_DIR', 'RENDER_DIR', 'DOWNLOADS', 'CUT_DIR'):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setattr(module, name, str(folder))
    module.app.config.update(TESTING=True)
    return module


@pytest.fixture
def web(monkeypatch, tmp_path):
    import dotenv

    # Ignore the developer's .env and avoid main.py changing the test cwd.
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda *args, **kwargs: {})
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setenv('SERVER', 'TRUE')
    monkeypatch.setenv('WEB_LOGIN', 'test-admin')
    monkeypatch.setenv('WEB_PASSWORD', 'test-only-password')
    monkeypatch.setenv('WEB_COOKIE_SECURE', 'FALSE')
    monkeypatch.setenv('WEB_AUTH_SECRET_FILE', str(tmp_path / 'session-secret'))
    monkeypatch.chdir(ROOT)
    spec = importlib.util.spec_from_file_location('web_app', ROOT / 'main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True)
    module.app.test_client_class = AuthenticatedWebClient
    return module
