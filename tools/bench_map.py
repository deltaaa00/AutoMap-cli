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

"""Benchmark map generation using fixed fake AI data, without spending API quota.

Run from the project root:
    python tools/bench_map.py --runs 3
    python tools/bench_map.py --compare /tmp/old_auto_map.py

The harness seeds random colors and supplies ai_answer_override, recording cold
and warm render times and optionally comparing RGBA pixels across revisions.
The alternate module must retain compatible renderer/helper/asset contracts;
this tool is for local measurement, not a stable performance assertion in CI.
Results are kept in memory and local map assets/fonts are required.
"""
import argparse
import importlib.util
import io
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from map import auto_map as current  # noqa: E402
from map.constants import europe_coords, world_coords  # noqa: E402

FONT = "Bips.ttf"


def fake_answer(coords, mode):
    """Build deterministic region values so both compared renderers receive identical data.
    """
    names = list(coords)
    if mode == "number":
        return {n: str((i * 37) % 900 + 5) for i, n in enumerate(names)}
    return {n: ("Yes", "No", "Tea", "Maybe")[i % 4] for i, n in enumerate(names)}


CASES = [
    ("default", "text", europe_coords),
    ("default", "number", europe_coords),
    ("world", "text", world_coords),
    ("flag", "text", europe_coords),
]


def run(mod, map_type, mode, coords):
    random.seed(1234)
    buf = mod.auto_map("bench", mode, map_type, 0, None, "fake", FONT,
                       show_result=False, io_res=True,
                       ai_answer_override=fake_answer(coords, mode))
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--compare")
    args = ap.parse_args()
    current.log.set_verbose(False)

    mods = {"current": current}
    if args.compare:
        spec = importlib.util.spec_from_file_location("map._old_auto_map", args.compare)
        old = importlib.util.module_from_spec(spec)
        old.__package__ = "map"
        spec.loader.exec_module(old)
        old.__file__ = current.__file__
        old.FONTS_DIR = current.FONTS_DIR
        mods = {"old": old, "current": current}

    outputs = {}
    for label, mod in mods.items():
        for map_type, mode, coords in CASES:
            times = []
            for _ in range(args.runs):
                t = time.perf_counter()
                buf = run(mod, map_type, mode, coords)
                times.append(time.perf_counter() - t)
            outputs[(label, map_type, mode)] = buf.getvalue()
            first, warm = times[0], (min(times[1:]) if len(times) > 1 else float("nan"))
            print(f"{label:8} {map_type:8} {mode:7} first={first:6.2f}s warm(min)={warm:6.2f}s", flush=True)

    if args.compare:
        import numpy as np
        for map_type, mode, _ in CASES:
            a = np.asarray(Image.open(io.BytesIO(outputs[("old", map_type, mode)])).convert("RGBA"))
            b = np.asarray(Image.open(io.BytesIO(outputs[("current", map_type, mode)])).convert("RGBA"))
            diff = int(np.any(a != b, axis=2).sum())
            print(f"diff {map_type:8} {mode:7} differing_pixels={diff} of {a.shape[0] * a.shape[1]}")


if __name__ == "__main__":
    main()
