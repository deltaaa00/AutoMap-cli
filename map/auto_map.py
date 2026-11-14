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

import random
import io
import math
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .constants import europe_coords, world_coords
from .functions import ai_request, load_filling, _warning as showwarning, _error as showerror
from . import log


LAST_GENERATED_MAP_STATE: dict[str, object] = {}

_AUTO_PALETTE: tuple[tuple[int, int, int, int], ...] = (
    (56, 189, 248, 255),   # #38bdf8 Sky
    (99, 102, 241, 255),   # #6366f1 Indigo
    (16, 185, 129, 255),   # #10b981 Emerald
    (245, 158, 11, 255),   # #f59e0b Amber
    (244, 63, 94, 255),    # #f43f5e Rose
    (168, 85, 247, 255),   # #a855f7 Purple
    (20, 184, 166, 255),   # #14b8a6 Teal
    (236, 72, 153, 255),   # #ec4899 Pink
    (234, 179, 8, 255),    # #eab308 Gold
    (249, 115, 22, 255),   # #f97316 Orange
)

_SEMANTIC_COLORS: dict[str, tuple[int, int, int, int]] = {
    "yes": (46, 204, 113, 255), "так": (46, 204, 113, 255), "true": (46, 204, 113, 255),
    "no": (231, 76, 60, 255), "ні": (231, 76, 60, 255), "false": (231, 76, 60, 255),
    "tea": (16, 185, 129, 255), "coffee": (245, 158, 11, 255), "coffe": (245, 158, 11, 255),
    "beer": (234, 179, 8, 255), "wine": (225, 29, 72, 255),
}


def _parse_short_number(val: object) -> float | None:
    """Parse plain numbers or short-form numbers like '10k', '2.5M', '1B'."""
    if isinstance(val, bool) or val is None:
        return None
    s = str(val).replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    mult = 1.0
    low = s.lower()
    if low.endswith("k"):
        mult, s = 1_000.0, s[:-1]
    elif low.endswith("m"):
        mult, s = 1_000_000.0, s[:-1]
    elif low.endswith("b"):
        mult, s = 1_000_000_000.0, s[:-1]
    try:
        num = float(s) * mult
    except ValueError:
        return None
    return num if math.isfinite(num) else None


def _auto_fill_from_ai(
    ai_answer: dict[str, object],
    mode: str,
) -> tuple[dict[str, tuple[int, int, int, int]], dict[tuple[int, int], tuple[int, int, int, int]]]:
    """Inspect AI response values (numbers, short-form numbers, or words) and pick colors."""
    auto_txt: dict[str, tuple[int, int, int, int]] = {}
    auto_num: dict[tuple[int, int], tuple[int, int, int, int]] = {}

    if mode == "number":
        nums: list[int] = []
        for val in ai_answer.values():
            parsed = _parse_short_number(val)
            if parsed is not None:
                nums.append(int(round(parsed)))
        if nums:
            nums.sort()
            low_start = min(0, nums[0] - 1) if nums[0] <= 10 else nums[0] - 1
            buckets = min(5, max(1, len(set(nums))))
            bounds = [low_start]
            for i in range(1, buckets):
                q = nums[int(round((len(nums) - 1) * (i / buckets)))]
                bounds.append(max(bounds[-1] + 1, q))
            bounds.append(max(bounds[-1] + 1, nums[-1]))
            for i in range(buckets):
                auto_num[(bounds[i], bounds[i + 1])] = _AUTO_PALETTE[i % len(_AUTO_PALETTE)]
    else:
        labels = [str(v).strip() for v in ai_answer.values() if v is not None and str(v).strip()]
        parsed_pairs = [(lbl, _parse_short_number(lbl)) for lbl in labels]
        numeric_vals = sorted(num for _, num in parsed_pairs if num is not None)

        # In text mode with short_form (e.g. '10k', '2.5M'), group numeric labels into
        # gradient buckets instead of giving every distinct number its own random color.
        if numeric_vals and len(numeric_vals) >= max(2, int(len(labels) * 0.6)):
            buckets = min(5, max(1, len(set(numeric_vals))))
            thresholds = [
                numeric_vals[int(round((len(numeric_vals) - 1) * (i / buckets)))]
                for i in range(1, buckets)
            ]
            for lbl, num in parsed_pairs:
                if num is not None:
                    b_idx = sum(1 for t in thresholds if num > t)
                    auto_txt[lbl] = _AUTO_PALETTE[b_idx % len(_AUTO_PALETTE)]
                else:
                    auto_txt.setdefault(lbl, _SEMANTIC_COLORS.get(lbl.lower(), _AUTO_PALETTE[5]))
        else:
            canonical: dict[str, str] = {}
            for lbl in labels:
                canonical.setdefault(lbl.lower(), lbl)
            idx = 0
            for low_lbl, _ in Counter(lbl.lower() for lbl in labels).most_common():
                orig = canonical[low_lbl]
                if low_lbl in _SEMANTIC_COLORS:
                    auto_txt[orig] = _SEMANTIC_COLORS[low_lbl]
                else:
                    auto_txt[orig] = _AUTO_PALETTE[idx % len(_AUTO_PALETTE)]
                    idx += 1

    return auto_txt, auto_num


