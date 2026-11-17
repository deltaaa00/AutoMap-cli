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

import base64
import io
from unittest.mock import Mock

import pytest
import requests


def test_pages_and_discovery(web):
    client = web.app.test_client()
    for path in ('/', '/studio', '/api/status', '/api/fonts', '/api/fonts/'):
        response = client.get(path)
        assert response.status_code == 200, path
        if path.startswith('/api/fonts'):
            assert 'DIN Condensed.ttf' in response.json['fonts']
            assert response.json['count'] == len(response.json['fonts'])
        elif path == '/api/status':
            assert response.json == {'needs_api_key': True}
        elif path == '/studio':
            assert b'titleFontSelect' in response.data and b'titleFontSize' in response.data


@pytest.fixture
def generation_request():
    return dict(topic='Population', mode='number', map_type='default', model='mock-model', api_key='form-key')


def test_generate_forwards_options_and_encodes_png(web, monkeypatch, generation_request):
    generate = Mock(return_value=io.BytesIO(b'png-data'))
    monkeypatch.setattr(web, 'auto_map', generate)
    response = web.app.test_client().post('/api/generate', json={**generation_request,
        'font': 'DIN Condensed.ttf', 'gsm': '22', 'extra_option': 'show_most_least'})
    assert response.status_code == 200
    assert base64.b64decode(response.json['image_url'].split(',')[1]) == b'png-data'
    generate.assert_called_once_with(prompt='Population', mode='number', map_type='default',
        size_mod=22, optional_feature='show_most_least', model='mock-model',
        font_name='DIN Condensed.ttf', show_result=False, io_res=True, api_key='form-key', show_legend=False)
    assert 'form-key' not in response.json['command']


def test_generate_forwards_temperature_including_zero(web, monkeypatch, generation_request):
    generate = Mock(return_value=io.BytesIO(b'png'))
    monkeypatch.setattr(web, 'auto_map', generate)
    client = web.app.test_client()
    for temperature in (0, 0.4, 2):
        response = client.post('/api/generate', json={**generation_request, 'temperature': temperature})
        assert response.status_code == 200
        assert generate.call_args.kwargs['temperature'] == temperature
        assert f'--temperature {temperature:g}' in response.json['command']


def test_generate_rejects_invalid_temperature_before_rendering(web, monkeypatch, generation_request):
    generate = Mock()
    monkeypatch.setattr(web, 'auto_map', generate)
    client = web.app.test_client()
    for temperature in (-1, 3, 'NaN', 'Infinity', True, None, 'invalid', {}):
        response = client.post('/api/generate', json={**generation_request, 'temperature': temperature})
        assert response.status_code == 400, temperature
        assert 'Temperature' in response.json['error'], temperature
    generate.assert_not_called()


def test_generate_defaults_and_invalid_font_fallback(web, monkeypatch, generation_request):
    generate = Mock(return_value=io.BytesIO(b'png'))
    monkeypatch.setattr(web, 'auto_map', generate)
    client = web.app.test_client()
    for gsm in (None, 'bad', 15):
        for extra in ('none', '', None):
            response = client.post('/api/generate/', json={
                **generation_request, 'font': '../bad.ttf', 'gsm': gsm, 'extra_option': extra,
            })
            assert response.status_code == 200
            assert generate.call_args.kwargs['font_name'] == 'Bips.ttf'
            assert generate.call_args.kwargs['size_mod'] == 15
            assert generate.call_args.kwargs['optional_feature'] is None


def test_generate_requires_key_without_server_key(web, monkeypatch, generation_request):
    generate = Mock()
    monkeypatch.setattr(web, 'auto_map', generate)
    generation_request.pop('api_key')
    assert web.app.test_client().post('/api/generate', json=generation_request).status_code == 400
    generate.assert_not_called()


def test_generate_server_key_does_not_require_form_key(web, monkeypatch, generation_request):
    monkeypatch.setattr(web, 'needs_api_key', False)
    generate = Mock(return_value=io.BytesIO(b'png'))
    monkeypatch.setattr(web, 'auto_map', generate)
    generation_request.pop('api_key')
    assert web.app.test_client().post('/api/generate', json=generation_request).status_code == 200
    assert generate.call_args.kwargs['api_key'] is None


