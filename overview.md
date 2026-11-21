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

# Maintainer's guide

AutoMap turns a topic into country/region values, paints a PNG map, and optionally
composes that map into a portrait video. Gemini and DeepSeek supply data. The web
application and media worker are separate Flask services; the CLI calls the map
renderer directly. The autonomous agent is a separate application under
`AutoMap-agent/`.

## Start here

1. Read `main.py` and `yt-dlp_serv.py`: they are only launchers.
2. Read `webapp/__init__.py` to see application configuration and registered routes.
3. Follow one request in `webapp/generation.py` into `map/auto_map.py`.
4. For video work, follow `webapp/media.py` into `media/worker.py`.
5. For automated publishing, read `AutoMap-agent/README.md`, then
   `agent/runner.py` and `agent/video_generation.py` inside that directory.

Every Python module begins with its purpose, callers and important contracts.
JavaScript, templates, native code and configuration use equivalent header
comments. Large functions document decisions and resource ownership where the
module description alone is insufficient. This guide connects those local notes.

## Where to change behavior

| Change | Start here | Check alongside it |
| --- | --- | --- |
| Web startup or configuration | `webapp/__init__.py`, `server_runtime.py` | `.env`/Compose forwarding and `tests/test_web_app.py` |
| CLI options | `cli.py` | Shared renderer options and `tests/test_cli.py` |
| Single map, repaint or ideas API | `webapp/generation.py` | `webapp/inputs.py`, template payloads, `tests/test_web.py` |
| AI providers, errors or tool conversations | `map/functions.py` | `map/web_search.py`, DeepSeek/search/idea tests |
| CSV schema or row defaults | `map/batch_ideas.py`, `webapp/batch.py` | CLI batch behavior and `tests/test_batch_csv.py` |
| Country colors, intervals or number labels | `map/styling.py` | `webapp/analysis.py`, styling and renderer tests |
| Region geometry or artwork | `map/geometry.py`, coordinate tables, `resources/maps/` | Coordinate bounds, masks and `tests/test_zoom_reveal.py` |
| Legend or map composition | `map/legend.py`, `map/auto_map.py` | Actual PNG pixels and effective render metadata |
| Worker downloads, audio or render lifecycle | `media/worker.py` | Web proxy, storage cleanup and worker/audio tests |
| Background preparation or static layers | `media/render.py`, `media/native_layers.py` | RGBA pixel/placement tests and local render tests |
| Titles, branding or stickers | `media/text.py`, `media/stickers.py` | Preview/final parity and bundled fonts |
| Camera zoom or country reveal | `media/camera.py`, `media/reveal.py`, `webapp/zoom.py` | Coordinate/mask contracts and frame tests |
| Login or browser sessions | `webapp/auth.py` | Credential rotation and session persistence tests |
| YouTube OAuth or uploads | `webapp/youtube.py`, `static/youtube-upload.js` | Ownership, CSRF, encrypted records and upload tests |
| Browser state/history/material restore | Templates and `static/studio-storage.js` | Async ordering and storage-failure browser tests |
| API Manager | `webapp/pages.py`, `templates/api_manager.html`, `static/api-manager.js` | Shared key names in the map page |
| Agent scheduling or approval commands | `AutoMap-agent/agent/runner.py` | Database claims and integration tests |
| Agent candidate video creation | `AutoMap-agent/agent/video_generation.py` | AutoMapClient payloads and media selection |
| Native acceleration | `automap_rs/src/lib.rs` | Python fallback, byte formats, FFmpeg and wheel build |

`webapp/pages.py` serves HTML and small capability/status responses. It should not
perform rendering. `webapp/media.py` handles the browser/worker HTTP boundary;
media files and processing belong to the worker. Assets are under `resources/`,
frontend files under `templates/` and `static/`, and benchmarks under `tools/` and
`benchmarks/`.

### Frontend layout and script order

`templates/index.html` and `templates/video.html` are page shells. Their sections
live under `templates/map/` and `templates/video/`; nested includes keep controls
inside their original forms. Edit the relevant section rather than duplicating
the complete page. API Manager remains a small standalone template.

