import io

import pytest
from rich.console import Console
from map import log


@pytest.mark.parametrize('verbose', [False, True])
def test_progress_logs_respect_verbose(monkeypatch, verbose):
    output = io.StringIO()
    monkeypatch.setattr(log, 'console', Console(file=output, color_system=None))
    log.set_verbose(verbose)
    assert log.is_verbose() is verbose
    for method in (log.info, log.step, log.success):
        method('progress')
    assert ('progress' in output.getvalue()) is verbose


def test_errors_warnings_settings_and_completion_always_visible(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(log, 'console', Console(file=output, color_system=None))
    log.warning('warning message')
    log.error('error message')
    log.config_table({'Mode': 'number'})
    log.finished('done message')
    for text in ('warning message', 'error message', 'Mode', 'number', 'done message'):
        assert text in output.getvalue()


def test_spinner_propagates_errors():
    with pytest.raises(RuntimeError, match='failed'):
        with log.spinner('Working'):
            raise RuntimeError('failed')
