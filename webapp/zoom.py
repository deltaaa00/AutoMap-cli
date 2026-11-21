"""Resolve country targets and prepare outline previews and reveal masks.

Video Studio requests normalized country centers and small PNG outlines here;
webapp.media also obtains base64 grayscale reveal masks for the worker. Centers
are normalized to 0..1 against reference artwork dimensions. Binary masks are
L-mode images with 0 outside and 255 inside a region, resized to the map image.

Europe uses indexed region geometry; world-country artwork uses flood filling.
Image/index primitives live in map.geometry. Preview and reveal caches are
process-local and protected by a lock. Keep reference dimensions consistent with
the bundled coordinate tables when replacing artwork.
"""
import base64
import io
import math
import threading
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from flask import Blueprint, request, jsonify, Response
from .paths import BASE_DIR


routes = Blueprint('zoom', __name__)


_ZOOM_REF_DIMENSIONS = {'europe': (3900.0, 4104.0), 'world': (3424.0, 2027.0)}
_ZOOM_OUTLINE_CACHE = {}
_REVEAL_MASK_CACHE = {}
_ZOOM_CACHE_LOCK = threading.Lock()


def _zoom_country_entries(
    coords: dict[str, list[tuple[int, int]]],
    ref_w: float,
    ref_h: float,
) -> list[dict[str, object]]:
    """Build normalized (0..1) center coordinate entries for a country map."""
    return [
        {
            "key": k,
            "name": k.replace("_", " ").title(),
            "x": round(pts[0][0] / ref_w, 4),
            "y": round(pts[0][1] / ref_h, 4),
        }
        for k, pts in coords.items()
        if pts
    ]


def _region_mask_from_index(
    map_path: Path,
    coords: dict[str, list[tuple[int, int]]],
    key: str,
) -> Image.Image | None:
    """Extract a full-resolution L-mode binary mask using ``_region_index``."""
    import numpy as np
    from map.auto_map import _load_rgba, _region_index

    if key not in coords:
        return None
    region_idx = list(coords.keys()).index(key) + 1
    base = _load_rgba(map_path)
    w, h = base.size
    flat, ids = _region_index(map_path, base, coords)
    pix_mask = np.zeros(w * h, dtype=np.uint8)
    pix_mask[flat[ids == region_idx]] = 255
    if not pix_mask.any():
        return None
    return Image.fromarray(pix_mask.reshape((h, w)), mode="L")


def _world_country_floodfill_mask(seeds: list[tuple[int, int]]) -> Image.Image | None:
    """Extract a full-resolution L-mode binary mask on ``world_countries_map.png``."""
    import numpy as np
    from map.auto_map import _floodfill_bool_mask, _load_rgba

    if not seeds:
        return None
    maps_dir = BASE_DIR / "resources" / "maps"
    map_path = maps_dir / "world_countries_map.png"
    base = _load_rgba(map_path)
    w, h = base.size
    base_arr = np.asarray(base)
    wc_base_alpha = base_arr[..., 3]
    wc_dark = (wc_base_alpha >= 128) & (base_arr[..., :3].max(axis=2) <= 12)
    wc_interior_crisp = (wc_base_alpha >= 128) & ~wc_dark
    wc_interior = wc_interior_crisp

    wc_border_alpha = None
    borders_path = maps_dir / "world_countries_map_blured.png"
    if borders_path.is_file():
        borders = _load_rgba(borders_path)
        if borders.size == (w, h):
            wc_border_alpha = np.asarray(borders)[..., 3]
            # Only trust blurred border alpha to bridge inland black flag bands
            # when the border asset actually covers the crisp borders.
            if not wc_dark.any() or float(np.mean(wc_border_alpha[wc_dark] >= 20)) >= 0.8:
                wc_interior = wc_interior_crisp | ((wc_base_alpha >= 128) & (wc_border_alpha < 44))

    max_px = int(0.08 * w * h)
    filled = _floodfill_bool_mask(wc_interior, seeds, max_px)
    if (filled is None or not filled.any()) and wc_border_alpha is not None:
        for thr in (45, 65):
            interior = (wc_base_alpha >= 128) & (wc_border_alpha < thr)
            filled = _floodfill_bool_mask(interior, seeds, max_px)
            if filled is not None and filled.any():
                break
    if filled is None or not filled.any():
        return None
    return Image.fromarray((filled.astype(np.uint8) * 255), mode="L")


