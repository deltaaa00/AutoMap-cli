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

"""Exercise palette selection and request ordering in the actual map UI."""
import atexit
import base64
import io
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright


pytestmark = pytest.mark.browser
VALUES = {'ukraine': 'Alpha', 'poland': 'Beta'}


_PLAYWRIGHT = None
_BROWSER = None


def _close_browser():
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is not None:
        _BROWSER.close()
        _BROWSER = None
    if _PLAYWRIGHT is not None:
        _PLAYWRIGHT.stop()
        _PLAYWRIGHT = None


def get_browser():
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is None:
        _PLAYWRIGHT = sync_playwright().start()
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        kwargs = {'executable_path': str(chrome)} if chrome.exists() else {}
        _BROWSER = _PLAYWRIGHT.chromium.launch(headless=True, **kwargs)
        atexit.register(_close_browser)
    return _BROWSER


@pytest.fixture
def map_page(web):
    context = get_browser().new_context()
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        request = route.request
        url = urlsplit(request.url)
        if url.hostname == 'automap.test':
            response = web.app.test_client().open(
                url.path + ('?' + url.query if url.query else ''), method=request.method, data=request.post_data,
                content_type=request.headers.get('content-type'))
            route.fulfill(status=response.status_code, body=response.data,
                          content_type=response.content_type)
        elif url.hostname == 'cdn.tailwindcss.com':
            route.fulfill(content_type='application/javascript', body='window.tailwind = {};')
        else:
            route.fulfill(content_type='text/css', body='')

    page.route('**/*', route_request)
    try:
        page.goto('http://automap.test/')
        page.add_style_tag(content='.hidden {display:none!important}')
        page.evaluate('''({analysis, values}) => {
            applyAnalysisToFillingStudio(analysis, values, false);
        }''', {'analysis': web._build_analysis_from_ai(
            VALUES, mode='text', palette_theme='heatmap'), 'values': VALUES})
        page.click('#openFillingStudioBtn')
        yield page
        assert not errors, errors
    finally:
        context.close()


