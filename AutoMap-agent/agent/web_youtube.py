"""YouTube OAuth Blueprint and credential-file paths for the agent dashboard.

The parent web app registers these routes and handles authentication; the
callback endpoint name includes the ``youtube.`` Blueprint namespace.
Connection accepts uploaded Google client JSON, records pending flow data next
to AgentDB, and stores flow state in the browser session. Callback exchanges
the code with Google and writes the token consumed by ``youtube_intel.py``.
AGENT_YOUTUBE_REDIRECT_URI and AGENT_YOUTUBE_TOKEN_PATH override URL/file defaults.
Disconnect removes the local token; it does not revoke authorization at Google."""

import json
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, redirect, request, session, url_for

from .web_services import get_db
from .youtube_intel import AGENT_YOUTUBE_SCOPES

youtube = Blueprint("youtube", __name__)


def parse_oauth_client_config(raw_obj: object) -> dict[str, Any]:
    """Validate an uploaded Google OAuth client_secret.json object."""
    if not isinstance(raw_obj, dict):
        raise ValueError("Invalid client_secret.json format.")
    for section in ("web", "installed"):
        block = raw_obj.get(section)
        if isinstance(block, dict) and block.get("client_id") and block.get("client_secret"):
            return {section: block}
    raise ValueError("client_secret.json must contain a 'web' or 'installed' OAuth client.")


def token_path() -> Path:
    env_tok = os.environ.get("AGENT_YOUTUBE_TOKEN_PATH", "").strip()
    if env_tok:
        return Path(env_tok)
    return get_db().path.parent / "agent_youtube_token.json"


def oauth_redirect_uri() -> str:
    explicit = (os.environ.get("AGENT_YOUTUBE_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    return url_for("youtube.api_agent_youtube_callback", _external=True)


@youtube.post("/api/agent/youtube/connect")
def api_agent_youtube_connect():
    """Start Google OAuth flow for agent Upload + Analytics scopes using uploaded client_secret.json."""
    from google_auth_oauthlib.flow import Flow

    uploaded = request.files.get("client_secret")
    if uploaded is None:
        return jsonify(status="error", error="Choose a client_secret.json file first."), 400
    raw = uploaded.read(128 * 1024)
    try:
        cfg_obj = parse_oauth_client_config(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return jsonify(status="error", error=str(exc)), 400

    redirect_uri = oauth_redirect_uri()
    host = (urlsplit(redirect_uri).hostname or "").lower()
    if "installed" in cfg_obj and host not in ("localhost", "127.0.0.1"):
        return jsonify(
            status="error",
            error="Desktop app credentials require localhost. For a hosted site use a Web application client with the displayed Redirect URI.",
        ), 400

    flow = Flow.from_client_config(cfg_obj, scopes=list(AGENT_YOUTUBE_SCOPES), redirect_uri=redirect_uri)
    state = secrets.token_urlsafe(24)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    pending_path = get_db().path.parent / "agent_oauth_pending.json"
    pending_path.write_text(
        json.dumps(
            {
                "state": state,
                "config": cfg_obj,
                "redirect_uri": redirect_uri,
                "code_verifier": getattr(flow, "code_verifier", None),
            }
        ),
        encoding="utf-8",
    )
    session["agent_oauth_state"] = state
    return jsonify(status="success", authorization_url=auth_url)


@youtube.get("/api/agent/youtube/callback")
def api_agent_youtube_callback():
    """Complete Google OAuth flow for the agent and persist its credentials token."""
    from google_auth_oauthlib.flow import Flow

    if request.args.get("error"):
        return redirect("/?youtube=denied")
    state = str(request.args.get("state") or "")
    code = str(request.args.get("code") or "")
    pending_path = get_db().path.parent / "agent_oauth_pending.json"
    if not code or not state or not pending_path.is_file():
        return redirect("/?youtube=failed")
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        if pending.get("state") != state or (
            session.get("agent_oauth_state") and session.get("agent_oauth_state") != state
        ):
            return redirect("/?youtube=failed")
        flow = Flow.from_client_config(
            pending["config"],
            scopes=list(AGENT_YOUTUBE_SCOPES),
            redirect_uri=pending["redirect_uri"],
            state=state,
        )
        if pending.get("code_verifier"):
            flow.code_verifier = pending["code_verifier"]
        flow.fetch_token(code=code)
        tok_path = token_path()
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        tok_path.write_text(flow.credentials.to_json(), encoding="utf-8")
        pending_path.unlink(missing_ok=True)
        session.pop("agent_oauth_state", None)
        return redirect("/?youtube=connected")
    except Exception:
        return redirect("/?youtube=failed")


@youtube.post("/api/agent/youtube/disconnect")
def api_agent_youtube_disconnect():
    """Remove persisted agent YouTube OAuth token."""
    tok_path = token_path()
    tok_path.unlink(missing_ok=True)
    return jsonify(status="success", message="Agent YouTube OAuth disconnected.")
