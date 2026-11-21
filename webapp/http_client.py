"""Share pooled HTTP connections to the private media worker.

Page capability checks and media proxy routes use worker_http, a normal requests
Session. worker_url reads the current app's WORKER_URL, allowing separate app
instances to use separate workers. Test proxy behavior by patching this session's
get/post methods. Callers choose timeouts and own streamed response cleanup.
"""
import requests
from flask import current_app

worker_http = requests.Session()


def worker_url() -> str:
    """Return the current Flask app's private worker base URL without a trailing slash.
    """
    return current_app.config['WORKER_URL']
