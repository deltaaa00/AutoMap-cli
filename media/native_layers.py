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

"""Translate MoviePy layers into the native compositor's input contract.

``media.worker`` calls ``build_native_layers`` after static layers are flattened.
The helper returns full-canvas straight RGBA bytes, positioned dynamic RGBA
buffers, and intro veil duration in seconds. Width/height are pixels; x/y are
canvas offsets. MoviePy stores alpha separately, so it must be reunited with RGB
before crossing the Python/Rust boundary. Background decoding and reveal frames
are handled separately by the native engine rather than duplicated as overlays.
"""

import numpy as np
from PIL import Image


def _clip_to_rgba_bytes(clip_obj, sample_t: float = 0.0) -> bytes:
    # MoviePy splits RGBA inputs into 3-channel clip.img + float clip.mask.
    """Return packed uint8 RGBA bytes from MoviePy's separate RGB and alpha.

    Dynamic masks are sampled at ``sample_t`` seconds. RGB clips without a
    mask receive fully opaque alpha; existing RGBA image arrays pass through.
    """
    arr = np.asarray(clip_obj.img, dtype=np.uint8)
    if arr.ndim == 3 and arr.shape[2] == 4:
        return arr.tobytes()
    if getattr(clip_obj, "mask", None) is not None:
        mask_frame = np.asarray(clip_obj.mask.get_frame(sample_t))
        alpha = np.clip(mask_frame * 255.0, 0, 255).astype(np.uint8)
        if alpha.ndim == 2:
            alpha = alpha[:, :, None]
    else:
        alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
    return np.concatenate([arr[:, :, :3], alpha], axis=2).tobytes()


def build_native_layers(
    clips,
    canvas_size,
    dynamic_layers,
    fade_timing,
    duration,
    *,
    reveal_clip=None,
    composite_rgba=None,
):
    """Return static RGBA bytes, dynamic overlay tuples, and veil duration.

    Skip the first clip because the engine decodes its background itself.
    ``dynamic_layers`` contains clip identities; ``fade_timing`` maps those
    identities to (start_seconds, fade_seconds). A supplied reveal clip is
    skipped because its frames travel through the separate reveal spec.
    ``composite_rgba`` may provide native alpha composition; Pillow handles
    the same operation when it is absent. No clip ownership is transferred.
    """
    canvas_w, canvas_h = canvas_size
    static_rgba = None
    dynamic_overlays = []
    veil_duration = 0.0

    # Skip clips[0] (background_video); Rust decodes it directly from prepared_bg_path.
    for clip in clips[1:]:
        if clip is reveal_clip:
            continue
        if not hasattr(clip, "img"):
            continue

        is_dynamic = id(clip) in dynamic_layers
        is_full_canvas = clip.img.shape[:2] == (canvas_h, canvas_w)

        if not is_dynamic and is_full_canvas:
            layer_rgba = _clip_to_rgba_bytes(clip, 0.0)
            if static_rgba is None:
                static_rgba = layer_rgba
            elif composite_rgba is not None:
                static_rgba = composite_rgba(static_rgba, layer_rgba)
            else:
                base_img = Image.frombytes("RGBA", (canvas_w, canvas_h), static_rgba)
                over_img = Image.frombytes("RGBA", (canvas_w, canvas_h), layer_rgba)
                static_rgba = Image.alpha_composite(base_img, over_img).tobytes()
        elif is_dynamic and is_full_canvas and clip.img.shape[2] == 3:
            # Full-canvas 3-channel black clip represents the intro fade veil.
            veil_duration = float(clip.duration or 0.0)
        elif is_dynamic:
            # Resolve MoviePy position ("center" or pixel offset) into top-left (x, y).
            position = getattr(clip, "pos", (0, 0))
            if callable(position):
                position = position(0)
            h, w = clip.img.shape[:2]
            if position[0] == "center":
                x = (canvas_w - w) // 2
            else:
                x = int(position[0])
            if position[1] == "center":
                y = (canvas_h - h) // 2
            else:
                y = int(position[1])
            start_sec, fade_in_sec = fade_timing.get(id(clip), (0.0, 0.0))
            overlay_pixels = _clip_to_rgba_bytes(clip, float(duration))
            dynamic_overlays.append(
                (overlay_pixels, x, y, int(w), int(h), start_sec, fade_in_sec)
            )

    return static_rgba, dynamic_overlays, veil_duration
