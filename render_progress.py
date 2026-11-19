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

"""Thread-safe per-render progress for the single-process media worker."""
from functools import wraps
from threading import Lock
from time import monotonic
from uuid import UUID

from flask import current_app, g, jsonify, request
from proglog import ProgressBarLogger


class RenderProgress:
    """Thread-safe in-memory store for tracking active and recent video renders."""

    def __init__(self):
        self.records = {}
        self.lock = Lock()

    def update(self, render_id, *, progress=None, phase=None, status=None, error=None):
        """Update fields for an existing render record; ignores unknown IDs."""
        with self.lock:
            record = self.records.get(render_id)
            if record is None:
                return
            if progress is not None:
                # Clamp to [0, 100] and never regress progress on out-of-order callbacks.
                record['progress'] = max(record['progress'], min(100, progress))
            for key, value in (('phase', phase), ('status', status), ('error', error)):
                if value is not None:
                    record[key] = value
            record['updated_at'] = monotonic()

    def snapshot(self, render_id):
        """Return a copy of the public progress fields for ``render_id``, or ``None``."""
        with self.lock:
            record = self.records.get(render_id)
            return {key: value for key, value in record.items() if key != 'updated_at'} if record else None

    def track(self, function):
        """Flask route decorator that registers, tracks, and finalizes a ``render_id``."""
        @wraps(function)
        def tracked(*args, **kwargs):
            data = request.get_json(silent=True) or {}
            raw_id = data.get('render_id')
            if raw_id is None:
                return function(*args, **kwargs)
            try:
                render_id = str(UUID(str(raw_id)))
            except ValueError:
                return jsonify(status='error', error='Invalid render ID.'), 400
            with self.lock:
                now = monotonic()
                # Keep active renders even if an encoding phase has no progress callbacks.
                self.records = {key: value for key, value in self.records.items()
                                if value['status'] == 'rendering' or now - value['updated_at'] < 3600}
                if render_id in self.records:
                    return jsonify(status='error', error='This render ID has already been used.'), 409
                if len(self.records) >= 256:
                    oldest_finished = min(
                        (key for key, value in self.records.items() if value['status'] != 'rendering'),
                        key=lambda key: self.records[key]['updated_at'],
                        default=None,
                    )
                    if oldest_finished is None:
                        return jsonify(status='error', error='Render service is busy. Try again later.'), 503
                    del self.records[oldest_finished]
                self.records[render_id] = dict(status='rendering', progress=0,
                                               phase='Preparing materials', updated_at=now)
            g.render_progress_id = render_id
            try:
                response = current_app.make_response(function(*args, **kwargs))
                payload = response.get_json(silent=True) or {}
                if response.status_code < 400 and payload.get('status') == 'success':
                    self.update(render_id, status='success', progress=100, phase='Complete')
                else:
                    self.update(render_id, status='error', phase='Render failed',
                                error=payload.get('error') or 'Video render failed.')
                return response
            except Exception:
                self.update(render_id, status='error', phase='Render failed', error='Video render failed.')
                raise
        return tracked

    def logger(self):
        """Return a Proglog logger bound to the current request's ``render_progress_id``."""
        render_id = getattr(g, 'render_progress_id', None)
        if render_id is None:
            return None
        tracker = self

        class Logger(ProgressBarLogger):
            """Bridge MoviePy/Proglog bar updates to ``RenderProgress`` percentage ranges."""

            def bars_callback(self, bar, attr, value, old_value=None):
                if attr != 'index':
                    return
                total = self.bars[bar].get('total') or 0
                if total <= 0:
                    return
                fraction = max(0, min(1, value / total))
                # Map audio chunking to 5-15% and video frame encoding to 15-95%.
                if bar == 'chunk':
                    tracker.update(render_id, progress=round(5 + 10 * fraction), phase='Encoding audio')
                elif bar == 'frame_index':
                    tracker.update(render_id, progress=round(15 + 80 * fraction),
                                   phase='Finalizing video' if fraction == 1 else 'Encoding video')

        return Logger()
