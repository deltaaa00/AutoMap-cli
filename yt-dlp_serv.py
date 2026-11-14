import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache

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

        match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', response.text)
        if match:
            return match.group(1)

        # Fall back to the page title when Open Graph metadata is unavailable.
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
            # Temporary downloads and persistent storage may be separate mounts.
            # shutil.move falls back to copying when an atomic rename is unavailable.
            shutil.move(mp3_path, new_mp3_path)

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

        if not re.fullmatch(r'\d+:[0-5]\d', time):
            return jsonify(status='error', error='Start time must use MM:SS format.'), 400
        minutes, seconds = time.split(":")
        minutes, seconds = int(minutes), int(seconds)

        # Converting to ms
        start_ms = (minutes * 60 + seconds) * 1000

        # Audio clip duration (1..30 seconds, default 8 seconds)
        end_ms = start_ms + (duration * 1000)

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

TITLE_FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "resources", "fonts", "DIN Condensed.ttf",
)


def _title_settings(data):
    font_name = str(data.get('title_font') or 'DIN Condensed.ttf')
    font_dir = os.path.dirname(TITLE_FONT_PATH)
    # Accept bundled filenames only, rather than arbitrary client-supplied paths.
    fonts = {name for name in os.listdir(font_dir) if name.lower().endswith(('.ttf', '.otf'))}
    if font_name not in fonts:
        raise ValueError('Unknown title font.')
    raw_size = data.get('title_font_size', 100)
    # Reject booleans explicitly because int(True) would otherwise succeed.
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
              data.get('title_other_lines_color', '#ffe735'))
    if any(not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color) for color in colors):
        raise ValueError('Title colors must use #RRGGBB format.')
    return colors


def _title_image(title: str, max_width: int, font_path=TITLE_FONT_PATH, font_size=100,
                 first_line_color='#ffffff', other_lines_color='#ffe735',
                 effects=True, max_height=360):
    """Lay out a centered, wrapped title with safe margins and a layered shadow."""
    import numpy as np
    from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

    title = title.upper()
    padding = 24
    outline = 6 if effects else 1
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
                                            spacing=spacing, align="center", stroke_width=outline)
        height = bounds[3] - bounds[1]
        if height <= max_height:
            break
    if height > max_height:
        raise ValueError("Video title is too long. Please shorten it.")

    size = (max_width, height + padding * 2)
    shadow = Image.new("RGBA", size)
    # Account for the font's vertical bounding-box offset inside the padded image.
    xy = (max_width / 2, padding - bounds[1])
    # Use one line advance for shadow and colored text. Different stroke widths
    # in multiline_text would otherwise change the spacing and clip lower rows.
    advance = measure.textbbox((0, 0), 'A', font=font, stroke_width=outline)[3] + outline + spacing
    shadow_draw = ImageDraw.Draw(shadow)
    for row, line in enumerate(lines):
        shadow_draw.text((xy[0], xy[1] + row * advance + 5), line,
                         font=font, anchor='ma', fill=(0, 0, 0, 255), stroke_width=outline)
    # Combine a soft halo with a tighter drop shadow for contrast on bright footage.
    # Preview and final video both use this image, so the shading stays consistent.
    image = shadow.filter(ImageFilter.GaussianBlur(8)) if effects else Image.new('RGBA', size)
    if effects:
        image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(2)))
    draw = ImageDraw.Draw(image)
    first_rgb = ImageColor.getrgb(first_line_color)
    other_rgb = ImageColor.getrgb(other_lines_color)
    for row, line in enumerate(lines):
        color = first_rgb if row == 0 else other_rgb
        draw.text((xy[0], xy[1] + row * advance), line, font=font, anchor='ma',
                  fill=color + (255,), stroke_width=outline,
                  stroke_fill=(0, 0, 0, 255) if effects else color + (220,))
    return np.array(image)


def _build_title_clip(title: str, duration: float, max_width: int,
                      font_path=TITLE_FONT_PATH, font_size=100,
                      first_line_color='#ffffff', other_lines_color='#ffe735', effects=True):
    from moviepy import ImageClip

    return ImageClip(_title_image(title, max_width, font_path, font_size,
                                 first_line_color, other_lines_color, effects)).with_duration(duration)


def _watermark_settings(data):
    text = data.get('watermark_text', '')
    color = data.get('watermark_color', '#a4e9ee')
    if not isinstance(text, str) or len(text) > 80:
        raise ValueError('Watermark text must be at most 80 characters.')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Watermark color must use #RRGGBB format.')
    return text.strip(), color


def _watermark_font_path(data):
    # Validate the watermark's own font against the bundled filenames.
    return _title_settings({'title_font': data.get('watermark_font', 'DIN Condensed.ttf')})[0]


