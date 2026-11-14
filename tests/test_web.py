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

