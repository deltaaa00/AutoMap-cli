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

"""Verify numeric parsing, palettes and editor rules without live AI.

Covers malformed/nonfinite colors, fractional bounds, explicit theme priority
and effective-render metadata. These tests focus on reusable styling contracts;
actual country fill pixels are checked by test_auto_map.
"""
from webapp import analysis as map_analysis
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


@pytest.mark.parametrize('nonfinite', [float('inf'), float('-inf'), float('nan')])
def test_nonfinite_color_channels_and_bounds_are_ignored(nonfinite):
    assert renderer._to_rgba_tuple([nonfinite, 0, 0]) is None
    assert renderer._parse_filling_overrides(None, [
        {'low': nonfinite, 'high': 10, 'color': '#112233'},
    ]) == ({}, {})
    assert renderer._parse_filling_overrides(None, {
        (0, nonfinite): '#112233',
    }) == ({}, {})


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


@pytest.mark.parametrize('values', [
    [0.1, 0.4],
    [i / 10 for i in range(-3, 12)],
    [0.1] * 20 + [0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
])
def test_fractional_numeric_palette_covers_values_without_rounding(values):
    _, rules = renderer._auto_fill_from_ai(dict(enumerate(values)), 'number')
    colors = []
    for value in values:
        matches = [color for (lo, hi), color in rules.items() if lo < value <= hi]
        assert len(matches) == 1
        colors.append(matches[0])
    assert len(set(colors)) >= 2
    assert max(hi for lo, hi in rules) == max(values)


@pytest.mark.parametrize('raw', [
    {'(0.1, 0.4)': '#112233'},
    [{'low': '0.1', 'high': '0.4', 'color': '#112233'}],
])
def test_numeric_overrides_preserve_fractional_bounds(raw):
    assert renderer._parse_filling_overrides(None, raw)[1] == {
        (0.1, 0.4): (17, 34, 51, 255),
    }


def test_analysis_preserves_actual_render_rules_and_skips_malformed_metadata(web):
    analysis = map_analysis._build_analysis_from_ai({'a': '2.5M', 'b': 10}, mode='number', render_meta={
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
