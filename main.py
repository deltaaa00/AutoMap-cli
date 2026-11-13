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

"""Flask web interface for AutoMap.

Serves the single-page UI in ``templates/index.html`` and exposes a small JSON
API used by that UI. The API key handling is the interesting part: if a ``.env``
file with a real ``GEMINI_API_KEY`` is present, the web UI never asks for a key.
If it is missing, ``/api/status`` reports it and the UI shows a key input, whose
value is forwarded to the generation request only.
"""

import os
import re
import time

from pathlib import Path

# auto_map() loads fonts/maps via *relative* paths ("resources/fonts", ...),
# so the process must run from the AutoMap-cli project directory. Anchor the
# working directory here to make the web API independent of how it is launched.
BASE_DIR = Path(__file__).parent
os.chdir(BASE_DIR)

STORAGE_DIR = "/tmp/automap_storage"
DOWNLOADS = os.path.join(STORAGE_DIR, "downloads")
CUT_DIR = os.path.join(STORAGE_DIR, "cut")

os.makedirs(STORAGE_DIR, exist_ok=True)
os.makedirs(CUT_DIR, exist_ok=True)

from dotenv import dotenv_values
from map.auto_map import auto_map

from flask import Flask, request, jsonify, send_file, render_template, Response

FONTS_DIR = BASE_DIR / "resources" / "fonts"

env = BASE_DIR / ".env"
key = (os.environ.get("GEMINI_API_KEY") or "").strip()
if not key and env.is_file():
    key = (dotenv_values(env).get("GEMINI_API_KEY") or "").strip()
needs_api_key = not key or key.startswith("your_")


def _available_fonts():
    """Return the list of usable font files from resources/fonts.

    Mirrors ``cli._available_fonts`` so the API and the CLI agree on what is
    selectable. Discovery is filename-based: only ``.ttf`` / ``.otf`` files
    (case-insensitive extension) are returned, sorted for stable output.
    """
    if not FONTS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in FONTS_DIR.iterdir() if p.suffix.lower() in (".ttf", ".otf")
    )


app = Flask(__name__)

@app.route('/')
def main():
    return render_template("index.html")


@app.route('/studio')
def studio():
    """Serve the dedicated Video Studio page (music downloader)."""
    return render_template("video.html", title_fonts=_available_fonts())


@app.route('/api/video_title_preview', methods=['POST'])
def video_title_preview():
    import requests

    data = request.get_json(silent=True) or {}
    try:
        response = requests.post(
            'http://yt-dlp-worker:8080/api/video_title_preview',
            json={key: data[key] for key in ('video_title', 'title_font', 'title_font_size',
                                            'title_first_line_color', 'title_other_lines_color') if key in data},
            timeout=15,
        )
    except requests.RequestException:
        return jsonify(error='Title preview is unavailable. Please try again.'), 502
    return Response(response.content, status=response.status_code,
                    content_type=response.headers.get('Content-Type', 'application/json'))

@app.route('/api/fonts/')
@app.route('/api/fonts')
def api_fonts():
    """Expose the fonts discovered in resources/fonts.

    Response:
        { "fonts": ["Bips.ttf", "DIN Condensed.ttf", ...], "count": 4 }
    """
    fonts = _available_fonts()
    return jsonify({"fonts": fonts, "count": len(fonts)})


@app.route('/api/status')
def api_status():
    """Report whether the server already has a usable Gemini API key.

    Response:
        { "needs_api_key": true }  -> the UI must ask the user for a key
    """
    return jsonify({"needs_api_key": needs_api_key})


