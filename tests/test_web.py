import base64
import io
from unittest.mock import Mock

import pytest
import requests


@pytest.mark.parametrize('path', ['/', '/studio', '/api/status', '/api/fonts', '/api/fonts/'])
def test_pages_and_discovery(web, path):
    response = web.app.test_client().get(path)
    assert response.status_code == 200
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
        font_name='DIN Condensed.ttf', show_result=False, io_res=True, api_key='form-key')
    assert 'form-key' not in response.json['command']


@pytest.mark.parametrize('gsm', [None, 'bad', 15])
@pytest.mark.parametrize('extra', ['none', '', None])
def test_generate_defaults_and_invalid_font_fallback(web, monkeypatch, generation_request, gsm, extra):
    generate = Mock(return_value=io.BytesIO(b'png'))
    monkeypatch.setattr(web, 'auto_map', generate)
    response = web.app.test_client().post('/api/generate/', json={**generation_request,
        'font': '../bad.ttf', 'gsm': gsm, 'extra_option': extra})
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


@pytest.mark.parametrize('failure,status', [(KeyError('topic'), 400), (RuntimeError('broken'), 500), (None, 500)])
def test_generation_errors(web, monkeypatch, generation_request, failure, status):
    generate = Mock(side_effect=failure) if failure else Mock(return_value=None)
    monkeypatch.setattr(web, 'auto_map', generate)
    response = web.app.test_client().post('/api/generate', json=generation_request)
    assert response.status_code == status
    assert response.json['status'] == 'error'


PROXIES = [
    ('/api/music_download', {'url': 'https://example.test/song'}, 40),
    ('/api/music_cut', {'title': 'song', 'start_time': '00:01'}, 40),
    ('/api/render_video', {'map_file': 'map', 'background_video': 'bg', 'video_title': 'title'}, 300),
]


@pytest.mark.parametrize('path,payload,timeout', PROXIES)
@pytest.mark.parametrize('status', [200, 400, 500])
def test_json_proxy_passes_response(web, monkeypatch, path, payload, timeout, status):
    upstream = Mock(status_code=status)
    upstream.json.return_value = {'status': 'success' if status == 200 else 'error', 'file': 'data'}
    post = Mock(return_value=upstream)
    monkeypatch.setattr(requests, 'post', post)
    response = web.app.test_client().post(path, json=payload)
    assert response.status_code == status and response.json == upstream.json.return_value
    assert post.call_args.kwargs['timeout'] == timeout
    if path.endswith('render_video'):
        assert post.call_args.kwargs['json']['title_font'] == 'DIN Condensed.ttf'
        assert post.call_args.kwargs['json']['title_font_size'] == 100
        assert post.call_args.kwargs['json']['include_audio'] is False


@pytest.mark.parametrize('path,payload,timeout', PROXIES)
@pytest.mark.parametrize('failure', ['network', 'json'])
def test_proxy_failure_returns_bad_gateway(web, monkeypatch, path, payload, timeout, failure):
    upstream = Mock()
    upstream.json.side_effect = ValueError('not json')
    post = Mock(side_effect=requests.ConnectionError('offline')) if failure == 'network' else Mock(return_value=upstream)
    monkeypatch.setattr(requests, 'post', post)
    assert web.app.test_client().post(path, json=payload).status_code == 502


@pytest.mark.parametrize('path', ['/api/music_download', '/api/music_cut', '/api/download_video'])
def test_missing_proxy_fields(web, path):
    client = web.app.test_client()
    response = client.get(path) if 'download_video' in path else client.post(path, json={})
    assert response.status_code == 400


@pytest.mark.parametrize('raw,expected', [(0, 1), (100, 30), ('bad', 8), (None, 8), ('12', 12)])
def test_cut_duration_normalization(web, monkeypatch, raw, expected):
    post = Mock(return_value=Mock(status_code=200, json=lambda: {}))
    monkeypatch.setattr(requests, 'post', post)
    web.app.test_client().post('/api/music_cut', json={'title': 'song', 'duration': raw})
    assert post.call_args.kwargs['json']['duration'] == expected


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


