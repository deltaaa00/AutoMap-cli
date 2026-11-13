"""Verify image preparation tools on temporary inputs, never repository assets."""
import runpy
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFont

from map import constants

ROOT = Path(__file__).resolve().parents[1]


def test_border_preparation_and_alpha_clamping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Image.new('RGBA', (3, 3), (100, 100, 100, 240)).save('default_map.png')
    namespace = runpy.run_path(str(ROOT / 'resources/maps/border_procesing.py'))
    assert (tmp_path / 'default_map_blured.png').exists()
    values = np.array([[(50, 60, 70, 240), (50, 60, 70, 0), (0, 50, 60, 100)]], dtype=np.uint8)
    result = np.array(namespace['borders_procesing'](Image.fromarray(values)))
    assert result.tolist() == [[[0, 0, 0, 255], [0, 0, 0, 0], [0, 50, 60, 100]]]


def test_remove_partial_alpha_preserves_opaque_pixels(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = np.array([[(1, 2, 3, 255), (4, 5, 6, 254), (7, 8, 9, 0)]], dtype=np.uint8)
    Image.fromarray(values).save('map.png')
    runpy.run_path(str(ROOT / 'resources/othr/white_pixels_cleaning.py'))
    with Image.open('map.png') as image:
        assert list(image.getdata()) == [(1, 2, 3, 255), (4, 5, 6, 0), (7, 8, 9, 0)]


@pytest.mark.parametrize('name,coordinates', [
    ('default_map.png', constants.europe_coords), ('flag_map.png', constants.europe_coords),
    ('world_map.png', constants.world_coords),
])
def test_region_coordinates_are_inside_base_map(name, coordinates):
    with Image.open(ROOT / 'resources/maps' / name) as image:
        for region, points in coordinates.items():
            assert points, region
            assert all(0 <= x < image.width and 0 <= y < image.height for x, y in points), region


@pytest.mark.parametrize('path', sorted((ROOT / 'resources/fonts').glob('*.ttf')), ids=lambda p: p.name)
def test_bundled_fonts_can_be_loaded(path):
    assert ImageFont.truetype(str(path), 24).getbbox('Map 123')[2] > 0
