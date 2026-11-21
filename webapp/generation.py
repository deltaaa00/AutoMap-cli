"""Handle single-map generation, topic ideas, model discovery and repainting.

Routes consume validated request dictionaries from webapp.inputs and call the
shared map renderer or provider helpers. Initial generation may contact AI;
restyling supplies existing map_values and must not make another AI request.
Successful PNG buffers may carry map_meta with raw values and effective styles;
webapp.analysis translates these into editor controls.

The AI answer cache excludes visual-only settings and scopes data by session and
credential hashes. The in-memory progress dictionary tracks short-lived map
stages, independently of video progress in render_progress.py. Batch generation
reuses the cached-render helper from this module.
"""
import base64
import threading
import time
from flask import Blueprint, jsonify, session
from map.auto_map import auto_map, _format_short_number, _parse_short_number
from map.constants import _PALETTE_THEMES
from cli import AVAILABLE_MODELS
from map.functions import (
    _ALLOWED_MAP_TYPES,
    AIServiceError,
    generate_map_idea,
    list_deepseek_models,
    provider_for_model,
    validate_temperature,
)
from webapp.cache import ai_cache
from .inputs import (
    request_data,
    _available_fonts,
    _server_api_key,
    _request_provider,
    _request_api_key,
    _wants_refresh,
    _web_search_options,
    _parse_render_params,
    _TRUTHY,
)
from .analysis import _build_analysis_from_ai, _inspect_uploaded_map_image


routes = Blueprint('generation', __name__)


_MAP_PROGRESS_LOCK = threading.Lock()
_MAP_PROGRESS: dict[str, dict[str, object]] = {}


def _auto_map_with_ai_cache(*, refresh: bool, provider: str, api_key, **kwargs):
    """Call auto_map, reusing a cached AI answer for identical data requests.

    Only request-affecting inputs form the key, so font/style/palette changes
    reuse the answer. API keys only contribute a hash to the user scope.
    Returns (buffer, cache_hit).
    """
    if kwargs.get('ai_answer_override') is not None:
        return auto_map(api_key=api_key, **kwargs), False

    temperature = kwargs.get('temperature')
    identity = ''
    try:
        identity = str(session.get('web_identity', '') or '')
    except RuntimeError:
        identity = ''
    short_form = 'short_form' in str(kwargs.get('optional_feature') or '')
    scope = ai_cache.scope(identity, api_key)
    if kwargs.get("web_search") and kwargs.get("tavily_api_key"):
        scope = ai_cache.scope(scope, kwargs["tavily_api_key"])
    base_fields = dict(
        prompt=str(kwargs.get('prompt') or ''),
        map_type=str(kwargs.get('map_type') or ''),
        model=str(kwargs.get('model') or ''),
        provider=provider,
        temperature=temperature,
        web_search=bool(kwargs.get('web_search', False)),
        max_search_requests=kwargs.get('max_search_requests', 5),
    )
    cache_key = ai_cache.make_key(scope, short_form=short_form, **base_fields)
    cached = None
    if not refresh:
        cached = ai_cache.get(cache_key)
    if cached is None and not refresh and short_form:
        raw_cached = ai_cache.get(ai_cache.make_key(scope, short_form=False, **base_fields))
        if raw_cached and any(_parse_short_number(v) is not None for v in raw_cached.values()):
            cached = raw_cached
    if cached is not None:
        kwargs['ai_answer_override'] = cached
    buffer = auto_map(api_key=api_key, **kwargs)
    if buffer is not None:
        meta = getattr(buffer, 'map_meta', None)
        answer = None
        if isinstance(meta, dict):
            answer = meta.get('raw_ai_answer', meta.get('ai_answer'))
        if isinstance(answer, dict):
            if short_form:
                meta['ai_answer'] = {k: _format_short_number(v) for k, v in answer.items()}
            if not refresh:
                ai_cache.put(cache_key, answer)
    return buffer, cached is not None


