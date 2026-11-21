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

"""Parse batch map ideas from CSV and create safe output filename stems.

CLI batch commands and web CSV uploads use these helpers through functions.
Headers are normalized, unknown/duplicate columns and malformed rows are
rejected, and each row requires a nonempty prompt. Optional empty cells are
omitted so the caller can apply its own generation defaults. gsm is an
integer from 1 to 100; modes/map types/extra flags use explicit allowed sets.

Edit column declarations and row parsing together when extending the format.
File reads use UTF-8, and parsing also accepts an initial byte-order mark.
"""

import csv
import io
import re
from pathlib import Path


_SLUG_RE = re.compile(r'[^a-zA-Z0-9]+')

BATCH_CSV_REQUIRED_COLUMNS: tuple[str, ...] = ("prompt",)
BATCH_CSV_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "mode",
    "map_type",
    "gsm",
    "extra_option",
    "model",
    "font",
    "palette_theme",
    "output",
)
BATCH_CSV_ALL_COLUMNS: tuple[str, ...] = BATCH_CSV_REQUIRED_COLUMNS + BATCH_CSV_OPTIONAL_COLUMNS
_ALLOWED_MODES = frozenset({"text", "number"})
_ALLOWED_MAP_TYPES = frozenset({"default", "flag", "world", "world_countries"})
_ALLOWED_EXTRA_OPTIONS = frozenset({"none", "show_most_least", "short_form", "show_most_least,short_form", "short_form,show_most_least"})


def slugify_prompt(prompt: str, max_len: int = 48) -> str:
    """Convert a map prompt into a safe, concise filename stem."""
    slug = _SLUG_RE.sub('_', prompt.strip().lower()).strip('_')
    return (slug[:max_len].strip('_') or 'map')


def parse_ideas_content(content: str, filename: str = "") -> list[dict[str, object]]:
    """Validate CSV text and return ordered per-row generation option dictionaries.

    Empty optional values are omitted, preserving caller defaults. Raises
    ValueError with a header or row-specific explanation on malformed input;
    partial results are never returned. A supplied filename must end in .csv.
    """
    if filename and Path(filename).suffix.lower() != ".csv":
        raise ValueError("Only .csv files are supported for batch ideas.")

    text = (content or "").lstrip("\ufeff").strip()
    if not text:
        raise ValueError("CSV file is empty.")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file is missing a header row.")

    headers = [str(h).strip().lower() for h in reader.fieldnames]
    if any(not h for h in headers):
        raise ValueError("CSV header contains an empty column name.")
    if len(set(headers)) != len(headers):
        raise ValueError("CSV header contains duplicate column names.")

    unknown_cols = [h for h in headers if h not in BATCH_CSV_ALL_COLUMNS]
    if unknown_cols:
        raise ValueError(
            f"Unknown CSV column(s): {', '.join(unknown_cols)}. "
            f"Allowed columns: {', '.join(BATCH_CSV_ALL_COLUMNS)}."
        )

    missing_cols = [c for c in BATCH_CSV_REQUIRED_COLUMNS if c not in headers]
    if missing_cols:
        raise ValueError(f"Missing required CSV column(s): {', '.join(missing_cols)}.")

    reader.fieldnames = headers
    ideas: list[dict[str, object]] = []
    for row_num, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"Row {row_num}: malformed CSV row (unexpected extra columns).")

        prompt = str(row.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Row {row_num}: 'prompt' is required and cannot be empty.")

        entry: dict[str, object] = {"prompt": prompt}

        # These optional columns follow the same rule: empty means use the default.
        choices_by_column = {
            "mode": _ALLOWED_MODES,
            "map_type": _ALLOWED_MAP_TYPES,
            "extra_option": _ALLOWED_EXTRA_OPTIONS,
        }
        for column, allowed_values in choices_by_column.items():
            value = str(row.get(column) or "").strip().lower()
            if not value:
                continue
            if value not in allowed_values:
                raise ValueError(
                    f"Row {row_num}: invalid {column} '{value}'. "
                    f"Allowed: {', '.join(sorted(allowed_values))}."
                )
            entry[column] = value

        gsm_raw = str(row.get("gsm") or "").strip()
        if gsm_raw:
            try:
                gsm_val = int(gsm_raw)
            except ValueError as exc:
                raise ValueError(
                    f"Row {row_num}: invalid gsm '{gsm_raw}'. Must be an integer between 1 and 100."
                ) from exc
            if gsm_val < 1 or gsm_val > 100:
                raise ValueError(
                    f"Row {row_num}: invalid gsm '{gsm_raw}'. Must be an integer between 1 and 100."
                )
            entry["gsm"] = gsm_val

        for key in ("model", "font", "palette_theme", "output"):
            val = str(row.get(key) or "").strip()
            if val:
                entry[key] = val

        ideas.append(entry)

    if not ideas:
        raise ValueError("CSV file contains no data rows.")

    return ideas


def load_ideas_from_file(file_path: str | Path) -> list[dict[str, object]]:
    """Read and parse map ideas from a .csv file."""
    path = Path(file_path)
    if path.suffix.lower() != ".csv":
        raise ValueError("Only .csv files are supported for batch ideas.")
    content = path.read_text(encoding="utf-8")
    return parse_ideas_content(content, filename=path.name)
