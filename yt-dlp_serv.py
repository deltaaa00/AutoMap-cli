"""Start the private AutoMap media worker on its default port, 8080.

Run this alongside the web application, not instead of it. The Flask app and
media endpoints live in ``media.worker``; image composition and animations live
in neighboring media modules. This launcher selects the shared server runtime.
The web app reaches this service through its YTDLP_URL setting.
"""
from media.worker import app


if __name__ == '__main__':
    from server_runtime import run_app

    run_app(app, default_port=8080)
