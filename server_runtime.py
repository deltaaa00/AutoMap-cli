# Copyright 2025-2026 deltaaa00
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared development and production launch settings for the web services."""

import os
from pathlib import Path

from dotenv import dotenv_values


def run_app(app, default_port=5000):
    """Use Waitress when SERVER=TRUE, otherwise use Flask's development server."""
    file_settings = dotenv_values(Path(__file__).parent / ".env")

    def setting(name, default):
        return os.environ.get(name, file_settings.get(name) or default)

    port = int(setting("PORT", str(default_port)))
    host = setting("BIND_HOST", "127.0.0.1")
    production = setting("SERVER", "FALSE") == "TRUE"
    if production:
        from waitress import serve

        app.debug = False
        serve(app, host=host, port=port, threads=8, channel_timeout=600,
              max_request_body_size=app.config.get("MAX_CONTENT_LENGTH") or 64 * 1024 * 1024)
    else:
        app.run(host=host, port=port, debug=False)
