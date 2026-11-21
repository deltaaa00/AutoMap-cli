"""Assemble the browser-facing Flask app without starting its HTTP server.

``main.py`` calls create_app; tests can create independent application instances.
The factory anchors templates, static files and instance data to the project,
loads configuration, installs authentication and JSON validation, and registers
page, generation, batch, zoom and media Blueprints. YouTubeUploads is stored in
app.extensions for routes sharing the current browser session. Add new route
modules to the registration list here; put endpoint logic in those modules.
"""
import os

import dotenv
from flask import Flask, jsonify

from webapp.auth import init_web_auth
from webapp.youtube import YouTubeUploads
from .http_client import worker_http
from .inputs import validate_api_json_body
from .paths import BASE_DIR


def create_app():
    """Build a configured Flask app without binding a socket.

    Each call reads environment/.env settings, creates its own session configuration
    and YouTube extension, and registers the shared route definitions. Instance data
    is persistent on disk; AI/progress caches are process-local. Use this factory in
    tests and WSGI integrations; the root launcher decides which server to run.
    """
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / 'templates'),
        static_folder=str(BASE_DIR / 'static'),
        instance_path=str(BASE_DIR / 'instance'),
    )
    env_file = BASE_DIR / '.env'
    settings = dotenv.dotenv_values(env_file) if env_file.is_file() else {}
    gemini_key = (os.environ.get('GEMINI_API_KEY') or '').strip()
    if not gemini_key:
        gemini_key = (settings.get('GEMINI_API_KEY') or '').strip()
    app.config.update(
        MAX_CONTENT_LENGTH=64 * 1024 * 1024,
        ENV_FILE=env_file,
        WORKER_URL=(os.environ.get('YTDLP_URL') or 'http://127.0.0.1:8080').rstrip('/'),
        NEEDS_API_KEY=not gemini_key or gemini_key.startswith('your_'),
    )
    init_web_auth(app, settings)
    app.extensions['youtube_uploads'] = YouTubeUploads(app, app.config['WORKER_URL'], worker_http)
    app.before_request(validate_api_json_body)

    @app.errorhandler(413)
    def request_too_large(error):
        return jsonify(status='error', error='Upload exceeds the total request size limit.'), 413

    from . import pages, generation, batch, zoom, media

    for module in (pages, generation, batch, zoom, media):
        app.register_blueprint(module.routes)
    return app
