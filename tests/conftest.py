"""Shared isolation: tests never use real credentials or external services."""
import importlib.util
import io
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch):
    from map import functions, log

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
    spec = importlib.util.spec_from_file_location('video_worker', ROOT / 'yt-dlp_serv.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('STORAGE_DIR', 'RENDER_DIR', 'DOWNLOADS', 'CUT_DIR'):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setattr(module, name, str(folder))
    module.app.config.update(TESTING=True)
    return module


@pytest.fixture
def web(monkeypatch):
    import dotenv

    # Ignore the developer's .env and avoid main.py changing the test cwd.
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda *args, **kwargs: {})
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.chdir(ROOT)
    spec = importlib.util.spec_from_file_location('web_app', ROOT / 'main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True)
    return module
