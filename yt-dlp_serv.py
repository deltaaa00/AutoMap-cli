import base64
import os
import re
import shutil
import subprocess
import tempfile
import time

from flask import Flask, request, jsonify

app = Flask(__name__)

STORAGE_DIR = "/tmp/automap_storage"
DOWNLOADS = os.path.join(STORAGE_DIR, "downloads")
CUT_DIR = os.path.join(STORAGE_DIR, "cut")

os.makedirs(STORAGE_DIR, exist_ok=True)
os.makedirs(CUT_DIR, exist_ok=True)

def cleanup_old_files(max_age_seconds=1800):
    """Delete old files"""
    now = time.time()
    for item in os.listdir(STORAGE_DIR):
        item_path = os.path.join(STORAGE_DIR, item)
        # Якщо файл/папка старіші за 30 хвилин — видаляємо
        if now - os.path.getmtime(item_path) > max_age_seconds:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path, ignore_errors=True)
            else:
                os.remove(item_path)

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

    command = [
        "yt-dlp",
        "-f", "ba[ext=m4a]/ba",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "-o", os.path.join(DOWNLOADS, "%(title)s.%(ext)s"),
        url,
    ]

    try:
        subprocess.run(command, capture_output=True, check=True)

        # Find the produced mp3 inside the temp dir.
        mp3_path = None
        for root, _dirs, files in os.walk(DOWNLOADS):
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

if __name__ == "__main__":

    port = int(os.environ.get("PORT", "8080"))
    app.run(host='0.0.0.0', port=port, debug=True)