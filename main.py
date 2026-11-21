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
API used by that UI. Gemini and DeepSeek use separate keys from the environment
or ``.env``. If the selected provider has no configured key, ``/api/status``
reports it and the UI uses that provider's browser-saved key for the request.
"""

import base64
import io
import json
import math
import os
import re
import threading
import time

from pathlib import Path
from PIL import Image, UnidentifiedImageError
import requests as _requests_module

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
from map.auto_map import (
    auto_map,
    _auto_fill_from_ai,
    _format_short_number,
    _parse_short_number,
    _parse_filling_overrides,
    _resolve_theme_name,
    _to_rgba_tuple,
)
from map.constants import europe_coords, world_coords, _PALETTE_THEMES
from cli import AVAILABLE_MODELS
from map.functions import (
    _ALLOWED_MAP_TYPES, AIServiceError, configured_api_key, generate_map_idea,
    list_deepseek_models, parse_ideas_content, provider_for_model,
    provider_key_name, slugify_prompt, validate_temperature,
)

from flask import Flask, request, jsonify, send_file, render_template, Response, session
from web_ai_cache import ai_cache

FONTS_DIR = BASE_DIR / "resources" / "fonts"

env = BASE_DIR / ".env"
key = (os.environ.get("GEMINI_API_KEY") or "").strip()
if not key and env.is_file():
    key = (dotenv_values(env).get("GEMINI_API_KEY") or "").strip()
needs_api_key = not key or key.startswith("your_")

class _PooledRequests:
    """``requests``-compatible facade that reuses one pooled Session.

    Keeps keep-alive connections to the worker instead of opening a new TCP
    connection per proxied call. Attribute access (exceptions, etc.) falls
    through to the ``requests`` module, and tests can still patch
    ``main.requests.get``/``post``.
    """

    def __init__(self):
        self._session = _requests_module.Session()
        self._orig_get = _requests_module.get
        self._orig_post = _requests_module.post

    def get(self, *args, **kwargs):
        if _requests_module.get is not self._orig_get:
            return _requests_module.get(*args, **kwargs)
        return self._session.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        if _requests_module.post is not self._orig_post:
            return _requests_module.post(*args, **kwargs)
        return self._session.post(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(_requests_module, name)


requests = _PooledRequests()

WORKER_URL = (os.environ.get("YTDLP_URL") or "http://127.0.0.1:8080").rstrip('/')


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
# Three 10 MiB materials (map, background and music) grow by 4/3 in JSON.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

from web_auth import init_web_auth

init_web_auth(app, dotenv_values(env) if env.is_file() else {})

from youtube_upload import YouTubeUploads

youtube_uploads = YouTubeUploads(app, WORKER_URL, requests)


@app.before_request
def validate_api_json_body():
    """Reject malformed or non-object JSON before endpoint field access."""
    if request.path.startswith('/api/') and request.is_json:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(status='error', error='Request body must be a valid JSON object.'), 400
        for name in ('api_key', 'url', 'provider', 'model'):
            if data.get(name) is not None and not isinstance(data[name], str):
                return jsonify(status='error', error=f'{name} must be a string.'), 400


@app.errorhandler(413)
def request_too_large(error):
    return jsonify(status='error', error='Upload exceeds the total request size limit.'), 413

@app.route('/')
def main():
    """Serve the main single-page map generator UI."""
    return render_template("index.html")


@app.route('/studio')
def studio():
    """Serve the dedicated Video Studio page (music downloader)."""
    # Establish identity before the page starts parallel API requests.
    youtube_uploads.owner()
    youtube_uploads.csrf()
    return render_template("video.html", title_fonts=_available_fonts())


@app.get('/api-manager')
def api_manager():
    """Manage browser API credentials and the existing YouTube OAuth connection."""
    youtube_uploads.owner()
    youtube_uploads.csrf()
    response = app.make_response(render_template('api_manager.html',
        server_keys={provider: bool(_server_api_key(provider)) for provider in ('gemini', 'deepseek')},
        search_server_configured=any(
            str(os.environ.get(name) or '').strip() and not str(os.environ.get(name)).lower().startswith('your_')
            for name in ('TAVILY_API_KEY', 'BRAVE_SEARCH_API_KEY'))))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/video_title_preview', methods=['POST'])
def video_title_preview():
    """Proxy title/watermark/sticker preview requests to the yt-dlp render worker."""

    data = request.get_json(silent=True) or {}
    allowed_keys = (
        'video_title',
        'title_font',
        'title_font_size',
        'title_first_line_color',
        'title_other_lines_color',
        'title_uppercase',
        'visual_effects',
        'preview_kind',
        'watermark_text',
        'watermark_color',
        'watermark_font',
        'subscribe_text',
        'sticker_kind',
        'sticker_side',
        'allow_remote_stickers',
    )
    try:
        # Preview and final rendering share the worker's title styling options.
        response = requests.post(
            f'{WORKER_URL}/api/video_title_preview',
            json={key: data[key] for key in allowed_keys if key in data},
            timeout=15,
        )
    except requests.RequestException:
        return jsonify(error='Title preview is unavailable. Please try again.'), 502
    # Preserve the worker's image content type for successful preview responses.
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


@app.route('/api/codecs')
def api_codecs():
    """Ask the render worker which encoders its FFmpeg and hardware can use."""

    try:
        response = requests.get(f'{WORKER_URL}/api/codecs', timeout=20)
        return jsonify(response.json()), response.status_code
    except (requests.RequestException, ValueError):
        return jsonify(error='Could not check render worker codecs. Use libx264 (CPU).'), 502


@app.route('/api/status')
def api_status():
    """Report whether the selected provider already has a server-side key.

    Response:
        { "needs_api_key": true }  -> the UI must ask the user for a key
    """
    provider = request.args.get('provider', 'gemini')
    if provider not in ('gemini', 'deepseek'):
        return jsonify(status='error', error='Unknown AI provider.'), 400
    required = needs_api_key if provider == 'gemini' else not _server_api_key(provider)
    return jsonify({"needs_api_key": required})


def _server_api_key(provider):
    """Return the server-configured API key for ``provider``, if any."""
    return configured_api_key(provider, dotenv_values(env) if env.is_file() else {})


def _request_provider(data):
    """Resolve and validate the AI provider from ``provider`` or ``model``."""
    inferred = provider_for_model(data.get('model') or '')
    provider = data.get('provider') or inferred
    if provider not in ('gemini', 'deepseek'):
        raise ValueError('Unknown AI provider.')
    if data.get('model') and inferred != provider:
        raise ValueError('The selected model belongs to a different AI provider.')
    return provider


def _request_api_key(data, provider):
    """Resolve the API key for ``provider`` from server config or the request payload."""
    supplied = str(data.get('api_key') or '').strip() or None
    if provider == 'deepseek':
        resolved = _server_api_key(provider) or supplied
    else:
        resolved = supplied
        if not needs_api_key:
            return resolved
    if not resolved:
        raise ValueError(f'API key required. Provide {provider_key_name(provider)} via .env or the web form.')
    return resolved


_TRUTHY = {'1', 'true', 'yes', 'on'}


def _wants_refresh(data) -> bool:
    """Return True if the request asks to bypass the AI response cache."""
    value = data.get('refresh_ai', data.get('refresh', False))
    return value is True or str(value).strip().lower() in _TRUTHY


def _auto_map_with_ai_cache(*, refresh: bool, provider: str, api_key, **kwargs):
    """Call auto_map, reusing a cached AI answer for identical data requests.

    Only request-affecting inputs form the key, so font/style/palette changes
    reuse the answer. API keys only contribute a hash to the user scope.
    Returns (buffer, cache_hit).
    """
    if kwargs.get('ai_answer_override') is not None:
        return auto_map(api_key=api_key, **kwargs), False

    temperature = kwargs.get('temperature')
    identity = ''
    try:
        identity = str(session.get('web_identity', '') or '')
    except RuntimeError:
        identity = ''
    short_form = 'short_form' in str(kwargs.get('optional_feature') or '')
    scope = ai_cache.scope(identity, api_key)
    if kwargs.get("web_search") and kwargs.get("tavily_api_key"):
        scope = ai_cache.scope(scope, kwargs["tavily_api_key"])
    base_fields = dict(
        prompt=str(kwargs.get('prompt') or ''),
        map_type=str(kwargs.get('map_type') or ''),
        model=str(kwargs.get('model') or ''),
        provider=provider,
        temperature=temperature,
        web_search=bool(kwargs.get('web_search', False)),
        max_search_requests=kwargs.get('max_search_requests', 5),
    )
    cache_key = ai_cache.make_key(scope, short_form=short_form, **base_fields)
    cached = None if refresh else ai_cache.get(cache_key)
    if cached is None and not refresh and short_form:
        raw_cached = ai_cache.get(ai_cache.make_key(scope, short_form=False, **base_fields))
        if raw_cached and any(_parse_short_number(v) is not None for v in raw_cached.values()):
            cached = raw_cached
    if cached is not None:
        kwargs['ai_answer_override'] = cached
    buffer = auto_map(api_key=api_key, **kwargs)
    if buffer is not None:
        meta = getattr(buffer, 'map_meta', None)
        answer = meta.get('raw_ai_answer', meta.get('ai_answer')) if isinstance(meta, dict) else None
        if isinstance(answer, dict):
            if short_form:
                meta['ai_answer'] = {k: _format_short_number(v) for k, v in answer.items()}
            if not refresh:
                ai_cache.put(cache_key, answer)
    return buffer, cached is not None


def _build_analysis_from_ai(
    ai_answer: dict[str, object],
    mode: str = "auto",
    palette_theme: str = "auto",
    topic: str = "",
    render_meta: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build UI-friendly color rules and stats from AI map values or render metadata."""
    vals = [v for v in ai_answer.values() if v is not None and str(v).strip() != ""]
    num_vals: list[float] = []
    for v in vals:
        parsed = _parse_short_number(v)
        if parsed is not None:
            num_vals.append(parsed)

    resolved_mode = mode if mode in ("number", "text") else (
        "number" if vals and len(num_vals) >= max(1, int(len(vals) * 0.6)) else "text"
    )
    resolved_theme = (
        str(render_meta.get("resolved_theme"))
        if isinstance(render_meta, dict) and render_meta.get("resolved_theme")
        else _resolve_theme_name(palette_theme, prompt=topic, mode=resolved_mode)
    )

    txt_dict: dict[str, tuple[int, int, int, int]] = {}
    num_dict: dict[tuple[int, int], tuple[int, int, int, int]] = {}

    if isinstance(render_meta, dict):
        raw_txt = render_meta.get("used_txt") or render_meta.get("filling_txt")
        if isinstance(raw_txt, dict) and raw_txt:
            for k, v in raw_txt.items():
                rgba = _to_rgba_tuple(v)
                if rgba is not None:
                    txt_dict[str(k)] = rgba
        raw_num = render_meta.get("used_num") or render_meta.get("filling_num")
        if isinstance(raw_num, dict) and raw_num:
            _, num_dict = _parse_filling_overrides(None, raw_num)

    if not txt_dict and not num_dict:
        txt_dict, num_dict = _auto_fill_from_ai(
            ai_answer,
            resolved_mode,
            palette_theme=palette_theme,
            prompt=topic,
        )

    ui_txt_rules = [
        {"key": str(k), "hex": f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}", "alpha": c[3]}
        for k, c in txt_dict.items()
    ]
    ui_num_rules = [
        {"low": lo, "high": hi, "hex": f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}", "alpha": c[3]}
        for (lo, hi), c in num_dict.items()
    ]

    default_label_style = {
        "text_color": "#ffffff",
        "stroke_color": "#090d16",
        "stroke_width": 15,
        "auto_fit": True,
        "recommended_font": "Bips.ttf" if resolved_mode == "number" else "DIN Condensed.ttf",
        "recommended_gsm": 15,
    }
    if isinstance(render_meta, dict) and isinstance(render_meta.get("label_style"), dict):
        default_label_style.update(render_meta["label_style"])

    return {
        "detected_type": resolved_mode,
        "resolved_mode": resolved_mode,
        "resolved_theme": resolved_theme,
        "filling_txt": {str(k): list(c) for k, c in txt_dict.items()},
        "filling_num": {f"({lo}, {hi})": list(c) for (lo, hi), c in num_dict.items()},
        "ui_txt_rules": ui_txt_rules,
        "ui_num_rules": ui_num_rules,
        "label_style": default_label_style,
        "stats": {
            "total_regions": len(vals),
            "unique_values": len({str(v).strip().lower() for v in vals}),
            "min_value": min(num_vals) if num_vals else None,
            "max_value": max(num_vals) if num_vals else None,
            "sample_values": [str(v) for v in vals[:8]],
        },
    }


