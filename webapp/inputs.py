"""Validate HTTP input once and translate shared map-generation options.

The application factory installs validate_api_json_body before routes run.
For API JSON requests it rejects non-object bodies and invalid shared string
fields, then stores the dictionary in Flask g. Handlers read it via request_data
instead of repeating top-level type checks. Multipart payloads, nested fields,
files and external responses still have their own boundaries.

This module also resolves provider credentials, font names, search options and
render defaults used by generation and batch routes. Browser keys remain
request-local: do not copy them into environment variables or response payloads.
"""
import dotenv
from flask import current_app, g, request, jsonify
from map.functions import configured_api_key, provider_for_model, provider_key_name
from .paths import FONTS_DIR


_TRUTHY = {'1', 'true', 'yes', 'on'}


def _needs_api_key():
    """Report whether this app lacked a usable Gemini server key at creation time.
    """
    return current_app.config['NEEDS_API_KEY']


def request_data() -> dict[str, object]:
    """Return the request-local JSON dictionary validated by the application hook.

    Use inside an API request, after validate_api_json_body has run. Non-JSON requests
    have an empty dictionary; file/form handlers parse their own multipart fields.
    This accessor intentionally does not repeat parsing or dictionary checks.
    """
    return g.api_data


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


def validate_api_json_body():
    """Reject malformed or non-object JSON before endpoint field access."""
    g.api_data = {}
    if request.path.startswith('/api/') and request.is_json:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(status='error', error='Request body must be a valid JSON object.'), 400
        for name in ('api_key', 'url', 'provider', 'model'):
            if data.get(name) is not None and not isinstance(data[name], str):
                return jsonify(status='error', error=f'{name} must be a string.'), 400

        g.api_data = data


def _server_api_key(provider):
    """Return the server-configured API key for ``provider``, if any."""
    env = current_app.config['ENV_FILE']
    return configured_api_key(provider, dotenv.dotenv_values(env) if env.is_file() else {})


def _request_provider(data):
    """Resolve and validate the AI provider from ``provider`` or ``model``."""
    inferred = provider_for_model(data.get('model') or '')
    provider = data.get('provider') or inferred
    if provider not in ('gemini', 'deepseek'):
        raise ValueError('Unknown AI provider.')
    if data.get('model') and inferred != provider:
        raise ValueError('The selected model belongs to a different AI provider.')
    return provider


def _request_api_key(data, provider):
    """Resolve the API key for ``provider`` from server config or the request payload."""
    supplied = str(data.get('api_key') or '').strip() or None
    if provider == 'deepseek':
        resolved = _server_api_key(provider) or supplied
    else:
        resolved = supplied
        if not _needs_api_key():
            # Pass file-configured credentials explicitly so startup cwd is irrelevant.
            return resolved or _server_api_key(provider)
    if not resolved:
        raise ValueError(f'API key required. Provide {provider_key_name(provider)} via .env or the web form.')
    return resolved


def _wants_refresh(data) -> bool:
    """Return True if the request asks to bypass the AI response cache."""
    value = data.get('refresh_ai', data.get('refresh', False))
    return value is True or str(value).strip().lower() in _TRUTHY


def _web_search_options(data: dict[str, object]) -> dict[str, object]:
    """Build optional search arguments shared by single and batch generation."""
    enabled = str(data.get('web_search') or '').strip().lower() in _TRUTHY
    if not enabled:
        return {}

    request_limit = int(data.get('max_search_requests') or 5)
    options: dict[str, object] = {
        'web_search': True,
        'max_search_requests': max(1, min(10, request_limit)),
    }
    if data.get('tavily_api_key'):
        options['tavily_api_key'] = str(data['tavily_api_key']).strip()
    return options


def _parse_render_params(data: dict[str, object]) -> tuple[str, str | None, int]:
    """Validate font, normalize extra_option, and parse gsm safely."""
    font_name = data.get("font")
    if font_name not in _available_fonts():
        font_name = "Bips.ttf"

    extra_option = data.get("extra_option")
    if extra_option in ("none", "", None):
        extra_option = None
    else:
        extra_option = str(extra_option)

    try:
        gsm = int(data.get("gsm", 15))
    except (TypeError, ValueError, OverflowError):
        gsm = 15

    return str(font_name), extra_option, gsm
