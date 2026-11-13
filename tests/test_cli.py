import importlib
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

import cli
from map import log


@pytest.fixture
def generate(monkeypatch, tmp_path):
    generate = Mock(return_value=tmp_path / 'result.png')
    monkeypatch.setattr(importlib.import_module('map.auto_map'), 'auto_map', generate)
    return generate


@pytest.mark.parametrize('args,status', [([], 2), (['--help'], 0), (['make', '--help'], 0)])
def test_help(args, status):
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == status
    assert 'Usage:' in result.output


def test_make_defaults(generate):
    result = CliRunner().invoke(cli.main, ['make', 'Population'])
    assert result.exit_code == 0, result.output
    generate.assert_called_once_with(prompt='Population', mode='text', map_type='default',
        size_mod=15, optional_feature=None, model='gemini-2.5-flash', font_name='Bips.ttf', show_result=False)


def test_make_options(generate, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'previous')
    result = CliRunner().invoke(cli.main, ['make', 'GDP', '-m', 'NUMBER', '-mp', 'WORLD',
        '-gsm', '22', '-eop', 'SHOW_MOST_LEAST', '--font', 'DIN Condensed.ttf',
        '--model', 'gemini-2.5-pro', '--show', '--key', 'test-override', '-v'])
    assert result.exit_code == 0, result.output
    assert generate.call_args.kwargs == dict(prompt='GDP', mode='number', map_type='world',
        size_mod=22, optional_feature='show_most_least', model='gemini-2.5-pro',
        font_name='DIN Condensed.ttf', show_result=True)
    assert log.is_verbose()
    import os
    assert os.environ['GEMINI_API_KEY'] == 'test-override'


@pytest.mark.parametrize('args', [
    ['make'], ['make', 'topic', '-m', 'wrong'], ['make', 'topic', '-mp', 'wrong'],
    ['make', 'topic', '--font', '../../bad.ttf'], ['make', 'topic', '--model', 'wrong'],
    ['make', 'topic', '-gsm', 'bad'], ['make', 'topic', '-eop', 'wrong'],
])
def test_invalid_arguments_do_not_generate(generate, args):
    assert CliRunner().invoke(cli.main, args).exit_code == 2
    generate.assert_not_called()


def test_failed_generation_returns_error(generate):
    generate.return_value = None
    result = CliRunner().invoke(cli.main, ['make', 'topic'])
    assert result.exit_code == 1
    assert 'Map generation failed' in result.output


@pytest.mark.parametrize('module_name', ['cli', 'main'])
def test_font_discovery(module_name, web, tmp_path, monkeypatch):
    module = cli if module_name == 'cli' else web
    for name in ['z.ttf', 'A.OTF', 'ignore.txt']:
        (tmp_path / name).touch()
    monkeypatch.setattr(module, 'FONTS_DIR', tmp_path)
    assert module._available_fonts() == ['A.OTF', 'z.ttf']
    monkeypatch.setattr(module, 'FONTS_DIR', tmp_path / 'missing')
    assert module._available_fonts() == []
