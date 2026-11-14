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
    # Hardware availability is a worker capability, independent of the test host.
    monkeypatch.setattr(worker, '_available_codecs', lambda: ('libx264', 'h264_nvenc'))
    def get_upstream(url, **kwargs):
        result = worker.app.test_client().get(urlsplit(url).path)
        response = requests.Response()
        response.status_code, response._content = result.status_code, result.data
        return response
    monkeypatch.setattr(requests, 'get', Mock(side_effect=get_upstream))
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
    assert page.locator('#stageTitleBanner img').evaluate('el => el.naturalWidth') == 1040
    page.get_by_role('button', name='Reset to defaults').click()
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
    page.wait_for_function("document.querySelector('#stageMapPreview').style.width === '1040px'")
    assert page.locator('#stageMapPreview').evaluate('el => el.style.height') == '520px'
    original = page.locator('#stageMapPreview').get_attribute('src')
    replacement = tmp_path / 'replacement.png'
    Image.new('RGB', (400, 800), 'blue').save(replacement)
    page.locator('#mapFileInput').set_input_files(str(replacement))
    page.wait_for_function('(url) => window.revokedUrls.includes(url)', arg=original)
    page.wait_for_function("parseFloat(document.querySelector('#stageMapPreview').style.height) > 1200")
    page.locator('#videoTitleInput').fill('Найкрасивіші країни Європи')
    wait_for_title(page)
    page.wait_for_function("document.querySelector('#stageMapPreview').getBoundingClientRect().top >= document.querySelector('#stageTitleBanner').getBoundingClientRect().bottom + 3")
    layout = page.evaluate('''() => {
        const stage = document.querySelector('.video-preview-stage').getBoundingClientRect();
        const title = document.querySelector('#stageTitleBanner').getBoundingClientRect();
        const map = document.querySelector('#stageMapPreview').getBoundingClientRect();
        return {titleTop: (title.top-stage.top)/stage.height, mapHeight: map.height/stage.height, mapBottom: (map.bottom-stage.top)/stage.height};
    }''')
    assert layout['titleTop'] == pytest.approx(70/1920, abs=.002)
    assert layout['mapHeight'] > .62
    assert layout['mapBottom'] <= 1761/1920
    page.locator('#mapDropzone .dropzone-clear').click()
    assert page.locator('#stageMapPreview').is_hidden()
    assert page.evaluate('window.revokedUrls.length') == 2


def test_title_color_pickers_preview_and_reset(studio):
    page, previews = studio
    assert page.locator('#titleFirstLineColor').input_value() == '#ffffff'
    assert page.locator('#titleOtherLinesColor').input_value() == '#ffe735'
    page.locator('#videoTitleInput').fill('Перший рядок\nДругий рядок')
    wait_for_title(page)
    page.locator('#titleFirstLineColor').evaluate("el => {el.value='#ff6600'; el.dispatchEvent(new Event('input'));}")
    page.locator('#titleOtherLinesColor').evaluate("el => {el.value='#3366ff'; el.dispatchEvent(new Event('input'));}")
    wait_for_title(page)
    assert previews[-1]['title_first_line_color'] == '#ff6600'
    assert previews[-1]['title_other_lines_color'] == '#3366ff'
    assert previews[-1]['video_title'] == 'Перший рядок\nДругий рядок'
    colors = page.locator('#stageTitleBanner img').evaluate("""image => {
        const canvas = document.createElement('canvas');
        canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let orange = 0, blue = 0;
        for (let i=0; i<pixels.length; i+=4) {
            if (pixels[i+3] < 240) continue;
            if (pixels[i]===255 && pixels[i+1]===102 && pixels[i+2]===0) orange++;
            if (pixels[i]===51 && pixels[i+1]===102 && pixels[i+2]===255) blue++;
        }
        return {orange, blue};
    }""")
    assert colors['orange'] > 100 and colors['blue'] > 100
    page.get_by_role('button', name='Reset to defaults').click()
    wait_for_title(page)
    assert previews[-1]['title_first_line_color'] == '#ffffff'
    assert previews[-1]['title_other_lines_color'] == '#ffe735'