def _set_map_progress(progress_id: str, stage: int, label: str, status: str = "running") -> None:
    """Record the active map generation stage for ``progress_id``."""
    if not progress_id:
        return
    now = time.time()
    with _MAP_PROGRESS_LOCK:
        stale = [k for k, v in _MAP_PROGRESS.items() if now - float(v.get("updated_at", 0)) > 300]
        for k in stale:
            _MAP_PROGRESS.pop(k, None)
        _MAP_PROGRESS[progress_id] = {
            "stage": stage,
            "label": label,
            "status": status,
            "updated_at": now,
        }


@routes.get('/api/generate_progress/<progress_id>')
def api_generate_progress(progress_id: str):
    """Return current map generation progress for ``progress_id``."""
    with _MAP_PROGRESS_LOCK:
        entry = dict(_MAP_PROGRESS.get(progress_id) or {"stage": 1, "label": "Querying AI model...", "status": "running"})
    resp = jsonify(entry)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@routes.route('/api/generate/', methods=['POST'])
@routes.route('/api/generate', methods=['POST'])
def generate():
    """Generate a map from the JSON body and return it as a data URL.

    Request JSON:
        { "topic", "mode", "map_type", "gsm", "extra_option", "model", "font", "temperature" }

    Response JSON:
        { "status": "success", "command": "...", "image_url": "data:image/png;base64,..." }
    """
    data = request_data()

    # Optional user-supplied key (used only when .env has none).
    try:
        provider = _request_provider(data)
        api_key = _request_api_key(data, provider)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400

    # Validate the requested font against what is actually on disk, otherwise
    # auto_map would raise FileNotFoundError when loading the TrueType file.
    font_name, extra_option, gsm = _parse_render_params(data)

    temperature_options = {}
    if 'temperature' in data:
        try:
            temperature_options['temperature'] = validate_temperature(data['temperature'])
        except ValueError as error:
            return jsonify(status='error', error=str(error)), 400

    if "map_type" in data:
        req_map_type = str(data["map_type"] or "").strip().lower()
        if req_map_type not in _ALLOWED_MAP_TYPES:
            return jsonify(
                status='error',
                error=f"Invalid map_type '{req_map_type}'. Allowed: {', '.join(sorted(_ALLOWED_MAP_TYPES))}.",
            ), 400
        data["map_type"] = req_map_type

    # Rebuild the equivalent CLI command for display only; it is never executed.
    command = (
        f"python cli.py make \"{data.get('topic', '')}\" "
        f"-m {data.get('mode', 'text')} -mp {data.get('map_type', 'default')} "
        f"-gsm {gsm} -eop {data.get('extra_option', 'none')} "
        f"--model {data.get('model', AVAILABLE_MODELS[0])} --font \"{font_name}\""
    )
    if temperature_options:
        command += f" --temperature {temperature_options['temperature']:g}"

    show_legend = bool(data.get("show_legend", False))
    highlight_extrema = bool(data.get("highlight_extrema", False))
    if show_legend:
        command += " --legend"
    if highlight_extrema:
        command += " --highlight-extrema"
    label_suffix = str(data.get("label_suffix") or "").replace("\r", "").replace("\n", "")[:16]
    if label_suffix:
        escaped_suffix = label_suffix.replace('"', '\\"')
        command += f' --suffix "{escaped_suffix}"'

    progress_id = str(data.get("progress_id") or "").strip()[:64]
    if progress_id:
        _set_map_progress(progress_id, 1, "Querying AI model...")

    style_kwargs: dict[str, object] = {
        "show_legend": show_legend,
        "highlight_extrema": highlight_extrema,
    }
    if highlight_extrema:
        if "highlight_high_color" in data:
            style_kwargs["highlight_high_color"] = str(data.get("highlight_high_color") or "#00e5ff")
        if "highlight_low_color" in data:
            style_kwargs["highlight_low_color"] = str(data.get("highlight_low_color") or "#ff2daa")
    if label_suffix:
        style_kwargs["label_suffix"] = label_suffix
    if isinstance(data.get("skip_label_countries"), list):
        style_kwargs["skip_label_countries"] = [str(c) for c in data["skip_label_countries"] if str(c).strip()]
    if isinstance(data.get("ai_answer_override"), dict):
        style_kwargs["ai_answer_override"] = data["ai_answer_override"]
    if progress_id:
        style_kwargs["progress_callback"] = lambda stage, label: _set_map_progress(progress_id, stage, label)
    for style_key, kw_name in (
        ("filling_txt", "filling_txt_override"),
        ("filling_num", "filling_num_override"),
        ("label_style", "label_style"),
        ("auto_style", "auto_style"),
        ("palette_theme", "palette_theme"),
    ):
        if style_key in data:
            style_kwargs[kw_name] = data[style_key]

    try:
        buffer, ai_cache_hit = _auto_map_with_ai_cache(
            refresh=_wants_refresh(data),
            provider=provider,
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
            **temperature_options,
            **_web_search_options(data),
            **style_kwargs,
        )
    except AIServiceError as error:
        if progress_id:
            _set_map_progress(progress_id, 1, str(error), status="error")
        return jsonify(status='error', command=command, error=str(error)), error.status_code
    except KeyError as e:
        if progress_id:
            _set_map_progress(progress_id, 1, f"Missing field: {e}", status="error")
        return jsonify({"status": "error", "command": command,
                        "error": f"Missing field: {e}"}), 400
    except Exception as e:  # noqa: BLE001 - surface failures to the UI
        if progress_id:
            _set_map_progress(progress_id, 1, str(e), status="error")
        return jsonify({"status": "error", "command": command,
                        "error": str(e)}), 500

    # auto_map returns None on AI failure / unknown map type.
    if buffer is None:
        if progress_id:
            _set_map_progress(progress_id, 1, "Map generation failed.", status="error")
        return jsonify({"status": "error", "command": command,
                        "error": "Map generation failed. Check the server logs."}), 500

    if progress_id:
        _set_map_progress(progress_id, 4, "Finalizing map...", status="done")

    # Encode the PNG buffer as a data URL the browser can load directly.
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')

    resp_payload: dict[str, object] = {
        "status": "success",
        "command": command,
        "image_url": f"data:image/png;base64,{encoded}",
        "ai_cached": ai_cache_hit,
    }
    render_meta = getattr(buffer, "map_meta", None)
    if isinstance(render_meta, dict) and render_meta.get("rust_warning"):
        resp_payload["warning"] = render_meta["rust_warning"]
    ai_vals = render_meta.get("ai_answer") if isinstance(render_meta, dict) else None
    if isinstance(ai_vals, dict) and ai_vals:
        resp_payload["map_values"] = render_meta.get("raw_ai_answer", ai_vals)
        resp_payload["analysis"] = _build_analysis_from_ai(
            ai_vals,
            mode=str(data.get("mode") or "auto"),
            palette_theme=str(data.get("palette_theme") or "auto"),
            topic=str(data.get("topic") or ""),
            render_meta=render_meta,
        )

    return jsonify(resp_payload)


