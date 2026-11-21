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

"""Verify server selection without opening ports.

Patched Flask/Waitress calls check exact SERVER=TRUE handling, environment versus
.env precedence and different web/worker default ports. Networking and process
launching remain outside this unit suite.
"""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import server_runtime


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(server_runtime, "dotenv_values", lambda _: {})
    monkeypatch.delenv("SERVER", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    serve = Mock()
    monkeypatch.setitem(sys.modules, "waitress", SimpleNamespace(serve=serve))
    return SimpleNamespace(debug=True, run=Mock()), serve


def test_production_uses_waitress(runtime, monkeypatch):
    app, serve = runtime
    monkeypatch.setenv("SERVER", "TRUE")
    monkeypatch.setenv("PORT", "5051")
    server_runtime.run_app(app)
    assert app.debug is False
    app.run.assert_not_called()
    serve.assert_called_once_with(app, host="0.0.0.0", port=5051, threads=8, channel_timeout=600)


def test_development_uses_flask(runtime):
    app, serve = runtime
    server_runtime.run_app(app)
    app.run.assert_called_once_with(host="0.0.0.0", port=5000, debug=True)
    serve.assert_not_called()


@pytest.mark.parametrize("value", ["true", "True", " TRUE ", "FALSE", "1", ""])
def test_only_exact_true_enables_production(runtime, monkeypatch, value):
    app, serve = runtime
    monkeypatch.setenv("SERVER", value)
    server_runtime.run_app(app)
    app.run.assert_called_once_with(host="0.0.0.0", port=5000, debug=True)
    serve.assert_not_called()


def test_dotenv_production_and_worker_port(runtime, monkeypatch):
    app, serve = runtime
    monkeypatch.setattr(server_runtime, "dotenv_values", lambda _: {"SERVER": "TRUE"})
    server_runtime.run_app(app, default_port=8080)
    assert serve.call_args.kwargs["port"] == 8080
    assert app.debug is False


def test_environment_overrides_dotenv(runtime, monkeypatch):
    app, serve = runtime
    monkeypatch.setattr(server_runtime, "dotenv_values", lambda _: {"SERVER": "TRUE", "PORT": "1234"})
    monkeypatch.setenv("SERVER", "FALSE")
    monkeypatch.setenv("PORT", "5001")
    server_runtime.run_app(app)
    app.run.assert_called_once_with(host="0.0.0.0", port=5001, debug=True)
    serve.assert_not_called()
