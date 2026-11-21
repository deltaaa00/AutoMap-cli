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

"""Ensure untrusted model/UI strings stay literal text in the map editor.

Uses shared offline map_page and injects hostile labels, model names and color
values. Assertions check DOM nodes and event-handler execution, not just string
escaping. No external AI service or CDN is contacted.
"""
from webapp import analysis as map_analysis
import pytest

from test_map_palettes import map_page


pytestmark = pytest.mark.browser


def test_ai_labels_and_samples_cannot_create_html(map_page, web):
    payload = '\"><img src=x onerror="window.injected=true">'
    analysis = map_analysis._build_analysis_from_ai({'ukraine': payload}, mode='text')
    map_page.evaluate('''({analysis, payload}) => {
        window.injected = false;
        applyAnalysisToFillingStudio(analysis, {ukraine: payload}, false);
    }''', {'analysis': analysis, 'payload': payload})
    assert map_page.locator('#detectedMapStatsBar img').count() == 0
    assert map_page.locator('#fillingTxtList img').count() == 0
    assert payload in map_page.locator('#detectedMapStatsBar').inner_text()
    assert map_page.locator('#fillingTxtList input[type=text]').first.input_value() == payload
    assert map_page.evaluate('window.injected') is False


def test_custom_model_is_literal_badge_text(map_page):
    payload = '<img src=x onerror="window.injected=true">'
    map_page.evaluate('''payload => {
        window.injected = false;
        document.getElementById('newModelInput').value = payload;
        addCustomModel();
    }''', payload)
    assert map_page.locator('#activeFlagsBadges img').count() == 0
    assert payload in map_page.locator('#activeFlagsBadges').inner_text()
    assert map_page.evaluate('window.injected') is False


def test_malformed_analysis_color_is_replaced_with_safe_color(map_page):
    map_page.evaluate('''() => {
        window.injected = false;
        applyAnalysisToFillingStudio({
            ui_txt_rules: [{key: 'hello', hex: '\"><img src=x onerror="window.injected=true">'}],
            ui_num_rules: [], stats: {},
        }, {ukraine: 'hello'}, false);
    }''')
    assert map_page.locator('#fillingTxtList img').count() == 0
    assert map_page.evaluate('fillingTxtRules[0].hex') == '#6366f1'
    assert map_page.evaluate('window.injected') is False
