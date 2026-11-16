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

from .constants import europe_coords, world_coords
from .functions import ai_request, load_filling, _warning as showwarning, _error as showerror
from . import log

FONTS_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"

_AUTO_PALETTE: tuple[tuple[int, int, int, int], ...] = (
    (56, 189, 248, 255),   # #38bdf8 Sky
    (99, 102, 241, 255),   # #6366f1 Indigo
    (16, 185, 129, 255),   # #10b981 Emerald
    (245, 158, 11, 255),   # #f59e0b Amber
    (244, 63, 94, 255),    # #f43f5e Rose
    (168, 85, 247, 255),   # #a855f7 Purple
    (20, 184, 166, 255),   # #14b8a6 Teal
    (236, 72, 153, 255),   # #ec4899 Pink
    (234, 179, 8, 255),    # #eab308 Gold
    (249, 115, 22, 255),   # #f97316 Orange
)

_PALETTE_THEMES: dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "auto": _AUTO_PALETTE,
    "viridis": (
        (68, 1, 84, 255), (59, 82, 139, 255), (33, 145, 140, 255),
        (94, 201, 98, 255), (253, 231, 37, 255),
    ),
    "sunset": (
        (124, 45, 18, 255), (194, 65, 12, 255), (234, 88, 12, 255),
        (245, 158, 11, 255), (253, 224, 71, 255),
    ),
    "ocean": (
        (12, 74, 110, 255), (3, 105, 161, 255), (2, 132, 199, 255),
        (56, 189, 248, 255), (125, 211, 252, 255),
    ),
    "festive": (
        (220, 38, 38, 255), (22, 163, 74, 255), (234, 179, 8, 255),
        (37, 99, 235, 255), (147, 51, 234, 255),
    ),
    "emerald": (
        (6, 78, 59, 255), (4, 120, 87, 255), (16, 185, 129, 255),
        (52, 211, 153, 255), (167, 243, 208, 255),
    ),
    "neon": (
        (236, 72, 153, 255), (139, 92, 246, 255), (6, 182, 212, 255),
        (16, 185, 129, 255), (250, 204, 21, 255),
    ),
    "pastel": (
        (147, 197, 253, 255), (196, 181, 253, 255), (167, 243, 208, 255),
        (253, 230, 138, 255), (253, 164, 175, 255),
    ),
    "monochrome": (
        (51, 65, 85, 255), (71, 85, 105, 255), (100, 116, 139, 255),
        (148, 163, 184, 255), (203, 213, 225, 255),
    ),
    "heatmap": (
        (37, 99, 235, 255), (16, 185, 129, 255), (245, 158, 11, 255),
        (239, 68, 68, 255), (185, 28, 28, 255),
    ),
    "emerald_gold": (
        (244, 63, 94, 255), (245, 158, 11, 255), (234, 179, 8, 255),
        (16, 185, 129, 255), (5, 150, 105, 255),
    ),
    "oceanic": (
        (56, 189, 248, 255), (14, 165, 233, 255), (37, 99, 235, 255),
        (99, 102, 241, 255), (139, 92, 246, 255),
    ),
    "categorical": _AUTO_PALETTE + (
        (14, 165, 233, 255), (132, 204, 22, 255),
    ),
    "greens": (
        (187, 247, 208, 255), (134, 239, 172, 255), (74, 222, 128, 255),
        (22, 163, 74, 255), (20, 83, 45, 255),
    ),
    "blues": (
        (191, 219, 254, 255), (147, 197, 253, 255), (96, 165, 250, 255),
        (37, 99, 235, 255), (30, 58, 138, 255),
    ),
    "reds": (
        (254, 202, 202, 255), (252, 165, 165, 255), (248, 113, 113, 255),
        (220, 38, 38, 255), (127, 29, 29, 255),
    ),
    "oranges": (
        (254, 215, 170, 255), (253, 186, 116, 255), (251, 146, 60, 255),
        (234, 88, 12, 255), (124, 45, 18, 255),
    ),
    "yellows": (
        (254, 240, 138, 255), (253, 224, 71, 255), (250, 204, 21, 255),
        (202, 138, 4, 255), (133, 77, 14, 255),
    ),
    "purples": (
        (233, 213, 255, 255), (216, 180, 254, 255), (192, 132, 252, 255),
        (147, 51, 234, 255), (88, 28, 135, 255),
    ),
    "teals": (
        (153, 246, 228, 255), (94, 234, 212, 255), (45, 212, 191, 255),
        (13, 148, 136, 255), (19, 78, 74, 255),
    ),
    "pinks": (
        (251, 207, 232, 255), (249, 168, 212, 255), (244, 114, 182, 255),
        (219, 39, 119, 255), (131, 24, 67, 255),
    ),
    "earth_tones": (
        (231, 229, 176, 255), (194, 178, 128, 255), (163, 137, 87, 255),
        (120, 113, 62, 255), (75, 85, 45, 255),
    ),
}