PROXIES = [
    ('/api/music_download', {'url': 'https://example.test/song'}, 150),
    ('/api/music_cut', {'title': 'song', 'start_time': '00:01'}, 40),
    ('/api/render_video', {'map_file': 'map', 'background_video': 'bg', 'video_title': 'title'}, 300),
]


def test_json_proxy_passes_response(web, monkeypatch):
    client = web.app.test_client()
    for path, payload, timeout in PROXIES:
        for status in (200, 400, 500):
            upstream = Mock(status_code=status)
            upstream.json.return_value = {'status': 'success' if status == 200 else 'error', 'file': 'data'}
            post = Mock(return_value=upstream)
            monkeypatch.setattr(requests, 'post', post)
            response = client.post(path, json=payload)
            assert response.status_code == status and response.json == upstream.json.return_value
            assert post.call_args.kwargs['timeout'] == timeout
            if path.endswith('render_video'):
                assert post.call_args.kwargs['json']['title_font'] == 'DIN Condensed.ttf'
                assert post.call_args.kwargs['json']['title_font_size'] == 100
                assert post.call_args.kwargs['json']['include_audio'] is False


def test_proxy_failure_returns_bad_gateway(web, monkeypatch):
    client = web.app.test_client()
    for path, payload, _ in PROXIES:
        for failure in ('network', 'json'):
            upstream = Mock()
            upstream.json.side_effect = ValueError('not json')
            post = (
                Mock(side_effect=requests.ConnectionError('offline'))
                if failure == 'network'
                else Mock(return_value=upstream)
            )
            monkeypatch.setattr(requests, 'post', post)
            assert client.post(path, json=payload).status_code == 502, (path, failure)


def test_missing_proxy_fields(web):
    client = web.app.test_client()
    for path in ('/api/music_download', '/api/music_cut', '/api/download_video'):
        response = client.get(path) if 'download_video' in path else client.post(path, json={})
        assert response.status_code == 400, path


def test_cut_duration_normalization(web, monkeypatch):
    client = web.app.test_client()
    for raw, expected in ((0, 1), (100, 30), ('bad', 8), (None, 8), ('12', 12)):
        post = Mock(return_value=Mock(status_code=200, json=lambda: {}))
        monkeypatch.setattr(requests, 'post', post)
        client.post('/api/music_cut', json={'title': 'song', 'duration': raw})
        assert post.call_args.kwargs['json']['duration'] == expected, raw


def test_render_forwards_title_style_and_legacy_title(web, monkeypatch):
    post = Mock(return_value=Mock(status_code=200, json=lambda: {}))
    monkeypatch.setattr(requests, 'post', post)
    web.app.test_client().post('/api/render_video', json={
        'title': ' Legacy ', 'title_font': 'Bips.ttf', 'title_font_size': 72,
        'title_first_line_color': '#ff6600', 'title_other_lines_color': '#3366ff',
        'watermark_image': 'base64-logo', 'watermark_text': 'Channel',
        'watermark_color': '#a4e9ee', 'visual_effects': False,
        'watermark_font': 'arialroundedmtbold.ttf',
        'audio_title': 'selected-song', 'audio_file': 'data:audio/mpeg;base64,YXVkaW8=',
        'include_audio': False,
    })
    assert post.call_args.kwargs['json']['video_title'] == 'Legacy'
    assert post.call_args.kwargs['json']['title_font'] == 'Bips.ttf'
    assert post.call_args.kwargs['json']['title_font_size'] == 72
    assert post.call_args.kwargs['json']['title_first_line_color'] == '#ff6600'
    assert post.call_args.kwargs['json']['title_other_lines_color'] == '#3366ff'
    assert post.call_args.kwargs['json']['watermark_image'] == 'base64-logo'
    assert post.call_args.kwargs['json']['watermark_text'] == 'Channel'
    assert post.call_args.kwargs['json']['watermark_font'] == 'arialroundedmtbold.ttf'
    assert post.call_args.kwargs['json']['audio_title'] == 'selected-song'
    assert post.call_args.kwargs['json']['include_audio'] is True
    assert post.call_args.kwargs['json']['audio_file'] == 'data:audio/mpeg;base64,YXVkaW8='
    assert post.call_args.kwargs['json']['visual_effects'] is False


