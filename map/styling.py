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

"""Convert region values and user filling options into map colors and labels.

The renderer uses these pure helpers for palette selection, RGB/RGBA input
normalization, numeric ranges, compact number strings, and extrema names.
Numeric fills use low < value <= high. Nonfinite numbers and boolean numeric
inputs are rejected; absent or invalid overrides do not become fill rules.

Palette colors and semantic categories are defined in constants. Change
formatting and automatic bucket selection here; image painting, label
positions, and layer composition belong in auto_map/geometry/legend.
"""

import ast
import math
import re
from collections import Counter

from . import constants


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
    if not math.isfinite(num):
        return None
    return num


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
    if float(num).is_integer():
        return str(int(num))
    return str(round(num, 2))


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
    if parsed is not None and abs(parsed) >= 1_000.0:
        return _format_number_value(parsed)
    if isinstance(val, str):
        return val.strip()
    return val


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


def _format_text_label(value: object) -> str:
    """Uppercase the first character while preserving acronyms and other casing."""
    label = "" if value is None else str(value).strip()
    return label[:1].upper() + label[1:]


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
