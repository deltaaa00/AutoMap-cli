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
    expected = worker._title_image('Найкрасивіші країни Європи', 1040, font_path, font_size)
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
        np.array(Image.open(io.BytesIO(response.data))), worker._title_image('Test', 1040),
    )


@pytest.mark.parametrize('font,size', [('DIN Condensed.ttf', 100), ('Bips.ttf', 72)])
@pytest.mark.parametrize('map_size', [(800, 400), (400, 800), (600, 600)])
def test_portrait_render_with_centered_title_and_map(worker, tmp_path, font, size, map_size):
    background = tmp_path / 'background.mp4'
    with ColorClip((640, 360), color=(50, 100, 150), duration=1) as clip:
        clip.write_videofile(str(background), fps=30, codec='libx264', logger=None)
    map_file = tmp_path / 'map.png'
    Image.new('RGB', map_size, (0, 255, 0)).save(map_file)
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
    assert (xs.max() - xs.min()) / (ys.max() - ys.min()) == pytest.approx(map_size[0] / map_size[1], abs=.03)
    assert ys.max() <= 1761
    if map_size[1] > map_size[0]:
        assert ys.max() - ys.min() > 1200
    else:
        assert xs.max() - xs.min() >= 1000
    white = np.all(frame > 220, axis=2)
    title_y, title_x = np.where(white)
    assert 70 <= title_y.min() < 150
    mint = ((frame[:, :, 0] > 220) & (frame[:, :, 1] > 195) & (frame[:, :, 2] < 90))
    mint_y, _ = np.where(mint)
    assert len(mint_y) > 100
    assert mint_y.min() > title_y.max()
    assert 15 < ys.min() - mint_y.max() < 70
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


@pytest.mark.parametrize('first,other', [('#ffffff', '#a7f3d0'), ('#ff6600', '#3366ff')])
def test_line_colors_follow_displayed_rows(worker, first, other):
    from PIL import ImageColor

    image = worker._title_image('First line\nSecond line\nThird line', 936,
                                first_line_color=first, other_lines_color=other)
    visible = image[:, :, 3] > 240
    first_y, _ = np.where(np.all(image[:, :, :3] == ImageColor.getrgb(first), axis=2) & visible)
    other_y, _ = np.where(np.all(image[:, :, :3] == ImageColor.getrgb(other), axis=2) & visible)
    assert len(first_y) > 100 and len(other_y) > 100
    assert first_y.max() < other_y.min()
    assert other_y.max() - other_y.min() > 100
    assert first_y.min() >= 20
    assert other_y.max() < image.shape[0] - 20


def test_wrapped_lines_use_secondary_color(worker):
    image = worker._title_image('A long title about the most beautiful countries in Europe', 936)
    alpha = image[:, :, 3] > 240
    assert np.any(np.all(image[:, :, :3] == (255, 255, 255), axis=2) & alpha)
    assert np.any(np.all(image[:, :, :3] == (255, 231, 53), axis=2) & alpha)


@pytest.mark.parametrize('field', ['title_first_line_color', 'title_other_lines_color'])
@pytest.mark.parametrize('value', ['red', '#fff', '#12345g', 'ffffff', '', None, 123])
def test_invalid_colors_are_rejected(worker, field, value):
    data = {'video_title': 'First\nSecond', field: value}
    client = worker.app.test_client()
    assert client.post('/api/video_title_preview', json=data).status_code == 400
    data.update(map_file='eA==', background_video='eA==')
    assert client.post('/api/render_video', json=data).status_code == 400


def test_preview_and_clip_share_custom_colors(worker):
    colors = {'title_first_line_color': '#ff6600', 'title_other_lines_color': '#3366ff'}
    response = worker.app.test_client().post('/api/video_title_preview', json={
        'video_title': 'First\nSecond', **colors,
    })
    assert response.status_code == 200
    expected = worker._title_image('First\nSecond', 1040,
                                  first_line_color='#ff6600', other_lines_color='#3366ff')
    np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(response.data))), expected)
    clip = worker._build_title_clip('First\nSecond', 1, 1040,
                                    first_line_color='#ff6600', other_lines_color='#3366ff')
    np.testing.assert_array_equal(clip.get_frame(0), expected[:, :, :3])
    clip.close()


