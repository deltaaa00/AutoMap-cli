"""Protect pixel and placement contracts across render engine boundaries.

Tiny real MoviePy clips check RGBA alpha composition, centered overlays, fade
metadata and exclusion of a separately rendered reveal layer. Camera frames
verify that zoom changes the crop and can return to the original image.
These tests do not need an encoded output file or external service.
"""

import numpy as np
from moviepy import ImageClip

from media.camera import apply_camera_zoom
from media.native_layers import build_native_layers


def test_native_layers_keep_alpha_placement_and_skip_separate_reveal():
    background = ImageClip(np.zeros((4, 4, 3), dtype=np.uint8))
    base = ImageClip(np.full((4, 4, 4), (255, 0, 0, 255), dtype=np.uint8))
    overlay = ImageClip(np.full((4, 4, 4), (0, 0, 255, 128), dtype=np.uint8))
    badge = ImageClip(np.full((2, 2, 4), (0, 255, 0, 64), dtype=np.uint8))
    badge = badge.with_position(('center', 'center'))
    reveal = ImageClip(np.zeros((4, 4, 3), dtype=np.uint8))
    veil = ImageClip(np.zeros((4, 4, 3), dtype=np.uint8)).with_duration(0.5)

    pixels, overlays, veil_duration = build_native_layers(
        [background, base, overlay, badge, reveal, veil],
        (4, 4),
        {id(badge), id(reveal), id(veil)},
        {id(badge): (0.2, 0.6)},
        2.0,
        reveal_clip=reveal,
    )

    composed = np.frombuffer(pixels, dtype=np.uint8).reshape(4, 4, 4)
    assert tuple(composed[0, 0]) == (127, 0, 128, 255)
    assert veil_duration == 0.5
    assert len(overlays) == 1
    badge_pixels, x, y, width, height, start, fade = overlays[0]
    assert (x, y, width, height, start, fade) == (1, 1, 2, 2, 0.2, 0.6)
    assert tuple(np.frombuffer(badge_pixels, dtype=np.uint8)[:4]) == (0, 255, 0, 64)


def test_camera_zoom_changes_crop_then_returns_to_original_frame():
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    frame[:, :, 0] = np.arange(32, dtype=np.uint8)[None, :] * 7
    frame[:, :, 1] = np.arange(32, dtype=np.uint8)[:, None] * 7
    clip = ImageClip(frame).with_duration(2.0)
    zoomed = apply_camera_zoom(
        clip,
        (32, 32),
        (24.0, 24.0, 2.0, 0.2, 0.4, True, 0.4),
        2.0,
    )

    assert np.array_equal(zoomed.get_frame(0.0), frame)
    assert not np.array_equal(zoomed.get_frame(0.8), frame)
    assert zoomed.get_frame(0.8).shape == frame.shape
    assert np.array_equal(zoomed.get_frame(2.0), frame)
