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

import importlib
import io
import re
from pathlib import Path
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
        size_mod=15, optional_feature=None, model='gemini-3.8-flash', font_name='Bips.ttf', show_result=False)


def test_make_options(generate, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'previous')
    result = CliRunner().invoke(cli.main, ['make', 'GDP', '-m', 'NUMBER', '-mp', 'WORLD',
        '-gsm', '22', '-eop', 'SHOW_MOST_LEAST', '--font', 'DIN Condensed.ttf',
        '--model', 'gemini-4-carbon', '--show', '--key', 'test-override', '-v'])
    assert result.exit_code == 0, result.output
    assert generate.call_args.kwargs == dict(prompt='GDP', mode='number', map_type='world',
        size_mod=22, optional_feature='show_most_least', model='gemini-4-carbon',
        font_name='DIN Condensed.ttf', show_result=True)
    assert log.is_verbose()
    import os
    assert os.environ['GEMINI_API_KEY'] == 'test-override'


@pytest.mark.parametrize('model', [
    'gemini-4-hydrogen', 'gemini-4-carbon', 'gemini-4-argon',
    'gemma-4-31b-it', 'gemma-4-26b-a4b-it',
])
def test_current_gemini_and_gemma_models_reach_generation(generate, model):
    result = CliRunner().invoke(cli.main, ['make', 'Population', '--model', model])
    assert result.exit_code == 0, result.output
    assert generate.call_args.kwargs['model'] == model


def test_web_fallback_models_match_cli_choices():
    html = (Path(__file__).resolve().parents[1] / 'templates' / 'index.html').read_text(encoding='utf-8')
    selector = re.search(r'<select id="modelSelect"[^>]*>(.*?)</select>', html, re.DOTALL)
    assert selector is not None
    choices = re.findall(r'<option value="([^"]+)"', selector.group(1))
    assert choices == cli.AVAILABLE_MODELS
    assert not any(model.startswith(('gemini-1.5-', 'gemini-2.0-', 'gemini-2.5-')) for model in choices)


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


def test_batch_honors_csv_palette_and_output_directory(generate, tmp_path):
    generate.return_value = io.BytesIO(b'png-content')
    ideas = tmp_path / 'ideas.csv'
    ideas.write_text('prompt,palette_theme,output\nPopulation,greens,../selected.png\n')
    output = tmp_path / 'maps'
    result = CliRunner().invoke(cli.main, ['batch', str(ideas), '-o', str(output)])
    assert result.exit_code == 0, result.output
    assert generate.call_args.kwargs['palette_theme'] == 'greens'
    assert generate.call_args.kwargs['auto_style'] is True
    assert (output / 'selected.png').read_bytes() == b'png-content'
    assert not (tmp_path / 'selected.png').exists()


@pytest.mark.parametrize('value', ['0', '0.4', '2'])
def test_cli_temperature_is_forwarded(generate, value):
    result = CliRunner().invoke(cli.main, ['make', 'Population', '--temperature', value])
    assert result.exit_code == 0, result.output
    assert generate.call_args.kwargs['temperature'] == float(value)


@pytest.mark.parametrize('value', ['-1', '3', 'nan', 'inf', 'invalid'])
def test_cli_rejects_invalid_temperature_without_generating(generate, value):
    result = CliRunner().invoke(cli.main, ['make', 'Population', '--temperature', value])
    assert result.exit_code == 2
    generate.assert_not_called()


@pytest.mark.parametrize('module_name', ['cli', 'main'])
def test_font_discovery(module_name, web, tmp_path, monkeypatch):
    module = cli if module_name == 'cli' else web
    for name in ['z.ttf', 'A.OTF', 'ignore.txt']:
        (tmp_path / name).touch()
    monkeypatch.setattr(module, 'FONTS_DIR', tmp_path)
    assert module._available_fonts() == ['A.OTF', 'z.ttf']
    monkeypatch.setattr(module, 'FONTS_DIR', tmp_path / 'missing')
    assert module._available_fonts() == []


def test_deepseek_cli_key_does_not_replace_gemini_key(generate, monkeypatch):
    import os
    monkeypatch.setenv('GEMINI_API_KEY', 'keep-gemini')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'previous-deepseek')
    result = CliRunner().invoke(cli.main, ['make', 'Population', '--model', 'deepseek-flash', '--key', 'deepseek-cli-key'])
    assert result.exit_code == 0, result.output
    assert os.environ['GEMINI_API_KEY'] == 'keep-gemini'
    assert os.environ['DEEPSEEK_API_KEY'] == 'deepseek-cli-key'
    assert generate.call_args.kwargs['model'] == 'deepseek-flash'
