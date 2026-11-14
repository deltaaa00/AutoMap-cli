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


def test_short_form_changes_ai_prompt(scene):
    answer, _, _ = scene
    render(optional_feature='short_form')
    assert '1000000=1M' in answer.call_args.args[0]


@pytest.mark.parametrize('mode,map_type,feature', [
    ('number', 'default', 'short_form'), ('text', 'default', 'show_most_least'),
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



