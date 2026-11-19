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
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

renderer = importlib.import_module('map.auto_map')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def _scene_assets(tmp_path_factory):
    """Create minimal 640x640 PNGs once per module so renderer caches hit across tests."""
    root = tmp_path_factory.mktemp('automap_scene')
    (root / 'map').mkdir()
    maps_dir = root / 'resources' / 'maps'
    maps_dir.mkdir(parents=True)
    base = Image.new('RGBA', (640, 640), 'white')
    draw = ImageDraw.Draw(base)
    draw.line((210, 0, 210, 639), fill='black')
    draw.line((420, 0, 420, 639), fill='black')
    for name in ('default_map.png', 'flag_map.png', 'world_map.png'):
        base.save(maps_dir / name)
    empty = Image.new('RGBA', (640, 640))
    for name in ('map_relief.png', 'default_map_blured.png', 'world_map_relief.png', 'world_map_blured.png'):
        empty.save(maps_dir / name)
    return root


@pytest.fixture
def scene(monkeypatch, _scene_assets):
    """Small real Pillow images keep compositing tests quick and deterministic."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv('AUTOMAP_REGION_CACHE_DIR', str(_scene_assets / 'rcache'))
    monkeypatch.setattr(renderer, '__file__', str(_scene_assets / 'map' / 'auto_map.py'))
    coords = {'one': [(80, 80)], 'two': [(290, 80)], 'three': [(500, 80)]}
    monkeypatch.setattr(renderer.constants, 'europe_coords', coords)
    monkeypatch.setattr(renderer.constants, 'world_coords', coords)
    answer = Mock(return_value={'one': 'Yes', 'two': 'no', 'three': 'yes'})
    monkeypatch.setattr(renderer, 'ai_request', answer)
    monkeypatch.setattr(renderer, 'load_filling', lambda: (
        {'YES': (255, 0, 0, 255), ' no ': (0, 0, 255, 255)},
        {(0, 10): (0, 255, 0, 255), (10, 20): (255, 255, 0, 255)},
    ))
    random = Mock(return_value=160)
    monkeypatch.setattr(renderer.random, 'randint', random)
    return answer, random, Image.open


def render(**changes):
    args = dict(prompt='topic', mode='text', map_type='default', size_mod=0,
                optional_feature=None, model='mock-model', font_name='Bips.ttf',
                show_result=False, io_res=True, api_key='request-key')
    args.update(changes)
    return renderer.auto_map(**args)


@pytest.mark.parametrize('map_type', ['default', 'flag', 'world'])
def test_render_map_types(scene, map_type):
    answer, _, real_open = scene
    output = render(map_type=map_type)
    assert isinstance(output, io.BytesIO)
    assert output.tell() == 0
    image = real_open(output)
    assert image.format == 'PNG' and image.size == (640, 640)
    assert image.getpixel((80, 600)) == ((255, 255, 255, 255) if map_type == 'flag' else (255, 0, 0, 255))
    answer.assert_called_once_with('topic', map_type, 'mock-model', 'request-key')


def test_text_colors_ignore_case_and_whitespace(scene):
    answer, _, real_open = scene
    answer.return_value = {'one': ' YES ', 'two': ' NO ', 'three': ''}
    image = real_open(render())
    assert image.getpixel((80, 600)) == (255, 0, 0, 255)
    assert image.getpixel((290, 600)) == (0, 0, 255, 255)
    assert image.getpixel((500, 600)) == (255, 255, 255, 255)


@pytest.mark.parametrize('map_type', ['default', 'flag'])
@pytest.mark.parametrize('label,expected_x', [('A', 80), ('AB', 50), ('ABC', 50), ('ABCD', 10), ('ABCDE', 10)])
def test_label_offset_for_short_and_long_text(scene, monkeypatch, map_type, label, expected_x):
    scene[0].return_value = {'one': label}
    draw_text = Mock()
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', draw_text)
    render(map_type=map_type)
    assert draw_text.call_count == 1
    assert draw_text.call_args.args[0][0] == expected_x
    assert draw_text.call_args.args[1] == label


@pytest.mark.parametrize('label,expected', [
    (' dog ', 'Dog'), ('dog breed', 'Dog breed'),
    ('iPhone', 'IPhone'), ('USA', 'USA'), ('кіт', 'Кіт'),
])
def test_text_labels_start_uppercase_on_countries_and_legend(scene, monkeypatch, label, expected):
    scene[0].return_value = {'one': label}
    draw_text = Mock()
    legend = Mock(return_value=None)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', draw_text)
    monkeypatch.setattr(renderer, '_draw_legend_overlay', legend)
    render(show_legend=True)
    assert draw_text.call_args.args[1] == expected
    assert [entry[1] for entry in legend.call_args.args[1]] == [expected]


def test_unknown_text_reuses_color(scene):
    answer, random, real_open = scene
    answer.return_value = {'one': 'unknown', 'two': 'unknown'}
    image = real_open(render())
    assert image.getpixel((80, 600)) == image.getpixel((290, 600)) == (160, 160, 160, 255)
    assert random.call_count == 3


@pytest.mark.parametrize('value,expected', [
    (0, (160, 160, 160, 255)), (1, (0, 255, 0, 255)),
    ('10', (0, 255, 0, 255)), (11, (255, 255, 0, 255)),
    (20, (255, 255, 0, 255)), (21, (160, 160, 160, 255)),
    ('unknown', (160, 160, 160, 255)), (None, (160, 160, 160, 255)),
])
def test_numeric_ranges_use_exclusive_low_inclusive_high(scene, value, expected):
    answer, _, real_open = scene
    answer.return_value = {'one': value}
    assert real_open(render(mode='number')).getpixel((80, 600)) == expected


@pytest.mark.parametrize('mode', ['text', 'number'])
def test_short_form_changes_ai_prompt_and_abbreviates_large_numbers(scene, monkeypatch, mode):
    answer, _, _ = scene
    answer.return_value = {'one': 4_000_000, 'two': '30,000', 'three': '10B'}
    draw_text = Mock()
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', draw_text)
    buf = render(mode=mode, optional_feature='short_form', auto_style=True)
    assert '1000000=1M' in answer.call_args.args[0]
    assert [call.args[1] for call in draw_text.call_args_list] == ['4M', '30k', '10B']
    assert buf.map_meta['ai_answer'] == {'one': '4M', 'two': '30k', 'three': '10B'}


@pytest.mark.parametrize('raw,expected', [
    (999, 999),
    (1_000, '1k'),
    (30_000, '30k'),
    ('30K', '30K'),
    (999_950, '1M'),
    (4_000_000, '4M'),
    ('38,450,000', '38.5M'),
    ('10B', '10B'),
    ('10b', '10b'),
    ('1,500,000,000', '1.5B'),
    (13_000_000_000_000, '13T'),
    ('13T', '13T'),
    ('6 Dec', '6 Dec'),
    ('Yes', 'Yes'),
    (None, None),
    (True, True),
])
def test_format_short_number_handles_plain_and_short_values(raw, expected):
    assert renderer._format_short_number(raw) == expected


def test_fractional_measurements_use_actual_range_at_integer_boundary(scene):
    answer, _, real_open = scene
    answer.return_value = {'one': 100.4, 'two': 100}
    image = real_open(render(
        mode='number', auto_style=False,
        filling_num_override={'(0, 100)': '#ff0000', '(100, 200)': '#0000ff'},
    ))
    assert image.getpixel((80, 600)) == (0, 0, 255, 255)
    assert image.getpixel((290, 600)) == (255, 0, 0, 255)


def test_fractional_manual_ranges_are_used_in_pixels_and_metadata(scene):
    answer, _, real_open = scene
    answer.return_value = {'one': 0.2, 'two': 0.6}
    output = render(
        mode='number', auto_style=False,
        filling_num_override={'(0.1, 0.4)': '#ff0000', '(0.4, 0.8)': '#0000ff'},
    )
    image = real_open(output)
    assert image.getpixel((80, 600)) == (255, 0, 0, 255)
    assert image.getpixel((290, 600)) == (0, 0, 255, 255)
    assert output.map_meta['used_num'] == {
        '(0.1, 0.4)': [255, 0, 0, 255], '(0.4, 0.8)': [0, 0, 255, 255],
    }


def test_fractional_measurements_get_distinct_automatic_colors(scene):
    answer, _, real_open = scene
    answer.return_value = {'one': 0.1, 'two': 0.4}
    image = real_open(render(mode='number', auto_style=True))
    assert image.getpixel((80, 600)) != image.getpixel((290, 600))


@pytest.mark.parametrize('mode,map_type,feature', [
    ('text', 'default', 'show_most_least'),
    ('number', 'world', 'show_most_least'),
])
def test_incompatible_options_are_disabled(scene, monkeypatch, mode, map_type, feature):
    warning = Mock()
    monkeypatch.setattr(renderer, 'showwarning', warning)
    assert render(mode=mode, map_type=map_type, optional_feature=feature)
    warning.assert_called()
    assert scene[0].call_args.args[0] == 'topic'


@pytest.mark.parametrize('answer', [{'one': '2', 'two': '100'}, {'one': 'unknown'}])
def test_numeric_extrema_overlay_and_no_numeric_data(scene, answer):
    scene[0].return_value = answer
    assert render(mode='number', optional_feature='show_most_least')


def test_ai_failure_returns_no_image(scene):
    scene[0].return_value = None
    assert render() is None


@pytest.mark.parametrize('changes', [{'map_type': 'unknown'}, {'mode': 'unknown'}])
def test_unknown_modes_return_no_image(scene, changes):
    assert render(**changes) is None


@pytest.mark.parametrize('show', [False, True])
def test_disk_output_and_optional_viewer(scene, monkeypatch, _scene_assets, show):
    viewer = Mock()
    monkeypatch.setattr(Image.Image, 'show', viewer)
    output = render(io_res=False, show_result=show)
    assert output == _scene_assets / 'result.png'
    assert output.read_bytes().startswith(b'\x89PNG')
    output.unlink(missing_ok=True)
    if show:
        viewer.assert_called_once_with('Map')
    else:
        viewer.assert_not_called()


def test_font_objects_are_cached_within_render(scene, monkeypatch):
    load_font = Mock(wraps=renderer.ImageFont.truetype)
    monkeypatch.setattr(renderer.ImageFont, 'truetype', load_font)
    render()
    assert load_font.call_count == 1


def test_font_loading_is_independent_of_working_directory(scene, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert isinstance(render(), io.BytesIO)


def test_auto_fill_from_ai_detects_numbers_and_words():
    txt_rules, num_rules = renderer._auto_fill_from_ai(
        {'ukraine': 38, 'poland': 37, 'germany': 84, 'estonia': 1},
        mode='number',
    )
    assert not txt_rules
    assert len(num_rules) >= 2

    txt_rules_w, num_rules_w = renderer._auto_fill_from_ai(
        {'ukraine': '6 Dec', 'russia': '19 Dec', 'netherlands': '5 Dec'},
        mode='text',
    )
    assert not num_rules_w
    assert '6 Dec' in txt_rules_w
    assert '19 Dec' in txt_rules_w
    assert txt_rules_w['6 Dec'] != txt_rules_w['19 Dec']

    # Short-form numeric strings in text mode should be grouped into gradient buckets,
    # not assigned a distinct color per unique string.
    short_txt, _ = renderer._auto_fill_from_ai(
        {f'r{i}': f'{i}k' for i in range(1, 21)},
        mode='text',
    )
    assert len(set(short_txt.values())) <= 5
    assert short_txt['1k'] == short_txt['2k']
    assert short_txt['1k'] != short_txt['20k']


def test_auto_map_applies_request_color_and_label_overrides(scene, monkeypatch):
    answer, _, real_open = scene
    answer.return_value = {'one': 'CustomLabel', 'two': 'OtherLabel', 'three': ''}
    orig_text = ImageDraw.ImageDraw.text
    text_calls = []

    def spy_text(self, *args, **kwargs):
        text_calls.append((args, kwargs))
        return orig_text(self, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', spy_text)

    buf = render(
        filling_txt_override={'CustomLabel': [12, 34, 56, 255], 'OtherLabel': [65, 43, 21, 255]},
        label_style={'text_color': '#ff1122', 'stroke_color': '#010203', 'stroke_width': 9, 'auto_fit': True},
        auto_style=False,
        palette_theme='emerald',
    )
    image = real_open(buf)
    assert image.getpixel((80, 600)) == (12, 34, 56, 255)
    assert image.getpixel((290, 600)) == (65, 43, 21, 255)
    assert len(text_calls) == 2
    assert text_calls[0][1]['fill'] == (255, 17, 34, 255)
    assert text_calls[0][1]['stroke_fill'] == (1, 2, 3, 255)
    assert text_calls[0][1]['stroke_width'] == 9
    assert buf.map_meta['label_style']['text_color'] == '#ff1122'
    assert buf.map_meta['used_txt']['CustomLabel'] == [12, 34, 56, 255]


def test_numeric_auto_style_ignores_untouched_four_ui_defaults(scene):
    answer, _, real_open = scene
    answer.return_value = {'one': 1, 'two': 50, 'three': 99}
    untouched_ui_num = {
        '(0, 100)': [56, 189, 248, 255],
        '(100, 1000)': [99, 102, 241, 255],
        '(1000, 10000)': [168, 85, 247, 255],
        '(10000, 100000000)': [244, 63, 94, 255],
    }
    buf = render(
        mode='number',
        filling_num_override=untouched_ui_num,
        auto_style=True,
        palette_theme='auto',
    )
    image = real_open(buf)
    assert len(buf.map_meta['used_num']) == 3
    assert image.getpixel((80, 600)) != image.getpixel((290, 600))
    assert image.getpixel((290, 600)) != image.getpixel((500, 600))


def test_auto_map_restyle_override_skips_ai_request(scene):
    answer, _, real_open = scene
    buf = render(
        ai_answer_override={'one': 'Alpha', 'two': 'Beta', 'three': 'Alpha'},
        filling_txt_override={'Alpha': [10, 200, 100, 255], 'Beta': [200, 10, 100, 255]},
        auto_style=False,
    )
    answer.assert_not_called()
    image = real_open(buf)
    assert image.getpixel((80, 600)) == (10, 200, 100, 255)
    assert image.getpixel((290, 600)) == (200, 10, 100, 255)


@pytest.mark.parametrize('theme', ['auto', 'oceanic'])
def test_auto_style_preserves_explicit_text_rule_case_insensitively(scene, theme):
    scene[0].return_value = {'one': 'Yes', 'two': 'Other', 'three': 'yes'}
    output = render(auto_style=True, palette_theme=theme,
                    filling_txt_override={' YES ': '#123456'})
    image = scene[2](output)
    assert image.getpixel((80, 600)) == (18, 52, 86, 255)
    assert image.getpixel((500, 600)) == (18, 52, 86, 255)
    assert image.getpixel((290, 600)) != (18, 52, 86, 255)


def test_explicit_theme_replaces_untouched_default_text_rules(scene):
    scene[0].return_value = {'one': 'Yes', 'two': 'No', 'three': 'Tea'}
    defaults = {'yes': '#2ecc71', 'no': '#e74c3c', 'tea': '#10b981', 'coffee': '#f59e0b'}
    output = render(auto_style=True, palette_theme='oceanic', filling_txt_override=defaults)
    image = scene[2](output)
    assert image.getpixel((80, 600)) == (56, 189, 248, 255)
    assert image.getpixel((290, 600)) == (14, 165, 233, 255)


def test_numeric_auto_style_keeps_manual_range_and_fills_unmatched_values(scene):
    scene[0].return_value = {'one': 1, 'two': 50, 'three': 99}
    output = render(mode='number', auto_style=True,
                    filling_num_override={'(0, 10)': '#123456'})
    image = scene[2](output)
    assert image.getpixel((80, 600)) == (18, 52, 86, 255)
    assert image.getpixel((290, 600)) != (160, 160, 160, 255)
    assert image.getpixel((500, 600)) != (160, 160, 160, 255)
    assert len(output.map_meta['used_num']) == 3


@pytest.mark.parametrize('stroke,expected', [('bad', 15), (None, 15), (-1, 0), (100, 40)])
def test_label_stroke_validation_keeps_rendering_usable(scene, stroke, expected):
    output = render(label_style={'stroke_width': stroke})
    assert scene[2](output).format == 'PNG'
    assert output.map_meta['label_style']['stroke_width'] == expected


@pytest.mark.parametrize('map_type', ['default', 'world'])
def test_long_labels_auto_fit_without_changing_their_text(scene, monkeypatch, map_type):
    label = 'A very long region label'
    scene[0].return_value = {'one': label}
    text = Mock()
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', text)
    render(map_type=map_type, label_style={'auto_fit': False})
    original_size = text.call_args.kwargs['font'].size
    render(map_type=map_type, label_style={'auto_fit': True})
    assert text.call_args.args[1] == label
    assert text.call_args.kwargs['font'].size < original_size


@pytest.mark.parametrize('theme', [
    'greens', 'blues', 'reds', 'oranges', 'yellows', 'purples', 'teals', 'pinks', 'earth_tones',
])
@pytest.mark.parametrize('mode', ['text', 'number'])
def test_new_palettes_render_selected_colors_for_categories_and_numbers(scene, theme, mode):
    values = {'one': 'Yes', 'two': 'No', 'three': 'Other'} if mode == 'text' else {
        'one': 1, 'two': 50, 'three': 99}
    scene[0].return_value = values
    output = render(mode=mode, auto_style=True, palette_theme=theme)
    image = scene[2](output)
    palette = renderer.constants._PALETTE_THEMES[theme]
    assert output.map_meta['resolved_theme'] == theme
    assert image.getpixel((80, 600)) == palette[0]
    assert image.getpixel((290, 600)) == palette[1]
    assert image.getpixel((500, 600)) == palette[2]


@pytest.mark.parametrize('value', ['1.9', '1,234', '2.5M'])
def test_numeric_render_uses_same_parser_as_palette_analysis(scene, value):
    scene[0].return_value = {'one': value}
    output = render(mode='number', auto_style=True, palette_theme='greens')
    assert scene[2](output).getpixel((80, 600)) == (187, 247, 208, 255)
    assert list(output.map_meta['used_num'].values()) == [[187, 247, 208, 255]]


def test_null_text_values_are_skipped_instead_of_printing_none(scene, monkeypatch):
    scene[0].return_value = {'one': None, 'two': '', 'three': 'Yes'}
    draw_text = Mock()
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', draw_text)
    output = render(auto_style=True)
    assert [call.args[1] for call in draw_text.call_args_list] == ['Yes']
    assert set(output.map_meta['used_txt']) == {'Yes'}
    assert scene[2](output).getpixel((80, 600)) == (255, 255, 255, 255)


def test_renderer_forwards_temperature_to_ai_but_repaint_does_not_call_ai(scene):
    render(temperature=0)
    scene[0].assert_called_once_with('topic', 'default', 'mock-model', 'request-key', temperature=0)
    scene[0].reset_mock()
    render(temperature=2, ai_answer_override={'one': 'Yes'})
    scene[0].assert_not_called()





@pytest.mark.parametrize('map_type', ['default', 'flag', 'world'])
@pytest.mark.parametrize('offset', [-45, 45])
def test_global_label_shift_moves_all_labels_but_not_country_fill_seeds(scene, monkeypatch, map_type, offset):
    draw = Mock()
    fill = Mock()
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', draw)
    monkeypatch.setattr(ImageDraw, 'floodfill', fill)
    render(map_type=map_type, label_style={'offset_x': 0})
    baseline = [call.args[0] for call in draw.call_args_list]
    seeds = [call.kwargs['xy'] for call in fill.call_args_list]
    draw.reset_mock()
    fill.reset_mock()
    output = render(map_type=map_type, label_style={'offset_x': offset})
    assert [call.args[0] for call in draw.call_args_list] == [(x + offset, y) for x, y in baseline]
    assert [call.kwargs['xy'] for call in fill.call_args_list] == seeds
    if map_type != 'flag':
        assert scene[2](output).getpixel((80, 600)) == (255, 0, 0, 255)
    assert output.map_meta['label_style']['offset_x'] == offset


@pytest.mark.parametrize('feature', ['show_most_least,short_form', 'short_form,show_most_least', 'both', ['short_form', 'show_most_least']])
def test_combined_features_compare_unrounded_values(scene, monkeypatch, feature):
    scene[0].return_value = {'one': 1001, 'two': 1049}
    text = Mock()
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', text)
    result = render(mode='number', optional_feature=feature)
    labels = [call.args[1] for call in text.call_args_list]
    assert 'Most: Two\nLeast: One' in labels
    assert result.map_meta['raw_ai_answer'] == {'one': 1001, 'two': 1049}
    assert result.map_meta['ai_answer'] == {'one': '1k', 'two': '1k'}


@pytest.mark.parametrize('mode', ['text', 'number'])
def test_legend_and_progress_are_rendered(scene, monkeypatch, mode):
    scene[0].return_value = {'one': 5, 'two': 15} if mode == 'number' else {'one': 'Yes', 'two': 'No'}
    overlay = Mock(wraps=renderer._draw_legend_overlay)
    monkeypatch.setattr(renderer, '_draw_legend_overlay', overlay)
    progress = Mock()
    result = render(mode=mode, show_legend=True, progress_callback=progress)
    assert result.getvalue().startswith(b'\x89PNG')
    assert overlay.call_args.args[1]
    assert [call.args[0] for call in progress.call_args_list] == [1, 2, 3, 4]


@pytest.mark.parametrize('value,expected', [(0, '0'), (1.25, '1.25'), (10000, '10k'), (12500, '12.5k'), (1000000, '1M'), (1500000000, '1.5B'), (-15000, '-15k')])
def test_legend_formats_bounds(value, expected):
    assert renderer._format_legend_bound(value) == expected


@pytest.mark.parametrize('lo,hi,expected', [(-1e12, 1e12, 'All'), (-1e12, 20, '< 20'), (20, 1e12, '20+'), (10, 20, '10 - 20')])
def test_legend_formats_ranges(lo, hi, expected):
    assert renderer._format_legend_range(lo, hi) == expected


@pytest.mark.parametrize('map_type', ['default', 'world', 'world_countries'])
@pytest.mark.parametrize('has_ml', [False, True])
def test_legend_draws_visible_swatches(map_type, has_ml):
    font_path = renderer.constants.FONTS_DIR / 'Bips.ttf'
    font = lambda size: renderer.ImageFont.truetype(str(font_path), size)
    layer = renderer._draw_legend_overlay((2000, 3000), [((255, 0, 0, 255), 'Yes'), ((0, 0, 255, 255), 'No')], font, (255,255,255,255), (0,0,0,255), 5, map_type, has_ml)
    assert layer.getbbox() is not None
    assert layer.mode == 'RGBA'


def test_legend_empty_long_and_overflow_entries():
    font = lambda size: renderer.ImageFont.truetype(str(renderer.constants.FONTS_DIR / 'Bips.ttf'), size)
    args = (font, (255,255,255,255), (0,0,0,255), 0, 'world', False)
    assert renderer._draw_legend_overlay((100,100), [], *args) is None
    layer = renderer._draw_legend_overlay((100,100), [((255,0,0,255), 'Long category ' * 20)] * 100, *args)
    assert layer.size == (100,100)
