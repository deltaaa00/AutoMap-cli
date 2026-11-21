"""Protect application assembly and project-relative asset lookup.

These regressions create separate apps to ensure worker configuration does not
leak between them, then verify templates, static files, fonts and CSV downloads
when the caller starts outside the project directory.
"""
from pathlib import Path
from unittest.mock import Mock

import dotenv
import pytest

from webapp import create_app, http_client


@pytest.fixture
def local_app_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda *args, **kwargs: {})
    monkeypatch.setenv('SERVER', 'FALSE')
    monkeypatch.setenv('WEB_AUTH_SECRET_FILE', str(tmp_path / 'session-secret'))


def test_separate_apps_use_their_own_worker_configuration(local_app_settings, monkeypatch):
    monkeypatch.setenv('YTDLP_URL', 'http://first-worker:8080/')
    first_app = create_app()
    monkeypatch.setenv('YTDLP_URL', 'http://second-worker:9090/')
    second_app = create_app()
    get = Mock(return_value=Mock(status_code=200, json=lambda: {'codecs': ['libx264']}))
    monkeypatch.setattr(http_client.worker_http, 'get', get)

    for app, expected_url in (
        (first_app, 'http://first-worker:8080/api/codecs'),
        (second_app, 'http://second-worker:9090/api/codecs'),
    ):
        assert app.test_client().get('/api/codecs').status_code == 200
        assert get.call_args.args == (expected_url,)


def test_templates_and_fonts_work_from_another_directory(local_app_settings, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = app.test_client()

    assert Path.cwd() == tmp_path
    assert client.get('/').status_code == 200
    assert 'Bips.ttf' in client.get('/api/fonts').json['fonts']
    assert client.get('/api/batch-template').status_code == 200
    assert client.get('/static/studio.css').status_code == 200
