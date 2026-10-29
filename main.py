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

"""Flask web interface for AutoMap.

Serves the single-page UI in ``templates/index.html`` and exposes a small JSON
API used by that UI. The API key handling is the interesting part: if a ``.env``
file with a real ``GEMINI_API_KEY`` is present, the web UI never asks for a key.
If it is missing, ``/api/status`` reports it and the UI shows a key input, whose
value is forwarded to the generation request only.
"""

import os
import re
import time
from pathlib import Path

# auto_map() loads fonts/maps via *relative* paths ("resources/fonts", ...),
# so the process must run from the AutoMap-cli project directory. Anchor the
# working directory here to make the web API independent of how it is launched.
BASE_DIR = Path(__file__).parent
os.chdir(BASE_DIR)

from dotenv import dotenv_values
from map.auto_map import auto_map

from flask import Flask, request, jsonify, send_file, render_template

FONTS_DIR = BASE_DIR / "resources" / "fonts"

env = BASE_DIR / ".env"
key = (dotenv_values(env).get("GEMINI_API_KEY") or "").strip() if env.is_file() else ""
needs_api_key = not key or key.startswith("your_")


def _available_fonts():
    """Return the list of usable font files from resources/fonts.

    Mirrors ``cli._available_fonts`` so the API and the CLI agree on what is
    selectable. Discovery is filename-based: only ``.ttf`` / ``.otf`` files
    (case-insensitive extension) are returned, sorted for stable output.
    """
    if not FONTS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in FONTS_DIR.iterdir() if p.suffix.lower() in (".ttf", ".otf")
    )


app = Flask(__name__)

@app.route('/')
def main():
    return render_template("index.html")

@app.route('/api/fonts/')
@app.route('/api/fonts')
def api_fonts():
    """Expose the fonts discovered in resources/fonts.

    Response:
        { "fonts": ["Bips.ttf", "DIN Condensed.ttf", ...], "count": 4 }
    """
    fonts = _available_fonts()
    return jsonify({"fonts": fonts, "count": len(fonts)})


@app.route('/api/status')
def api_status():
    """Report whether the server already has a usable Gemini API key.

    Response:
        { "needs_api_key": true }  -> the UI must ask the user for a key
    """
    return jsonify({"needs_api_key": needs_api_key})


@app.route('/api/generate/', methods=['POST'])
@app.route('/api/generate', methods=['POST'])
def generate():
    """Generate a map from the JSON body and return it as a data URL.

    Request JSON:
        { "topic", "mode", "map_type", "gsm", "extra_option", "model", "font" }

    Response JSON:
        { "status": "success", "command": "...", "image_url": "data:image/png;base64,..." }
    """
    import base64
    data = request.get_json(silent=True) or {}

    # Optional user-supplied key (used only when .env has none).
    api_key = (data.get('api_key') or '').strip() or None
    if needs_api_key and not api_key:
        return jsonify({"status": "error",
                        "error": "API key required. Provide GEMINI_API_KEY via .env or the web form."}), 400

    # Validate the requested font against what is actually on disk, otherwise
    # auto_map would raise FileNotFoundError when loading the TrueType file.
    font_name = data.get('font')
    if font_name not in _available_fonts():
        font_name = "Bips.ttf"

    extra_option = data.get('extra_option')
    if extra_option in ("none", "", None):
        extra_option = None

    try:
        gsm = int(data.get('gsm', 15))
    except (TypeError, ValueError):
        gsm = 15

    # Rebuild the equivalent CLI command so the UI can show / log it.
    command = (
        f"python cli.py make \"{data.get('topic', '')}\" "
        f"-m {data.get('mode', 'text')} -mp {data.get('map_type', 'default')} "
        f"-gsm {gsm} -eop {data.get('extra_option', 'none')} "
        f"--model {data.get('model', 'gemini-2.5-flash')} --font \"{font_name}\""
    )

    try:
        buffer = auto_map(
            prompt=data['topic'],
            mode=data['mode'],
            map_type=data['map_type'],
            size_mod=gsm,
            optional_feature=extra_option,
            model=data['model'],
            font_name=font_name,
            show_result=False,
            io_res=True,
            api_key=api_key,
        )
    except KeyError as e:
        return jsonify({"status": "error", "command": command,
                        "error": f"Missing field: {e}"}), 400
    except Exception as e:  # noqa: BLE001 - surface failures to the UI
        return jsonify({"status": "error", "command": command,
                        "error": str(e)}), 500

    # auto_map returns None on AI failure / unknown map type.
    if buffer is None:
        return jsonify({"status": "error", "command": command,
                        "error": "Map generation failed. Check the server logs."}), 500

    # Encode the PNG buffer as a data URL the browser can load directly.
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')

    return jsonify({
        "status": "success",
        "command": command,
        "image_url": f"data:image/png;base64,{encoded}",
    })

if __name__ == "__main__":
    app.run(debug=True)