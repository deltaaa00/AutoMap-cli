"""Offline browser interaction tests; CDN styling is deliberately stubbed.

Python API and real title rasterization run in-process. These are behavior
checks, not visual regression tests of external Tailwind/font assets.
"""
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import Mock

import pytest
import requests
from PIL import Image
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.browser

# Only the layout primitives required for interaction/visibility checks.
# Product CSS still comes from the actual template.
OFFLINE_CSS = '''
*,::before,::after {box-sizing:border-box}
.hidden {display:none!important} .relative {position:relative}
.absolute {position:absolute} .flex {display:flex}
.flex-col {flex-direction:column} .items-center {align-items:center}
.justify-center {justify-content:center} .w-full {width:100%} .h-full {height:100%}
'''


@pytest.fixture
def studio(web, worker, monkeypatch):
    def upstream(url, **kwargs):
        result = worker.app.test_client().post(urlsplit(url).path, json=kwargs['json'])
        response = requests.Response()
        response.status_code = result.status_code
        response._content = result.data
        response.headers.update(result.headers)
        return response

    monkeypatch.setattr(requests, 'post', Mock(side_effect=upstream))
    with sync_playwright() as playwright:
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        kwargs = {'executable_path': str(chrome)} if chrome.exists() else {}
        browser = playwright.chromium.launch(headless=True, **kwargs)
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        errors = []
        previews = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route_request(route):
            request = route.request
            url = urlsplit(request.url)
            if url.hostname == 'automap.test':
                if url.path == '/api/video_title_preview':
                    previews.append(request.post_data_json)
                result = web.app.test_client().open(
                    url.path + ('?' + url.query if url.query else ''),
                    method=request.method, data=request.post_data,
                    content_type=request.headers.get('content-type'),
                )
                route.fulfill(status=result.status_code, body=result.data,
                              content_type=result.content_type)
            elif url.hostname == 'cdn.tailwindcss.com':
                route.fulfill(content_type='application/javascript', body='window.tailwind = {};')
            else:
                route.fulfill(content_type='text/css', body='')

        page.route('**/*', route_request)
        page.goto('http://automap.test/studio')
        page.add_style_tag(content=OFFLINE_CSS)
        yield page, previews
        assert not errors, errors
        browser.close()


def wait_for_title(page):
    page.wait_for_function("document.querySelector('#titlePreviewStatus').textContent === 'Preview matches rendered title'")


def test_font_size_reset_and_mobile_frame(studio):
    page, previews = studio
    assert page.locator('#titleFontSelect').input_value() == 'DIN Condensed.ttf'
    assert page.locator('#titleFontSize').input_value() == '100'
    assert page.locator('#titleFontSelect option').count() >= 4
    page.locator('#videoTitleInput').fill('Найкрасивіші країни Європи')
    wait_for_title(page)
    page.locator('#titleFontSelect').select_option('Bips.ttf')
    page.locator('#titleFontSize').evaluate("el => {el.value=72; el.dispatchEvent(new Event('input'));}")
    wait_for_title(page)
    assert previews[-1]['title_font'] == 'Bips.ttf'
    assert previews[-1]['title_font_size'] == 72
    assert page.locator('#titleFontSizeValue').inner_text() == '72 px'
    assert page.locator('#stageTitleBanner img').evaluate('el => el.naturalWidth') == 936
    page.get_by_role('button', name='Reset · DIN / 100 px').click()
    wait_for_title(page)
    assert previews[-1]['title_font'] == 'DIN Condensed.ttf'
    assert previews[-1]['title_font_size'] == 100
    page.set_viewport_size({'width': 390, 'height': 844})
    page.wait_for_function("Math.abs(document.querySelector('#stageCompositeBox').getBoundingClientRect().width - document.querySelector('.video-preview-stage').clientWidth) < 1")
    frame = page.locator('.video-preview-stage').bounding_box()
    assert frame['width'] / frame['height'] == pytest.approx(9 / 16, abs=.005)


def test_rapid_title_edits_are_debounced_and_clear_hides_title(studio):
    page, previews = studio
    page.locator('#videoTitleInput').evaluate('''el => {
        for (const title of ['First', 'Second', 'Final title']) {
            el.value = title; el.dispatchEvent(new Event('input'));
        }
    }''')
    wait_for_title(page)
    assert len(previews) == 1
    assert previews[0]['video_title'] == 'Final title'
    assert page.locator('#stageTitleBanner img').get_attribute('alt') == 'Final title'
    page.locator('#videoTitleInput').fill('')
    assert page.locator('#stageTitleBanner').is_hidden()
    assert page.locator('#titleCharCount').inner_text() == '0 chars'


def test_map_fit_replacement_and_removal_release_blob_urls(studio, tmp_path):
    page, _ = studio
    page.evaluate('''() => {
        window.revokedUrls = [];
        const original = URL.revokeObjectURL.bind(URL);
        URL.revokeObjectURL = url => {window.revokedUrls.push(url); original(url);};
    }''')
    image = tmp_path / 'map.png'
    Image.new('RGB', (800, 400), 'green').save(image)
    page.locator('#mapFileInput').set_input_files(str(image))
    page.wait_for_function("document.querySelector('#stageMapPreview').style.width === '936px'")
    assert page.locator('#stageMapPreview').evaluate('el => el.style.height') == '468px'
    original = page.locator('#stageMapPreview').get_attribute('src')
    replacement = tmp_path / 'replacement.png'
    Image.new('RGB', (400, 800), 'blue').save(replacement)
    page.locator('#mapFileInput').set_input_files(str(replacement))
    page.wait_for_function('(url) => window.revokedUrls.includes(url)', arg=original)
    page.locator('#mapDropzone .dropzone-clear').click()
    assert page.locator('#stageMapPreview').is_hidden()
    assert page.evaluate('window.revokedUrls.length') == 2
