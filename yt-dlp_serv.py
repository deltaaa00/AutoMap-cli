import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

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

def cleanup_old_files(max_age_seconds=1800):
    """Delete old files"""
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

        match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', response.text)
        if match:
            return match.group(1)

        match_title = re.search(r'<title>(.*?)</title>', response.text)
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
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({"status": "error", "error": "Missing 'url' in request body."}), 400

    # yt-dlp with `-x` writes to a temp file, so pipe through the temp dir and
    # read the produced .mp3 back. `-o -` is unreliable when extracting audio.

    download_dir = tempfile.mkdtemp(prefix="automap_download_")
    command = [
        "yt-dlp",
        "-f", "ba[ext=m4a]/ba",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", os.path.join(download_dir, "track.%(ext)s"),
        url,
    ]

    try:
        subprocess.run(command, capture_output=True, check=True)

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

        import uuid
        title = _get_song_title(url) or str(uuid.uuid4())
        title = re.sub(r'[^\w\-]', '_', title)

        with open(mp3_path, "rb") as fh:
            base64_audio = base64.b64encode(fh.read()).decode("utf-8")
            new_mp3_path = os.path.join(DOWNLOADS, f"{title}.mp3")
            os.rename(mp3_path, new_mp3_path)

        return jsonify({
            "status": "success",
            "file": base64_audio,
            "title": title,
        }), 200

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode("utf-8", errors="ignore").strip()
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
        if not isinstance(filename, str) or not re.fullmatch(r'[\w\-]+', filename):
            return jsonify({"status": "error", "error": "Invalid track title."}), 400

        raw_duration = data.get('duration', 8)
        try:
            duration = int(raw_duration) if raw_duration is not None else 8
        except (TypeError, ValueError):
            duration = 8
        duration = max(1, min(30, duration))

        os.makedirs(CUT_DIR, exist_ok=True)

        # Parsing time
        minutes, seconds = time.split(":")
        minutes, seconds = int(minutes), int(seconds)

        # Converting to ms
        start_ms = (minutes * 60 + seconds) * 1000

        # Audio clip duration (1..30 seconds, default 8 seconds)
        end_ms = start_ms + (duration * 1000)

        from pydub import AudioSegment

        audio = AudioSegment.from_file(os.path.join(DOWNLOADS, f"{filename}.mp3"))

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

TITLE_FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "resources", "fonts", "DIN Condensed.ttf",
)


def _title_settings(data):
    font_name = str(data.get('title_font') or 'DIN Condensed.ttf')
    font_dir = os.path.dirname(TITLE_FONT_PATH)
    fonts = {name for name in os.listdir(font_dir) if name.lower().endswith(('.ttf', '.otf'))}
    if font_name not in fonts:
        raise ValueError('Unknown title font.')
    raw_size = data.get('title_font_size', 100)
    if isinstance(raw_size, bool):
        raise ValueError('Title size must be a number between 48 and 144.')
    try:
        size = int(raw_size)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Title size must be a number between 48 and 144.')
    if not 48 <= size <= 144:
        raise ValueError('Title size must be between 48 and 144.')
    return os.path.join(font_dir, font_name), size


def _title_colors(data):
    colors = (data.get('title_first_line_color', '#ffffff'),
              data.get('title_other_lines_color', '#a7f3d0'))
    if any(not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color) for color in colors):
        raise ValueError('Title colors must use #RRGGBB format.')
    return colors


