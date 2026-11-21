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

"""Measure local worker rendering with synthetic video, map and audio material.

Run ``python benchmarks/bench_video_render.py --seconds 3`` from the project root.
The --module option selects a compatible media/worker.py implementation so
revisions can be compared. Cases toggle visual effects and draft rendering and
print elapsed times; they are measurements, not fixed CI timing thresholds.

No AI or remote downloads are required. MoviePy/FFmpeg must work locally. The
harness creates temporary input/output files and overrides worker storage; it
loads the implementation module rather than the thin yt-dlp_serv.py launcher.
"""
import argparse
import base64
import importlib.util
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_worker(path):
    """Load a worker implementation for benchmarking with the project on sys.path.
    """
    sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location('bench_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=3.0)
    parser.add_argument('--module', default=os.path.join(ROOT, 'media', 'worker.py'))
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()

    import numpy as np
    from moviepy import VideoClip
    from PIL import Image

    worker = load_worker(args.module)
    tmp = tempfile.mkdtemp()
    worker.RENDER_DIR = tmp
    background = os.path.join(tmp, 'bg.mp4')
    # Moving gradient at 1280x720 so scaling, cropping and blur do real work.
    xs = np.linspace(0, 255, 1280)[None, :, None]

    def frame(t):
        base = (xs + 120 * t) % 255
        return np.broadcast_to(base, (720, 1280, 3)).astype(np.uint8)

    VideoClip(frame, duration=args.seconds).write_videofile(
        background, fps=30, codec='libx264', logger=None)
    map_png = os.path.join(tmp, 'map.png')
    Image.new('RGBA', (1600, 1200), (40, 160, 90, 255)).save(map_png)
    material = {
        'map_file': base64.b64encode(open(map_png, 'rb').read()).decode(),
        'background_video': base64.b64encode(open(background, 'rb').read()).decode(),
        'video_title': 'Benchmark title\nSecond line',
        'duration': args.seconds,
        'watermark_text': 'automap', 'watermark_stars': True,
        'subscribe_badge': True, 'subscribe_delay': 0.5,
        'top_left_sticker': 'star', 'top_right_sticker': 'heart',
        'allow_remote_stickers': False,
    }
    client = worker.app.test_client()
    cases = [('effects on', {'visual_effects': True}),
             ('effects off', {'visual_effects': False}),
             ('draft, effects on', {'visual_effects': True, 'draft': True})]
    for name, extra in cases:
        best = None
        for _ in range(args.repeat):
            start = time.perf_counter()
            response = client.post('/api/render_video', json={**material, **extra})
            elapsed = time.perf_counter() - start
            assert response.status_code == 200, response.json
            best = elapsed if best is None else min(best, elapsed)
            os.remove(response.json['file_path'])
        print(f'{name:20s} {best:6.2f}s for {args.seconds:g}s clip')


if __name__ == '__main__':
    main()