@pytest.mark.parametrize('zoom,reveal,expected', [
    (False, True, False), ('false', True, False),
    (True, False, False), (True, True, True), ('true', 'true', True),
])
def test_country_reveal_requires_zoom(web, monkeypatch, zoom, reveal, expected):
    post = Mock(return_value=Mock(status_code=200, json=lambda: {}))
    monkeypatch.setattr(requests, 'post', post)
    extract = Mock(return_value='mask')
    monkeypatch.setattr(web, '_decode_image_size', lambda _: (100, 100))
    monkeypatch.setattr(web, '_extract_country_reveal_mask', extract)
    response = web.app.test_client().post('/api/render_video', json={
        'map_file': 'map', 'zoom_enabled': zoom, 'reveal_enabled': reveal,
    })
    assert response.status_code == 200
    payload = post.call_args.kwargs['json']
    assert payload['reveal_enabled'] is expected
    assert payload['reveal_mask'] == ('mask' if expected else '')
    assert extract.called is expected


def test_title_preview_proxy_preserves_content(web, monkeypatch):
    client = web.app.test_client()
    for status in (200, 400):
        upstream = Mock(status_code=status, content=b'png-or-error', headers={'Content-Type': 'image/png'})
        post = Mock(return_value=upstream)
        monkeypatch.setattr(requests, 'post', post)
        response = client.post('/api/video_title_preview', json={
            'video_title': 'Title', 'title_font': 'Bips.ttf', 'title_font_size': 72, 'ignore': True,
            'title_first_line_color': '#ff6600', 'title_other_lines_color': '#3366ff'})
        assert response.status_code == status and response.data == b'png-or-error'
        assert response.mimetype == 'image/png'
        assert 'ignore' not in post.call_args.kwargs['json']
        assert post.call_args.kwargs['json']['title_first_line_color'] == '#ff6600'
        assert post.call_args.kwargs['json']['title_other_lines_color'] == '#3366ff'


def test_title_preview_unavailable(web, monkeypatch):
    monkeypatch.setattr(requests, 'post', Mock(side_effect=requests.Timeout()))
    assert web.app.test_client().post('/api/video_title_preview', json={}).status_code == 502


def test_video_download_streaming(web, monkeypatch):
    upstream = Mock(status_code=200, headers={'Content-Disposition': 'attachment; filename="clip.mp4"'})
    upstream.iter_content.return_value = iter([b'first', b'', b'last'])
    get = Mock(return_value=upstream)
    monkeypatch.setattr(requests, 'get', get)
    response = web.app.test_client().get('/api/download_video?path=/tmp/clip.mp4')
    assert response.status_code == 200 and response.data == b'firstlast'
    assert response.mimetype == 'video/mp4'
    assert 'clip.mp4' in response.headers['Content-Disposition']
    assert get.call_args.kwargs['stream'] is True


def test_video_download_errors(web, monkeypatch):
    client = web.app.test_client()
    for kind in ('network', 'json', 'text', 'invalid-json'):
        upstream = Mock(
            status_code=404,
            headers={'Content-Type': 'application/json' if 'json' in kind else 'text/plain'},
            text='missing',
        )
        upstream.json.return_value = {'error': 'missing'}
        if kind == 'invalid-json':
            upstream.json.side_effect = ValueError()
        get = Mock(side_effect=requests.ConnectionError()) if kind == 'network' else Mock(return_value=upstream)
        monkeypatch.setattr(requests, 'get', get)
        response = client.get('/api/download_video?path=clip.mp4')
        assert response.status_code == (502 if kind == 'network' else 404), kind


def test_codec_proxy_returns_worker_capabilities(web, monkeypatch):
    monkeypatch.setattr(requests, 'get', Mock(return_value=Mock(
        status_code=200, json=lambda: {'codecs': ['libx264']})))
    assert web.app.test_client().get('/api/codecs').json == {'codecs': ['libx264']}


def test_worker_url_environment_override(monkeypatch, request):
    monkeypatch.setenv('YTDLP_URL', 'http://127.0.0.1:9090/')
    web = request.getfixturevalue('web')
    get = Mock(return_value=Mock(status_code=200, json=lambda: {'codecs': []}))
    monkeypatch.setattr(requests, 'get', get)
    assert web.app.test_client().get('/api/codecs').status_code == 200
    get.assert_called_once_with('http://127.0.0.1:9090/api/codecs', timeout=20)


