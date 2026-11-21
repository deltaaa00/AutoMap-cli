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

"""Orchestrate one map render for CLI commands and web generation endpoints.

The render flow loads artwork and seed coordinates, resolves AI or supplied
region values, selects filling rules, paints regions, and composites labels,
highlights, relief, borders, and the optional legend. Pixel coordinates refer
to bundled artwork, not latitude/longitude. Cached images from geometry are
shared and must not be modified directly.

Edit styling for palette/number rules, geometry for country masks and caches,
and legend for legend layout. This module owns render options, layer order,
metadata, and the optional Rust acceleration fallback.
"""
import logging
import random
import io
import math
import re
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import constants
from .geometry import (
    _RESERVED_HIGHLIGHT_COLORS,
    _build_extrema_highlight_layer,
    _compute_region_ids,
    _file_key,
    _floodfill_bool_mask,
    _load_rgba,
    _prepared_relief,
    _region_cache_dir,
    _region_index,
    _region_thresh,
)
from .legend import _draw_legend_overlay
from .styling import (
    _auto_fill_from_ai,
    _format_legend_bound,
    _format_legend_range,
    _format_number_value,
    _format_short_number,
    _format_text_label,
    _hex_to_rgba,
    _numeric_extrema,
    _parse_filling_overrides,
    _parse_short_number,
    _resolve_theme_name,
    _to_rgba_tuple,
)

from .functions import ai_request, load_filling, _warning as showwarning, _error as showerror
from . import log

