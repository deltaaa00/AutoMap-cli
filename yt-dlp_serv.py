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

import base64
import importlib
import io
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from functools import lru_cache
from urllib.parse import urlencode, urlsplit

from flask import Flask, request, jsonify, send_file
from render_progress import RenderProgress

app = Flask(__name__)
render_progress = RenderProgress()


@app.get('/api/render_progress/<uuid:render_id>')
def get_render_progress(render_id):
    """Return current progress for ``render_id`` with ``no-store`` so polling never reads stale cache."""
    result = render_progress.snapshot(str(render_id))
    response = jsonify(result if result else {'error': 'Render not found.'})
    response.headers['Cache-Control'] = 'no-store'
    return response, 200 if result else 404

STORAGE_DIR = "/tmp/automap_storage"
DOWNLOADS = os.path.join(STORAGE_DIR, "downloads")
CUT_DIR = os.path.join(STORAGE_DIR, "cut")
RENDER_DIR = os.path.join(STORAGE_DIR, "render")

os.makedirs(STORAGE_DIR, exist_ok=True)
os.makedirs(DOWNLOADS, exist_ok=True)
os.makedirs(CUT_DIR, exist_ok=True)
os.makedirs(RENDER_DIR, exist_ok=True)

# Incoming materials (map image, background video) must be <= 10 MB each.
MAX_MATERIAL_BYTES = 10 * 1024 * 1024

# Allowed ffmpeg codec names (see the UI dropdown).
#   libx264            - universal software encoder
#   h264_nvenc         - NVIDIA GPUs
#   h264_videotoolbox  - Apple Silicon chips
ALLOWED_CODECS = ("libx264", "h264_nvenc", "h264_videotoolbox")
_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"')
_HTML_TITLE_RE = re.compile(r'<title>(.*?)</title>')
_SAFE_TITLE_RE = re.compile(r'[^\w\-]')
_AUDIO_TITLE_RE = re.compile(r'[\w\-]+')


def _filename_stem(title):
    """Leave room for suffixes within common 255-byte filesystem limits."""
    safe = _SAFE_TITLE_RE.sub('_', title)
    return safe.encode('utf-8')[:180].decode('utf-8', errors='ignore') or 'untitled'