def test_codec_proxy_failure(web, monkeypatch):
    client = web.app.test_client()
    for failure in (requests.Timeout(), ValueError('bad JSON')):
        response = Mock()
        response.json.side_effect = failure
        monkeypatch.setattr(requests, 'get', Mock(return_value=response))
        assert client.get('/api/codecs').status_code == 502


def test_analyze_map_endpoint_returns_rules_and_style(web):
    client = web.app.test_client()
    res = client.post('/api/analyze_map', json={
        'topic': 'When is Saint Nicholas Day',
        'mode': 'text',
        'map_values': {'ukraine': '6 Dec', 'serbia': '19 Dec', 'netherlands': '5 Dec'},
    })
    assert res.status_code == 200
    data = res.json
    assert data['status'] == 'success'
    assert data['analysis']['detected_type'] == 'text'
    assert len(data['analysis']['ui_txt_rules']) == 3
    assert 'recommended_font' in data['analysis']['label_style']


def test_restyle_map_repaints_without_ai_request(web, monkeypatch):
    client = web.app.test_client()
    fake_buf = io.BytesIO(b'repainted-png')
    fake_buf.map_meta = {
        'ai_answer': {'ukraine': 'Yes', 'poland': 'No'},
        'resolved_theme': 'emerald',
        'used_txt': {'Yes': [16, 185, 129, 255], 'No': [239, 68, 68, 255]},
        'label_style': {
            'text_color': '#ffeeaa',
            'stroke_color': '#112233',
            'stroke_width': 11,
            'auto_fit': True,
            'recommended_font': 'Bips.ttf',
            'recommended_gsm': 15,
        },
    }
    mock_auto_map = Mock(return_value=fake_buf)
    monkeypatch.setattr(web, 'auto_map', mock_auto_map)

    res = client.post('/api/restyle_map', json={
        'topic': 'Test',
        'mode': 'text',
        'map_type': 'default',
        'gsm': 15,
        'font': 'Bips.ttf',
        'auto_style': False,
        'palette_theme': 'emerald',
        'filling_txt': {'Yes': [16, 185, 129, 255], 'No': [239, 68, 68, 255]},
        'label_style': {'text_color': '#ffeeaa', 'stroke_color': '#112233', 'stroke_width': 11, 'auto_fit': True},
        'map_values': {'ukraine': 'Yes', 'poland': 'No'},
    })
    assert res.status_code == 200
    assert res.json['status'] == 'success'
    assert base64.b64decode(res.json['image_url'].split(',')[1]) == b'repainted-png'
    assert mock_auto_map.call_args.kwargs['ai_answer_override'] == {'ukraine': 'Yes', 'poland': 'No'}
    assert mock_auto_map.call_args.kwargs['auto_style'] is False
    assert res.json['analysis']['label_style']['text_color'] == '#ffeeaa'
    assert res.json['analysis']['filling_txt']['Yes'] == [16, 185, 129, 255]


def test_restyle_map_requires_map_values(web):
    client = web.app.test_client()
    res = client.post('/api/restyle_map', json={'topic': 'Test', 'mode': 'text'})
    assert res.status_code == 400
    assert res.json['status'] == 'error'


def test_analyze_map_validates_and_inspects_uploaded_image(web):
    from PIL import Image
    client = web.app.test_client()

    # Valid PNG image is inspected for its actual pixel colors
    img = Image.new('RGBA', (32, 32), (20, 180, 90, 255))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')

    ok_res = client.post('/api/analyze_map', json={
        'image_data': f'data:image/png;base64,{b64}',
        'map_type': 'default',
        'prefer_cached': False,
    })
    assert ok_res.status_code == 200
    assert ok_res.json['source'] == 'palette_inspection'
    assert ok_res.json['map_values'] is None
    assert ok_res.json['analysis']['detected_type'] == 'palette'
    assert ok_res.json['analysis']['inspection_only'] is True
    assert ok_res.json['analysis']['stats']['sample_values'] == []
    assert '#14b45a' in ok_res.json['analysis']['filling_txt']


