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
        ({}, None),
    ],
)
def test_numeric_extrema(values, expected):
    assert _numeric_extrema(values) == expected