def _decode_audio_payload(value):
    """Decode browser audio without allocating an unbounded decoded buffer."""
    if not isinstance(value, str) or not value:
        raise ValueError('Audio file is required.')
    if value.startswith('data:'):
        header, separator, value = value.partition(',')
        if not separator or not header.endswith(';base64'):
            raise ValueError('Audio file must be base64 encoded.')
    if len(value) > 4 * ((MAX_MATERIAL_BYTES + 2) // 3):
        raise ValueError('Audio file exceeds the 10 MB limit.')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise ValueError('Audio file is not valid base64.') from None
    if not raw:
        raise ValueError('Audio file is empty.')
    if len(raw) > MAX_MATERIAL_BYTES:
        raise ValueError('Audio file exceeds the 10 MB limit.')
    return raw


def _convert_uploaded_audio(raw, start=None, duration=None):
    """Verify audio and normalize it with the same FFmpeg binary as video rendering."""
    from moviepy.config import FFMPEG_BINARY

    with tempfile.TemporaryDirectory(prefix='automap_audio_') as folder:
        source = os.path.join(folder, 'source')
        output = os.path.join(folder, 'normalized.mp3')
        with open(source, 'wb') as stream:
            stream.write(raw)
        command = [FFMPEG_BINARY, '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-protocol_whitelist', 'file,pipe']
        if start is not None:
            command += ['-ss', str(start)]
        command += ['-i', source, '-map', '0:a:0', '-vn']
        if duration is not None:
            command += ['-t', str(duration)]
        command += ['-ac', '2', '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '192k',
                    '-write_xing', '0', '-id3v2_version', '0',
                    '-fs', str(MAX_MATERIAL_BYTES + 1), output]
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        if result.returncode != 0 or not os.path.isfile(output):
            raise ValueError('Audio file could not be decoded. Choose a valid audio file.')
        with open(output, 'rb') as stream:
            normalized = stream.read(MAX_MATERIAL_BYTES + 1)
        if not normalized:
            raise ValueError('Audio contains no playable samples at the selected start time.')
        if len(normalized) > MAX_MATERIAL_BYTES:
            raise ValueError('Converted audio exceeds the 10 MB limit. Choose a shorter track.')
        return normalized


@app.route('/api/music_upload', methods=['POST'])
@app.route('/api/music_upload/', methods=['POST'])
def music_upload():
    """Normalize uploaded audio and return the same contract as music_download."""
    cleanup_old_files()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(status='error', error='Expected an audio upload object.'), 400
    filename = data.get('filename') or 'uploaded_audio'
    if not isinstance(filename, str):
        return jsonify(status='error', error='Invalid audio filename.'), 400
    try:
        raw = _decode_audio_payload(data.get('audio_file'))
        normalized = _convert_uploaded_audio(raw)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400
    except subprocess.TimeoutExpired:
        return jsonify(status='error', error='Audio conversion timed out. Choose a shorter track.'), 504
    except OSError:
        return jsonify(status='error', error='Audio conversion is unavailable.'), 503
    original = os.path.splitext(os.path.basename(filename.replace('\\', '/')))[0]
    title = f'{_filename_stem(original)}_{uuid.uuid4().hex[:12]}'
    try:
        os.makedirs(DOWNLOADS, exist_ok=True)
        with open(os.path.join(DOWNLOADS, f'{title}.mp3'), 'xb') as stream:
            stream.write(normalized)
    except OSError:
        return jsonify(status='error', error='Audio storage is unavailable. Please try again.'), 503
    return jsonify(status='success', file=base64.b64encode(normalized).decode('ascii'), title=title)


@lru_cache(maxsize=1)
def _available_codecs():
    """Probe the encoder and device together, using the same FFmpeg as MoviePy."""
    from moviepy.config import FFMPEG_BINARY

    available = []
    for codec in ALLOWED_CODECS:
        try:
            result = subprocess.run([
                FFMPEG_BINARY, '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'color=size=64x64:rate=1',
                '-frames:v', '1', '-pix_fmt', 'yuv420p', '-c:v', codec,
                '-f', 'null', '-',
            ], capture_output=True, timeout=5, check=False)
            if result.returncode == 0:
                available.append(codec)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return tuple(available)


@app.route('/api/codecs')
def api_codecs():
    return jsonify(codecs=list(_available_codecs()))

def cleanup_old_files(max_age_seconds=1800):
    """Delete old files"""
    # Use one cutoff reference for the entire scan of downloads, cuts, and renders.
    now = time.time()
    for root, _dirs, files in os.walk(STORAGE_DIR):
        for name in files:
            item_path = os.path.join(root, name)
            try:
                if now - os.path.getmtime(item_path) > max_age_seconds:
                    os.remove(item_path)
            except FileNotFoundError:
                pass  # Another request may have removed or renamed the file.

def _get_song_title(url: str) -> str | None:
    """Try to extract the song title from the given URL."""
    import requests
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        match = _OG_TITLE_RE.search(response.text)
        if match:
            return match.group(1)

        # Fall back to the page title when Open Graph metadata is unavailable.
        match_title = _HTML_TITLE_RE.search(response.text)
        if match_title:
            return match_title.group(1)

    except requests.RequestException:
        return None


@app.route('/api/music_download/', methods=['POST'])
@app.route('/api/music_download', methods=['POST'])
def music_download():
    cleanup_old_files()  # Clean up old files before processing the new request
    """Download a video's audio, convert it to mp3, and return it as base64.

    Request JSON:
        { "url": "https://music.youtube.com/watch?v=..." }

    Response JSON (success):
        { "status": "success", "file": "<base64 mp3>", "title": "<song title>" }

    Response JSON (error):
        { "status": "error", "error": "<message>" }
    """
    data = request.get_json(silent=True) or {}
    raw_url = data.get('url') or ''
    if not isinstance(raw_url, str):
        return jsonify(status='error', error='URL must be an HTTP or HTTPS address.'), 400
    url = raw_url.strip()
    if not url:
        return jsonify({"status": "error", "error": "Missing 'url' in request body."}), 400
    try:
        parsed_url = urlsplit(url)
        valid_url = parsed_url.scheme in ('http', 'https') and bool(parsed_url.hostname)
    except ValueError:
        valid_url = False
    if not valid_url:
        return jsonify(status='error', error='URL must be an HTTP or HTTPS address.'), 400

    # yt-dlp with `-x` writes to a temp file, so pipe through the temp dir and
    # read the produced .mp3 back. `-o -` is unreliable when extracting audio.

    cookies = str(data.get('cookies') or '').strip('\r\n')
    download_dir = tempfile.mkdtemp(prefix="automap_download_")
    cookie_args = []
    if cookies:
        cookie_path = os.path.join(download_dir, "cookies.txt")
        with open(cookie_path, "w", encoding="utf-8") as cf:
            cf.write(cookies + "\n")
        cookie_args = ["--cookies", cookie_path]
    command = [
        "yt-dlp",
        "--remote-components", "ejs:github",
        *cookie_args,
        "-f", "ba[ext=m4a]/ba",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", os.path.join(download_dir, "track.%(ext)s"),
        "--",
        url,
    ]

    try:
        subprocess.run(command, capture_output=True, check=True, timeout=120)

        # Find the produced mp3 inside the temp dir.
        mp3_path = None
        for root, _dirs, files in os.walk(download_dir):
            for name in files:
                if name.lower().endswith(".mp3"):
                    mp3_path = os.path.join(root, name)
                    break
            if mp3_path:
                break

        if not mp3_path:
            return jsonify({"status": "error",
                            "error": "yt-dlp ERROR: No mp3 file produced."}), 500

        if os.path.getsize(mp3_path) > MAX_MATERIAL_BYTES:
            return jsonify(status='error', error='Downloaded audio exceeds the 10 MB limit. Choose a shorter track.'), 400
        with open(mp3_path, "rb") as fh:
            audio = fh.read(MAX_MATERIAL_BYTES + 1)
        if len(audio) > MAX_MATERIAL_BYTES:
            return jsonify(status='error', error='Downloaded audio exceeds the 10 MB limit. Choose a shorter track.'), 400

        # Page titles are display labels, not unique track identifiers. Keep each
        # download independent from previous tracks and their saved cuts.
        title = f"{_filename_stem(_get_song_title(url) or 'downloaded_audio')}_{uuid.uuid4().hex[:12]}"
        base64_audio = base64.b64encode(audio).decode("utf-8")
        new_mp3_path = os.path.join(DOWNLOADS, f"{title}.mp3")
        # Temporary downloads and persistent storage may be separate mounts.
        # shutil.move falls back to copying when an atomic rename is unavailable.
        shutil.move(mp3_path, new_mp3_path)

        return jsonify({
            "status": "success",
            "file": base64_audio,
            "title": title,
        }), 200

    except subprocess.TimeoutExpired:
        return jsonify(status='error', error='Audio download timed out. Please try again.'), 504

    except subprocess.CalledProcessError as e:
        error_message = (e.stderr or b'').decode("utf-8", errors="ignore").strip()
        if 'cookies are no longer valid' in error_message:
            error_message = ('YouTube rejected the account cookies because the session is no longer valid. '
                             'Export fresh youtube.com cookies from a new private/incognito session, '
                             'close that window, then upload the new cookies.txt file.')
        elif 'Sign in to confirm' in error_message and 'bot' in error_message:
            error_message = ('YouTube requires a sign-in/bot check for this request. '
                             'Upload fresh YouTube account cookies. If they are already fresh, '
                             'YouTube may also be challenging the server connection; '
                             'a correctly formatted cookies file alone cannot guarantee access.')
        return jsonify({"status": "error",
                        "error": f"yt-dlp ERROR: {error_message}"}), 500

    except FileNotFoundError:
        return jsonify({"status": "error",
                        "error": "yt-dlp not found. Please ensure yt-dlp is installed."}), 500

    except Exception as e:  # noqa: BLE001 - surface any failure to the client
        return jsonify({"status": "error", "error": f"Unexpected error: {str(e)}"}), 500
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        

@app.route('/api/music_cut/', methods=['POST'])
@app.route('/api/music_cut', methods=['POST'])
def cut_audio() -> tuple[object, int]:
    """Cut a stored mp3 into a short clip and return it as base64.

    Reads a previously downloaded track from ``DOWNLOADS`` (by title), slices
    it starting at ``start_time`` for ``duration`` seconds (1..30, default 8),
    and writes the clip to ``CUT_DIR``.

    Request JSON:
        { "start_time": "MM:SS", "title": "<song title>", "duration": 8 }

    Response JSON (success):
        { "status": "successfull", "file": "<base64 mp3 clip>" }

    Response JSON (error):
        { "status": "error", "error": "<message>" }
    """
    data: dict[str, object] = request.get_json(silent=True) or {}

    try:
        time = str(data['start_time'])
        filename = data['title']
        if (not isinstance(filename, str) or not re.fullmatch(r'[\w\-]+', filename)
                or len(filename.encode('utf-8')) > 220):
            return jsonify({"status": "error", "error": "Invalid track title."}), 400

        raw_duration = data.get('duration', 8)
        try:
            duration = int(raw_duration) if raw_duration is not None else 8
        except (TypeError, ValueError):
            duration = 8
        duration = max(1, min(30, duration))

        os.makedirs(CUT_DIR, exist_ok=True)

        if not re.fullmatch(r'\d+:[0-5]\d', time):
            return jsonify(status='error', error='Start time must use MM:SS format.'), 400
        minutes, seconds = time.split(":")
        minutes, seconds = int(minutes), int(seconds)

        # Converting to ms
        start_ms = (minutes * 60 + seconds) * 1000

        # Audio clip duration (1..30 seconds, default 8 seconds)
        end_ms = start_ms + (duration * 1000)

        if data.get('audio_file') is not None:
            try:
                raw = _decode_audio_payload(data['audio_file'])
                normalized = _convert_uploaded_audio(raw, start=start_ms / 1000, duration=duration)
            except ValueError as error:
                return jsonify(status='error', error=str(error)), 400
            except subprocess.TimeoutExpired:
                return jsonify(status='error', error='Audio conversion timed out.'), 504
            except OSError:
                return jsonify(status='error', error='Audio conversion is unavailable.'), 503
            with open(os.path.join(CUT_DIR, f'{filename}_cut.mp3'), 'wb') as stream:
                stream.write(normalized)
            return jsonify(status='successfull', file=base64.b64encode(normalized).decode('ascii')), 200

        from pydub import AudioSegment

        audio = AudioSegment.from_file(os.path.join(DOWNLOADS, f"{filename}.mp3"))
        if start_ms >= len(audio):
            return jsonify(status='error', error='Start time is beyond the end of the track.'), 400

        cut = audio[start_ms:end_ms]

        cut.export(
            f"{CUT_DIR}/{filename}_cut.mp3",
            format="mp3",
            bitrate="192k",
        )

        # Encoding in base64
        with open(f"{CUT_DIR}/{filename}_cut.mp3", 'rb') as file:
            cut_base64 = base64.b64encode(file.read()).decode('utf-8')

        return jsonify({
            "status": "successfull",
            "file": cut_base64
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

from video_text import (
    TITLE_FONT_PATH,
    _build_title_clip,
    _round_watermark,
    _title_colors,
    _title_image,
    _title_settings,
    _watermark_font_path,
    _watermark_settings,
    _with_shadow,
)
from video_stickers import (
    ALLOWED_STICKERS,
    TWEMOJI_CODEPOINTS,
    _parse_sticker_spec,
    _resolve_auto_stickers,
    _stars_row_image,
    _sticker_image,
    _subscribe_badge_image,
)
from video_render import (
    _apply_clip_fade_in,
    _flatten_static_layers,
    _prepare_background,
    _render_threads,
    _x264_preset,
)


def _load_automap_rs():
    """Import ``automap_rs``, bypassing any local source directory shadowing."""
    if "automap_rs" in sys.modules and sys.modules["automap_rs"] is None:
        raise ImportError("automap_rs is disabled")

    mod = sys.modules.get("automap_rs")
    if mod is None:
        mod = importlib.import_module("automap_rs")
    if hasattr(mod, "render_video_rs"):
        return mod

    # When CWD is the repo root, ./automap_rs/ can shadow site-packages/automap_rs.abi3.so.
    repo_root = os.path.dirname(os.path.abspath(__file__))
    orig_path = list(sys.path)
    try:
        sys.path = [p for p in orig_path if os.path.abspath(p or os.getcwd()) != repo_root]
        if "automap_rs" in sys.modules and sys.modules["automap_rs"] is not None:
            if not hasattr(sys.modules["automap_rs"], "render_video_rs"):
                sys.modules.pop("automap_rs", None)
        mod = importlib.import_module("automap_rs")
    finally:
        sys.path = orig_path

    if not hasattr(mod, "render_video_rs"):
        raise ImportError("automap_rs native extension not built")
    return mod


@app.route('/api/video_title_preview', methods=['POST'])
def video_title_preview():
    from PIL import Image

    data = request.get_json(silent=True) or {}
    preview_kind = str(data.get('preview_kind') or 'title').strip()
    title = str(data.get('video_title') or '').strip()
    if preview_kind in ('title', 'watermark') and (not title or len(title) > 1000):
        return jsonify(error='Enter a title of up to 1000 characters.'), 400
    try:
        font_path, font_size = _title_settings(data)
        first_color, other_color = _title_colors(data)
        effects = data.get('visual_effects', True) is not False
        title_uppercase = data.get('title_uppercase', True) is not False
        if preview_kind == 'watermark':
            text, color = _watermark_settings(data)
            image = _title_image(text, 936, _watermark_font_path(data), 96,
                                 color, color, effects, max_height=110)
        elif preview_kind == 'subscribe':
            sub_text = str(data.get('subscribe_text') or 'Subscribe!').strip()[:40] or 'Subscribe!'
            image = _subscribe_badge_image(sub_text, font_path, effects)
        elif preview_kind == 'stars':
            _, color = _watermark_settings(data)
            image = _stars_row_image(color, effects)
        elif preview_kind == 'sticker':
            raw_sticker = str(data.get('sticker_kind') or 'santa').strip().lower()
            side = str(data.get('sticker_side') or 'left').strip().lower()
            gen_style, base_kind = _parse_sticker_spec(raw_sticker, title=title, side=side)
            sticker_kind = f'gen_{gen_style}:{base_kind}' if gen_style else base_kind
            image = _sticker_image(
                sticker_kind,
                size=140,
                effects=effects,
                allow_remote=bool(data.get('allow_remote_stickers', True)),
            )
            if image is None:
                return jsonify(error='Unknown sticker kind.'), 400
        else:
            image = _title_image(title, 1040, font_path, font_size, first_color,
                                 other_color, effects, uppercase=title_uppercase)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    output = io.BytesIO()
    Image.fromarray(image).save(output, format='PNG')
    output.seek(0)
    return send_file(output, mimetype='image/png')


def _parse_finite_float(value: object, default: float) -> float:
    """Parse a finite float from JSON input, returning ``default`` on missing or invalid values."""
    if value is None or value == '' or isinstance(value, bool):
        return default
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _decode_reveal_mask(raw_mask: object, box_w: int, box_h: int):
    """Decode a base64 or data-URL L-mode PNG mask resized to ``(box_w, box_h)``, or return None."""
    if not isinstance(raw_mask, str) or not raw_mask.strip():
        return None
    import numpy as np
    from PIL import Image, UnidentifiedImageError

    value = raw_mask.strip()
    if value.startswith('data:'):
        header, sep, value = value.partition(',')
        if not sep or ';base64' not in header.lower():
            return None
    b64_part = ''.join(value.split())
    if not b64_part or len(b64_part) > 4 * ((MAX_MATERIAL_BYTES + 2) // 3):
        return None
    try:
        raw_bytes = base64.b64decode(b64_part, validate=True)
    except (ValueError, TypeError):
        return None
    if not raw_bytes or len(raw_bytes) > MAX_MATERIAL_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(raw_bytes)) as mask_img:
            if mask_img.width * mask_img.height > 20_000_000:
                return None
            mask_resized = mask_img.convert('L').resize(
                (box_w, box_h),
                Image.Resampling.BILINEAR,
            )
            mask_arr = np.array(mask_resized, dtype=np.float32) / 255.0
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        return None
    if not np.any(mask_arr > 0.0):
        return None
    return mask_arr[..., None]


def _parse_reveal_color(raw_color: object) -> tuple[int, int, int]:
    """Parse a hex color string (e.g. ``#000000``) into an RGB tuple, defaulting to black."""
    if not isinstance(raw_color, str):
        return (0, 0, 0)
    s = raw_color.strip().lstrip('#')
    if len(s) == 3 and all(c in '0123456789abcdefABCDEF' for c in s):
        s = ''.join(c * 2 for c in s)
    if len(s) == 6 and all(c in '0123456789abcdefABCDEF' for c in s):
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    return (0, 0, 0)


def _render_reveal_dot_overlays(
    box_w: int,
    box_h: int,
    cx: float,
    cy: float,
    cover_rgb: tuple[int, int, int] = (0, 0, 0),
) -> dict[int, tuple[int, int, int, int, object]]:
    """Pre-render 1-, 2-, and 3-dot RGBA overlays centered at ``(cx, cy)`` using 4x supersampling."""
    import numpy as np
    from PIL import Image, ImageDraw

    base_dim = min(box_w, box_h)
    dot_r = max(6.0, min(10.5, base_dim * 0.011))
    outline_w = max(2.0, dot_r * 0.34)
    outer_r = dot_r + outline_w
    spacing = dot_r * 3.0

    pad = int(math.ceil(outer_r)) + 4
    x0 = max(0, int(math.floor(cx - spacing - pad)))
    x1 = min(box_w, int(math.ceil(cx + spacing + pad)))
    y0 = max(0, int(math.floor(cy - pad)))
    y1 = min(box_h, int(math.ceil(cy + pad)))
    patch_w = max(1, x1 - x0)
    patch_h = max(1, y1 - y0)

    lum = 0.299 * cover_rgb[0] + 0.587 * cover_rgb[1] + 0.114 * cover_rgb[2]
    dot_fill = (15, 23, 42, 255) if lum > 175 else (255, 255, 255, 255)
    ring_fill = (255, 255, 255, 255) if lum > 175 else (10, 12, 20, 255)

    ss = 4
    overlays: dict[int, tuple[int, int, int, int, object]] = {}
    for count in (1, 2, 3):
        hi_img = Image.new('RGBA', (patch_w * ss, patch_h * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(hi_img)
        for idx in range(count):
            dot_x = (cx + (idx - 1) * spacing - x0) * ss
            dot_y = (cy - y0) * ss
            ro = outer_r * ss
            ri = dot_r * ss
            draw.ellipse(
                (dot_x - ro, dot_y - ro, dot_x + ro, dot_y + ro),
                fill=ring_fill,
            )
            draw.ellipse(
                (dot_x - ri, dot_y - ri, dot_x + ri, dot_y + ri),
                fill=dot_fill,
            )
        patch = hi_img.resize((patch_w, patch_h), Image.Resampling.LANCZOS)
        overlays[count] = (y0, y1, x0, x1, np.array(patch, dtype=np.uint8))
    return overlays


def _alpha_composite_patch(base_rgba, y0: int, y1: int, x0: int, x1: int, patch_rgba):
    """Return a copy of ``base_rgba`` with ``patch_rgba`` alpha-composited into ``[y0:y1, x0:x1]``."""
    import numpy as np

    out = base_rgba.copy()
    bg = out[y0:y1, x0:x1].astype(np.float32)
    fg = patch_rgba.astype(np.float32)
    fg_a = fg[:, :, 3:4] / 255.0
    bg_a = bg[:, :, 3:4] / 255.0
    out_a = fg_a + bg_a * (1.0 - fg_a)
    safe_a = np.maximum(out_a, 1e-6)
    out_rgb = (fg[:, :, :3] * fg_a + bg[:, :, :3] * bg_a * (1.0 - fg_a)) / safe_a
    out[y0:y1, x0:x1, :3] = np.clip(np.rint(out_rgb), 0, 255).astype(np.uint8)
    out[y0:y1, x0:x1, 3:4] = np.clip(np.rint(out_a * 255.0), 0, 255).astype(np.uint8)
    return out


def _compute_reveal_time(
    zoom_enabled: bool,
    zoom_start: float,
    zoom_duration: float,
    reveal_time_raw: object,
) -> float:
    """Resolve the effective reveal trigger timestamp (after zoom ends when zoom is enabled)."""
    z_start = zoom_start if zoom_enabled else 1.5
    zoom_end = (z_start + zoom_duration) if zoom_enabled else 0.0
    default_reveal = (zoom_end + 0.8) if zoom_enabled else 2.5
    raw_r_time = _parse_finite_float(reveal_time_raw, default_reveal)
    if zoom_enabled:
        return max(zoom_end + 0.5, raw_r_time)
    return max(0.2, raw_r_time)


def _apply_reveal_impact_fx(clip, reveal_time: float, canvas_w: int, canvas_h: int):
    """Apply a brief camera-shake + RGB chromatic glitch + impact punch when dots vanish."""
    import numpy as np
    from PIL import Image

    impact_dur = 0.60

    def _impact_frame(get_frame, t: float) -> np.ndarray:
        frame = get_frame(t)
        dt = float(t) - reveal_time
        if dt < 0.0 or dt >= impact_dur:
            return frame

        env = math.exp(-dt * 5.8) * max(0.0, 1.0 - dt / impact_dur)
        punch = 1.0 + 0.042 * env
        inv_p = 1.0 / punch
        cw = canvas_w * inv_p
        ch = canvas_h * inv_p
        margin_x = (canvas_w - cw) * 0.5
        margin_y = (canvas_h - ch) * 0.5
        shake_x = env * 22.0 * math.sin(dt * 85.0) + env * 8.0 * math.cos(dt * 145.0)
        shake_y = env * 18.0 * math.cos(dt * 78.0) - env * 6.0 * math.sin(dt * 130.0)
        x0 = max(0.0, min(canvas_w - cw, margin_x + shake_x))
        y0 = max(0.0, min(canvas_h - ch, margin_y + shake_y))

        punched = Image.fromarray(frame).transform(
            (canvas_w, canvas_h),
            Image.Transform.AFFINE,
            (inv_p, 0.0, x0, 0.0, inv_p, y0),
            resample=Image.Resampling.BILINEAR,
        )
        out = np.array(punched, dtype=np.uint8, copy=True)

        if dt < 0.38:
            glitch_env = math.exp(-dt * 7.5)
            ca_shift = int(round(10.0 * glitch_env * (1.0 if math.sin(dt * 105.0) >= 0 else -1.0)))
            if ca_shift > 0 and ca_shift < canvas_w:
                src = out.copy()
                out[:, ca_shift:, 0] = src[:, :-ca_shift, 0]
                out[:, :-ca_shift, 2] = src[:, ca_shift:, 2]
            elif ca_shift < 0 and (-ca_shift) < canvas_w:
                s = -ca_shift
                src = out.copy()
                out[:, :-s, 0] = src[:, s:, 0]
                out[:, s:, 2] = src[:, :-s, 2]

            if glitch_env > 0.20:
                step = int(dt * 50.0)
                band_h = max(8, canvas_h // 65)
                dx = max(4, int(round(18.0 * glitch_env)))
                for b_idx, frac in enumerate((0.38, 0.56)):
                    by0 = min(
                        canvas_h - band_h,
                        max(0, int(canvas_h * frac) + ((step * 53 + b_idx * 97) % 140) - 70),
                    )
                    by1 = min(canvas_h, by0 + band_h)
                    shift_px = dx if ((step + b_idx) % 2 == 0) else -dx
                    out[by0:by1] = np.roll(out[by0:by1], shift_px, axis=1)

        if dt < 0.14:
            boost = int(round(18.0 * (1.0 - dt / 0.14)))
            if boost > 0:
                out = np.clip(out.astype(np.int16) + boost, 0, 255).astype(np.uint8)
        return out

    return clip.transform(_impact_frame)


def _build_reveal_map_clip(
    map_path: str,
    mask_f,
    box_w: int,
    box_h: int,
    bg_duration: float,
    effects: bool,
    zoom_enabled: bool,
    zoom_x: float,
    zoom_y: float,
    zoom_start: float,
    zoom_duration: float,
    reveal_time_raw: object,
    reveal_color: object = "#000000",
):
    """Build a dynamic RGB + mask ``VideoClip`` for the country reveal animation."""
    import numpy as np
    from moviepy import VideoClip
    from PIL import Image, ImageFilter

    with Image.open(map_path) as raw_map:
        base_pil = raw_map.convert('RGBA').resize((box_w, box_h), Image.Resampling.LANCZOS)
    base_rgba = np.array(base_pil, dtype=np.uint8)

    cover_r, cover_g, cover_b = _parse_reveal_color(reveal_color)
    map_visible = (base_rgba[:, :, 3:4] > 0).astype(np.float32)
    eff_mask = mask_f * map_visible
    black_pixel = np.array([float(cover_r), float(cover_g), float(cover_b), 255.0], dtype=np.float32).reshape(1, 1, 4)
    base_f = base_rgba.astype(np.float32)
    black_f = base_f * (1.0 - eff_mask) + black_pixel * eff_mask
    delta_f = black_f - base_f
    black_rgba = np.clip(np.rint(black_f), 0, 255).astype(np.uint8)

    shadow_only = None
    if effects:
        alpha_chan = Image.new('L', (box_w + 48, box_h + 48))
        alpha_chan.paste(base_pil.getchannel('A'), (24, 32))
        shadow_only = Image.new('RGBA', (box_w + 48, box_h + 48), (0, 0, 0, 0))
        shadow_only.putalpha(
            alpha_chan.filter(ImageFilter.GaussianBlur(10)).point(lambda p: round(p * 0.8))
        )

    def _compose_rgba(frame_rgba) -> np.ndarray:
        if shadow_only is not None:
            canvas = shadow_only.copy()
            canvas.alpha_composite(Image.fromarray(frame_rgba), (24, 24))
            return np.asarray(canvas, dtype=np.uint8)
        return np.ascontiguousarray(frame_rgba, dtype=np.uint8)

    def _to_rgb_and_mask(composed: np.ndarray) -> tuple[object, object]:
        rgb = np.ascontiguousarray(composed[:, :, :3])
        mask = np.ascontiguousarray(composed[:, :, 3], dtype=np.float32) / 255.0
        return rgb, mask

    z_start = zoom_start if zoom_enabled else 1.5
    r_time = _compute_reveal_time(zoom_enabled, zoom_start, zoom_duration, reveal_time_raw)
    fade_dur = 0.25
    phase_end = z_start if (zoom_enabled and z_start > 0.2) else min(1.5, r_time * 0.6)

    dot_cx = zoom_x * box_w
    dot_cy = zoom_y * box_h
    mask_2d = eff_mask[:, :, 0] > 0.5
    ys, xs = np.nonzero(mask_2d)
    if ys.size > 0:
        dot_cx = float(xs.mean())
        dot_cy = float(ys.mean())
        ix = max(0, min(box_w - 1, int(round(dot_cx))))
        iy = max(0, min(box_h - 1, int(round(dot_cy))))
        if not mask_2d[iy, ix]:
            d2 = (xs.astype(np.float32) - dot_cx) ** 2 + (ys.astype(np.float32) - dot_cy) ** 2
            best = int(np.argmin(d2))
            dot_cx = float(xs[best])
            dot_cy = float(ys[best])

    dot_overlays = _render_reveal_dot_overlays(box_w, box_h, dot_cx, dot_cy, (cover_r, cover_g, cover_b))
    dot_rgba_frames = {
        count: _compose_rgba(_alpha_composite_patch(black_rgba, *dot_overlays[count]))
        for count in (1, 2, 3)
    }
    dot_frames = {
        count: _to_rgb_and_mask(dot_rgba_frames[count])
        for count in (1, 2, 3)
    }
    black_composed_rgba = _compose_rgba(black_rgba)
    base_composed_rgba = _compose_rgba(base_rgba)
    final_frame = _to_rgb_and_mask(base_composed_rgba)

    frame_cache: list[tuple[float, object, object] | None] = [None]

    def _get_cached_frame(t: float) -> tuple[object, object]:
        t_key = float(t)
        cached = frame_cache[0]
        if cached is not None and cached[0] == t_key:
            return cached[1], cached[2]
        if t_key < r_time:
            if t_key < phase_end * 0.33:
                count = 1
            elif t_key < phase_end * 0.66:
                count = 2
            else:
                count = 3
            rgb, mask = dot_frames[count]
        elif t_key >= r_time + fade_dur:
            rgb, mask = final_frame
        else:
            fade_alpha = max(0.0, min(1.0, 1.0 - (t_key - r_time) / fade_dur))
            blended_rgba = np.clip(np.rint(base_f + fade_alpha * delta_f), 0, 255).astype(np.uint8)
            rgb, mask = _to_rgb_and_mask(_compose_rgba(blended_rgba))
        frame_cache[0] = (t_key, rgb, mask)
        return rgb, mask

    rgb_clip = VideoClip(lambda t: _get_cached_frame(t)[0], duration=bg_duration)
    mask_clip = VideoClip(lambda t: _get_cached_frame(t)[1], is_mask=True, duration=bg_duration)
    reveal_clip = rgb_clip.with_mask(mask_clip)
    spec_h, spec_w = base_composed_rgba.shape[:2]
    reveal_clip.rust_reveal_spec = (
        int(spec_w),
        int(spec_h),
        dot_rgba_frames[1].tobytes(),
        dot_rgba_frames[2].tobytes(),
        dot_rgba_frames[3].tobytes(),
        black_composed_rgba.tobytes(),
        base_composed_rgba.tobytes(),
        float(r_time),
        float(phase_end),
        float(fade_dur),
    )
    return reveal_clip


@app.route('/api/render_video', methods=['POST'])
@app.route('/api/render_video/', methods=['POST'])
@render_progress.track
def make_video() -> tuple[object, int]:
    cleanup_old_files()
    data: dict[str, object] = request.get_json(silent=True) or {}

    map_b64 = str(data.get('map_file') or '')
    background_b64 = str(data.get('background_video') or '')
    title = str(data.get('video_title') or data.get('title') or '').strip()
    codec = str(data.get('codec') or 'libx264').strip()
    raw_duration = data.get('duration')

    # --- Validation -----------------------------------------------------------
    if not map_b64:
        return jsonify({"status": "error", "error": "Missing 'map_file'."}), 400
    if not background_b64:
        return jsonify({"status": "error", "error": "Missing 'background_video'."}), 400
    if not title:
        return jsonify({"status": "error", "error": "Missing 'video_title'."}), 400
    if len(title) > 1000:
        return jsonify(status='error', error='Title must be at most 1000 characters.'), 400
    try:
        font_path, font_size = _title_settings(data)
        first_color, other_color = _title_colors(data)
        watermark_text, watermark_color = _watermark_settings(data)
        watermark_font_path = _watermark_font_path(data)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400
    if codec not in ALLOWED_CODECS:
        return jsonify({"status": "error",
                        "error": f"Unsupported codec '{codec}'. "
                                 f"Allowed: {', '.join(ALLOWED_CODECS)}."}), 400
    if codec != 'libx264' and codec not in _available_codecs():
        return jsonify(status='error', error=f"Codec '{codec}' is unavailable on the render worker. Select libx264 (CPU)."), 400

    def _decode_material(value: str, label: str) -> bytes:
        """Strip an optional `data:...;base64,` prefix and decode to bytes."""
        if value.startswith("data:"):
            # e.g. "data:image/png;base64,AAAA..."
            _, _, b64_part = value.partition(",")
        else:
            b64_part = value
        try:
            raw = base64.b64decode(b64_part, validate=True)
        except Exception:
            raise ValueError(f"{label} is not valid base64.")
        if not raw:
            raise ValueError(f"{label} is empty after decoding.")
        # Apply the upload limit to decoded bytes, excluding base64 overhead.
        if len(raw) > MAX_MATERIAL_BYTES:
            raise ValueError(f"{label} exceeds the 10 MB limit ({len(raw) // (1024 * 1024)} MB).")
        return raw

    try:
        map_bytes = _decode_material(map_b64, "map_file")
        bg_bytes = _decode_material(background_b64, "background_video")
        from PIL import Image, ImageOps, UnidentifiedImageError

        def _decode_optional_image(field: str, label: str):
            raw_val = data.get(field)
            if not raw_val:
                return None
            raw_bytes = _decode_material(str(raw_val), field)
            try:
                with Image.open(io.BytesIO(raw_bytes)) as uploaded:
                    if uploaded.width * uploaded.height > 20_000_000:
                        raise ValueError(f'{label} image must be at most 20 megapixels.')
                    return uploaded.convert('RGBA')
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValueError(f'{label} must be a valid image.')

        watermark_image = _decode_optional_image('watermark_image', 'Watermark')
        subscribe_custom_img = _decode_optional_image('subscribe_image', 'Subscribe')
        watermark_stars_custom_img = _decode_optional_image('watermark_stars_image', 'Watermark stars')
        top_left_custom_img = _decode_optional_image('top_left_sticker_image', 'Top-left sticker')
        top_right_custom_img = _decode_optional_image('top_right_sticker_image', 'Top-right sticker')
    except ValueError as ve:
        return jsonify({"status": "error", "error": str(ve)}), 400

    safe_title = _filename_stem(title)
    # The video headline and the selected song are independent identifiers.
    audio_title = data.get('audio_title', '')
    if not isinstance(audio_title, str) or (audio_title and not _AUDIO_TITLE_RE.fullmatch(audio_title)):
        return jsonify(status='error', error='Invalid audio track title.'), 400
    try:
        audio_bytes = _decode_material(str(data['audio_file']), 'audio_file') if data.get('audio_file') else None
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400
    cut_audio_path = os.path.join(CUT_DIR, f"{audio_title}_cut.mp3")
    # Prefer an edited track; fall back to the full download if no cut exists.
    audio_path = (
        cut_audio_path
        if os.path.exists(cut_audio_path)
        else os.path.join(DOWNLOADS, f"{audio_title}.mp3")
    )
    if 'audio_title' in data and audio_title and not audio_bytes and not os.path.isfile(audio_path):
        return jsonify(status='error', error='Selected audio is no longer available. Download and cut the track again.'), 400
    if data.get('include_audio') is True and not audio_bytes and not (audio_title and os.path.isfile(audio_path)):
        return jsonify(status='error', error='Selected audio is missing. Reload Video Studio and download the track again.'), 400
    # A fresh URL prevents the player from reusing an older, silent render.
    output_path = os.path.join(RENDER_DIR, f"{safe_title}_{uuid.uuid4().hex[:12]}_final.mp4")

    map_path = bg_path = audio_temp_path = prepared_bg_path = None
    media_resources = []
    try:
        from moviepy import (VideoFileClip, CompositeVideoClip,
                             ImageClip, AudioFileClip)

        os.makedirs(RENDER_DIR, exist_ok=True)

        # Write the decoded materials to temp files on disk.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as map_file_temp:
            map_file_temp.write(map_bytes)
            map_path = map_file_temp.name

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as bg_video_temp:
            bg_video_temp.write(bg_bytes)
            bg_path = bg_video_temp.name

        if audio_bytes:
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as audio_temp:
                audio_temp.write(audio_bytes)
                audio_temp_path = audio_temp.name
                audio_path = audio_temp_path

        # Target clip length: 1..30 seconds (default 8.0), never longer than the source clips.
        try:
            TARGET = float(raw_duration) if raw_duration is not None else 8.0
        except (TypeError, ValueError):
            TARGET = 8.0
        TARGET = max(1.0, min(30.0, TARGET))

        source_video = VideoFileClip(bg_path, audio=False)
        source_duration = source_video.duration or TARGET
        source_video.close()
        bg_duration = min(TARGET, source_duration)

        # Fill the portrait frame without stretching the source footage; FFmpeg
        # scales, crops and softens natively instead of per frame in Python.
        canvas_w, canvas_h = 1080, 1920
        effects = data.get('visual_effects', True) is not False
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as prepared_temp:
            prepared_bg_path = prepared_temp.name
        _prepare_background(bg_path, prepared_bg_path, bg_duration, (canvas_w, canvas_h), effects)
        background_video = VideoFileClip(prepared_bg_path, audio=False)
        media_resources.append(background_video)
        background_video = background_video.with_duration(bg_duration)
        dynamic_layers = {id(background_video)}
        dynamic_fade_timing = {}

        # Darken the edges and the area behind the title for readable white text.
        import numpy as np
        shade = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        y = np.linspace(-1, 1, canvas_h)
        shade[:, :, 3] = (25 + 65 * np.abs(y) ** 1.5).astype(np.uint8)[:, None]
        scrim = ImageClip(shade).with_duration(bg_duration)

        # Anchor the headline near the top and let the map fill the space below.
        title_uppercase = data.get('title_uppercase', True) is not False
        allow_remote_stickers = data.get('allow_remote_stickers', True) is not False
        left_gen, left_base = _parse_sticker_spec(str(data.get('top_left_sticker') or 'none'), title=title, side='left')
        right_gen, right_base = _parse_sticker_spec(str(data.get('top_right_sticker') or 'none'), title=title, side='right')
        top_left_sticker = f'gen_{left_gen}:{left_base}' if left_gen else left_base
        top_right_sticker = f'gen_{right_gen}:{right_base}' if right_gen else right_base

        if top_left_custom_img is not None:
            left_pil = ImageOps.contain(top_left_custom_img, (140, 140), method=Image.Resampling.LANCZOS)
            if effects:
                left_pil = _with_shadow(left_pil)
            left_sticker_arr = np.array(left_pil)
        else:
            left_sticker_arr = _sticker_image(top_left_sticker, size=140, effects=effects, allow_remote=allow_remote_stickers)

        if top_right_custom_img is not None:
            right_pil = ImageOps.contain(top_right_custom_img, (140, 140), method=Image.Resampling.LANCZOS)
            if effects:
                right_pil = _with_shadow(right_pil)
            right_sticker_arr = np.array(right_pil)
        else:
            right_sticker_arr = _sticker_image(top_right_sticker, size=140, effects=effects, allow_remote=allow_remote_stickers)

        has_top_stickers = left_sticker_arr is not None or right_sticker_arr is not None

        subscribe_enabled = bool(data.get('subscribe_badge', False))
        subscribe_text = str(data.get('subscribe_text') or 'Subscribe!').strip()[:40] or 'Subscribe!'
        try:
            subscribe_delay = float(data.get('subscribe_delay', 2.5))
        except (TypeError, ValueError):
            subscribe_delay = 2.5
        subscribe_delay = max(0.0, min(25.0, subscribe_delay))

        watermark_stars = bool(data.get('watermark_stars', False))
        # Explicit switches take precedence over previously uploaded custom images.
        if data.get('subscribe_badge') is False:
            subscribe_custom_img = None
        if data.get('watermark_stars') is False:
            watermark_stars_custom_img = None
        has_stars = bool(watermark_stars or watermark_stars_custom_img is not None)
        intro_fade = bool(data.get('intro_fade', False))

        title_clip = _build_title_clip(
            title,
            bg_duration,
            1040,
            font_path,
            font_size,
            first_color,
            other_color,
            effects,
            uppercase=title_uppercase,
        )
        map_clip = ImageClip(map_path).with_duration(bg_duration)
        title_top = 176 if has_top_stickers else 70
        gap = 16

        subscribe_clip = None
        accent_top = title_top + title_clip.h + gap
        accent_height = 0
        if subscribe_custom_img is not None or subscribe_enabled:
            if subscribe_custom_img is not None:
                sub_pil = ImageOps.contain(subscribe_custom_img, (680, 115), method=Image.Resampling.LANCZOS)
                if effects:
                    sub_pil = _with_shadow(sub_pil)
                sub_arr = np.array(sub_pil)
            else:
                sub_arr = _subscribe_badge_image(subscribe_text, font_path, effects)
            subscribe_clip = ImageClip(sub_arr).with_duration(bg_duration)
            if subscribe_clip.w > 728:
                subscribe_clip = subscribe_clip.resized(width=728)
            fade_start = min(subscribe_delay, max(0.0, bg_duration - 0.5)) if bg_duration > 1.0 else 0.0
            fade_dur = min(0.6, max(0.15, bg_duration - fade_start)) if bg_duration > 0.4 else 0.0
            subscribe_clip = _apply_clip_fade_in(subscribe_clip, bg_duration, fade_start=fade_start, fade_duration=fade_dur)
            subscribe_clip = subscribe_clip.with_position(('center', accent_top))
            dynamic_layers.add(id(subscribe_clip))
            dynamic_fade_timing[id(subscribe_clip)] = (float(fade_start), float(fade_dur))
            accent_height = max(accent_height, subscribe_clip.h)
        map_top = accent_top + accent_height + gap if accent_height else accent_top

        branded = bool(watermark_image is not None or watermark_text or has_stars)
        map_bottom = 1450 if watermark_image is not None else (1664 if branded else 1760)
        map_area_height = max(240, map_bottom - map_top)
        # Fit inside the map area while ensuring non-portrait maps span the full Shorts width.
        scale = min(canvas_w / map_clip.w, map_area_height / map_clip.h)
        if map_clip.w >= map_clip.h:
            scale = max(canvas_w / map_clip.w, scale)
        map_clip = map_clip.resized(scale)
        box_w, box_h = int(map_clip.w), int(map_clip.h)
        map_y = map_top + (map_area_height - box_h) / 2
        map_x = float(canvas_w - box_w) if box_w < canvas_w else (canvas_w - box_w) / 2.0
        map_clip_x = map_x - (24 if effects else 0)

        zoom_enabled = bool(data.get('zoom_enabled', False))
        zoom_x = max(0.0, min(1.0, _parse_finite_float(data.get('zoom_x'), 0.80)))
        zoom_y = max(0.0, min(1.0, _parse_finite_float(data.get('zoom_y'), 0.35)))
        zoom_scale = max(1.1, min(4.0, _parse_finite_float(data.get('zoom_scale'), 2.0)))
        zoom_start = max(0.0, _parse_finite_float(data.get('zoom_start'), 1.5))
        zoom_duration = max(0.2, _parse_finite_float(data.get('zoom_duration'), 2.5))
        zoom_out_enabled = bool(data.get('zoom_out_enabled', False))
        zoom_out_duration = max(0.0, _parse_finite_float(data.get('zoom_out_duration'), 2.0))

        reveal_enabled = zoom_enabled and bool(data.get('reveal_enabled', False))
        reveal_mask_f = (
            _decode_reveal_mask(data.get('reveal_mask'), box_w, box_h)
            if reveal_enabled
            else None
        )
        reveal_active = reveal_enabled and reveal_mask_f is not None

        rust_reveal_spec = None
        if reveal_active:
            map_clip = _build_reveal_map_clip(
                map_path,
                reveal_mask_f,
                box_w,
                box_h,
                bg_duration,
                effects,
                zoom_enabled,
                zoom_x,
                zoom_y,
                zoom_start,
                zoom_duration,
                data.get('reveal_time'),
                data.get('reveal_color', '#000000'),
            )
            if effects:
                map_y -= 24
            raw_reveal = getattr(map_clip, "rust_reveal_spec", None)
            if raw_reveal is not None:
                spec_w, spec_h, f1, f2, f3, blk, base, rt, pe, fd = raw_reveal
                rust_reveal_spec = (
                    int(map_clip_x),
                    int(map_y),
                    spec_w,
                    spec_h,
                    f1,
                    f2,
                    f3,
                    blk,
                    base,
                    rt,
                    pe,
                    fd,
                    True,
                )
        elif effects:
            with Image.open(map_path) as raw_map:
                map_image = raw_map.convert('RGBA').resize((box_w, box_h))
            map_clip = ImageClip(np.array(_with_shadow(map_image))).with_duration(bg_duration)
            map_y -= 24

        title_clip = title_clip.with_position(("center", title_top))
        map_clip = map_clip.with_position((map_clip_x, map_y))
        if reveal_active:
            dynamic_layers.add(id(map_clip))

        zoom_params = None
        if zoom_enabled:
            target_cx = map_x + zoom_x * box_w
            target_cy = (map_y + (24 if effects else 0)) + zoom_y * box_h
            zoom_params = (
                target_cx,
                target_cy,
                zoom_scale,
                zoom_start,
                zoom_duration,
                zoom_out_enabled,
                zoom_out_duration,
            )
        clips = [background_video, scrim, map_clip, title_clip]

        if left_sticker_arr is not None:
            left_clip = ImageClip(left_sticker_arr).with_duration(bg_duration)
            clips.append(left_clip.with_position((145, 20)))
        if right_sticker_arr is not None:
            right_clip = ImageClip(right_sticker_arr).with_duration(bg_duration)
            clips.append(right_clip.with_position((canvas_w - 145 - right_clip.w, 20)))
        if subscribe_clip is not None:
            clips.append(subscribe_clip)

        if watermark_image is not None:
            watermark_image = _round_watermark(watermark_image)
            logo_top = 1470
            if effects:
                watermark_image = _with_shadow(watermark_image)
                logo_top -= 24
            logo = ImageClip(np.array(watermark_image)).with_duration(bg_duration)
            clips.append(logo.with_position(('center', logo_top)))
        if watermark_text:
            lettering = _title_image(watermark_text, 936, watermark_font_path, 96,
                                    watermark_color, watermark_color, effects, max_height=110)
            branding = ImageClip(lettering).with_duration(bg_duration)
            watermark_y = 1695 if has_stars else 1740
            clips.append(branding.with_position(('center', watermark_y)))
        if has_stars:
            if watermark_stars_custom_img is not None:
                stars_pil = ImageOps.contain(watermark_stars_custom_img, (480, 95), method=Image.Resampling.LANCZOS)
                if effects:
                    stars_pil = _with_shadow(stars_pil)
                stars_arr = np.array(stars_pil)
            else:
                stars_arr = _stars_row_image(watermark_color or '#ffffff', effects)
            stars_clip = ImageClip(stars_arr).with_duration(bg_duration)
            stars_start = 0.0
            stars_fade_dur = 0.0
            if bg_duration > 1.8:
                stars_start = min(1.8, bg_duration * 0.25)
                stars_fade_dur = 0.5
                stars_clip = _apply_clip_fade_in(stars_clip, bg_duration, fade_start=stars_start, fade_duration=stars_fade_dur)
            stars_y = 1805 if watermark_text else 1785
            stars_clip = stars_clip.with_position(('center', stars_y))
            if bg_duration > 1.8:
                dynamic_layers.add(id(stars_clip))
                dynamic_fade_timing[id(stars_clip)] = (float(stars_start), float(stars_fade_dur))
            clips.append(stars_clip)
        if intro_fade:
            from moviepy import VideoClip
            fade_in_dur = min(0.75, max(0.2, bg_duration * 0.3))
            black_frame = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
            intro_veil = ImageClip(black_frame).with_duration(fade_in_dur)
            base_ones = np.ones((canvas_h, canvas_w), dtype=np.float32)

            def _veil_mask(t: float) -> np.ndarray:
                if t >= fade_in_dur or fade_in_dur <= 0:
                    return np.zeros_like(base_ones)
                alpha = 0.88 * (1.0 - float(t / fade_in_dur))
                return base_ones * max(0.0, min(1.0, alpha))

            veil_mask = VideoClip(_veil_mask, is_mask=True, duration=fade_in_dur)
            intro_veil = intro_veil.with_mask(veil_mask)
            dynamic_layers.add(id(intro_veil))
            clips.append(intro_veil)

        # Static layers are composed once into a single RGBA foreground.
        clips = _flatten_static_layers(clips, (canvas_w, canvas_h), dynamic_layers, bg_duration)

        has_audio = bool(audio_bytes or (audio_title and os.path.isfile(audio_path)))
        render_duration = bg_duration
        if has_audio:
            audio_clip = AudioFileClip(audio_path)
            media_resources.append(audio_clip)
            render_duration = min(bg_duration, audio_clip.duration or bg_duration)

        # Prefer the native Rust compositor when available; fall back to MoviePy on failure.
        used_rust = False
        rust_warning: str | None = None
        # ======== RUST ENGINE INJECTION =============
        try:  # pragma: no cover
            automap_rs = _load_automap_rs()

            def _clip_to_rgba_bytes(clip_obj, sample_t: float = 0.0) -> bytes:
                # MoviePy splits RGBA inputs into 3-channel clip.img + float clip.mask.
                arr = np.asarray(clip_obj.img, dtype=np.uint8)
                if arr.ndim == 3 and arr.shape[2] == 4:
                    return arr.tobytes()
                if getattr(clip_obj, "mask", None) is not None:
                    mask_frame = np.asarray(clip_obj.mask.get_frame(sample_t))
                    alpha = np.clip(mask_frame * 255.0, 0, 255).astype(np.uint8)
                    if alpha.ndim == 2:
                        alpha = alpha[:, :, None]
                else:
                    alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
                return np.concatenate([arr[:, :, :3], alpha], axis=2).tobytes()

            static_rgba = None
            dynamic_overlays = []
            veil_duration = 0.0

            # Skip clips[0] (background_video); Rust decodes it directly from prepared_bg_path.
            for clip in clips[1:]:
                if rust_reveal_spec is not None and clip is map_clip:
                    continue
                if not hasattr(clip, "img"):
                    continue

                is_dynamic = id(clip) in dynamic_layers
                is_full_canvas = clip.img.shape[:2] == (canvas_h, canvas_w)

                if not is_dynamic and is_full_canvas:
                    layer_rgba = _clip_to_rgba_bytes(clip, 0.0)
                    if static_rgba is None:
                        static_rgba = layer_rgba
                    elif hasattr(automap_rs, "alpha_composite_rs"):
                        static_rgba = automap_rs.alpha_composite_rs(static_rgba, layer_rgba)
                    else:
                        base_img = Image.frombytes("RGBA", (canvas_w, canvas_h), static_rgba)
                        over_img = Image.frombytes("RGBA", (canvas_w, canvas_h), layer_rgba)
                        static_rgba = Image.alpha_composite(base_img, over_img).tobytes()
                elif is_dynamic and is_full_canvas and clip.img.shape[2] == 3:
                    # Full-canvas 3-channel black clip represents the intro fade veil.
                    veil_duration = float(clip.duration or 0.0)
                elif is_dynamic:
                    # Resolve MoviePy position ("center" or pixel offset) into top-left (x, y).
                    pos = clip.pos(0) if callable(getattr(clip, "pos", None)) else getattr(clip, "pos", (0, 0))
                    h, w = clip.img.shape[:2]
                    x = (canvas_w - w) // 2 if pos[0] == "center" else int(pos[0])
                    y = (canvas_h - h) // 2 if pos[1] == "center" else int(pos[1])
                    start_sec, fade_in_sec = dynamic_fade_timing.get(id(clip), (0.0, 0.0))
                    dynamic_overlays.append((_clip_to_rgba_bytes(clip, float(bg_duration)), x, y, int(w), int(h), start_sec, fade_in_sec))

            if static_rgba is not None and prepared_bg_path:
                try:
                    from moviepy.config import FFMPEG_BINARY
                    if FFMPEG_BINARY:
                        os.environ.setdefault("FFMPEG_BINARY", FFMPEG_BINARY)
                except ImportError:
                    pass
                automap_rs.render_video_rs(
                    prepared_bg_path,
                    static_rgba,
                    dynamic_overlays,
                    veil_duration,
                    audio_path if has_audio else None,
                    output_path,
                    canvas_w,
                    canvas_h,
                    30,
                    float(render_duration),
                    codec,
                    _x264_preset() if codec == 'libx264' else None,
                    data.get('draft') is True,
                    zoom_params=zoom_params,
                    reveal_spec=rust_reveal_spec,
                )
                used_rust = True
        except Exception as exc:
            rust_warning = (
                f"Rust video render unavailable or failed ({type(exc).__name__}: {exc}); "
                "falling back to MoviePy."
            )
            logging.warning(rust_warning)

        if not used_rust:  # pragma: no branch
            final_clip = CompositeVideoClip(clips, size=(canvas_w, canvas_h))
            media_resources.append(final_clip)
            if zoom_params is not None:
                target_cx, target_cy, z_scale, z_start, z_dur, z_out_enabled, z_out_dur = zoom_params
                final_cw = canvas_w / z_scale
                final_ch = canvas_h / z_scale
                final_x0 = max(0.0, min(canvas_w - final_cw, target_cx - final_cw / 2.0))
                final_y0 = max(0.0, min(canvas_h - final_ch, target_cy - final_ch / 2.0))
                zoom_total_dur = render_duration if has_audio else bg_duration

                def _zoom_full_frame(get_frame, t: float) -> np.ndarray:
                    frame = get_frame(t)
                    if t <= z_start:
                        return frame
                    if t < z_start + z_dur:
                        u = (t - z_start) / z_dur
                        # Quintic smootherstep for silky-smooth camera acceleration/deceleration.
                        ease = u * u * u * (u * (u * 6.0 - 15.0) + 10.0)
                    elif z_out_enabled and z_out_dur > 0.0 and zoom_total_dur > (z_start + z_dur + 0.05):
                        out_start = max(z_start + z_dur, zoom_total_dur - z_out_dur)
                        out_dur = max(0.05, zoom_total_dur - out_start)
                        if t >= zoom_total_dur:
                            ease = 0.0
                        elif t > out_start:
                            u = (t - out_start) / out_dur
                            ease = 1.0 - (u * u * u * (u * (u * 6.0 - 15.0) + 10.0))
                        else:
                            ease = 1.0
                    else:
                        ease = 1.0
                    if ease <= 0.0:
                        return frame
                    cw = canvas_w + (final_cw - canvas_w) * ease
                    ch = canvas_h + (final_ch - canvas_h) * ease
                    x0 = final_x0 * ease
                    y0 = final_y0 * ease
                    inv_z = cw / canvas_w
                    zoomed = Image.fromarray(frame).transform(
                        (canvas_w, canvas_h),
                        Image.Transform.AFFINE,
                        (inv_z, 0.0, x0, 0.0, inv_z, y0),
                        resample=Image.Resampling.BILINEAR,
                    )
                    return np.asarray(zoomed, dtype=np.uint8)

                final_clip = final_clip.transform(_zoom_full_frame)
                media_resources.append(final_clip)
            if reveal_active:
                r_trigger = _compute_reveal_time(
                    zoom_enabled, zoom_start, zoom_duration, data.get('reveal_time')
                )
                final_clip = _apply_reveal_impact_fx(final_clip, r_trigger, canvas_w, canvas_h)
                media_resources.append(final_clip)
            if has_audio:
                final_clip = final_clip.with_audio(audio_clip.subclipped(0, render_duration))
                final_clip = final_clip.with_duration(render_duration)
            else:
                final_clip = final_clip.with_duration(bg_duration)

            # Draft renders keep the 1080x1920 layout and scale only at encode time.
            encode_options = {}
            if codec == 'libx264':
                encode_options['preset'] = _x264_preset()
            if data.get('draft') is True:
                encode_options['ffmpeg_params'] = ['-vf', 'scale=720:1280:flags=bicubic']
            final_clip.write_videofile(
                output_path,
                codec=codec,
                audio_codec="aac",
                fps=30,
                threads=_render_threads(),
                logger=render_progress.logger(),
                **encode_options,
            )

        payload = {
            "status": "success",
            "file_path": output_path,
            "download_url": "/api/download_video?" + urlencode({'path': output_path}),
            "has_audio": has_audio,
            "render_engine": "rust" if used_rust else "moviepy",
        }
        if rust_warning:
            payload["warning"] = rust_warning
        return jsonify(payload), 200
    
    except Exception as e:
        return jsonify({"status": "error", "error": f"Error processing video: {str(e)}"}), 500

    finally:
        # Release FFmpeg readers before removing their input files.
        for resource in reversed(media_resources):
            try:
                resource.close()
            except Exception:
                pass
        # Always clean up the temp input files.
        for p in (map_path, bg_path, audio_temp_path, prepared_bg_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


@app.route('/api/download_video/', methods=['GET'])
@app.route('/api/download_video', methods=['GET'])
def download_video():
    """Stream a rendered video file by its server path.

    Query params:
        path - absolute (or storage-relative) path to the rendered .mp4.

    Only files inside RENDER_DIR are served (path traversal is rejected).
    """
    raw_path = (request.args.get('path') or '').strip()
    if not raw_path:
        return jsonify({"status": "error", "error": "Missing 'path' query param."}), 400

    # Normalise and enforce that the file lives under RENDER_DIR.
    # Resolve symlinks before checking containment in the render directory.
    if '\0' in raw_path:
        return jsonify(status='error', error='Invalid path.'), 400
    candidate = os.path.realpath(raw_path if os.path.isabs(raw_path)
                                 else os.path.join(RENDER_DIR, raw_path))
    render_root = os.path.realpath(RENDER_DIR)
    if os.path.commonpath([candidate, render_root]) != render_root:
        return jsonify({"status": "error", "error": "Invalid path."}), 400
    if not os.path.isfile(candidate):
        return jsonify({"status": "error", "error": "File not found."}), 404

    return send_file(
        candidate,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=os.path.basename(candidate),
    )


if __name__ == "__main__":

    from server_runtime import run_app

    run_app(app, default_port=8080)
