import base64
import io

import pytest

import numpy as np
from moviepy import ColorClip, VideoFileClip
from PIL import Image, ImageFont


@pytest.mark.parametrize('title', [
    'Найкрасивіші країни Європи',
    'A long title about the most beautiful places around the world ' * 3,
    'W' * 100,
])
def test_title_fits_safe_area(worker, title):
    image = worker._title_image(title, 936)
    assert image.shape[1] == 936
    assert image.shape[0] <= 408
    ys, xs = np.where(np.all(image[:, :, :3] > 240, axis=2) & (image[:, :, 3] > 240))
    assert len(xs)
    assert xs.min() >= 20 and xs.max() < 916
    assert ys.min() >= 20 and ys.max() < image.shape[0] - 20
    assert abs((xs.min() + xs.max()) / 2 - 468) < 8


def test_bundled_title_font_supports_ukrainian(worker):
    font = ImageFont.truetype(worker.TITLE_FONT_PATH, 80)
    missing = bytes(font.getmask('\ufffd'))
    for character in 'Найкрасивіші країни Європи':
        if not character.isspace():
            assert bytes(font.getmask(character)) != missing


@pytest.mark.parametrize('font,size', [('DIN Condensed.ttf', 48), ('Bips.ttf', 144)])
def test_preview_uses_render_title_layout(worker, font, size):
    response = worker.app.test_client().post('/api/video_title_preview', json={
        'video_title': 'Найкрасивіші країни Європи',
        'title_font': font, 'title_font_size': size,
    })
    assert response.status_code == 200
    assert response.mimetype == 'image/png'
    actual = np.array(Image.open(io.BytesIO(response.data)))
    font_path, font_size = worker._title_settings({'title_font': font, 'title_font_size': size})
    expected = worker._title_image('Найкрасивіші країни Європи', 936, font_path, font_size)
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('settings', [
    {'title_font': '../../etc/passwd'}, {'title_font': 'missing.ttf'},
    {'title_font_size': 0}, {'title_font_size': 200},
    {'title_font_size': 'invalid'}, {'title_font_size': True},
])
def test_invalid_title_settings_rejected_before_render(worker, settings):
    client = worker.app.test_client()
    data = {'video_title': 'Test', **settings}
    assert client.post('/api/video_title_preview', json=data).status_code == 400
    data.update(map_file='eA==', background_video='eA==')
    assert client.post('/api/render_video', json=data).status_code == 400


def test_default_preview_matches_default_render_title(worker):
    response = worker.app.test_client().post('/api/video_title_preview', json={'video_title': 'Test'})
    assert response.status_code == 200
    np.testing.assert_array_equal(
        np.array(Image.open(io.BytesIO(response.data))), worker._title_image('Test', 936),
    )


@pytest.mark.parametrize('font,size', [('DIN Condensed.ttf', 100), ('Bips.ttf', 72)])
def test_portrait_render_with_centered_title_and_map(worker, tmp_path, font, size):
    background = tmp_path / 'background.mp4'
    with ColorClip((640, 360), color=(50, 100, 150), duration=1) as clip:
        clip.write_videofile(str(background), fps=30, codec='libx264', logger=None)
    map_file = tmp_path / 'map.png'
    Image.new('RGB', (800, 400), (0, 255, 0)).save(map_file)
    response = worker.app.test_client().post('/api/render_video', json={
        'map_file': base64.b64encode(map_file.read_bytes()).decode(),
        'background_video': base64.b64encode(background.read_bytes()).decode(),
        'video_title': 'Найкрасивіші країни Європи',
        'title_font': font, 'title_font_size': size,
        'duration': 1,
    })
    assert response.status_code == 200, response.json
    with VideoFileClip(response.json['file_path']) as result:
        assert result.size == [1080, 1920]
        assert result.duration == pytest.approx(1, abs=.05)
        frame = result.get_frame(.5)
    green = (frame[:, :, 1] > 220) & (frame[:, :, 0] < 30)
    ys, xs = np.where(green)
    assert abs((xs.min() + xs.max()) / 2 - 540) < 2
    assert (xs.max() - xs.min()) / (ys.max() - ys.min()) == pytest.approx(2, abs=.03)
    white = np.all(frame > 220, axis=2)
    title_y, title_x = np.where(white)
    assert title_y.max() < ys.min() - 40
    assert abs((title_x.min() + title_x.max()) / 2 - 540) < 8


def test_render_attaches_audio_and_limits_duration(worker, tmp_path):
    from pathlib import Path
    from moviepy import AudioClip

    background = tmp_path / 'background.mp4'
    with ColorClip((180, 320), color=(50, 100, 150), duration=1) as clip:
        clip.write_videofile(str(background), fps=30, codec='libx264', logger=None)
    track = Path(worker.CUT_DIR) / 'Audio_title_cut.mp3'
    with AudioClip(lambda t: .2 * np.sin(2 * np.pi * 440 * t), duration=.4, fps=44100) as audio:
        audio.write_audiofile(str(track), logger=None)
    map_file = tmp_path / 'map.png'
    Image.new('RGB', (100, 50), 'green').save(map_file)
    response = worker.app.test_client().post('/api/render_video', json={
        'map_file': base64.b64encode(map_file.read_bytes()).decode(),
        'background_video': base64.b64encode(background.read_bytes()).decode(),
        'video_title': 'Audio title', 'duration': 'invalid',
    })
    assert response.status_code == 200, response.json
    with VideoFileClip(response.json['file_path']) as result:
        assert result.audio is not None
        assert .35 <= result.duration <= .55
        # Sample inside the clip: MoviePy's bulk reader has a short-clip
        # buffer bug, so read scalar frames without crossing chunk boundaries.
        samples = np.array([result.audio.get_frame(float(t)) for t in np.linspace(.05, .25, 64)])
        assert np.max(np.abs(samples)) > .05
