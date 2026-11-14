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

import ast
import base64
import io
import os
import re
import time

from pathlib import Path
from PIL import Image, UnidentifiedImageError
import requests

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
    _parse_short_number,
    _resolve_theme_name,
    _to_rgba_tuple,
)
from map.constants import europe_coords, world_coords

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
            'http://yt-dlp-worker:8080/api/video_title_preview',
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
    import requests

    try:
        response = requests.get('http://yt-dlp-worker:8080/api/codecs', timeout=20)
        return jsonify(response.json()), response.status_code
    except (requests.RequestException, ValueError):
        return jsonify(error='Could not check render worker codecs. Use libx264 (CPU).'), 502


@app.route('/api/status')
def api_status():
    """Report whether the server already has a usable Gemini API key.

    Response:
        { "needs_api_key": true }  -> the UI must ask the user for a key
    """
    return jsonify({"needs_api_key": needs_api_key})


def _build_analysis_from_ai(
    ai_answer: dict[str, object],
    mode: str = "auto",
    palette_theme: str = "auto",
    topic: str = "",
    render_meta: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build UI-friendly color rules and stats from AI map values or render metadata."""
    vals = [v for v in ai_answer.values() if v is not None and str(v).strip() != ""]
    num_vals: list[int] = []
    for v in vals:
        parsed = _parse_short_number(v)
        if parsed is not None:
            num_vals.append(int(round(parsed)))

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
            for k, v in raw_num.items():
                rgba = _to_rgba_tuple(v)
                if rgba is None:
                    continue
                try:
                    b = k if isinstance(k, tuple) else ast.literal_eval(str(k))
                    if isinstance(b, (list, tuple)) and len(b) == 2:
                        num_dict[(int(float(b[0])), int(float(b[1])))] = rgba
                except (ValueError, SyntaxError, TypeError):
                    continue

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

    ref_coords = world_coords if map_type == "world" else europe_coords
    base_name = "world_map.png" if map_type == "world" else "default_map.png"
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
    except (TypeError, ValueError):
        gsm = 15

    return str(font_name), extra_option, gsm


@app.route('/api/generate/', methods=['POST'])
@app.route('/api/generate', methods=['POST'])
def generate():
    """Generate a map from the JSON body and return it as a data URL.

    Request JSON:
        { "topic", "mode", "map_type", "gsm", "extra_option", "model", "font" }

    Response JSON:
        { "status": "success", "command": "...", "image_url": "data:image/png;base64,..." }
    """
    data = request.get_json(silent=True) or {}

    # Optional user-supplied key (used only when .env has none).
    api_key = (data.get('api_key') or '').strip() or None
    if needs_api_key and not api_key:
        return jsonify({"status": "error",
                        "error": "API key required. Provide GEMINI_API_KEY via .env or the web form."}), 400

    # Validate the requested font against what is actually on disk, otherwise
    # auto_map would raise FileNotFoundError when loading the TrueType file.
    font_name, extra_option, gsm = _parse_render_params(data)

    # Rebuild the equivalent CLI command for display only; it is never executed.
    command = (
        f"python cli.py make \"{data.get('topic', '')}\" "
        f"-m {data.get('mode', 'text')} -mp {data.get('map_type', 'default')} "
        f"-gsm {gsm} -eop {data.get('extra_option', 'none')} "
        f"--model {data.get('model', 'gemini-2.5-flash')} --font \"{font_name}\""
    )

    style_kwargs: dict[str, object] = {}
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
            **style_kwargs,
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

    resp_payload: dict[str, object] = {
        "status": "success",
        "command": command,
        "image_url": f"data:image/png;base64,{encoded}",
    }
    render_meta = getattr(buffer, "map_meta", None)
    ai_vals = render_meta.get("ai_answer") if isinstance(render_meta, dict) else None
    if isinstance(ai_vals, dict) and ai_vals:
        resp_payload["map_values"] = ai_vals
        resp_payload["analysis"] = _build_analysis_from_ai(
            ai_vals,
            mode=str(data.get("mode") or "auto"),
            palette_theme=str(data.get("palette_theme") or "auto"),
            topic=str(data.get("topic") or ""),
            render_meta=render_meta,
        )

    return jsonify(resp_payload)


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
    map_type = str(data.get("map_type") or "default")
    palette_theme = str(data.get("palette_theme") or "auto")
    auto_style = bool(data.get("auto_style", False))

    try:
        buffer = auto_map(
            prompt=str(data.get("topic") or ""),
            mode=mode,
            map_type=map_type,
            size_mod=gsm,
            optional_feature=extra_option,
            model=str(data.get("model") or "gemini-2.5-flash"),
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
    # Keep audio cuts within the supported 1–30 second range before forwarding.
    duration = max(1, min(30, duration))

    # The worker owns the downloaded track and performs the cut in its storage.
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
