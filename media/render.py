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

"""Shared clip preparation and encoding policy for ``media.worker``.

FFmpeg normalizes background footage before either rendering engine uses it.
MoviePy helpers build float alpha masks and merge consecutive static layers in
painter's order; dynamic clips remain separate so fades and reveals still work.
Sizes are (width, height) in pixels and all durations are seconds. Helpers return
clips to the worker, which owns closing file-backed readers after encoding.
"""

import os
import subprocess

def _apply_clip_fade_in(clip, total_duration: float, fade_start: float = 0.0, fade_duration: float = 0.6):
    """Smoothly fade a clip's alpha mask in starting at ``fade_start`` over ``fade_duration`` seconds."""
    import numpy as np
    from moviepy import VideoClip

    if fade_start <= 0.0 and fade_duration <= 0.0:
        return clip.with_duration(total_duration)
    base_frame = clip.get_frame(0)
    if base_frame.ndim == 3 and base_frame.shape[2] == 4:
        base_mask = base_frame[:, :, 3].astype(np.float32) / 255.0
    elif clip.mask is not None:
        base_mask = clip.mask.get_frame(0).astype(np.float32)
    else:
        base_mask = np.ones((clip.h, clip.w), dtype=np.float32)

    def _mask_frame(t: float) -> np.ndarray:
        if t < fade_start:
            return np.zeros_like(base_mask)
        if fade_duration <= 0.0 or t >= fade_start + fade_duration:
            return base_mask
        alpha = float((t - fade_start) / fade_duration)
        return base_mask * max(0.0, min(1.0, alpha))

    mask_clip = VideoClip(_mask_frame, is_mask=True, duration=total_duration)
    return clip.with_duration(total_duration).with_mask(mask_clip)


def _x264_preset() -> str:
    """x264 speed preset; ``VIDEO_X264_PRESET`` overrides the fast default."""
    allowed = ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast',
               'medium', 'slow', 'slower', 'veryslow')
    preset = os.environ.get('VIDEO_X264_PRESET', 'veryfast').strip().lower()
    if preset not in allowed:
        return 'veryfast'
    return preset


def _render_threads() -> int:
    """Encoder thread count; ``VIDEO_RENDER_THREADS`` overrides the CPU count."""
    try:
        threads = int(os.environ.get('VIDEO_RENDER_THREADS', '0'))
    except ValueError:
        threads = 0
    if threads > 0:
        return threads
    return os.cpu_count() or 2


def _prepare_background(src: str, dst: str, duration: float, size=(1080, 1920),
                        effects: bool = True) -> None:
    """Trim, cover-scale, center-crop and optionally soften the background in FFmpeg.

    This replaces per-frame MoviePy resizing and the Pillow blur (downscale to
    a quarter, Gaussian blur radius 2, upscale) with one native pass.  The
    intermediate is encoded near-losslessly so the final encode sees the same
    pixels the Python path produced.
    """
    from moviepy.config import FFMPEG_BINARY

    w, h = size
    filters = [f'scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos',
               f'crop={w}:{h}']
    if effects:
        filters += [f'scale={w // 4}:{h // 4}:flags=bicubic', 'gblur=sigma=2',
                    f'scale={w}:{h}:flags=bicubic']
    filters.append('format=yuv420p')
    command = [FFMPEG_BINARY, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
               '-i', src, '-t', f'{duration:.3f}', '-an', '-vf', ','.join(filters),
               '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '10',
               '-threads', str(_render_threads()), dst]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f'Background preprocessing failed: {result.stderr.strip()[-300:]}')


def _flatten_static_layers(clips, size, dynamic, duration):
    """Merge consecutive static layers into single RGBA foreground clips.

    ``dynamic`` holds ids of clips that change over time (background, fades,
    veil).  Static runs between them are alpha-composited once with Pillow in
    the same order and with the same positioning rules MoviePy uses, so each
    output frame blends one foreground instead of many layers.
    """
    import numpy as np
    from PIL import Image
    from moviepy import ImageClip
    from moviepy.tools import compute_position

    flattened_clips = []
    canvas = None

    def flush():
        nonlocal canvas
        if canvas is not None:
            # A completed static run becomes one clip at the canvas origin.
            combined_clip = ImageClip(np.array(canvas))
            combined_clip = combined_clip.with_duration(duration).with_position((0, 0))
            flattened_clips.append(combined_clip)
            canvas = None

    for clip in clips:
        if id(clip) in dynamic:
            flush()
            flattened_clips.append(clip)
            continue
        frame = np.asarray(clip.get_frame(0))
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        layer = Image.fromarray(frame[:, :, :3]).convert('RGBA')
        if clip.mask is not None:
            alpha = np.clip(np.asarray(clip.mask.get_frame(0)) * 255, 0, 255).astype(np.uint8)
            layer.putalpha(Image.fromarray(alpha))
        elif frame.ndim == 3 and frame.shape[2] == 4:
            layer.putalpha(Image.fromarray(frame[:, :, 3]))
        pos = compute_position(layer.size, size, clip.pos(0), clip.relative_pos)
        if canvas is None:
            canvas = Image.new('RGBA', size, (0, 0, 0, 0))
        canvas.alpha_composite(layer, tuple(int(v) for v in pos))
    flush()
    return flattened_clips

