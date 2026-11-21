"""Translate map values or uploaded-image colors into editor analysis.

Generation, batch, repaint and analysis endpoints use these helpers to produce
palette rules, label defaults and statistics. This module does not contact AI.
When renderer metadata exists, prefer its effective colors to recomputing a
potentially different palette. Numeric range boundaries remain fractional.

Uploaded-image inspection samples known region coordinates in bundled artwork;
it recovers visible colors, not the original country values or their meaning.
Color parsing and automatic palette selection belong to map.styling.
"""
import base64
import io
from PIL import Image, UnidentifiedImageError
from map.auto_map import (
    _auto_fill_from_ai,
    _parse_short_number,
    _parse_filling_overrides,
    _resolve_theme_name,
    _to_rgba_tuple,
)
from map.constants import europe_coords, world_coords
from .paths import BASE_DIR


def _build_analysis_from_ai(
    ai_answer: dict[str, object],
    mode: str = "auto",
    palette_theme: str = "auto",
    topic: str = "",
    render_meta: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build UI-friendly color rules and stats from AI map values or render metadata."""
    # Normalize optional metadata once; the rest of the analysis uses one shape.
    if not isinstance(render_meta, dict):
        render_meta = {}
    vals = [v for v in ai_answer.values() if v is not None and str(v).strip() != ""]
    num_vals: list[float] = []
    for v in vals:
        parsed = _parse_short_number(v)
        if parsed is not None:
            num_vals.append(parsed)

    if mode in ("number", "text"):
        resolved_mode = mode
    else:
        # Infer numeric mode only when enough nonempty answers contain numbers.
        numeric_threshold = max(1, int(len(vals) * 0.6))
        resolved_mode = "text"
        if vals and len(num_vals) >= numeric_threshold:
            resolved_mode = "number"

    if render_meta.get("resolved_theme"):
        resolved_theme = str(render_meta["resolved_theme"])
    else:
        resolved_theme = _resolve_theme_name(palette_theme, prompt=topic, mode=resolved_mode)

    txt_dict: dict[str, tuple[int, int, int, int]] = {}
    num_dict: dict[tuple[int, int], tuple[int, int, int, int]] = {}

    raw_txt = render_meta.get("used_txt") or render_meta.get("filling_txt")
    if isinstance(raw_txt, dict):
        for key, color in raw_txt.items():
            rgba = _to_rgba_tuple(color)
            if rgba is not None:
                txt_dict[str(key)] = rgba
    raw_num = render_meta.get("used_num") or render_meta.get("filling_num")
    if isinstance(raw_num, dict) and raw_num:
        _, num_dict = _parse_filling_overrides(None, raw_num)

    if not txt_dict and not num_dict:
        txt_dict, num_dict = _auto_fill_from_ai(
            ai_answer,
            resolved_mode,
            palette_theme=palette_theme,
            prompt=topic,
        )

    ui_txt_rules = [
        {"key": str(k), "hex": f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}", "alpha": c[3]}
        for k, c in txt_dict.items()
    ]
    ui_num_rules = [
        {"low": lo, "high": hi, "hex": f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}", "alpha": c[3]}
        for (lo, hi), c in num_dict.items()
    ]

    default_label_style = {
        "text_color": "#ffffff",
        "stroke_color": "#090d16",
        "stroke_width": 15,
        "auto_fit": True,
        "recommended_font": "Bips.ttf" if resolved_mode == "number" else "DIN Condensed.ttf",
        "recommended_gsm": 15,
    }
    label_style = render_meta.get("label_style")
    if isinstance(label_style, dict):
        default_label_style.update(label_style)

    return {
        "detected_type": resolved_mode,
        "resolved_mode": resolved_mode,
        "resolved_theme": resolved_theme,
        "filling_txt": {str(k): list(c) for k, c in txt_dict.items()},
        "filling_num": {f"({lo}, {hi})": list(c) for (lo, hi), c in num_dict.items()},
        "ui_txt_rules": ui_txt_rules,
        "ui_num_rules": ui_num_rules,
        "label_style": default_label_style,
        "stats": {
            "total_regions": len(vals),
            "unique_values": len({str(v).strip().lower() for v in vals}),
            "min_value": min(num_vals) if num_vals else None,
            "max_value": max(num_vals) if num_vals else None,
            "sample_values": [str(v) for v in vals[:8]],
        },
    }


def _inspect_uploaded_map_image(
    image_data: object,
    map_type: str = "default",
    palette_theme: str = "auto",
) -> dict[str, object]:
    """Decode an uploaded map image and extract its sampled region color palette."""
    if not isinstance(image_data, str) or not image_data.strip():
        raise ValueError("Empty image data.")
    raw_b64 = image_data.strip()
    if "," in raw_b64 and raw_b64.lower().startswith("data:"):
        header, raw_b64 = raw_b64.split(",", 1)
        if not header.lower().startswith("data:image/"):
            raise ValueError("Unsupported data URL media type.")
    try:
        decoded = base64.b64decode(raw_b64, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 image payload.") from exc
    if not decoded:
        raise ValueError("Decoded image payload is empty.")
    try:
        with Image.open(io.BytesIO(decoded)) as opened:
            img = opened.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Uploaded file is not a valid image.") from exc

    width, height = img.size
    if width < 8 or height < 8:
        raise ValueError("Uploaded map image is too small to inspect.")

    if map_type == "world":
        ref_coords = world_coords
        base_name = "world_map.png"
    elif map_type == "world_countries":
        from map.world_countries_coords import world_countries_coords
        ref_coords = world_countries_coords
        base_name = "world_countries_map.png"
    else:
        ref_coords = europe_coords
        base_name = "default_map.png"
    base_path = BASE_DIR / "resources" / "maps" / base_name
    ref_w, ref_h = (2200, 1800)
    if base_path.is_file():
        try:
            with Image.open(base_path) as ref_img:
                ref_w, ref_h = ref_img.size
        except OSError:
            pass

    px = img.load()
    clusters: list[tuple[int, int, int, int]] = []
    rule_colors: dict[str, tuple[int, int, int, int]] = {}
    sampled_regions = 0
    scale_x = width / max(1, ref_w)
    scale_y = height / max(1, ref_h)

    for points in ref_coords.values():
        if not points:
            continue
        sampled_regions += 1
        px_x, px_y = points[0]
        sx = max(0, min(width - 1, int(round(px_x * scale_x))))
        sy = max(0, min(height - 1, int(round(px_y * scale_y))))
        samples: list[tuple[int, int, int, int]] = []
        for dx in (-6, 0, 6):
            cx = max(0, min(width - 1, sx + dx))
            for dy in (-6, 0, 6):
                cy = max(0, min(height - 1, sy + dy))
                r, g, b, a = px[cx, cy]
                if a >= 32 and (r + g + b) > 35:
                    samples.append((r, g, b, 255))
        if not samples:
            r, g, b, a = px[sx, sy]
            samples.append((r, g, b, 255 if a >= 32 else a))

        avg = (
            int(round(sum(p[0] for p in samples) / len(samples))),
            int(round(sum(p[1] for p in samples) / len(samples))),
            int(round(sum(p[2] for p in samples) / len(samples))),
            255,
        )
        matched: tuple[int, int, int, int] | None = None
        for existing in clusters:
            dist = abs(avg[0] - existing[0]) + abs(avg[1] - existing[1]) + abs(avg[2] - existing[2])
            if dist <= 36:
                matched = existing
                break
        if matched is None:
            clusters.append(avg)
            matched = avg

        hex_label = f"#{matched[0]:02x}{matched[1]:02x}{matched[2]:02x}"
        rule_colors.setdefault(hex_label, matched)

    ui_txt_rules = [
        {"key": k, "hex": k, "alpha": c[3]}
        for k, c in rule_colors.items()
    ]
    return {
        "detected_type": "palette",
        "resolved_mode": "palette",
        "resolved_theme": palette_theme if palette_theme and palette_theme != "auto" else "sampled",
        "inspection_only": True,
        "filling_txt": {k: list(c) for k, c in rule_colors.items()},
        "filling_num": {},
        "ui_txt_rules": ui_txt_rules,
        "ui_num_rules": [],
        "label_style": {
            "text_color": "#ffffff",
            "stroke_color": "#090d16",
            "stroke_width": 15,
            "auto_fit": True,
            "recommended_font": "Bips.ttf",
            "recommended_gsm": 15,
        },
        "stats": {
            "total_regions": sampled_regions,
            "unique_values": len(rule_colors),
            "min_value": None,
            "max_value": None,
            "sample_values": [],
        },
    }