The corresponding classic JavaScript controllers and page CSS live under
`static/map/` and `static/video/`. Script order is explicit in the map shell and
`templates/video/scripts.html`: shared storage/state first, feature controllers
next, initialization last. Video's YouTube controller follows initialization.
These scripts share global bindings with existing HTML event handlers; changing
them to async or module scripts requires migrating that contract deliberately.
There is no frontend build step. Server-rendered values stay in HTML/Jinja rather
than being interpolated into static JavaScript.

The agent follows the same pattern in `AutoMap-agent/templates/agent/` and
`AutoMap-agent/static/agent/`. Its Dockerfile explicitly copies static assets.
Root Flask serves assets from `static/`; the root web Dockerfile copies that tree.

## Follow a request

### Generate or repaint a map

The map page posts to `webapp/generation.py`. The application hook in
`webapp/inputs.py` validates the JSON object once and stores it in Flask `g`;
routes use `request_data()`. Provider, key, font and search parsing are shared
with the batch endpoints.

The generation cache may supply an existing answer. Otherwise `map/functions.py`
obtains and parses provider output, with optional bounded searches through
`map/web_search.py`. `map/auto_map.py` selects assets, normalizes region values,
chooses colors, paints regions, draws labels and composes the result. Styling,
geometry and legend details live in their own modules.

For `io_res=True`, the renderer returns a PNG `BytesIO` with optional `map_meta`.
Web routes base64-encode the PNG. Metadata carries raw values and effective styles
so the editor can show what was actually rendered. Disk output uses `result.png`
in the project root. Some failures return `None`; provider failures can raise
`AIServiceError` with a displayable message/status code.

Repaint supplies `ai_answer_override` from existing map values and must skip the
AI call. Visual-only changes can reuse cached AI data. Uploaded-image inspection
samples palette colors; it does not recover the original data behind the image.

### Render and download a video

Video Studio posts settings plus data URLs or multipart files. `webapp/media.py`
normalizes the transport, optionally obtains a country mask from `webapp/zoom.py`,
and forwards to the configured `YTDLP_URL` using the pooled HTTP session.

`media/worker.py` validates material, prepares temporary inputs, normalizes audio,
prepares the background and composes text/map/overlay clips. The optional native
engine and MoviePy paths share effect and layer contracts. The worker owns clip
readers, temporary files, rendered output and expiry cleanup. Downloads are
streamed back through the web application; close upstream responses on success,
error and disconnect.

Video progress lives in `render_progress.py`, separate from map-generation stages.
An optional UUID connects a render POST to polling GETs. Progress percentages must
not regress, and encoding callbacks cannot mark a failed response successful.

### Upload to YouTube

The web YouTube extension binds OAuth credentials and signed render tokens to the
browser/session owner. OAuth configuration and pending state stay server-side;
credential records are encrypted under the web instance directory. Upload threads
retrieve the worker MP4, upload it in resumable chunks and report sanitized job
status. Rendering and uploading are separate outcomes: retain the MP4 download
when upload fails. Jobs are not a durable queue; verify YouTube Studio before
retrying an upload after a server restart.

## Contracts to preserve

- Validate external boundaries: HTTP bodies and nested fields, uploaded files,
  provider responses and subprocess output. Inside an already-normalized flow,
  rely on the established shape rather than repeating dictionary checks.
- Map types are `default`, `flag`, `world` and `world_countries`. Region keys and
  seed coordinates must match the corresponding bundled artwork/coordinate table.
- Numeric filling intervals use `low < value <= high`. Keep fractional/raw values
  for comparisons and extrema; short display labels must not change precision.
- Pillow artwork uses RGBA colors. Reveal masks are grayscale `L`: 0 is outside,
  255 is inside. NumPy/native byte order, channel count and frame dimensions must
  agree; see `media/native_layers.py` and the Rust module documentation.
- Country centers in web payloads are normalized to 0..1. Image dimensions and
  layer placement are pixels. Video timing is seconds. Read helper docstrings
  before mixing normalized coordinates with canvas coordinates.
- Fonts and region coordinates are part of rendering behavior. Replacing/resizing
  assets can invalidate cached indexes, coordinate assumptions and text fitting.