def test_analyze_map_supports_every_ui_palette_theme(web):
    client = web.app.test_client()
    themes = (
        'auto', 'viridis', 'heatmap', 'emerald_gold', 'festive', 'oceanic', 'categorical',
        'greens', 'blues', 'reds', 'oranges', 'yellows', 'purples', 'teals', 'pinks', 'earth_tones',
        'pastel', 'neon', 'monochrome', 'sunset',
    )
    for theme in themes:
        res = client.post('/api/analyze_map', json={
            'topic': 'Population',
            'mode': 'number',
            'palette_theme': theme,
            'map_values': {'germany': 84, 'france': 68, 'estonia': 1},
        })
        assert res.status_code == 200, theme
        expected = 'viridis' if theme == 'auto' else theme
        assert res.json['analysis']['resolved_theme'] == expected, theme


def test_analyze_map_prefer_cached_false_does_not_leak_previous_state(web):
    client = web.app.test_client()
    res = client.post('/api/analyze_map', json={
        'topic': 'When is Saint Nicholas Day',
        'mode': 'text',
        'prefer_cached': False,
    })
    assert res.status_code == 200
    assert res.json['source'] == 'topic_preview'
    assert res.json['map_values']['ukraine'] == '6 Dec'


def test_generate_returns_request_values_and_effective_style(web, monkeypatch, generation_request):
    output = io.BytesIO(b'png')
    output.map_meta = {
        'ai_answer': {'ukraine': 38}, 'resolved_theme': 'oceanic',
        'used_num': {'(0, 100)': [56, 189, 248, 255]},
        'label_style': {'text_color': '#abcdef'},
    }
    generate = Mock(return_value=output)
    monkeypatch.setattr(web, 'auto_map', generate)
    style = {'filling_txt': {}, 'filling_num': {'(0, 100)': '#38bdf8'},
             'label_style': {'text_color': '#abcdef'}, 'auto_style': False,
             'palette_theme': 'oceanic'}
    response = web.app.test_client().post('/api/generate', json={**generation_request, **style})
    assert response.status_code == 200
    assert response.json['map_values'] == {'ukraine': 38}
    assert response.json['analysis']['ui_num_rules'] == [
        {'low': 0, 'high': 100, 'hex': '#38bdf8', 'alpha': 255}]
    assert response.json['analysis']['label_style']['text_color'] == '#abcdef'
    kwargs = generate.call_args.kwargs
    assert kwargs['filling_txt_override'] == {}
    assert kwargs['filling_num_override'] == style['filling_num']
    for key in ('auto_style', 'palette_theme', 'label_style'):
        assert kwargs[key] == style[key]


def test_restyle_reports_renderer_failure(web, monkeypatch):
    client = web.app.test_client()
    for failure in (None, RuntimeError('repaint failed')):
        generate = Mock(side_effect=failure) if failure else Mock(return_value=None)
        monkeypatch.setattr(web, 'auto_map', generate)
        response = client.post('/api/restyle_map', json={'map_values': {'ukraine': 'Yes'}})
        assert response.status_code == 500
        assert response.json['status'] == 'error'
        assert 'image_url' not in response.json


def test_palette_inspection_rejects_invalid_uploads(web):
    client = web.app.test_client()
    cases = (
        ('', 'Empty image data'),
        ('data:text/plain;base64,YQ==', 'Unsupported data URL media type'),
        ('data:image/png;base64,', 'Decoded image payload is empty'),
        ('data:image/png;base64,YQ==', 'not a valid image'),
        ('data:image/png;base64,invalid', 'Invalid base64 image payload'),
    )
    for image_data, error in cases:
        response = client.post('/api/analyze_map', json={'image_data': image_data})
        assert response.status_code == 400, image_data
        assert error in response.json['error'], image_data
        assert 'map_values' not in response.json


def test_palette_inspection_rejects_images_too_small_for_sampling(web):
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (7, 7)).save(output, format='PNG')
    response = web.app.test_client().post('/api/analyze_map', json={
        'image_data': base64.b64encode(output.getvalue()).decode('ascii')})
    assert response.status_code == 400
    assert 'too small' in response.json['error']


def test_topic_preview_is_explicitly_identified_as_sample_data(web):
    client = web.app.test_client()
    for mode in ('text', 'number'):
        response = client.post('/api/analyze_map', json={'topic': 'Example', 'mode': mode})
        assert response.status_code == 200
        assert response.json['source'] == 'topic_preview'
        assert response.json['analysis']['detected_type'] == mode
        assert response.json['map_values']


