import base64
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, Mock

import pytest
import requests


@pytest.mark.parametrize('html,expected', [
    ('<meta property="og:title" content="My song"><title>Other</title>', 'My song'),
    ('<title>Fallback song</title>', 'Fallback song'), ('<html></html>', None),
])
def test_song_title_metadata(worker, monkeypatch, html, expected):
    monkeypatch.setattr(requests, 'get', Mock(return_value=Mock(text=html)))
    assert worker._get_song_title('https://example.test') == expected


def test_song_title_network_error(worker, monkeypatch):
    monkeypatch.setattr(requests, 'get', Mock(side_effect=requests.Timeout()))
    assert worker._get_song_title('https://example.test') is None


def test_cleanup_removes_only_expired_files(worker, monkeypatch):
    folder = Path(worker.STORAGE_DIR) / 'nested'
    folder.mkdir()
    old, new = folder / 'old.mp4', folder / 'new.mp4'
    old.write_bytes(b'old'); new.write_bytes(b'new')
    os.utime(old, (100, 100)); os.utime(new, (9999, 9999))
    monkeypatch.setattr(worker.time, 'time', lambda: 10000)
    worker.cleanup_old_files(1800)
    assert not old.exists() and new.read_bytes() == b'new'


def test_cleanup_tolerates_racing_deletion(worker, monkeypatch):
    path = Path(worker.STORAGE_DIR) / 'old'
    path.touch()
    os.utime(path, (0, 0))
    monkeypatch.setattr(worker.os, 'remove', Mock(side_effect=FileNotFoundError()))
    worker.cleanup_old_files(1)


@pytest.mark.parametrize('title', ['A nice / song!', None])
def test_download_stores_mp3_and_cleans_temporary_files(worker, monkeypatch, tmp_path, title):
    download = tmp_path / 'temporary-download'
    download.mkdir()
    monkeypatch.setattr(worker.tempfile, 'mkdtemp', lambda **kwargs: str(download))
    run = Mock(side_effect=lambda *args, **kwargs: (download / 'track.mp3').write_bytes(b'mp3-data'))
    monkeypatch.setattr(worker.subprocess, 'run', run)
    monkeypatch.setattr(worker, '_get_song_title', lambda url: title)
    monkeypatch.setattr(worker.uuid, 'uuid4', lambda: 'fallback-id')
    response = worker.app.test_client().post('/api/music_download/', json={'url': ' https://example.test/song '})
    assert response.status_code == 200, response.json
    expected_title = 'A_nice___song_' if title else 'fallback-id'
    assert response.json['title'] == expected_title
    assert base64.b64decode(response.json['file']) == b'mp3-data'
    assert (Path(worker.DOWNLOADS) / f'{expected_title}.mp3').read_bytes() == b'mp3-data'
    assert not download.exists()
    command = run.call_args.args[0]
    assert command[0] == 'yt-dlp' and '--no-playlist' in command
    assert command[-1] == 'https://example.test/song'


@pytest.mark.parametrize('failure', [
    FileNotFoundError(), subprocess.CalledProcessError(1, 'yt-dlp', stderr=b'failed download'),
    RuntimeError('unexpected'), None,
])
def test_download_errors_cleanup(worker, monkeypatch, tmp_path, failure):
    folder = tmp_path / 'temporary-download'
    folder.mkdir()
    monkeypatch.setattr(worker.tempfile, 'mkdtemp', lambda **kwargs: str(folder))
    monkeypatch.setattr(worker.subprocess, 'run', Mock(side_effect=failure))
    response = worker.app.test_client().post('/api/music_download', json={'url': 'https://example.test'})
    assert response.status_code == 500 and response.json['status'] == 'error'
    assert not folder.exists()


@pytest.mark.parametrize('payload', [{}, {'url': ''}, {'url': '  '}])
def test_download_requires_url(worker, payload):
    assert worker.app.test_client().post('/api/music_download', json=payload).status_code == 400


@pytest.mark.parametrize('raw,expected', [(None, 8), ('invalid', 8), (0, 1), (100, 30), (12, 12)])
def test_audio_cut_slices_correct_milliseconds(worker, monkeypatch, raw, expected):
    from pydub import AudioSegment

    audio = MagicMock()
    cut = audio.__getitem__.return_value
    cut.export.side_effect = lambda path, **kwargs: Path(path).write_bytes(b'cut-audio')
    read = Mock(return_value=audio)
    monkeypatch.setattr(AudioSegment, 'from_file', read)
    response = worker.app.test_client().post('/api/music_cut', json={
        'title': 'test-song', 'start_time': '01:30', 'duration': raw,
    })
    assert response.status_code == 200
    assert base64.b64decode(response.json['file']) == b'cut-audio'
    audio.__getitem__.assert_called_once_with(slice(90000, 90000 + expected * 1000))
    assert read.call_args.args[0] == os.path.join(worker.DOWNLOADS, 'test-song.mp3')
    assert cut.export.call_args.kwargs == {'format': 'mp3', 'bitrate': '192k'}


@pytest.mark.parametrize('title', ['../secret', 'a/b', '', 'a.mp3', 123])
def test_audio_cut_rejects_unsafe_titles(worker, title):
    response = worker.app.test_client().post('/api/music_cut', json={'title': title, 'start_time': '00:00'})
    assert response.status_code == 400


