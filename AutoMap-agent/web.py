"""Assemble the standalone agent dashboard and its HTTP API.

Gunicorn loads ``web:app``; running this file starts the development HTTP server.
This module owns login/session handling, overview, configuration, and queued
approval actions. Media and OAuth routes are registered from ``agent.web_media``
and ``agent.web_youtube``. Dashboard sections are Jinja partials under
templates/agent/; feature controllers and CSS are served from static/agent/.
Configuration comes from the adjacent .env file and
AGENT_* variables. Requests read/write AgentDB; queued work is executed by
``agent.runner``, rather than by a background thread in this web process."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from agent.optimizer import AgentOptimizer
from agent.telegram_bot import TelegramBot
from agent.web_media import media
from agent.web_services import get_db, get_media_pool
from agent.web_youtube import oauth_redirect_uri, token_path, youtube

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AVAILABLE_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-4-hydrogen",
    "gemini-4-carbon",
    "gemini-4-argon",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
    "deepseek-flash",
    "deepseek-v4-pro",
)


def provider_for_model(model: str) -> str:
    """Return 'deepseek' for DeepSeek models, otherwise 'gemini'."""
    model_name = str(model or "").strip().lower()
    if model_name.startswith("deepseek-"):
        return "deepseek"
    return "gemini"


app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.secret_key = (os.environ.get("AGENT_SECRET_KEY") or secrets.token_hex(32)).strip()
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024
app.register_blueprint(media)
app.register_blueprint(youtube)

SERVER_MODE = os.environ.get("AGENT_SERVER", "FALSE").strip().upper() in ("1", "TRUE", "YES", "ON")
AUTH_USER = os.environ.get("AGENT_WEB_USERNAME", "admin").strip()
AUTH_PASS = os.environ.get("AGENT_WEB_PASSWORD", "").strip()
AUTH_ENABLED = SERVER_MODE and bool(AUTH_PASS)


def _public_automap_url(cfg: dict[str, Any]) -> str:
    configured = str(cfg.get("automap_url") or "").strip()
    if configured:
        return configured.rstrip("/")
    public_env = (os.environ.get("AUTOMAP_PUBLIC_URL") or "").strip()
    if public_env:
        return public_env.rstrip("/")
    raw = (os.environ.get("AUTOMAP_URL") or "http://localhost:3000").strip().rstrip("/")
    return raw.replace("http://host.docker.internal:", "http://localhost:")


@app.before_request
def _require_auth():
    if not AUTH_ENABLED:
        return None
    if request.endpoint in ("login_page", "logout_page", "youtube.api_agent_youtube_callback", "healthz", "static"):
        return None
    if session.get("agent_authed") is True:
        return None
    if request.path.startswith("/api/"):
        return jsonify(status="error", error="Authentication required."), 401
    return redirect(url_for("login_page", next=request.path))


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if not AUTH_ENABLED:
        return redirect(url_for("agent_home"))
    error = ""
    if request.method == "POST":
        user = str(request.form.get("username") or "").strip()
        pwd = str(request.form.get("password") or "")
        if hmac.compare_digest(user, AUTH_USER) and hmac.compare_digest(pwd, AUTH_PASS):
            session["agent_authed"] = True
            return redirect(url_for("agent_home"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout_page():
    session.pop("agent_authed", None)
    return redirect(url_for("login_page"))


@app.get("/")
@app.get("/agent")
def agent_home():
    """Render the standalone Autonomous YouTube Shorts Agent dashboard."""
    cfg = get_db().get_config()
    return render_template(
        "agent.html",
        automap_public_url=_public_automap_url(cfg),
        auth_enabled=AUTH_ENABLED,
    )


@app.get("/api/agent/overview")
def api_agent_overview():
    """Return agent status, pending approvals, A/B leaderboard, competitor outliers, and media pool."""
    db = get_db()
    cfg = db.get_config()
    sec = db.get_secrets()
    pool = get_media_pool(int(cfg.get("series_size") or 4))
    opt = AgentOptimizer(db)
    total_created = db.count_created_experiments()
    hours = opt.choose_posting_hours(
        posts_per_day=int(cfg.get("posts_per_day") or 2),
        fallback_hours=cfg.get("default_hours"),
    )
    return jsonify({
        "status": "success",
        "config": cfg,
        "available_models": list(AVAILABLE_MODELS),
        "recommended_hours": hours,
        "media_pool": pool.summary(total_created),
        "pending_queue": db.list_experiments(status="pending_approval", limit=20),
        "experiments": db.list_experiments(limit=40),
        "leaderboard": db.get_arm_leaderboard(),
        "competitors": db.list_competitor_outliers(limit=30),
        "pending_commands": db.list_pending_commands(limit=10),
        "telegram_configured": bool(sec["telegram_bot_token"] and sec["telegram_chat_id"]),
        "telegram_token_set": bool(sec["telegram_bot_token"]),
        "telegram_chat_id": sec["telegram_chat_id"],
        "youtube_api_configured": bool(sec["youtube_api_key"]),
        "gemini_api_configured": bool(sec["gemini_api_key"]),
        "deepseek_api_configured": bool(sec["deepseek_api_key"]),
        "tavily_api_configured": bool(sec["tavily_api_key"]),
        "automap_auth_configured": bool(sec["automap_login"] and sec["automap_password"]),
        "automap_public_url": _public_automap_url(cfg),
        "youtube_oauth_connected": token_path().is_file(),
        "agent_redirect_uri": oauth_redirect_uri(),
    })


@app.post("/api/agent/secrets")
def api_agent_secrets():
    """Save Telegram, YouTube, AI, and AutoMap credentials from the agent UI."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(status="error", error="Invalid JSON payload."), 400
    updates: dict[str, str] = {}
    for key in (
        "telegram_bot_token",
        "telegram_chat_id",
        "youtube_api_key",
        "gemini_api_key",
        "deepseek_api_key",
        "tavily_api_key",
        "automap_login",
        "automap_password",
    ):
        val = data.get(key)
        if isinstance(val, str) and (val.strip() or data.get(f"clear_{key}") is True):
            updates[key] = val.strip()
    db = get_db()
    status_flags = db.update_secrets(updates)
    return jsonify(status="success", **status_flags)