def _inspect_uploaded_map_image(
    image_data: object,
    map_type: str = "default",
    palette_theme: str = "auto",
) -> dict[str, object]:
    """Decode an uploaded map image and extract its sampled region color palette."""
    if not isinstance(image_data, str) or not image_data.strip():
        raise ValueError("Empty image data.")
    raw_b64 = image_data.strip()
    if "," in raw_b64 and raw_b64.lower().startswith("data:"):
        header, raw_b64 = raw_b64.split(",", 1)
        if not header.lower().startswith("data:image/"):
            raise ValueError("Unsupported data URL media type.")
    try:
        decoded = base64.b64decode(raw_b64, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 image payload.") from exc
    if not decoded:
        raise ValueError("Decoded image payload is empty.")
    try:
        with Image.open(io.BytesIO(decoded)) as opened:
            img = opened.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Uploaded file is not a valid image.") from exc

    width, height = img.size
    if width < 8 or height < 8:
        raise ValueError("Uploaded map image is too small to inspect.")

    if map_type == "world":
        ref_coords = world_coords
        base_name = "world_map.png"
    elif map_type == "world_countries":
        from map.world_countries_coords import world_countries_coords
        ref_coords = world_countries_coords
        base_name = "world_countries_map.png"
    else:
        ref_coords = europe_coords
        base_name = "default_map.png"
    base_path = BASE_DIR / "resources" / "maps" / base_name
    ref_w, ref_h = (2200, 1800)
    if base_path.is_file():
        try:
            with Image.open(base_path) as ref_img:
                ref_w, ref_h = ref_img.size
        except OSError:
            pass

    px = img.load()
    clusters: list[tuple[int, int, int, int]] = []
    rule_colors: dict[str, tuple[int, int, int, int]] = {}
    sampled_regions = 0
    scale_x = width / max(1, ref_w)
    scale_y = height / max(1, ref_h)

    for points in ref_coords.values():
        if not points:
            continue
        sampled_regions += 1
        px_x, px_y = points[0]
        sx = max(0, min(width - 1, int(round(px_x * scale_x))))
        sy = max(0, min(height - 1, int(round(px_y * scale_y))))
        samples: list[tuple[int, int, int, int]] = []
        for dx in (-6, 0, 6):
            cx = max(0, min(width - 1, sx + dx))
            for dy in (-6, 0, 6):
                cy = max(0, min(height - 1, sy + dy))
                r, g, b, a = px[cx, cy]
                if a >= 32 and (r + g + b) > 35:
                    samples.append((r, g, b, 255))
        if not samples:
            r, g, b, a = px[sx, sy]
            samples.append((r, g, b, 255 if a >= 32 else a))

        avg = (
            int(round(sum(p[0] for p in samples) / len(samples))),
            int(round(sum(p[1] for p in samples) / len(samples))),
            int(round(sum(p[2] for p in samples) / len(samples))),
            255,
        )
        matched: tuple[int, int, int, int] | None = None
        for existing in clusters:
            dist = abs(avg[0] - existing[0]) + abs(avg[1] - existing[1]) + abs(avg[2] - existing[2])
            if dist <= 36:
                matched = existing
                break
        if matched is None:
            clusters.append(avg)
            matched = avg

        hex_label = f"#{matched[0]:02x}{matched[1]:02x}{matched[2]:02x}"
        rule_colors.setdefault(hex_label, matched)

    ui_txt_rules = [
        {"key": k, "hex": k, "alpha": c[3]}
        for k, c in rule_colors.items()
    ]
    return {
        "detected_type": "palette",
        "resolved_mode": "palette",
        "resolved_theme": palette_theme if palette_theme and palette_theme != "auto" else "sampled",
        "inspection_only": True,
        "filling_txt": {k: list(c) for k, c in rule_colors.items()},
        "filling_num": {},
        "ui_txt_rules": ui_txt_rules,
        "ui_num_rules": [],
        "label_style": {
            "text_color": "#ffffff",
            "stroke_color": "#090d16",
            "stroke_width": 15,
            "auto_fit": True,
            "recommended_font": "Bips.ttf",
            "recommended_gsm": 15,
        },
        "stats": {
            "total_regions": sampled_regions,
            "unique_values": len(rule_colors),
            "min_value": None,
            "max_value": None,
            "sample_values": [],
        },
    }


def _parse_render_params(data: dict[str, object]) -> tuple[str, str | None, int]:
    """Validate font, normalize extra_option, and parse gsm safely."""
    font_name = data.get("font")
    if font_name not in _available_fonts():
        font_name = "Bips.ttf"

    extra_option = data.get("extra_option")
    if extra_option in ("none", "", None):
        extra_option = None
    else:
        extra_option = str(extra_option)

    try:
        gsm = int(data.get("gsm", 15))
    except (TypeError, ValueError, OverflowError):
        gsm = 15

    return str(font_name), extra_option, gsm


_MAP_PROGRESS_LOCK = threading.Lock()
_MAP_PROGRESS: dict[str, dict[str, object]] = {}


def _set_map_progress(progress_id: str, stage: int, label: str, status: str = "running") -> None:
    """Record the active map generation stage for ``progress_id``."""
    if not progress_id:
        return
    now = time.time()
    with _MAP_PROGRESS_LOCK:
        stale = [k for k, v in _MAP_PROGRESS.items() if now - float(v.get("updated_at", 0)) > 300]
        for k in stale:
            _MAP_PROGRESS.pop(k, None)
        _MAP_PROGRESS[progress_id] = {
            "stage": stage,
            "label": label,
            "status": status,
            "updated_at": now,
        }


@app.get('/api/generate_progress/<progress_id>')
def api_generate_progress(progress_id: str):
    """Return current map generation progress for ``progress_id``."""
    with _MAP_PROGRESS_LOCK:
        entry = dict(_MAP_PROGRESS.get(progress_id) or {"stage": 1, "label": "Querying AI model...", "status": "running"})
    resp = jsonify(entry)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route('/api/generate/', methods=['POST'])
@app.route('/api/generate', methods=['POST'])
def generate():
    """Generate a map from the JSON body and return it as a data URL.

    Request JSON:
        { "topic", "mode", "map_type", "gsm", "extra_option", "model", "font", "temperature" }

    Response JSON:
        { "status": "success", "command": "...", "image_url": "data:image/png;base64,..." }
    """
    data = request.get_json(silent=True) or {}

    # Optional user-supplied key (used only when .env has none).
    try:
        provider = _request_provider(data)
        api_key = _request_api_key(data, provider)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400

    # Validate the requested font against what is actually on disk, otherwise
    # auto_map would raise FileNotFoundError when loading the TrueType file.
    font_name, extra_option, gsm = _parse_render_params(data)

    temperature_options = {}
    if 'temperature' in data:
        try:
            temperature_options['temperature'] = validate_temperature(data['temperature'])
        except ValueError as error:
            return jsonify(status='error', error=str(error)), 400

    if "map_type" in data:
        req_map_type = str(data["map_type"] or "").strip().lower()
        if req_map_type not in _ALLOWED_MAP_TYPES:
            return jsonify(
                status='error',
                error=f"Invalid map_type '{req_map_type}'. Allowed: {', '.join(sorted(_ALLOWED_MAP_TYPES))}.",
            ), 400
        data["map_type"] = req_map_type

    # Rebuild the equivalent CLI command for display only; it is never executed.
    command = (
        f"python cli.py make \"{data.get('topic', '')}\" "
        f"-m {data.get('mode', 'text')} -mp {data.get('map_type', 'default')} "
        f"-gsm {gsm} -eop {data.get('extra_option', 'none')} "
        f"--model {data.get('model', AVAILABLE_MODELS[0])} --font \"{font_name}\""
    )
    if temperature_options:
        command += f" --temperature {temperature_options['temperature']:g}"

    show_legend = bool(data.get("show_legend", False))
    highlight_extrema = bool(data.get("highlight_extrema", False))
    if show_legend:
        command += " --legend"
    if highlight_extrema:
        command += " --highlight-extrema"
    label_suffix = str(data.get("label_suffix") or "").replace("\r", "").replace("\n", "")[:16]
    if label_suffix:
        escaped_suffix = label_suffix.replace('"', '\\"')
        command += f' --suffix "{escaped_suffix}"'

    progress_id = str(data.get("progress_id") or "").strip()[:64]
    if progress_id:
        _set_map_progress(progress_id, 1, "Querying AI model...")

    style_kwargs: dict[str, object] = {
        "show_legend": show_legend,
        "highlight_extrema": highlight_extrema,
    }
    if highlight_extrema:
        if "highlight_high_color" in data:
            style_kwargs["highlight_high_color"] = str(data.get("highlight_high_color") or "#00e5ff")
        if "highlight_low_color" in data:
            style_kwargs["highlight_low_color"] = str(data.get("highlight_low_color") or "#ff2daa")
    if label_suffix:
        style_kwargs["label_suffix"] = label_suffix
    if isinstance(data.get("skip_label_countries"), list):
        style_kwargs["skip_label_countries"] = [str(c) for c in data["skip_label_countries"] if str(c).strip()]
    if isinstance(data.get("ai_answer_override"), dict):
        style_kwargs["ai_answer_override"] = data["ai_answer_override"]
    if progress_id:
        style_kwargs["progress_callback"] = lambda stage, label: _set_map_progress(progress_id, stage, label)
    for style_key, kw_name in (
        ("filling_txt", "filling_txt_override"),
        ("filling_num", "filling_num_override"),
        ("label_style", "label_style"),
        ("auto_style", "auto_style"),
        ("palette_theme", "palette_theme"),
    ):
        if style_key in data:
            style_kwargs[kw_name] = data[style_key]

    try:
        buffer, ai_cache_hit = _auto_map_with_ai_cache(
            refresh=_wants_refresh(data),
            provider=provider,
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
            **temperature_options,
            **({"web_search": True, "max_search_requests": max(1, min(10, int(data.get("max_search_requests") or 5))), **({"tavily_api_key": str(data.get("tavily_api_key") or "").strip()} if data.get("tavily_api_key") else {})} if str(data.get("web_search") or "").strip().lower() in ("1", "true", "yes", "on") else {}),
            **style_kwargs,
        )
    except AIServiceError as error:
        if progress_id:
            _set_map_progress(progress_id, 1, str(error), status="error")
        return jsonify(status='error', command=command, error=str(error)), error.status_code
    except KeyError as e:
        if progress_id:
            _set_map_progress(progress_id, 1, f"Missing field: {e}", status="error")
        return jsonify({"status": "error", "command": command,
                        "error": f"Missing field: {e}"}), 400
    except Exception as e:  # noqa: BLE001 - surface failures to the UI
        if progress_id:
            _set_map_progress(progress_id, 1, str(e), status="error")
        return jsonify({"status": "error", "command": command,
                        "error": str(e)}), 500

    # auto_map returns None on AI failure / unknown map type.
    if buffer is None:
        if progress_id:
            _set_map_progress(progress_id, 1, "Map generation failed.", status="error")
        return jsonify({"status": "error", "command": command,
                        "error": "Map generation failed. Check the server logs."}), 500

    if progress_id:
        _set_map_progress(progress_id, 4, "Finalizing map...", status="done")

    # Encode the PNG buffer as a data URL the browser can load directly.
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')

    resp_payload: dict[str, object] = {
        "status": "success",
        "command": command,
        "image_url": f"data:image/png;base64,{encoded}",
        "ai_cached": ai_cache_hit,
    }
    render_meta = getattr(buffer, "map_meta", None)
    if isinstance(render_meta, dict) and render_meta.get("rust_warning"):
        resp_payload["warning"] = render_meta["rust_warning"]
    ai_vals = render_meta.get("ai_answer") if isinstance(render_meta, dict) else None
    if isinstance(ai_vals, dict) and ai_vals:
        resp_payload["map_values"] = render_meta.get("raw_ai_answer", ai_vals)
        resp_payload["analysis"] = _build_analysis_from_ai(
            ai_vals,
            mode=str(data.get("mode") or "auto"),
            palette_theme=str(data.get("palette_theme") or "auto"),
            topic=str(data.get("topic") or ""),
            render_meta=render_meta,
        )

    return jsonify(resp_payload)


@app.route('/api/generate_idea', methods=['POST'])
def api_generate_idea():
    """Generate a map topic idea (and optionally matching parameters) using the selected AI model."""
    data = request.get_json(silent=True) or {}
    try:
        provider = _request_provider(data)
        api_key = _request_api_key(data, provider)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400

    model = str(data.get("model") or ('deepseek-flash' if provider == 'deepseek' else AVAILABLE_MODELS[0])).strip()
    if provider_for_model(model) != provider:
        return jsonify(status='error', error=f"Model '{model}' does not match provider '{provider}'."), 400

    count = data.get('count', 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5:
        return jsonify(status='error', error='Idea count must be an integer from 1 to 5.'), 400

    with_params = str(data.get("with_params") or "").strip().lower() in ("1", "true", "yes", "on") or data.get("with_params") is True

    temperature = None
    if "temperature" in data and str(data["temperature"]).strip() != "":
        try:
            temperature = validate_temperature(data["temperature"])
        except ValueError as err:
            return jsonify(status='error', error=str(err)), 400

    raw_avoid = data.get("avoid_ideas")
    avoid_ideas = [str(item).strip() for item in raw_avoid if str(item).strip()][:30] if isinstance(raw_avoid, list) else []
    raw_top = data.get("top_channel_topics")
    top_channel_topics = [str(item).strip() for item in raw_top if str(item).strip()][:10] if isinstance(raw_top, list) else []

    try:
        result = generate_map_idea(
            model,
            api_key=api_key,
            with_params=with_params,
            count=count,
            available_themes=list(_PALETTE_THEMES.keys()),
            available_fonts=_available_fonts(),
            temperature=temperature,
            avoid_ideas=avoid_ideas,
            top_channel_topics=top_channel_topics,
        )
    except AIServiceError as error:
        return jsonify(status='error', error=str(error)), error.status_code
    except Exception as exc:  # noqa: BLE001
        return jsonify(status='error', error=str(exc)), 500

    return jsonify({"status": "success", **result}), 200


@app.route('/api/models', methods=['POST'])
def list_available_models():
    """Return models available for the selected provider's API key."""
    data = request.get_json(silent=True) or {}
    try:
        provider = _request_provider(data)
    except ValueError as error:
        return jsonify(status='error', error=str(error), models=[]), 400
    if provider == 'deepseek':
        try:
            models = list_deepseek_models(_request_api_key(data, provider))
            return jsonify(status='success', models=models)
        except ValueError as error:
            return jsonify(status='error', error=str(error), models=[]), 400
        except AIServiceError as error:
            return jsonify(status='error', error=str(error), models=[]), error.status_code
    req_key = str(data.get("api_key") or "").strip()
    env_key = _server_api_key('gemini') or ''
    key = req_key or env_key
    if not key:
        return jsonify({"status": "error", "error": "API key required.", "models": []}), 400
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=15_000, retry_options=types.HttpRetryOptions(attempts=1),
        ))
        models: list[str] = []
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            if name.startswith("models/"):
                name = name[len("models/"):]
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            low = name.lower()
            if not (low.startswith("gemini") or low.startswith("gemma")):
                continue
            if any(skip in low for skip in (
                "embedding", "imagen", "veo", "tts", "aqa", "robotics", "computer-use",
                "image", "nano-banana", "omni", "transcribe", "live", "native-audio", "decision",
            )):
                continue
            models.append(name)
        return jsonify({"status": "success", "models": sorted(set(models), reverse=True)})
    except Exception as err:  # noqa: BLE001
        return jsonify({"status": "error", "error": str(err), "models": []}), 500


