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

"""Artwork loading, reusable region indexes, and extrema highlight masks.

The renderer calls these helpers with artwork paths and image-pixel seeds.
RGBA images, resized relief, and region indexes are cached within the process;
returned cached images are shared and callers must copy before mutation.
Region indexes are also saved below AUTOMAP_REGION_CACHE_DIR, or the system
temporary directory. Cache keys include artwork identity, pixels, seeds, and
fill tolerances so edits rebuild the corresponding index.

Highlight masks use 8-bit grayscale and preserve country boundaries. Adjust
flood-fill tolerances or mask construction here; layer order and user-facing
highlight options remain in auto_map.
"""

import hashlib
import os
import tempfile
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import constants, log


# ---------------------------------------------------------------------------
# Per-process caches for decoded artwork, prepared overlays and region-ID maps.
# Cached images are shared; callers must copy them before mutating.
# ---------------------------------------------------------------------------

_REGION_CACHE_VERSION = 1
_CACHE_LOCK = threading.Lock()
_IMAGE_CACHE: dict[tuple, Image.Image] = {}
_RELIEF_CACHE: dict[tuple, Image.Image] = {}
_REGION_CACHE: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}


def _file_key(path: Path) -> tuple | None:
    """Identity of an artwork file; None when it cannot be stat'ed (no caching)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)


def _load_rgba(path: Path) -> Image.Image:
    """Decode ``path`` as RGBA once per process. Returned image is shared: copy before mutating."""
    key = _file_key(path)
    if key is not None:
        with _CACHE_LOCK:
            cached = _IMAGE_CACHE.get(key)
        if cached is not None:
            return cached
    img = Image.open(path).convert("RGBA")
    if key is not None:
        img.load()
        with _CACHE_LOCK:
            _IMAGE_CACHE[key] = img
    return img


def _prepared_relief(path: Path, size: tuple[int, int]) -> Image.Image:
    """Relief overlay resized to ``size`` with reduced opacity (shared, do not mutate)."""
    key = _file_key(path)
    ckey = None if key is None else (key, tuple(size))
    if ckey is not None:
        with _CACHE_LOCK:
            cached = _RELIEF_CACHE.get(ckey)
        if cached is not None:
            return cached
    relief = _load_rgba(path)
    relief = relief.resize(size) if relief.size != tuple(size) else relief.copy()
    _, _, _, a = relief.split()
    relief.putalpha(a.point(constants._RELIEF_ALPHA_LUT))
    if ckey is not None:
        with _CACHE_LOCK:
            _RELIEF_CACHE[ckey] = relief
    return relief


def _region_thresh(name: str) -> int:
    """Return the flood-fill color tolerance for a region."""
    # Tolerances are tuned to the shading in the source map images.
    if name == "south_america":
        return 15
    if name == "asia":
        return 20
    return 30


def _region_cache_dir() -> Path:
    """Return the directory used to cache precomputed region-ID maps on disk."""
    env = os.environ.get("AUTOMAP_REGION_CACHE_DIR")
    return Path(env) if env else Path(tempfile.gettempdir()) / "automap-region-cache"


def _compute_region_ids(base: Image.Image, coords: dict) -> np.ndarray:
    """Flood fill every seed once, on a scratch copy, with a unique sentinel per region.

    Fills run in the same order and with the same tolerances as the original
    per-render flood fill. Sentinels use alpha 128 so they differ by more than
    any tolerance from both opaque and transparent artwork, which keeps
    already-claimed regions acting as boundaries for later fills.
    Returns an array of region indices (1-based, 0 = unpainted).
    """
    work = base.copy()
    for idx, (name, points) in enumerate(coords.items(), start=1):
        sentinel = (idx & 0xFF, (idx >> 8) & 0xFF, 77, 128)
        thresh = _region_thresh(name)
        for coord in points:
            ImageDraw.floodfill(work, xy=coord, value=sentinel, thresh=thresh)
    orig = np.asarray(base)
    new = np.asarray(work)
    changed = np.any(orig != new, axis=2)
    ids = new[..., 0].astype(np.uint16) | (new[..., 1].astype(np.uint16) << 8)
    ids[~changed] = 0
    return ids


def _region_index(map_path: Path, base: Image.Image, coords: dict) -> tuple[np.ndarray, np.ndarray]:
    """Return flat pixel positions and matching 1-based region IDs.

    A region ID follows coords insertion order; pixels outside all filled regions
    are omitted. Results are cached in memory by artwork identity and seeds,
    then on disk by decoded pixels, seeds, tolerances, and cache format version.
    Unreadable disk caches are rebuilt; cache write failures do not fail renders.
    """
    seeds = tuple((str(n), _region_thresh(n), tuple(tuple(map(int, c)) for c in pts))
                  for n, pts in coords.items())
    fkey = _file_key(map_path)
    mkey = None if fkey is None else (fkey, base.size, seeds)
    if mkey is not None:
        with _CACHE_LOCK:
            hit = _REGION_CACHE.get(mkey)
        if hit is not None:
            return hit

    disk_path = None
    if mkey is not None:
        h = hashlib.sha256()
        h.update(f"v{_REGION_CACHE_VERSION}|{base.size}|{seeds!r}|".encode())
        h.update(base.tobytes())
        disk_path = _region_cache_dir() / f"{h.hexdigest()}.npz"
        try:
            with np.load(disk_path) as data:
                result = (data["flat"], data["ids"])
            log.info("Loaded cached region map")
        except (OSError, KeyError, ValueError):
            result = None
        if result is not None:
            with _CACHE_LOCK:
                _REGION_CACHE[mkey] = result
            return result

    log.info("Building region map (one-time per base map)")
    ids = _compute_region_ids(base, coords).ravel()
    flat = np.flatnonzero(ids)
    id_dtype = np.uint8 if len(coords) < 256 else np.uint16
    result = (flat.astype(np.uint32), ids[flat].astype(id_dtype))

    if mkey is not None:
        with _CACHE_LOCK:
            _REGION_CACHE[mkey] = result
        try:
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=disk_path.parent, suffix=".npz.tmp")
            with os.fdopen(fd, "wb") as fh:
                np.savez(fh, flat=result[0], ids=result[1])
            os.replace(tmp, disk_path)
        except OSError:
            pass
    return result


_RESERVED_HIGHLIGHT_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 229, 255),   # Electric cyan (highest)
    (255, 45, 170),  # Neon magenta (lowest)
)


def _floodfill_bool_mask(interior: np.ndarray, seeds: list, max_pixels: int) -> np.ndarray | None:
    """4-connected BFS flood fill on a boolean ``(H, W)`` interior mask, aborting if it exceeds ``max_pixels``."""
    from collections import deque

    h, w = interior.shape
    visited = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()
    for coord in seeds:
        cx, cy = int(round(coord[0])), int(round(coord[1]))
        seed = None
        for dy in (0, -2, 2, -4, 4, -8, 8):
            for dx in (0, -2, 2, -4, 4, -8, 8):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < w and 0 <= ny < h and interior[ny, nx]:
                    seed = (nx, ny)
                    break
            if seed is not None:
                break
        if seed is not None and not visited[seed[1], seed[0]]:
            visited[seed[1], seed[0]] = True
            q.append(seed)

    if not q:
        return None

    count = 0
    while q:
        x, y = q.popleft()
        count += 1
        if count > max_pixels:
            return None
        if x > 0 and interior[y, x - 1] and not visited[y, x - 1]:
            visited[y, x - 1] = True
            q.append((x - 1, y))
        if x + 1 < w and interior[y, x + 1] and not visited[y, x + 1]:
            visited[y, x + 1] = True
            q.append((x + 1, y))
        if y > 0 and interior[y - 1, x] and not visited[y - 1, x]:
            visited[y - 1, x] = True
            q.append((x, y - 1))
        if y + 1 < h and interior[y + 1, x] and not visited[y + 1, x]:
            visited[y + 1, x] = True
            q.append((x, y + 1))

    return visited


def _build_extrema_highlight_layer(
    size: tuple[int, int],
    highest_idx: int | None,
    lowest_idx: int | None,
    map_type: str,
    dict_: dict,
    flat: np.ndarray | None,
    ids: np.ndarray | None,
    borders: Image.Image | None,
    base_map: Image.Image | None = None,
    high_rgb: tuple[int, int, int] = _RESERVED_HIGHLIGHT_COLORS[0],
    low_rgb: tuple[int, int, int] = _RESERVED_HIGHLIGHT_COLORS[1],
) -> Image.Image | None:
    """Create a soft tint + inner glow highlight layer for the highest and lowest regions."""
    from PIL import ImageChops, ImageFilter

    targets: list[tuple[int, tuple[int, int, int]]] = []
    if highest_idx is not None:
        targets.append((highest_idx, high_rgb))
    if lowest_idx is not None and lowest_idx != highest_idx:
        targets.append((lowest_idx, low_rgb))
    if not targets:
        return None

    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    coords_list = list(dict_.values())

    if map_type == "flag" and (flat is None or ids is None):
        mask_path = Path(__file__).parent.parent / "resources" / "maps" / "default_map.png"
        flat, ids = _region_index(mask_path, _load_rgba(mask_path), dict_)

    inner_size = 37 if w >= 3000 else 21
    outer_size = 25 if w >= 3000 else 15
    wc_interior_crisp: np.ndarray | None = None
    wc_base_alpha: np.ndarray | None = None
    wc_border_alpha: np.ndarray | None = None
    if map_type == "world_countries" and base_map is not None:
        base_arr = np.asarray(base_map)
        wc_base_alpha = base_arr[..., 3]
        # Crisp dark country borders on world_countries_map.png cleanly separate adjacent nations.
        wc_interior_crisp = (wc_base_alpha >= 128) & (base_arr[..., :3].max(axis=2) > 12)
        if borders is not None:
            wc_border_alpha = np.asarray(borders)[..., 3]

    for region_idx, rgb in targets:
        mask: Image.Image | None = None
        if flat is not None and ids is not None:
            pix_mask = np.zeros(w * h, dtype=np.uint8)
            pix_mask[flat[ids == region_idx]] = 255
            if pix_mask.any():
                mask = Image.fromarray(pix_mask.reshape((h, w)), mode="L")
        elif wc_interior_crisp is not None and 1 <= region_idx <= len(coords_list):
            max_px = int(0.08 * w * h)
            filled = _floodfill_bool_mask(wc_interior_crisp, coords_list[region_idx - 1], max_px)
            if (filled is None or not filled.any()) and wc_base_alpha is not None and wc_border_alpha is not None:
                for thr in (45, 65):
                    interior = (wc_base_alpha >= 128) & (wc_border_alpha < thr)
                    filled = _floodfill_bool_mask(interior, coords_list[region_idx - 1], max_px)
                    if filled is not None and filled.any():
                        break
            if filled is not None and filled.any():
                mask = Image.fromarray((filled.astype(np.uint8) * 255), mode="L")

        if mask is None:
            continue

        # Downscale the binary mask before morphological filters so MinFilter/MaxFilter
        # run in milliseconds instead of O(k^2) over 16M full-res pixels.
        ds = 4 if w >= 2400 else 2
        small_size = (max(1, w // ds), max(1, h // ds))
        small_mask = mask.resize(small_size, Image.Resampling.BILINEAR)
        small_inner = small_mask.filter(ImageFilter.MinFilter(9))
        small_outer = small_mask.filter(ImageFilter.MaxFilter(7))
        inner_ring = ImageChops.subtract(small_mask, small_inner).filter(ImageFilter.GaussianBlur(2))
        outer_halo = ImageChops.subtract(small_outer, small_mask).filter(ImageFilter.GaussianBlur(2.5))
        glow_small = ImageChops.lighter(inner_ring, outer_halo.point(lambda p: min(255, int(p * 0.95))))
        glow_alpha = glow_small.resize((w, h), Image.Resampling.BILINEAR)
        tint_alpha = mask.point(lambda p: 95 if p else 0)
        combined_alpha = ImageChops.lighter(tint_alpha, glow_alpha)

        overlay = Image.new("RGBA", size, (*rgb, 255))
        overlay.putalpha(combined_alpha)
        layer = Image.alpha_composite(layer, overlay)

    return layer
