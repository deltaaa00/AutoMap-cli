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

#This file makes blured borders


"""Prepare a blurred border overlay from a map asset, outside the live renderer.

This standalone maintenance script reads default_map.png in the current working
directory and writes default_map_blured.png there. It blurs first, blackens the
selected border pixels, then strengthens their alpha without uint8 overflow.
Run from a disposable asset directory when experimenting. Top-level I/O also runs
on import; application code should use prepared assets rather than import this
script. tests/test_asset_tools.py exercises it with temporary artwork.
"""

from PIL import Image, ImageFilter
import numpy as np

def borders_procesing(image: Image.Image): #Function prepares borders to composing after ImageFilter.GaussianBlur()

    """Return an RGBA border overlay with selected blur pixels blackened and strengthened.

    The input must have four channels. Opacity addition uses a wider integer type
    before clamping to 255 so bright edges cannot wrap back to transparency.
    """
    pixels = np.array(image)

    r, g, b, a = pixels[..., 0], pixels[..., 1], pixels[..., 2], pixels[..., 3]

    # Select pixels with a nonzero value in every RGB channel.
    mask = (r != 0) & (g != 0) & (b != 0)

    # Turn the selected blur pixels black to form a border overlay.
    pixels[mask, 0:3] = 0

    alpha_mask = mask & (a != 0)
    # Widen the integer type before adding opacity to avoid uint8 wraparound.
    pixels[alpha_mask, 3] = np.clip(a[alpha_mask].astype(np.int16) + 30, 0, 255)

    return Image.fromarray(pixels)


# Run from the map asset directory; blur first, then strengthen the border pixels.
borders = Image.open("default_map.png").convert("RGBA")
borders = borders.filter(ImageFilter.GaussianBlur(radius=15))

borders = borders_procesing(borders)

borders.save("default_map_blured.png")