def _numeric_extrema(values):
    """Return region names for finite numeric extrema, or None if absent."""
    numeric = {}
    for name, value in values.items():
        # bool converts to 0/1 in Python, but is not a numeric measurement here.
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number):
            numeric[name] = number
    if not numeric:
        return None
    return max(numeric, key=numeric.get), min(numeric, key=numeric.get)


def auto_map(prompt: str, mode: str, map_type: str, size_mod: int,
             optional_feature: str, model: str, font_name: str,
             show_result: bool = True, io_res = False, api_key: str | None = None):
    """Create a map from an AI query and save it as ``result.png``.

    CLI options:
      mode             -- "text" or "number"
      map_type         -- "default", "flag" or "world"
      size_mod         -- global font size modifier (default 15)
      optional_feature -- None, "show_most_least" or "short_form"
      model            -- Gemini model id
      font_name        -- font file name from resources/fonts
      show_result      -- open the result in the OS image viewer (GUI only)
      io_res           -- save result in io (for web gui)
      api_key          -- optional Gemini API key (overrides env var for this run)
    """

    # Reuse font objects across regions that share a label size.
    _font_cache = {}

    def get_font(size):
        key = (font_name, size)
        if key not in _font_cache:
            font_path = Path("resources/fonts") / font_name
            _font_cache[key] = ImageFont.truetype(str(font_path), size)
        return _font_cache[key]

    filling_txt, filling_num = load_filling()  # Loading filling from JSON

    # Case-insensitive lookups for text mode. Keys from the JSON are trimmed and
    # lower-cased so that AI answers like "Yes", "yes" or " yes " all match.
    filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}

    # Most least and short form conflict solution
    # @TODO Make most/least and short form compatible

    if optional_feature == "short_form" and mode == "number":
        showwarning("Warning", "Short form feature is only available for text mode. It will be disabled.")
        optional_feature = None

    if optional_feature == "show_most_least" and mode == "text":
        showwarning("Warning", "Most/least feature may be incompatible with text mode. Turning off...")
        optional_feature = None

    if optional_feature == "show_most_least" and map_type == "world":
        showwarning("Warning", "Most/Least feature is not available for world map. It will be disabled.")
        optional_feature = None

    if optional_feature == "short_form":
        prompt += " Write in short form, for example 1000=1k, 1000000=1M etc."

    random_colors = {}

    log.step("Starting map generation")
    log.config_table({
        "Mode": mode,
        "Map": map_type,
        "Model": model,
        "Font": font_name,
    })

    # map selecting
    if map_type == "default":
        path = Path(__file__).parent.parent / "./resources/maps/default_map.png"
        dict_ = europe_coords
    elif map_type == 'flag':
        path = Path(__file__).parent.parent / "./resources/maps/flag_map.png"
        dict_ = europe_coords
    elif map_type == 'world':
        path = Path(__file__).parent.parent / "./resources/maps/world_map.png"
        dict_ = world_coords
    else:
        showerror("Error", f"Unknown map type: {map_type}")
        return

    # opening selected map
    log.step(f"Loading base map: {path.name}")
    img = Image.open(path).convert("RGBA")
    log.info(f"Base map size: {img.size[0]}x{img.size[1]}")

    # Keep labels separate so later flood fills and overlays cannot paint over them.
    text_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(text_layer)

    # Requesting information from AI
    log.step("Requesting data from AI")
    ai_answer = ai_request(prompt, map_type, model, api_key)

    if ai_answer is None:
        showerror("Error", "No information or bad info received from AI")
        return

    log.success(f"Got information from AI ({len(ai_answer)} entries)")

    # Auto-pick colors based on the numbers or words returned by AI when filling.json has no rules
    auto_txt, auto_num = _auto_fill_from_ai(ai_answer, mode)
    if not filling_txt and auto_txt:
        filling_txt = auto_txt
        filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}
    if not filling_num and auto_num:
        filling_num = auto_num

    LAST_GENERATED_MAP_STATE.clear()
    LAST_GENERATED_MAP_STATE.update({
        "ai_answer": dict(ai_answer),
        "filling_txt": {str(k): list(v) for k, v in (filling_txt or auto_txt).items()},
        "filling_num": {f"({lo}, {hi})": list(v) for (lo, hi), v in (filling_num or auto_num).items()},
    })

    most_least = None
    extrema = _numeric_extrema(ai_answer) if optional_feature == "show_most_least" else None
    if optional_feature == "show_most_least" and extrema is None:
        log.warning("No numeric values available; skipping most / least overlay")
    if extrema is not None:
        log.step("Computing most / least values")
        most_least = Image.new("RGBA", img.size, (255, 255, 255, 0))
        ml_draw = ImageDraw.Draw(most_least)
        '''Writing the most and the least country'''
        most_country, least_country = extrema
        log.info(f"Most: {most_country} | Least: {least_country}")

        ml_draw.text((106, 1240),
                     f'Most: {most_country.capitalize()}\nLeast: {least_country.capitalize()}',
                     font=ImageFont.truetype(str(Path("resources/fonts") / font_name), 150),
                     fill=(255, 255, 255, 255),
                     stroke_width=15,
                     stroke_fill=(0, 0, 0, 255))

    total = len(dict_)
    log.step(f"Painting {total} regions")

    for index, (name, points) in enumerate(dict_.items(), start=1):
        '''Number mode'''
        if mode == 'number':
            try:
                info = ai_answer[name]
                color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
                try:
                    num_value = int(info)
                except (ValueError, TypeError):
                    log.warning(f"Non-numeric value for {name}: {info!r}; using random color")
                    num_value = None

                if num_value is not None:
                    # Pick the colour of the first matching range. Ranges are
                    # treated as (low < value <= high) per the docs.
                    for (low_lim, high_lim), color_tup in filling_num.items():
                        if low_lim < num_value <= high_lim:
                            color = color_tup
                            break
            except KeyError as e:
                info = ""
                log.warning(f"No data / color for {e}; using random color")
                color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
                random_colors[name] = color

        elif mode == 'text':
            '''Text mode'''
            info = str(ai_answer.get(name, "")).strip()
            if not info:
                log.info(f"No information for {name}, skipping")
                continue
            try:
                # Match filling colours case-insensitively and ignoring whitespace,
                # so "Yes", "yes" and " yes " all find the same colour.
                color = filling_txt_ci[info.lower()]
            except KeyError:
                if map_type == 'default' or map_type == 'world':
                    log.info(f"No filling color for '{info}'; using random")
                    # Repeated answers share a color so categories remain recognizable.
                    try:
                        color = random_colors[info]
                    except KeyError:
                        color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
                        random_colors[info] = color
                else:
                    color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
        else:
            showerror("Error", f"Unknown mode: {mode}")
            return

        log.info(f"[{index}/{total}] Painting {name} -> {info}")

        # Flag maps retain their original artwork. Other maps use every seed point
        # to fill disconnected pieces of a region, such as islands.
        if map_type == 'default' or map_type == 'world':
            for coord in points:
                # Tolerances are tuned to the shading in the source map images.
                if name == "south_America":
                    thresh = 15
                elif name == "asia":
                    thresh = 20
                else:
                    thresh = 30

                ImageDraw.floodfill(img, xy=coord, value=color, thresh=thresh)

        # The first fill seed also anchors the region's single label.
        x, y = points[0]

        info = str(info)

        if map_type == 'flag' or map_type == 'default':
            if len(info) >= 4:
                x -= 70
            elif len(info) >= 2:
                x -= 30
            # number in countries

            if name in ['cyprus', 'kosovo', 'montenegro']:
                size = 70
            elif name in ['ukraine', 'poland', 'france', 'spain',
                          'turkey', 'romania', 'italy', 'belarus', 'united_kingdom', 'germany']:
                size = 175
            elif name in ['sweden', 'norway', 'finland']:
                size = 100
            elif name in ['russia', 'usa']:
                y -= 10
                size = 250
            elif name in ['slovenia', 'latvia', 'lithuania', 'estonia', 'switzerland', 'austria', 'slovakia',
                          'hungary', 'croatia', 'netherlands', 'belgium', 'czechia', 'moldova',
                          'bosnia_and_herzegovina', 'serbia', 'bulgaria', 'albania', 'greece', 'ireland',
                          'iceland', 'norway', 'finland', 'sweden', 'denmark', 'portugal']:
                size = 70
            elif name in ['ireland', 'belgium', 'estonia', 'lithuania', 'north_macedonia',
                          'switzerland', 'bosnia']:
                y -= 25
                size = 125
            else:
                size = 125

            if len(info) == 1:
                y -= 20
                size += 25

            size += size_mod  # Global size modifier for other fonts. Main font is BIPS

            font = get_font(size)  # getting font from cache

            draw.text((x, y),
                      info,
                      font=font,
                      fill=(255, 255, 255, 255),
                      stroke_width=15,
                      stroke_fill=(0, 0, 0, 255))

        elif map_type == 'world':  # text size for World map

            if name == "asia":
                size = 200
            elif name == "africa":
                size = 100
            elif name == "europe":
                size = 110
            elif name == "australia":
                size = 50
            elif name == "south_America":
                size = 100
            elif name == "north_America":
                size = 120
            else:
                size = 100

            size += size_mod  # Global size modifier for other fonts. Main font is BIPS

            draw.text((x, y),
                      info,
                      font=get_font(size),
                      fill=(255, 255, 255, 255),
                      stroke_width=15,
                      stroke_fill=(0, 0, 0, 255))

    # adding relief map
    log.step("Compositing layers")
    if map_type in ['default', 'flag']:
        log.info("Adding relief + borders overlay")
        relief = Image.open(Path(__file__).parent.parent / "./resources/maps/map_relief.png").convert("RGBA")
        relief = relief.resize(img.size)

        # Reduce relief opacity to preserve the generated region colors beneath it.
        r, g, b, a = relief.split()
        new_alpha = a.point(lambda p: int(p * 0.3))
        relief.putalpha(new_alpha)

        result = Image.alpha_composite(img, relief)

        # Restore borders above the relief, then put readable labels on top.
        borders = Image.open(
            Path(__file__).parent.parent / "./resources/maps/default_map_blured.png"
        ).convert("RGBA")

        result = Image.alpha_composite(result, borders)
        result = Image.alpha_composite(result, text_layer)

        if optional_feature == "show_most_least" and map_type != "world" and most_least is not None:
            result = Image.alpha_composite(result, most_least)

    # merging layers
    elif map_type == 'world':
        borders = Image.open(Path(__file__).parent.parent / "./resources/maps/world_map_blured.png").convert("RGBA")

        result = Image.alpha_composite(img, borders)
        result = Image.alpha_composite(result, text_layer)
    else:
        result = Image.alpha_composite(img, text_layer)


    if io_res:
        # Return an in-memory PNG for the web API instead of writing result.png.

        buffer = io.BytesIO()
        result.save(buffer, format='PNG')
        # Consumers should be able to read the PNG from its first byte.
        buffer.seek(0)

        return buffer


    out_path = Path(__file__).parent.parent / "result.png"
    log.step(f"Saving result to {out_path}")
    result.save(out_path)

    if show_result:
        result.show("Map")

    log.finished(f"Finished! Map saved as {out_path}")

    return out_path