def test_watermark_upload_text_effects_and_render_payload(studio, tmp_path):
    page, previews = studio
    logo = tmp_path / 'logo.png'
    Image.new('RGBA', (200, 200), (255, 0, 255, 255)).save(logo)
    page.locator('#videoTitleInput').fill('How many lessons usually\nhave children in primary\nschool in your country?')
    wait_for_title(page)
    page.locator('#watermarkFileInput').set_input_files(str(logo))
    page.wait_for_function("!document.querySelector('#stageWatermarkImage').classList.contains('hidden')")
    assert page.locator('#watermarkThumbPreview').is_visible()
    assert page.locator('#watermarkThumbPreview').bounding_box()['height'] == 88
    assert page.locator('#stageWatermarkImage').evaluate("el => getComputedStyle(el).borderRadius") == '50%'
    page.locator('#watermarkFontSelect').select_option('Bips.ttf')
    assert page.locator('#titleFontSelect').input_value() == 'DIN Condensed.ttf'
    page.locator('#watermarkTextInput').fill('URANIANGUY')
    page.wait_for_function("document.querySelector('#watermarkPreviewStatus').textContent === 'Preview matches rendered watermark'")
    assert previews[-1]['preview_kind'] == 'watermark'
    assert previews[-1]['watermark_text'] == 'URANIANGUY'
    assert previews[-1]['watermark_font'] == 'Bips.ttf'
    assert page.locator('#stageCompositeBox').evaluate("el => el.classList.contains('has-branding')")
    page.locator('#visualEffects').uncheck()
    page.wait_for_function("document.querySelector('#watermarkPreviewStatus').textContent === 'Preview matches rendered watermark'")
    assert page.locator('.video-preview-stage').evaluate("el => !el.classList.contains('effects-on')")
    assert previews[-1]['visual_effects'] is False
    page.locator('#visualEffects').check()
    map_file = tmp_path / 'map.png'
    Image.new('RGBA', (600, 900), (100, 180, 50, 255)).save(map_file)
    page.locator('#mapFileInput').set_input_files(str(map_file))
    page.wait_for_function("document.querySelector('#stageMapPreview').naturalWidth > 0")
    page.wait_for_function("document.querySelector('#watermarkPreviewStatus').textContent === 'Preview matches rendered watermark'")
    geometry = page.evaluate('''() => {
        const stage = document.querySelector('.video-preview-stage').getBoundingClientRect();
        const map = document.querySelector('#stageMapPreview').getBoundingClientRect();
        const logo = document.querySelector('#stageWatermarkImage').getBoundingClientRect();
        const text = document.querySelector('#stageWatermarkText').getBoundingClientRect();
        return {mapBottom:(map.bottom-stage.top)/stage.height, logoTop:(logo.top-stage.top)/stage.height,
            textTop:(text.top-stage.top)/stage.height};
    }''')
    assert geometry['mapBottom'] <= 1451 / 1920
    assert geometry['logoTop'] == pytest.approx(1470 / 1920, abs=.003)
    assert geometry['textTop'] == pytest.approx(1740 / 1920, abs=.003)
    # Check actual browser-to-server fields without spending time encoding a video here.
    page.evaluate("renderFiles.bgFileInput = new File(['test'], 'background.mp4', {type:'video/mp4'})")
    payloads = []
    def capture(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(status=400, content_type='application/json', body='{"error":"Test render captured"}')
    page.route('**/api/render_video', capture)
    page.evaluate("handleRenderVideo({preventDefault(){}})")
    assert payloads[0]['watermark_text'] == 'URANIANGUY'
    assert payloads[0]['watermark_font'] == 'Bips.ttf'
    assert payloads[0]['watermark_image'].startswith('data:image/png;base64,')
    assert payloads[0]['visual_effects'] is True
    page.get_by_role('button', name='Remove logo').click()
    assert page.locator('#stageWatermarkImage').is_hidden()
    assert page.locator('#watermarkThumbPreview').is_hidden()
    page.locator('#watermarkTextInput').fill('')
    assert page.locator('#stageWatermarkText').is_hidden()
    assert page.locator('#stageCompositeBox').evaluate("el => !el.classList.contains('has-branding')")


def test_watermark_drag_drop_replacement_and_validation(studio):
    import base64
    import io

    page, _ = studio
    buffer = io.BytesIO()
    Image.new('RGBA', (40, 40), 'purple').save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode()
    def drop(name, mime='image/png'):
        page.locator('#watermarkDropzone').evaluate('''(zone, args) => {
            const bytes = Uint8Array.from(atob(args.encoded), c => c.charCodeAt(0));
            const transfer = new DataTransfer();
            transfer.items.add(new File([bytes], args.name, {type:args.mime}));
            zone.dispatchEvent(new DragEvent('dragover', {bubbles:true, cancelable:true, dataTransfer:transfer}));
            zone.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:transfer}));
        }''', {'encoded': encoded, 'name': name, 'mime': mime})
    drop('first.png')
    page.wait_for_function("document.querySelector('#watermarkFileName').textContent === 'first.png'")
    original = page.locator('#stageWatermarkImage').get_attribute('src')
    drop('replacement.png')
    page.wait_for_function("document.querySelector('#watermarkFileName').textContent === 'replacement.png'")
    assert page.locator('#stageWatermarkImage').get_attribute('src') != original
    assert page.locator('#watermarkDropzone').evaluate("el => !el.classList.contains('drag-active')")
    drop('wrong.mp4', 'video/mp4')
    assert page.locator('#watermarkFileName').inner_text() == 'replacement.png'
    page.get_by_role('button', name='Remove logo').click()
    assert page.locator('#stageWatermarkImage').is_hidden()
    with page.expect_file_chooser():
        page.locator('#watermarkDropzone').press('Enter')


def test_codec_capabilities_and_selection_stay_in_sync(studio):
    page, _ = studio
    page.wait_for_function("!document.querySelector('[data-codec=h264_nvenc]').disabled")
    assert page.locator('[data-codec="h264_videotoolbox"]').is_disabled()
    assert page.locator('#codecSelect option[value="h264_videotoolbox"]').is_disabled()
    page.locator('[data-codec="h264_nvenc"]').click()
    assert page.locator('#codecSelect').input_value() == 'h264_nvenc'
    assert page.locator('#stageCodecBadge').inner_text() == 'h264_nvenc'
    assert page.locator('[data-codec="h264_nvenc"]').get_attribute('aria-pressed') == 'true'
    page.locator('#codecSelect').select_option('libx264')
    assert page.locator('#stageCodecBadge').inner_text() == 'libx264'
    assert page.locator('[data-codec="libx264"]').get_attribute('aria-pressed') == 'true'
    # A failed refresh returns to CPU instead of retaining a stale hardware choice.
    page.locator('#codecSelect').select_option('h264_nvenc')
    page.route('**/api/codecs', lambda route: route.fulfill(status=502, content_type='application/json', body='{}'))
    page.evaluate('loadCodecs()')
    assert page.locator('#codecSelect').input_value() == 'libx264'
    assert page.locator('[data-codec="h264_nvenc"]').is_disabled()


def reopen_studio(page):
    page.goto('http://automap.test/studio')
    page.add_style_tag(content=OFFLINE_CSS)
    page.evaluate('async () => { await window.videoStudioReady; }')


def test_generated_map_transfers_and_respects_manual_selection(studio, tmp_path):
    import base64
    import io
    import json

    page, _ = studio
    def generate_map(size):
        output = io.BytesIO()
        Image.new('RGBA', size, (20, 160, 90, 255)).save(output, format='PNG')
        data_url = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        page.route('**/api/status', lambda route: route.fulfill(
            content_type='application/json', body='{"needs_api_key":false}'))
        page.route('**/api/generate', lambda route: route.fulfill(
            content_type='application/json', body=json.dumps({'status': 'success', 'image_url': data_url})))
        page.goto('http://automap.test/')
        page.locator('#topicInput').fill('Test map')
        page.evaluate('apiKeyRequired = false; handleGenerate({preventDefault(){}})')
    generate_map((450, 300))
    reopen_studio(page)
    page.wait_for_function("document.querySelector('#stageMapPreview').naturalWidth === 450")
    assert page.locator('#mapDropzone .dropzone-name').inner_text().startswith('generated-map.png')
    assert page.evaluate('renderFiles.mapFileInput.type') == 'image/png'
    custom = tmp_path / 'custom.png'
    Image.new('RGBA', (320, 200), 'blue').save(custom)
    page.locator('#mapFileInput').set_input_files(str(custom))
    page.evaluate('async () => { await pendingAssetSaves; }')
    reopen_studio(page)
    page.wait_for_function("document.querySelector('#stageMapPreview').naturalWidth === 320")
    assert page.locator('#mapDropzone .dropzone-name').inner_text().startswith('custom.png')
    page.locator('#mapDropzone .dropzone-clear').click()
    page.evaluate('async () => { await pendingAssetSaves; }')
    reopen_studio(page)
    assert page.locator('#stageMapPreview').is_hidden()
    generate_map((500, 300))
    reopen_studio(page)
    page.wait_for_function("document.querySelector('#stageMapPreview').naturalWidth === 500")


def test_video_settings_and_uploaded_materials_survive_navigation(studio, tmp_path):
    page, _ = studio
    page.evaluate('async () => { await window.videoStudioReady; }')
    page.locator('#musicUrlInput').fill('https://music.youtube.com/watch?v=example')
    page.evaluate("setCutPreset('01:12'); setCutDuration(15)")
    page.locator('#videoTitleInput').fill('Saved title')
    page.locator('#titleFontSelect').select_option('Bips.ttf')
    page.locator('#titleFontSize').evaluate("el => {el.value=76; el.dispatchEvent(new Event('input', {bubbles:true}));}")
    page.locator('#titleOtherLinesColor').evaluate("el => {el.value='#ff6600'; el.dispatchEvent(new Event('input', {bubbles:true}));}")
    page.locator('#visualEffects').uncheck()
    page.locator('#watermarkTextInput').fill('Saved channel')
    page.locator('#watermarkFontSelect').select_option('arialroundedmtbold.ttf')
    page.locator('#codecSelect').select_option('h264_nvenc')
    logo = tmp_path / 'saved-logo.png'
    Image.new('RGBA', (80, 80), 'purple').save(logo)
    page.locator('#watermarkFileInput').set_input_files(str(logo))
    page.wait_for_function("document.querySelector('#watermarkFileName').textContent === 'saved-logo.png'")
    page.locator('#bgFileInput').set_input_files({
        'name': 'saved-background.mp4', 'mimeType': 'video/mp4', 'buffer': b'test-video-bytes',
    })
    page.evaluate('async () => { await pendingAssetSaves; }')
    page.goto('http://automap.test/')
    reopen_studio(page)
    wait_for_title(page)
    assert page.locator('#musicUrlInput').input_value().endswith('v=example')
    assert page.locator('#cutStartInput').input_value() == '01:12'
    assert page.locator('#cutDurationInput').input_value() == '15'
    assert page.locator('#cutDurationRange').input_value() == '15'
    assert page.locator('#titleFontSelect').input_value() == 'Bips.ttf'
    assert page.locator('#titleFontSizeValue').inner_text() == '76 px'
    assert page.locator('#titleOtherLinesColor').input_value() == '#ff6600'
    assert not page.locator('#visualEffects').is_checked()
    assert page.locator('#watermarkTextInput').input_value() == 'Saved channel'
    assert page.locator('#watermarkFontSelect').input_value() == 'arialroundedmtbold.ttf'
    page.wait_for_function("document.querySelector('#codecSelect').value === 'h264_nvenc'")
    assert page.locator('#watermarkThumbPreview').is_visible()
    assert page.evaluate('watermarkFile.name') == 'saved-logo.png'
    assert page.evaluate('renderFiles.bgFileInput.name') == 'saved-background.mp4'
    assert page.evaluate('renderFiles.bgFileInput.size') == len(b'test-video-bytes')


def test_storage_failure_does_not_break_video_studio(studio):
    page, _ = studio
    page.add_init_script('''
        Storage.prototype.getItem = () => { throw new Error('Storage unavailable'); };
        Storage.prototype.setItem = () => { throw new Error('Storage unavailable'); };
        Object.defineProperty(window, 'indexedDB', {value: {open() { throw new Error('Storage unavailable'); }}});
    ''')
    reopen_studio(page)
    page.locator('#videoTitleInput').fill('Still works')
    wait_for_title(page)
    assert page.locator('#stageTitleBanner img').is_visible()
    assert 'cannot be saved' in page.locator('#studioSaveStatus').inner_text()


def test_large_generated_map_is_prepared_for_video_without_changing_original(studio):
    import base64
    import io

    page, _ = studio
    output = io.BytesIO()
    Image.new('RGBA', (320, 200), (30, 90, 150, 255)).save(output, format='PNG')
    encoded = base64.b64encode(output.getvalue()).decode()
    page.evaluate('''async encoded => {
        const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
        const original = new File([bytes, new Uint8Array(11*1024*1024)], 'large-map.png', {type:'image/png'});
        await StudioStorage.setAsset('latest-map', original);
    }''', encoded)
    reopen_studio(page)
    page.wait_for_function("document.querySelector('#stageMapPreview').naturalWidth === 320")
    assert page.evaluate('renderFiles.mapFileInput.size') < 10 * 1024 * 1024
    assert page.evaluate("async () => (await StudioStorage.getAsset('latest-map')).file.size") > 10 * 1024 * 1024


def test_restore_does_not_overwrite_a_new_manual_map(studio):
    page, _ = studio
    page.evaluate('async () => { await window.videoStudioReady; }')
    page.evaluate('''async () => {
        const getAsset = StudioStorage.getAsset;
        let release;
        const waiting = new Promise(resolve => release = resolve);
        StudioStorage.getAsset = async key => {
            if (key === 'latest-map') return waiting;
            return getAsset(key);
        };
        const restoring = restoreVideoStudio();
        const canvas = document.createElement('canvas'); canvas.width=80; canvas.height=40;
        const blob = await new Promise(resolve => canvas.toBlob(resolve));
        acceptFile('mapFileInput', new File([blob], 'manual.png', {type:'image/png'}));
        release({file: new File([blob], 'old-generated.png', {type:'image/png'}), updatedAt:Date.now()});
        await restoring;
    }''')
    assert page.evaluate('renderFiles.mapFileInput.name') == 'manual.png'