_SEMANTIC_COLORS: dict[str, tuple[int, int, int, int]] = {
    "yes": (46, 204, 113, 255), "так": (46, 204, 113, 255), "true": (46, 204, 113, 255),
    "no": (231, 76, 60, 255), "ні": (231, 76, 60, 255), "false": (231, 76, 60, 255),
    "tea": (16, 185, 129, 255), "coffee": (245, 158, 11, 255), "coffe": (245, 158, 11, 255),
    "beer": (234, 179, 8, 255), "wine": (225, 29, 72, 255),
}

_DEFAULT_UI_TXT_KEYS = {"yes", "no", "tea", "coffee"}
_DEFAULT_UI_NUM_BOUNDS = {(0, 100), (100, 1000), (1000, 10000), (10000, 100000000)}
_RELIEF_ALPHA_LUT: list[int] = [int(p * 0.3) for p in range(256)]

_EUROPE_LABEL_SIZES: dict[str, int] = {
    **{k: 70 for k in (
        "cyprus", "kosovo", "montenegro", "slovenia", "latvia", "lithuania",
        "estonia", "switzerland", "austria", "slovakia", "hungary", "croatia",
        "netherlands", "belgium", "czechia", "moldova", "bosnia_and_herzegovina",
        "serbia", "bulgaria", "albania", "greece", "ireland", "iceland",
        "norway", "finland", "sweden", "denmark", "portugal",
    )},
    **{k: 100 for k in ("sweden", "norway", "finland")},
    **{k: 125 for k in ("north_macedonia", "bosnia")},
    **{k: 175 for k in (
        "ukraine", "poland", "france", "spain", "turkey",
        "romania", "italy", "belarus", "united_kingdom", "germany",
    )},
    **{k: 250 for k in ("russia", "usa")},
}

_WORLD_LABEL_SIZES: dict[str, int] = {
    "asia": 200,
    "africa": 100,
    "europe": 110,
    "australia": 50,
    "south_america": 100,
    "north_america": 120,
}

# Map types whose regions are painted from the cached region-ID map.
# Flag-filled maps (``flag``, ``world_countries``) keep their underlying flag artwork.
_PAINTED_MAP_TYPES: frozenset[str] = frozenset({"default", "world"})

# Smallest base label size (before the global size modifier) still drawn on the
# world countries map; smaller countries stay unlabeled to keep the map readable.
_WORLD_COUNTRIES_MIN_LABEL = 1

