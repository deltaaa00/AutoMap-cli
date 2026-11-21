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

"""Check numeric min/max selection independently of image rendering.

Cases cover short-number strings, missing values, ties and nonfinite inputs.
This protects the numeric contract shared by highlights and Most/Least labels;
mask pixels and animation frames are covered in test_zoom_reveal.
"""

import pytest

from map.auto_map import _numeric_extrema


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"a": "9", "b": "100"}, ("b", "a")),
        ({"a": 9, "b": "100", "c": -2.5}, ("b", "c")),
        ({"a": "1.25", "b": "-0.5"}, ("a", "b")),
        ({"a": None, "b": "unknown", "c": 0}, ("c", "c")),
        ({"a": "NaN", "b": "Infinity", "c": True}, None),
        ({"ukraine": "2M", "poland": 100000}, ("ukraine", "poland")),
        ({"a": "1B", "b": "2.5M", "c": "100k"}, ("a", "c")),
        ({"a": "1,234", "b": "2 345", "c": "0.1"}, ("b", "c")),
        ({}, None),
    ],
)
def test_numeric_extrema(values, expected):
    assert _numeric_extrema(values) == expected
