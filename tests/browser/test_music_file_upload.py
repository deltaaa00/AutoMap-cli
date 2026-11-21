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

"""Check uploaded music selection, restoration and render payloads in Chromium.

Uses the real Video Studio page through the shared studio fixture. Cases cover
file type/size errors, upload failures and retaining the previous selection.
Actual audio decoding is tested by test_audio_upload rather than a live server.
"""
import base64

import pytest
from PIL import Image

from test_video_studio import studio, reopen_studio, render_payload

pytestmark = pytest.mark.browser


def test_uploaded_music_restores_cuts_and_is_sent_to_render(studio, tmp_path):
    page, _ = studio
    page.evaluate('async () => await window.videoStudioReady')
    uploads = []
    def upload(route):
        uploads.append(route.request.post_data_json)
        route.fulfill(json={'status':'success','title':'Uploaded_track_abc','file':base64.b64encode(b'normalized-mp3').decode()})
    page.route('**/api/music_upload', upload)
    page.locator('#musicFileInput').set_input_files({'name':'My song.wav','mimeType':'audio/wav','buffer':b'wav-source'})
    page.wait_for_function("document.getElementById('musicUploadStatus').textContent.startsWith('Ready:')")
    assert uploads[0]['filename'] == 'My song.wav'
    assert base64.b64decode(uploads[0]['audio_file'].split(',')[1]) == b'wav-source'
    assert page.locator('#musicPreview').is_visible()
    reopen_studio(page)
    assert page.evaluate('musicAudioFile.size') == len(b'normalized-mp3')
    assert page.locator('#musicResult').is_visible()
    assert 'Uploaded_track_abc' in page.locator('#renderAudioStatus').inner_text()
    cuts = []
    def cut(route):
        cuts.append(route.request.post_data_json)
        route.fulfill(json={'status':'successfull','file':base64.b64encode(b'cut-mp3').decode()})
    page.route('**/api/music_cut', cut)
    page.locator('#cutStartInput').fill('00:00')
    page.evaluate('handleMusicCut({preventDefault(){}})')
    assert base64.b64decode(cuts[0]['audio_file'].split(',')[1]) == b'normalized-mp3'
    image = tmp_path/'map.png'
    Image.new('RGB',(80,80),'green').save(image)
    page.locator('#mapFileInput').set_input_files(str(image))
    page.locator('#bgFileInput').set_input_files({'name':'background.mp4','mimeType':'video/mp4','buffer':b'video'})
    page.locator('#videoTitleInput').fill('Uploaded music video')
    renders = []
    def render(route):
        renders.append(render_payload(route.request))
        route.fulfill(status=400,json={'error':'Captured'})
    page.route('**/api/render_video',render)
    page.evaluate('handleRenderVideo({preventDefault(){}})')
    assert renders[0]['include_audio'] is True
    assert base64.b64decode(renders[0]['audio_file'].split(',')[1]) == b'cut-mp3'
    # A replacement removes the previous cut and uses the full new track.
    page.locator('#musicFileInput').set_input_files({'name':'Other.mp3','mimeType':'audio/mpeg','buffer':b'other'})
    page.wait_for_function("!document.getElementById('musicFileInput').disabled")
    assert page.evaluate('cutAudioFile') is None
    page.evaluate('handleRenderVideo({preventDefault(){}})')
    assert base64.b64decode(renders[-1]['audio_file'].split(',')[1]) == b'normalized-mp3'


def test_upload_rejects_wrong_type_and_oversized_files(studio):
    page, _ = studio
    page.evaluate('async () => await window.videoStudioReady')
    page.locator('#musicFileInput').set_input_files({'name':'notes.txt','mimeType':'text/plain','buffer':b'notes'})
    assert 'supported audio' in page.locator('#musicUploadStatus').inner_text()
    page.evaluate("handleMusicUpload(new File([new Uint8Array(15*1024*1024+1)], 'large.mp3', {type:'audio/mpeg'}))")
    assert 'up to 15 MB' in page.locator('#musicUploadStatus').inner_text()
    assert page.locator('#musicFileInput').is_enabled()


def test_failed_upload_keeps_previous_music(studio):
    page, _ = studio
    page.evaluate('async () => await window.videoStudioReady')
    page.evaluate("saveMusicTitle('existing-song'); updateRenderAudioStatus()")
    page.route('**/api/music_upload',lambda route:route.fulfill(status=400,json={'error':'Invalid audio file.'}))
    page.locator('#musicFileInput').set_input_files({'name':'broken.mp3','mimeType':'audio/mpeg','buffer':b'bad'})
    page.wait_for_function("!document.getElementById('musicFileInput').disabled")
    assert 'Invalid audio file.' in page.locator('#musicUploadStatus').inner_text()
    assert page.evaluate('loadMusicTitle()') == 'existing-song'