@routes.route('/api/generate_idea', methods=['POST'])
def api_generate_idea():
    """Generate a map topic idea (and optionally matching parameters) using the selected AI model."""
    data = request_data()
    try:
        provider = _request_provider(data)
        api_key = _request_api_key(data, provider)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400

    default_model = AVAILABLE_MODELS[0]
    if provider == 'deepseek':
        default_model = 'deepseek-flash'
    model = str(data.get("model") or default_model).strip()
    if provider_for_model(model) != provider:
        return jsonify(status='error', error=f"Model '{model}' does not match provider '{provider}'."), 400

    count = data.get('count', 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5:
        return jsonify(status='error', error='Idea count must be an integer from 1 to 5.'), 400

    raw_with_params = data.get("with_params")
    with_params = raw_with_params is True or str(raw_with_params or "").strip().lower() in _TRUTHY

    temperature = None
    if "temperature" in data and str(data["temperature"]).strip() != "":
        try:
            temperature = validate_temperature(data["temperature"])
        except ValueError as err:
            return jsonify(status='error', error=str(err)), 400

    raw_avoid = data.get("avoid_ideas")
    avoid_ideas = []
    if isinstance(raw_avoid, list):
        avoid_ideas = [str(item).strip() for item in raw_avoid if str(item).strip()][:30]

    raw_top = data.get("top_channel_topics")
    top_channel_topics = []
    if isinstance(raw_top, list):
        top_channel_topics = [str(item).strip() for item in raw_top if str(item).strip()][:10]

    try:
        result = generate_map_idea(
            model,
            api_key=api_key,
            with_params=with_params,
            count=count,
            available_themes=list(_PALETTE_THEMES.keys()),
            available_fonts=_available_fonts(),
            temperature=temperature,
            avoid_ideas=avoid_ideas,
            top_channel_topics=top_channel_topics,
        )
    except AIServiceError as error:
        return jsonify(status='error', error=str(error)), error.status_code
    except Exception as exc:  # noqa: BLE001
        return jsonify(status='error', error=str(exc)), 500

    return jsonify({"status": "success", **result}), 200


@routes.route('/api/models', methods=['POST'])
def list_available_models():
    """Return models available for the selected provider's API key."""
    data = request_data()
    try:
        provider = _request_provider(data)
    except ValueError as error:
        return jsonify(status='error', error=str(error), models=[]), 400
    if provider == 'deepseek':
        try:
            models = list_deepseek_models(_request_api_key(data, provider))
            return jsonify(status='success', models=models)
        except ValueError as error:
            return jsonify(status='error', error=str(error), models=[]), 400
        except AIServiceError as error:
            return jsonify(status='error', error=str(error), models=[]), error.status_code
    req_key = str(data.get("api_key") or "").strip()
    env_key = _server_api_key('gemini') or ''
    key = req_key or env_key
    if not key:
        return jsonify({"status": "error", "error": "API key required.", "models": []}), 400
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=15_000, retry_options=types.HttpRetryOptions(attempts=1),
        ))
        models: list[str] = []
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            if name.startswith("models/"):
                name = name[len("models/"):]
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            low = name.lower()
            if not (low.startswith("gemini") or low.startswith("gemma")):
                continue
            if any(skip in low for skip in (
                "embedding", "imagen", "veo", "tts", "aqa", "robotics", "computer-use",
                "image", "nano-banana", "omni", "transcribe", "live", "native-audio", "decision",
            )):
                continue
            models.append(name)
        return jsonify({"status": "success", "models": sorted(set(models), reverse=True)})
    except Exception as err:  # noqa: BLE001
        return jsonify({"status": "error", "error": str(err), "models": []}), 500


