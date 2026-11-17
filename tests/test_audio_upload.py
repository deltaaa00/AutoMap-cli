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

"""Real audio uploads and browser-restored sources without external downloads."""
import base64
from functools import lru_cache
import io
from pathlib import Path
import re
import subprocess
from unittest.mock import Mock
import wave

import pytest


@lru_cache(maxsize=4)
def wav_bytes(seconds=0.3):
    import math
    import struct
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b''.join(struct.pack('<h', int(12000 * math.sin(i * 2 * math.pi * 440 / 8000)))
                                   for i in range(int(8000 * seconds))))
    return output.getvalue()


def payload(raw, filename='My music.wav'):
    return {'audio_file': 'data:audio/wav;base64,' + base64.b64encode(raw).decode(), 'filename': filename}


def test_real_wav_upload_is_normalized_to_mp3_with_unique_safe_title(worker):
    from moviepy import AudioFileClip
    client = worker.app.test_client()
    raw_wav = wav_bytes(0.3)
    responses = [client.post('/api/music_upload', json=payload(raw_wav, '../../Моя музика.wav'))
                 for _ in range(2)]
    titles = []
    for response in responses:
        assert response.status_code == 200, response.json
        assert response.json['status'] == 'success'
        title = response.json['title']
        assert re.fullmatch(r'[\w-]+', title)
        raw = base64.b64decode(response.json['file'])
        path = Path(worker.DOWNLOADS) / f'{title}.mp3'
        assert path.read_bytes() == raw
        assert not raw.startswith(b'RIFF')
        with AudioFileClip(str(path)) as audio:
            assert audio.duration == pytest.approx(0.3, abs=.15)
        titles.append(title)
    assert titles[0] != titles[1]


def test_upload_rejects_empty_or_invalid_base64(worker, monkeypatch):
    convert = Mock()
    monkeypatch.setattr(worker, '_convert_uploaded_audio', convert)
    client = worker.app.test_client()
    for value in (None, '', 'data:audio/mp3;base64,', '!', 123, 'data:audio/wav,not-base64', 'eA==!'):
        response = client.post('/api/music_upload', json={'audio_file': value})
        assert response.status_code == 400, value
    convert.assert_not_called()
    assert not list(Path(worker.DOWNLOADS).iterdir())


def test_upload_rejects_oversize_audio_before_conversion(worker, monkeypatch):
    monkeypatch.setattr(worker, 'MAX_MATERIAL_BYTES', 3)
    convert = Mock()
    monkeypatch.setattr(worker, '_convert_uploaded_audio', convert)
    response = worker.app.test_client().post('/api/music_upload', json=payload(b'1234'))
    assert response.status_code == 400
    assert '10 MB' in response.json['error']
    convert.assert_not_called()


def test_upload_rejects_file_without_actual_audio(worker):
    response = worker.app.test_client().post('/api/music_upload', json=payload(b'not audio'))
    assert response.status_code == 400
    assert 'decoded' in response.json['error']
    assert not list(Path(worker.DOWNLOADS).iterdir())


def test_upload_conversion_failures_are_bounded_and_do_not_save_file(worker, monkeypatch):
    client = worker.app.test_client()
    for error, status in ((subprocess.TimeoutExpired('ffmpeg', 30), 504),
                          (FileNotFoundError('ffmpeg unavailable'), 503)):
        monkeypatch.setattr(worker.subprocess, 'run', Mock(side_effect=error))
        response = client.post('/api/music_upload', json=payload(wav_bytes()))
        assert response.status_code == status
        assert not list(Path(worker.DOWNLOADS).iterdir())


def test_converted_audio_limit_and_subprocess_timeout(worker, monkeypatch):
    monkeypatch.setattr(worker, 'MAX_MATERIAL_BYTES', 8)
    def conversion(command, **kwargs):
        assert kwargs['timeout'] == 30
        assert command[command.index('-protocol_whitelist') + 1] == 'file,pipe'
        Path(command[-1]).write_bytes(b'x' * 9)
        return Mock(returncode=0)
    monkeypatch.setattr(worker.subprocess, 'run', conversion)
    with pytest.raises(ValueError, match='Converted audio exceeds'):
        worker._convert_uploaded_audio(b'input')


def test_restored_mp3_source_can_be_cut_after_server_source_disappears(worker):
    from moviepy import AudioFileClip
    client = worker.app.test_client()
    uploaded = client.post('/api/music_upload', json=payload(wav_bytes(2.2))).json
    title = uploaded['title']
    (Path(worker.DOWNLOADS) / f'{title}.mp3').unlink()
    response = client.post('/api/music_cut', json={
        'title': title, 'audio_file': uploaded['file'], 'start_time': '00:01', 'duration': 1,
    })
    assert response.status_code == 200, response.json
    path = Path(worker.CUT_DIR) / f'{title}_cut.mp3'
    assert path.read_bytes() == base64.b64decode(response.json['file'])
    with AudioFileClip(str(path)) as cut:
        assert cut.duration == pytest.approx(1, abs=.15)


def test_inline_cut_rejects_start_beyond_audio(worker):
    response = worker.app.test_client().post('/api/music_cut', json={
        'title': 'restored', 'audio_file': base64.b64encode(wav_bytes()).decode(),
        'start_time': '01:00', 'duration': 1,
    })
    assert response.status_code == 400, response.json
    assert not list(Path(worker.CUT_DIR).iterdir())


def test_inline_cut_requires_valid_source(worker):
    client = worker.app.test_client()
    for raw in ('', 'bad!'):
        response = client.post('/api/music_cut', json={
            'title': 'restored', 'audio_file': raw, 'start_time': '00:00',
        })
        assert response.status_code == 400, raw


def test_inline_cut_rejects_oversize_source(worker, monkeypatch):
    monkeypatch.setattr(worker, 'MAX_MATERIAL_BYTES', 3)
    response = worker.app.test_client().post('/api/music_cut', json={
        'title': 'restored', 'audio_file': base64.b64encode(b'1234').decode(), 'start_time': '00:00',
    })
    assert response.status_code == 400
    assert '10 MB' in response.json['error']


def test_inline_cut_timeout_returns_actionable_error(worker, monkeypatch):
    monkeypatch.setattr(worker.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('ffmpeg', 30)))
    response = worker.app.test_client().post('/api/music_cut', json={
        'title': 'restored', 'audio_file': base64.b64encode(wav_bytes()).decode(), 'start_time': '00:00',
    })
    assert response.status_code == 504
    assert not list(Path(worker.CUT_DIR).iterdir())


def test_upload_invalid_filename_and_json_shape(worker):
    client = worker.app.test_client()
    assert client.post('/api/music_upload', json=['bad']).status_code == 400
    assert client.post('/api/music_upload', json={**payload(wav_bytes()), 'filename': 123}).status_code == 400
