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

import ast
import hashlib
import os
import random
import io
import tempfile
import threading
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import constants

from .functions import ai_request, load_filling, _warning as showwarning, _error as showerror
from . import log

def _country_key(name: object) -> str:
    """Normalize a country name to snake_case ASCII ("Côte d'Ivoire" -> "cote_d_ivoire")."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _world_countries_answer_norm(ai_answer: dict[str, object], keys: object) -> dict[str, object]:
    """Index AI answers by normalized country key, resolving common aliases."""
    norm: dict[str, object] = {}
    for k, v in (ai_answer or {}).items():
        norm.setdefault(_country_key(k), v)
    known = {_country_key(k) for k in keys}
    for alias, targets in constants._WORLD_COUNTRY_ALIASES.items():
        if alias not in norm:
            continue
        for target in targets:
            if target in known and target not in norm:
                norm[target] = norm[alias]
                break
    return norm


def _load_world_countries() -> tuple[dict[str, list[tuple[int, int]]], dict[str, int]]:
    """Import world-countries seed points and label sizes on first use."""
    from .world_countries_coords import world_countries_coords, world_countries_label_sizes

    return world_countries_coords, world_countries_label_sizes


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
    """Return (flat pixel indices, region ids) for every pixel belonging to a region.

    Cached in memory per (artwork file, seeds) and on disk keyed by a hash of the
    decoded pixels, seeds, and tolerances, so artwork or seed edits regenerate it.
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


def _paint_regions(img: Image.Image, flat: np.ndarray, ids: np.ndarray,
                   colors: dict[int, tuple[int, int, int, int]]) -> Image.Image:
    """Return a copy of ``img`` with region ``i`` painted ``colors[i]``."""
    if not colors:
        return img.copy()
    n = int(max(colors)) + 1
    if ids.size:
        n = max(n, int(ids.max()) + 1)
    lut = np.zeros((n, 4), dtype=np.uint8)
    use = np.zeros(n, dtype=bool)
    for i, c in colors.items():
        lut[i] = c
        use[i] = True
    arr = np.array(img, dtype=np.uint8)
    pix = arr.reshape(-1, 4)
    sel = use[ids]
    pix[flat[sel]] = lut[ids[sel]]
    return Image.fromarray(arr)


def _resolve_theme_name(palette_theme: str = "auto", prompt: str = "", mode: str = "text") -> str:
    """Pick a palette theme name from an explicit choice, prompt keywords, or mode."""
    theme = (palette_theme or "auto").strip().lower()
    if theme in constants._PALETTE_THEMES and theme != "auto":
        return theme
    low = (prompt or "").lower()
    if any(w in low for w in ("nicholas", "christmas", "santa", "holiday", "xmas")):
        return "festive"
    if any(w in low for w in ("sea", "ocean", "water", "cold", "ice", "winter")):
        return "ocean"
    if any(w in low for w in ("hot", "heat", "sun", "fire", "summer")):
        return "sunset"
    if mode == "number":
        return "viridis"
    return "auto"


def _hex_to_rgba(val: object, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Parse a ``#RRGGBB`` hex string into an opaque RGBA tuple, or return ``default``."""
    if not isinstance(val, str):
        return default
    clean = val.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", clean):
        return default
    return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16), 255)


def _to_rgba_tuple(val: object) -> tuple[int, int, int, int] | None:
    """Coerce an RGB/RGBA sequence or ``#RRGGBB`` hex string into a clamped RGBA tuple."""
    if isinstance(val, (list, tuple)) and len(val) in (3, 4):
        try:
            r, g, b = int(val[0]), int(val[1]), int(val[2])
            a = int(val[3]) if len(val) == 4 else 255
            return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), max(0, min(255, a)))
        except (ValueError, TypeError, OverflowError):
            return None
    if isinstance(val, str) and val.strip().startswith("#"):
        return _hex_to_rgba(val, (99, 102, 241, 255))
    return None


def _parse_filling_overrides(
    raw_txt: object,
    raw_num: object,
) -> tuple[dict[str, tuple[int, int, int, int]], dict[tuple[float, float], tuple[int, int, int, int]]]:
    """Normalize optional text and numeric filling overrides into RGBA lookup dicts."""
    txt_out: dict[str, tuple[int, int, int, int]] = {}
    num_out: dict[tuple[float, float], tuple[int, int, int, int]] = {}

    def add_range(low, high, rgba):
        if isinstance(low, bool) or isinstance(high, bool):
            return
        low, high = float(low), float(high)
        if math.isfinite(low) and math.isfinite(high) and low < high:
            # Keep integer keys stable in metadata without truncating fractions.
            bounds = tuple(int(v) if v.is_integer() else v for v in (low, high))
            num_out[bounds] = rgba
    if isinstance(raw_txt, dict):
        for k, v in raw_txt.items():
            key_str = str(k).strip()
            rgba = _to_rgba_tuple(v)
            if key_str and rgba is not None:
                txt_out[key_str] = rgba
    if isinstance(raw_num, dict):
        for k, v in raw_num.items():
            rgba = _to_rgba_tuple(v)
            if rgba is None:
                continue
            try:
                bounds = k if isinstance(k, tuple) else ast.literal_eval(str(k))
                if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                    add_range(bounds[0], bounds[1], rgba)
            except (ValueError, SyntaxError, TypeError, OverflowError):
                continue
    elif isinstance(raw_num, list):
        for item in raw_num:
            if not isinstance(item, dict):
                continue
            rgba = _to_rgba_tuple(item.get("color") or item.get("hex"))
            if rgba is None:
                continue
            try:
                add_range(item["low"], item["high"], rgba)
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
    return txt_out, num_out


