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

"""Regressions found while checking long-lived server deployments."""
import base64
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('value', [123, ['https://example.test'], '--exec=bad',
                                  'file:///etc/passwd', 'https://[bad', 'https:///missing-host'])
def test_download_rejects_non_http_urls_before_running_process(worker, monkeypatch, value):
    run = Mock()
    monkeypatch.setattr(worker.subprocess, 'run', run)
    response = worker.app.test_client().post('/api/music_download', json={'url': value})
    assert response.status_code == 400
    run.assert_not_called()


def test_download_timeout_releases_temporary_storage(worker, monkeypatch, tmp_path):
    folder = tmp_path / 'download'
    folder.mkdir()
    monkeypatch.setattr(worker.tempfile, 'mkdtemp', lambda **_: str(folder))
    run = Mock(side_effect=subprocess.TimeoutExpired('yt-dlp', 120))
    monkeypatch.setattr(worker.subprocess, 'run', run)
    response = worker.app.test_client().post('/api/music_download', json={'url': 'https://example.test/song'})
    assert response.status_code == 504
    assert not folder.exists()
    assert run.call_args.kwargs['timeout'] == 120
    assert run.call_args.args[0][-2:] == ['--', 'https://example.test/song']


@pytest.mark.parametrize('title', ['a' * 1000, 'Найкрасивіші країни Європи ' * 20, '😀' * 1000])
def test_long_titles_fit_download_and_render_filenames(worker, monkeypatch, tmp_path, title):
    folder = tmp_path / 'download'
    folder.mkdir()
    monkeypatch.setattr(worker.tempfile, 'mkdtemp', lambda **_: str(folder))
    monkeypatch.setattr(worker.subprocess, 'run', Mock(
        side_effect=lambda *args, **kwargs: (folder / 'track.mp3').write_bytes(b'mp3')))
    monkeypatch.setattr(worker, '_get_song_title', lambda _: title)
    response = worker.app.test_client().post('/api/music_download', json={'url': 'https://example.test/song'})
    assert response.status_code == 200, response.json
    assert base64.b64decode(response.json['file']) == b'mp3'
    assert (Path(worker.DOWNLOADS) / (response.json['title'] + '.mp3')).exists()
    # The exact same stem is used for output filenames in make_video.
    rendered = Path(worker.RENDER_DIR) / (worker._filename_stem(title) + '_0123456789ab_final.mp4')
    rendered.write_bytes(b'video')
    assert rendered.is_file()


def test_render_requests_remove_expired_output_without_requiring_music_download(worker, monkeypatch):
    import os
    # Mirror production: the general worker fixture keeps these folders as siblings.
    render_dir = Path(worker.STORAGE_DIR) / 'render'
    render_dir.mkdir()
    monkeypatch.setattr(worker, 'RENDER_DIR', str(render_dir))
    output = render_dir / 'old.mp4'
    output.write_bytes(b'expired')
    os.utime(output, (0, 0))
    assert worker.app.test_client().post('/api/render_video', json={}).status_code == 400
    assert not output.exists()


def test_download_accepts_storage_relative_filename(worker):
    output = Path(worker.RENDER_DIR) / 'video.mp4'
    output.write_bytes(b'video')
    response = worker.app.test_client().get('/api/download_video', query_string={'path': 'video.mp4'})
    assert response.status_code == 200
    assert response.data == b'video'
    response.close()


@pytest.mark.parametrize('titles', [
    ('Same title', 'Same title'),
    ('Same / title', 'Same ? title'),
    ('a' * 200 + 'first', 'a' * 200 + 'second'),
])
def test_download_identifiers_preserve_tracks_and_cuts_on_title_collisions(worker, monkeypatch, titles):
    tracks = iter((b'first-track', b'second-track'))
    temporary_folders = []

    def download(command, **kwargs):
        output = Path(command[command.index('-o') + 1].replace('%(ext)s', 'mp3'))
        temporary_folders.append(output.parent)
        output.write_bytes(next(tracks))

    monkeypatch.setattr(worker.subprocess, 'run', Mock(side_effect=download))
    monkeypatch.setattr(worker, '_get_song_title', Mock(side_effect=titles))
    client = worker.app.test_client()
    first = client.post('/api/music_download', json={'url': 'https://example.test/first'})
    assert first.status_code == 200, first.json
    first_title = first.json['title']
    first_cut = Path(worker.CUT_DIR) / f'{first_title}_cut.mp3'
    first_cut.write_bytes(b'first-cut')

    second = client.post('/api/music_download', json={'url': 'https://example.test/second'})
    assert second.status_code == 200, second.json
    second_title = second.json['title']
    assert first_title != second_title
    assert (Path(worker.DOWNLOADS) / f'{first_title}.mp3').read_bytes() == b'first-track'
    assert (Path(worker.DOWNLOADS) / f'{second_title}.mp3').read_bytes() == b'second-track'
    assert first_cut.read_bytes() == b'first-cut'
    assert not (Path(worker.CUT_DIR) / f'{second_title}_cut.mp3').exists()
    assert all(not folder.exists() for folder in temporary_folders)


@pytest.mark.parametrize('size', [3, 4])
def test_download_size_limit_matches_audio_upload_and_releases_temporary_files(worker, monkeypatch, size):
    monkeypatch.setattr(worker, 'MAX_MATERIAL_BYTES', 3)
    temporary_folders = []

    def download(command, **kwargs):
        output = Path(command[command.index('-o') + 1].replace('%(ext)s', 'mp3'))
        temporary_folders.append(output.parent)
        output.write_bytes(b'x' * size)

    monkeypatch.setattr(worker.subprocess, 'run', Mock(side_effect=download))
    title_lookup = Mock(return_value='Track')
    monkeypatch.setattr(worker, '_get_song_title', title_lookup)
    response = worker.app.test_client().post('/api/music_download', json={'url': 'https://example.test/song'})
    if size == 3:
        assert response.status_code == 200, response.json
        assert worker._decode_audio_payload(response.json['file']) == b'xxx'
        assert len(list(Path(worker.DOWNLOADS).iterdir())) == 1
    else:
        assert response.status_code == 400, response.json
        assert response.json['status'] == 'error'
        assert '10 MB limit' in response.json['error']
        assert 'shorter track' in response.json['error']
        assert 'file' not in response.json
        assert not list(Path(worker.DOWNLOADS).iterdir())
        title_lookup.assert_not_called()
    assert all(not folder.exists() for folder in temporary_folders)
