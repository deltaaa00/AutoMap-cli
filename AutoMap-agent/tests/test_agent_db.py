"""Tests for agent SQLite storage and 4-video series MediaPool rotation."""

from __future__ import annotations

import pytest

from agent.db import AgentDB
from agent.media_pool import MAX_MEDIA_BYTES, MediaPool


def test_agent_db_experiments_and_commands_lifecycle(tmp_path):
    db = AgentDB(tmp_path / "agent.db")
    cfg = db.get_config()
    assert cfg["posts_per_day"] == 2
    assert cfg["series_size"] == 4

    updated = db.update_config({"competitor_channels": ["@maps1", "@maps2"], "posts_per_day": 3})
    assert updated["competitor_channels"] == ["@maps1", "@maps2"]
    assert updated["posts_per_day"] == 3

    exp = db.create_experiment(
        topic="Tallest mountains in Europe",
        arm="europe_zoom_reveal_fx",
        params={"map_type": "default", "reveal_enabled": True},
        rationale="Cold start",
        posting_hour=18,
    )
    assert exp["status"] == "pending_approval"
    assert exp["params"]["reveal_enabled"] is True
    assert db.count_created_experiments() == 1

    db.update_experiment(exp["id"], status="published", views_72h=12000, avg_view_pct=88.5, score=10620.0)
    board = db.get_arm_leaderboard()
    assert len(board) == 1
    assert board[0]["arm"] == "europe_zoom_reveal_fx"
    assert board[0]["measured_runs"] == 1

    cmd_id = db.enqueue_command("trigger_generate", {"topic": "Coffee consumption"})
    pending = db.list_pending_commands()
    assert len(pending) == 1 and pending[0]["id"] == cmd_id
    db.complete_command(cmd_id, status="done", message="ok")
    assert db.list_pending_commands() == []


def test_media_pool_rotates_in_blocks_of_four_videos(tmp_path):
    pool = MediaPool(tmp_path / "agent_media", series_size=4)
    pool.ensure_dirs()
    (pool.backgrounds_dir / "bg_1.mp4").write_bytes(b"bg1")
    (pool.backgrounds_dir / "bg_2.mp4").write_bytes(b"bg2")
    (pool.music_dir / "track_1.mp3").write_bytes(b"m1")
    (pool.music_dir / "track_2.mp3").write_bytes(b"m2")

    # First 4 videos (0..3) must keep bg_1 + track_1
    for i in range(4):
        sel = pool.select_for_video_index(i)
        assert sel.background is not None and sel.background.name == "bg_1.mp4"
        assert sel.music is not None and sel.music.name == "track_1.mp3"
        assert sel.series_index == 0
        assert sel.slot_in_series == i + 1

    # Next 4 videos (4..7) must switch to bg_2 + track_2
    for i in range(4, 8):
        sel = pool.select_for_video_index(i)
        assert sel.background is not None and sel.background.name == "bg_2.mp4"
        assert sel.music is not None and sel.music.name == "track_2.mp3"
        assert sel.series_index == 1

    # Video 8 wraps back to bg_1 + track_1
    sel8 = pool.select_for_video_index(8)
    assert sel8.background is not None and sel8.background.name == "bg_1.mp4"
    assert sel8.music is not None and sel8.music.name == "track_1.mp3"


def test_media_pool_saves_imported_music_and_rejects_oversize(tmp_path):
    pool = MediaPool(tmp_path / "agent_media", series_size=4)
    saved = pool.save_music_bytes("Viral Track!", b"ID3dummydata")
    assert saved.is_file() and saved.parent == pool.music_dir
    assert saved.name == "Viral_Track.mp3"

    saved2 = pool.save_music_bytes("Viral Track!", b"ID3dummydata2")
    assert saved2.name == "Viral_Track_2.mp3"

    with pytest.raises(ValueError):
        pool.save_music_bytes("TooBig", b"x" * (MAX_MEDIA_BYTES + 1))