@pytest.mark.parametrize('status', [200, 400])
def test_title_preview_proxy_preserves_content(web, monkeypatch, status):
    upstream = Mock(status_code=status, content=b'png-or-error', headers={'Content-Type': 'image/png'})
    post = Mock(return_value=upstream)
    monkeypatch.setattr(requests, 'post', post)
    response = web.app.test_client().post('/api/video_title_preview', json={
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


@pytest.mark.parametrize('kind', ['network', 'json', 'text', 'invalid-json'])
def test_video_download_errors(web, monkeypatch, kind):
    upstream = Mock(status_code=404, headers={'Content-Type': 'application/json' if 'json' in kind else 'text/plain'}, text='missing')
    upstream.json.return_value = {'error': 'missing'}
    if kind == 'invalid-json':
        upstream.json.side_effect = ValueError()
    get = Mock(side_effect=requests.ConnectionError()) if kind == 'network' else Mock(return_value=upstream)
    monkeypatch.setattr(requests, 'get', get)
    response = web.app.test_client().get('/api/download_video?path=clip.mp4')
    assert response.status_code == (502 if kind == 'network' else 404)
def test_codec_proxy_returns_worker_capabilities(web, monkeypatch):
    monkeypatch.setattr(requests, 'get', Mock(return_value=Mock(
        status_code=200, json=lambda: {'codecs': ['libx264']})))
    assert web.app.test_client().get('/api/codecs').json == {'codecs': ['libx264']}


@pytest.mark.parametrize('failure', [requests.Timeout(), ValueError('bad JSON')])
def test_codec_proxy_failure(web, monkeypatch, failure):
    response = Mock()
    response.json.side_effect = failure
    monkeypatch.setattr(requests, 'get', Mock(return_value=response))
    assert web.app.test_client().get('/api/codecs').status_code == 502


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

    # Invalid base64/image must return 400 error instead of sample data
    bad_res = client.post('/api/analyze_map', json={
        'image_data': 'data:image/png;base64,invalid',
        'topic': 'Any',
        'prefer_cached': False,
    })
    assert bad_res.status_code == 400
    assert bad_res.json['status'] == 'error'

    # Valid PNG image is inspected for its actual pixel colors
    img = Image.new('RGBA', (220, 180), (20, 180, 90, 255))
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


@pytest.mark.parametrize('theme', ['auto', 'viridis', 'heatmap', 'emerald_gold', 'festive', 'oceanic', 'categorical'])
def test_analyze_map_supports_every_ui_palette_theme(web, theme):
    client = web.app.test_client()
    res = client.post('/api/analyze_map', json={
        'topic': 'Population',
        'mode': 'number',
        'palette_theme': theme,
        'map_values': {'germany': 84, 'france': 68, 'estonia': 1},
    })
    assert res.status_code == 200
    expected = 'viridis' if theme == 'auto' else theme
    assert res.json['analysis']['resolved_theme'] == expected


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


@pytest.mark.parametrize('failure', [None, RuntimeError('repaint failed')])
def test_restyle_reports_renderer_failure(web, monkeypatch, failure):
    generate = Mock(side_effect=failure) if failure else Mock(return_value=None)
    monkeypatch.setattr(web, 'auto_map', generate)
    response = web.app.test_client().post('/api/restyle_map', json={'map_values': {'ukraine': 'Yes'}})
    assert response.status_code == 500
    assert response.json['status'] == 'error'
    assert 'image_url' not in response.json


@pytest.mark.parametrize('image_data,error', [
    ('', 'Empty image data'),
    ('data:text/plain;base64,YQ==', 'Unsupported data URL media type'),
    ('data:image/png;base64,', 'Decoded image payload is empty'),
    ('data:image/png;base64,YQ==', 'not a valid image'),
])
def test_palette_inspection_rejects_invalid_uploads(web, image_data, error):
    response = web.app.test_client().post('/api/analyze_map', json={'image_data': image_data})
    assert response.status_code == 400
    assert error in response.json['error']
    assert 'map_values' not in response.json


def test_palette_inspection_rejects_images_too_small_for_sampling(web):
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (7, 7)).save(output, format='PNG')
    response = web.app.test_client().post('/api/analyze_map', json={
        'image_data': base64.b64encode(output.getvalue()).decode('ascii')})
    assert response.status_code == 400
    assert 'too small' in response.json['error']


@pytest.mark.parametrize('mode', ['text', 'number'])
def test_topic_preview_is_explicitly_identified_as_sample_data(web, mode):
    response = web.app.test_client().post('/api/analyze_map', json={'topic': 'Example', 'mode': mode})
    assert response.status_code == 200
    assert response.json['source'] == 'topic_preview'
    assert response.json['analysis']['detected_type'] == mode
    assert response.json['map_values']