def _extract_country_mask_image(
    map_kind: str,
    key: str,
    *,
    allow_world_continent_fallback: bool = False,
) -> Image.Image | None:
    """Return a full-resolution L-mode binary mask for ``key`` on ``europe`` or ``world``."""
    from map.constants import europe_coords, world_coords
    from map.world_countries_coords import world_countries_coords

    maps_dir = BASE_DIR / "resources" / "maps"
    if map_kind == "europe":
        return _region_mask_from_index(maps_dir / "default_map.png", europe_coords, key)
    if map_kind == "world":
        if key in world_countries_coords:
            return _world_country_floodfill_mask(world_countries_coords[key])
        if allow_world_continent_fallback and key in world_coords:
            return _region_mask_from_index(maps_dir / "world_map.png", world_coords, key)
    return None


def _resolve_reveal_country_key(
    coords: dict[str, list[tuple[int, int]]],
    ref_w: float,
    ref_h: float,
    zoom_country_key: str,
    zoom_x: float,
    zoom_y: float,
) -> str:
    """Resolve target country key by exact match or nearest seed within 0.18 distance."""
    candidate = str(zoom_country_key or "").strip().lower()
    if candidate and candidate in coords and coords[candidate]:
        return candidate
    if isinstance(zoom_x, bool) or isinstance(zoom_y, bool):
        return ""
    try:
        zx = float(zoom_x)
        zy = float(zoom_y)
    except (TypeError, ValueError):
        return ""
    if not (math.isfinite(zx) and math.isfinite(zy)):
        return ""
    best_key = ""
    best_dist_sq = 0.18 * 0.18
    for k, pts in coords.items():
        if not pts:
            continue
        dx = (pts[0][0] / ref_w) - zx
        dy = (pts[0][1] / ref_h) - zy
        dist_sq = dx * dx + dy * dy
        if dist_sq <= best_dist_sq:
            best_dist_sq = dist_sq
            best_key = k
    return best_key


def _extract_country_reveal_mask(
    map_w: int,
    map_h: int,
    zoom_country_key: str,
    zoom_x: float,
    zoom_y: float,
) -> str:
    """Extract a country reveal mask as a base64 L-mode PNG resized to ``(map_w, map_h)``."""
    map_w = int(map_w)
    map_h = int(map_h)
    if map_w <= 0 or map_h <= 0:
        return ""

    from map.constants import europe_coords
    from map.world_countries_coords import world_countries_coords

    map_kind = "europe" if (map_w / max(1, map_h)) < 1.25 else "world"
    coords = europe_coords if map_kind == "europe" else world_countries_coords
    ref_w, ref_h = _ZOOM_REF_DIMENSIONS[map_kind]

    key = _resolve_reveal_country_key(
        coords, ref_w, ref_h, zoom_country_key, zoom_x, zoom_y
    )
    if not key:
        return ""

    cache_key = (map_kind, key, map_w, map_h)
    with _ZOOM_CACHE_LOCK:
        cached = _REVEAL_MASK_CACHE.get(cache_key)
    if cached is not None:
        return cached

    mask = _extract_country_mask_image(map_kind, key, allow_world_continent_fallback=False)
    if mask is None:
        return ""
    if mask.size != (map_w, map_h):
        mask = mask.resize((map_w, map_h), Image.Resampling.BILINEAR)
    if mask.mode != "L":
        mask = mask.convert("L")
    from PIL import ImageFilter
    mask = mask.filter(ImageFilter.MaxFilter(5)).point(lambda p: 255 if p > 64 else 0)
    if mask.getbbox() is None:
        return ""

    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    b64_mask = base64.b64encode(buf.getvalue()).decode("ascii")
    with _ZOOM_CACHE_LOCK:
        _REVEAL_MASK_CACHE[cache_key] = b64_mask
    return b64_mask


