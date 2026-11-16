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

import io
import os
import re

from video_text import TITLE_FONT_PATH, _with_shadow

STORAGE_DIR = "/tmp/automap_storage"

from sticker_themes import (
    ALLOWED_STICKERS,
    TWEMOJI_CODEPOINTS,
    AUTO_STICKER_THEMES as _AUTO_STICKER_THEMES,
    EXACT_WORD_KEYWORDS as _EXACT_WORD_KEYWORDS,
)


def _resolve_auto_stickers(title: str) -> tuple[str, str]:
    """Choose a thematic (left, right) sticker pair based on English title keywords."""
    lowered = (title or '').lower()
    if not lowered.strip():
        return ('globe', 'pin')
    best_pair: tuple[str, str] | None = None
    best_count = 0
    # Score each theme by distinct keyword hits; ties keep the earlier (higher-priority) theme.
    for keywords, pair in _AUTO_STICKER_THEMES:
        count = 0
        for kw in keywords:
            if kw in _EXACT_WORD_KEYWORDS:
                pattern = rf'(?<!\w){re.escape(kw)}(?!\w)'
            elif kw == 'war':
                pattern = r'(?<!\w)war(?:s|fare|rior|riors)?(?!\w)'
            elif kw == 'king':
                pattern = r'(?<!\w)king(?:s|dom|doms)?(?!\w)'
            else:
                pattern = rf'(?<!\w){re.escape(kw)}'
            if re.search(pattern, lowered):
                count += 1
        if count > best_count:
            best_count = count
            best_pair = pair
    return best_pair if best_pair is not None else ('globe', 'pin')


def _star_polygon(cx: float, cy: float, outer_r: float, inner_r: float) -> list[tuple[float, float]]:
    """Return the 10 vertices of a 5-pointed star centered at (cx, cy)."""
    import math

    points: list[tuple[float, float]] = []
    for i in range(10):
        angle = -math.pi / 2 + i * (math.pi / 5)
        r = outer_r if i % 2 == 0 else inner_r
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def _draw_heart(draw, cx: float, cy: float, size: float, fill: tuple[int, int, int, int],
                outline: tuple[int, int, int, int] = (0, 0, 0, 255), width: int = 4) -> None:
    """Draw a smooth vector heart icon centered at (cx, cy)."""
    import math

    pts: list[tuple[float, float]] = []
    for step in range(64):
        t = (step / 64.0) * 2.0 * math.pi
        x = 16.0 * (math.sin(t) ** 3)
        y = -(13.0 * math.cos(t) - 5.0 * math.cos(2 * t) - 2.0 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * (size / 34.0), cy + y * (size / 34.0)))
    draw.polygon(pts, fill=fill, outline=outline, width=width)