def test_generate_displays_upstream_service_reason(web, monkeypatch, generation_request):
    from map.functions import AIServiceError
    client = web.app.test_client()
    for code, phrase in ((503, 'overloaded'), (429, 'quota'), (504, '45 seconds')):
        monkeypatch.setattr(web, 'auto_map', Mock(side_effect=AIServiceError(phrase, code)))
        response = client.post('/api/generate', json=generation_request)
        assert response.status_code == code
        assert response.json['error'] == phrase


def test_analysis_preserves_fractional_values_and_rendered_bounds(web):
    analysis = web._build_analysis_from_ai(
        {'ukraine': 0.1, 'poland': 0.4}, mode='number',
        render_meta={'used_num': {'(0.1, 0.4)': (12, 34, 56, 255)}},
    )
    assert analysis['ui_num_rules'] == [{'low': 0.1, 'high': 0.4, 'hex': '#0c2238', 'alpha': 255}]
    assert analysis['stats']['min_value'] == 0.1
    assert analysis['stats']['max_value'] == 0.4


def test_model_discovery_excludes_non_text_models_and_keeps_new_versions(web, monkeypatch):
    from types import SimpleNamespace
    from google import genai
    names = [
        'gemini-4-hydrogen', 'gemini-3.8-flash', 'gemma-4-31b-it',
        'gemini-3.8-flash-image', 'gemini-live', 'gemini-native-audio',
        'gemini-transcribe', 'gemini-omni-preview', 'gemini-decision',
        'gemini-embedding',
    ]
    listing = [SimpleNamespace(name='models/' + name, supported_actions=['generateContent']) for name in names]
    listing.append(SimpleNamespace(name='models/gemini-non-generative', supported_actions=['embedContent']))
    client = Mock(return_value=SimpleNamespace(models=SimpleNamespace(list=lambda: listing)))
    monkeypatch.setattr(genai, 'Client', client)
    result = web.app.test_client().post('/api/models', json={'api_key': 'test'})
    assert result.status_code == 200
    assert set(result.json['models']) == {'gemini-4-hydrogen', 'gemini-3.8-flash', 'gemma-4-31b-it'}
    assert client.call_args.kwargs['http_options'].timeout == 15000


def test_map_progress_tracks_stages_and_expires_old_entries(web, monkeypatch, generation_request):
    def generate(**kwargs):
        kwargs['progress_callback'](2, 'Painting')
        assert web._MAP_PROGRESS['render-id']['stage'] == 2
        buf = io.BytesIO(b'png')
        buf.map_meta = {'ai_answer': {'a': '1k'}, 'raw_ai_answer': {'a': 1049}}
        return buf
    monkeypatch.setattr(web, 'auto_map', generate)
    web._MAP_PROGRESS['old'] = {'updated_at': 0}
    web._set_map_progress('', 1, 'ignored')
    client = web.app.test_client()
    response = client.post('/api/generate', json={**generation_request, 'progress_id': 'render-id', 'show_legend': True})
    assert response.status_code == 200
    assert response.json['map_values'] == {'a': 1049}
    assert '--legend' in response.json['command']
    assert 'old' not in web._MAP_PROGRESS
    progress = client.get('/api/generate_progress/render-id')
    assert progress.json['status'] == 'done' and progress.json['stage'] == 4
    assert progress.headers['Cache-Control'] == 'no-store'
    assert client.get('/api/generate_progress/unknown').json['stage'] == 1


def test_map_progress_records_generation_failure(web, monkeypatch, generation_request):
    client = web.app.test_client()
    for error, status in ((KeyError('x'), 400), (RuntimeError('failed'), 500), (None, 500)):
        monkeypatch.setattr(web, 'auto_map', Mock(side_effect=error, return_value=None))
        no_progress = client.post('/api/generate', json=generation_request)
        assert no_progress.status_code == status
        assert no_progress.json['status'] == 'error'
        response = client.post('/api/generate', json={**generation_request, 'progress_id': 'failed'})
        assert response.status_code == status
        assert response.json['status'] == 'error'
        assert client.get('/api/generate_progress/failed').json['status'] == 'error'