@app.route('/api/batch-template', methods=['GET'])
def download_batch_template():
    """Serve the downloadable batch CSV template."""
    template_path = Path(__file__).resolve().parent / "batch_template.csv"
    if not template_path.is_file():
        return jsonify({"status": "error", "error": "batch_template.csv not found."}), 404
    return send_file(
        template_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="batch_template.csv",
    )


@app.route('/api/download_batch_zip', methods=['POST'])
def download_batch_zip():
    """Bundle generated batch maps into a single ZIP archive named by topic."""
    import zipfile

    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"status": "error", "error": "No maps provided."}), 400

    buf = io.BytesIO()
    used_names: set[str] = set()
    added = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            image_url = str(item.get("image_url") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            if not image_url.startswith('data:image/png;base64,'):
                continue
            try:
                image_bytes = base64.b64decode(image_url.split(',', 1)[1], validate=True)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    if image.format != 'PNG':
                        continue
                    image.verify()
            except (ValueError, OSError):
                continue

            base = re.sub(r"[^\w\-]+", "_", prompt.strip().replace(" ", "_")).strip("_") if prompt else f"map_{idx:02d}"
            if not base:
                base = f"map_{idx:02d}"
            count = 1
            arc_name = f"{base}.png"
            while arc_name in used_names:
                count += 1
                arc_name = f"{base}_{count}.png"
            used_names.add(arc_name)
            zf.writestr(arc_name, image_bytes)
            added += 1

    if added == 0:
        return jsonify({"status": "error", "error": "No valid map files found to archive."}), 404

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="automap_batch.zip",
    )


