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

"""Lay out and draw a map legend from already resolved color/label entries.

The renderer provides RGBA colors, display strings, and a font-loading
callback. Sizes, offsets, swatches, and stroke widths are measured in image
pixels. The font is reduced until the requested entries fit the allotted
area, and rows beyond the image bottom are skipped. No entries returns None.

Edit this module for legend placement or fit rules. Number-range labels are
formatted in styling; the renderer decides which used fill rules to show.
"""

from PIL import Image, ImageDraw


def _draw_legend_overlay(
    size: tuple[int, int],
    entries: list[tuple[tuple[int, int, int, int], str]],
    get_font,
    label_fill: tuple[int, int, int, int],
    label_stroke_fill: tuple[int, int, int, int],
    label_stroke_width: int,
    map_type: str,
    has_most_least: bool,
) -> Image.Image | None:
    """Render a floating color legend in the open sea on the left side of the map."""
    if not entries:
        return None
    w, h = size
    is_world = map_type in ("world", "world_countries")
    if is_world:
        x = int(w * 0.025)
        start_y = int(h * (0.44 if has_most_least else 0.56))
        max_h = int(h * (0.34 if has_most_least else 0.40))
        max_w = int(w * 0.22)
        base_size = max(20, int(h * 0.032))
        min_size = 14
    else:
        x = 80
        start_y = 1640 if has_most_least else 1240
        max_h = max(300, 2750 - start_y)
        max_w = 560
        base_size = 86
        min_size = 32

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    count = len(entries)
    font_size = base_size
    while font_size > min_size:
        fits_height = count * int(font_size * 1.32) <= max_h
        swatch_try = max(14, int(font_size * 0.76))
        gap_try = max(10, int(font_size * 0.30))
        font_try = get_font(font_size)
        widest = max(measure.textlength(text, font=font_try) for _, text in entries)
        fits_width = swatch_try + gap_try + widest <= max_w
        if fits_height and fits_width:
            break
        font_size -= 4

    row_step = int(font_size * 1.28)
    swatch = max(14, int(font_size * 0.76))
    gap = max(10, int(font_size * 0.30))
    radius = max(4, swatch // 5)
    stroke_w = min(label_stroke_width, max(2, font_size // 5)) if label_stroke_width > 0 else 0
    outline_w = max(2, min(6, font_size // 14))

    layer = Image.new("RGBA", size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    font = get_font(font_size)

    for idx, (color, text) in enumerate(entries):
        row_y = start_y + idx * row_step
        if row_y + swatch > h - 10:
            break
        swatch_y = row_y + max(0, (font_size - swatch) // 2)
        rgba = (int(color[0]), int(color[1]), int(color[2]), 255)
        draw.rounded_rectangle(
            (x, swatch_y, x + swatch, swatch_y + swatch),
            radius=radius,
            fill=rgba,
            outline=label_stroke_fill,
            width=outline_w,
        )
        draw.text(
            (x + swatch + gap, row_y),
            text,
            font=font,
            fill=label_fill,
            stroke_width=stroke_w,
            stroke_fill=label_stroke_fill,
        )
    return layer
