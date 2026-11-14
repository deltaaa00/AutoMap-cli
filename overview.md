# Project overview for AI agents

AutoMap generates labelled, coloured maps from natural-language prompts using
Google Gemini and Pillow. It provides a Click CLI, a Flask web UI, and a Python
function. A separate Flask worker downloads music, cuts audio, and renders videos.

## Code map

| Path | Responsibility |
| --- | --- |
| `cli.py` | CLI options, model/font selection, verbose logging, `automap make` entry point |
| `main.py` | Web pages, map-generation API, and proxy routes to the media worker |
| `map/auto_map.py` | Map selection, flood filling, labels, overlays, PNG output |
| `map/functions.py` | Gemini requests, JSON response parsing, filling configuration |
| `map/constants.py` | Region coordinates and expected AI response keys |
| `map/log.py` | Rich logging to stderr |
| `templates/index.html` | Map UI with inline JavaScript |
| `templates/video.html` | Video Studio UI with inline JavaScript |
| `yt-dlp_serv.py` | Music download/cut, MoviePy rendering, video downloads |
| `resources/` | Map images, fonts, and demo assets |
| `tests/` | AI response parsing test and response fixtures |

## Generation flow and contracts

1. CLI or web API calls `map.auto_map.auto_map`.
2. The generator loads filling rules and the selected base map.
3. `ai_request` obtains Gemini output and parses JSON into region data.
4. Pillow colours regions, draws labels, and composites borders and relief.
5. The function returns the path to repository-root `result.png`, or a PNG
   `BytesIO` when `io_res=True`. Some failures return `None`.

- Map types: `default` and `flag` use European countries; `world` uses continents.
- Modes: `text` matches categorical colours; `number` uses numeric ranges.
- Region keys must match `map/constants.py`, including their casing.
- Text filling keys are matched after trimming and lowercasing.
- Numeric filling ranges follow `low < value <= high`; colours use RGBA tuples.
- `short_form` is disabled in number mode. `show_most_least` is disabled in text
  mode and on the world map. Check the implementation before changing these rules.
- Font names are discovered from `.ttf` and `.otf` files in `resources/fonts`.

## Local setup and validation

Run from the repository root: font loading and `.env` loading use relative paths.
`main.py` changes the process working directory to the repository root on import.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp map/filling.template.json map/filling.json
```

Set `GEMINI_API_KEY` in the environment or a local `.env` file. The CLI accepts
`--key`; the web API accepts a per-request `api_key`. Never commit credentials.

```sh
python cli.py make "Population of European countries" -m number -mp default
python main.py
python -m pip install -r requirements-test.txt
python -m pytest -q
```

`pip install -e .` exposes the `automap` command. Dependencies are declared in both
`requirements.txt` and `pyproject.toml`; keep their runtime lists and constraints
in sync. `requirements-test.txt` includes runtime dependencies plus test tools.
The existing test checks JSON
parsing using local fixtures and does not call Gemini. Map generation requires a
working key; media operations require additional tools and integration checks.

## Web and media services

- Web pages: `/` and `/studio`.
- Map API: `/api/status`, `/api/fonts`, `/api/generate`.
- Media routes: `/api/music_download`, `/api/music_cut`, `/api/render_video`,
  `/api/download_video`. `main.py` proxies these to
  `http://yt-dlp-worker:8080`.
- `docker compose up --build` runs the web service on host port 3000 and the
  worker on host port 8080. Running only `python main.py` does not start the worker.
- The worker uses yt-dlp, ffmpeg, pydub, and MoviePy. Its Dockerfile installs
  ffmpeg, curl, and Node.js. Software rendering defaults to `libx264`; hardware
  codecs require compatible host support.
- Persistent media paths are under `/tmp/automap_storage/{downloads,cut,render}`.
  Downloads use a separate temporary directory per request before storing MP3s.
  Music-download requests trigger cleanup of files older than 30 minutes.
- Cutting accepts `MM:SS` and produces an eight-second clip. Track titles are
  restricted to word characters and hyphens before use in paths.
- Rendering accepts base64 map/background materials, limited to 10 MB each after
  decoding. It renders a 1080×1920 video of up to eight seconds.
- Rendering currently looks for audio in `downloads` using the sanitized video
  title; it does not load the cut clip from `cut`. Missing audio yields silence.
- Video downloads enforce containment within the render directory.

## Working on this repository

Keep changes scoped to the requested behavior. Coordinate changes to request
fields and response shapes with both inline frontend scripts and worker routes.
Preserve asset dimensions and region coordinates when changing map resources.
Use fixture responses or mocks for checks that do not need live AI or downloads,
and report when an integration path has not been exercised.

Treat source files and Docker configuration as the reference for current
behavior: portions of `readme.md`, including deployment instructions, may be stale.
Both Flask entry points currently start with debug mode enabled.
