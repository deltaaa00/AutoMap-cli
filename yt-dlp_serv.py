import base64
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
def cut_audio():
    data = request.get_json(silent=True)

    """Cut a stored mp3 into a short clip and return it as base64.

    Reads a previously downloaded track from ``DOWNLOADS`` (by title), slices
    it starting at ``start_time``, and writes the clip to ``CUT_DIR``.

    Request JSON:
        { "start_time": "MM:SS", "title": "<song title>" }

    Response JSON (success):
        { "status": "successfull", "file": "<base64 mp3 clip>" }

    Response JSON (error):
        { "status": "error", "error": "<message>" }
    """
    try:
        time = data['start_time']
        filename = data['title']
        if not isinstance(filename, str) or not re.fullmatch(r'[\w\-]+', filename):
            return jsonify({"status": "error", "error": "Invalid track title."}), 400

        os.makedirs(CUT_DIR, exist_ok=True)

        #Parsing time
        minutes, seconds = time.split(":")
        minutes, seconds = int(minutes), int(seconds)

        #Converting to ms
        start_ms = (minutes * 60 + seconds) * 1000

        #Audio must be only 8 second long
        end_ms = (minutes * 60 + seconds) * 1000 + 8000 

        from pydub import AudioSegment

        audio = AudioSegment.from_file(os.path.join(DOWNLOADS, f"{filename}.mp3"))

        cut = audio[start_ms:end_ms]

        cut.export(
            f"{CUT_DIR}/{filename}_cut.mp3",
            format="mp3",
            bitrate="192k",
        )

        #Encoding in base64
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

# Fonts to try (in order) for the title overlay. First one that loads wins.
_TITLE_FONT_CANDIDATES = (
    "DejaVu-Sans-Bold",
    "DejaVuSans-Bold",
    "Arial-Bold",
    "Arial",
    "Liberation-Sans-Bold",
    "FreeSansBold",
)


def _build_title_clip(title: str, duration: float, max_width: int):
    """Return a positioned title TextClip, or None if text rendering is unavailable.

    ``max_width`` caps the text width (pass the video width, e.g. 1080). Longer
    titles wrap onto multiple lines instead of overflowing the frame.

    Tries a list of common fonts, then Pillow's default font, then gives up.
    The title is decorative, so a failure must never break the render.
    """
    from moviepy import TextClip

    def _make(font=None, use_caption=False):
        kwargs = dict(text=title, font_size=110, color="white",
                      stroke_color="black", stroke_width=2)
        if use_caption:
            # Caption mode wraps text to the given width.
            kwargs["method"] = "caption"
            kwargs["size"] = (max_width, None)
        if font:
            kwargs["font"] = font
        return (TextClip(**kwargs)
                .with_position(("center", 60))
                .with_duration(duration))

    # 1) Try explicit known fonts, wrapping text to the video width (caption mode).
    for font in _TITLE_FONT_CANDIDATES:
        try:
            return _make(font, use_caption=True)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue

    # 2) Fall back to Pillow's bundled default font (caption mode).
    try:
        return _make(None, use_caption=True)
    except Exception:  # noqa: BLE001 - caption mode may not support the font
        pass

    # 3) Last resort: label mode (no wrapping) so the title still appears.
    for font in _TITLE_FONT_CANDIDATES + (None,):
        try:
            return _make(font, use_caption=False)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue

    app.logger.warning("Title overlay disabled (no usable font).")
    return None


@app.route('/api/render_video', methods=['POST'])
@app.route('/api/render_video/', methods=['POST'])
def make_video():
    data = request.get_json(silent=True) or {}

    map_b64 = data.get('map_file') or ''
    background_b64 = data.get('background_video') or ''
    title = (data.get('video_title') or data.get('title') or '').strip()
    codec = (data.get('codec') or 'libx264').strip()

    # --- Validation -----------------------------------------------------------
    if not map_b64:
        return jsonify({"status": "error", "error": "Missing 'map_file'."}), 400
    if not background_b64:
        return jsonify({"status": "error", "error": "Missing 'background_video'."}), 400
    if not title:
        return jsonify({"status": "error", "error": "Missing 'video_title'."}), 400
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
    audio_path = os.path.join(DOWNLOADS, f"{safe_title}.mp3")
    output_path = os.path.join(RENDER_DIR, f"{safe_title}_final.mp4")

    map_path = bg_path = None
    try:
        from moviepy import (VideoFileClip, TextClip, CompositeVideoClip,
                             ImageClip, AudioFileClip)

        os.makedirs(RENDER_DIR, exist_ok=True)

        # Write the decoded materials to temp files on disk.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as map_file_temp:
            map_file_temp.write(map_bytes)
            map_path = map_file_temp.name

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as bg_video_temp:
            bg_video_temp.write(bg_bytes)
            bg_path = bg_video_temp.name

        # Target clip length: 8 seconds, but never longer than the source clips.
        TARGET = 8.0

        background_video = VideoFileClip(bg_path).without_audio()
        bg_duration = min(TARGET, background_video.duration or TARGET)
        background_video = background_video.subclipped(0, bg_duration)

        # Scale the background to a 1280x720 frame (letterbox-free: stretch then
        # optionally crop). Keeping sizes consistent avoids compositing errors.
        canvas_w, canvas_h = 1080, 1920
        background_video = background_video.resized((canvas_w, canvas_h))

        # Map overlay: fit inside the frame while preserving aspect ratio.
        map_clip = ImageClip(map_path).with_duration(bg_duration)
        max_map_w, max_map_h = canvas_w, canvas_h // 2
        scale = min(max_map_w / map_clip.w, max_map_h / map_clip.h, 1.0)
        map_clip = (map_clip.resized(scale)
                            .with_position("center")
                            .with_duration(bg_duration))

        # Title text overlay near the top. Try a few common fonts, then fall back
        # to Pillow's default font; if all fail, render without the title.
        clips = [background_video, map_clip]
        title_clip = _build_title_clip(title, bg_duration, canvas_w)
        if title_clip is not None:
            clips.append(title_clip)

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