# Common alternative AI spellings mapped to canonical snake_case country keys.
# Applied only when the alias target exists in the coordinates dictionary.
_WORLD_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "usa": ("united_states", "united_states_of_america"),
    "us": ("united_states", "united_states_of_america"),
    "united_states_of_america": ("united_states",),
    "america": ("united_states", "united_states_of_america"),
    "uk": ("united_kingdom",),
    "great_britain": ("united_kingdom",),
    "britain": ("united_kingdom",),
    "russian_federation": ("russia",),
    "czech_republic": ("czechia",),
    "turkiye": ("turkey",),
    "ivory_coast": ("cote_d_ivoire", "cote_divoire"),
    "cote_d_ivoire": ("ivory_coast",),
    "cote_divoire": ("ivory_coast", "cote_d_ivoire"),
    "drc": ("democratic_republic_of_the_congo", "dr_congo"),
    "dr_congo": ("democratic_republic_of_the_congo",),
    "congo_kinshasa": ("democratic_republic_of_the_congo", "dr_congo"),
    "democratic_republic_of_congo": ("democratic_republic_of_the_congo", "dr_congo"),
    "congo_brazzaville": ("republic_of_the_congo", "congo"),
    "republic_of_congo": ("republic_of_the_congo", "congo"),
    "south_korea": ("korea_south", "republic_of_korea"),
    "korea": ("south_korea",),
    "korea_south": ("south_korea",),
    "republic_of_korea": ("south_korea",),
    "north_korea": ("korea_north", "dprk"),
    "korea_north": ("north_korea",),
    "dprk": ("north_korea",),
    "burma": ("myanmar",),
    "swaziland": ("eswatini",),
    "eswatini": ("swaziland",),
    "macedonia": ("north_macedonia",),
    "holland": ("netherlands",),
    "the_netherlands": ("netherlands",),
    "uae": ("united_arab_emirates",),
    "east_timor": ("timor_leste",),
    "timor_leste": ("east_timor",),
    "cabo_verde": ("cape_verde",),
    "cape_verde": ("cabo_verde",),
    "vatican_city": ("vatican",),
    "bosnia": ("bosnia_and_herzegovina",),
    "the_gambia": ("gambia",),
    "the_bahamas": ("bahamas",),
    "united_republic_of_tanzania": ("tanzania",),
    "republic_of_serbia": ("serbia",),
    "state_of_palestine": ("palestine",),
    "brunei_darussalam": ("brunei",),
    "viet_nam": ("vietnam",),
    "lao_pdr": ("laos",),
}


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
    for alias, targets in _WORLD_COUNTRY_ALIASES.items():
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
    relief.putalpha(a.point(_RELIEF_ALPHA_LUT))
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
    if theme in _PALETTE_THEMES and theme != "auto":
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
    palette = _PALETTE_THEMES.get(resolved, _AUTO_PALETTE)
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
                    auto_txt.setdefault(lbl, _SEMANTIC_COLORS.get(lbl.lower(), palette[-1]))
        else:
            canonical: dict[str, str] = {}
            for lbl in labels:
                canonical.setdefault(lbl.lower(), lbl)
            idx = 0
            for low_lbl, _ in Counter(lbl.lower() for lbl in labels).most_common():
                orig = canonical[low_lbl]
                if use_semantic and low_lbl in _SEMANTIC_COLORS:
                    auto_txt[orig] = _SEMANTIC_COLORS[low_lbl]
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
             progress_callback=None):
    """Create a map from an AI query and save it as ``result.png``.

    CLI options:
      mode             -- "text" or "number"
      map_type         -- "default", "flag", "world" or "world_countries"
      size_mod         -- global font size modifier (default 15)
      optional_feature -- None, "show_most_least" or "short_form"
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
            font_path = FONTS_DIR / font_name
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
        dict_ = europe_coords
    elif map_type == 'flag':
        path = Path(__file__).parent.parent / "./resources/maps/flag_map.png"
        dict_ = europe_coords
    elif map_type == 'world':
        path = Path(__file__).parent.parent / "./resources/maps/world_map.png"
        dict_ = world_coords
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
            if req_txt and (not theme_requested or req_keys_low != _DEFAULT_UI_TXT_KEYS):
                ci_to_orig = {k.strip().lower(): k for k in merged_txt}
                for rk, rv in req_txt.items():
                    short_rk = str(_format_short_number(rk)).strip() if has_short_form else rk
                    target_k = ci_to_orig.get(short_rk.lower(), ci_to_orig.get(rk.strip().lower(), short_rk))
                    merged_txt[target_k] = rv
            filling_txt = merged_txt
            filling_txt_ci = {str(k).strip().lower(): v for k, v in filling_txt.items()}
        elif mode == "number":
            if req_num and set(req_num.keys()) != _DEFAULT_UI_NUM_BOUNDS and not theme_requested:
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
    extrema = _numeric_extrema(ai_answer) if has_most_least else None
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
                color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
                num_value = _parse_short_number(info)
                raw_num_value = _parse_short_number(raw_ai_answer_norm.get(name_key))
                if num_value is None:
                    log.warning(f"Non-numeric value for {name}: {info!r}; using random color")

                if num_value is not None:
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
                color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
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
                if map_type in _PAINTED_MAP_TYPES:
                    log.info(f"No filling color for '{info}'; using random")
                    # Repeated answers share a color so categories remain recognizable.
                    try:
                        color = random_colors[info]
                    except KeyError:
                        color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
                        random_colors[info] = color
                else:
                    color = (random.randint(80, 255), random.randint(80, 255), random.randint(80, 255), 255)
            used_txt_rules.setdefault(info, color)
        else:
            showerror("Error", f"Unknown mode: {mode}")
            return

        log.info(f"[{index}/{total}] Painting {name} -> {info}")

        # Flag maps retain their original artwork. Other maps use every seed point
        # to fill disconnected pieces of a region, such as islands.
        # Painting is applied after the loop from a cached region-ID map.
        if map_type in _PAINTED_MAP_TYPES:
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

            size = _EUROPE_LABEL_SIZES.get(name, 125)
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
            size = _WORLD_LABEL_SIZES.get(name, 100)

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
            if base_size and base_size >= _WORLD_COUNTRIES_MIN_LABEL:
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

    if map_type in _PAINTED_MAP_TYPES:
        flat, ids = _region_index(path, img, dict_)
        img = _paint_regions(img, flat, ids, region_colors)

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
