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

"""Cookies remain parseable across file import, browser storage and both APIs."""
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from test_video_studio import studio, reopen_studio

pytestmark = pytest.mark.browser


def test_cookie_file_reaches_ytdlp_without_losing_empty_values(studio, worker, monkeypatch):
    page, _ = studio
    page.evaluate('async () => await window.videoStudioReady')
    # An export without a header, Windows newlines, BOM, HttpOnly and empty value.
    rows = '#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfake-test-value\r\n.youtube.com\tTRUE\t/\tFALSE\t0\tEMPTY\t\r\n'
    page.locator('#musicCookiesFileInput').set_input_files({
        'name': 'cookies.txt', 'mimeType': 'text/plain', 'buffer': ('\ufeff' + rows).encode(),
    })
    page.wait_for_function("document.getElementById('musicCookiesFileStatus').textContent.startsWith('Loaded cookies.txt')")
    normalized = '# Netscape HTTP Cookie File\n' + rows.replace('\r\n', '\n')
    assert page.locator('#musicCookiesInput').input_value() == normalized
    reopen_studio(page)
    assert page.locator('#musicCookiesInput').input_value() == normalized
    received = []

    def download(command, **kwargs):
        cookie_path = command[command.index('--cookies') + 1]
        jar = MozillaCookieJar(cookie_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        received.append({cookie.name: cookie.value for cookie in jar})
        output = command[command.index('-o') + 1].replace('%(ext)s', 'mp3')
        Path(output).write_bytes(b'test-mp3')
        return CompletedProcess(command, 0, stdout=b'"Test song"')

    monkeypatch.setattr(worker.subprocess, 'run', download)
    page.locator('#musicUrlInput').fill('https://www.youtube.com/watch?v=videoId0001')
    page.evaluate('handleMusicDownload({preventDefault(){}})')
    page.wait_for_function("!document.getElementById('musicBtn').disabled")
    assert received == [{'SID': 'fake-test-value', 'EMPTY': ''}]
    assert page.locator('#studioMetaBadge').inner_text().startswith('Done')


def test_invalid_cookie_file_keeps_previous_cookies(studio):
    page, _ = studio
    page.evaluate('async () => await window.videoStudioReady')
    page.locator('summary').filter(has_text='YouTube Cookies').click()
    page.locator('#musicCookiesInput').fill('previous cookies')
    page.locator('#musicCookiesFileInput').set_input_files({
        'name': 'cookies.txt', 'mimeType': 'text/plain', 'buffer': b'{"cookies": []}',
    })
    page.wait_for_function("document.getElementById('musicCookiesFileStatus').textContent.startsWith('Please export')")
    assert page.locator('#musicCookiesInput').input_value() == 'previous cookies'
    assert page.locator('#musicBtn').is_enabled()
