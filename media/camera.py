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

"""Camera motion for the MoviePy rendering fallback.

``media.worker`` applies this transform to the complete composited frame after
layout. Zoom targets are canvas pixel coordinates, scale is a multiplier, and
all timeline values are seconds. The output retains the original canvas size;
a smooth crop moves toward the target and optionally returns to the full frame.
Native rendering implements the corresponding camera motion in ``automap_rs``.
"""

import numpy as np
from PIL import Image


def _smootherstep(progress):
    """Ease camera motion without abrupt acceleration at either endpoint."""
    return progress * progress * progress * (progress * (progress * 6.0 - 15.0) + 10.0)


def apply_camera_zoom(clip, canvas_size, zoom_params, duration):
    """Return a transformed clip with a smooth crop toward the country target.

    ``zoom_params`` is (target_x_px, target_y_px, scale, start_seconds,
    zoom_seconds, zoom_out_enabled, zoom_out_seconds). ``duration`` is the
    effective output timeline, including audio trimming. The caller retains
    responsibility for closing the source and returned MoviePy clips.
    """
    canvas_w, canvas_h = canvas_size
    (
        target_cx,
        target_cy,
        zoom_scale,
        zoom_start,
        zoom_duration,
        zoom_out_enabled,
        zoom_out_duration,
    ) = zoom_params
    final_cw = canvas_w / zoom_scale
    final_ch = canvas_h / zoom_scale
    final_x0 = max(0.0, min(canvas_w - final_cw, target_cx - final_cw / 2.0))
    final_y0 = max(0.0, min(canvas_h - final_ch, target_cy - final_ch / 2.0))

    def _zoom_full_frame(get_frame, t: float) -> np.ndarray:
        frame = get_frame(t)
        if t <= zoom_start:
            return frame
        if t < zoom_start + zoom_duration:
            progress = (t - zoom_start) / zoom_duration
            ease = _smootherstep(progress)
        elif zoom_out_enabled and zoom_out_duration > 0.0 and duration > (zoom_start + zoom_duration + 0.05):
            out_start = max(zoom_start + zoom_duration, duration - zoom_out_duration)
            out_dur = max(0.05, duration - out_start)
            if t >= duration:
                ease = 0.0
            elif t > out_start:
                progress = (t - out_start) / out_dur
                ease = 1.0 - _smootherstep(progress)
            else:
                ease = 1.0
        else:
            ease = 1.0
        if ease <= 0.0:
            return frame
        crop_width = canvas_w + (final_cw - canvas_w) * ease
        x0 = final_x0 * ease
        y0 = final_y0 * ease
        inverse_zoom = crop_width / canvas_w
        zoomed = Image.fromarray(frame).transform(
            (canvas_w, canvas_h),
            Image.Transform.AFFINE,
            (inverse_zoom, 0.0, x0, 0.0, inverse_zoom, y0),
            resample=Image.Resampling.BILINEAR,
        )
        return np.asarray(zoomed, dtype=np.uint8)

    return clip.transform(_zoom_full_frame)