@routes.route('/api/restyle_map/', methods=['POST'])
@routes.route('/api/restyle_map', methods=['POST'])
def api_restyle_map():
    """Repaint the map from existing map_values without making another AI request."""
    data = request_data()
    map_values = data.get("map_values")
    if not isinstance(map_values, dict) or not map_values:
        return jsonify({
            "status": "error",
            "error": "No map_values provided. Generate a map first to re-paint it live.",
        }), 400

    font_name, extra_option, gsm = _parse_render_params(data)

    mode = str(data.get("mode") or "text")
    map_type = str(data.get("map_type") or "default").strip().lower()
    if map_type not in _ALLOWED_MAP_TYPES:
        return jsonify({
            "status": "error",
            "error": f"Invalid map_type '{map_type}'. Allowed: {', '.join(sorted(_ALLOWED_MAP_TYPES))}.",
        }), 400
    palette_theme = str(data.get("palette_theme") or "auto")
    auto_style = bool(data.get("auto_style", False))

    try:
        buffer = auto_map(
            prompt=str(data.get("topic") or ""),
            mode=mode,
            map_type=map_type,
            size_mod=gsm,
            optional_feature=extra_option,
            model=str(data.get("model") or AVAILABLE_MODELS[0]),
            font_name=font_name,
            show_result=False,
            io_res=True,
            api_key=None,
            filling_txt_override=data.get("filling_txt"),
            filling_num_override=data.get("filling_num"),
            label_style=data.get("label_style"),
            auto_style=auto_style,
            palette_theme=palette_theme,
            ai_answer_override=map_values,
            show_legend=bool(data.get("show_legend", False)),
            highlight_extrema=bool(data.get("highlight_extrema", False)),
            highlight_high_color=str(data.get("highlight_high_color") or "#00e5ff"),
            highlight_low_color=str(data.get("highlight_low_color") or "#ff2daa"),
            label_suffix=str(data.get("label_suffix") or "").replace("\r", "").replace("\n", "")[:16],
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"status": "error", "error": str(e)}), 500

    if buffer is None:
        return jsonify({"status": "error", "error": "Map repainting failed."}), 500

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    render_meta = getattr(buffer, "map_meta", None)
    analysis = _build_analysis_from_ai(
        map_values,
        mode=mode,
        palette_theme=palette_theme,
        topic=str(data.get("topic") or ""),
        render_meta=render_meta if isinstance(render_meta, dict) else None,
    )
    return jsonify({
        "status": "success",
        "image_url": f"data:image/png;base64,{encoded}",
        "map_values": map_values,
        "analysis": analysis,
    })