@app.route('/api/parse_batch_csv', methods=['POST'])
def parse_batch_csv():
    """Parse an uploaded .csv ideas file and return the normalized idea rows."""
    if request.files and "file" in request.files:
        uploaded = request.files["file"]
        filename = uploaded.filename or ""
        ideas_text = uploaded.read().decode("utf-8", errors="replace")
    else:
        data = request.get_json(silent=True) or {}
        filename = str(data.get("filename") or "")
        ideas_text = str(data.get("ideas_text") or "")

    try:
        ideas = parse_ideas_content(ideas_text, filename=filename)
    except ValueError as err:
        return jsonify({"status": "error", "error": str(err)}), 400

    if not ideas:
        return jsonify({"status": "error", "error": "No valid map ideas found in the uploaded file."}), 400

    return jsonify({"status": "success", "ideas": ideas, "total": len(ideas)})


@app.route('/api/generate_batch/', methods=['POST'])
@app.route('/api/generate_batch', methods=['POST'])
def generate_batch():
    """Generate multiple maps from an uploaded .csv ideas file."""
    if request.files and "file" in request.files:
        uploaded = request.files["file"]
        filename = uploaded.filename or ""
        ideas_text = uploaded.read().decode("utf-8", errors="replace")
        data: dict[str, object] = dict(request.form)
    else:
        data = request.get_json(silent=True) or {}
        filename = str(data.get("filename") or "")
        ideas_text = str(data.get("ideas_text") or "")

    try:
        provider = _request_provider(data)
        api_key = _request_api_key(data, provider)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400

    try:
        ideas = parse_ideas_content(ideas_text, filename=filename)
    except ValueError as err:
        return jsonify({"status": "error", "error": str(err)}), 400

    if not ideas:
        return jsonify({"status": "error", "error": "No valid map ideas found in the uploaded file."}), 400

    default_font, default_eop, default_gsm = _parse_render_params(data)
    default_mode = str(data.get("mode") or "text").lower()
    default_map_type = str(data.get("map_type") or "default").strip().lower()
    if default_map_type not in _ALLOWED_MAP_TYPES:
        return jsonify({
            "status": "error",
            "error": f"Invalid map_type '{default_map_type}'. Allowed: {', '.join(sorted(_ALLOWED_MAP_TYPES))}.",
        }), 400
    default_model = str(data.get("model") or ('deepseek-flash' if provider == 'deepseek' else AVAILABLE_MODELS[0]))
    available_fonts = set(_available_fonts())

    temperature_options: dict[str, object] = {}
    if "temperature" in data and str(data["temperature"]).strip() != "":
        try:
            temperature_options["temperature"] = validate_temperature(data["temperature"])
        except ValueError as err:
            return jsonify({"status": "error", "error": str(err)}), 400

    base_style_kwargs: dict[str, object] = {}
    for style_key, kw_name in (
        ("filling_txt", "filling_txt_override"),
        ("filling_num", "filling_num_override"),
        ("label_style", "label_style"),
        ("auto_style", "auto_style"),
        ("palette_theme", "palette_theme"),
        ("label_suffix", "label_suffix"),
        ("show_legend", "show_legend"),
        ("highlight_extrema", "highlight_extrema"),
        ("highlight_high_color", "highlight_high_color"),
        ("highlight_low_color", "highlight_low_color"),
    ):
        if style_key in data:
            base_style_kwargs[kw_name] = data[style_key]

    results: list[dict[str, object]] = []
    succeeded = 0

    for idx, idea in enumerate(ideas, start=1):
        prompt = str(idea["prompt"])
        item_mode = str(idea.get("mode") or default_mode).lower()
        item_map_type = str(idea.get("map_type") or default_map_type).lower()
        item_gsm = int(idea.get("gsm", default_gsm))
        raw_eop = str(idea.get("extra_option") or default_eop or "none").lower()
        item_eop = None if raw_eop in ("none", "") else raw_eop
        item_model = str(idea.get("model") or default_model)
        item_font = str(idea.get("font") or default_font)
        if item_font not in available_fonts:
            item_font = default_font

        item_style_kwargs = dict(base_style_kwargs)
        if "palette_theme" in idea:
            item_style_kwargs["palette_theme"] = idea["palette_theme"]

        custom_out = str(idea.get("output") or "").strip()
        out_filename = Path(custom_out).name if custom_out else f"{idx:02d}_{slugify_prompt(prompt)}.png"
        if not out_filename.lower().endswith(".png"):
            out_filename += ".png"

        try:
            item_provider = provider_for_model(item_model)
            item_api_key = api_key if item_provider == provider else _server_api_key(item_provider)
            if not item_api_key and (item_provider == 'deepseek' or needs_api_key):
                raise ValueError(f'API key required for {item_provider}. Use one provider per CSV or configure its server key.')
            buffer, _hit = _auto_map_with_ai_cache(
                refresh=_wants_refresh(data),
                provider=item_provider,
                prompt=prompt,
                mode=item_mode,
                map_type=item_map_type,
                size_mod=item_gsm,
                optional_feature=item_eop,
                model=item_model,
                font_name=item_font,
                show_result=False,
                io_res=True,
                api_key=item_api_key,
                **temperature_options,
                **({"web_search": True, "max_search_requests": max(1, min(10, int(data.get("max_search_requests") or 5))), **({"tavily_api_key": str(data.get("tavily_api_key") or "").strip()} if data.get("tavily_api_key") else {})} if str(data.get("web_search") or "").strip().lower() in ("1", "true", "yes", "on") else {}),
                **item_style_kwargs,
            )
        except Exception as err:  # noqa: BLE001
            results.append({
                "index": idx,
                "prompt": prompt,
                "filename": out_filename,
                "status": "error",
                "error": str(err),
            })
            continue

        if buffer is None:
            results.append({
                "index": idx,
                "prompt": prompt,
                "filename": out_filename,
                "status": "error",
                "error": "Map generation failed.",
            })
            continue

        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        item_payload: dict[str, object] = {
            "index": idx,
            "prompt": prompt,
            "filename": out_filename,
            "status": "success",
            "image_url": f"data:image/png;base64,{encoded}",
            "mode": item_mode,
            "map_type": item_map_type,
            "gsm": item_gsm,
            "extra_option": item_eop or "none",
            "model": item_model,
            "provider": item_provider,
            "font": item_font,
            "palette_theme": item_style_kwargs.get("palette_theme", "auto"),
            "label_suffix": item_style_kwargs.get("label_suffix", ""),
            "show_legend": item_style_kwargs.get("show_legend", False),
            "highlight_extrema": item_style_kwargs.get("highlight_extrema", False),
            "highlight_high_color": item_style_kwargs.get("highlight_high_color", "#00e5ff"),
            "highlight_low_color": item_style_kwargs.get("highlight_low_color", "#ff2daa"),
        }
        if temperature_options:
            item_payload["temperature"] = temperature_options["temperature"]
        render_meta = getattr(buffer, "map_meta", None)
        ai_vals = render_meta.get("ai_answer") if isinstance(render_meta, dict) else None
        if isinstance(ai_vals, dict) and ai_vals:
            item_payload["map_values"] = render_meta.get("raw_ai_answer", ai_vals)
            item_payload["analysis"] = _build_analysis_from_ai(
                ai_vals,
                mode=item_mode,
                palette_theme=str(item_style_kwargs.get("palette_theme") or "auto"),
                topic=prompt,
                render_meta=render_meta,
            )
        results.append(item_payload)
        succeeded += 1

    return jsonify({
        "status": "success" if succeeded > 0 else "error",
        "total": len(ideas),
        "succeeded": succeeded,
        "results": results,
    }), (200 if succeeded > 0 else 500)


