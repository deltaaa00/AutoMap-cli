"""Adapt browser media requests to the separate private render worker.

These routes forward title previews, music download/upload/cuts, video renders,
progress polls and streamed MP4 downloads. JSON settings come from webapp.inputs;
multipart files are encoded into the worker's data-URL contract here. The worker
owns media files and FFmpeg/MoviePy processing; this module owns HTTP errors,
proxy timeouts and closing streamed responses.

Country reveal masks are prepared by webapp.zoom. A successful render can receive
a session-bound upload token and start a YouTube upload via the app extension.
Rendering success must remain usable even if the subsequent upload fails.
"""
import base64
import json
from flask import Blueprint, current_app, request, jsonify, Response
from requests import RequestException
from .http_client import worker_http, worker_url
from .inputs import request_data, _TRUTHY
from .zoom import _decode_image_size, _extract_country_reveal_mask, _parse_finite_coord


routes = Blueprint('media', __name__)


@routes.route('/api/video_title_preview', methods=['POST'])
def video_title_preview():
    """Proxy title/watermark/sticker preview requests to the yt-dlp render worker."""

    data = request_data()
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
        response = worker_http.post(
            f'{worker_url()}/api/video_title_preview',
            json={key: data[key] for key in allowed_keys if key in data},
            timeout=15,
        )
    except RequestException:
        return jsonify(error='Title preview is unavailable. Please try again.'), 502
    # Preserve the worker's image content type for successful preview responses.
    return Response(response.content, status=response.status_code,
                    content_type=response.headers.get('Content-Type', 'application/json'))


@routes.route('/api/music_download/', methods=['POST'])
@routes.route('/api/music_download', methods=['POST'])
def music_download():
    """Download a track via the downstream render/yt-dlp service.

    Request JSON:
        { "url": "https://music.youtube.com/watch?v=..." }

    Response JSON (passthrough from the downstream service):
        { "status": "success", "file": "<base64 mp3>", "title": "<song title>" }
        { "status": "error", "error": "<message>" }
    """

    data = request_data()
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({"status": "error", "error": "Missing 'url' in request body."}), 400

    cookies = str(data.get('cookies') or '').strip('\r\n')
    forward_payload = {"url": url, "cookies": cookies} if cookies else {"url": url}

    # Another service downloads music from YouTube and converts it to mp3.
    try:
        response = worker_http.post(
            f"{worker_url()}/api/music_download/",
            json=forward_payload,
            timeout=150,
        )
    except RequestException as e:
        return jsonify({"status": "error",
                        "error": f"Music service unreachable: {e}"}), 502

    try:
        payload = response.json()
    except ValueError:
        return jsonify({"status": "error",
                        "error": "Music service returned a non-JSON response."}), 502

    # Pass the downstream payload/status straight through.
    return jsonify(payload), response.status_code


@routes.post('/api/music_upload')
def music_upload():
    """Normalize a user-uploaded audio file through the media worker."""
    data = request_data()
    if not isinstance(data.get('audio_file'), str) or not data['audio_file'].strip():
        return jsonify(status='error', error='Choose an audio file to upload.'), 400
    try:
        response = worker_http.post(
            f'{worker_url()}/api/music_upload',
            json={'audio_file': data['audio_file'], 'filename': str(data.get('filename') or 'uploaded-audio')},
            timeout=90,
        )
        return jsonify(response.json()), response.status_code
    except RequestException:
        return jsonify(status='error', error='Audio upload service is unavailable. Please try again.'), 502
    except ValueError:
        return jsonify(status='error', error='Audio upload service returned an invalid response.'), 502


@routes.route('/api/music_cut', methods=['POST'])
@routes.route('/api/music_cut/', methods=['POST'])
def cut_audio() -> tuple[object, int]:
    """Cut a previously downloaded track via the downstream render service.

    Request JSON:
        { "start_time": "00:01:30", "title": "<song title>", "duration": 8 }

    Response JSON (passthrough from the downstream service):
        { "status": "success", "file": "<base64 mp3>", "title": "<song title>" }
        { "status": "error", "error": "<message>" }
    """

    data: dict[str, object] = request_data()
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
        response = worker_http.post(
            f"{worker_url()}/api/music_cut/",
            json=payload,
            timeout=40,
        )

    except RequestException as e:
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
        return request_data()
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


@routes.route('/api/render_video', methods=['POST'])
@routes.route('/api/render_video/', methods=['POST'])
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
    youtube_uploads = current_app.extensions['youtube_uploads']

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
        response = worker_http.post(
            f"{worker_url()}/api/render_video/",
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

    except RequestException as e:
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


@routes.get('/api/render_progress/<uuid:render_id>')
def video_render_progress(render_id):
    try:
        response = worker_http.get(f'{worker_url()}/api/render_progress/{render_id}', timeout=5)
    except RequestException:
        return jsonify(error='Render progress is temporarily unavailable.'), 502
    try:
        result = jsonify(response.json())
        result.headers['Cache-Control'] = 'no-store'
        return result, response.status_code
    except ValueError:
        return jsonify(error='Render service returned invalid progress.'), 502
    finally:
        response.close()


@routes.route('/api/download_video', methods=['GET'])
@routes.route('/api/download_video/', methods=['GET'])
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
        upstream = worker_http.get(
            f"{worker_url()}/api/download_video",
            params={"path": raw_path},
            stream=True,
            timeout=120,
        )
    except RequestException as e:
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