def _subscribe_badge_image(
    text: str = 'Subscribe!',
    font_path: str = TITLE_FONT_PATH,
    effects: bool = True,
):
    """Render the dark rounded '[ ❤️ Subscribe! ▶️ ]' call-to-action pill badge."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    label = (text or 'Subscribe!').strip()[:40] or 'Subscribe!'
    scale = 2
    font_size = 52 * scale
    font = ImageFont.truetype(font_path, font_size)
    measure = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    bbox = measure.textbbox((0, 0), label, font=font, stroke_width=3 * scale)
    text_w = bbox[2] - bbox[0]

    heart_slot = 64 * scale
    play_w, play_h = 68 * scale, 48 * scale
    pad_x = 28 * scale
    gap = 18 * scale
    pill_h = 84 * scale
    pill_w = pad_x * 2 + heart_slot + gap * 2 + text_w + play_w

    canvas = Image.new('RGBA', (pill_w + 16 * scale, pill_h + 16 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    ox, oy = 8 * scale, 8 * scale

    # Rounded dark charcoal pill with subtle glossy border
    draw.rounded_rectangle(
        (ox, oy, ox + pill_w, oy + pill_h),
        radius=26 * scale,
        fill=(28, 30, 36, 228),
        outline=(255, 255, 255, 58),
        width=2 * scale,
    )

    # Left icon: glossy red heart
    heart_cx = ox + pad_x + heart_slot / 2
    heart_cy = oy + pill_h / 2 + 2 * scale
    _draw_heart(
        draw,
        heart_cx,
        heart_cy,
        54 * scale,
        fill=(255, 42, 75, 255),
        outline=(15, 15, 20, 255),
        width=3 * scale,
    )
    # Specular highlight on top-left lobe of heart
    draw.ellipse(
        (heart_cx - 16 * scale, heart_cy - 16 * scale, heart_cx - 6 * scale, heart_cy - 7 * scale),
        fill=(255, 190, 200, 190),
    )

    # Center text: bold white Subscribe! text with dark stroke
    text_x = ox + pad_x + heart_slot + gap + text_w / 2
    text_y = oy + pill_h / 2
    draw.text(
        (text_x, text_y),
        label,
        font=font,
        anchor='mm',
        fill=(255, 255, 255, 255),
        stroke_width=3 * scale,
        stroke_fill=(0, 0, 0, 240),
    )

    # Right icon: red YouTube-style rounded play button
    play_x0 = ox + pad_x + heart_slot + gap * 2 + text_w
    play_y0 = oy + (pill_h - play_h) / 2
    draw.rounded_rectangle(
        (play_x0, play_y0, play_x0 + play_w, play_y0 + play_h),
        radius=12 * scale,
        fill=(255, 32, 32, 255),
        outline=(15, 15, 20, 255),
        width=3 * scale,
    )
    tri_cx = play_x0 + play_w / 2 + 2 * scale
    tri_cy = play_y0 + play_h / 2
    tri = [
        (tri_cx - 11 * scale, tri_cy - 12 * scale),
        (tri_cx - 11 * scale, tri_cy + 12 * scale),
        (tri_cx + 13 * scale, tri_cy),
    ]
    draw.polygon(tri, fill=(255, 255, 255, 255))

    final_img = canvas.resize((canvas.width // scale, canvas.height // scale), Image.Resampling.LANCZOS)
    if effects:
        final_img = _with_shadow(final_img)
    return np.array(final_img)


def _stars_row_image(color: str = '#ffffff', effects: bool = True):
    """Render three 5-pointed stars ('★ ★ ★') for the bottom watermark footer."""
    import numpy as np
    from PIL import Image, ImageColor, ImageDraw

    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        color = '#ffffff'
    rgb = ImageColor.getrgb(color)

    scale = 2
    w, h = 320 * scale, 76 * scale
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    centers_x = (w * 0.22, w * 0.50, w * 0.78)
    cy = h * 0.52
    for cx in centers_x:
        pts = _star_polygon(cx, cy, outer_r=26 * scale, inner_r=11 * scale)
        draw.polygon(
            pts,
            fill=rgb + (255,),
            outline=(0, 0, 0, 240) if effects else rgb + (220,),
            width=4 * scale if effects else 2 * scale,
        )

    final_img = canvas.resize((w // scale, h // scale), Image.Resampling.LANCZOS)
    if effects:
        final_img = _with_shadow(final_img)
    return np.array(final_img)


def _parse_sticker_spec(raw_kind: str, title: str = '', side: str = 'left') -> tuple[str | None, str]:
    """Parse a sticker specification into ``(gen_style_or_none, base_kind)``."""
    raw = (raw_kind or 'none').strip().lower()
    gen_style: str | None = None
    if ':' in raw:
        prefix, _, base_kind = raw.partition(':')
        if prefix in ('gen', 'gen_diecut', 'gen_neon', 'gen_stamp'):
            gen_style = 'diecut' if prefix == 'gen' else prefix.removeprefix('gen_')
            raw = base_kind or 'auto'
    if raw == 'auto':
        left_auto, right_auto = _resolve_auto_stickers(title)
        raw = right_auto if side == 'right' else left_auto
    return gen_style, raw


def _style_generated_sticker(icon_canvas, gen_style: str, size: int):
    """Wrap a procedural vector icon in a generated sticker style (die-cut, neon, or stamp)."""
    from PIL import Image, ImageDraw, ImageFilter

    s = icon_canvas.width
    if gen_style == 'diecut':
        # Create a crisp white vinyl die-cut sticker border around the vector silhouette.
        alpha = icon_canvas.getchannel('A')
        dilate = max(3, int(round(s * 0.055))) | 1
        border_mask = alpha.filter(ImageFilter.MaxFilter(dilate))
        vinyl = Image.new('RGBA', (s, s), (255, 255, 255, 0))
        vinyl.putalpha(border_mask)
        white_layer = Image.new('RGBA', (s, s), (252, 253, 255, 255))
        white_layer.putalpha(border_mask)
        styled = Image.alpha_composite(white_layer, icon_canvas)
        styled = styled.rotate(6, resample=Image.Resampling.BICUBIC, expand=False)
        return styled.resize((size, size), Image.Resampling.LANCZOS)

    if gen_style == 'neon':
        badge = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        glow = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        pad = int(s * 0.08)
        gdraw.ellipse((pad, pad, s - pad, s - pad), outline=(56, 189, 248, 230), width=int(s * 0.055))
        glow = glow.filter(ImageFilter.GaussianBlur(max(4, s // 28)))
        bdraw = ImageDraw.Draw(badge)
        bdraw.ellipse((pad, pad, s - pad, s - pad), fill=(15, 23, 42, 238), outline=(99, 102, 241, 255), width=int(s * 0.032))
        bdraw.ellipse((pad + 6, pad + 6, s - pad - 6, s - pad - 6), outline=(56, 189, 248, 210), width=max(2, s // 70))
        badge = Image.alpha_composite(glow, badge)
        inner_size = int(s * 0.68)
        icon_small = icon_canvas.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
        offset = (s - inner_size) // 2
        badge.alpha_composite(icon_small, (offset, offset))
        return badge.resize((size, size), Image.Resampling.LANCZOS)

    # 'stamp': tilted editorial badge with double accent frame and corner sparkles
    stamp = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stamp)
    pad = int(s * 0.08)
    sdraw.rounded_rectangle((pad, pad, s - pad, s - pad), radius=int(s * 0.22),
                            fill=(30, 27, 75, 235), outline=(251, 191, 36, 255), width=int(s * 0.03))
    sdraw.rounded_rectangle((pad + 10, pad + 10, s - pad - 10, s - pad - 10), radius=int(s * 0.17),
                            outline=(255, 255, 255, 110), width=max(2, s // 80))
    # Tiny decorative sparkle stars in top corners
    for sx, sy in ((s * 0.22, s * 0.22), (s * 0.78, s * 0.22)):
        sdraw.polygon(_star_polygon(sx, sy, s * 0.055, s * 0.022), fill=(253, 224, 71, 240))
    inner_size = int(s * 0.68)
    icon_small = icon_canvas.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
    offset = (s - inner_size) // 2
    stamp.alpha_composite(icon_small, (offset, offset + int(s * 0.03)))
    stamp = stamp.rotate(-6, resample=Image.Resampling.BICUBIC, expand=False)
    return stamp.resize((size, size), Image.Resampling.LANCZOS)


from extra_stickers import draw_extra_sticker


def _draw_extra_sticker(kind, draw, s, outline, ow):
    """Delegate extended procedural sticker drawing to extra_stickers.py."""
    return draw_extra_sticker(kind, draw, s, outline, ow, star_polygon_fn=_star_polygon)


def _sticker_image(kind: str, size: int = 140, effects: bool = True, allow_remote: bool = False):
    """Render a decorative top-corner sticker (library Twemoji or procedural generated style)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageOps

    gen_style, kind = _parse_sticker_spec(kind)
    if kind not in ALLOWED_STICKERS or kind == 'none':
        return None

    # Library stickers (without a gen_ prefix) use Twemoji cache/CDN when available.
    codepoint = None if gen_style else TWEMOJI_CODEPOINTS.get(kind)
    if codepoint:
        stickers_dir = os.path.join(STORAGE_DIR, 'stickers')
        cache_path = os.path.join(stickers_dir, f'{kind}.png')
        if not os.path.isfile(cache_path) and allow_remote:
            try:
                import requests
                os.makedirs(stickers_dir, exist_ok=True)
                url = f'https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{codepoint}.png'
                resp = requests.get(url, timeout=1.5)
                if resp.status_code == 200 and resp.content:
                    with Image.open(io.BytesIO(resp.content)) as fetched:
                        fetched.convert('RGBA').save(cache_path, format='PNG')
            except Exception:
                pass
        if os.path.isfile(cache_path):
            try:
                with Image.open(cache_path) as cached:
                    final_img = ImageOps.contain(cached.convert('RGBA'), (size, size), method=Image.Resampling.LANCZOS)
                    if final_img.size != (size, size):
                        padded = Image.new('RGBA', (size, size), (0, 0, 0, 0))
                        padded.paste(final_img, ((size - final_img.width) // 2, (size - final_img.height) // 2))
                        final_img = padded
                    if effects:
                        final_img = _with_shadow(final_img)
                    return np.array(final_img)
            except Exception:
                pass

    scale = 2
    s = size * scale
    canvas = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    outline = (18, 18, 24, 255)
    ow = 4 * scale

    if _draw_extra_sticker(kind, draw, s, outline, ow):
        pass
    elif kind == 'santa':
        # Fluffy white beard base
        draw.ellipse((s * 0.16, s * 0.44, s * 0.84, s * 0.94), fill=(250, 252, 255, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.12, s * 0.52, s * 0.46, s * 0.84), fill=(250, 252, 255, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.54, s * 0.52, s * 0.88, s * 0.84), fill=(250, 252, 255, 255), outline=outline, width=ow)
        # Warm face
        draw.ellipse((s * 0.24, s * 0.30, s * 0.76, s * 0.72), fill=(255, 214, 186, 255), outline=outline, width=ow)
        # Re-fill inner beard over lower face
        draw.chord((s * 0.18, s * 0.42, s * 0.82, s * 0.92), start=0, end=180, fill=(250, 252, 255, 255), outline=outline, width=ow)
        # Rosy cheeks & eyes
        draw.ellipse((s * 0.28, s * 0.48, s * 0.39, s * 0.56), fill=(255, 138, 138, 210))
        draw.ellipse((s * 0.61, s * 0.48, s * 0.72, s * 0.56), fill=(255, 138, 138, 210))
        draw.ellipse((s * 0.35, s * 0.41, s * 0.42, s * 0.48), fill=outline)
        draw.ellipse((s * 0.58, s * 0.41, s * 0.65, s * 0.48), fill=outline)
        # Mustache & rosy nose
        draw.ellipse((s * 0.30, s * 0.53, s * 0.52, s * 0.65), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.48, s * 0.53, s * 0.70, s * 0.65), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.44, s * 0.47, s * 0.56, s * 0.57), fill=(244, 92, 92, 255), outline=outline, width=ow // 2)
        # Red Santa hat & white fur brim + pom-pom
        hat_pts = [(s * 0.18, s * 0.34), (s * 0.52, s * 0.06), (s * 0.86, s * 0.28), (s * 0.80, s * 0.36)]
        draw.polygon(hat_pts, fill=(228, 34, 46, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.15, s * 0.27, s * 0.83, s * 0.40), radius=14 * scale, fill=(252, 253, 255, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.76, s * 0.18, s * 0.94, s * 0.36), fill=(252, 253, 255, 255), outline=outline, width=ow)

    elif kind == 'gift':
        # Candy cane behind top-right of gift box
        draw.arc((s * 0.52, s * 0.06, s * 0.90, s * 0.46), start=180, end=20, fill=(235, 38, 50, 255), width=12 * scale)
        # Golden ribbon bow Loops on top
        draw.ellipse((s * 0.24, s * 0.14, s * 0.52, s * 0.38), fill=(255, 204, 42, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.48, s * 0.14, s * 0.76, s * 0.38), fill=(255, 204, 42, 255), outline=outline, width=ow)
        # Red gift box body
        draw.rounded_rectangle((s * 0.18, s * 0.36, s * 0.82, s * 0.90), radius=12 * scale, fill=(224, 36, 48, 255), outline=outline, width=ow)
        # Gift box lid
        draw.rounded_rectangle((s * 0.14, s * 0.28, s * 0.86, s * 0.44), radius=10 * scale, fill=(242, 52, 64, 255), outline=outline, width=ow)
        # Vertical & horizontal gold ribbon stripes
        draw.rectangle((s * 0.44, s * 0.28, s * 0.56, s * 0.90), fill=(255, 208, 46, 255), outline=outline, width=ow // 2)
        draw.rectangle((s * 0.18, s * 0.58, s * 0.82, s * 0.69), fill=(255, 208, 46, 255), outline=outline, width=ow // 2)
        # Holly leaves & berry accent
        draw.ellipse((s * 0.64, s * 0.20, s * 0.88, s * 0.34), fill=(40, 184, 86, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.44, s * 0.22, s * 0.56, s * 0.34), fill=(255, 230, 90, 255), outline=outline, width=ow // 2)

    elif kind == 'snowflake':
        import math
        draw.ellipse((s * 0.14, s * 0.14, s * 0.86, s * 0.86), fill=(48, 168, 255, 255), outline=outline, width=ow)
        cx, cy = s * 0.50, s * 0.50
        for i in range(6):
            ang = i * (math.pi / 3)
            x2 = cx + s * 0.30 * math.cos(ang)
            y2 = cy + s * 0.30 * math.sin(ang)
            draw.line((cx, cy, x2, y2), fill=(255, 255, 255, 255), width=6 * scale)
            bx = cx + s * 0.19 * math.cos(ang)
            by = cy + s * 0.19 * math.sin(ang)
            for delta in (-0.55, 0.55):
                tx = bx + s * 0.09 * math.cos(ang + delta)
                ty = by + s * 0.09 * math.sin(ang + delta)
                draw.line((bx, by, tx, ty), fill=(255, 255, 255, 255), width=4 * scale)
        draw.ellipse((s * 0.42, s * 0.42, s * 0.58, s * 0.58), fill=(225, 248, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'pumpkin':
        # Stem
        draw.polygon([(s * 0.45, s * 0.10), (s * 0.57, s * 0.10), (s * 0.54, s * 0.28), (s * 0.46, s * 0.28)],
                     fill=(46, 164, 72, 255), outline=outline, width=ow)
        # Ribbed orange pumpkin lobes
        draw.ellipse((s * 0.12, s * 0.24, s * 0.58, s * 0.88), fill=(242, 116, 28, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.42, s * 0.24, s * 0.88, s * 0.88), fill=(242, 116, 28, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.26, s * 0.22, s * 0.74, s * 0.90), fill=(255, 138, 36, 255), outline=outline, width=ow)
        # Glowing jack-o'-lantern eyes & grin
        draw.polygon([(s * 0.30, s * 0.48), (s * 0.40, s * 0.36), (s * 0.45, s * 0.48)], fill=(255, 232, 64, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.55, s * 0.48), (s * 0.60, s * 0.36), (s * 0.70, s * 0.48)], fill=(255, 232, 64, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.28, s * 0.64), (s * 0.50, s * 0.72), (s * 0.72, s * 0.64), (s * 0.64, s * 0.78), (s * 0.36, s * 0.78)],
                     fill=(255, 232, 64, 255), outline=outline, width=ow // 2)

    elif kind == 'globe':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(36, 146, 245, 255), outline=outline, width=ow)
        # Stylized green continents
        draw.polygon([(s * 0.26, s * 0.30), (s * 0.46, s * 0.24), (s * 0.48, s * 0.46), (s * 0.34, s * 0.66), (s * 0.22, s * 0.50)], fill=(58, 204, 112, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.56, s * 0.28), (s * 0.78, s * 0.36), (s * 0.74, s * 0.68), (s * 0.54, s * 0.58)], fill=(58, 204, 112, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), outline=outline, width=ow)

    elif kind == 'compass':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(245, 194, 48, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.20, s * 0.20, s * 0.80, s * 0.80), fill=(30, 42, 62, 255), outline=outline, width=ow // 2)
        # North red pointer & South white pointer
        draw.polygon([(s * 0.50, s * 0.24), (s * 0.60, s * 0.50), (s * 0.40, s * 0.50)], fill=(244, 54, 68, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.50, s * 0.76), (s * 0.60, s * 0.50), (s * 0.40, s * 0.50)], fill=(245, 248, 255, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.45, s * 0.45, s * 0.55, s * 0.55), fill=(255, 214, 64, 255), outline=outline, width=ow // 2)

    elif kind == 'pin':
        draw.polygon([(s * 0.24, s * 0.46), (s * 0.50, s * 0.90), (s * 0.76, s * 0.46)], fill=(238, 42, 62, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.20, s * 0.12, s * 0.80, s * 0.68), fill=(238, 42, 62, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.36, s * 0.26, s * 0.64, s * 0.54), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'plane':
        # Sky badge + white/blue jet plane
        draw.ellipse((s * 0.14, s * 0.14, s * 0.86, s * 0.86), fill=(46, 156, 255, 255), outline=outline, width=ow)
        wings = [(s * 0.18, s * 0.56), (s * 0.82, s * 0.24), (s * 0.64, s * 0.82), (s * 0.48, s * 0.58), (s * 0.32, s * 0.68)]
        draw.polygon(wings, fill=(250, 252, 255, 255), outline=outline, width=ow)

    elif kind == 'money':
        # Tie top + plump golden money sack
        draw.polygon([(s * 0.36, s * 0.14), (s * 0.64, s * 0.14), (s * 0.58, s * 0.30), (s * 0.42, s * 0.30)],
                     fill=(238, 188, 52, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.18, s * 0.26, s * 0.82, s * 0.90), fill=(246, 198, 58, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.34, s * 0.44, s * 0.66, s * 0.76), fill=(46, 192, 96, 255), outline=outline, width=ow // 2)

    elif kind == 'chart':
        draw.rounded_rectangle((s * 0.12, s * 0.14, s * 0.88, s * 0.88), radius=14 * scale, fill=(245, 248, 255, 255), outline=outline, width=ow)
        draw.rectangle((s * 0.22, s * 0.56, s * 0.36, s * 0.80), fill=(52, 152, 245, 255), outline=outline, width=ow // 2)
        draw.rectangle((s * 0.43, s * 0.40, s * 0.57, s * 0.80), fill=(46, 204, 113, 255), outline=outline, width=ow // 2)
        draw.rectangle((s * 0.64, s * 0.24, s * 0.78, s * 0.80), fill=(255, 196, 42, 255), outline=outline, width=ow // 2)

    elif kind == 'diamond':
        gem = [(s * 0.28, s * 0.18), (s * 0.72, s * 0.18), (s * 0.90, s * 0.40), (s * 0.50, s * 0.88), (s * 0.10, s * 0.40)]
        draw.polygon(gem, fill=(56, 214, 255, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.28, s * 0.18), (s * 0.72, s * 0.18), (s * 0.64, s * 0.40), (s * 0.36, s * 0.40)],
                     fill=(165, 242, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'shield':
        pts = [(s * 0.18, s * 0.14), (s * 0.82, s * 0.14), (s * 0.82, s * 0.50), (s * 0.50, s * 0.90), (s * 0.18, s * 0.50)]
        draw.polygon(pts, fill=(42, 118, 238, 255), outline=outline, width=ow)
        inner = [(s * 0.28, s * 0.22), (s * 0.72, s * 0.22), (s * 0.72, s * 0.48), (s * 0.50, s * 0.78), (s * 0.28, s * 0.48)]
        draw.polygon(inner, fill=(255, 204, 46, 255), outline=outline, width=ow // 2)

    elif kind == 'swords':
        # Crossed silver blades with gold guards
        draw.polygon([(s * 0.16, s * 0.22), (s * 0.24, s * 0.14), (s * 0.84, s * 0.74), (s * 0.76, s * 0.82)],
                     fill=(224, 232, 245, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.84, s * 0.22), (s * 0.76, s * 0.14), (s * 0.16, s * 0.74), (s * 0.24, s * 0.82)],
                     fill=(224, 232, 245, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.40, s * 0.40, s * 0.60, s * 0.60), fill=(255, 204, 46, 255), outline=outline, width=ow)

    elif kind == 'rocket':
        # Exhaust flame
        draw.polygon([(s * 0.38, s * 0.72), (s * 0.50, s * 0.94), (s * 0.62, s * 0.72)], fill=(255, 112, 34, 255), outline=outline, width=ow // 2)
        # Side fins
        draw.polygon([(s * 0.20, s * 0.74), (s * 0.34, s * 0.52), (s * 0.36, s * 0.74)], fill=(235, 44, 58, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.80, s * 0.74), (s * 0.66, s * 0.52), (s * 0.64, s * 0.74)], fill=(235, 44, 58, 255), outline=outline, width=ow)
        # Rocket fuselage & nose cone
        draw.polygon([(s * 0.50, s * 0.08), (s * 0.68, s * 0.34), (s * 0.66, s * 0.74), (s * 0.34, s * 0.74), (s * 0.32, s * 0.34)],
                     fill=(248, 250, 255, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.08), (s * 0.66, s * 0.30), (s * 0.34, s * 0.30)],
                     fill=(235, 44, 58, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.40, s * 0.38, s * 0.60, s * 0.58), fill=(58, 198, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'bolt':
        bolt_pts = [
            (s * 0.56, s * 0.08),
            (s * 0.22, s * 0.54),
            (s * 0.48, s * 0.54),
            (s * 0.38, s * 0.92),
            (s * 0.78, s * 0.44),
            (s * 0.52, s * 0.44),
        ]
        draw.polygon(bolt_pts, fill=(255, 214, 36, 255), outline=outline, width=ow)

    elif kind == 'fire':
        outer_flame = [(s * 0.50, s * 0.08), (s * 0.82, s * 0.48), (s * 0.74, s * 0.84), (s * 0.50, s * 0.92), (s * 0.26, s * 0.84), (s * 0.18, s * 0.48)]
        inner_flame = [(s * 0.50, s * 0.32), (s * 0.68, s * 0.58), (s * 0.62, s * 0.82), (s * 0.50, s * 0.88), (s * 0.38, s * 0.82), (s * 0.32, s * 0.58)]
        draw.polygon(outer_flame, fill=(255, 86, 34, 255), outline=outline, width=ow)
        draw.polygon(inner_flame, fill=(255, 214, 46, 255), outline=outline, width=ow // 2)

    elif kind == 'trophy':
        # Handles
        draw.ellipse((s * 0.12, s * 0.22, s * 0.40, s * 0.52), fill=(255, 204, 38, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.60, s * 0.22, s * 0.88, s * 0.52), fill=(255, 204, 38, 255), outline=outline, width=ow)
        # Cup body + stem + base
        draw.polygon([(s * 0.24, s * 0.16), (s * 0.76, s * 0.16), (s * 0.66, s * 0.58), (s * 0.34, s * 0.58)],
                     fill=(255, 214, 46, 255), outline=outline, width=ow)
        draw.rectangle((s * 0.44, s * 0.56, s * 0.56, s * 0.76), fill=(235, 180, 28, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.28, s * 0.74, s * 0.72, s * 0.88), radius=8 * scale, fill=(215, 152, 24, 255), outline=outline, width=ow)

    elif kind == 'crown':
        crown_pts = [
            (s * 0.14, s * 0.78),
            (s * 0.10, s * 0.28),
            (s * 0.34, s * 0.52),
            (s * 0.50, s * 0.18),
            (s * 0.66, s * 0.52),
            (s * 0.90, s * 0.28),
            (s * 0.86, s * 0.78),
        ]
        draw.polygon(crown_pts, fill=(255, 204, 38, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.14, s * 0.74, s * 0.86, s * 0.88), radius=8 * scale, fill=(235, 168, 20, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.45, s * 0.56, s * 0.55, s * 0.66), fill=(244, 42, 75, 255), outline=outline, width=ow // 2)

    elif kind == 'star':
        pts = _star_polygon(s * 0.50, s * 0.52, outer_r=s * 0.40, inner_r=s * 0.18)
        draw.polygon(pts, fill=(255, 216, 46, 255), outline=outline, width=ow)

    elif kind == 'heart':
        _draw_heart(draw, s * 0.50, s * 0.52, s * 0.78, fill=(255, 42, 78, 255), outline=outline, width=ow)

    elif kind == 'skull':
        draw.rounded_rectangle((s * 0.32, s * 0.56, s * 0.68, s * 0.88), radius=10 * scale, fill=(245, 247, 252, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.18, s * 0.14, s * 0.82, s * 0.74), fill=(245, 247, 252, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.28, s * 0.36, s * 0.44, s * 0.52), fill=outline)
        draw.ellipse((s * 0.56, s * 0.36, s * 0.72, s * 0.52), fill=outline)
        draw.polygon([(s * 0.50, s * 0.50), (s * 0.45, s * 0.60), (s * 0.55, s * 0.60)], fill=outline)

    elif kind == 'bear':
        # Round ears + warm inner ears
        draw.ellipse((s * 0.12, s * 0.12, s * 0.42, s * 0.42), fill=(142, 82, 42, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.58, s * 0.12, s * 0.88, s * 0.42), fill=(142, 82, 42, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.19, s * 0.19, s * 0.35, s * 0.35), fill=(216, 158, 108, 255))
        draw.ellipse((s * 0.65, s * 0.19, s * 0.81, s * 0.35), fill=(216, 158, 108, 255))
        # Plump brown bear head
        draw.ellipse((s * 0.16, s * 0.22, s * 0.84, s * 0.88), fill=(158, 94, 48, 255), outline=outline, width=ow)
        # Light tan muzzle + dark nose
        draw.ellipse((s * 0.32, s * 0.48, s * 0.68, s * 0.78), fill=(234, 192, 146, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.42, s * 0.50, s * 0.58, s * 0.62), fill=outline)
        # Eyes
        draw.ellipse((s * 0.31, s * 0.38, s * 0.39, s * 0.46), fill=outline)
        draw.ellipse((s * 0.61, s * 0.38, s * 0.69, s * 0.46), fill=outline)

    elif kind == 'paw':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(252, 211, 77, 255), outline=outline, width=ow)
        pad_color = (120, 53, 15, 255)
        draw.ellipse((s * 0.30, s * 0.48, s * 0.70, s * 0.78), fill=pad_color, outline=outline, width=ow // 2)
        for px, py in ((0.24, 0.34), (0.39, 0.24), (0.61, 0.24), (0.76, 0.34)):
            draw.ellipse((s * (px - 0.08), s * (py - 0.09), s * (px + 0.08), s * (py + 0.09)), fill=pad_color, outline=outline, width=ow // 2)

    elif kind == 'tree':
        draw.rectangle((s * 0.42, s * 0.68, s * 0.58, s * 0.90), fill=(132, 78, 42, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.34), (s * 0.86, s * 0.74), (s * 0.14, s * 0.74)], fill=(22, 163, 74, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.20), (s * 0.80, s * 0.56), (s * 0.20, s * 0.56)], fill=(34, 197, 94, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.08), (s * 0.72, s * 0.38), (s * 0.28, s * 0.38)], fill=(74, 222, 128, 255), outline=outline, width=ow)

    elif kind == 'wolf':
        draw.polygon([(s * 0.18, s * 0.42), (s * 0.22, s * 0.10), (s * 0.42, s * 0.28)], fill=(100, 116, 139, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.82, s * 0.42), (s * 0.78, s * 0.10), (s * 0.58, s * 0.28)], fill=(100, 116, 139, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.16, s * 0.38), (s * 0.50, s * 0.22), (s * 0.84, s * 0.38), (s * 0.76, s * 0.68), (s * 0.50, s * 0.88), (s * 0.24, s * 0.68)],
                     fill=(148, 163, 184, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.34, s * 0.54), (s * 0.50, s * 0.88), (s * 0.66, s * 0.54)], fill=(241, 245, 249, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.32, s * 0.42, s * 0.42, s * 0.50), fill=(250, 204, 21, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.58, s * 0.42, s * 0.68, s * 0.50), fill=(250, 204, 21, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.44, s * 0.74, s * 0.56, s * 0.84), fill=outline)

    elif kind == 'beer':
        draw.rounded_rectangle((s * 0.62, s * 0.38, s * 0.86, s * 0.76), radius=12 * scale, outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.22, s * 0.28, s * 0.70, s * 0.88), radius=10 * scale, fill=(245, 158, 11, 255), outline=outline, width=ow)
        for fx in (0.28, 0.44, 0.60):
            draw.ellipse((s * (fx - 0.12), s * 0.14, s * (fx + 0.12), s * 0.38), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'coffee':
        draw.rounded_rectangle((s * 0.64, s * 0.42, s * 0.86, s * 0.70), radius=12 * scale, outline=outline, width=ow)
        draw.chord((s * 0.18, s * 0.28, s * 0.74, s * 0.84), start=0, end=180, fill=(248, 250, 252, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.18, s * 0.32, s * 0.74, s * 0.48), fill=(120, 53, 15, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.12, s * 0.78, s * 0.82, s * 0.88), radius=6 * scale, fill=(226, 232, 240, 255), outline=outline, width=ow)

    elif kind == 'wine':
        draw.polygon([(s * 0.28, s * 0.14), (s * 0.72, s * 0.14), (s * 0.64, s * 0.54), (s * 0.36, s * 0.54)], fill=(225, 29, 72, 255), outline=outline, width=ow)
        draw.rectangle((s * 0.47, s * 0.54, s * 0.53, s * 0.82), fill=(226, 232, 240, 255), outline=outline, width=ow // 2)
        draw.rounded_rectangle((s * 0.32, s * 0.80, s * 0.68, s * 0.88), radius=6 * scale, fill=(226, 232, 240, 255), outline=outline, width=ow)

    elif kind == 'pizza':
        draw.polygon([(s * 0.50, s * 0.88), (s * 0.16, s * 0.22), (s * 0.84, s * 0.22)], fill=(250, 204, 21, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.14, s * 0.14, s * 0.86, s * 0.28), radius=10 * scale, fill=(217, 119, 6, 255), outline=outline, width=ow)
        for px, py in ((0.40, 0.40), (0.58, 0.44), (0.48, 0.62)):
            draw.ellipse((s * (px - 0.07), s * (py - 0.07), s * (px + 0.07), s * (py + 0.07)), fill=(220, 38, 38, 255), outline=outline, width=ow // 2)

    elif kind == 'soccer':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(248, 250, 252, 255), outline=outline, width=ow)
        pent = _star_polygon(s * 0.50, s * 0.50, outer_r=s * 0.18, inner_r=s * 0.15)
        draw.polygon(pent, fill=outline)
        for vx, vy in pent[::2]:
            draw.line((vx, vy, s * 0.50 + (vx - s * 0.50) * 2.0, s * 0.50 + (vy - s * 0.50) * 2.0), fill=outline, width=ow // 2)

    elif kind == 'car':
        draw.polygon([(s * 0.24, s * 0.48), (s * 0.36, s * 0.28), (s * 0.66, s * 0.28), (s * 0.78, s * 0.48)], fill=(56, 189, 248, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.12, s * 0.46, s * 0.88, s * 0.74), radius=12 * scale, fill=(239, 68, 68, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.20, s * 0.62, s * 0.38, s * 0.80), fill=(30, 41, 59, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.62, s * 0.62, s * 0.80, s * 0.80), fill=(30, 41, 59, 255), outline=outline, width=ow)

    elif kind == 'brain':
        for bx in (0.36, 0.64):
            draw.ellipse((s * (bx - 0.22), s * 0.18, s * (bx + 0.22), s * 0.82), fill=(244, 114, 182, 255), outline=outline, width=ow)
        draw.line((s * 0.50, s * 0.18, s * 0.50, s * 0.82), fill=outline, width=ow)

    elif kind == 'book':
        draw.rounded_rectangle((s * 0.18, s * 0.58, s * 0.84, s * 0.82), radius=8 * scale, fill=(59, 130, 246, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.16, s * 0.38, s * 0.82, s * 0.62), radius=8 * scale, fill=(16, 185, 129, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.20, s * 0.18, s * 0.86, s * 0.42), radius=8 * scale, fill=(244, 63, 94, 255), outline=outline, width=ow)

    elif kind == 'sun':
        pts = _star_polygon(s * 0.50, s * 0.50, outer_r=s * 0.42, inner_r=s * 0.28)
        draw.polygon(pts, fill=(251, 146, 60, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.24, s * 0.24, s * 0.76, s * 0.76), fill=(250, 204, 21, 255), outline=outline, width=ow)

    if gen_style:
        final_img = _style_generated_sticker(canvas, gen_style, size)
    else:
        final_img = canvas.resize((size, size), Image.Resampling.LANCZOS)
    if effects:
        final_img = _with_shadow(final_img)
    return np.array(final_img)

