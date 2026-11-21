"""Limits for untrusted images and YouTube audio URLs."""

import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image


MEDIA_STORAGE_DIR = os.environ.get("AUTOMAP_STORAGE_DIR") or str(Path(__file__).resolve().parent / "instance" / "media")
IMAGE_FORMATS = ('PNG', 'JPEG', 'WEBP', 'GIF', 'BMP')
MAX_IMAGE_PIXELS = 20_000_000
_YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}
_VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')


def open_upload_image(stream, formats=IMAGE_FORMATS):
    """Read an image header with an explicit format and pixel limit."""
    image = Image.open(stream, formats=formats)
    if image.width * image.height > MAX_IMAGE_PIXELS:
        image.close()
        raise ValueError('Image must be at most 20 megapixels.')
    return image


def youtube_video_url(value):
    """Return a canonical, single-video HTTPS URL for the YouTube extractor."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('Enter a YouTube video URL.')
    try:
        url = urlsplit(value.strip())
        if (url.scheme not in ('https', 'http') or url.username is not None
                or url.password is not None or url.port is not None):
            raise ValueError
        host = url.hostname
        parts = url.path.strip('/').split('/')
        if host == 'youtu.be' and len(parts) == 1:
            video_id = parts[0]
        elif host in _YOUTUBE_HOSTS and url.path == '/watch':
            video_id, = parse_qs(url.query, max_num_fields=32).get('v', [])
        elif host in _YOUTUBE_HOSTS and len(parts) == 2 and parts[0] in ('shorts', 'live', 'embed'):
            video_id = parts[1]
        else:
            raise ValueError
        if not _VIDEO_ID.fullmatch(video_id):
            raise ValueError
    except ValueError:
        raise ValueError('Enter a YouTube video URL.') from None
    return f'https://www.youtube.com/watch?v={video_id}'
