"""Start the browser-facing AutoMap Flask application.

Run ``python main.py`` or import ``app`` from a WSGI server. Application creation,
authentication and Blueprint registration belong to ``webapp.create_app``; this
file intentionally contains no routes. The media worker is a separate process,
started with ``python yt-dlp_serv.py``. Both launchers use ``server_runtime``.
"""
from webapp import create_app

app = create_app()


if __name__ == '__main__':
    from server_runtime import run_app

    run_app(app)
