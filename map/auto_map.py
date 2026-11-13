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
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .constants import europe_coords, world_coords
from .functions import ai_request, load_filling, _warning as showwarning, _error as showerror
from . import log


def _numeric_extrema(values):
    """Return region names for finite numeric extrema, or None if absent."""
    numeric = {}
    for name, value in values.items():
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

    _font_cache = {}  # Dictionary with fonts

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

    text_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(text_layer)

    # Requesting information from AI
    log.step("Requesting data from AI")
    ai_answer = ai_request(prompt, map_type, model, api_key)

    if ai_answer is None:
        showerror("Error", "No information or bad info received from AI")
        return

    log.success(f"Got information from AI ({len(ai_answer)} entries)")

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

        # filling
        if map_type == 'default' or map_type == 'world':
            for coord in points:
                if name == "south_America":
                    thresh = 15
                elif name == "asia":
                    thresh = 20
                else:
                    thresh = 30

                ImageDraw.floodfill(img, xy=coord, value=color, thresh=thresh)

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

        r, g, b, a = relief.split()
        new_alpha = a.point(lambda p: int(p * 0.3))
        relief.putalpha(new_alpha)

        result = Image.alpha_composite(img, relief)

        # Second borders layer
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
        # FOR WEB

        buffer = io.BytesIO()
        result.save(buffer, format='PNG')
        buffer.seek(0)

        return buffer


    out_path = Path(__file__).parent.parent / "result.png"
    log.step(f"Saving result to {out_path}")
    result.save(out_path)

    if show_result:
        result.show("Map")

    log.finished(f"Finished! Map saved as {out_path}")

    return out_path