@app.route('/api/generate/', methods=['POST'])
@app.route('/api/generate', methods=['POST'])
def generate():
    """Generate a map from the JSON body and return it as a data URL.

    Request JSON:
        { "topic", "mode", "map_type", "gsm", "extra_option", "model", "font" }

    Response JSON:
        { "status": "success", "command": "...", "image_url": "data:image/png;base64,..." }
    """
    import base64
    data = request.get_json(silent=True) or {}

    # Optional user-supplied key (used only when .env has none).
    api_key = (data.get('api_key') or '').strip() or None
    if needs_api_key and not api_key:
        return jsonify({"status": "error",
                        "error": "API key required. Provide GEMINI_API_KEY via .env or the web form."}), 400

    # Validate the requested font against what is actually on disk, otherwise
    # auto_map would raise FileNotFoundError when loading the TrueType file.
    font_name = data.get('font')
    if font_name not in _available_fonts():
        font_name = "Bips.ttf"

    extra_option = data.get('extra_option')
    if extra_option in ("none", "", None):
        extra_option = None

    try:
        gsm = int(data.get('gsm', 15))
    except (TypeError, ValueError):
        gsm = 15

    # Rebuild the equivalent CLI command so the UI can show / log it.
    command = (
        f"python cli.py make \"{data.get('topic', '')}\" "
        f"-m {data.get('mode', 'text')} -mp {data.get('map_type', 'default')} "
        f"-gsm {gsm} -eop {data.get('extra_option', 'none')} "
        f"--model {data.get('model', 'gemini-2.5-flash')} --font \"{font_name}\""
    )

    try:
        buffer = auto_map(
            prompt=data['topic'],
            mode=data['mode'],
            map_type=data['map_type'],
            size_mod=gsm,
            optional_feature=extra_option,
            model=data['model'],
            font_name=font_name,
            show_result=False,
            io_res=True,
            api_key=api_key,
        )
    except KeyError as e:
        return jsonify({"status": "error", "command": command,
                        "error": f"Missing field: {e}"}), 400
    except Exception as e:  # noqa: BLE001 - surface failures to the UI
        return jsonify({"status": "error", "command": command,
                        "error": str(e)}), 500

    # auto_map returns None on AI failure / unknown map type.
    if buffer is None:
        return jsonify({"status": "error", "command": command,
                        "error": "Map generation failed. Check the server logs."}), 500

    # Encode the PNG buffer as a data URL the browser can load directly.
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')

    return jsonify({
        "status": "success",
        "command": command,
        "image_url": f"data:image/png;base64,{encoded}",
    })


@app.route('/api/music_download/', methods=['POST'])
@app.route('/api/music_download', methods=['POST'])
def music_download():
    """Download a track via the downstream render/yt-dlp service.

    Request JSON:
        { "url": "https://music.youtube.com/watch?v=..." }

    Response JSON (passthrough from the downstream service):
        { "status": "success", "file": "<base64 mp3>", "title": "<song title>" }
        { "status": "error", "error": "<message>" }
    """
    import requests

    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({"status": "error", "error": "Missing 'url' in request body."}), 400

    # Another service downloads music from YouTube and converts it to mp3.
    try:
        response = requests.post(
            "http://yt-dlp-worker:8080/api/music_download/",
            json={"url": url},
            timeout=40,
        )
    except requests.RequestException as e:
        return jsonify({"status": "error",
                        "error": f"Music service unreachable: {e}"}), 502

    try:
        payload = response.json()
    except ValueError:
        return jsonify({"status": "error",
                        "error": "Music service returned a non-JSON response."}), 502

    # Pass the downstream payload/status straight through.
    return jsonify(payload), response.status_code

@app.route('/api/music_cut', methods=['POST'])
@app.route('/api/music_cut/', methods=['POST'])
def cut_audio() -> tuple[object, int]:
    """Cut a previously downloaded track via the downstream render service.

    Request JSON:
        { "start_time": "00:01:30", "title": "<song title>", "duration": 8 }

    Response JSON (passthrough from the downstream service):
        { "status": "success", "file": "<base64 mp3>", "title": "<song title>" }
        { "status": "error", "error": "<message>" }
    """
    import requests

    data: dict[str, object] = request.get_json(silent=True) or {}
    start_time = str(data.get('start_time') or '').strip()
    title = str(data.get('title') or '').strip()
    if not title:
        return jsonify({"status": "error", "error": "Missing 'title' in request body."}), 400

    raw_duration = data.get('duration', 8)
    try:
        duration = int(raw_duration) if raw_duration is not None else 8
    except (TypeError, ValueError):
        duration = 8
    duration = max(1, min(30, duration))

    # Another service downloads music from YouTube and converts it to mp3.
    try:
        response = requests.post(
            "http://yt-dlp-worker:8080/api/music_cut/",
            json={
                "start_time": start_time,
                "title": title,
                "duration": duration,
            },
            timeout=40,
        )

    except requests.RequestException as e:
        return jsonify({"status": "error",
                        "error": f"Music service unreachable: {e}"}), 502

    try:
        payload = response.json()
    except ValueError:
        return jsonify({"status": "error",
                        "error": "Music service returned a non-JSON response."}), 502

    # Pass the downstream payload/status straight through.
    return jsonify(payload), response.status_code