def test_auto_preview_is_not_reused_as_manual_generation_rules(map_page):
    page = map_page
    state = page.evaluate('getFormState()')
    assert state['auto_style'] is True
    assert 'filling_txt' not in state and 'filling_num' not in state

    page.evaluate("updateTxtRule(0, 'hex', '#123456')")
    state = page.evaluate('getFormState()')
    assert state['auto_style'] is False
    assert state['filling_txt']['Alpha'] == [18, 52, 86, 255]

    page.select_option('#fillingPaletteTheme', 'oceanic')
    page.wait_for_function("lastMapAnalysis.resolved_theme === 'oceanic' && !paletteAnalysisPending")
    state = page.evaluate('getFormState()')
    assert state['auto_style'] is True
    assert 'filling_txt' not in state
    assert set(page.evaluate('fillingTxtRules.map(rule => rule.hex)')) == {'#38bdf8', '#0ea5e9'}

    # Apply uses the freshly calculated rules even though generation uses Auto.
    payloads = []

    def repaint(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(status=500, json={'status': 'error', 'error': 'Stop after inspecting payload'})

    page.route('**/api/restyle_map', repaint)
    page.evaluate('closeFillingStudio()')
    page.click('#restyleCurrentMapBtn')
    page.wait_for_function("!document.getElementById('restyleCurrentMapBtn').disabled")
    assert len(payloads) == 1
    assert payloads[0]['auto_style'] is False
    assert set(tuple(c) for c in payloads[0]['filling_txt'].values()) == {
        (56, 189, 248, 255), (14, 165, 233, 255)}


def test_theme_editor_has_one_entry_in_left_panel(map_page):
    page = map_page
    buttons = page.locator('button[onclick="openFillingStudio()"]')
    assert buttons.count() == 1
    assert buttons.get_attribute('id') == 'openFillingStudioBtn'
    left_panel = page.locator('main > section').first
    assert left_panel.locator('#cliForm #openFillingStudioBtn').count() == 1
    assert page.locator('#fillingPaletteTheme').count() == 1
    assert page.locator('#fillingStudioModal #fillingPaletteTheme').count() == 1
    assert page.locator('#mapPaletteThemeSelect').count() == 0
    page.locator('#fillingStudioModal button[onclick="closeFillingStudio()"]').click()
    assert page.locator('#fillingStudioModal').is_hidden()
    buttons.click()
    assert page.locator('#fillingStudioModal').is_visible()


def test_latest_theme_wins_when_responses_arrive_out_of_order(map_page, web):
    page = map_page
    page.evaluate('''() => {
        window.pendingPalettes = [];
        const originalFetch = window.fetch;
        window.fetch = (url, options) => url === '/api/analyze_map'
            ? new Promise(resolve => pendingPalettes.push(resolve))
            : originalFetch(url, options);
    }''')
    page.select_option('#fillingPaletteTheme', 'oceanic')
    assert page.locator('#restyleCurrentMapBtn').is_disabled()
    page.evaluate('restyleCurrentMapFromStudio()')
    assert 'Wait for palette analysis' in page.locator('#toastMsg').inner_text()
    page.select_option('#fillingPaletteTheme', 'festive')
    assert page.evaluate('pendingPalettes.length') == 2

    def respond(index, theme):
        page.evaluate('''({index, payload}) => {
            pendingPalettes[index](new Response(JSON.stringify(payload), {
                status: 200, headers: {'Content-Type': 'application/json'}
            }));
        }''', {'index': index, 'payload': {'status': 'success', 'map_values': VALUES,
            'analysis': web._build_analysis_from_ai(VALUES, mode='text', palette_theme=theme)}})

    respond(1, 'festive')
    page.wait_for_function("lastMapAnalysis.resolved_theme === 'festive' && !paletteAnalysisPending")
    respond(0, 'oceanic')
    # Resolve microtasks after the stale JSON response has been handled.
    page.evaluate('() => new Promise(resolve => setTimeout(resolve, 0))')
    assert page.evaluate('lastMapAnalysis.resolved_theme') == 'festive'
    assert set(page.evaluate('fillingTxtRules.map(rule => rule.hex)')) == {'#dc2626', '#16a34a'}
    assert page.locator('#restyleCurrentMapBtn').is_enabled()


def test_failed_theme_analysis_cannot_apply_old_palette(map_page):
    page = map_page
    page.route('**/api/analyze_map', lambda route: route.fulfill(
        status=500, json={'status': 'error', 'error': 'Analysis unavailable'}))
    page.select_option('#fillingPaletteTheme', 'oceanic')
    page.wait_for_function('!paletteAnalysisPending && !paletteAnalysisReady')
    assert page.locator('#restyleCurrentMapBtn').is_disabled()
    page.evaluate('restyleCurrentMapFromStudio()')
    assert 'retry Detect' in page.locator('#toastMsg').inner_text()

    # Explicit manual editing is still available after an analysis failure.
    page.evaluate("updateTxtRule(0, 'hex', '#123456')")
    assert page.locator('#restyleCurrentMapBtn').is_enabled()
    assert page.evaluate('getFormState().auto_style') is False


@pytest.mark.parametrize('status', [200, 500])
def test_repaint_shows_busy_state_until_image_or_error_is_ready(map_page, status):
    page = map_page
    pending = []
    page.route('**/api/restyle_map', lambda route: pending.append(route))
    page.evaluate('closeFillingStudio()')
    page.click('#restyleCurrentMapBtn')
    page.wait_for_function('mapRepaintPending')
    assert page.locator('#mapRepaintStatus').is_visible()
    assert page.locator('#mapViewport #mapRepaintOverlay').is_visible()
    assert 'Re-painting map' in page.locator('#mapRepaintOverlay').inner_text()
    assert page.locator('#mapViewport').get_attribute('aria-busy') == 'true'
    assert page.locator('#mapRepaintButtonLabel').inner_text() == 'Re-painting…'
    assert page.locator('#restyleCurrentMapBtn').get_attribute('aria-busy') == 'true'
    assert page.locator('#restyleCurrentMapBtn').is_disabled()
    assert page.locator('#fillingPaletteTheme').is_disabled()
    page.evaluate('restyleCurrentMapFromStudio()')
    assert len(pending) == 1

    if status == 200:
        png = io.BytesIO()
        Image.new('RGB', (2, 2), 'green').save(png, format='PNG')
        payload = {'status': 'success', 'image_url': 'data:image/png;base64,' +
                   base64.b64encode(png.getvalue()).decode('ascii')}
    else:
        payload = {'status': 'error', 'error': 'Could not render map'}
    pending[0].fulfill(status=status, json=payload)
    page.wait_for_function('!mapRepaintPending')
    assert page.locator('#mapRepaintStatus').is_hidden()
    assert page.locator('#mapRepaintOverlay').is_hidden()
    assert page.locator('#mapViewport').get_attribute('aria-busy') == 'false'
    assert page.locator('#restyleCurrentMapBtn').get_attribute('aria-busy') == 'false'
    assert page.locator('#fillingPaletteTheme').is_enabled()
    assert page.locator('#mapRepaintButtonLabel').inner_text() == 'Apply & Re-Paint Current Map'
    if status == 200:
        assert page.locator('#serverMapImage').is_visible()
        page.wait_for_function("async () => !!(await StudioStorage.getAsset('latest-map'))?.file")
        assert page.evaluate("async () => (await StudioStorage.getAsset('latest-map')).file.size") == len(png.getvalue())
    else:
        assert page.locator('#restyleCurrentMapBtn').is_enabled()
        assert 'Could not render map' in page.locator('#toastMsg').inner_text()


def test_new_palette_is_selectable_and_analyzed(map_page):
    page = map_page
    labels = page.locator('#fillingPaletteTheme option').all_inner_texts()
    for label in ('Greens', 'Blues', 'Reds', 'Oranges', 'Yellows', 'Purples', 'Teals', 'Pinks', 'Earth Tones'):
        assert sum(label in text for text in labels) == 1
    page.select_option('#fillingPaletteTheme', 'greens')
    page.wait_for_function("lastMapAnalysis.resolved_theme === 'greens' && !paletteAnalysisPending")
    assert set(page.evaluate('fillingTxtRules.map(rule => rule.hex)')) == {'#bbf7d0', '#86efac'}


def test_removed_saved_palette_falls_back_to_auto(map_page):
    page = map_page
    page.evaluate("localStorage.setItem(MAP_PALETTE_STORAGE_KEY, 'removed-theme')")
    page.reload()
    assert page.locator('#fillingPaletteTheme').input_value() == 'auto'


def test_palette_selection_works_when_storage_is_blocked(map_page):
    page = map_page
    page.add_init_script('''
        Storage.prototype.getItem = Storage.prototype.setItem = () => {
            throw new DOMException('Storage blocked', 'SecurityError');
        };
    ''')
    page.reload()
    page.evaluate('openFillingStudio()')
    page.select_option('#fillingPaletteTheme', 'blues')
    assert page.evaluate('getFormState().palette_theme') == 'blues'


def test_theme_menu_selection_is_sent_on_first_generation(map_page):
    page = map_page
    page.reload()
    page.add_style_tag(content='.hidden {display:none!important}')
    assert page.locator('#fillingStudioModal').is_hidden()
    page.click('#openFillingStudioBtn')
    assert page.locator('#fillingStudioModal').is_visible()
    page.select_option('#fillingPaletteTheme', 'greens')
    assert page.locator('#fillingPaletteTheme').input_value() == 'greens'
    page.locator('#fillingStudioModal button[onclick="closeFillingStudio()"]').click()
    assert page.locator('#fillingStudioModal').is_hidden()
    page.locator('#aiTemperature').press('Home')
    assert page.locator('#aiTemperatureValue').inner_text() == '0.00'
    page.fill('#topicInput', 'Population of Europe')
    page.evaluate("document.getElementById('apiKeyInput').value = 'test-key-never-send'")
    payloads = []
    png = io.BytesIO()
    Image.new('RGB', (2, 2), 'green').save(png, format='PNG')
    image_url = 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode('ascii')

    def generate(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(json={'status': 'success', 'image_url': image_url})

    page.route('**/api/generate', generate)
    page.click('#generateBtn')
    page.wait_for_function("!document.getElementById('generateBtn').disabled")
    assert len(payloads) == 1
    assert payloads[0]['palette_theme'] == 'greens'
    assert payloads[0]['temperature'] == 0
    assert payloads[0]['auto_style'] is True
    assert 'filling_txt' not in payloads[0]
    page.reload()
    assert page.locator('#fillingPaletteTheme').input_value() == 'greens'
    state = page.evaluate('getFormState()')
    assert state['palette_theme'] == 'greens'
    assert state['auto_style'] is True
    assert 'filling_txt' not in state
    assert page.locator('#aiTemperature').input_value() == '0'
    assert page.locator('#aiTemperatureValue').inner_text() == '0.00'
    assert '--temperature 0' in page.locator('#cliCommandDisplay').inner_text()


def test_temperature_slider_updates_output_and_restores_from_history(map_page):
    page = map_page
    page.evaluate('closeFillingStudio()')
    page.locator('#aiTemperature').press('End')
    assert page.locator('#aiTemperatureValue').inner_text() == '2.00'
    assert '--temperature 2' in page.locator('#cliCommandDisplay').inner_text()
    page.evaluate('addToHistory(getFormState(), "test")')
    page.locator('#aiTemperature').press('Home')
    page.evaluate('restoreHistoryItem(0)')
    assert page.locator('#aiTemperature').input_value() == '2'
    page.reload()
    assert page.locator('#aiTemperature').input_value() == '2'


def test_history_topic_is_literal_text_and_click_restores(map_page):
    page = map_page
    payload = '<img src=x onerror="window.historyXss=true">.png'
    page.evaluate('''filename => {
        window.historyXss = false;
        closeFillingStudio();
        addToHistory({...getFormState(), topic: filename}, `[batch] ${filename}`);
        addToHistory(getFormState(), 'Another run');
        document.getElementById('topicInput').value = 'Changed topic';
        switchBottomTab('history');
    }''', payload)
    history = page.locator('#historyList')
    assert history.locator('img').count() == 0
    assert history.locator('[onerror]').count() == 0
    assert history.inner_text().count(payload) == 1
    page.get_by_text(payload, exact=True).click()
    assert page.locator('#topicInput').input_value() == payload
    assert page.evaluate('window.historyXss') is False


def test_temperature_defaults_to_point_seven_five_and_can_be_reset(map_page):
    page = map_page
    page.evaluate('closeFillingStudio()')
    assert page.locator('#aiTemperature').input_value() == '0.75'
    assert page.locator('#aiTemperatureValue').inner_text() == '0.75'
    assert page.evaluate('getFormState().temperature') == 0.75
    assert '--temperature 0.75' in page.locator('#cliCommandDisplay').inner_text()
    page.locator('#aiTemperature').press('ArrowRight')
    assert page.locator('#aiTemperatureValue').inner_text() == '0.80'
    page.get_by_role('button', name='Reset to 0.75', exact=True).click()
    assert page.locator('#aiTemperature').input_value() == '0.75'
    page.reload()
    assert page.locator('#aiTemperatureValue').inner_text() == '0.75'


def test_api_key_and_topic_survive_reload_and_can_be_cleared(map_page):
    page = map_page
    page.evaluate('closeFillingStudio()')
    page.locator('#apiKeyInput').fill('AIza-test-browser-key')
    page.locator('#topicInput').fill('Population of European countries')
    page.click('#openFillingStudioBtn')
    page.select_option('#fillingPaletteTheme', 'oceanic')
    page.wait_for_function('!paletteAnalysisPending')
    page.locator('#fillingStudioModal button[onclick="closeFillingStudio()"]').click()
    page.reload()
    assert page.locator('#apiKeyInput').input_value() == 'AIza-test-browser-key'
    assert page.locator('#apiKeyInput').get_attribute('type') == 'password'
    assert page.locator('#topicInput').input_value() == 'Population of European countries'
    assert page.locator('#fillingPaletteTheme').input_value() == 'oceanic'
    page.locator('#apiKeyInput').fill('')
    page.evaluate('clearTopicInput()')
    page.reload()
    assert page.locator('#apiKeyInput').input_value() == ''
    assert page.locator('#topicInput').input_value() == ''


def test_map_inputs_work_when_browser_storage_is_blocked(map_page):
    page = map_page
    page.add_init_script('''Object.defineProperty(window, 'localStorage', {
        get() { throw new DOMException('Storage blocked', 'SecurityError'); }
    });''')
    page.reload()
    page.locator('#apiKeyInput').fill('test-key')
    page.locator('#topicInput').fill('Test topic')
    assert page.evaluate('getFormState().api_key') == 'test-key'
    # The preview updates after typing settles; storage may still be blocked.
    expect(page.locator('#cliCommandDisplay')).to_contain_text('Test topic')


def test_batch_selection_restores_parameters_before_repainting(map_page, web):
    page = map_page
    png = io.BytesIO()
    Image.new('RGB', (2, 2), 'green').save(png, format='PNG')
    world_values = {'china': 10, 'brazil': 20}
    item = {
        'index': 1, 'prompt': 'Population around the world', 'mode': 'number',
        'map_type': 'world', 'gsm': 8, 'extra_option': 'short_form',
        'model': 'custom-batch-model', 'font': 'DIN Condensed.ttf',
        'temperature': 0.25, 'palette_theme': 'oceanic',
        'image_url': 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode(),
        'map_values': world_values,
        'analysis': web._build_analysis_from_ai(world_values, mode='number', palette_theme='oceanic'),
    }
    analysis_requests = []
    page.on('request', lambda request: analysis_requests.append(request.url)
            if '/api/analyze_map' in request.url else None)
    page.evaluate('''item => {
        renderBatchResultsPanel([item]);
        selectBatchResult(0);
    }''', item)
    state = page.evaluate('getFormState()')
    for field in ('mode', 'map_type', 'gsm', 'extra_option', 'model', 'font',
                  'temperature', 'palette_theme'):
        assert state[field] == item[field]
    assert state['topic'] == item['prompt']
    assert not analysis_requests
    assert '-mp world' in page.locator('#cliCommandDisplay').inner_text()

    payloads = []

    def repaint(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(status=500, json={'status': 'error', 'error': 'Test repaint captured'})

    page.route('**/api/restyle_map', repaint)
    page.evaluate('restyleCurrentMapFromStudio()')
    assert len(payloads) == 1
    for field in ('mode', 'map_type', 'gsm', 'extra_option', 'model', 'font',
                  'temperature', 'palette_theme'):
        assert payloads[0][field] == item[field]
    assert payloads[0]['topic'] == item['prompt']
    assert payloads[0]['map_values'] == world_values


def test_generation_failure_shows_actual_error_and_keeps_retry_available(map_page):
    page = map_page
    page.locator('#fillingStudioModal button[onclick="closeFillingStudio()"]').click()
    page.fill('#topicInput', 'Population of Europe')
    page.evaluate("document.getElementById('apiKeyInput').value = 'test-key-never-send'")
    error = 'The AI model is overloaded. Please try again later.'
    page.route('**/api/generate', lambda route: route.fulfill(
        status=503, json={'status': 'error', 'error': error}))
    page.click('#generateBtn')
    page.wait_for_function("!document.getElementById('generateBtn').disabled")
    assert page.locator('#generationError').is_visible()
    assert error in page.locator('#generationError').inner_text()
    assert error in page.locator('#toastMsg').inner_text()
    assert page.locator('#generateBtn').is_enabled()


def stub_provider_model_lists(page):
    def respond(route):
        provider = route.request.post_data_json.get('provider', 'gemini')
        models = ['deepseek-flash', 'deepseek-v4-pro'] if provider == 'deepseek' else ['gemini-3.8-flash']
        route.fulfill(json={'status': 'success', 'models': models})
    page.route('**/api/models', respond)


def test_provider_groups_and_keys_survive_switches_reload_and_independent_clear(map_page):
    page = map_page
    stub_provider_model_lists(page)
    page.evaluate('closeFillingStudio()')
    assert page.locator('#modelSelect optgroup').count() == 2
    assert page.locator('#modelSelect optgroup').evaluate_all('(groups) => groups.map(group => group.label)') == ['Gemini', 'DeepSeek']
    page.locator('#apiKeyInput').fill('gemini-browser-key')
    page.select_option('#modelSelect', 'deepseek-flash')
    page.wait_for_function("activeAiProvider === 'deepseek' && document.getElementById('apiKeyLabel').textContent.includes('DeepSeek')")
    assert page.locator('#apiKeyInput').input_value() == ''
    page.locator('#apiKeyInput').fill('deepseek-browser-key')
    page.reload()
    page.wait_for_function("activeAiProvider === 'deepseek' && getFormState().model === 'deepseek-flash'")
    assert page.locator('#apiKeyInput').input_value() == 'deepseek-browser-key'
    assert page.locator('#apiKeyInput').get_attribute('type') == 'password'
    assert page.evaluate('getFormState().provider') == 'deepseek'
    page.select_option('#modelSelect', 'gemini-3.8-flash')
    assert page.locator('#apiKeyInput').input_value() == 'gemini-browser-key'
    page.select_option('#modelSelect', 'deepseek-flash')
    page.locator('#apiKeyInput').fill('')
    page.reload()
    assert page.locator('#apiKeyInput').input_value() == ''
    page.select_option('#modelSelect', 'gemini-3.8-flash')
    assert page.locator('#apiKeyInput').input_value() == 'gemini-browser-key'
    assert page.locator('#modelSelect optgroup').count() == 2


def test_deepseek_generation_sends_only_active_provider_key(map_page):
    page = map_page
    stub_provider_model_lists(page)
    page.evaluate('closeFillingStudio()')
    page.locator('#apiKeyInput').fill('gemini-do-not-send')
    page.select_option('#modelSelect', 'deepseek-flash')
    page.locator('#apiKeyInput').fill('deepseek-use-this')
    page.locator('#topicInput').fill('Population of Europe')
    payloads = []
    def capture(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(status=503, json={'status': 'error', 'error': 'DeepSeek is overloaded. Please try again.'})
    page.route('**/api/generate', capture)
    page.click('#generateBtn')
    page.wait_for_function("!document.getElementById('generateBtn').disabled")
    assert len(payloads) == 1
    assert payloads[0]['model'] == 'deepseek-flash'
    assert payloads[0]['provider'] == 'deepseek'
    assert payloads[0]['api_key'] == 'deepseek-use-this'
    assert 'gemini-do-not-send' not in str(payloads)
    assert 'DeepSeek is overloaded' in page.locator('#generationError').inner_text()


def test_server_deepseek_key_hides_input_without_exposing_key(map_page, monkeypatch):
    page = map_page
    stub_provider_model_lists(page)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'server-deepseek-never-expose')
    page.select_option('#modelSelect', 'deepseek-flash')
    page.wait_for_function("activeAiProvider === 'deepseek' && !apiKeyRequired")
    assert page.locator('#apiKeySection').is_hidden()
    assert page.locator('#apiKeyInput').input_value() == ''
    assert 'server-deepseek-never-expose' not in page.content()
    page.select_option('#modelSelect', 'gemini-3.8-flash')
    page.wait_for_function('apiKeyRequired')
    assert page.locator('#apiKeySection').is_visible()


def test_provider_keys_stay_independent_when_storage_is_blocked(map_page):
    page = map_page
    stub_provider_model_lists(page)
    page.add_init_script('''Object.defineProperty(window, 'localStorage', {
        get() { throw new DOMException('Storage blocked', 'SecurityError'); }
    });''')
    page.reload()
    page.locator('#apiKeyInput').fill('gemini-memory')
    page.select_option('#modelSelect', 'deepseek-flash')
    page.locator('#apiKeyInput').fill('deepseek-memory')
    page.select_option('#modelSelect', 'gemini-3.8-flash')
    assert page.locator('#apiKeyInput').input_value() == 'gemini-memory'
    page.select_option('#modelSelect', 'deepseek-flash')
    assert page.locator('#apiKeyInput').input_value() == 'deepseek-memory'


def test_history_uses_model_provider_and_its_saved_key(map_page):
    page = map_page
    stub_provider_model_lists(page)
    page.evaluate('closeFillingStudio()')
    page.locator('#apiKeyInput').fill('gemini-history-key')
    page.select_option('#modelSelect', 'deepseek-flash')
    page.locator('#apiKeyInput').fill('deepseek-history-key')
    page.select_option('#modelSelect', 'gemini-3.8-flash')
    page.evaluate('''() => {
        addToHistory({...getFormState(), provider:'gemini', model:'deepseek-flash'}, 'Legacy history');
        restoreHistoryItem(0);
    }''')
    assert page.evaluate('getFormState().provider') == 'deepseek'
    assert page.locator('#apiKeyInput').input_value() == 'deepseek-history-key'
    assert page.locator('#modelSelect').input_value() == 'deepseek-flash'


def test_label_shift_and_repaint_are_accessible_next_to_generate_without_ai(map_page):
    page = map_page
    page.evaluate('closeFillingStudio()')
    assert page.locator('#cliForm #restyleCurrentMapBtn').count() == 1
    assert page.locator('#fillingStudioModal #restyleCurrentMapBtn').count() == 0
    assert page.locator('#restyleCurrentMapBtn').is_visible()
    payloads = []
    ai_calls = []
    page.on('request', lambda request: ai_calls.append(request.url) if '/api/generate' in request.url else None)
    png = io.BytesIO()
    Image.new('RGB', (2, 2), 'green').save(png, format='PNG')
    image_url = 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode('ascii')
    def repaint(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(json={'status': 'success', 'image_url': image_url})
    page.route('**/api/restyle_map', repaint)
    for key, offset in [('End', 500), ('Home', -500)]:
        page.locator('#labelOffsetX').press(key)
        assert page.evaluate('getFormState().label_style.offset_x') == offset
        assert page.locator('#labelOffsetXValue').inner_text() == f'{offset:+d} px'
        page.click('#restyleCurrentMapBtn')
        page.wait_for_function('!mapRepaintPending')
        assert payloads[-1]['label_style']['offset_x'] == offset
        assert payloads[-1]['map_values'] == VALUES
    assert not ai_calls
    page.reload()
    assert page.locator('#labelOffsetX').input_value() == '-500'
    page.add_style_tag(content='.hidden {display:none!important}')
    page.locator('button[onclick="updateLabelOffset(0)"]').click()
    assert page.evaluate('getFormState().label_style.offset_x') == 0


def test_run_history_persists_map_and_settings_without_api_keys(map_page, web):
    page = map_page
    image = io.BytesIO()
    Image.new('RGB', (2, 2), 'purple').save(image, format='PNG')
    image_url = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
    page.evaluate('''async ({image_url, analysis, values}) => {
        await runHistoryReady;
        addToHistory({...getFormState(), topic:'Saved map topic', api_key:'do-not-store-in-history',
                      label_style:{...getFormState().label_style, offset_x:75}}, 'Saved command',
                     {image_url, analysis, map_values:values});
        await historyWrites;
    }''', {'image_url': image_url, 'analysis': web._build_analysis_from_ai(VALUES, mode='text'), 'values': VALUES})
    saved = page.evaluate('StudioStorage.getHistory()')
    assert len(saved) == 1
    assert 'api_key' not in saved[0]['state']
    assert 'do-not-store-in-history' not in str(saved)
    page.reload()
    page.evaluate('async () => { await runHistoryReady; }')
    page.add_style_tag(content='.hidden {display:none!important}')
    assert page.locator('#historyCountBadge').inner_text() == '1'
    page.evaluate("switchBottomTab('history')")
    page.locator('#historyList > div').first.click()
    assert page.locator('#topicInput').input_value() == 'Saved map topic'
    assert page.locator('#serverMapImage').get_attribute('src') == image_url
    assert page.evaluate('lastMapValues') == VALUES
    assert page.evaluate('getFormState().label_style.offset_x') == 75


def test_history_older_than_one_day_is_deleted_on_open_and_while_open(map_page):
    page = map_page
    page.evaluate('''async () => {
        await runHistoryReady;
        const state = getFormState(), now = Date.now();
        await StudioStorage.addHistory({id:'recent', createdAt:now-23*3600000, state, cmd:'Recent'});
        await StudioStorage.addHistory({id:'expired', createdAt:now-25*3600000, state, cmd:'Expired'});
    }''')
    page.reload()
    page.evaluate('async () => { await runHistoryReady; }')
    assert page.evaluate('runHistory.map(entry => entry.id)') == ['recent']
    assert page.evaluate('async () => (await StudioStorage.getHistory()).map(entry => entry.id)') == ['recent']
    page.evaluate('''async () => {
        const later = Date.now() + 2*3600000;
        Date.now = () => later;
        pruneRunHistory();
        await historyWrites;
    }''')
    assert page.locator('#historyCountBadge').inner_text() == '0'
    assert page.evaluate('StudioStorage.getHistory()') == []


def test_clear_history_is_persistent_and_late_restore_cannot_resurrect_it(map_page):
    page = map_page
    page.evaluate('''async () => {
        await runHistoryReady;
        addToHistory(getFormState(), 'Clear this');
        await historyWrites;
        const old = [...runHistory];
        let release;
        StudioStorage.getHistory = () => new Promise(resolve => release = resolve);
        const loading = restoreRunHistory();
        clearHistory();
        release(old);
        await loading;
        await historyWrites;
    }''')
    assert page.locator('#historyCountBadge').inner_text() == '0'
    page.reload()
    page.evaluate('async () => { await runHistoryReady; }')
    assert page.evaluate('runHistory.length') == 0
    assert page.evaluate('StudioStorage.getHistory()') == []
    page.add_init_script("indexedDB.open = () => { throw new DOMException('Blocked', 'SecurityError'); };")
    page.reload()
    page.evaluate('''async () => {
        await runHistoryReady;
        addToHistory(getFormState(), 'Memory only');
        await historyWrites;
    }''')
    assert page.locator('#historyCountBadge').inner_text() == '1'
    assert 'Kept for this visit only' in page.locator('#historyStorageStatus').inner_text()