@app.route('/api/restyle_map/', methods=['POST'])
@app.route('/api/restyle_map', methods=['POST'])
def api_restyle_map():
    """Repaint the map from existing map_values without making another AI request."""
    data = request.get_json(silent=True) or {}
    map_values = data.get("map_values")
    if not isinstance(map_values, dict) or not map_values:
        return jsonify({
            "status": "error",
            "error": "No map_values provided. Generate a map first to re-paint it live.",
        }), 400

    font_name, extra_option, gsm = _parse_render_params(data)

    mode = str(data.get("mode") or "text")
    map_type = str(data.get("map_type") or "default").strip().lower()
    if map_type not in _ALLOWED_MAP_TYPES:
        return jsonify({
            "status": "error",
            "error": f"Invalid map_type '{map_type}'. Allowed: {', '.join(sorted(_ALLOWED_MAP_TYPES))}.",
        }), 400
    palette_theme = str(data.get("palette_theme") or "auto")
    auto_style = bool(data.get("auto_style", False))

    try:
        buffer = auto_map(
            prompt=str(data.get("topic") or ""),
            mode=mode,
            map_type=map_type,
            size_mod=gsm,
            optional_feature=extra_option,
            model=str(data.get("model") or AVAILABLE_MODELS[0]),
            font_name=font_name,
            show_result=False,
            io_res=True,
            api_key=None,
            filling_txt_override=data.get("filling_txt"),
            filling_num_override=data.get("filling_num"),
            label_style=data.get("label_style"),
            auto_style=auto_style,
            palette_theme=palette_theme,
            ai_answer_override=map_values,
            show_legend=bool(data.get("show_legend", False)),
            highlight_extrema=bool(data.get("highlight_extrema", False)),
            highlight_high_color=str(data.get("highlight_high_color") or "#00e5ff"),
            highlight_low_color=str(data.get("highlight_low_color") or "#ff2daa"),
            label_suffix=str(data.get("label_suffix") or "").replace("\r", "").replace("\n", "")[:16],
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"status": "error", "error": str(e)}), 500

    if buffer is None:
        return jsonify({"status": "error", "error": "Map repainting failed."}), 500

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    render_meta = getattr(buffer, "map_meta", None)
    analysis = _build_analysis_from_ai(
        map_values,
        mode=mode,
        palette_theme=palette_theme,
        topic=str(data.get("topic") or ""),
        render_meta=render_meta if isinstance(render_meta, dict) else None,
    )
    return jsonify({
        "status": "success",
        "image_url": f"data:image/png;base64,{encoded}",
        "map_values": map_values,
        "analysis": analysis,
    })