_SHORT_UNITS: tuple[tuple[float, str], ...] = (
    (1_000_000_000_000.0, "T"),
    (1_000_000_000.0, "B"),
    (1_000_000.0, "M"),
    (1_000.0, "k"),
)


def _parse_short_number(val: object) -> float | None:
    """Parse plain numbers or English short-form numbers like '10k', '30K', '4M', '10B', '13T'."""
    if isinstance(val, bool) or val is None:
        return None
    s = str(val).replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    mult = 1.0
    low = s.lower()
    if low.endswith("k"):
        mult, s = 1_000.0, s[:-1]
    elif low.endswith("m"):
        mult, s = 1_000_000.0, s[:-1]
    elif low.endswith("b"):
        mult, s = 1_000_000_000.0, s[:-1]
    elif low.endswith("t"):
        mult, s = 1_000_000_000_000.0, s[:-1]
    try:
        num = float(s) * mult
    except ValueError:
        return None
    return num if math.isfinite(num) else None


def _format_number_value(num: float) -> str:
    """Format a finite float (>= 1,000 in magnitude) using compact English k/M/B/T suffixes."""
    abs_num = abs(num)
    for idx, (threshold, suffix) in enumerate(_SHORT_UNITS):
        if abs_num >= threshold:
            scaled = round(num / threshold, 1)
            if abs(scaled) >= 1_000.0 and idx > 0:
                prev_threshold, prev_suffix = _SHORT_UNITS[idx - 1]
                scaled = round(num / prev_threshold, 1)
                suffix = prev_suffix
            if float(scaled).is_integer():
                return f"{int(scaled)}{suffix}"
            return f"{scaled:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(num)) if float(num).is_integer() else str(round(num, 2))


def _format_short_number(val: object) -> object:
    """Abbreviate large numeric values (>= 1,000) into compact English k/M/B/T strings."""
    if val is None or isinstance(val, bool):
        return val
    if isinstance(val, str):
        clean = val.strip()
        if clean and clean[-1].lower() in ("k", "m", "b", "t"):
            try:
                coeff = float(clean[:-1].replace(",", "").replace(" ", ""))
                if math.isfinite(coeff) and 0 < abs(coeff) < 1_000.0:
                    return clean
            except ValueError:
                pass
    parsed = _parse_short_number(val)
    if parsed is None:
        return val.strip() if isinstance(val, str) else val
    if abs(parsed) >= 1_000.0:
        return _format_number_value(parsed)
    return val.strip() if isinstance(val, str) else val


