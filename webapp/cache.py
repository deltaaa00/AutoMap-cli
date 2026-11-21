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

"""Cache parsed AI answers so visual-only map changes avoid another AI call.

webapp.generation uses the shared ai_cache instance. Keys hash request-affecting
inputs and a user scope derived from session identity and credential hashes;
raw API keys are not stored. Cached content is a region-to-value dictionary,
not a PNG, so the renderer can change fonts, colors and labels independently.

The cache is process-local, locked, size-bounded and expires by monotonic time.
Values are copied to prevent a render from modifying a later request's answer.
Environment settings control retention and capacity; restarting clears entries.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


class AIResponseCache:
    """Thread-safe LRU + TTL cache for parsed AI map responses."""

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 256):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Return True when both TTL and capacity are positive."""
        return self.ttl > 0 and self.max_entries > 0

    @staticmethod
    def scope(identity: str | None, api_key: str | None) -> str:
        """Derive a per-user/per-key cache namespace without storing the raw key."""
        key_part = hashlib.sha256(api_key.encode()).hexdigest() if api_key else ''
        return hashlib.sha256(f"{identity or ''}\0{key_part}".encode()).hexdigest()

    @staticmethod
    def make_key(scope: str, **fields: object) -> str:
        """Hash the cache scope and sorted request fields into a lookup key."""
        blob = json.dumps(fields, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(f"{scope}\0{blob}".encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        """Return a shallow copy of a non-expired cached dict, or None on miss."""
        if not self.enabled:
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            stored_at, value = item
            if time.monotonic() - stored_at > self.ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return dict(value)

    def put(self, key: str, value: dict) -> None:
        """Store a non-empty dict in the cache and evict the oldest entry if full."""
        if not self.enabled or not isinstance(value, dict) or not value:
            return
        with self._lock:
            self._data[key] = (time.monotonic(), dict(value))
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        """Remove all cached entries."""
        with self._lock:
            self._data.clear()


ai_cache = AIResponseCache(
    ttl_seconds=_env_int('AI_CACHE_TTL', 3600),
    max_entries=_env_int('AI_CACHE_MAX_ENTRIES', 256),
)
