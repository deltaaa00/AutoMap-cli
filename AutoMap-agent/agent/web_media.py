"""Media management Blueprint registered by the standalone web app.

Routes upload, split, delete, or queue URL imports for backgrounds/music.
Database and media roots come from ``web_services`` in the current app context;
extension/size limits and ffmpeg operations come from ``media_pool``. Uploaded
filenames are normalized and generated paths are contained within the pool.
URL imports enqueue commands for the runner instead of downloading in requests.
The parent app in web.py applies authentication before Blueprint handlers run;
keep that gate and per-route external-input checks when adding new endpoints."""

from pathlib import Path

from flask import Blueprint, jsonify, request

from .media_pool import MAX_MEDIA_BYTES, MAX_MEDIA_MB, split_background_video
from .web_services import get_db, get_media_pool

media = Blueprint("media", __name__)


@media.post("/api/agent/media/upload")
def api_agent_media_upload():
    """Upload one or more background videos or music tracks into agent_media/."""
    kind = str(request.form.get("kind") or "").strip().lower()
    if kind not in ("background", "music"):
        return jsonify(status="error", error="kind must be 'background' or 'music'."), 400
    files = request.files.getlist("files")
    if not files:
        return jsonify(status="error", error="Choose at least one file to upload."), 400
    pool = get_media_pool()
    pool.ensure_dirs()
    if kind == "background":
        allowed_exts = (".mp4", ".mov", ".webm")
        target_dir = pool.backgrounds_dir
    else:
        allowed_exts = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
        target_dir = pool.music_dir
    saved_names: list[str] = []
    for fh in files:
        orig_name = Path(str(fh.filename or "")).name
        ext = Path(orig_name).suffix.lower()
        if ext not in allowed_exts:
            return jsonify(status="error", error=f"Unsupported file format: {orig_name}"), 400
        # Keep a readable filename without carrying path or punctuation characters.
        safe_characters = []
        for character in Path(orig_name).stem:
            if character.isalnum() or character in ("_", "-"):
                safe_characters.append(character)
            else:
                safe_characters.append("_")
        stem = "".join(safe_characters).strip("_")[:60]
        if not stem:
            stem = kind
        dest = target_dir / f"{stem}{ext}"
        counter = 2
        while dest.exists():
            dest = target_dir / f"{stem}_{counter}{ext}"
            counter += 1
        total_bytes = 0
        try:
            with dest.open("wb") as out_fh:
                while True:
                    chunk = fh.stream.read(64 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_MEDIA_BYTES:
                        raise ValueError(f"{orig_name} exceeds the {MAX_MEDIA_MB} MB limit.")
                    out_fh.write(chunk)
            if total_bytes == 0:
                raise ValueError(f"{orig_name} is empty.")
        except ValueError as exc:
            dest.unlink(missing_ok=True)
            return jsonify(status="error", error=str(exc)), 400

        if kind == "background":
            parts = split_background_video(dest)
            saved_names.extend(p.name for p in parts)
        else:
            saved_names.append(dest.name)
    return jsonify(status="success", saved=saved_names, message=f"Uploaded: {', '.join(saved_names)}")


@media.post("/api/agent/media/split")
def api_agent_media_split():
    """Split an existing long background video into <=15s clips."""
    data = request.get_json(silent=True) or {}
    filename = Path(str(data.get("filename") or "")).name
    if not filename:
        return jsonify(status="error", error="Invalid media file."), 400
    pool = get_media_pool()
    target_dir = pool.backgrounds_dir.resolve()
    target = (target_dir / filename).resolve()
    try:
        target.relative_to(target_dir)
    except ValueError:
        return jsonify(status="error", error="Invalid path."), 403
    if not target.is_file():
        return jsonify(status="error", error="File not found."), 404
    parts = split_background_video(target)
    if len(parts) == 1 and parts[0] == target:
        return jsonify(status="success", split=False, message=f"{filename} is already short (<22s).")
    names = [p.name for p in parts]
    return jsonify(status="success", split=True, saved=names, message=f"Split into {len(names)} clips: {', '.join(names)}")


@media.post("/api/agent/media/delete")
def api_agent_media_delete():
    """Delete a file from agent_media/backgrounds/ or agent_media/music/."""
    data = request.get_json(silent=True) or {}
    kind = str(data.get("kind") or "").strip().lower()
    filename = Path(str(data.get("filename") or "")).name
    if kind not in ("background", "music") or not filename or filename.lower() == "readme.txt":
        return jsonify(status="error", error="Invalid media file."), 400
    pool = get_media_pool()
    if kind == "background":
        target_dir = pool.backgrounds_dir.resolve()
    else:
        target_dir = pool.music_dir.resolve()
    target = (target_dir / filename).resolve()
    try:
        target.relative_to(target_dir)
    except ValueError:
        return jsonify(status="error", error="Invalid path."), 403
    if not target.is_file():
        return jsonify(status="error", error="File not found."), 404
    target.unlink()
    return jsonify(status="success", message=f"Deleted {filename}")


@media.post("/api/agent/media/import_url")
def api_agent_media_import_url():
    """Queue downloading a YouTube URL as an MP3 track into agent_media/music/."""
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    if not url.startswith(("https://", "http://")):
        return jsonify(status="error", error="Paste a valid YouTube URL."), 400
    db = get_db()
    cmd_id = db.enqueue_command("import_music_url", {"url": url})
    return jsonify(status="success", command_id=cmd_id, message="Track import queued.")
