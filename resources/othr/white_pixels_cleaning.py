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

from PIL import Image

_OPAQUE_ALPHA_LUT: list[int] = [255 if a == 255 else 0 for a in range(256)]

# Run from the asset directory: this utility overwrites map.png in place.
with Image.open("map.png") as opened:
    img = opened.convert("RGBA")

# Remove partially transparent edge pixels in C via LUT while preserving opaque artwork.
r, g, b, a = img.split()
img = Image.merge("RGBA", (r, g, b, a.point(_OPAQUE_ALPHA_LUT)))
img.save("map.png")