@pytest.mark.parametrize('effects', [True, False])
@pytest.mark.parametrize('watermark_font', ['DIN Condensed.ttf', 'Bips.ttf'])
def test_watermark_preview_uses_rendered_lettering(worker, effects, watermark_font):
    response = worker.app.test_client().post('/api/video_title_preview', json={
        'video_title': 'My channel', 'preview_kind': 'watermark',
        'watermark_text': 'My channel', 'watermark_color': '#a4e9ee',
        'visual_effects': effects,
        'title_font': 'arialroundedmtbold.ttf', 'watermark_font': watermark_font,
    })
    assert response.status_code == 200
    font_path = worker._watermark_font_path({'watermark_font': watermark_font})
    expected = worker._title_image('My channel', 936, font_path=font_path, font_size=96,
        first_line_color='#a4e9ee', other_lines_color='#a4e9ee', effects=effects, max_height=110)
    np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(response.data))), expected)


def test_branded_render_reserves_footer_and_keeps_logo_transparency(worker, tmp_path):
    background = tmp_path / 'background.mp4'
    with ColorClip((180, 320), color=(50, 100, 150), duration=.3) as clip:
        clip.write_videofile(str(background), fps=30, codec='libx264', logger=None)
    map_buffer, logo_buffer = io.BytesIO(), io.BytesIO()
    Image.new('RGBA', (400, 800), (0, 255, 0, 255)).save(map_buffer, format='PNG')
    logo = Image.new('RGBA', (300, 200), (255, 0, 255, 255))
    logo.save(logo_buffer, format='PNG')
    response = worker.app.test_client().post('/api/render_video', json={
        'map_file': base64.b64encode(map_buffer.getvalue()).decode(),
        'background_video': base64.b64encode(background.read_bytes()).decode(),
        'watermark_image': base64.b64encode(logo_buffer.getvalue()).decode(),
        'video_title': 'Headline\nSecond line', 'watermark_text': 'My channel',
        'watermark_font': 'Bips.ttf', 'duration': 1,
    })
    assert response.status_code == 200, response.json
    with VideoFileClip(response.json['file_path']) as result:
        frame = result.get_frame(.1)
    green_y, _ = np.where((frame[:, :, 1] > 220) & (frame[:, :, 0] < 30))
    logo_y, logo_x = np.where((frame[:, :, 0] > 220) & (frame[:, :, 2] > 220) & (frame[:, :, 1] < 50))
    assert green_y.max() < 1451
    assert 1470 <= logo_y.min() < logo_y.max() < 1701
    assert abs((logo_x.min() + logo_x.max()) / 2 - 540) < 2
    # Transparent corners show the background, not an opaque logo rectangle.
    assert frame[1480, 435, 2] > frame[1480, 435, 0]
    cyan = (frame[:, :, 0] > 130) & (frame[:, :, 1] > 200) & (frame[:, :, 2] > 200)
    footer_y, _ = np.where(cyan & (np.indices(cyan.shape)[0] > 1740))
    assert len(footer_y) > 100 and footer_y.max() < 1900


def test_round_logo_center_crop_and_transparency(worker):
    from PIL import ImageDraw

    original = Image.new('RGBA', (400, 200), 'red')
    ImageDraw.Draw(original).rectangle((100, 0, 299, 199), fill=(0, 255, 0, 128))
    logo = np.array(worker._round_watermark(original))
    assert logo.shape == (230, 230, 4)
    assert logo[0, 0, 3] == 0 and logo[-1, -1, 3] == 0
    assert tuple(logo[115, 115]) == (0, 255, 0, 128)