def _auto_fill_from_ai(
    ai_answer: dict[str, object],
    mode: str,
    palette_theme: str = "auto",
    prompt: str = "",
) -> tuple[dict[str, tuple[int, int, int, int]], dict[tuple[float, float], tuple[int, int, int, int]]]:
    """Inspect AI response values (numbers, short-form numbers, or words) and pick colors."""
    auto_txt: dict[str, tuple[int, int, int, int]] = {}
    auto_num: dict[tuple[float, float], tuple[int, int, int, int]] = {}
    resolved = _resolve_theme_name(palette_theme, prompt=prompt, mode=mode)
    palette = constants._PALETTE_THEMES.get(resolved, constants._AUTO_PALETTE)
    use_semantic = (palette_theme or "auto").strip().lower() == "auto"

    if mode == "number":
        nums: list[float] = []
        for val in ai_answer.values():
            parsed = _parse_short_number(val)
            if parsed is not None:
                nums.append(parsed)
        if nums:
            nums.sort()
            unique_nums = sorted(set(nums))
            low_start = min(0, nums[0] - 1) if nums[0] <= 10 else nums[0] - 1
            if low_start >= nums[0]:
                low_start = math.nextafter(nums[0], -math.inf)
            if len(unique_nums) <= 5:
                bounds = [low_start] + unique_nums
                buckets = len(unique_nums)
            else:
                # Quantiles can repeat when countries share a measurement.
                # Deduplicate thresholds instead of adding 1, which erases
                # sub-unit differences and can push bounds above the maximum.
                thresholds = {
                    nums[int(round((len(nums) - 1) * (i / 5)))]
                    for i in range(1, 5)
                }
                bounds = [low_start] + sorted(thresholds - {nums[-1]}) + [nums[-1]]
                buckets = len(bounds) - 1
            bounds = [int(v) if math.isfinite(v) and float(v).is_integer() else v for v in bounds]
            for i in range(buckets):
                auto_num[(bounds[i], bounds[i + 1])] = palette[i % len(palette)]
    else:
        labels = [str(v).strip() for v in ai_answer.values() if v is not None and str(v).strip()]
        parsed_pairs = [(lbl, _parse_short_number(lbl)) for lbl in labels]
        numeric_vals = sorted(num for _, num in parsed_pairs if num is not None)

        # In text mode with short_form (e.g. '10k', '2.5M'), group numeric labels into
        # gradient buckets instead of giving every distinct number its own random color.
        if numeric_vals and len(numeric_vals) >= max(2, int(len(labels) * 0.6)):
            buckets = min(5, max(1, len(set(numeric_vals))))
            thresholds = [
                numeric_vals[int(round((len(numeric_vals) - 1) * (i / buckets)))]
                for i in range(1, buckets)
            ]
            for lbl, num in parsed_pairs:
                if num is not None:
                    b_idx = sum(1 for t in thresholds if num > t)
                    auto_txt[lbl] = palette[b_idx % len(palette)]
                else:
                    auto_txt.setdefault(lbl, constants._SEMANTIC_COLORS.get(lbl.lower(), palette[-1]))
        else:
            canonical: dict[str, str] = {}
            for lbl in labels:
                canonical.setdefault(lbl.lower(), lbl)
            idx = 0
            for low_lbl, _ in Counter(lbl.lower() for lbl in labels).most_common():
                orig = canonical[low_lbl]
                if use_semantic and low_lbl in constants._SEMANTIC_COLORS:
                    auto_txt[orig] = constants._SEMANTIC_COLORS[low_lbl]
                else:
                    auto_txt[orig] = palette[idx % len(palette)]
                    idx += 1

    return auto_txt, auto_num


def _numeric_extrema(values):
    """Return region names for finite numeric extrema, or None if absent."""
    numeric = {}
    for name, value in values.items():
        number = _parse_short_number(value)
        if number is not None:
            numeric[name] = number
    if not numeric:
        return None
    return max(numeric, key=numeric.get), min(numeric, key=numeric.get)


def _format_legend_bound(val: float) -> str:
    """Format a numeric legend bound compactly so ranges fit in open sea space."""
    abs_val = abs(val)
    if abs_val >= 1_000_000_000:
        scaled, suffix = val / 1_000_000_000, "B"
    elif abs_val >= 1_000_000:
        scaled, suffix = val / 1_000_000, "M"
    elif abs_val >= 10_000:
        scaled, suffix = val / 1_000, "k"
    elif float(val).is_integer():
        return str(int(val))
    else:
        return f"{val:g}"
    rounded = round(scaled, 1)
    if float(rounded).is_integer():
        return f"{int(rounded)}{suffix}"
    return f"{rounded:g}{suffix}"


def _format_legend_range(lo: float, hi: float) -> str:
    """Format a ``(lo, hi)`` numeric interval for the floating map legend."""
    if lo <= -1e12 and hi >= 1e12:
        return "All"
    if lo <= -1e12:
        return f"< {_format_legend_bound(hi)}"
    if hi >= 1e12:
        return f"{_format_legend_bound(lo)}+"
    return f"{_format_legend_bound(lo)} - {_format_legend_bound(hi)}"