def _round_watermark(image, diameter=230):
    """Center-crop the logo to a circle while preserving existing transparency."""
    from PIL import Image, ImageChops, ImageDraw, ImageOps

    image = ImageOps.fit(image.convert('RGBA'), (diameter, diameter), method=Image.Resampling.LANCZOS)
    # Draw the mask at higher resolution for a smooth circular edge.
    mask = Image.new('L', (diameter * 4, diameter * 4))
    ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
    mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    image.putalpha(ImageChops.multiply(image.getchannel('A'), mask))
    return image


def _with_shadow(image):
    """Add a soft silhouette shadow while keeping transparent areas transparent."""
    from PIL import Image, ImageFilter

    image = image.convert('RGBA')
    canvas = Image.new('RGBA', (image.width + 48, image.height + 48))
    alpha = Image.new('L', canvas.size)
    alpha.paste(image.getchannel('A'), (24, 32))
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(10)).point(lambda p: round(p * .8)))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(image, (24, 24))
    return canvas


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
        effects = data.get('visual_effects', True) is not False
        if data.get('preview_kind') == 'watermark':
            text, color = _watermark_settings(data)
            image = _title_image(text, 936, _watermark_font_path(data), 96,
                                 color, color, effects, max_height=110)
        else:
            image = _title_image(title, 1040, font_path, font_size, first_color, other_color, effects)
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
            raw = base64.b64decode(b64_part, validate=False)
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
        watermark_bytes = _decode_material(str(data['watermark_image']), 'watermark_image') if data.get('watermark_image') else None
        from PIL import Image, UnidentifiedImageError
        watermark_image = None
        if watermark_bytes:
            try:
                with Image.open(io.BytesIO(watermark_bytes)) as uploaded:
                    if uploaded.width * uploaded.height > 20_000_000:
                        raise ValueError('Watermark image must be at most 20 megapixels.')
                    watermark_image = uploaded.convert('RGBA')
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValueError('Watermark must be a valid image.')
    except ValueError as ve:
        return jsonify({"status": "error", "error": str(ve)}), 400

    safe_title = re.sub(r'[^\w\-]', '_', title)
    # The video headline and the selected song are independent identifiers.
    audio_title = data.get('audio_title', '')
    if not isinstance(audio_title, str) or (audio_title and not re.fullmatch(r'[\w\-]+', audio_title)):
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

    map_path = bg_path = audio_temp_path = None
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

        background_video = VideoFileClip(bg_path)
        media_resources.append(background_video)
        background_video = background_video.without_audio()
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

        effects = data.get('visual_effects', True) is not False
        if effects:
            from PIL import ImageFilter
            # Blur a smaller frame for a soft background without blurring the overlays.
            def soften(frame):
                small = Image.fromarray(frame).resize((270, 480))
                return np.array(small.filter(ImageFilter.GaussianBlur(2)).resize((1080, 1920)))
            import numpy as np
            background_video = background_video.image_transform(soften)

        # Darken the edges and the area behind the title for readable white text.
        import numpy as np
        shade = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        y = np.linspace(-1, 1, canvas_h)
        shade[:, :, 3] = (25 + 65 * np.abs(y) ** 1.5).astype(np.uint8)[:, None]
        scrim = ImageClip(shade).with_duration(bg_duration)

        # Anchor the headline near the top and let the map fill the space below.
        title_clip = _build_title_clip(title, bg_duration, 1040, font_path,
                                       font_size, first_color, other_color, effects)
        map_clip = ImageClip(map_path).with_duration(bg_duration)
        title_top, gap = 70, 16
        map_top = title_top + title_clip.h + gap
        branded = bool(watermark_image is not None or watermark_text)
        map_bottom = 1450 if branded else 1760
        map_area_height = map_bottom - map_top
        # Fit both dimensions so the whole map stays inside the space below the title.
        scale = min(1040 / map_clip.w, map_area_height / map_clip.h)
        map_clip = map_clip.resized(scale)
        if effects:
            map_image = Image.open(map_path).convert('RGBA').resize((map_clip.w, map_clip.h))
            map_clip = ImageClip(np.array(_with_shadow(map_image))).with_duration(bg_duration)
        title_clip = title_clip.with_position(("center", title_top))
        map_clip = map_clip.with_position(("center", map_top - (24 if effects else 0)))
        clips = [background_video, scrim, map_clip, title_clip]
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
            clips.append(branding.with_position(('center', 1740)))

        final_clip = CompositeVideoClip(clips, size=(canvas_w, canvas_h))
        media_resources.append(final_clip)

        # Attach the selected cut or full track when available; otherwise render silently.
        if audio_bytes or (audio_title and os.path.isfile(audio_path)):
            audio_clip = AudioFileClip(audio_path)
            media_resources.append(audio_clip)
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
            "download_url": f"/api/download_video?path={output_path}",
            "has_audio": final_clip.audio is not None,
        }), 200
    
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
        for p in (map_path, bg_path, audio_temp_path):
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
