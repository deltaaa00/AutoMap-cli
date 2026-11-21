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

"""Parse media settings at the worker's untrusted-input boundary.

Numeric settings reject booleans and nonfinite values through a caller-provided
default. Reveal masks accept base64 image data, enforce byte and pixel limits,
and return resized float32 alpha of shape (height, width, 1) in [0, 1]. An invalid
or entirely empty mask returns None so the caller can disable reveal cleanly.
These helpers have no Flask dependency and do not own files or persistent state.
"""

import base64
import io
import math


def parse_finite_float(value: object, default: float) -> float:
    """Parse a finite float from JSON input, returning ``default`` on missing or invalid values."""
    if value is None or value == '' or isinstance(value, bool):
        return default
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def decode_reveal_mask(raw_mask: object, box_w: int, box_h: int, *, max_bytes: int):
    """Return resized float32 alpha of shape (box_h, box_w, 1), or None.

    Accept raw base64 or a base64 data URL. ``max_bytes`` bounds the decoded
    payload, and a separate 20-megapixel limit bounds source image expansion.
    All-zero masks and malformed data disable reveal rather than raising.
    """
    if not isinstance(raw_mask, str) or not raw_mask.strip():
        return None
    import numpy as np
    from PIL import Image, UnidentifiedImageError

    value = raw_mask.strip()
    if value.startswith('data:'):
        header, sep, value = value.partition(',')
        if not sep or ';base64' not in header.lower():
            return None
    b64_part = ''.join(value.split())
    if not b64_part or len(b64_part) > 4 * ((max_bytes + 2) // 3):
        return None
    try:
        raw_bytes = base64.b64decode(b64_part, validate=True)
    except (ValueError, TypeError):
        return None
    if not raw_bytes or len(raw_bytes) > max_bytes:
        return None
    try:
        with Image.open(io.BytesIO(raw_bytes)) as mask_img:
            if mask_img.width * mask_img.height > 20_000_000:
                return None
            mask_resized = mask_img.convert('L').resize(
                (box_w, box_h),
                Image.Resampling.BILINEAR,
            )
            mask_arr = np.array(mask_resized, dtype=np.float32) / 255.0
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        return None
    if not np.any(mask_arr > 0.0):
        return None
    return mask_arr[..., None]


def parse_reveal_color(raw_color: object) -> tuple[int, int, int]:
    """Parse a hex color string (e.g. ``#000000``) into an RGB tuple, defaulting to black."""
    if not isinstance(raw_color, str):
        return (0, 0, 0)
    s = raw_color.strip().lstrip('#')
    if len(s) == 3 and all(c in '0123456789abcdefABCDEF' for c in s):
        s = ''.join(c * 2 for c in s)
    if len(s) == 6 and all(c in '0123456789abcdefABCDEF' for c in s):
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    return (0, 0, 0)
