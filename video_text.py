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

import os
import re

TITLE_FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "resources", "fonts", "DIN Condensed.ttf",
)


def _title_settings(data):
    font_name = str(data.get('title_font') or 'DIN Condensed.ttf')
    font_dir = os.path.dirname(TITLE_FONT_PATH)
    # Accept bundled filenames only, rather than arbitrary client-supplied paths.
    fonts = {name for name in os.listdir(font_dir) if name.lower().endswith(('.ttf', '.otf'))}
    if font_name not in fonts:
        raise ValueError('Unknown title font.')
    raw_size = data.get('title_font_size', 100)
    # Reject booleans explicitly because int(True) would otherwise succeed.
    if isinstance(raw_size, bool):
        raise ValueError('Title size must be a number between 48 and 144.')
    try:
        size = int(raw_size)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Title size must be a number between 48 and 144.')
    if not 48 <= size <= 144:
        raise ValueError('Title size must be between 48 and 144.')
    return os.path.join(font_dir, font_name), size


def _title_colors(data):
    colors = (data.get('title_first_line_color', '#ffffff'),
              data.get('title_other_lines_color', '#ffe735'))
    if any(not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color) for color in colors):
        raise ValueError('Title colors must use #RRGGBB format.')
    return colors


def _title_image(
    title: str,
    max_width: int,
    font_path: str = TITLE_FONT_PATH,
    font_size: int = 100,
    first_line_color: str = '#ffffff',
    other_lines_color: str = '#ffe735',
    effects: bool = True,
    max_height: int = 360,
    uppercase: bool = True,
):
    """Lay out a centered, wrapped title with safe margins and a layered shadow."""
    import numpy as np
    from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

    if uppercase:
        title = title.upper()
    padding = 24
    outline = 6 if effects else 1
    text_width = max_width - padding * 2
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    # Shrink long titles to keep the map visible. Character wrapping also handles
    # URLs and words that are wider than the available space.
    for fitted_size in range(font_size, 19, -4):
        font = ImageFont.truetype(font_path, fitted_size)
        lines = []
        for paragraph in title.splitlines() or [title]:
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}".strip()
                if measure.textlength(candidate, font=font) <= text_width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                line = ""
                for char in word:
                    if line and measure.textlength(line + char, font=font) > text_width:
                        lines.append(line)
                        line = ""
                    line += char
            lines.append(line)
        text = "\n".join(lines)
        spacing = round(fitted_size * 0.22)
        bounds = measure.multiline_textbbox((0, 0), text, font=font,
                                            spacing=spacing, align="center", stroke_width=outline)
        height = bounds[3] - bounds[1]
        if height <= max_height:
            break
    if height > max_height:
        raise ValueError("Video title is too long. Please shorten it.")

    size = (max_width, height + padding * 2)
    shadow = Image.new("RGBA", size)
    # Account for the font's vertical bounding-box offset inside the padded image.
    xy = (max_width / 2, padding - bounds[1])
    # Use one line advance for shadow and colored text. Different stroke widths
    # in multiline_text would otherwise change the spacing and clip lower rows.
    advance = measure.textbbox((0, 0), 'A', font=font, stroke_width=outline)[3] + outline + spacing
    shadow_draw = ImageDraw.Draw(shadow)
    for row, line in enumerate(lines):
        shadow_draw.text((xy[0], xy[1] + row * advance + 5), line,
                         font=font, anchor='ma', fill=(0, 0, 0, 255), stroke_width=outline)
    # Combine a soft halo with a tighter drop shadow for contrast on bright footage.
    # Preview and final video both use this image, so the shading stays consistent.
    image = shadow.filter(ImageFilter.GaussianBlur(8)) if effects else Image.new('RGBA', size)
    if effects:
        image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(2)))
    draw = ImageDraw.Draw(image)
    first_rgb = ImageColor.getrgb(first_line_color)
    other_rgb = ImageColor.getrgb(other_lines_color)
    for row, line in enumerate(lines):
        color = first_rgb if row == 0 else other_rgb
        draw.text((xy[0], xy[1] + row * advance), line, font=font, anchor='ma',
                  fill=color + (255,), stroke_width=outline,
                  stroke_fill=(0, 0, 0, 255) if effects else color + (220,))
    return np.array(image)


def _build_title_clip(
    title: str,
    duration: float,
    max_width: int,
    font_path: str = TITLE_FONT_PATH,
    font_size: int = 100,
    first_line_color: str = '#ffffff',
    other_lines_color: str = '#ffe735',
    effects: bool = True,
    uppercase: bool = True,
):
    from moviepy import ImageClip

    return ImageClip(
        _title_image(
            title,
            max_width,
            font_path,
            font_size,
            first_line_color,
            other_lines_color,
            effects,
            uppercase=uppercase,
        )
    ).with_duration(duration)


def _watermark_settings(data):
    text = data.get('watermark_text', '')
    color = data.get('watermark_color', '#a4e9ee')
    if not isinstance(text, str) or len(text) > 80:
        raise ValueError('Watermark text must be at most 80 characters.')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Watermark color must use #RRGGBB format.')
    return text.strip(), color


def _watermark_font_path(data):
    # Validate the watermark's own font against the bundled filenames.
    return _title_settings({'title_font': data.get('watermark_font', 'DIN Condensed.ttf')})[0]


def _round_watermark(image, diameter=230):
    """Center-crop the logo to a circle while preserving existing transparency."""
    from PIL import Image, ImageChops, ImageDraw, ImageOps

    image = ImageOps.fit(image.convert('RGBA'), (diameter, diameter), method=Image.Resampling.LANCZOS)
    # Draw the mask at higher resolution for a smooth circular edge.
    mask = Image.new('L', (diameter * 4, diameter * 4))
    ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
    mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    image.putalpha(ImageChops.multiply(image.getchannel('A'), mask))
    return image


def _with_shadow(image):
    """Add a soft silhouette shadow while keeping transparent areas transparent."""
    from PIL import Image, ImageFilter

    image = image.convert('RGBA')
    canvas = Image.new('RGBA', (image.width + 48, image.height + 48))
    alpha = Image.new('L', canvas.size)
    alpha.paste(image.getchannel('A'), (24, 32))
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(10)).point(lambda p: round(p * .8)))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(image, (24, 24))
    return canvas

