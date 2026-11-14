"""Color configuration and AI-value analysis without external services."""
import importlib

import pytest


renderer = importlib.import_module('map.auto_map')


@pytest.mark.parametrize('value,expected', [
    (' 2.5M ', 2_500_000), ('1B', 1_000_000_000), ('-1.5k', -1500),
    ('1,234', 1234), ('1 234', 1234), (0, 0),
    ('', None), ('NaN', None), ('inf', None), ('unknown', None),
    (True, None), (None, None),
])
def test_short_number_parsing_accepts_measurements_not_missing_or_nonfinite_values(value, expected):
    assert renderer._parse_short_number(value) == expected


@pytest.mark.parametrize('topic,mode,expected', [
    ('Christmas holidays', 'text', 'festive'),
    ('Winter temperatures', 'number', 'ocean'),
    ('Summer temperatures', 'number', 'sunset'),
    ('Population', 'number', 'viridis'),
    ('Favorite drink', 'text', 'auto'),
])
def test_automatic_theme_follows_topic_and_data_type(topic, mode, expected):
    assert renderer._resolve_theme_name('auto', topic, mode) == expected


def test_explicit_theme_takes_precedence_over_topic():
    assert renderer._resolve_theme_name(' OCEANIC ', 'Christmas', 'number') == 'oceanic'


def test_color_overrides_accept_hex_rgb_and_rgba_and_ignore_invalid_rules():
    txt, num = renderer._parse_filling_overrides({
        ' Yes ': '#12Ab34', 'No': [300, -5, 25, 500], 'RGB': [1, 2, 3],
        '': [1, 2, 3], 'bad': ['not-a-channel', 2, 3], 'missing': None,
    }, {
        '(0, 10)': '#112233', (10, 20): [4, 5, 6, 128],
        'invalid': [1, 2, 3], '(20,)': [1, 2, 3], '(20, 30)': None,
    })
    assert txt == {'Yes': (18, 171, 52, 255), 'No': (255, 0, 25, 255),
                   'RGB': (1, 2, 3, 255)}
    assert num == {(0, 10): (17, 34, 51, 255), (10, 20): (4, 5, 6, 128)}


def test_numeric_rule_list_accepts_supported_colors_and_skips_incomplete_entries():
    _, rules = renderer._parse_filling_overrides(None, [
        {'low': '0', 'high': '10', 'hex': '#112233'},
        {'low': 10, 'high': 20, 'color': [4, 5, 6, 128]},
        None, {'low': 20, 'high': 30, 'color': None},
        {'low': 20, 'hex': '#112233'},
        {'low': 'bad', 'high': 30, 'hex': '#112233'},
    ])
    assert rules == {(0, 10): (17, 34, 51, 255), (10, 20): (4, 5, 6, 128)}


@pytest.mark.parametrize('value', [None, 'not-a-color', '#xyz'])
def test_invalid_label_color_uses_safe_default(value):
    fallback = (255, 255, 255, 255)
    assert renderer._hex_to_rgba(value, fallback) == fallback


def test_numeric_palette_covers_every_value_when_more_than_five_values_exist():
    values = {'region' + str(i): i for i in range(-3, 12)}
    txt, rules = renderer._auto_fill_from_ai(values, 'number', palette_theme='heatmap')
    assert not txt
    assert len(rules) == 5
    for value in values.values():
        matches = [color for (lo, hi), color in rules.items() if lo < value <= hi]
        assert len(matches) == 1
    assert len(set(rules.values())) == 5


def test_short_form_text_palette_keeps_unknown_labels_as_separate_categories():
    rules, _ = renderer._auto_fill_from_ai(
        {'a': '1k', 'b': '2k', 'c': '3k', 'd': 'unknown'}, 'text')
    assert set(rules) == {'1k', '2k', '3k', 'unknown'}
    assert rules['1k'] != rules['3k']
    assert rules['unknown'] not in (rules['1k'], rules['3k'])


def test_analysis_preserves_actual_render_rules_and_skips_malformed_metadata(web):
    analysis = web._build_analysis_from_ai({'a': '2.5M', 'b': 10}, mode='number', render_meta={
        'resolved_theme': 'heatmap',
        'used_txt': {'Yes': '#112233', 'invalid': None},
        'used_num': {'(0, 10)': [1, 2, 3, 128], (10, 3_000_000): '#445566',
                     'broken': [1, 2, 3], '(1,)': [1, 2, 3], '(4, 5)': None},
        'label_style': {'text_color': '#abcdef', 'stroke_width': 9},
    })
    assert analysis['resolved_theme'] == 'heatmap'
    assert analysis['filling_txt'] == {'Yes': [17, 34, 51, 255]}
    assert analysis['filling_num'] == {
        '(0, 10)': [1, 2, 3, 128], '(10, 3000000)': [68, 85, 102, 255]}
    assert analysis['label_style']['text_color'] == '#abcdef'
    assert analysis['label_style']['stroke_width'] == 9
    assert analysis['stats']['min_value'] == 10
    assert analysis['stats']['max_value'] == 2_500_000