@routes.route('/api/analyze_map/', methods=['POST'])
@routes.route('/api/analyze_map', methods=['POST'])
def api_analyze_map():
    """Inspect uploaded map images, explicit map_values, or topic previews."""
    data = request_data()
    mode = str(data.get('mode') or 'auto')
    topic = str(data.get('topic') or '').strip()
    map_type = str(data.get('map_type') or 'default')
    palette_theme = str(data.get('palette_theme') or 'auto')

    map_values = data.get('map_values')
    image_data = data.get('image_data')

    if image_data is not None and (not isinstance(map_values, dict) or not map_values):
        try:
            analysis = _inspect_uploaded_map_image(
                image_data,
                map_type=map_type,
                palette_theme=palette_theme,
            )
        except ValueError as err:
            return jsonify({"status": "error", "error": str(err)}), 400
        return jsonify({
            "status": "success",
            "source": "palette_inspection",
            "topic": topic,
            "map_values": None,
            "analysis": analysis,
        })

    if isinstance(map_values, dict) and map_values:
        analysis = _build_analysis_from_ai(
            map_values,
            mode=mode,
            palette_theme=palette_theme,
            topic=topic,
        )
        return jsonify({
            "status": "success",
            "source": "ai_answer",
            "topic": topic,
            "map_values": map_values,
            "analysis": analysis,
        })

    t_low = topic.lower()
    if any(w in t_low for w in ("nicholas", "christmas", "santa", "holiday", "dec")):
        sample_values: dict[str, object] = {
            "ukraine": "6 Dec", "poland": "6 Dec", "germany": "6 Dec", "france": "6 Dec",
            "netherlands": "5 Dec", "russia": "19 Dec", "serbia": "19 Dec", "spain": "6 Jan",
        }
    elif mode == "text":
        sample_values = {
            "ukraine": "Yes", "poland": "Yes", "germany": "Yes", "france": "No",
            "spain": "Yes", "italy": "Partial", "united_kingdom": "No",
        }
    else:
        sample_values = {
            "germany": 84, "france": 68, "italy": 59, "spain": 48, "ukraine": 38,
            "poland": 37, "romania": 19, "netherlands": 18, "sweden": 10, "estonia": 1,
        }

    analysis = _build_analysis_from_ai(
        sample_values,
        mode=mode,
        palette_theme=palette_theme,
        topic=topic,
    )
    return jsonify({
        "status": "success",
        "source": "topic_preview",
        "topic": topic,
        "map_values": sample_values,
        "analysis": analysis,
    })