def _draw_legend_overlay(
    size: tuple[int, int],
    entries: list[tuple[tuple[int, int, int, int], str]],
    get_font,
    label_fill: tuple[int, int, int, int],
    label_stroke_fill: tuple[int, int, int, int],
    label_stroke_width: int,
    map_type: str,
    has_most_least: bool,
) -> Image.Image | None:
    """Render a floating color legend in the open sea on the left side of the map."""
    if not entries:
        return None
    w, h = size
    is_world = map_type in ("world", "world_countries")
    if is_world:
        x = int(w * 0.025)
        start_y = int(h * (0.44 if has_most_least else 0.56))
        max_h = int(h * (0.34 if has_most_least else 0.40))
        max_w = int(w * 0.22)
        base_size = max(20, int(h * 0.032))
        min_size = 14
    else:
        x = 80
        start_y = 1640 if has_most_least else 1240
        max_h = max(300, 2750 - start_y)
        max_w = 560
        base_size = 86
        min_size = 32

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    count = len(entries)
    font_size = base_size
    while font_size > min_size:
        fits_height = count * int(font_size * 1.32) <= max_h
        swatch_try = max(14, int(font_size * 0.76))
        gap_try = max(10, int(font_size * 0.30))
        font_try = get_font(font_size)
        widest = max(measure.textlength(text, font=font_try) for _, text in entries)
        fits_width = swatch_try + gap_try + widest <= max_w
        if fits_height and fits_width:
            break
        font_size -= 4

    row_step = int(font_size * 1.28)
    swatch = max(14, int(font_size * 0.76))
    gap = max(10, int(font_size * 0.30))
    radius = max(4, swatch // 5)
    stroke_w = min(label_stroke_width, max(2, font_size // 5)) if label_stroke_width > 0 else 0
    outline_w = max(2, min(6, font_size // 14))

    layer = Image.new("RGBA", size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    font = get_font(font_size)

    for idx, (color, text) in enumerate(entries):
        row_y = start_y + idx * row_step
        if row_y + swatch > h - 10:
            break
        swatch_y = row_y + max(0, (font_size - swatch) // 2)
        rgba = (int(color[0]), int(color[1]), int(color[2]), 255)
        draw.rounded_rectangle(
            (x, swatch_y, x + swatch, swatch_y + swatch),
            radius=radius,
            fill=rgba,
            outline=label_stroke_fill,
            width=outline_w,
        )
        draw.text(
            (x + swatch + gap, row_y),
            text,
            font=font,
            fill=label_fill,
            stroke_width=stroke_w,
            stroke_fill=label_stroke_fill,
        )
    return layer


_RESERVED_HIGHLIGHT_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 229, 255),   # Electric cyan (highest)
    (255, 45, 170),  # Neon magenta (lowest)
)


def _random_region_color() -> tuple[int, int, int, int]:
    """Pick a random bright RGBA color that stays distinct from reserved highlight colors."""
    for _ in range(16):
        c = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255))
        if all(sum((c[i] - r[i]) ** 2 for i in range(3)) >= 90 ** 2 for r in _RESERVED_HIGHLIGHT_COLORS):
            return (*c, 255)
    return (120, 180, 120, 255)


def _build_extrema_highlight_layer(
    size: tuple[int, int],
    highest_idx: int | None,
    lowest_idx: int | None,
    map_type: str,
    dict_: dict,
    flat: np.ndarray | None,
    ids: np.ndarray | None,
    borders: Image.Image | None,
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
    border_work = borders.copy() if (map_type == "world_countries" and borders is not None) else None
    border_orig_alpha = np.asarray(borders)[..., 3] if border_work is not None else None

    for region_idx, rgb in targets:
        mask: Image.Image | None = None
        if flat is not None and ids is not None:
            pix_mask = np.zeros(w * h, dtype=np.uint8)
            pix_mask[flat[ids == region_idx]] = 255
            if pix_mask.any():
                mask = Image.fromarray(pix_mask.reshape((h, w)), mode="L")
        elif border_work is not None and 1 <= region_idx <= len(coords_list):
            work = border_work.copy()
            for coord in coords_list[region_idx - 1]:
                ImageDraw.floodfill(work, xy=tuple(coord), value=(255, 255, 255, 255), thresh=60)
            work_arr = np.asarray(work)
            filled = (work_arr[..., 3] == 255) & (border_orig_alpha < 60)
            if filled.any():
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
        tint_alpha = mask.point(lambda p: 75 if p else 0)
        combined_alpha = ImageChops.lighter(tint_alpha, glow_alpha)

        overlay = Image.new("RGBA", size, (*rgb, 255))
        overlay.putalpha(combined_alpha)
        layer = Image.alpha_composite(layer, overlay)

    return layer


def auto_map(prompt: str, mode: str, map_type: str, size_mod: int,
             optional_feature: str, model: str, font_name: str,
             show_result: bool = True, io_res = False, api_key: str | None = None,
             *,
             filling_txt_override: dict[str, object] | None = None,
             filling_num_override: object | None = None,
             label_style: dict[str, object] | None = None,
             auto_style: bool | None = None,
             palette_theme: str = "auto",
             ai_answer_override: dict[str, object] | None = None,
             temperature: float | None = None,
             web_search: bool = False,
             max_search_requests: int = 5,
             show_legend: bool = False,
             highlight_extrema: bool = False,
             highlight_high_color: str = "#00e5ff",
             highlight_low_color: str = "#ff2daa",
             progress_callback=None):
    """Create a map from an AI query and save it as ``result.png``.

    CLI options:
      mode             -- "text" or "number"
      map_type         -- "default", "flag", "world" or "world_countries"
      size_mod         -- global font size modifier (default 15)
      optional_feature -- None, "show_most_least", "short_form", or comma-separated flags
      model            -- Gemini model id
      font_name        -- font file name from resources/fonts
      show_result      -- open the result in the OS image viewer (GUI only)
      io_res           -- save result in io (for web gui)
      api_key          -- optional Gemini API key (overrides env var for this run)
      temperature      -- optional AI sampling randomness, from 0 to 2
    """

    # Reuse font objects across regions that share a label size.
    _font_cache = {}

    def get_font(size):
        key = (font_name, size)
        if key not in _font_cache:
            font_path = constants.FONTS_DIR / font_name
            _font_cache[key] = ImageFont.truetype(str(font_path), size)
        return _font_cache[key]

    req_txt, req_num = _parse_filling_overrides(filling_txt_override, filling_num_override)
    has_txt_override = filling_txt_override is not None
    has_num_override = filling_num_override is not None
    if has_txt_override and has_num_override:
        filling_txt, filling_num = dict(req_txt), dict(req_num)
    else:
        disk_txt, disk_num = load_filling()  # Loading filling from JSON
        filling_txt = dict(req_txt) if has_txt_override else disk_txt
        filling_num = dict(req_num) if has_num_override else disk_num

    # Case-insensitive lookups for text mode. Keys from the JSON are trimmed and
    # lower-cased so that AI answers like "Yes", "yes" or " yes " all match.
    filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}

    ls = label_style if isinstance(label_style, dict) else {}
    label_fill = _hex_to_rgba(ls.get("text_color"), (255, 255, 255, 255))
    label_stroke_fill = _hex_to_rgba(ls.get("stroke_color"), (0, 0, 0, 255))
    try:
        raw_sw = ls.get("stroke_width", 15)
        label_stroke_width = max(0, min(40, int(15 if raw_sw is None else raw_sw)))
    except (ValueError, TypeError, OverflowError):
        label_stroke_width = 15
    label_auto_fit = bool(ls.get("auto_fit", False)) if "auto_fit" in ls else False
    try:
        raw_offset = ls.get('offset_x', 0)
        offset = float(raw_offset) if not isinstance(raw_offset, bool) else 0
        label_offset_x = max(-500, min(500, round(offset))) if math.isfinite(offset) else 0
    except (TypeError, ValueError, OverflowError):
        label_offset_x = 0

    def _parse_optional_features(value: object) -> set[str]:
        """Normalize ``optional_feature`` (string or collection) into a set of active flags."""
        if not value:
            return set()
        if isinstance(value, (list, tuple, set, frozenset)):
            tokens = {str(item).strip().lower() for item in value}
        else:
            raw = str(value).strip().lower()
            if raw == "both":
                return {"show_most_least", "short_form"}
            tokens = {part.strip() for part in re.split(r"[,+\s]+", raw) if part.strip()}
        tokens.discard("none")
        return tokens

    features = _parse_optional_features(optional_feature)
    has_most_least = "show_most_least" in features
    has_short_form = "short_form" in features

    if has_most_least and mode == "text":
        showwarning("Warning", "Most/least feature may be incompatible with text mode. Turning off...")
        has_most_least = False

    if has_most_least and map_type == "world":
        showwarning("Warning", "Most/Least feature is not available for world map. It will be disabled.")
        has_most_least = False

    if has_short_form and ai_answer_override is None:
        prompt += " Write in English short form only (k, M, B, T), for example 1000=1k, 1000000=1M, 1000000000=1B, 1000000000000=1T etc."

    random_colors = {}

    log.step("Starting map generation")
    log.config_table({
        "Mode": mode,
        "Map": map_type,
        "Model": model,
        "Font": font_name,
    })

    # map selecting
    wc_label_sizes: dict[str, int] = {}
    if map_type == "default":
        path = Path(__file__).parent.parent / "./resources/maps/default_map.png"
        dict_ = constants.europe_coords
    elif map_type == 'flag':
        path = Path(__file__).parent.parent / "./resources/maps/flag_map.png"
        dict_ = constants.europe_coords
    elif map_type == 'world':
        path = Path(__file__).parent.parent / "./resources/maps/world_map.png"
        dict_ = constants.world_coords
    elif map_type == 'world_countries':
        path = Path(__file__).parent.parent / "./resources/maps/world_countries_map.png"
        dict_, wc_label_sizes = _load_world_countries()
    else:
        showerror("Error", f"Unknown map type: {map_type}")
        return

    # opening selected map
    log.step(f"Loading base map: {path.name}")
    img = _load_rgba(path)  # shared cached copy; never mutated in place
    log.info(f"Base map size: {img.size[0]}x{img.size[1]}")

    # Keep labels separate so later flood fills and overlays cannot paint over them.
    text_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(text_layer)

    def _report(stage: int, label: str) -> None:
        if progress_callback is not None:
            progress_callback(stage, label)

    _report(1, "Querying AI model...")
    if ai_answer_override is not None:
        ai_answer = dict(ai_answer_override)
    else:
        # Requesting information from AI
        log.step("Requesting data from AI")
        ai_options = {} if temperature is None else {'temperature': temperature}
        if web_search:
            ai_options["web_search"] = True
            ai_options["max_search_requests"] = max_search_requests
        ai_answer = ai_request(prompt, map_type, model, api_key, **ai_options)

    if ai_answer is None:
        showerror("Error", "No information or bad info received from AI")
        return

    raw_ai_answer = dict(ai_answer)
    if has_short_form:
        ai_answer = {k: _format_short_number(v) for k, v in ai_answer.items()}

    log.success(f"Got information from AI ({len(ai_answer)} entries)")

    resolved_theme = _resolve_theme_name(palette_theme, prompt=prompt, mode=mode)
    auto_txt, auto_num = _auto_fill_from_ai(
        ai_answer,
        mode,
        palette_theme=palette_theme,
        prompt=prompt,
    )
    theme_requested = (palette_theme or "auto").strip().lower() != "auto"

    if auto_style is True:
        if mode == "text":
            merged_txt = dict(auto_txt)
            req_keys_low = {k.strip().lower() for k in req_txt}
            if req_txt and (not theme_requested or req_keys_low != constants._DEFAULT_UI_TXT_KEYS):
                ci_to_orig = {k.strip().lower(): k for k in merged_txt}
                for rk, rv in req_txt.items():
                    short_rk = str(_format_short_number(rk)).strip() if has_short_form else rk
                    target_k = ci_to_orig.get(short_rk.lower(), ci_to_orig.get(rk.strip().lower(), short_rk))
                    merged_txt[target_k] = rv
            filling_txt = merged_txt
            filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}
        elif mode == "number":
            if req_num and set(req_num.keys()) != constants._DEFAULT_UI_NUM_BOUNDS and not theme_requested:
                filling_num = dict(req_num)
            elif auto_num:
                filling_num = dict(auto_num)
    elif auto_style is False:
        # Honor the request's explicit rules without overwriting them with auto_fill.
        pass
    else:
        if not filling_txt and auto_txt:
            filling_txt = auto_txt
            filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}
        if not filling_num and auto_num:
            filling_num = auto_num

    if has_short_form:
        for k, v in list(filling_txt.items()):
            short_k = str(_format_short_number(k)).strip().lower()
            if short_k:
                filling_txt_ci.setdefault(short_k, v)

    most_least = None
    extrema = _numeric_extrema(raw_ai_answer) if has_most_least else None
    if has_most_least and extrema is None:
        log.warning("No numeric values available; skipping most / least overlay")
    if extrema is not None:
        log.step("Computing most / least values")
        most_least = Image.new("RGBA", img.size, (255, 255, 255, 0))
        ml_draw = ImageDraw.Draw(most_least)
        '''Writing the most and the least country'''
        most_country, least_country = extrema
        log.info(f"Most: {most_country} | Least: {least_country}")

        if map_type == "world_countries":
            ml_xy = (int(img.size[0] * 0.025), int(img.size[1] * 0.80))
            ml_size = max(20, int(img.size[1] * 0.036))
            ml_stroke = min(label_stroke_width, max(2, ml_size // 5)) if label_stroke_width > 0 else 0
            ml_text = (
                f"Most: {most_country.replace('_', ' ').title()}\n"
                f"Least: {least_country.replace('_', ' ').title()}"
            )
        else:
            ml_xy = (106, 1240)
            ml_size = 150
            ml_stroke = label_stroke_width
            ml_text = f'Most: {most_country.capitalize()}\nLeast: {least_country.capitalize()}'

        ml_draw.text(ml_xy,
                     ml_text,
                     font=get_font(ml_size),
                     fill=label_fill,
                     stroke_width=ml_stroke,
                     stroke_fill=label_stroke_fill)

    total = len(dict_)
    _report(2, "Painting regions & labels...")
    log.step(f"Painting {total} regions")
    used_txt_rules: dict[str, tuple[int, int, int, int]] = {}
    used_num_rules: dict[tuple[float, float], tuple[int, int, int, int]] = {}

    if map_type == "world_countries":
        ai_answer_norm = _world_countries_answer_norm(ai_answer or {}, dict_.keys())
        raw_ai_answer_norm = _world_countries_answer_norm(raw_ai_answer or {}, dict_.keys())
    else:
        ai_answer_norm = {
            str(k).strip().lower().replace(" ", "_").replace("-", "_"): v
            for k, v in (ai_answer or {}).items()
        }
        raw_ai_answer_norm = {
            str(k).strip().lower().replace(" ", "_").replace("-", "_"): v
            for k, v in (raw_ai_answer or {}).items()
        }
        if "oceania" in ai_answer_norm and "australia" not in ai_answer_norm:
            ai_answer_norm["australia"] = ai_answer_norm["oceania"]
        if "oceania" in raw_ai_answer_norm and "australia" not in raw_ai_answer_norm:
            raw_ai_answer_norm["australia"] = raw_ai_answer_norm["oceania"]

    region_colors: dict[int, tuple[int, int, int, int]] = {}
    numeric_region_values: dict[int, float] = {}

    for index, (name, points) in enumerate(dict_.items(), start=1):
        name_key = (
            _country_key(name)
            if map_type == "world_countries"
            else str(name).strip().lower().replace(" ", "_").replace("-", "_")
        )
        '''Number mode'''
        if mode == 'number':
            try:
                info = ai_answer_norm[name_key]
                color = _random_region_color()
                num_value = _parse_short_number(info)
                raw_num_value = _parse_short_number(raw_ai_answer_norm.get(name_key))
                if num_value is None:
                    log.warning(f"Non-numeric value for {name}: {info!r}; using random color")

                if num_value is not None:
                    if highlight_extrema:
                        numeric_region_values[index] = float(raw_num_value if raw_num_value is not None else num_value)
                    # Pick the colour of the first matching range. Ranges are
                    # treated as (low < value <= high) per the docs.
                    matched_bounds = None
                    for candidate in (num_value, raw_num_value):
                        if candidate is None or matched_bounds is not None:
                            continue
                        for (low_lim, high_lim), color_tup in filling_num.items():
                            if low_lim < candidate <= high_lim:
                                color = color_tup
                                matched_bounds = (low_lim, high_lim)
                                break
                    if matched_bounds is None and auto_style is True and auto_num:
                        for (low_lim, high_lim), color_tup in auto_num.items():
                            if low_lim < num_value <= high_lim:
                                color = color_tup
                                matched_bounds = (low_lim, high_lim)
                                break
                    if matched_bounds is not None:
                        used_num_rules[matched_bounds] = color
            except KeyError as e:
                info = ""
                log.warning(f"No data / color for {e}; using random color")
                color = _random_region_color()
                random_colors[name] = color

        elif mode == 'text':
            '''Text mode'''
            raw_info = ai_answer_norm.get(name_key)
            info = "" if raw_info is None else str(raw_info).strip()
            if not info:
                log.info(f"No information for {name}, skipping")
                continue
            try:
                # Match filling colours case-insensitively and ignoring whitespace,
                # so "Yes", "yes" and " yes " all find the same colour.
                color = filling_txt_ci[info.lower()]
            except KeyError:
                if map_type in constants._PAINTED_MAP_TYPES:
                    log.info(f"No filling color for '{info}'; using random")
                    # Repeated answers share a color so categories remain recognizable.
                    try:
                        color = random_colors[info]
                    except KeyError:
                        color = _random_region_color()
                        random_colors[info] = color
                else:
                    color = _random_region_color()
            used_txt_rules.setdefault(info, color)
        else:
            showerror("Error", f"Unknown mode: {mode}")
            return

        log.info(f"[{index}/{total}] Painting {name} -> {info}")

        # Flag maps retain their original artwork. Other maps use every seed point
        # to fill disconnected pieces of a region, such as islands.
        # Painting is applied after the loop from a cached region-ID map.
        if map_type in constants._PAINTED_MAP_TYPES:
            region_colors[index] = tuple(int(c) for c in color)

        if not points:
            continue

        # The first fill seed also anchors the region's single label.
        x, y = points[0]
        x += label_offset_x

        info = str(info)

        if map_type == 'flag' or map_type == 'default':
            if len(info) >= 4:
                x -= 70
            elif len(info) >= 2:
                x -= 30

            size = constants._EUROPE_LABEL_SIZES.get(name, 125)
            if name in ('russia', 'usa'):
                y -= 10
            elif name in ('north_macedonia', 'bosnia'):
                y -= 25

            if len(info) == 1:
                y -= 20
                size += 25

            if label_auto_fit and len(info) >= 6:
                size = max(45, int(size * max(0.55, 5.0 / len(info))))

            size = max(20, size + size_mod)  # Global size modifier for other fonts. Main font is BIPS

            font = get_font(size)  # getting font from cache

            draw.text((x, y),
                      info,
                      font=font,
                      fill=label_fill,
                      stroke_width=label_stroke_width,
                      stroke_fill=label_stroke_fill)

        elif map_type == 'world':  # text size for World map
            size = constants._WORLD_LABEL_SIZES.get(name, 100)

            if label_auto_fit and len(info) >= 6:
                size = max(40, int(size * max(0.55, 5.0 / len(info))))

            size = max(20, size + size_mod)  # Global size modifier for other fonts. Main font is BIPS

            draw.text((x, y),
                      info,
                      font=get_font(size),
                      fill=label_fill,
                      stroke_width=label_stroke_width,
                      stroke_fill=label_stroke_fill)

        elif map_type == 'world_countries':
            base_size = wc_label_sizes.get(name)
            if base_size is None:
                base_size = wc_label_sizes.get(name_key, 0)
            if base_size and base_size >= constants._WORLD_COUNTRIES_MIN_LABEL:
                size = base_size
                if label_auto_fit and len(info) >= 6:
                    size = max(14, int(size * max(0.55, 5.0 / len(info))))
                size = max(12, size + size_mod)
                # Cap outline thickness for compact country labels so strokes stay legible.
                stroke_w = min(label_stroke_width, max(2, size // 5)) if label_stroke_width > 0 else 0
                draw.text((x, y),
                          info,
                          font=get_font(size),
                          fill=label_fill,
                          stroke_width=stroke_w,
                          stroke_fill=label_stroke_fill,
                          anchor="mm")

    effective_txt = dict(used_txt_rules) if used_txt_rules else dict(filling_txt or auto_txt)
    seen_txt_lower = {str(uk).strip().lower() for uk in effective_txt}
    for k, v in (filling_txt or {}).items():
        k_str = str(k)
        k_low = k_str.strip().lower()
        if k_low not in seen_txt_lower:
            effective_txt[k_str] = v
            seen_txt_lower.add(k_low)
    effective_num = dict(filling_num or auto_num)
    for bounds, c in used_num_rules.items():
        effective_num[bounds] = c

    render_meta = {
        "ai_answer": dict(ai_answer),
        "raw_ai_answer": raw_ai_answer,
        "mode": mode,
        "resolved_theme": resolved_theme,
        "filling_txt": {str(k): list(v) for k, v in effective_txt.items()},
        "filling_num": {f"({lo}, {hi})": list(v) for (lo, hi), v in effective_num.items()},
        "used_txt": {str(k): list(v) for k, v in (used_txt_rules or effective_txt).items()},
        "used_num": {f"({lo}, {hi})": list(v) for (lo, hi), v in (used_num_rules or effective_num).items()},
        "label_style": {
            "text_color": f"#{label_fill[0]:02x}{label_fill[1]:02x}{label_fill[2]:02x}",
            "stroke_color": f"#{label_stroke_fill[0]:02x}{label_stroke_fill[1]:02x}{label_stroke_fill[2]:02x}",
            "stroke_width": label_stroke_width,
            "auto_fit": label_auto_fit,
            "offset_x": label_offset_x,
            "recommended_font": font_name or ("Bips.ttf" if mode == "number" else "DIN Condensed.ttf"),
            "recommended_gsm": size_mod,
        },
    }

    flat = ids = None
    if map_type in constants._PAINTED_MAP_TYPES:
        flat, ids = _region_index(path, img, dict_)
        img = _paint_regions(img, flat, ids, region_colors)

    highest_idx = lowest_idx = None
    if highlight_extrema and mode == "number" and numeric_region_values:
        highest_idx = max(numeric_region_values, key=numeric_region_values.get)
        lowest_idx = min(numeric_region_values, key=numeric_region_values.get)

    # adding relief map
    _report(3, "Adding relief, borders & overlays...")
    log.step("Compositing layers")
    _layer_assets = {
        "default": ("map_relief.png", "default_map_blured.png"),
        "flag": ("map_relief.png", "default_map_blured.png"),
        "world": ("world_map_relief.png", "world_map_blured.png"),
        "world_countries": ("world_map_relief.png", "world_countries_map_blured.png"),
    }
    if map_type in _layer_assets:
        relief_name, borders_name = _layer_assets[map_type]
        log.info("Adding relief + borders overlay")
        # Relief is resized and its opacity reduced once per process.
        maps_dir = Path(__file__).parent.parent / "resources" / "maps"
        relief = _prepared_relief(maps_dir / relief_name, img.size)
        result = Image.alpha_composite(img, relief)

        # Restore borders above the relief, then put readable labels on top.
        borders = _load_rgba(maps_dir / borders_name)
        if highest_idx is not None:
            high_rgb = _hex_to_rgba(highlight_high_color, (*_RESERVED_HIGHLIGHT_COLORS[0], 255))[:3]
            low_rgb = _hex_to_rgba(highlight_low_color, (*_RESERVED_HIGHLIGHT_COLORS[1], 255))[:3]
            hl_layer = _build_extrema_highlight_layer(
                img.size, highest_idx, lowest_idx, map_type, dict_, flat, ids, borders,
                high_rgb=high_rgb, low_rgb=low_rgb,
            )
            if hl_layer is not None:
                result = Image.alpha_composite(result, hl_layer)
        result = Image.alpha_composite(result, borders)
        result = Image.alpha_composite(result, text_layer)

        if has_most_least and map_type != "world" and most_least is not None:
            result = Image.alpha_composite(result, most_least)
    else:
        result = Image.alpha_composite(img, text_layer)

    if show_legend:
        if mode == "number":
            legend_src = used_num_rules or effective_num
            legend_entries = [
                (tuple(c), _format_legend_range(lo, hi))
                for (lo, hi), c in sorted(legend_src.items(), key=lambda item: (item[0][0], item[0][1]))
            ]
        else:
            legend_src = used_txt_rules or effective_txt
            legend_entries = [
                (tuple(c), str(label).strip())
                for label, c in legend_src.items()
                if str(label).strip()
            ]
        has_ml = has_most_least and map_type != "world" and most_least is not None
        legend_layer = _draw_legend_overlay(
            img.size,
            legend_entries,
            get_font,
            label_fill,
            label_stroke_fill,
            label_stroke_width,
            map_type,
            has_ml,
        )
        if legend_layer is not None:
            result = Image.alpha_composite(result, legend_layer)


    _report(4, "Encoding final PNG...")
    if io_res:
        # Return an in-memory PNG for the web API instead of writing result.png.

        buffer = io.BytesIO()
        result.save(buffer, format='PNG')
        # Consumers should be able to read the PNG from its first byte.
        buffer.seek(0)
        buffer.map_meta = render_meta

        return buffer


    out_path = Path(__file__).parent.parent / "result.png"
    log.step(f"Saving result to {out_path}")
    result.save(out_path)

    if show_result:
        result.show("Map")

    log.finished(f"Finished! Map saved as {out_path}")

    return out_path
