"""Tests for A/B effect optimizer, posting-hour selector, and competitor outlier scanner."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from agent.db import AgentDB
from agent.optimizer import (
    EFFECT_ARMS,
    AgentOptimizer,
    compute_video_score,
    infer_label_suffix,
)
from agent.youtube_intel import YouTubeIntel, extract_music_hint


def test_score_and_suffix_inference():
    assert compute_video_score(1000, None, 80.0) == 800.0
    assert compute_video_score(1000, 5000, 90.0, likes_72h=250) == 5400.0
    assert infer_label_suffix("How tall is the tallest mountain in your country?", "number") == "m"
    assert infer_label_suffix("Forest coverage percentage in Europe", "number") == "%"
    assert infer_label_suffix("Most popular drink in Europe", "text") == ""


def test_optimizer_cold_start_and_exploitation(tmp_path):
    db = AgentDB(tmp_path / "agent.db")
    opt = AgentOptimizer(db, epsilon=0.0, rng=random.Random(42))

    # Cold start walks through every untested arm first.
    arm0, reason0 = opt.choose_arm()
    assert arm0.name == EFFECT_ARMS[0].name
    assert "cold-start" in reason0

    # Seed measured experiments for all arms with europe_flags_reveal_fx clearly winning.
    for idx, arm in enumerate(EFFECT_ARMS):
        exp = db.create_experiment(topic=f"Topic {idx}", arm=arm.name, params={})
        score = 50000.0 if arm.name == "europe_flags_reveal_fx" else 1000.0
        db.update_experiment(exp["id"], status="published", views_72h=int(score), avg_view_pct=85.0, score=score)

    winner, reason = opt.choose_arm()
    assert winner.name == "europe_flags_reveal_fx"
    assert "winner" in reason


def test_optimizer_posting_hours_fallback_and_learned(tmp_path):
    db = AgentDB(tmp_path / "agent.db")
    opt = AgentOptimizer(db)
    assert opt.choose_posting_hours(2, [14, 20]) == [14, 20]

    # Add 12 measured experiments where 11:00 and 19:00 dominate (12:00 is too close to 11:00).
    for i in range(4):
        e1 = db.create_experiment(topic=f"A{i}", arm="europe_baseline", params={}, posting_hour=11)
        db.update_experiment(e1["id"], status="published", score=9000.0)
        e2 = db.create_experiment(topic=f"B{i}", arm="europe_baseline", params={}, posting_hour=12)
        db.update_experiment(e2["id"], status="published", score=8500.0)
        e3 = db.create_experiment(topic=f"C{i}", arm="europe_baseline", params={}, posting_hour=19)
        db.update_experiment(e3["id"], status="published", score=8000.0)

    assert opt.choose_posting_hours(2, [14, 20]) == [11, 19]


def test_youtube_intel_competitor_outlier_and_music_hint(tmp_path):
    db = AgentDB(tmp_path / "agent.db")
    mock_http = Mock()

    ch_resp = Mock(ok=True)
    ch_resp.json.return_value = {
        "items": [
            {
                "id": "UC123",
                "snippet": {"title": "Map Rival"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
            }
        ]
    }
    pl_resp = Mock(ok=True)
    pl_resp.json.return_value = {
        "items": [
            {"contentDetails": {"videoId": "v1"}},
            {"contentDetails": {"videoId": "v2"}},
            {"contentDetails": {"videoId": "v3"}},
        ]
    }
    vid_resp = Mock(ok=True)
    now = datetime.now(timezone.utc)
    vid_resp.json.return_value = {
        "items": [
            {
                "id": "v1",
                "snippet": {"title": "Normal map", "description": "", "publishedAt": (now - timedelta(days=3)).isoformat()},
                "statistics": {"viewCount": "1000"},
            },
            {
                "id": "v2",
                "snippet": {"title": "Another normal map", "description": "", "publishedAt": (now - timedelta(days=2)).isoformat()},
                "statistics": {"viewCount": "1200"},
            },
            {
                "id": "v3",
                "snippet": {
                    "title": "Tallest mountain in Europe #shorts",
                    "description": "Music: Epic Phonk Beat - DJ Map\n#maps",
                    "publishedAt": (now - timedelta(days=1)).isoformat(),
                },
                "statistics": {"viewCount": "12000"},
            },
        ]
    }
    mock_http.get.side_effect = [ch_resp, pl_resp, vid_resp]

    intel = YouTubeIntel(db, api_key="test-key", http=mock_http)
    saved = intel.scan_competitor_channel("@maprival")
    assert len(saved) == 3

    outliers = db.list_competitor_outliers(min_ratio=2.0)
    assert len(outliers) == 1
    assert outliers[0]["video_id"] == "v3"
    assert outliers[0]["outlier_ratio"] == 10.0
    assert "Epic Phonk Beat" in outliers[0]["music_hint"]
    assert "Synthwave" in extract_music_hint("Map", "Track: Synthwave Nights")