def _country_key(name: object) -> str:
    """Normalize a country name to snake_case ASCII ("Côte d'Ivoire" -> "cote_d_ivoire")."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _world_countries_answer_norm(ai_answer: dict[str, object], keys: object) -> dict[str, object]:
    """Index AI answers by normalized country key, resolving common aliases."""
    norm: dict[str, object] = {}
    for k, v in (ai_answer or {}).items():
        norm.setdefault(_country_key(k), v)
    known = {_country_key(k) for k in keys}
    for alias, targets in constants._WORLD_COUNTRY_ALIASES.items():
        if alias not in norm:
            continue
        for target in targets:
            if target in known and target not in norm:
                norm[target] = norm[alias]
                break
    return norm


def _load_world_countries() -> tuple[dict[str, list[tuple[int, int]]], dict[str, int]]:
    """Import world-countries seed points and label sizes on first use."""
    from .world_countries_coords import world_countries_coords, world_countries_label_sizes

    return world_countries_coords, world_countries_label_sizes


_RUST_WARNED = False
_LAST_RUST_WARNING: str | None = None


def _record_rust_warning(msg: str) -> None:
    global _RUST_WARNED, _LAST_RUST_WARNING
    _LAST_RUST_WARNING = msg
    if not _RUST_WARNED:
        _RUST_WARNED = True
        log.warning(msg)
        logging.warning(msg)


def _get_automap_rs():
    """Return the native ``automap_rs`` module when available, else ``None``."""
    global _LAST_RUST_WARNING
    try:
        import automap_rs
        if hasattr(automap_rs, "paint_regions_rs") and hasattr(automap_rs, "alpha_composite_rs"):
            _LAST_RUST_WARNING = None
            return automap_rs
        raise ImportError("automap_rs map functions not built")
    except Exception as exc:
        _record_rust_warning(
            f"Rust map acceleration unavailable ({type(exc).__name__}: {exc}); falling back to Python/PIL."
        )
        return None


def _alpha_composite(base: Image.Image, overlay: Image.Image) -> Image.Image:
    """Alpha-composite ``overlay`` over ``base`` using Rust when available."""
    rs = _get_automap_rs()
    if rs is not None and base.size == overlay.size:
        try:
            base_rgba = base if base.mode == "RGBA" else base.convert("RGBA")
            overlay_rgba = overlay if overlay.mode == "RGBA" else overlay.convert("RGBA")
            out_bytes = rs.alpha_composite_rs(base_rgba.tobytes(), overlay_rgba.tobytes())
            return Image.frombytes("RGBA", base.size, out_bytes)
        except Exception as exc:
            _record_rust_warning(
                f"Rust alpha_composite_rs failed ({type(exc).__name__}: {exc}); falling back to PIL."
            )
    return Image.alpha_composite(base, overlay)


def _paint_regions(img: Image.Image, flat: np.ndarray, ids: np.ndarray,
                   colors: dict[int, tuple[int, int, int, int]]) -> Image.Image:
    """Return a copy of ``img`` with region ``i`` painted ``colors[i]``."""
    if not colors:
        return img.copy()
    n = int(max(colors)) + 1
    if ids.size:
        n = max(n, int(ids.max()) + 1)
    lut = np.zeros((n, 4), dtype=np.uint8)
    use = np.zeros(n, dtype=np.uint8)
    for i, c in colors.items():
        lut[i] = c
        use[i] = 1
    rs = _get_automap_rs()
    if rs is not None:
        try:
            base_rgba = img if img.mode == "RGBA" else img.convert("RGBA")
            out_bytes = rs.paint_regions_rs(
                base_rgba.tobytes(),
                np.ascontiguousarray(flat, dtype=np.uint32).tobytes(),
                np.ascontiguousarray(ids, dtype=np.uint16).tobytes(),
                lut.tobytes(),
                use.tobytes(),
            )
            return Image.frombytes("RGBA", img.size, out_bytes)
        except Exception as exc:
            _record_rust_warning(
                f"Rust paint_regions_rs failed ({type(exc).__name__}: {exc}); falling back to NumPy."
            )
    arr = np.array(img, dtype=np.uint8)
    pix = arr.reshape(-1, 4)
    sel = use.astype(bool, copy=False)[ids]
    pix[flat[sel]] = lut[ids[sel]]
    return Image.fromarray(arr)


def _random_region_color() -> tuple[int, int, int, int]:
    """Pick a random bright RGBA color that stays distinct from reserved highlight colors."""
    for _ in range(16):
        c = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255))
        if all(sum((c[i] - r[i]) ** 2 for i in range(3)) >= 90 ** 2 for r in _RESERVED_HIGHLIGHT_COLORS):
            return (*c, 255)
    return (120, 180, 120, 255)


def auto_map(prompt: str, mode: str, map_type: str, size_mod: int,
             optional_feature: str, model: str, font_name: str,
             show_result: bool = True, io_res = False, api_key: str | None = None,
             *,
             filling_txt_override: dict[str, object] | None = None,
             filling_num_override: object | None = None,
             label_style: dict[str, object] | None = None,
             auto_style: bool | None = None,
             palette_theme: str = "auto",
             ai_answer_override: dict[str, object] | None = None,
             temperature: float | None = None,
             web_search: bool = False,
             max_search_requests: int = 5,
             tavily_api_key: str | None = None,
             show_legend: bool = False,
             highlight_extrema: bool = False,
             highlight_high_color: str = "#00e5ff",
             highlight_low_color: str = "#ff2daa",
             label_suffix: str = "",
             skip_label_countries: set[str] | list[str] | tuple[str, ...] | None = None,
             progress_callback=None):
    """Create a map from an AI query and save it as ``result.png``.

    CLI options:
      mode             -- "text" or "number"
      map_type         -- "default", "flag", "world" or "world_countries"
      size_mod         -- global font size modifier (default 15)
      optional_feature -- None, "show_most_least", "short_form", or comma-separated flags
      model            -- Gemini model id
      font_name        -- font file name from resources/fonts
      show_result      -- open the result in the OS image viewer (GUI only)
      io_res           -- save result in io (for web gui)
      api_key          -- optional Gemini API key (overrides env var for this run)
      temperature      -- optional AI sampling randomness, from 0 to 2
    """

    # Reuse font objects across regions that share a label size.
    _font_cache = {}

    def get_font(size):
        key = (font_name, size)
        if key not in _font_cache:
            font_path = constants.FONTS_DIR / font_name
            _font_cache[key] = ImageFont.truetype(str(font_path), size)
        return _font_cache[key]

    req_txt, req_num = _parse_filling_overrides(filling_txt_override, filling_num_override)
    has_txt_override = filling_txt_override is not None
    has_num_override = filling_num_override is not None
    if has_txt_override and has_num_override:
        filling_txt, filling_num = dict(req_txt), dict(req_num)
    else:
        disk_txt, disk_num = load_filling()  # Loading filling from JSON
        filling_txt = dict(req_txt) if has_txt_override else disk_txt
        filling_num = dict(req_num) if has_num_override else disk_num

    # Case-insensitive lookups for text mode. Keys from the JSON are trimmed and
    # lower-cased so that AI answers like "Yes", "yes" or " yes " all match.
    filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}

    label_options = {}
    if isinstance(label_style, dict):
        label_options = label_style

    label_fill = _hex_to_rgba(label_options.get("text_color"), (255, 255, 255, 255))
    label_stroke_fill = _hex_to_rgba(label_options.get("stroke_color"), (0, 0, 0, 255))
    try:
        raw_stroke_width = label_options.get("stroke_width", 15)
        if raw_stroke_width is None:
            raw_stroke_width = 15
        label_stroke_width = max(0, min(40, int(raw_stroke_width)))
    except (ValueError, TypeError, OverflowError):
        label_stroke_width = 15

    label_auto_fit = bool(label_options.get("auto_fit", False))
    label_offset_x = 0
    try:
        raw_offset = label_options.get('offset_x', 0)
        # Booleans are numeric in Python, but are not meaningful pixel offsets.
        if not isinstance(raw_offset, bool):
            offset = float(raw_offset)
            if math.isfinite(offset):
                label_offset_x = max(-500, min(500, round(offset)))
    except (TypeError, ValueError, OverflowError):
        label_offset_x = 0

    def _parse_optional_features(value: object) -> set[str]:
        """Normalize ``optional_feature`` (string or collection) into a set of active flags."""
        if not value:
            return set()
        if isinstance(value, (list, tuple, set, frozenset)):
            tokens = {str(item).strip().lower() for item in value}
        else:
            raw = str(value).strip().lower()
            if raw == "both":
                return {"show_most_least", "short_form"}
            tokens = {part.strip() for part in re.split(r"[,+\s]+", raw) if part.strip()}
        tokens.discard("none")
        return tokens

    features = _parse_optional_features(optional_feature)
    has_most_least = "show_most_least" in features
    has_short_form = "short_form" in features

    if has_most_least and mode == "text":
        showwarning("Warning", "Most/least feature may be incompatible with text mode. Turning off...")
        has_most_least = False

    if has_most_least and map_type == "world":
        showwarning("Warning", "Most/Least feature is not available for world map. It will be disabled.")
        has_most_least = False

    if has_short_form and ai_answer_override is None:
        prompt += " Write in English short form only (k, M, B, T), for example 1000=1k, 1000000=1M, 1000000000=1B, 1000000000000=1T etc."

    random_colors = {}

    log.step("Starting map generation")
    log.config_table({
        "Mode": mode,
        "Map": map_type,
        "Model": model,
        "Font": font_name,
    })

    # map selecting
    wc_label_sizes: dict[str, int] = {}
    if map_type == "default":
        path = Path(__file__).parent.parent / "./resources/maps/default_map.png"
        dict_ = constants.europe_coords
    elif map_type == 'flag':
        path = Path(__file__).parent.parent / "./resources/maps/flag_map.png"
        dict_ = constants.europe_coords
    elif map_type == 'world':
        path = Path(__file__).parent.parent / "./resources/maps/world_map.png"
        dict_ = constants.world_coords
    elif map_type == 'world_countries':
        path = Path(__file__).parent.parent / "./resources/maps/world_countries_map.png"
        dict_, wc_label_sizes = _load_world_countries()
    else:
        showerror("Error", f"Unknown map type: {map_type}")
        return

    # opening selected map
    log.step(f"Loading base map: {path.name}")
    img = _load_rgba(path)  # shared cached copy; never mutated in place
    log.info(f"Base map size: {img.size[0]}x{img.size[1]}")

    # Keep labels separate so later flood fills and overlays cannot paint over them.
    text_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(text_layer)

    def _report(stage: int, label: str) -> None:
        if progress_callback is not None:
            progress_callback(stage, label)

    _report(1, "Querying AI model...")
    if ai_answer_override is not None:
        ai_answer = dict(ai_answer_override)
    else:
        # Requesting information from AI
        log.step("Requesting data from AI")
        ai_options = {} if temperature is None else {'temperature': temperature}
        if web_search:
            ai_options["web_search"] = True
            ai_options["max_search_requests"] = max_search_requests
            if tavily_api_key:
                ai_options["tavily_api_key"] = tavily_api_key
        ai_answer = ai_request(prompt, map_type, model, api_key, **ai_options)

    if ai_answer is None:
        showerror("Error", "No information or bad info received from AI")
        return

    raw_ai_answer = dict(ai_answer)
    if has_short_form:
        ai_answer = {k: _format_short_number(v) for k, v in ai_answer.items()}

    log.success(f"Got information from AI ({len(ai_answer)} entries)")

    is_painted_map = map_type in constants._PAINTED_MAP_TYPES
    if is_painted_map:
        resolved_theme = _resolve_theme_name(palette_theme, prompt=prompt, mode=mode)
        auto_txt, auto_num = _auto_fill_from_ai(
            ai_answer,
            mode,
            palette_theme=palette_theme,
            prompt=prompt,
        )
    else:
        resolved_theme = "flags"
        auto_txt, auto_num = {}, {}
        filling_txt = {}
        filling_txt_ci = {}
        filling_num = {}
    theme_requested = (palette_theme or "auto").strip().lower() != "auto"

    if is_painted_map and auto_style is True:
        if mode == "text":
            merged_txt = dict(auto_txt)
            requested_keys = {key.strip().lower() for key in req_txt}
            uses_default_text_rules = requested_keys == constants._DEFAULT_UI_TXT_KEYS
            if req_txt and (not theme_requested or not uses_default_text_rules):
                original_keys = {key.strip().lower(): key for key in merged_txt}
                for requested_key, requested_color in req_txt.items():
                    display_key = requested_key
                    if has_short_form:
                        display_key = str(_format_short_number(requested_key)).strip()
                    # Prefer a compact-label match, then the original label, then a new rule.
                    fallback_key = original_keys.get(requested_key.strip().lower(), display_key)
                    target_key = original_keys.get(display_key.lower(), fallback_key)
                    merged_txt[target_key] = requested_color
            filling_txt = merged_txt
            filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}
        elif mode == "number":
            if req_num and set(req_num.keys()) != constants._DEFAULT_UI_NUM_BOUNDS and not theme_requested:
                filling_num = dict(req_num)
            elif auto_num:
                filling_num = dict(auto_num)
    elif auto_style is False:
        # Honor the request's explicit rules without overwriting them with auto_fill.
        pass
    else:
        if not filling_txt and auto_txt:
            filling_txt = auto_txt
            filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}
        if not filling_num and auto_num:
            filling_num = auto_num

    if has_short_form:
        for k, v in list(filling_txt.items()):
            short_k = str(_format_short_number(k)).strip().lower()
            if short_k:
                filling_txt_ci.setdefault(short_k, v)

    most_least = None
    extrema = _numeric_extrema(raw_ai_answer) if has_most_least else None
    if has_most_least and extrema is None:
        log.warning("No numeric values available; skipping most / least overlay")
    if extrema is not None:
        log.step("Computing most / least values")
        most_least = Image.new("RGBA", img.size, (255, 255, 255, 0))
        ml_draw = ImageDraw.Draw(most_least)
        '''Writing the most and the least country'''
        most_country, least_country = extrema
        log.info(f"Most: {most_country} | Least: {least_country}")

        if map_type == "world_countries":
            ml_xy = (int(img.size[0] * 0.025), int(img.size[1] * 0.80))
            ml_size = max(20, int(img.size[1] * 0.036))
            ml_stroke = min(label_stroke_width, max(2, ml_size // 5)) if label_stroke_width > 0 else 0
            ml_text = (
                f"Most: {most_country.replace('_', ' ').title()}\n"
                f"Least: {least_country.replace('_', ' ').title()}"
            )
        else:
            ml_xy = (106, 1240)
            ml_size = 150
            ml_stroke = label_stroke_width
            ml_text = f'Most: {most_country.capitalize()}\nLeast: {least_country.capitalize()}'

        ml_draw.text(ml_xy,
                     ml_text,
                     font=get_font(ml_size),
                     fill=label_fill,
                     stroke_width=ml_stroke,
                     stroke_fill=label_stroke_fill)

    total = len(dict_)
    _report(2, "Painting regions & labels...")
    log.step(f"Painting {total} regions")
    used_txt_rules: dict[str, tuple[int, int, int, int]] = {}
    used_num_rules: dict[tuple[float, float], tuple[int, int, int, int]] = {}

    if map_type == "world_countries":
        ai_answer_norm = _world_countries_answer_norm(ai_answer or {}, dict_.keys())
        raw_ai_answer_norm = _world_countries_answer_norm(raw_ai_answer or {}, dict_.keys())
    else:
        ai_answer_norm = {
            str(k).strip().lower().replace(" ", "_").replace("-", "_"): v
            for k, v in (ai_answer or {}).items()
        }
        raw_ai_answer_norm = {
            str(k).strip().lower().replace(" ", "_").replace("-", "_"): v
            for k, v in (raw_ai_answer or {}).items()
        }
        if "oceania" in ai_answer_norm and "australia" not in ai_answer_norm:
            ai_answer_norm["australia"] = ai_answer_norm["oceania"]
        if "oceania" in raw_ai_answer_norm and "australia" not in raw_ai_answer_norm:
            raw_ai_answer_norm["australia"] = raw_ai_answer_norm["oceania"]

    region_colors: dict[int, tuple[int, int, int, int]] = {}
    numeric_region_values: dict[int, float] = {}

    clean_suffix = str(label_suffix or "").replace("\r", "").replace("\n", "")[:16]
    skip_labels_ci: set[str] = set()
    for item in (skip_label_countries or ()):
        s = str(item or "").strip().lower()
        if s:
            skip_labels_ci.add(s)
            skip_labels_ci.add(s.replace("_", " "))
            skip_labels_ci.add(s.replace(" ", "_"))

    for index, (name, points) in enumerate(dict_.items(), start=1):
        name_key = (
            _country_key(name)
            if map_type == "world_countries"
            else str(name).strip().lower().replace(" ", "_").replace("-", "_")
        )
        # Resolve numeric values against explicit ranges before automatic colors.
        if mode == 'number':
            try:
                info = ai_answer_norm[name_key]
                color = (0, 0, 0, 0)
                if is_painted_map:
                    color = _random_region_color()
                num_value = _parse_short_number(info)
                raw_num_value = _parse_short_number(raw_ai_answer_norm.get(name_key))
                if num_value is None and is_painted_map:
                    log.warning(f"Non-numeric value for {name}: {info!r}; using random color")

                if num_value is not None:
                    if highlight_extrema:
                        highlight_value = num_value
                        if raw_num_value is not None:
                            highlight_value = raw_num_value
                        numeric_region_values[index] = float(highlight_value)
                    if is_painted_map:
                        # Pick the colour of the first matching range. Ranges are
                        # treated as (low < value <= high) per the docs.
                        matched_bounds = None
                        for candidate in (num_value, raw_num_value):
                            if matched_bounds is not None:
                                break
                            if candidate is None:
                                continue
                            for (low_lim, high_lim), color_tup in filling_num.items():
                                if low_lim < candidate <= high_lim:
                                    color = color_tup
                                    matched_bounds = (low_lim, high_lim)
                                    break
                        if matched_bounds is None and auto_style is True and auto_num:
                            for (low_lim, high_lim), color_tup in auto_num.items():
                                if low_lim < num_value <= high_lim:
                                    color = color_tup
                                    matched_bounds = (low_lim, high_lim)
                                    break
                        if matched_bounds is not None:
                            used_num_rules[matched_bounds] = color
            except KeyError as e:
                info = ""
                if is_painted_map:
                    log.warning(f"No data / color for {e}; using random color")
                    color = _random_region_color()
                    random_colors[name] = color
                else:
                    color = (0, 0, 0, 0)

        elif mode == 'text':
            # Repeated text categories share a color, including fallback colors.
            raw_info = ai_answer_norm.get(name_key)
            info = _format_text_label(raw_info)
            if not info:
                log.info(f"No information for {name}, skipping")
                continue
            if is_painted_map:
                try:
                    # Match filling colours case-insensitively and ignoring whitespace,
                    # so "Yes", "yes" and " yes " all find the same colour.
                    color = filling_txt_ci[info.lower()]
                except KeyError:
                    log.info(f"No filling color for '{info}'; using random")
                    # Repeated answers share a color so categories remain recognizable.
                    try:
                        color = random_colors[info]
                    except KeyError:
                        color = _random_region_color()
                        random_colors[info] = color
                used_txt_rules.setdefault(info, color)
            else:
                color = (0, 0, 0, 0)
        else:
            showerror("Error", f"Unknown mode: {mode}")
            return

        log.info(f"[{index}/{total}] Painting {name} -> {info}")

        # Flag maps retain their original artwork. Other maps use every seed point
        # to fill disconnected pieces of a region, such as islands.
        # Painting is applied after the loop from a cached region-ID map.
        if map_type in constants._PAINTED_MAP_TYPES:
            region_colors[index] = tuple(int(c) for c in color)

        if not points:
            continue

        if name_key in skip_labels_ci or str(name).strip().lower() in skip_labels_ci:
            continue

        # The first fill seed also anchors the region's single label.
        x, y = points[0]
        x += label_offset_x

        info = str(info)
        if clean_suffix and not info.endswith(clean_suffix):
            info = f"{info}{clean_suffix}"

        if map_type == 'flag' or map_type == 'default':
            if len(info) >= 4:
                x -= 70
            elif len(info) >= 2:
                x -= 30

            size = constants._EUROPE_LABEL_SIZES.get(name, 125)
            if name in ('russia', 'usa'):
                y -= 10
            elif name in ('north_macedonia', 'bosnia'):
                y -= 25

            if len(info) == 1:
                y -= 20
                size += 25

            if label_auto_fit and len(info) >= 6:
                size = max(45, int(size * max(0.55, 5.0 / len(info))))

            size = max(20, size + size_mod)  # Global size modifier for other fonts. Main font is BIPS

            font = get_font(size)  # getting font from cache

            draw.text((x, y),
                      info,
                      font=font,
                      fill=label_fill,
                      stroke_width=label_stroke_width,
                      stroke_fill=label_stroke_fill)

        elif map_type == 'world':  # text size for World map
            size = constants._WORLD_LABEL_SIZES.get(name, 100)

            if label_auto_fit and len(info) >= 6:
                size = max(40, int(size * max(0.55, 5.0 / len(info))))

            size = max(20, size + size_mod)  # Global size modifier for other fonts. Main font is BIPS

            draw.text((x, y),
                      info,
                      font=get_font(size),
                      fill=label_fill,
                      stroke_width=label_stroke_width,
                      stroke_fill=label_stroke_fill)

        elif map_type == 'world_countries':
            base_size = wc_label_sizes.get(name)
            if base_size is None:
                base_size = wc_label_sizes.get(name_key, 0)
            if base_size and base_size >= constants._WORLD_COUNTRIES_MIN_LABEL:
                size = base_size
                if label_auto_fit and len(info) >= 6:
                    size = max(14, int(size * max(0.55, 5.0 / len(info))))
                size = max(12, size + size_mod)
                # Cap outline thickness for compact country labels so strokes stay legible.
                stroke_w = min(label_stroke_width, max(2, size // 5)) if label_stroke_width > 0 else 0
                draw.text((x, y),
                          info,
                          font=get_font(size),
                          fill=label_fill,
                          stroke_width=stroke_w,
                          stroke_fill=label_stroke_fill,
                          anchor="mm")

    effective_txt = dict(used_txt_rules) if used_txt_rules else dict(filling_txt or auto_txt)
    seen_txt_lower = {str(uk).strip().lower() for uk in effective_txt}
    for k, v in (filling_txt or {}).items():
        k_str = str(k)
        k_low = k_str.strip().lower()
        if k_low not in seen_txt_lower:
            effective_txt[k_str] = v
            seen_txt_lower.add(k_low)
    effective_num = dict(filling_num or auto_num)
    for bounds, c in used_num_rules.items():
        effective_num[bounds] = c

    render_meta = {
        "ai_answer": dict(ai_answer),
        "raw_ai_answer": raw_ai_answer,
        "mode": mode,
        "resolved_theme": resolved_theme,
        "label_suffix": clean_suffix,
        "filling_txt": {str(k): list(v) for k, v in effective_txt.items()},
        "filling_num": {f"({lo}, {hi})": list(v) for (lo, hi), v in effective_num.items()},
        "used_txt": {str(k): list(v) for k, v in (used_txt_rules or effective_txt).items()},
        "used_num": {f"({lo}, {hi})": list(v) for (lo, hi), v in (used_num_rules or effective_num).items()},
        "label_style": {
            "text_color": f"#{label_fill[0]:02x}{label_fill[1]:02x}{label_fill[2]:02x}",
            "stroke_color": f"#{label_stroke_fill[0]:02x}{label_stroke_fill[1]:02x}{label_stroke_fill[2]:02x}",
            "stroke_width": label_stroke_width,
            "auto_fit": label_auto_fit,
            "offset_x": label_offset_x,
            "recommended_font": font_name or ("Bips.ttf" if mode == "number" else "DIN Condensed.ttf"),
            "recommended_gsm": size_mod,
        },
    }

    flat = ids = None
    if map_type in constants._PAINTED_MAP_TYPES:
        flat, ids = _region_index(path, img, dict_)
        img = _paint_regions(img, flat, ids, region_colors)

    highest_idx = lowest_idx = None
    if highlight_extrema and mode == "number" and numeric_region_values:
        highest_idx = max(numeric_region_values, key=numeric_region_values.get)
        lowest_idx = min(numeric_region_values, key=numeric_region_values.get)

    # adding relief map
    _report(3, "Adding relief, borders & overlays...")
    log.step("Compositing layers")
    _layer_assets = {
        "default": ("map_relief.png", "default_map_blured.png"),
        "flag": ("map_relief.png", "default_map_blured.png"),
        "world": ("world_map_relief.png", "world_map_blured.png"),
        "world_countries": ("world_map_relief.png", "world_countries_map_blured.png"),
    }
    if map_type in _layer_assets:
        relief_name, borders_name = _layer_assets[map_type]
        log.info("Adding relief + borders overlay")
        # Relief is resized and its opacity reduced once per process.
        maps_dir = Path(__file__).parent.parent / "resources" / "maps"
        relief = _prepared_relief(maps_dir / relief_name, img.size)
        result = _alpha_composite(img, relief)

        # Restore borders above the relief, then put readable labels on top.
        borders = _load_rgba(maps_dir / borders_name)
        if highest_idx is not None:
            high_rgb = _hex_to_rgba(highlight_high_color, (*_RESERVED_HIGHLIGHT_COLORS[0], 255))[:3]
            low_rgb = _hex_to_rgba(highlight_low_color, (*_RESERVED_HIGHLIGHT_COLORS[1], 255))[:3]
            hl_layer = _build_extrema_highlight_layer(
                img.size, highest_idx, lowest_idx, map_type, dict_, flat, ids, borders,
                base_map=img, high_rgb=high_rgb, low_rgb=low_rgb,
            )
            if hl_layer is not None:
                result = _alpha_composite(result, hl_layer)
        result = _alpha_composite(result, borders)
        result = _alpha_composite(result, text_layer)

        if has_most_least and map_type != "world" and most_least is not None:
            result = _alpha_composite(result, most_least)
    else:
        result = _alpha_composite(img, text_layer)

    if show_legend and is_painted_map:
        if mode == "number":
            legend_src = used_num_rules or effective_num
            legend_entries = [
                (tuple(c), _format_legend_range(lo, hi))
                for (lo, hi), c in sorted(legend_src.items(), key=lambda item: (item[0][0], item[0][1]))
            ]
        else:
            legend_src = used_txt_rules or effective_txt
            legend_entries = [
                (tuple(c), _format_text_label(label))
                for label, c in legend_src.items()
                if str(label).strip()
            ]
        has_ml = has_most_least and map_type != "world" and most_least is not None
        legend_layer = _draw_legend_overlay(
            img.size,
            legend_entries,
            get_font,
            label_fill,
            label_stroke_fill,
            label_stroke_width,
            map_type,
            has_ml,
        )
        if legend_layer is not None:
            result = _alpha_composite(result, legend_layer)


    _report(4, "Encoding final PNG...")
    if _LAST_RUST_WARNING:
        render_meta["rust_warning"] = _LAST_RUST_WARNING
    if io_res:
        # Return an in-memory PNG for the web API instead of writing result.png.

        buffer = io.BytesIO()
        result.save(buffer, format='PNG', compress_level=1)
        # Consumers should be able to read the PNG from its first byte.
        buffer.seek(0)
        buffer.map_meta = render_meta

        return buffer


    out_path = Path(__file__).parent.parent / "result.png"
    log.step(f"Saving result to {out_path}")
    result.save(out_path, compress_level=1)

    if show_result:
        result.show("Map")

    log.finished(f"Finished! Map saved as {out_path}")

    return out_path
