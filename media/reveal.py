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

"""Country reveal animation shared by native and MoviePy rendering.

``media.worker`` supplies a map path, a float alpha mask, target pixel dimensions,
and timing in seconds. The animation covers a country, shows one to three dots,
then fades back to the original map. Frames use uint8 straight RGBA internally;
MoviePy receives RGB plus a separate float alpha mask.

The built clip caches the most recent RGB/mask pair because MoviePy requests
both at the same timestamp. Its ``rust_reveal_spec`` attribute contains the same
precomposed frames and timing for the native compositor, keeping both engines'
layout and reveal phases aligned. The worker owns the returned clip lifecycle.
"""

import math

from .inputs import (
    parse_finite_float as _parse_finite_float,
    parse_reveal_color as _parse_reveal_color,
)


def _render_reveal_dot_overlays(
    box_w: int,
    box_h: int,
    cx: float,
    cy: float,
    cover_rgb: tuple[int, int, int] = (0, 0, 0),
) -> dict[int, tuple[int, int, int, int, object]]:
    """Pre-render 1-, 2-, and 3-dot RGBA overlays centered at ``(cx, cy)`` using 4x supersampling."""
    import numpy as np
    from PIL import Image, ImageDraw

    base_dim = min(box_w, box_h)
    dot_r = max(6.0, min(10.5, base_dim * 0.011))
    outline_w = max(2.0, dot_r * 0.34)
    outer_r = dot_r + outline_w
    spacing = dot_r * 3.0

    pad = int(math.ceil(outer_r)) + 4
    x0 = max(0, int(math.floor(cx - spacing - pad)))
    x1 = min(box_w, int(math.ceil(cx + spacing + pad)))
    y0 = max(0, int(math.floor(cy - pad)))
    y1 = min(box_h, int(math.ceil(cy + pad)))
    patch_w = max(1, x1 - x0)
    patch_h = max(1, y1 - y0)

    lum = 0.299 * cover_rgb[0] + 0.587 * cover_rgb[1] + 0.114 * cover_rgb[2]
    dot_fill = (15, 23, 42, 255) if lum > 175 else (255, 255, 255, 255)
    ring_fill = (255, 255, 255, 255) if lum > 175 else (10, 12, 20, 255)

    ss = 4
    overlays: dict[int, tuple[int, int, int, int, object]] = {}
    for count in (1, 2, 3):
        hi_img = Image.new('RGBA', (patch_w * ss, patch_h * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(hi_img)
        for idx in range(count):
            dot_x = (cx + (idx - 1) * spacing - x0) * ss
            dot_y = (cy - y0) * ss
            ro = outer_r * ss
            ri = dot_r * ss
            draw.ellipse(
                (dot_x - ro, dot_y - ro, dot_x + ro, dot_y + ro),
                fill=ring_fill,
            )
            draw.ellipse(
                (dot_x - ri, dot_y - ri, dot_x + ri, dot_y + ri),
                fill=dot_fill,
            )
        patch = hi_img.resize((patch_w, patch_h), Image.Resampling.LANCZOS)
        overlays[count] = (y0, y1, x0, x1, np.array(patch, dtype=np.uint8))
    return overlays


def _alpha_composite_patch(base_rgba, y0: int, y1: int, x0: int, x1: int, patch_rgba):
    """Return a copy of ``base_rgba`` with ``patch_rgba`` alpha-composited into ``[y0:y1, x0:x1]``."""
    import numpy as np

    out = base_rgba.copy()
    bg = out[y0:y1, x0:x1].astype(np.float32)
    fg = patch_rgba.astype(np.float32)
    fg_a = fg[:, :, 3:4] / 255.0
    bg_a = bg[:, :, 3:4] / 255.0
    out_a = fg_a + bg_a * (1.0 - fg_a)
    safe_a = np.maximum(out_a, 1e-6)
    out_rgb = (fg[:, :, :3] * fg_a + bg[:, :, :3] * bg_a * (1.0 - fg_a)) / safe_a
    out[y0:y1, x0:x1, :3] = np.clip(np.rint(out_rgb), 0, 255).astype(np.uint8)
    out[y0:y1, x0:x1, 3:4] = np.clip(np.rint(out_a * 255.0), 0, 255).astype(np.uint8)
    return out


def _compute_reveal_time(
    zoom_enabled: bool,
    zoom_start: float,
    zoom_duration: float,
    reveal_time_raw: object,
) -> float:
    """Resolve the effective reveal trigger timestamp (after zoom ends when zoom is enabled)."""
    if zoom_enabled:
        zoom_end = zoom_start + zoom_duration
        default_reveal = zoom_end + 0.8
        minimum_reveal = zoom_end + 0.5
    else:
        default_reveal = 2.5
        minimum_reveal = 0.2
    requested_reveal = _parse_finite_float(reveal_time_raw, default_reveal)
    return max(minimum_reveal, requested_reveal)


def _apply_reveal_impact_fx(clip, reveal_time: float, canvas_w: int, canvas_h: int):
    """Apply a brief camera-shake + RGB chromatic glitch + impact punch when dots vanish."""
    import numpy as np
    from PIL import Image

    impact_dur = 0.60

    def _impact_frame(get_frame, t: float) -> np.ndarray:
        frame = get_frame(t)
        dt = float(t) - reveal_time
        if dt < 0.0 or dt >= impact_dur:
            return frame

        env = math.exp(-dt * 5.8) * max(0.0, 1.0 - dt / impact_dur)
        punch = 1.0 + 0.042 * env
        inv_p = 1.0 / punch
        cw = canvas_w * inv_p
        ch = canvas_h * inv_p
        margin_x = (canvas_w - cw) * 0.5
        margin_y = (canvas_h - ch) * 0.5
        shake_x = env * 22.0 * math.sin(dt * 85.0) + env * 8.0 * math.cos(dt * 145.0)
        shake_y = env * 18.0 * math.cos(dt * 78.0) - env * 6.0 * math.sin(dt * 130.0)
        x0 = max(0.0, min(canvas_w - cw, margin_x + shake_x))
        y0 = max(0.0, min(canvas_h - ch, margin_y + shake_y))

        punched = Image.fromarray(frame).transform(
            (canvas_w, canvas_h),
            Image.Transform.AFFINE,
            (inv_p, 0.0, x0, 0.0, inv_p, y0),
            resample=Image.Resampling.BILINEAR,
        )
        out = np.array(punched, dtype=np.uint8, copy=True)

        if dt < 0.38:
            glitch_env = math.exp(-dt * 7.5)
            ca_shift = int(round(10.0 * glitch_env * (1.0 if math.sin(dt * 105.0) >= 0 else -1.0)))
            if ca_shift > 0 and ca_shift < canvas_w:
                src = out.copy()
                out[:, ca_shift:, 0] = src[:, :-ca_shift, 0]
                out[:, :-ca_shift, 2] = src[:, ca_shift:, 2]
            elif ca_shift < 0 and (-ca_shift) < canvas_w:
                s = -ca_shift
                src = out.copy()
                out[:, :-s, 0] = src[:, s:, 0]
                out[:, s:, 2] = src[:, :-s, 2]

            if glitch_env > 0.20:
                step = int(dt * 50.0)
                band_h = max(8, canvas_h // 65)
                dx = max(4, int(round(18.0 * glitch_env)))
                for b_idx, frac in enumerate((0.38, 0.56)):
                    by0 = min(
                        canvas_h - band_h,
                        max(0, int(canvas_h * frac) + ((step * 53 + b_idx * 97) % 140) - 70),
                    )
                    by1 = min(canvas_h, by0 + band_h)
                    shift_px = dx if ((step + b_idx) % 2 == 0) else -dx
                    out[by0:by1] = np.roll(out[by0:by1], shift_px, axis=1)

        if dt < 0.14:
            boost = int(round(18.0 * (1.0 - dt / 0.14)))
            if boost > 0:
                out = np.clip(out.astype(np.int16) + boost, 0, 255).astype(np.uint8)
        return out

    return clip.transform(_impact_frame)


def _build_reveal_map_clip(
    map_path: str,
    mask_f,
    box_w: int,
    box_h: int,
    bg_duration: float,
    effects: bool,
    zoom_enabled: bool,
    zoom_x: float,
    zoom_y: float,
    zoom_start: float,
    zoom_duration: float,
    reveal_time_raw: object,
    reveal_color: object = "#000000",
    cover_map_pil=None,
):
    """Build synchronized RGB/mask clips and native reveal frame metadata.

    ``mask_f`` is float alpha with shape (box_h, box_w, 1). Target coordinates
    are normalized to [0, 1], dimensions are pixels, and times are seconds.
    An optional clean cover map hides labels until reveal. Shadow padding
    enlarges returned frames by 48 pixels; the worker adjusts their placement.
    ``rust_reveal_spec`` stores five RGBA buffers plus dimensions and timing.
    """
    import numpy as np
    from moviepy import VideoClip
    from PIL import Image, ImageFilter

    with Image.open(map_path) as raw_map:
        base_pil = raw_map.convert('RGBA').resize((box_w, box_h), Image.Resampling.LANCZOS)
    base_rgba = np.array(base_pil, dtype=np.uint8)

    if cover_map_pil is not None:
        cover_resized = cover_map_pil.convert('RGBA').resize((box_w, box_h), Image.Resampling.LANCZOS)
        cover_rgba = np.array(cover_resized, dtype=np.uint8)
    else:
        cover_rgba = base_rgba

    cover_r, cover_g, cover_b = _parse_reveal_color(reveal_color)
    map_visible = (cover_rgba[:, :, 3:4] > 0).astype(np.float32)
    eff_mask = mask_f * map_visible
    black_pixel = np.array([float(cover_r), float(cover_g), float(cover_b), 255.0], dtype=np.float32).reshape(1, 1, 4)
    base_f = base_rgba.astype(np.float32)
    cover_f = cover_rgba.astype(np.float32)
    black_f = cover_f * (1.0 - eff_mask) + black_pixel * eff_mask
    delta_f = black_f - base_f
    black_rgba = np.clip(np.rint(black_f), 0, 255).astype(np.uint8)

    shadow_only = None
    if effects:
        alpha_chan = Image.new('L', (box_w + 48, box_h + 48))
        alpha_chan.paste(base_pil.getchannel('A'), (24, 32))
        shadow_only = Image.new('RGBA', (box_w + 48, box_h + 48), (0, 0, 0, 0))
        shadow_only.putalpha(
            alpha_chan.filter(ImageFilter.GaussianBlur(10)).point(lambda p: round(p * 0.8))
        )

    def _compose_rgba(frame_rgba) -> np.ndarray:
        if shadow_only is not None:
            canvas = shadow_only.copy()
            canvas.alpha_composite(Image.fromarray(frame_rgba), (24, 24))
            return np.asarray(canvas, dtype=np.uint8)
        return np.ascontiguousarray(frame_rgba, dtype=np.uint8)

    def _to_rgb_and_mask(composed: np.ndarray) -> tuple[object, object]:
        rgb = np.ascontiguousarray(composed[:, :, :3])
        mask = np.ascontiguousarray(composed[:, :, 3], dtype=np.float32) / 255.0
        return rgb, mask

    r_time = _compute_reveal_time(zoom_enabled, zoom_start, zoom_duration, reveal_time_raw)
    fade_dur = 0.25
    if zoom_enabled and zoom_start > 0.2:
        phase_end = zoom_start
    else:
        phase_end = min(1.5, r_time * 0.6)

    dot_cx = zoom_x * box_w
    dot_cy = zoom_y * box_h
    mask_2d = eff_mask[:, :, 0] > 0.5
    ys, xs = np.nonzero(mask_2d)
    if ys.size > 0:
        dot_cx = float(xs.mean())
        dot_cy = float(ys.mean())
        ix = max(0, min(box_w - 1, int(round(dot_cx))))
        iy = max(0, min(box_h - 1, int(round(dot_cy))))
        if not mask_2d[iy, ix]:
            d2 = (xs.astype(np.float32) - dot_cx) ** 2 + (ys.astype(np.float32) - dot_cy) ** 2
            best = int(np.argmin(d2))
            dot_cx = float(xs[best])
            dot_cy = float(ys[best])

    dot_overlays = _render_reveal_dot_overlays(box_w, box_h, dot_cx, dot_cy, (cover_r, cover_g, cover_b))
    dot_rgba_frames = {
        count: _compose_rgba(_alpha_composite_patch(black_rgba, *dot_overlays[count]))
        for count in (1, 2, 3)
    }
    dot_frames = {
        count: _to_rgb_and_mask(dot_rgba_frames[count])
        for count in (1, 2, 3)
    }
    black_composed_rgba = _compose_rgba(black_rgba)
    base_composed_rgba = _compose_rgba(base_rgba)
    final_frame = _to_rgb_and_mask(base_composed_rgba)

    cached_frame: tuple[float, object, object] | None = None

    def _get_cached_frame(t: float) -> tuple[object, object]:
        nonlocal cached_frame
        t_key = float(t)
        # MoviePy asks for RGB and alpha separately at the same timestamp.
        # Reuse the composed frame instead of doing that work twice.
        if cached_frame is not None and cached_frame[0] == t_key:
            return cached_frame[1], cached_frame[2]
        if t_key < r_time:
            if t_key < phase_end * 0.33:
                count = 1
            elif t_key < phase_end * 0.66:
                count = 2
            else:
                count = 3
            rgb, mask = dot_frames[count]
        elif t_key >= r_time + fade_dur:
            rgb, mask = final_frame
        else:
            fade_alpha = max(0.0, min(1.0, 1.0 - (t_key - r_time) / fade_dur))
            blended_rgba = np.clip(np.rint(base_f + fade_alpha * delta_f), 0, 255).astype(np.uint8)
            rgb, mask = _to_rgb_and_mask(_compose_rgba(blended_rgba))
        cached_frame = (t_key, rgb, mask)
        return rgb, mask

    rgb_clip = VideoClip(lambda t: _get_cached_frame(t)[0], duration=bg_duration)
    mask_clip = VideoClip(lambda t: _get_cached_frame(t)[1], is_mask=True, duration=bg_duration)
    reveal_clip = rgb_clip.with_mask(mask_clip)
    spec_h, spec_w = base_composed_rgba.shape[:2]
    reveal_clip.rust_reveal_spec = (
        int(spec_w),
        int(spec_h),
        dot_rgba_frames[1].tobytes(),
        dot_rgba_frames[2].tobytes(),
        dot_rgba_frames[3].tobytes(),
        black_composed_rgba.tobytes(),
        base_composed_rgba.tobytes(),
        float(r_time),
        float(phase_end),
        float(fade_dur),
    )
    return reveal_clip
