"""Handle CSV import, batch map generation and downloadable map archives.

Schema validation lives in map.batch_ideas; these HTTP routes handle uploads,
request defaults, per-row overrides and partial-success responses. Each row uses
the same cached renderer as single-map generation. A failed row is reported
without discarding successful maps. ZIP downloads accept verified PNG data URLs
and produce unique topic-based filenames in memory, without executing filenames
or writing user-selected paths on the server.
"""
import base64
import io
import re
from pathlib import Path
from PIL import Image
from flask import Blueprint, request, jsonify, send_file
from cli import AVAILABLE_MODELS
from map.functions import (
    _ALLOWED_MAP_TYPES,
    parse_ideas_content,
    provider_for_model,
    slugify_prompt,
    validate_temperature,
)
from .paths import BASE_DIR
from .inputs import (
    request_data,
    _available_fonts,
    _server_api_key,
    _request_provider,
    _request_api_key,
    _wants_refresh,
    _web_search_options,
    _parse_render_params,
    _needs_api_key,
)
from .generation import _auto_map_with_ai_cache
from .analysis import _build_analysis_from_ai


routes = Blueprint('batch', __name__)


@routes.route('/api/batch-template', methods=['GET'])
def download_batch_template():
    """Serve the downloadable batch CSV template."""
    template_path = BASE_DIR / "batch_template.csv"
    if not template_path.is_file():
        return jsonify({"status": "error", "error": "batch_template.csv not found."}), 404
    return send_file(
        template_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="batch_template.csv",
    )


@routes.route('/api/download_batch_zip', methods=['POST'])
def download_batch_zip():
    """Bundle generated batch maps into a single ZIP archive named by topic."""
    import zipfile

    data = request_data()
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"status": "error", "error": "No maps provided."}), 400

    buf = io.BytesIO()
    used_names: set[str] = set()
    added = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            image_url = str(item.get("image_url") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            if not image_url.startswith('data:image/png;base64,'):
                continue
            try:
                image_bytes = base64.b64decode(image_url.split(',', 1)[1], validate=True)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    if image.format != 'PNG':
                        continue
                    image.verify()
            except (ValueError, OSError):
                continue

            base = re.sub(r"[^\w\-]+", "_", prompt.strip().replace(" ", "_")).strip("_") if prompt else f"map_{idx:02d}"
            if not base:
                base = f"map_{idx:02d}"
            count = 1
            arc_name = f"{base}.png"
            while arc_name in used_names:
                count += 1
                arc_name = f"{base}_{count}.png"
            used_names.add(arc_name)
            zf.writestr(arc_name, image_bytes)
            added += 1

    if added == 0:
        return jsonify({"status": "error", "error": "No valid map files found to archive."}), 404

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="automap_batch.zip",
    )


@routes.route('/api/parse_batch_csv', methods=['POST'])
def parse_batch_csv():
    """Parse an uploaded .csv ideas file and return the normalized idea rows."""
    if request.files and "file" in request.files:
        uploaded = request.files["file"]
        filename = uploaded.filename or ""
        ideas_text = uploaded.read().decode("utf-8", errors="replace")
    else:
        data = request_data()
        filename = str(data.get("filename") or "")
        ideas_text = str(data.get("ideas_text") or "")

    try:
        ideas = parse_ideas_content(ideas_text, filename=filename)
    except ValueError as err:
        return jsonify({"status": "error", "error": str(err)}), 400

    if not ideas:
        return jsonify({"status": "error", "error": "No valid map ideas found in the uploaded file."}), 400

    return jsonify({"status": "success", "ideas": ideas, "total": len(ideas)})


