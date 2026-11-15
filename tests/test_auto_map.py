import importlib
import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

renderer = importlib.import_module('map.auto_map')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scene(monkeypatch, tmp_path):
    """Small real Pillow images keep compositing tests quick and deterministic."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(renderer, '__file__', str(tmp_path / 'map' / 'auto_map.py'))
    base = Image.new('RGBA', (640, 640), 'white')
    draw = ImageDraw.Draw(base)
    draw.line((210, 0, 210, 639), fill='black')
    draw.line((420, 0, 420, 639), fill='black')
    real_open = Image.open

    def open_image(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and str(path).endswith('.png'):
            if Path(path).name in ('default_map.png', 'flag_map.png', 'world_map.png'):
                return base.copy()
            return Image.new('RGBA', base.size)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Image, 'open', open_image)
    coords = {'one': [(80, 80)], 'two': [(290, 80)], 'three': [(500, 80)]}
    monkeypatch.setattr(renderer, 'europe_coords', coords)
    monkeypatch.setattr(renderer, 'world_coords', coords)
    answer = Mock(return_value={'one': 'Yes', 'two': 'no', 'three': 'yes'})
    monkeypatch.setattr(renderer, 'ai_request', answer)
    monkeypatch.setattr(renderer, 'load_filling', lambda: (
        {'YES': (255, 0, 0, 255), ' no ': (0, 0, 255, 255)},
        {(0, 10): (0, 255, 0, 255), (10, 20): (255, 255, 0, 255)},
    ))
    random = Mock(return_value=160)
    monkeypatch.setattr(renderer.random, 'randint', random)
    return answer, random, real_open


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
def test_disk_output_and_optional_viewer(scene, monkeypatch, tmp_path, show):
    viewer = Mock()
    monkeypatch.setattr(Image.Image, 'show', viewer)
    output = render(io_res=False, show_result=show)
    assert output == tmp_path / 'result.png'
    assert output.read_bytes().startswith(b'\x89PNG')
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
    palette = renderer._PALETTE_THEMES[theme]
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
    assert output.map_meta['label_style']['offset_x'] == offset
