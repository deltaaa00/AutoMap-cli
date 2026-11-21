"""HTTP boundary between the agent and the separate AutoMap web service.

The runner and CandidateVideoGenerator use AutoMapClient for idea/map generation,
video rendering, and music downloads. AUTOMAP_URL and WEB_LOGIN/WEB_PASSWORD
provide defaults; constructor arguments support explicit configuration and tests.
This client owns the HTTP session and retries authentication after a 401.
Rendering sends multipart media, while generated maps arrive as base64 data.
Completed video downloads replace their destination atomically; server-side
rendering and media validation remain in the AutoMap application."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import requests


class AutoMapClientError(RuntimeError):
    """Raised when an AutoMap API call fails."""


def _map_upload(image: str) -> tuple[str, bytes, str]:
    encoded = image.split(",", 1)[-1] if image.startswith("data:") else image
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise AutoMapClientError("AutoMap returned invalid map data.") from exc
    if not raw:
        raise AutoMapClientError("AutoMap returned an empty map.")
    return "map.png", raw, "image/png"


class AutoMapClient:
    """Thin authenticated client for AutoMap's Flask endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        login: str | None = None,
        password: str | None = None,
        timeout: float = 300.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AUTOMAP_URL") or "http://host.docker.internal:3000").rstrip("/")
        self.login_user = login if login is not None else os.environ.get("WEB_LOGIN", "deltaaa")
        self.login_password = password if password is not None else os.environ.get("WEB_PASSWORD", "deltaaa00!")
        self.timeout = float(timeout)
        self.http = session or requests.Session()
        self._logged_in = False

    def ensure_auth(self) -> None:
        """Sign in via /login when the web service runs with SERVER=TRUE."""
        if self._logged_in:
            return
        try:
            resp = self.http.post(
                f"{self.base_url}/login",
                data={"login": self.login_user, "password": self.login_password, "next": "/"},
                allow_redirects=False,
                timeout=15,
            )
            if resp.status_code in (200, 302, 303):
                self._logged_in = True
        except requests.RequestException:
            # Local mode does not require login; actual API call will surface connectivity errors.
            pass

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        self.ensure_auth()
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        resp = self.http.request(method, url, **kwargs)
        if resp.status_code == 401:
            self._logged_in = False
            self.ensure_auth()
            resp = self.http.request(method, url, **kwargs)
        return resp

    def generate_idea(
        self,
        *,
        provider: str,
        model: str,
        with_params: bool = True,
        avoid_ideas: list[str] | None = None,
        top_channel_topics: list[str] | None = None,
        api_key: str = "",
    ) -> dict[str, Any]:
        """Request a fresh map idea (and optional parameters) from /api/generate_idea."""
        payload: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "with_params": with_params,
            "avoid_ideas": list(avoid_ideas or [])[-30:],
        }
        if top_channel_topics:
            payload["top_channel_topics"] = list(top_channel_topics)[:10]
        if api_key:
            payload["api_key"] = api_key
        resp = self._request("POST", "/api/generate_idea", json=payload)
        data = resp.json()
        if not resp.ok or data.get("status") != "success":
            raise AutoMapClientError(str(data.get("error") or f"generate_idea failed ({resp.status_code})"))
        return data

    def generate_map(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate a map PNG via POST /api/generate."""
        resp = self._request("POST", "/api/generate", json=payload)
        data = resp.json()
        if not resp.ok or data.get("status") != "success":
            raise AutoMapClientError(str(data.get("error") or f"generate_map failed ({resp.status_code})"))
        return data

    def get_zoom_countries(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch normalized zoom coordinates for Europe and World maps."""
        resp = self._request("GET", "/api/zoom_countries", timeout=30)
        if not resp.ok:
            raise AutoMapClientError(f"zoom_countries failed ({resp.status_code})")
        return resp.json()

    def render_video(
        self,
        *,
        map_data_url: str,
        background_path: Path,
        video_title: str,
        duration: int = 8,
        audio_path: Path | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Render a Short via POST /api/render_video and return the JSON result."""
        payload: dict[str, Any] = {
            "video_title": video_title,
            "codec": "libx264",
            "duration": int(duration),
            "visual_effects": True,
            "intro_fade": True,
            "subscribe_badge": True,
            "subscribe_text": "Subscribe!",
            "subscribe_delay": 2.5,
        }
        if extra_params:
            payload.update(extra_params)
        files = {
            "map_file": _map_upload(map_data_url),
            "background_video": (background_path.name, background_path.read_bytes(), "application/octet-stream"),
        }
        if audio_path is not None and audio_path.is_file():
            files["audio_file"] = (audio_path.name, audio_path.read_bytes(), "application/octet-stream")
            payload["audio_title"] = audio_path.stem
        clean_map = payload.pop("reveal_clean_map_file", "")
        if clean_map:
            files["reveal_clean_map_file"] = _map_upload(clean_map)

        resp = self._request("POST", "/api/render_video", data={"payload": json.dumps(payload)}, files=files)
        try:
            data = resp.json()
        except ValueError as exc:
            detail = f"render_video returned an invalid response ({resp.status_code})"
            raise AutoMapClientError(detail) from exc
        if resp.ok and isinstance(data, dict) and data.get("status") == "success":
            return data
        error = data.get("error") if isinstance(data, dict) else None
        raise AutoMapClientError(str(error or f"render_video failed ({resp.status_code})"))

    def download_rendered_video(self, download_url: str, dest_path: Path) -> Path:
        """Stream an MP4 into a temporary sibling and publish it only when complete.

        The destination is preserved on interrupted/empty downloads. Temporary .part
        files are removed on all exits, including network and filesystem failures."""
        path = download_url if download_url.startswith("/") else f"/{download_url}"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        partial = None
        try:
            with self._request("GET", path, stream=True, timeout=180) as resp:
                if not resp.ok:
                    raise AutoMapClientError(f"download_video failed ({resp.status_code})")
                with NamedTemporaryFile(dir=dest_path.parent, suffix=".part", delete=False) as fh:
                    partial = Path(fh.name)
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        fh.write(chunk)
                    if not fh.tell():
                        raise AutoMapClientError("AutoMap returned an empty video.")
            partial.replace(dest_path)
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
        return dest_path

    def download_music_from_url(self, youtube_url: str) -> tuple[str, bytes]:
        """Download an MP3 track from a YouTube URL via AutoMap's /api/music_download."""
        resp = self._request("POST", "/api/music_download", json={"url": youtube_url}, timeout=120)
        data = resp.json()
        if not resp.ok or data.get("status") != "success":
            raise AutoMapClientError(str(data.get("error") or f"music_download failed ({resp.status_code})"))
        raw_audio = str(data.get("file") or data.get("audio_file") or "")
        if "," in raw_audio and raw_audio.lower().startswith("data:"):
            _, raw_audio = raw_audio.split(",", 1)
        audio_bytes = base64.b64decode(raw_audio)
        title = str(data.get("title") or "competitor_track").strip()
        return title, audio_bytes
