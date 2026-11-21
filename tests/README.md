<!--
Copyright 2025-2026 deltaaa00

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Tests

Use Python 3.12, matching CI. From the project root:

`requirements.txt` contains runtime dependencies. `requirements-test.txt`
includes that list plus pytest, coverage, Playwright, and dependency validation tools.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-test.txt
python -m playwright install chromium
python -m pytest --cov --cov-report=term-missing
```

On Linux, use `python -m playwright install --with-deps chromium` to install
browser system dependencies too. Browser tests use installed Google Chrome on
macOS when available; otherwise they use Playwright's Chromium.

For Python tests without launching a browser:

```sh
python -m pytest -m 'not browser' --cov --cov-report=term-missing
```

For browser interaction checks only:

```sh
python -m pytest -m browser
```

Coverage includes branch coverage and all project Python modules, including
the two image preparation scripts under `resources`. The minimum is 85%.
GitHub Actions runs the complete suite and uploads `coverage.xml`.

## Scope and isolation

- AI: response parsing, failed requests, model/key forwarding, filling config.
- Maps: real Pillow filling/compositing on small images, numeric range edges,
  text colors, optional features, output files, bundled fonts/coordinates.
- CLI: help, defaults, options, invalid arguments, generation failures.
- Web: pages, generation, API-key behavior, all service proxies, streaming,
  timeouts and invalid upstream responses.
- Worker: metadata/downloads, audio slices, expiry cleanup, validation, safe
  downloads, title fitting, real FFmpeg portrait rendering and audio attachment.
  Audio checks cover song titles independent of video headlines and inline cut
  fragments after worker files are lost.
- Browser: font/size/reset controls, exact title PNGs, debounce, responsive
  preview scaling, map replacement/removal and blob URL cleanup; automatic map
  transfer between studios, persistent settings and uploaded materials, large-map
  preparation for video, and graceful handling of unavailable browser storage.
- Image preparation tools: temporary input/output images only.

Tests mock external AI, HTTP and yt-dlp downloads. An autouse fixture rejects
unmocked Requests calls and AI clients. No real credentials are needed. Audio
slicing mocks pydub's file backend; actual video/audio encoding uses the FFmpeg
binary provided by imageio-ffmpeg (installed with MoviePy), without GPU hardware.

Browser HTTP traffic is routed to Flask test clients. External CDN scripts,
styles and fonts are stubbed, so browser tests verify behavior rather than
visual appearance of the external Tailwind stylesheet. Live Gemini/YouTube
availability and NVIDIA/VideoToolbox encoders are not covered.

Page shells include sections from `templates/map/` and `templates/video/` and
load ordered feature scripts from `static/map/` and `static/video/`.
`test_templates.py` checks rendered includes and local asset delivery. Source-level
checks should read the relevant controller or a rendered page. The Node preview
tests read `static/map/form.js`; they no longer slice JavaScript out of HTML.

Tests use temporary folders and leave repository maps and user outputs intact.
