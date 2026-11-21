"""Tests for /agent dashboard HTML and /api/agent/* endpoints in main.py."""

from __future__ import annotations

import web
from agent.db import AgentDB


def test_agent_dashboard_and_api_endpoints(tmp_path, monkeypatch):
    db_path = tmp_path / "agent.db"
    media_dir = tmp_path / "agent_media"
    (media_dir / "backgrounds").mkdir(parents=True)
    (media_dir / "music").mkdir(parents=True)
    (media_dir / "backgrounds" / "bg1.mp4").write_bytes(b"mp4data")

    monkeypatch.setitem(web.app.config, "AGENT_DB_PATH", str(db_path))
    monkeypatch.setitem(web.app.config, "AGENT_MEDIA_DIR", str(media_dir))
    monkeypatch.setattr(web, "AUTH_ENABLED", False)

    client = web.app.test_client()

    # Dashboard HTML loads cleanly.
    page = client.get("/agent")
    assert page.status_code == 200
    assert "AutoMap Agent" in page.get_data(as_text=True)

    # Overview returns media pool and defaults.
    ov = client.get("/api/agent/overview")
    assert ov.status_code == 200
    data = ov.get_json()
    assert data["media_pool"]["backgrounds"] == ["bg1.mp4"]
    assert data["config"]["series_size"] == 4

    # Config update persists competitor channels and series size.
    cfg_resp = client.post(
        "/api/agent/config",
        json={"competitor_channels": "@rival1, @rival2", "posts_per_day": 2, "series_size": 4},
    )
    assert cfg_resp.status_code == 200
    assert cfg_resp.get_json()["config"]["competitor_channels"] == ["@rival1", "@rival2"]

    # Create experiment + competitor video and test 1-click actions.
    db = AgentDB(db_path)
    renders = db_path.parent / "agent_renders"
    renders.mkdir(parents=True, exist_ok=True)
    fake_mp4 = renders / "exp_1.mp4"
    fake_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    exp = db.create_experiment(
        topic="Average coffee consumption",
        arm="europe_zoom_reveal_fx",
        params={"background_file": "bg1.mp4"},
        mp4_path=str(fake_mp4),
    )
    comp_id = db.upsert_competitor_video(
        {
            "channel_id": "UC1",
            "channel_title": "Rival",
            "video_id": "abc123",
            "title": "Viral Map",
            "views": 50000,
            "channel_median": 5000,
            "outlier_ratio": 10.0,
            "music_hint": "Viral Beat",
        }
    )

    vid_resp = client.get(f"/api/agent/video/{exp['id']}")
    assert vid_resp.status_code == 200
    assert vid_resp.mimetype == "video/mp4"

    act_resp = client.post(f"/api/agent/action/{exp['id']}", json={"action": "private"})
    assert act_resp.status_code == 200
    assert act_resp.get_json()["command_id"] >= 1

    mus_resp = client.post(f"/api/agent/competitor/{comp_id}/import_music")
    assert mus_resp.status_code == 200
    assert mus_resp.get_json()["command_id"] >= 1

    trig_resp = client.post("/api/agent/trigger", json={"topic": "Snow cover"})
    assert trig_resp.status_code == 200

    # Path traversal guard on /api/agent/video/<id>
    bad_exp = db.create_experiment(
        topic="Bad",
        arm="europe_baseline",
        params={},
        mp4_path=str(tmp_path / "outside.mp4"),
    )
    (tmp_path / "outside.mp4").write_bytes(b"secret")
    assert client.get(f"/api/agent/video/{bad_exp['id']}").status_code == 403

    # Save secrets via web UI without exposing raw tokens in overview
    sec_resp = client.post(
        "/api/agent/secrets",
        json={"telegram_bot_token": "999:SECRET", "telegram_chat_id": "123456", "youtube_api_key": "AIzaSECRET"},
    )
    assert sec_resp.status_code == 200
    assert sec_resp.get_json()["telegram_configured"] is True
    ov2 = client.get("/api/agent/overview").get_json()
    assert ov2["telegram_configured"] is True and ov2["youtube_api_configured"] is True
    assert "999:SECRET" not in str(ov2) and "AIzaSECRET" not in str(ov2)

    # Upload & delete music file via /api/agent/media/*
    import io
    up_resp = client.post(
        "/api/agent/media/upload",
        data={"kind": "music", "files": (io.BytesIO(b"ID3music"), "beat.mp3")},
        content_type="multipart/form-data",
    )
    assert up_resp.status_code == 200 and "beat.mp3" in up_resp.get_json()["saved"]
    del_resp = client.post("/api/agent/media/delete", json={"kind": "music", "filename": "beat.mp3"})
    assert del_resp.status_code == 200


def test_oauth_callback_requires_the_browser_that_started_it(tmp_path, monkeypatch):
    import io
    import json
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setitem(web.app.config, "AGENT_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setattr(web, "AUTH_ENABLED", False)
    oauth = {
        "web": {
            "client_id": "test-client",
            "client_secret": "test-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    }
    client = web.app.test_client()

    def connect():
        return client.post("/api/agent/youtube/connect", data={
            "client_secret": (io.BytesIO(json.dumps(oauth).encode()), "client_secret.json"),
        })

    response = connect()
    assert response.status_code == 200
    params = parse_qs(urlsplit(response.json["authorization_url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    callback = f"/api/agent/youtube/callback?state={params['state'][0]}&error=access_denied"
    assert web.app.test_client().get(callback).location == "/?youtube=failed"
    assert client.get(callback).location == "/?youtube=denied"
    assert client.get(callback).location == "/?youtube=failed"

    oauth["web"]["token_uri"] = "https://example.test/token"
    assert connect().status_code == 400


def test_cross_origin_agent_mutations_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setitem(web.app.config, "AGENT_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setattr(web, "AUTH_ENABLED", False)
    response = web.app.test_client().post(
        "/api/agent/youtube/disconnect", headers={"Origin": "https://other.example"},
    )
    assert response.status_code == 403
