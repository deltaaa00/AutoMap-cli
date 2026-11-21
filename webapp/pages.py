"""Serve the map workspace, Video Studio and API Manager HTML pages.

Registered by create_app. These routes supply fonts, provider-key presence,
codec capabilities and the agent link; server credential values are never
rendered into templates. Studio and API Manager initialize YouTube session state
before their JavaScript starts parallel requests. Change page behavior in the
matching templates/static files; keep map generation in webapp.generation and
worker proxy endpoints in webapp.media.
"""
import os
from flask import Blueprint, current_app, request, jsonify, render_template
from requests import RequestException
from .http_client import worker_http, worker_url
from .inputs import _available_fonts, _server_api_key, _needs_api_key


routes = Blueprint('pages', __name__)


@routes.route('/')
def main():
    """Serve the main single-page map generator UI."""
    return render_template("index.html")


@routes.route('/studio')
def studio():
    """Serve the dedicated Video Studio page (music downloader)."""
    youtube_uploads = current_app.extensions['youtube_uploads']
    # Establish identity before the page starts parallel API requests.
    youtube_uploads.owner()
    youtube_uploads.csrf()
    return render_template("video.html", title_fonts=_available_fonts())


@routes.get('/api-manager')
def api_manager():
    """Manage browser API credentials and the existing YouTube OAuth connection."""
    youtube_uploads = current_app.extensions['youtube_uploads']
    youtube_uploads.owner()
    youtube_uploads.csrf()
    response = current_app.make_response(render_template('api_manager.html',
        server_keys={provider: bool(_server_api_key(provider)) for provider in ('gemini', 'deepseek')},
        search_server_configured=any(
            str(os.environ.get(name) or '').strip() and not str(os.environ.get(name)).lower().startswith('your_')
            for name in ('TAVILY_API_KEY', 'BRAVE_SEARCH_API_KEY'))))
    response.headers['Cache-Control'] = 'no-store'
    return response


@routes.route('/api/fonts/')
@routes.route('/api/fonts')
def api_fonts():
    """Expose the fonts discovered in resources/fonts.

    Response:
        { "fonts": ["Bips.ttf", "DIN Condensed.ttf", ...], "count": 4 }
    """
    fonts = _available_fonts()
    return jsonify({"fonts": fonts, "count": len(fonts)})


@routes.route('/api/codecs')
def api_codecs():
    """Ask the render worker which encoders its FFmpeg and hardware can use."""

    try:
        response = worker_http.get(f'{worker_url()}/api/codecs', timeout=20)
        return jsonify(response.json()), response.status_code
    except (RequestException, ValueError):
        return jsonify(error='Could not check render worker codecs. Use libx264 (CPU).'), 502


@routes.route('/api/status')
def api_status():
    """Report whether the selected provider already has a server-side key.

    Response:
        { "needs_api_key": true }  -> the UI must ask the user for a key
    """
    provider = request.args.get('provider', 'gemini')
    if provider not in ('gemini', 'deepseek'):
        return jsonify(status='error', error='Unknown AI provider.'), 400
    required = _needs_api_key() if provider == 'gemini' else not _server_api_key(provider)
    return jsonify({"needs_api_key": required})


@routes.context_processor
def _inject_agent_url() -> dict[str, str]:
    return {"agent_url": (os.environ.get("AGENT_URL") or "http://localhost:4000").strip().rstrip("/")}