def _title_image(title: str, max_width: int, font_path=TITLE_FONT_PATH, font_size=100,
                 first_line_color='#ffffff', other_lines_color='#a7f3d0'):
    """Lay out a centered, wrapped title with safe margins and a soft shadow."""
    import numpy as np
    from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

    padding = 24
    text_width = max_width - padding * 2
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    # Shrink long titles to keep the map visible. Character wrapping also handles
    # URLs and words that are wider than the available space.
    for fitted_size in range(font_size, 19, -4):
        font = ImageFont.truetype(font_path, fitted_size)
        lines = []
        for paragraph in title.splitlines() or [title]:
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}".strip()
                if measure.textlength(candidate, font=font) <= text_width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                line = ""
                for char in word:
                    if line and measure.textlength(line + char, font=font) > text_width:
                        lines.append(line)
                        line = ""
                    line += char
            lines.append(line)
        text = "\n".join(lines)
        spacing = round(fitted_size * 0.22)
        bounds = measure.multiline_textbbox((0, 0), text, font=font,
                                            spacing=spacing, align="center", stroke_width=1)
        height = bounds[3] - bounds[1]
        if height <= 360:
            break
    if height > 360:
        raise ValueError("Video title is too long. Please shorten it.")

    size = (max_width, height + padding * 2)
    shadow = Image.new("RGBA", size)
    xy = (max_width / 2, padding - bounds[1])
    ImageDraw.Draw(shadow).multiline_text(
        (xy[0], xy[1] + 5), text, font=font, anchor="ma", align="center",
        spacing=spacing, fill=(0, 0, 0, 230), stroke_width=3,
    )
    image = shadow.filter(ImageFilter.GaussianBlur(8))
    draw = ImageDraw.Draw(image)
    first_rgb = ImageColor.getrgb(first_line_color)
    other_rgb = ImageColor.getrgb(other_lines_color)
    # Preserve the same baseline and line spacing as the shadow, including
    # automatic wrapping. A blank first line reserves its place for the headline.
    if len(lines) > 1:
        draw.multiline_text(
            xy, '\n' + '\n'.join(lines[1:]), font=font, anchor='ma',
            align='center', spacing=spacing, fill=other_rgb + (255,),
            stroke_width=1, stroke_fill=other_rgb + (220,),
        )
    draw.text(xy, lines[0], font=font, anchor='ma', fill=first_rgb + (255,),
              stroke_width=1, stroke_fill=first_rgb + (220,))
    return np.array(image)


def _build_title_clip(title: str, duration: float, max_width: int,
                      font_path=TITLE_FONT_PATH, font_size=100,
                      first_line_color='#ffffff', other_lines_color='#a7f3d0'):
    from moviepy import ImageClip

    return ImageClip(_title_image(title, max_width, font_path, font_size,
                                 first_line_color, other_lines_color)).with_duration(duration)


@app.route('/api/video_title_preview', methods=['POST'])
def video_title_preview():
    from PIL import Image

    data = request.get_json(silent=True) or {}
    title = str(data.get('video_title') or '').strip()
    if not title or len(title) > 1000:
        return jsonify(error='Enter a title of up to 1000 characters.'), 400
    try:
        font_path, font_size = _title_settings(data)
        first_color, other_color = _title_colors(data)
        image = _title_image(title, 936, font_path, font_size, first_color, other_color)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    output = io.BytesIO()
    Image.fromarray(image).save(output, format='PNG')
    output.seek(0)
    return send_file(output, mimetype='image/png')