@app.post("/api/agent/telegram/test")
def api_agent_telegram_test():
    """Send a quick test message to the configured Telegram chat."""
    db = get_db()
    sec = db.get_secrets()
    bot = TelegramBot(token=sec["telegram_bot_token"], chat_id=sec["telegram_chat_id"])
    if not bot.enabled:
        return jsonify(status="error", error="Save Telegram Bot Token and Chat ID first."), 400
    msg_id = bot.send_message("✅ AutoMap Agent is connected to your Telegram!")
    if msg_id is None:
        return jsonify(
            status="error",
            error="Telegram rejected the message. Check token, Chat ID, and make sure you pressed /start in the bot.",
        ), 502
    return jsonify(status="success", message="Test message sent to Telegram!")


@app.post("/api/agent/config")
def api_agent_config():
    """Update agent configuration."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(status="error", error="Invalid JSON payload."), 400
    updates: dict[str, object] = {}
    if "competitor_channels" in data:
        raw_ch = data["competitor_channels"]
        if isinstance(raw_ch, str):
            channels = [c.strip() for c in raw_ch.replace("\n", ",").split(",") if c.strip()]
        elif isinstance(raw_ch, list):
            channels = [str(c).strip() for c in raw_ch if str(c).strip()]
        else:
            channels = []
        updates["competitor_channels"] = channels[:30]
    if "posts_per_day" in data:
        try:
            updates["posts_per_day"] = max(1, min(6, int(data["posts_per_day"])))
        except (TypeError, ValueError):
            return jsonify(status="error", error="posts_per_day must be an integer between 1 and 6."), 400
    if "series_size" in data:
        try:
            updates["series_size"] = max(1, min(20, int(data["series_size"])))
        except (TypeError, ValueError):
            return jsonify(status="error", error="series_size must be an integer between 1 and 20."), 400
    if "default_privacy" in data and str(data["default_privacy"]) in ("private", "unlisted", "public"):
        updates["default_privacy"] = str(data["default_privacy"])
    if "timezone" in data and str(data["timezone"]).strip():
        updates["timezone"] = str(data["timezone"]).strip()[:64]
    if "ai_provider" in data and str(data["ai_provider"]).strip().lower() in ("gemini", "deepseek"):
        updates["ai_provider"] = str(data["ai_provider"]).strip().lower()
    if "ai_model" in data and str(data["ai_model"]).strip():
        model_val = str(data["ai_model"]).strip()[:80]
        updates["ai_model"] = model_val
        if "ai_provider" not in updates:
            updates["ai_provider"] = provider_for_model(model_val)
    if "watermark_text" in data:
        updates["watermark_text"] = str(data["watermark_text"] or "").strip()[:40]
    if "description_template" in data:
        updates["description_template"] = str(data["description_template"] or "").strip()[:4000]
    if "automap_url" in data:
        raw_url = str(data["automap_url"] or "").strip().rstrip("/")
        if raw_url and not raw_url.startswith(("http://", "https://")):
            return jsonify(status="error", error="AutoMap URL must start with http:// or https://"), 400
        updates["automap_url"] = raw_url[:300]
    if "video_duration" in data:
        try:
            updates["video_duration"] = max(9, min(15, int(data["video_duration"])))
        except (TypeError, ValueError):
            pass
    if "web_search_enabled" in data:
        updates["web_search_enabled"] = bool(data["web_search_enabled"])
    if "max_search_requests" in data:
        try:
            updates["max_search_requests"] = max(1, min(5, int(data["max_search_requests"])))
        except (TypeError, ValueError):
            return jsonify(status="error", error="max_search_requests must be between 1 and 5."), 400

    db = get_db()
    updated = db.update_config(updates)
    return jsonify(status="success", config=updated)


@app.post("/api/agent/action/<int:exp_id>")
def api_agent_experiment_action(exp_id: int):
    """Queue a 1-click user decision for an experiment."""
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "").strip().lower()
    if action not in ("publish", "schedule", "private", "regenerate", "reject"):
        return jsonify(status="error", error="Invalid action."), 400
    db = get_db()
    exp = db.get_experiment(exp_id)
    if exp is None:
        return jsonify(status="error", error="Experiment not found."), 404
    if exp["status"] != "pending_approval" or exp["youtube_video_id"]:
        return jsonify(status="error", error="Short is no longer awaiting approval."), 409
    cmd_id = db.enqueue_command("experiment_action", {"exp_id": exp_id, "action": action})
    return jsonify(status="success", command_id=cmd_id, message=f"Queued '{action}' for Short #{exp_id}.")


@app.post("/api/agent/trigger")
def api_agent_trigger():
    """Manually queue generating a candidate Short right now."""
    data = request.get_json(silent=True) or {}
    topic = str(data.get("topic") or "").strip()[:140]
    db = get_db()
    cmd_id = db.enqueue_command("trigger_generate", {"topic": topic})
    msg = f'Queued video for idea: "{topic}"' if topic else "Video generation queued."
    return jsonify(status="success", command_id=cmd_id, message=msg)


@app.post("/api/agent/sync")
def api_agent_sync():
    """Manually queue competitor & YouTube analytics synchronization."""
    db = get_db()
    cmd_id = db.enqueue_command("sync_now", {})
    return jsonify(status="success", command_id=cmd_id, message="Analytics & competitor sync queued.")


@app.post("/api/agent/competitor/<int:comp_id>/import_music")
def api_agent_import_competitor_music(comp_id: int):
    """Queue importing a viral competitor video's audio track into agent_media/music/."""
    db = get_db()
    if db.get_competitor_video(comp_id) is None:
        return jsonify(status="error", error="Competitor video not found."), 404
    cmd_id = db.enqueue_command("import_music", {"comp_id": comp_id})
    return jsonify(status="success", command_id=cmd_id, message="Track import queued.")


@app.get("/api/agent/video/<int:exp_id>")
def api_agent_video(exp_id: int):
    """Stream a rendered candidate Short MP4 for in-browser preview."""
    db = get_db()
    exp = db.get_experiment(exp_id)
    if exp is None or not exp.get("mp4_path"):
        return jsonify(status="error", error="Video not found."), 404
    mp4 = Path(str(exp["mp4_path"])).resolve()
    renders_root = (db.path.parent / "agent_renders").resolve()
    try:
        mp4.relative_to(renders_root)
    except ValueError:
        return jsonify(status="error", error="Invalid video path."), 403
    if not mp4.is_file():
        return jsonify(status="error", error="Rendered file missing."), 404
    return send_file(mp4, mimetype="video/mp4")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4000"))
    app.run(host="0.0.0.0", port=port, debug=False)