- Template field IDs, event handlers, payload names and storage keys are contracts
  with the feature controllers and routes. Node tests extract marked sections of
  `static/map/form.js`; keep those anchors current. Tests should inspect rendered
  pages or actual static assets rather than assuming an entire page is one file.

## Configuration and state ownership

| State | Owner/location | Persistence |
| --- | --- | --- |
| Login signing secret | `webapp/auth.py`, configured key or web `instance/` | Keep stable to retain sessions and decrypt saved YouTube credentials |
| YouTube credentials/pending state | `webapp/youtube.py`, instance data or configured directory | Files persist; user ownership derives from session identity |
| AI response cache | `webapp/cache.py` | Bounded process-local cache; restart clears it |
| Geometry/artwork indexes | `map/geometry.py` | In-memory artwork plus file-identity-based disk region cache |
| Map/video progress and upload jobs | Web generation, worker tracker, YouTube extension | Process-local; not durable job storage |
| Worker downloads, cuts and renders | `/tmp/automap_storage/` by default | Retained in a Compose volume but subject to worker cleanup |
| Browser preferences/materials/history | localStorage and IndexedDB | Per browser/origin; tolerate disabled storage and asynchronous clears |
| Agent configuration/experiments | Agent SQLite database and media directories | Separate from the root web app; see agent README |

Process environment takes precedence over `.env` for application settings. Compose
must explicitly forward variables into containers; updating `.env` alone does not
change a running container's environment. AI/search browser keys are request-local
and share storage names between the map page and API Manager. Do not log or echo
credentials when handling provider failures.

`docker-compose.yml` publishes the web app on host port 3000 and connects it to the
worker on the internal network; the worker has no published host port there.
Native launch defaults are web port 5000 and worker port 8080. Exact `SERVER=TRUE`
selects Waitress and web authentication; other values use the development server.
The standalone agent has its own configuration and deployment files.

## Develop and validate

Run these commands from the repository root after installing Python dependencies
with `python -m pip install -r requirements-test.txt`. Real media cases require
working local FFmpeg/MoviePy support. Browser cases need Playwright Chromium.

```sh
# Run web and worker in separate terminals, or use docker compose up --build.
python main.py
python yt-dlp_serv.py

# Fast/root backend checks, then browser checks or the complete CI-style suite.
python -m pytest tests --ignore=tests/browser -q
python -m playwright install chromium
python -m pytest tests/browser -q
python -m pytest tests --cov --cov-report=term-missing -q
node --test tests/frontend/*.test.cjs

# The agent has separate requirements and its own Python import root.
python -m pip install -r AutoMap-agent/requirements.txt
PYTHONPATH=AutoMap-agent python -m pytest AutoMap-agent/tests -q
```

Root tests isolate credentials, HTTP and AI clients in `tests/conftest.py`. Patch
dependencies where the implementation uses them, for example
`webapp.generation.auto_map` or `webapp.http_client.worker_http`; do not add runtime
forwarders solely to keep an old test patch path alive. Browser tests serve real
Flask pages with network interception. Local pixel/audio/render tests complement
mocked transport checks. Test-module docstrings explain their boundaries.

Known baseline failures: the agent tests
`test_failed_clean_map_keeps_zoom_without_reveal` and
`test_score_and_suffix_inference` disagree with the existing zoom/score behavior.
These failures were also reproduced on unchanged `HEAD`; the structure cleanup
does not resolve them. The frontend debounce harness now supplies its missing
storage/DOM stubs and tests the extracted form controller.

Runtime package versions are declared in both `requirements.txt` and
`pyproject.toml`; keep them aligned and check MoviePy/Pillow compatibility in a
clean environment when changing either. Rust build metadata lives under
`automap_rs/`. Root web/worker Dockerfiles and the agent Dockerfile have different
COPY contexts: update the relevant one after a module move.

For performance investigation use `tools/bench_map.py` and
`benchmarks/bench_video_render.py`. They use synthetic data rather than live AI;
measurement requires local assets/encoders and is not a fixed timing assertion.

Standalone artwork utilities under `resources/` perform file I/O at import time.
Run them in a disposable asset directory, never import them into a request path.
JSON filling files, coordinate tables and the batch CSV template are input data;
keep them parseable rather than inserting unsupported comment syntax. Their
owning parser/renderer modules document the formats and defaults.
