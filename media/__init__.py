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

"""Media processing used by the private HTTP worker.

The root ``yt-dlp_serv.py`` launcher exposes ``worker.app``. ``worker`` owns
requests and temporary files; the other modules build images, animation clips,
and engine inputs without depending on Flask. Images use uint8 RGB/RGBA pixels,
MoviePy masks use float alpha in [0, 1], and animation times are seconds.
"""