@routes.route('/api/generate_batch/', methods=['POST'])
@routes.route('/api/generate_batch', methods=['POST'])
def generate_batch():
    """Generate multiple maps from an uploaded .csv ideas file."""
    if request.files and "file" in request.files:
        uploaded = request.files["file"]
        filename = uploaded.filename or ""
        ideas_text = uploaded.read().decode("utf-8", errors="replace")
        data: dict[str, object] = dict(request.form)
    else:
        data = request_data()
        filename = str(data.get("filename") or "")
        ideas_text = str(data.get("ideas_text") or "")

    try:
        provider = _request_provider(data)
        api_key = _request_api_key(data, provider)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400

    try:
        ideas = parse_ideas_content(ideas_text, filename=filename)
    except ValueError as err:
        return jsonify({"status": "error", "error": str(err)}), 400

    if not ideas:
        return jsonify({"status": "error", "error": "No valid map ideas found in the uploaded file."}), 400

    default_font, default_eop, default_gsm = _parse_render_params(data)
    default_mode = str(data.get("mode") or "text").lower()
    default_map_type = str(data.get("map_type") or "default").strip().lower()
    if default_map_type not in _ALLOWED_MAP_TYPES:
        return jsonify({
            "status": "error",
            "error": f"Invalid map_type '{default_map_type}'. Allowed: {', '.join(sorted(_ALLOWED_MAP_TYPES))}.",
        }), 400
    provider_default_model = AVAILABLE_MODELS[0]
    if provider == 'deepseek':
        provider_default_model = 'deepseek-flash'
    default_model = str(data.get("model") or provider_default_model)
    available_fonts = set(_available_fonts())

    temperature_options: dict[str, object] = {}
    if "temperature" in data and str(data["temperature"]).strip() != "":
        try:
            temperature_options["temperature"] = validate_temperature(data["temperature"])
        except ValueError as err:
            return jsonify({"status": "error", "error": str(err)}), 400

    base_style_kwargs: dict[str, object] = {}
    for style_key, kw_name in (
        ("filling_txt", "filling_txt_override"),
        ("filling_num", "filling_num_override"),
        ("label_style", "label_style"),
        ("auto_style", "auto_style"),
        ("palette_theme", "palette_theme"),
        ("label_suffix", "label_suffix"),
        ("show_legend", "show_legend"),
        ("highlight_extrema", "highlight_extrema"),
        ("highlight_high_color", "highlight_high_color"),
        ("highlight_low_color", "highlight_low_color"),
    ):
        if style_key in data:
            base_style_kwargs[kw_name] = data[style_key]

    results: list[dict[str, object]] = []
    succeeded = 0

    for idx, idea in enumerate(ideas, start=1):
        prompt = str(idea["prompt"])
        item_mode = str(idea.get("mode") or default_mode).lower()
        item_map_type = str(idea.get("map_type") or default_map_type).lower()
        item_gsm = int(idea.get("gsm", default_gsm))
        raw_eop = str(idea.get("extra_option") or default_eop or "none").lower()
        item_eop = None if raw_eop in ("none", "") else raw_eop
        item_model = str(idea.get("model") or default_model)
        item_font = str(idea.get("font") or default_font)
        if item_font not in available_fonts:
            item_font = default_font

        item_style_kwargs = dict(base_style_kwargs)
        if "palette_theme" in idea:
            item_style_kwargs["palette_theme"] = idea["palette_theme"]

        custom_out = str(idea.get("output") or "").strip()
        if custom_out:
            out_filename = Path(custom_out).name
        else:
            out_filename = f"{idx:02d}_{slugify_prompt(prompt)}.png"
        if not out_filename.lower().endswith(".png"):
            out_filename += ".png"

        try:
            item_provider = provider_for_model(item_model)
            item_api_key = api_key
            if item_provider != provider:
                item_api_key = _server_api_key(item_provider)
            if not item_api_key and (item_provider == 'deepseek' or _needs_api_key()):
                raise ValueError(f'API key required for {item_provider}. Use one provider per CSV or configure its server key.')
            buffer, _hit = _auto_map_with_ai_cache(
                refresh=_wants_refresh(data),
                provider=item_provider,
                prompt=prompt,
                mode=item_mode,
                map_type=item_map_type,
                size_mod=item_gsm,
                optional_feature=item_eop,
                model=item_model,
                font_name=item_font,
                show_result=False,
                io_res=True,
                api_key=item_api_key,
                **temperature_options,
                **_web_search_options(data),
                **item_style_kwargs,
            )
        except Exception as err:  # noqa: BLE001
            results.append({
                "index": idx,
                "prompt": prompt,
                "filename": out_filename,
                "status": "error",
                "error": str(err),
            })
            continue

        if buffer is None:
            results.append({
                "index": idx,
                "prompt": prompt,
                "filename": out_filename,
                "status": "error",
                "error": "Map generation failed.",
            })
            continue

        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        item_payload: dict[str, object] = {
            "index": idx,
            "prompt": prompt,
            "filename": out_filename,
            "status": "success",
            "image_url": f"data:image/png;base64,{encoded}",
            "mode": item_mode,
            "map_type": item_map_type,
            "gsm": item_gsm,
            "extra_option": item_eop or "none",
            "model": item_model,
            "provider": item_provider,
            "font": item_font,
            "palette_theme": item_style_kwargs.get("palette_theme", "auto"),
            "label_suffix": item_style_kwargs.get("label_suffix", ""),
            "show_legend": item_style_kwargs.get("show_legend", False),
            "highlight_extrema": item_style_kwargs.get("highlight_extrema", False),
            "highlight_high_color": item_style_kwargs.get("highlight_high_color", "#00e5ff"),
            "highlight_low_color": item_style_kwargs.get("highlight_low_color", "#ff2daa"),
        }
        if temperature_options:
            item_payload["temperature"] = temperature_options["temperature"]
        render_meta = getattr(buffer, "map_meta", None)
        ai_vals = render_meta.get("ai_answer") if isinstance(render_meta, dict) else None
        if isinstance(ai_vals, dict) and ai_vals:
            item_payload["map_values"] = render_meta.get("raw_ai_answer", ai_vals)
            item_payload["analysis"] = _build_analysis_from_ai(
                ai_vals,
                mode=item_mode,
                palette_theme=str(item_style_kwargs.get("palette_theme") or "auto"),
                topic=prompt,
                render_meta=render_meta,
            )
        results.append(item_payload)
        succeeded += 1

    return jsonify({
        "status": "success" if succeeded > 0 else "error",
        "total": len(ideas),
        "succeeded": succeeded,
        "results": results,
    }), (200 if succeeded > 0 else 500)
