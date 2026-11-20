"""Local media pool manager for backgrounds and music with 4-video series rotation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_MEDIA_BYTES = 15 * 1024 * 1024
BACKGROUND_EXTS = {".mp4", ".mov", ".webm"}
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def default_media_root() -> Path:
    """Return the root directory for agent_media (backgrounds/ and music/)."""
    env_dir = os.environ.get("AGENT_MEDIA_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "agent_media"


@dataclass(frozen=True)
class MediaSelection:
    """Selected background video and optional music track for one video in a series."""

    background: Path | None
    music: Path | None
    series_index: int
    slot_in_series: int
    series_size: int


class MediaPool:
    """Manages agent_media/backgrounds and agent_media/music in 4-video blocks."""

    def __init__(self, root: str | Path | None = None, *, series_size: int = 4) -> None:
        self.root = Path(root) if root is not None else default_media_root()
        self.backgrounds_dir = self.root / "backgrounds"
        self.music_dir = self.root / "music"
        self.series_size = max(1, int(series_size))

    def ensure_dirs(self) -> None:
        """Create backgrounds/ and music/ folders if missing."""
        self.backgrounds_dir.mkdir(parents=True, exist_ok=True)
        self.music_dir.mkdir(parents=True, exist_ok=True)

    def _list_valid(self, directory: Path, allowed_exts: set[str]) -> list[Path]:
        if not directory.is_dir():
            return []
        files = [
            p
            for p in sorted(directory.iterdir(), key=lambda x: x.name.lower())
            if p.is_file()
            and p.suffix.lower() in allowed_exts
            and 0 < p.stat().st_size <= MAX_MEDIA_BYTES
        ]
        return files

    def list_backgrounds(self) -> list[Path]:
        """List eligible background videos (<= 15 MB)."""
        return self._list_valid(self.backgrounds_dir, BACKGROUND_EXTS)

    def list_music(self) -> list[Path]:
        """List eligible music tracks (<= 15 MB)."""
        return self._list_valid(self.music_dir, MUSIC_EXTS)

    def select_for_video_index(self, video_index: int) -> MediaSelection:
        """Pick background & music using blocks of ``series_size`` (default 4) videos.

        Videos 0..3 use (backgrounds[0], music[0]), videos 4..7 use (backgrounds[1], music[1]),
        wrapping cleanly when the pool ends.
        """
        idx = max(0, int(video_index))
        series_index = idx // self.series_size
        slot_in_series = (idx % self.series_size) + 1

        backgrounds = self.list_backgrounds()
        tracks = self.list_music()

        bg = backgrounds[series_index % len(backgrounds)] if backgrounds else None
        track = tracks[series_index % len(tracks)] if tracks else None
        return MediaSelection(
            background=bg,
            music=track,
            series_index=series_index,
            slot_in_series=slot_in_series,
            series_size=self.series_size,
        )

    def summary(self, video_index: int = 0) -> dict[str, object]:
        """Return a JSON-serializable summary for the /agent web UI."""
        backgrounds = self.list_backgrounds()
        tracks = self.list_music()
        current = self.select_for_video_index(video_index)
        return {
            "root": str(self.root),
            "series_size": self.series_size,
            "backgrounds": [p.name for p in backgrounds],
            "music": [p.name for p in tracks],
            "current_background": current.background.name if current.background else None,
            "current_music": current.music.name if current.music else None,
            "series_index": current.series_index + 1,
            "slot_in_series": current.slot_in_series,
        }

    def save_music_bytes(self, title: str, data: bytes, *, ext: str = ".mp3") -> Path:
        """Save downloaded competitor audio into agent_media/music/ cleanly."""
        if not data or len(data) > MAX_MEDIA_BYTES:
            raise ValueError("Audio file is empty or exceeds the 15 MB limit.")
        self.ensure_dirs()
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", (title or "competitor_track").strip()).strip("_.")
        slug = (slug or "competitor_track")[:64]
        suffix = ext if ext.startswith(".") else f".{ext}"
        if suffix.lower() not in MUSIC_EXTS:
            suffix = ".mp3"
        dest = self.music_dir / f"{slug}{suffix}"
        counter = 2
        while dest.exists():
            dest = self.music_dir / f"{slug}_{counter}{suffix}"
            counter += 1
        dest.write_bytes(data)
        return dest