@app.route('/api/render_video', methods=['POST'])
@app.route('/api/render_video/', methods=['POST'])
def make_video() -> tuple[object, int]:
    """Render a video via the downstream render service.

    Request JSON:
        {
            "map_file": "<base64 / data URL>",
            "background_video": "<base64 / data URL>",
            "video_title": "<title>",
            "codec": "libx264",
            "duration": 8
        }

    Response JSON (passthrough from the downstream service):
        { "status": "success", "file_path": "...", "download_url": "..." }
        { "status": "error", "error": "<message>" }
    """
    import requests

    data: dict[str, object] = request.get_json(silent=True) or {}

    # The frontend sends base64 strings; keep them as-is (default to empty string).
    map_b64 = data.get('map_file') or ''
    background_b64 = data.get('background_video') or ''
    # Frontend field is "video_title"; accept legacy "title" as a fallback.
    title = str(data.get('video_title') or data.get('title') or '').strip()
    codec = str(data.get('codec') or 'libx264').strip()
    duration = data.get('duration')

    try:
        response = requests.post(
            "http://yt-dlp-worker:8080/api/render_video/",
            json={
                "map_file": map_b64,
                "background_video": background_b64,
                "video_title": title,
                "codec": codec,
                "duration": duration,
                "title_font": data.get('title_font', 'DIN Condensed.ttf'),
                "title_font_size": data.get('title_font_size', 100),
                "title_first_line_color": data.get('title_first_line_color', '#ffffff'),
                "title_other_lines_color": data.get('title_other_lines_color', '#a7f3d0'),
            },
            # Video rendering (moviepy/ffmpeg) can take a while - allow more time.
            timeout=300,
        )

    except requests.RequestException as e:
        return jsonify({"status": "error",
                        "error": f"Video service unreachable: {e}"}), 502

    try:
        payload = response.json()
    except ValueError:
        return jsonify({"status": "error",
                        "error": "Video service returned a non-JSON response."}), 502

    # Pass the downstream payload/status straight through.
    return jsonify(payload), response.status_code


@app.route('/api/download_video', methods=['GET'])
@app.route('/api/download_video/', methods=['GET'])
def download_video():
    """Stream a rendered video from the downstream render service.

    The browser hits this endpoint on the web origin, but the rendered .mp4
    lives in the worker container's storage, so we proxy the download to the
    worker and stream its response back as an attachment.
    """
    import requests
    from flask import Response

    raw_path = (request.args.get('path') or '').strip()
    if not raw_path:
        return jsonify({"status": "error", "error": "Missing 'path' query param."}), 400

    try:
        upstream = requests.get(
            "http://yt-dlp-worker:8080/api/download_video",
            params={"path": raw_path},
            stream=True,
            timeout=120,
        )
    except requests.RequestException as e:
        return jsonify({"status": "error",
                        "error": f"Video service unreachable: {e}"}), 502

    # If the worker did not return a file (e.g. 404/400 JSON error), pass it through.
    if upstream.status_code != 200:
        content_type = upstream.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                return jsonify(upstream.json()), upstream.status_code
            except ValueError:
                pass
        return (upstream.text or "Download failed"), upstream.status_code

    disposition = upstream.headers.get(
        "Content-Disposition",
        'attachment; filename="rendered_video.mp4"',
    )

    def _generate():
        # Stream in chunks so large videos don't load fully into memory.
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk

    return Response(
        _generate(),
        mimetype="video/mp4",
        headers={"Content-Disposition": disposition},
    )


if __name__ == "__main__":

    app.run(host='0.0.0.0',debug=True)
