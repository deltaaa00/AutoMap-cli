# Repository Guidelines

## Project Structure & Module Organization

Start with `overview.md` for request flows, contracts, and change locations. `main.py` and `yt-dlp_serv.py` launch separate web and media services; `cli.py` exposes map commands.

- `webapp/`: Flask factory, routes, authentication, caching, and YouTube uploads.
- `map/`: AI providers, CSV parsing, styling, geometry, and PNG rendering.
- `media/`: worker endpoints, audio processing, text/stickers, and video composition.
- `templates/`, `static/`, `resources/`: frontend controllers/styles, maps, and fonts.
- `AutoMap-agent/`: standalone automation service with separate dependencies/tests.
- `automap_rs/`: optional Rust acceleration. `tools/` and `benchmarks/` contain synthetic benchmarks.
- `tests/`: backend tests; `tests/browser/` uses Chromium; `tests/frontend/` uses Node.

## Build, Test, and Development Commands

Run from the repository root:

```sh
python -m pip install -r requirements-test.txt  # Runtime and test dependencies
python main.py                              # Web, default port 5000
python yt-dlp_serv.py                        # Worker, default port 8080
python cli.py make "Population" -m number -mp default


docker compose up --build -d                # Build/start both services
python -m pytest tests --ignore=tests/browser -q
python -m playwright install chromium
python -m pytest tests --cov --cov-report=term-missing -q
node --test tests/frontend/*.test.cjs
PYTHONPATH=AutoMap-agent python -m pytest AutoMap-agent/tests -q
```

Install `AutoMap-agent/requirements.txt` before agent tests. Real media tests require working FFmpeg/MoviePy.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions/modules, and `PascalCase` classes. Follow neighboring JavaScript/Rust style; no repository-wide formatter/linter is configured. Write English module docstrings and explain non-obvious contracts. Prefer named steps over nested one-line expressions. Validate external boundaries once; trust normalized internal data. Keep routes, rendering, and storage responsibilities separate.

## Testing Guidelines

Use pytest files named `test_*.py` and functions named `test_*`. Root coverage must meet **85%**, configured in `.coveragerc`. Mock external AI/HTTP calls; patch dependencies where used. Test pixels/media locally where behavior depends on encoding. See `overview.md` for known baseline failures; report them separately from regressions.

## Commit & Pull Request Guidelines

History mostly uses short imperative subjects such as “Add…” and “Fix…”, with occasional `feat:` prefixes; no strict convention is enforced. Keep commits focused. PRs should explain behavior changes, affected services, validation results, and configuration/migration needs. Link relevant issues and include screenshots for UI changes.

## Security & Configuration

Copy `example.env` for local settings; never commit credentials or runtime storage. Keep dependency declarations in `requirements.txt` and `pyproject.toml` aligned. Update Docker COPY paths after module moves. Preserve session ownership, credential redaction, and temporary-file cleanup.