def test_audio_cut_read_failure(worker, monkeypatch):
    from pydub import AudioSegment
    monkeypatch.setattr(AudioSegment, 'from_file', Mock(side_effect=FileNotFoundError('missing track')))
    response = worker.app.test_client().post('/api/music_cut', json={'title': 'song', 'start_time': '00:00'})
    assert response.status_code == 500 and 'missing track' in response.json['error']


@pytest.mark.parametrize('changes', [
    {'map_file': ''}, {'background_video': ''}, {'video_title': ''},
    {'video_title': 'a' * 1001}, {'codec': 'unsupported'},
    {'map_file': 'data:image/png;base64,'}, {'map_file': 'a'},
    {'background_video': '!!!'},
])
def test_render_validation(worker, changes):
    data = dict(map_file='eA==', background_video='eA==', video_title='test')
    data.update(changes)
    response = worker.app.test_client().post('/api/render_video', json=data)
    assert response.status_code == 400 and response.json['status'] == 'error'


@pytest.mark.parametrize('field', ['map_file', 'background_video', 'watermark_image'])
def test_render_material_limit(worker, monkeypatch, field):
    monkeypatch.setattr(worker, 'MAX_MATERIAL_BYTES', 3)
    data = dict(map_file='eA==', background_video='eA==', video_title='test')
    data[field] = base64.b64encode(b'1234').decode()
    response = worker.app.test_client().post('/api/render_video', json=data)
    assert response.status_code == 400 and '10 MB' in response.json['error']


@pytest.mark.parametrize('settings', [
    {'watermark_text': 'x' * 81}, {'watermark_text': 123},
    {'watermark_color': '#xyzxyz'}, {'watermark_color': None},
    {'watermark_font': '../../etc/passwd'}, {'watermark_font': 'missing.ttf'},
    {'watermark_image': 'not-an-image'},
    {'watermark_image': base64.b64encode(b'not an image').decode()},
])
def test_invalid_watermark_rejected_before_render(worker, settings):
    data = dict(map_file='eA==', background_video='eA==', video_title='test', **settings)
    response = worker.app.test_client().post('/api/render_video', json=data)
    assert response.status_code == 400, response.json


def test_failed_render_cleans_material_files(worker, monkeypatch, tmp_path):
    real_tempfile = worker.tempfile.NamedTemporaryFile
    created = []
    def temporary(**kwargs):
        file = real_tempfile(dir=tmp_path, **kwargs)
        created.append(file.name)
        return file
    monkeypatch.setattr(worker.tempfile, 'NamedTemporaryFile', temporary)
    response = worker.app.test_client().post('/api/render_video', json={
        'map_file': 'data:image/png;base64,eA==', 'background_video': 'eA==', 'video_title': 'test',
    })
    assert response.status_code == 500
    assert len(created) == 2 and all(not Path(path).exists() for path in created)


@pytest.mark.parametrize('title', ['', 'a' * 1001])
def test_preview_title_validation(worker, title):
    assert worker.app.test_client().post('/api/video_title_preview', json={'video_title': title}).status_code == 400


def test_download_video_stream(worker):
    path = Path(worker.RENDER_DIR) / 'video.mp4'
    path.write_bytes(b'video-content')
    response = worker.app.test_client().get('/api/download_video', query_string={'path': str(path)})
    assert response.status_code == 200 and response.data == b'video-content'
    assert response.mimetype == 'video/mp4'
    assert 'video.mp4' in response.headers['Content-Disposition']
    response.close()


@pytest.mark.parametrize('kind,status', [('empty', 400), ('missing', 404), ('outside', 400), ('sibling', 400), ('symlink', 400)])
def test_download_path_validation(worker, tmp_path, kind, status):
    outside = tmp_path / 'secret.mp4'
    outside.write_bytes(b'private')
    paths = {'empty': '', 'missing': str(Path(worker.RENDER_DIR) / 'missing.mp4'),
             'outside': str(outside), 'sibling': worker.RENDER_DIR + '-other/secret.mp4'}
    if kind == 'symlink':
        link = Path(worker.RENDER_DIR) / 'link.mp4'
        link.symlink_to(outside)
        paths[kind] = str(link)
    response = worker.app.test_client().get('/api/download_video/', query_string={'path': paths[kind]})
    assert response.status_code == status


def test_preview_rejects_unfittable_multiline_title(worker):
    response = worker.app.test_client().post('/api/video_title_preview', json={
        'video_title': '\n'.join(['Title'] * 80),
    })
    assert response.status_code == 400
    assert 'too long' in response.json['error']
def test_codec_probe_checks_actual_encoder_and_caches_result(worker, monkeypatch):
    run = Mock(side_effect=[Mock(returncode=0), Mock(returncode=1),
                            subprocess.TimeoutExpired('ffmpeg', 5)])
    monkeypatch.setattr(worker.subprocess, 'run', run)
    assert worker._available_codecs() == ('libx264',)
    assert worker._available_codecs() == ('libx264',)
    assert run.call_count == 3
    assert all('-frames:v' in call.args[0] for call in run.call_args_list)
    assert worker.app.test_client().get('/api/codecs').json == {'codecs': ['libx264']}


def test_render_rejects_unavailable_hardware_codec(worker, monkeypatch):
    monkeypatch.setattr(worker, '_available_codecs', lambda: ('libx264',))
    response = worker.app.test_client().post('/api/render_video', json={
        'map_file': 'eA==', 'background_video': 'eA==', 'video_title': 'Test', 'codec': 'h264_nvenc',
    })
    assert response.status_code == 400
    assert 'unavailable' in response.json['error']