@app.route('/api/render_video', methods=['POST'])
@app.route('/api/render_video/', methods=['POST'])
def make_video() -> tuple[object, int]:
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
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400
    if codec not in ALLOWED_CODECS:
        return jsonify({"status": "error",
                        "error": f"Unsupported codec '{codec}'. "
                                 f"Allowed: {', '.join(ALLOWED_CODECS)}."}), 400

    def _decode_material(value: str, label: str) -> bytes:
        """Strip an optional `data:...;base64,` prefix and decode to bytes."""
        if value.startswith("data:"):
            # e.g. "data:image/png;base64,AAAA..."
            _, _, b64_part = value.partition(",")
        else:
            b64_part = value
        try:
            raw = base64.b64decode(b64_part, validate=False)
        except Exception:
            raise ValueError(f"{label} is not valid base64.")
        if not raw:
            raise ValueError(f"{label} is empty after decoding.")
        if len(raw) > MAX_MATERIAL_BYTES:
            raise ValueError(f"{label} exceeds the 10 MB limit ({len(raw) // (1024 * 1024)} MB).")
        return raw

    try:
        map_bytes = _decode_material(map_b64, "map_file")
        bg_bytes = _decode_material(background_b64, "background_video")
    except ValueError as ve:
        return jsonify({"status": "error", "error": str(ve)}), 400

    safe_title = re.sub(r'[^\w\-]', '_', title)
    cut_audio_path = os.path.join(CUT_DIR, f"{safe_title}_cut.mp3")
    audio_path = (
        cut_audio_path
        if os.path.exists(cut_audio_path)
        else os.path.join(DOWNLOADS, f"{safe_title}.mp3")
    )
    output_path = os.path.join(RENDER_DIR, f"{safe_title}_final.mp4")

    map_path = bg_path = None
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

        # Target clip length: 1..30 seconds (default 8.0), never longer than the source clips.
        try:
            TARGET = float(raw_duration) if raw_duration is not None else 8.0
        except (TypeError, ValueError):
            TARGET = 8.0
        TARGET = max(1.0, min(30.0, TARGET))

        background_video = VideoFileClip(bg_path).without_audio()
        bg_duration = min(TARGET, background_video.duration or TARGET)
        background_video = background_video.subclipped(0, bg_duration)

        # Fill the portrait frame without stretching the source footage.
        canvas_w, canvas_h = 1080, 1920
        bg_scale = max(canvas_w / background_video.w, canvas_h / background_video.h)
        background_video = background_video.resized(bg_scale).cropped(
            x_center=background_video.w * bg_scale / 2,
            y_center=background_video.h * bg_scale / 2,
            width=canvas_w, height=canvas_h,
        )

        # Darken the edges and the area behind the title for readable white text.
        import numpy as np
        shade = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        y = np.linspace(-1, 1, canvas_h)
        shade[:, :, 3] = (70 + 95 * np.abs(y) ** 1.5).astype(np.uint8)[:, None]
        scrim = ImageClip(shade).with_duration(bg_duration)

        # Anchor the headline near the top and let the map fill the space below.
        title_clip = _build_title_clip(title, bg_duration, canvas_w - 144, font_path,
                                       font_size, first_color, other_color)
        map_clip = ImageClip(map_path).with_duration(bg_duration)
        title_top, gap, bottom_margin = 120, 48, 160
        map_top = title_top + title_clip.h + gap
        map_area_height = canvas_h - map_top - bottom_margin
        scale = min((canvas_w - 72) / map_clip.w, map_area_height / map_clip.h)
        map_clip = map_clip.resized(scale)
        title_clip = title_clip.with_position(("center", title_top))
        map_clip = map_clip.with_position(("center", map_top))
        clips = [background_video, scrim, map_clip, title_clip]

        final_clip = CompositeVideoClip(clips, size=(canvas_w, canvas_h))

        # Attach audio if the cut mp3 exists; otherwise render silent video.
        if os.path.exists(audio_path):
            audio_clip = AudioFileClip(audio_path)
            audio_duration = min(bg_duration, audio_clip.duration or bg_duration)
            final_clip = final_clip.with_audio(audio_clip.subclipped(0, audio_duration))
            final_clip = final_clip.with_duration(audio_duration)
        else:
            final_clip = final_clip.with_duration(bg_duration)

        final_clip.write_videofile(
            output_path,
            codec=codec,
            audio_codec="aac",
            fps=30,
            logger=None,
        )

        return jsonify({
            "status": "success",
            "file_path": output_path,
            "download_url": f"/api/download_video?path={output_path}"
        }), 200
    
    except Exception as e:
        return jsonify({"status": "error", "error": f"Error processing video: {str(e)}"}), 500

    finally:
        # Always clean up the temp input files.
        for p in (map_path, bg_path):
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
    candidate = os.path.realpath(raw_path)
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

    port = int(os.environ.get("PORT", "8080"))
    app.run(host='0.0.0.0', port=port, debug=True)