@app.route('/api/analyze_map/', methods=['POST'])
@app.route('/api/analyze_map', methods=['POST'])
def api_analyze_map():
    """Inspect uploaded map images, explicit map_values, or topic previews."""
    data = request.get_json(silent=True) or {}
    mode = str(data.get('mode') or 'auto')
    topic = str(data.get('topic') or '').strip()
    map_type = str(data.get('map_type') or 'default')
    palette_theme = str(data.get('palette_theme') or 'auto')

    map_values = data.get('map_values')
    image_data = data.get('image_data')

    if image_data is not None and (not isinstance(map_values, dict) or not map_values):
        try:
            analysis = _inspect_uploaded_map_image(
                image_data,
                map_type=map_type,
                palette_theme=palette_theme,
            )
        except ValueError as err:
            return jsonify({"status": "error", "error": str(err)}), 400
        return jsonify({
            "status": "success",
            "source": "palette_inspection",
            "topic": topic,
            "map_values": None,
            "analysis": analysis,
        })

    if isinstance(map_values, dict) and map_values:
        analysis = _build_analysis_from_ai(
            map_values,
            mode=mode,
            palette_theme=palette_theme,
            topic=topic,
        )
        return jsonify({
            "status": "success",
            "source": "ai_answer",
            "topic": topic,
            "map_values": map_values,
            "analysis": analysis,
        })

    t_low = topic.lower()
    if any(w in t_low for w in ("nicholas", "christmas", "santa", "holiday", "dec")):
        sample_values: dict[str, object] = {
            "ukraine": "6 Dec", "poland": "6 Dec", "germany": "6 Dec", "france": "6 Dec",
            "netherlands": "5 Dec", "russia": "19 Dec", "serbia": "19 Dec", "spain": "6 Jan",
        }
    elif mode == "text":
        sample_values = {
            "ukraine": "Yes", "poland": "Yes", "germany": "Yes", "france": "No",
            "spain": "Yes", "italy": "Partial", "united_kingdom": "No",
        }
    else:
        sample_values = {
            "germany": 84, "france": 68, "italy": 59, "spain": 48, "ukraine": 38,
            "poland": 37, "romania": 19, "netherlands": 18, "sweden": 10, "estonia": 1,
        }

    analysis = _build_analysis_from_ai(
        sample_values,
        mode=mode,
        palette_theme=palette_theme,
        topic=topic,
    )
    return jsonify({
        "status": "success",
        "source": "topic_preview",
        "topic": topic,
        "map_values": sample_values,
        "analysis": analysis,
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

    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({"status": "error", "error": "Missing 'url' in request body."}), 400

    cookies = str(data.get('cookies') or '').strip('\r\n')
    forward_payload = {"url": url, "cookies": cookies} if cookies else {"url": url}

    # Another service downloads music from YouTube and converts it to mp3.
    try:
        response = requests.post(
            f"{WORKER_URL}/api/music_download/",
            json=forward_payload,
            timeout=150,
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

@app.post('/api/music_upload')
def music_upload():
    """Normalize a user-uploaded audio file through the media worker."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('audio_file'), str) or not data['audio_file'].strip():
        return jsonify(status='error', error='Choose an audio file to upload.'), 400
    try:
        response = requests.post(
            f'{WORKER_URL}/api/music_upload',
            json={'audio_file': data['audio_file'], 'filename': str(data.get('filename') or 'uploaded-audio')},
            timeout=90,
        )
        return jsonify(response.json()), response.status_code
    except requests.RequestException:
        return jsonify(status='error', error='Audio upload service is unavailable. Please try again.'), 502
    except ValueError:
        return jsonify(status='error', error='Audio upload service returned an invalid response.'), 502


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
    # Keep audio cuts within the supported 1–30 second range before forwarding.
    duration = max(1, min(30, duration))
    payload = {'start_time': start_time, 'title': title, 'duration': duration}
    if data.get('audio_file'):
        payload['audio_file'] = data['audio_file']

    # The worker owns the downloaded track and performs the cut in its storage.
    try:
        response = requests.post(
            f"{WORKER_URL}/api/music_cut/",
            json=payload,
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


def _render_video_request_data():
    """Return render settings from JSON or from a binary multipart upload.

    Multipart form: a ``payload`` field holding the JSON settings object plus
    file parts named like the JSON fields (``map_file``, ``background_video``,
    ``audio_file``, ...). Files are converted to the data URLs the worker
    expects, so the browser avoids base64 encoding and the ~33% upload overhead.
    """
    if not request.files and 'payload' not in request.form:
        return request.get_json(silent=True) or {}
    try:
        data = json.loads(request.form.get('payload') or '{}')
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for field, storage in request.files.items():
        raw = storage.read()
        if not raw:
            continue
        mime = storage.mimetype or 'application/octet-stream'
        data[field] = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return data


_ZOOM_REF_DIMENSIONS: dict[str, tuple[float, float]] = {
    "europe": (3900.0, 4104.0),
    "world": (3424.0, 2027.0),
}
_ZOOM_OUTLINE_CACHE: dict[tuple[str, str], bytes] = {}
_REVEAL_MASK_CACHE: dict[tuple[str, str, int, int], str] = {}
_ZOOM_CACHE_LOCK = threading.Lock()


def _zoom_country_entries(
    coords: dict[str, list[tuple[int, int]]],
    ref_w: float,
    ref_h: float,
) -> list[dict[str, object]]:
    """Build normalized (0..1) center coordinate entries for a country map."""
    return [
        {
            "key": k,
            "name": k.replace("_", " ").title(),
            "x": round(pts[0][0] / ref_w, 4),
            "y": round(pts[0][1] / ref_h, 4),
        }
        for k, pts in coords.items()
        if pts
    ]


def _region_mask_from_index(
    map_path: Path,
    coords: dict[str, list[tuple[int, int]]],
    key: str,
) -> Image.Image | None:
    """Extract a full-resolution L-mode binary mask using ``_region_index``."""
    import numpy as np
    from map.auto_map import _load_rgba, _region_index

    if key not in coords:
        return None
    region_idx = list(coords.keys()).index(key) + 1
    base = _load_rgba(map_path)
    w, h = base.size
    flat, ids = _region_index(map_path, base, coords)
    pix_mask = np.zeros(w * h, dtype=np.uint8)
    pix_mask[flat[ids == region_idx]] = 255
    if not pix_mask.any():
        return None
    return Image.fromarray(pix_mask.reshape((h, w)), mode="L")


def _world_country_floodfill_mask(seeds: list[tuple[int, int]]) -> Image.Image | None:
    """Extract a full-resolution L-mode binary mask on ``world_countries_map.png``."""
    import numpy as np
    from map.auto_map import _floodfill_bool_mask, _load_rgba

    if not seeds:
        return None
    maps_dir = BASE_DIR / "resources" / "maps"
    map_path = maps_dir / "world_countries_map.png"
    base = _load_rgba(map_path)
    w, h = base.size
    base_arr = np.asarray(base)
    wc_base_alpha = base_arr[..., 3]
    wc_dark = (wc_base_alpha >= 128) & (base_arr[..., :3].max(axis=2) <= 12)
    wc_interior_crisp = (wc_base_alpha >= 128) & ~wc_dark
    wc_interior = wc_interior_crisp

    wc_border_alpha = None
    borders_path = maps_dir / "world_countries_map_blured.png"
    if borders_path.is_file():
        borders = _load_rgba(borders_path)
        if borders.size == (w, h):
            wc_border_alpha = np.asarray(borders)[..., 3]
            # Only trust blurred border alpha to bridge inland black flag bands
            # when the border asset actually covers the crisp borders.
            if not wc_dark.any() or float(np.mean(wc_border_alpha[wc_dark] >= 20)) >= 0.8:
                wc_interior = wc_interior_crisp | ((wc_base_alpha >= 128) & (wc_border_alpha < 44))

    max_px = int(0.08 * w * h)
    filled = _floodfill_bool_mask(wc_interior, seeds, max_px)
    if (filled is None or not filled.any()) and wc_border_alpha is not None:
        for thr in (45, 65):
            interior = (wc_base_alpha >= 128) & (wc_border_alpha < thr)
            filled = _floodfill_bool_mask(interior, seeds, max_px)
            if filled is not None and filled.any():
                break
    if filled is None or not filled.any():
        return None
    return Image.fromarray((filled.astype(np.uint8) * 255), mode="L")


def _extract_country_mask_image(
    map_kind: str,
    key: str,
    *,
    allow_world_continent_fallback: bool = False,
) -> Image.Image | None:
    """Return a full-resolution L-mode binary mask for ``key`` on ``europe`` or ``world``."""
    from map.constants import europe_coords, world_coords
    from map.world_countries_coords import world_countries_coords

    maps_dir = BASE_DIR / "resources" / "maps"
    if map_kind == "europe":
        return _region_mask_from_index(maps_dir / "default_map.png", europe_coords, key)
    if map_kind == "world":
        if key in world_countries_coords:
            return _world_country_floodfill_mask(world_countries_coords[key])
        if allow_world_continent_fallback and key in world_coords:
            return _region_mask_from_index(maps_dir / "world_map.png", world_coords, key)
    return None


def _resolve_reveal_country_key(
    coords: dict[str, list[tuple[int, int]]],
    ref_w: float,
    ref_h: float,
    zoom_country_key: str,
    zoom_x: float,
    zoom_y: float,
) -> str:
    """Resolve target country key by exact match or nearest seed within 0.18 distance."""
    candidate = str(zoom_country_key or "").strip().lower()
    if candidate and candidate in coords and coords[candidate]:
        return candidate
    if isinstance(zoom_x, bool) or isinstance(zoom_y, bool):
        return ""
    try:
        zx = float(zoom_x)
        zy = float(zoom_y)
    except (TypeError, ValueError):
        return ""
    if not (math.isfinite(zx) and math.isfinite(zy)):
        return ""
    best_key = ""
    best_dist_sq = 0.18 * 0.18
    for k, pts in coords.items():
        if not pts:
            continue
        dx = (pts[0][0] / ref_w) - zx
        dy = (pts[0][1] / ref_h) - zy
        dist_sq = dx * dx + dy * dy
        if dist_sq <= best_dist_sq:
            best_dist_sq = dist_sq
            best_key = k
    return best_key


def _extract_country_reveal_mask(
    map_w: int,
    map_h: int,
    zoom_country_key: str,
    zoom_x: float,
    zoom_y: float,
) -> str:
    """Extract a country reveal mask as a base64 L-mode PNG resized to ``(map_w, map_h)``."""
    map_w = int(map_w)
    map_h = int(map_h)
    if map_w <= 0 or map_h <= 0:
        return ""

    from map.constants import europe_coords
    from map.world_countries_coords import world_countries_coords

    map_kind = "europe" if (map_w / max(1, map_h)) < 1.25 else "world"
    coords = europe_coords if map_kind == "europe" else world_countries_coords
    ref_w, ref_h = _ZOOM_REF_DIMENSIONS[map_kind]

    key = _resolve_reveal_country_key(
        coords, ref_w, ref_h, zoom_country_key, zoom_x, zoom_y
    )
    if not key:
        return ""

    cache_key = (map_kind, key, map_w, map_h)
    with _ZOOM_CACHE_LOCK:
        cached = _REVEAL_MASK_CACHE.get(cache_key)
    if cached is not None:
        return cached

    mask = _extract_country_mask_image(map_kind, key, allow_world_continent_fallback=False)
    if mask is None:
        return ""
    if mask.size != (map_w, map_h):
        mask = mask.resize((map_w, map_h), Image.Resampling.BILINEAR)
    if mask.mode != "L":
        mask = mask.convert("L")
    from PIL import ImageFilter
    mask = mask.filter(ImageFilter.MaxFilter(5)).point(lambda p: 255 if p > 64 else 0)
    if mask.getbbox() is None:
        return ""

    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    b64_mask = base64.b64encode(buf.getvalue()).decode("ascii")
    with _ZOOM_CACHE_LOCK:
        _REVEAL_MASK_CACHE[cache_key] = b64_mask
    return b64_mask


def _parse_finite_coord(value: object, default: float) -> float:
    """Parse a finite float coordinate, falling back to ``default`` when invalid."""
    if value is None or isinstance(value, bool) or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _decode_image_size(image_data: object) -> tuple[int, int] | None:
    """Decode a base64 or data-URL image payload and return its ``(width, height)``."""
    if not isinstance(image_data, str) or not image_data.strip():
        return None
    raw_b64 = image_data.strip()
    if "," in raw_b64 and raw_b64.lower().startswith("data:"):
        _, raw_b64 = raw_b64.split(",", 1)
    b64_part = "".join(raw_b64.split())
    if not b64_part:
        return None
    try:
        decoded = base64.b64decode(b64_part, validate=True)
    except (ValueError, TypeError):
        return None
    if not decoded:
        return None
    try:
        with Image.open(io.BytesIO(decoded)) as opened:
            return opened.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        return None


@app.get('/api/zoom_countries')
def api_zoom_countries():
    """Return normalized (0..1) country center coordinates for Europe and World maps."""
    from map.constants import europe_coords, world_coords
    from map.world_countries_coords import world_countries_coords

    europe = _zoom_country_entries(europe_coords, *_ZOOM_REF_DIMENSIONS["europe"])
    world_source = world_countries_coords if world_countries_coords else world_coords
    world = _zoom_country_entries(world_source, *_ZOOM_REF_DIMENSIONS["world"])
    return jsonify({"europe": europe, "world": world})


@app.get('/api/zoom_country_outline')
def api_zoom_country_outline():
    """Return a small transparent PNG highlighting the hovered country's outline."""
    from PIL import ImageChops, ImageFilter

    map_kind = str(request.args.get('map') or 'europe').strip().lower()
    key = str(request.args.get('key') or '').strip().lower()
    solid = str(request.args.get('solid') or '').strip() in ('1', 'true', 'yes')
    if not key or map_kind not in ('europe', 'world'):
        return ('', 404)

    cache_key = (map_kind, key, solid)
    with _ZOOM_CACHE_LOCK:
        cached = _ZOOM_OUTLINE_CACHE.get(cache_key)
    if cached is not None:
        resp = Response(cached, mimetype='image/png')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp

    mask = _extract_country_mask_image(
        map_kind, key, allow_world_continent_fallback=True
    )
    if mask is None:
        return ('', 404)

    w, h = mask.size
    pw = 720 if solid else 360
    ph = max(1, int(round(h * (pw / w))))
    small_mask = mask.resize((pw, ph), Image.Resampling.BILINEAR)
    if solid:
        alpha = small_mask.filter(ImageFilter.MaxFilter(5)).point(lambda p: 255 if p > 64 else 0)
        out = Image.new('RGBA', (pw, ph), (0, 0, 0, 255))
    else:
        edge = ImageChops.subtract(small_mask.filter(ImageFilter.MaxFilter(5)), small_mask.filter(ImageFilter.MinFilter(3)))
        fill_alpha = small_mask.point(lambda p: 45 if p > 128 else 0)
        alpha = ImageChops.lighter(fill_alpha, edge.point(lambda p: min(255, int(p * 1.2))))
        out = Image.new('RGBA', (pw, ph), (252, 211, 77, 255))
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, format='PNG')
    png_bytes = buf.getvalue()
    with _ZOOM_CACHE_LOCK:
        _ZOOM_OUTLINE_CACHE[cache_key] = png_bytes

    resp = Response(png_bytes, mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


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

    data = _render_video_request_data()
    if data is None:
        return jsonify(status='error', error='Invalid multipart payload field.'), 400

    youtube_body = None
    if data.get('youtube') is not None:
        if not youtube_uploads.valid_csrf():
            return jsonify(code='youtube_csrf_invalid', error='Invalid YouTube request token. Reload the page.'), 403
        try:
            youtube_body = youtube_uploads.check_ready(data['youtube'])
        except ValueError as error:
            return jsonify(status='error', error=str(error)), 400

    # The frontend sends base64 strings; keep them as-is (default to empty string).
    map_b64 = data.get('map_file') or ''
    background_b64 = data.get('background_video') or ''
    # Frontend field is "video_title"; accept legacy "title" as a fallback.
    title = str(data.get('video_title') or data.get('title') or '').strip()
    codec = str(data.get('codec') or 'libx264').strip()
    duration = data.get('duration')

    raw_country_key = data.get('zoom_country_key')
    zoom_country_key = raw_country_key.strip() if isinstance(raw_country_key, str) else ''
    raw_reveal = data.get('reveal_enabled', False)
    reveal_enabled = (
        raw_reveal.strip().lower() in _TRUTHY
        if isinstance(raw_reveal, str)
        else bool(raw_reveal)
    )
    raw_zoom = data.get('zoom_enabled', False)
    zoom_enabled = raw_zoom.strip().lower() in _TRUTHY if isinstance(raw_zoom, str) else bool(raw_zoom)
    reveal_enabled = zoom_enabled and reveal_enabled
    reveal_time = data.get('reveal_time', None)
    reveal_mask = ''
    if reveal_enabled and map_b64:
        map_size = _decode_image_size(map_b64)
        if map_size is not None:
            zoom_x = _parse_finite_coord(data.get('zoom_x'), 0.80)
            zoom_y = _parse_finite_coord(data.get('zoom_y'), 0.35)
            reveal_mask = _extract_country_reveal_mask(
                map_size[0],
                map_size[1],
                zoom_country_key,
                zoom_x,
                zoom_y,
            )

    try:
        response = requests.post(
            f"{WORKER_URL}/api/render_video/",
            json={
                "map_file": map_b64,
                "background_video": background_b64,
                "video_title": title,
                "codec": codec,
                "duration": duration,
                "audio_title": data.get('audio_title', ''),
                "audio_file": data.get('audio_file', ''),
                "include_audio": bool(data.get('audio_title') or data.get('audio_file')),
                "title_font": data.get('title_font', 'DIN Condensed.ttf'),
                "title_font_size": data.get('title_font_size', 100),
                "title_first_line_color": data.get('title_first_line_color', '#ffffff'),
                "title_other_lines_color": data.get('title_other_lines_color', '#ffe735'),
                "title_uppercase": data.get('title_uppercase', True),
                "visual_effects": data.get('visual_effects', True),
                "watermark_image": data.get('watermark_image', ''),
                "watermark_text": data.get('watermark_text', ''),
                "watermark_color": data.get('watermark_color', '#a4e9ee'),
                "watermark_font": data.get('watermark_font', 'DIN Condensed.ttf'),
                "watermark_stars": data.get('watermark_stars', False),
                "watermark_stars_image": data.get('watermark_stars_image', ''),
                "top_left_sticker": data.get('top_left_sticker', 'none'),
                "top_right_sticker": data.get('top_right_sticker', 'none'),
                "top_left_sticker_image": data.get('top_left_sticker_image', ''),
                "top_right_sticker_image": data.get('top_right_sticker_image', ''),
                "subscribe_badge": data.get('subscribe_badge', False),
                "subscribe_image": data.get('subscribe_image', ''),
                "subscribe_text": data.get('subscribe_text', 'Subscribe!'),
                "subscribe_delay": data.get('subscribe_delay', 2.5),
                "intro_fade": data.get('intro_fade', False),
                "map_scale": data.get('map_scale', 1.0),
                "map_offset_x": data.get('map_offset_x', 0.0),
                "zoom_enabled": zoom_enabled,
                "zoom_x": data.get('zoom_x', 0.80),
                "zoom_y": data.get('zoom_y', 0.35),
                "zoom_scale": data.get('zoom_scale', 2.0),
                "zoom_start": data.get('zoom_start', 1.5),
                "zoom_duration": data.get('zoom_duration', 2.5),
                "zoom_out_enabled": data.get('zoom_out_enabled', False),
                "zoom_out_duration": data.get('zoom_out_duration', 2.0),
                "zoom_country_key": zoom_country_key,
                "reveal_enabled": reveal_enabled,
                "reveal_time": reveal_time,
                "reveal_color": str(data.get('reveal_color') or '#000000').strip(),
                "reveal_mask": reveal_mask,
                "reveal_clean_map_file": data.get('reveal_clean_map_file', '') if reveal_mask else '',
                **({'render_id': data['render_id']} if data.get('render_id') is not None else {}),
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

    if response.ok and isinstance(payload, dict) and payload.get('status') == 'success':
        path = payload.get('file_path') or payload.get('path') or payload.get('file')
        if isinstance(path, str) and path:
            payload['upload_token'] = youtube_uploads.render_token(path)
            if youtube_body is not None:
                try:
                    payload['youtube_job_id'] = youtube_uploads.start(payload['upload_token'], youtube_body)
                except (ValueError, ImportError):
                    payload['youtube_error'] = 'Video rendered, but YouTube upload could not start. Check your connection and try Upload to YouTube.'
    return jsonify(payload), response.status_code


@app.get('/api/render_progress/<uuid:render_id>')
def video_render_progress(render_id):
    try:
        response = requests.get(f'{WORKER_URL}/api/render_progress/{render_id}', timeout=5)
    except requests.RequestException:
        return jsonify(error='Render progress is temporarily unavailable.'), 502
    try:
        result = jsonify(response.json())
        result.headers['Cache-Control'] = 'no-store'
        return result, response.status_code
    except ValueError:
        return jsonify(error='Render service returned invalid progress.'), 502
    finally:
        response.close()


@app.route('/api/download_video', methods=['GET'])
@app.route('/api/download_video/', methods=['GET'])
def download_video():
    """Stream a rendered video from the downstream render service.

    The browser hits this endpoint on the web origin, but the rendered .mp4
    lives in the worker container's storage, so we proxy the download to the
    worker and stream its response back as an attachment.
    """
    from flask import Response

    raw_path = (request.args.get('path') or '').strip()
    if not raw_path:
        return jsonify({"status": "error", "error": "Missing 'path' query param."}), 400

    try:
        upstream = requests.get(
            f"{WORKER_URL}/api/download_video",
            params={"path": raw_path},
            stream=True,
            timeout=120,
        )
    except requests.RequestException as e:
        return jsonify({"status": "error",
                        "error": f"Video service unreachable: {e}"}), 502

    # If the worker did not return a file (e.g. 404/400 JSON error), pass it through.
    if upstream.status_code != 200:
        try:
            content_type = upstream.headers.get("Content-Type", "")
            if "application/json" in content_type:
                try:
                    return jsonify(upstream.json()), upstream.status_code
                except ValueError:
                    pass
            return (upstream.text or "Download failed"), upstream.status_code
        finally:
            upstream.close()

    disposition = upstream.headers.get(
        "Content-Disposition",
        'attachment; filename="rendered_video.mp4"',
    )

    def _generate():
        # Stream in chunks so large videos don't load fully into memory.
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    response = Response(
        _generate(),
        mimetype="video/mp4",
        headers={"Content-Disposition": disposition},
    )
    response.call_on_close(upstream.close)
    return response


@app.context_processor
def _inject_agent_url() -> dict[str, str]:
    return {"agent_url": (os.environ.get("AGENT_URL") or "http://localhost:4000").strip().rstrip("/")}


if __name__ == "__main__":
    from server_runtime import run_app

    run_app(app)
