import base64
import os
import re
import subprocess
import threading
import time
import uuid

from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory job store.
#
# A job is a plain dict: {status, url, title, file, error, details, created, updated}
#   status: "pending" -> "running" -> "done" | "error"
# `file` holds the base64 mp3 only once status == "done".
#
# Access is guarded by a single lock; the store is pruned on every write so a
# long-running server does not leak memory from finished jobs.
# ---------------------------------------------------------------------------
TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()

# How long (seconds) a finished job stays available before it is pruned.
JOB_TTL = 5 * 60  # 10 minutes


def _prune_locked(now: float) -> None:
    """Drop finished jobs older than JOB_TTL. Caller must hold _TASKS_LOCK."""
    expired = [
        jid for jid, job in TASKS.items()
        if job["status"] in ("done", "error") and now - job["updated"] > JOB_TTL
    ]
    for jid in expired:
        TASKS.pop(jid, None)


def _update_job(job_id: str, **fields) -> None:
    """Thread-safe partial update of a job."""
    with _TASKS_LOCK:
        job = TASKS.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated"] = time.time()


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
def job_id_send():
    """Create a download job and return immediately with its job_id."""
    request_data = request.get_json(silent=True) or {}
    url = request_data.get('url')

    if not url or not isinstance(url, str):
        return jsonify({"status": "error", "error": "Missing 'url' in request body."}), 400

    job_id = str(uuid.uuid4())

    now = time.time()
    with _TASKS_LOCK:
        _prune_locked(now)
        TASKS[job_id] = {
            "status": "pending",
            "url": url,
            "title": None,
            "file": None,
            "error": None,
            "details": None,
            "created": now,
            "updated": now,
        }

    # Background worker: actually download/convert the audio.
    thread = threading.Thread(
        target=music_download, args=(url, job_id), daemon=True
    )
    thread.start()

    return jsonify({
        "status": "success",
        "job_id": job_id,
    }), 200


@app.route('/api/music_download/<job_id>', methods=['GET'])
def job_status(job_id: str):
    """Return the current state of a download job.

    pending/running -> { "status": "pending" | "running", "job_id": "..." }
    done            -> { "status": "done", "job_id", "title", "file": "<base64 mp3>" }
    error           -> { "status": "error", "job_id", "error", "details" }

    404 if the job_id is unknown (never existed or already pruned).
    """
    with _TASKS_LOCK:
        job = TASKS.get(job_id)
        if job is None:
            return jsonify({"status": "error", "error": "Unknown job_id."}), 404
        # Copy out under the lock so the worker thread can't mutate mid-serialise.
        snapshot = dict(job)

    status = snapshot["status"]
    if status in ("pending", "running"):
        return jsonify({"status": status, "job_id": job_id}), 200

    if status == "done":
        return jsonify({
            "status": "done",
            "job_id": job_id,
            "title": snapshot["title"] or "music_automap",
            "file": snapshot["file"],
        }), 200

    # status == "error"
    return jsonify({
        "status": "error",
        "job_id": job_id,
        "error": snapshot["error"] or "Download failed.",
        "details": snapshot.get("details") or "",
    }), 200


def music_download(url: str, job_id: str) -> None:
    """Worker: download + convert to mp3 and store the result in TASKS.

    Runs in a background thread, so it must NEVER touch Flask's request/response
    objects (no jsonify/return). It only mutates the shared TASKS store.
    """
    _update_job(job_id, status="running")

    # yt-dlp with `-x` writes to a temp file, so pipe through the temp dir and
    # read the produced .mp3 back. `-o -` is unreliable when extracting audio.
    tmp_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"automap_{job_id}")

    command = [
        "yt-dlp",
        "-f", "ba[ext=m4a]/ba",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "-o", os.path.join(tmp_dir, "%(title)s.%(ext)s"),
        url,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=True,
        )

        # Find the produced mp3 inside the job temp dir.
        mp3_path = None
        for root, _dirs, files in os.walk(tmp_dir):
            for name in files:
                if name.lower().endswith(".mp3"):
                    mp3_path = os.path.join(root, name)
                    break
            if mp3_path:
                break

        if not mp3_path:
            _update_job(job_id, status="error",
                        error="yt-dlp finished but no MP3 was produced.",
                        details=result.stdout.decode("utf-8", errors="ignore")[-2000:])
            return

        with open(mp3_path, "rb") as fh:
            base64_audio = base64.b64encode(fh.read()).decode("utf-8")

        _update_job(
            job_id,
            status="done",
            file=base64_audio,
            title=_get_song_title(url) or "music_automap",
        )

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode("utf-8", errors="ignore").strip()
        _update_job(
            job_id,
            status="error",
            error="yt-dlp ERROR: Failed to download or convert the audio.",
            details=error_message,
        )

    except FileNotFoundError:
        _update_job(
            job_id,
            status="error",
            error="yt-dlp or ffmpeg not installed / not on PATH.",
        )

    except Exception as e:  # noqa: BLE001 - surface any failure to the poller
        _update_job(job_id, status="error", error=str(e))

    finally:
        # Clean up the temp directory regardless of outcome.
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":

    port = int(os.environ.get("PORT", "8080"))
    app.run(host='0.0.0.0', port=port, debug=True)