def _parse_finite_coord(value: object, default: float) -> float:
    """Parse a finite float coordinate, falling back to ``default`` when invalid."""
    if value is None or isinstance(value, bool) or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _decode_image_size(image_data: object) -> tuple[int, int] | None:
    """Decode a base64 or data-URL image payload and return its ``(width, height)``."""
    if not isinstance(image_data, str) or not image_data.strip():
        return None
    raw_b64 = image_data.strip()
    if "," in raw_b64 and raw_b64.lower().startswith("data:"):
        _, raw_b64 = raw_b64.split(",", 1)
    b64_part = "".join(raw_b64.split())
    if not b64_part:
        return None
    try:
        decoded = base64.b64decode(b64_part, validate=True)
    except (ValueError, TypeError):
        return None
    if not decoded:
        return None
    try:
        with Image.open(io.BytesIO(decoded)) as opened:
            return opened.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        return None


@routes.get('/api/zoom_countries')
def api_zoom_countries():
    """Return normalized (0..1) country center coordinates for Europe and World maps."""
    from map.constants import europe_coords, world_coords
    from map.world_countries_coords import world_countries_coords

    europe = _zoom_country_entries(europe_coords, *_ZOOM_REF_DIMENSIONS["europe"])
    world_source = world_countries_coords if world_countries_coords else world_coords
    world = _zoom_country_entries(world_source, *_ZOOM_REF_DIMENSIONS["world"])
    return jsonify({"europe": europe, "world": world})


@routes.get('/api/zoom_country_outline')
def api_zoom_country_outline():
    """Return a small transparent PNG highlighting the hovered country's outline."""
    from PIL import ImageChops, ImageFilter

    map_kind = str(request.args.get('map') or 'europe').strip().lower()
    key = str(request.args.get('key') or '').strip().lower()
    solid = str(request.args.get('solid') or '').strip() in ('1', 'true', 'yes')
    if not key or map_kind not in ('europe', 'world'):
        return ('', 404)

    cache_key = (map_kind, key, solid)
    with _ZOOM_CACHE_LOCK:
        cached = _ZOOM_OUTLINE_CACHE.get(cache_key)
    if cached is not None:
        resp = Response(cached, mimetype='image/png')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp

    mask = _extract_country_mask_image(
        map_kind, key, allow_world_continent_fallback=True
    )
    if mask is None:
        return ('', 404)

    w, h = mask.size
    pw = 720 if solid else 360
    ph = max(1, int(round(h * (pw / w))))
    small_mask = mask.resize((pw, ph), Image.Resampling.BILINEAR)
    if solid:
        alpha = small_mask.filter(ImageFilter.MaxFilter(5)).point(lambda p: 255 if p > 64 else 0)
        out = Image.new('RGBA', (pw, ph), (0, 0, 0, 255))
    else:
        edge = ImageChops.subtract(small_mask.filter(ImageFilter.MaxFilter(5)), small_mask.filter(ImageFilter.MinFilter(3)))
        fill_alpha = small_mask.point(lambda p: 45 if p > 128 else 0)
        alpha = ImageChops.lighter(fill_alpha, edge.point(lambda p: min(255, int(p * 1.2))))
        out = Image.new('RGBA', (pw, ph), (252, 211, 77, 255))
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, format='PNG')
    png_bytes = buf.getvalue()
    with _ZOOM_CACHE_LOCK:
        _ZOOM_OUTLINE_CACHE[cache_key] = png_bytes

    resp = Response(png_bytes, mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp
