import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache

from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

STORAGE_DIR = "/tmp/automap_storage"
DOWNLOADS = os.path.join(STORAGE_DIR, "downloads")
CUT_DIR = os.path.join(STORAGE_DIR, "cut")
RENDER_DIR = os.path.join(STORAGE_DIR, "render")

os.makedirs(STORAGE_DIR, exist_ok=True)
os.makedirs(DOWNLOADS, exist_ok=True)
os.makedirs(CUT_DIR, exist_ok=True)
os.makedirs(RENDER_DIR, exist_ok=True)

# Incoming materials (map image, background video) must be <= 10 MB each.
MAX_MATERIAL_BYTES = 10 * 1024 * 1024

# Allowed ffmpeg codec names (see the UI dropdown).
#   libx264            - universal software encoder
#   h264_nvenc         - NVIDIA GPUs
#   h264_videotoolbox  - Apple Silicon chips
ALLOWED_CODECS = ("libx264", "h264_nvenc", "h264_videotoolbox")
_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"')
_HTML_TITLE_RE = re.compile(r'<title>(.*?)</title>')
_SAFE_TITLE_RE = re.compile(r'[^\w\-]')
_AUDIO_TITLE_RE = re.compile(r'[\w\-]+')


@lru_cache(maxsize=1)
def _available_codecs():
    """Probe the encoder and device together, using the same FFmpeg as MoviePy."""
    from moviepy.config import FFMPEG_BINARY

    available = []
    for codec in ALLOWED_CODECS:
        try:
            result = subprocess.run([
                FFMPEG_BINARY, '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'color=size=64x64:rate=1',
                '-frames:v', '1', '-pix_fmt', 'yuv420p', '-c:v', codec,
                '-f', 'null', '-',
            ], capture_output=True, timeout=5, check=False)
            if result.returncode == 0:
                available.append(codec)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return tuple(available)


@app.route('/api/codecs')
def api_codecs():
    return jsonify(codecs=list(_available_codecs()))

def cleanup_old_files(max_age_seconds=1800):
    """Delete old files"""
    # Use one cutoff reference for the entire scan of downloads, cuts, and renders.
    now = time.time()
    for root, _dirs, files in os.walk(STORAGE_DIR):
        for name in files:
            item_path = os.path.join(root, name)
            try:
                if now - os.path.getmtime(item_path) > max_age_seconds:
                    os.remove(item_path)
            except FileNotFoundError:
                pass  # Another request may have removed or renamed the file.

def _get_song_title(url: str) -> str | None:
    """Try to extract the song title from the given URL."""
    import requests
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        match = _OG_TITLE_RE.search(response.text)
        if match:
            return match.group(1)

        # Fall back to the page title when Open Graph metadata is unavailable.
        match_title = _HTML_TITLE_RE.search(response.text)
        if match_title:
            return match_title.group(1)

    except requests.RequestException:
        return None


@app.route('/api/music_download/', methods=['POST'])
@app.route('/api/music_download', methods=['POST'])
def music_download():
    cleanup_old_files()  # Clean up old files before processing the new request
    """Download a video's audio, convert it to mp3, and return it as base64.

    Request JSON:
        { "url": "https://music.youtube.com/watch?v=..." }

    Response JSON (success):
        { "status": "success", "file": "<base64 mp3>", "title": "<song title>" }

    Response JSON (error):
        { "status": "error", "error": "<message>" }
    """
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({"status": "error", "error": "Missing 'url' in request body."}), 400

    # yt-dlp with `-x` writes to a temp file, so pipe through the temp dir and
    # read the produced .mp3 back. `-o -` is unreliable when extracting audio.

    download_dir = tempfile.mkdtemp(prefix="automap_download_")
    command = [
        "yt-dlp",
        "-f", "ba[ext=m4a]/ba",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", os.path.join(download_dir, "track.%(ext)s"),
        url,
    ]

    try:
        subprocess.run(command, capture_output=True, check=True)

        # Find the produced mp3 inside the temp dir.
        mp3_path = None
        for root, _dirs, files in os.walk(download_dir):
            for name in files:
                if name.lower().endswith(".mp3"):
                    mp3_path = os.path.join(root, name)
                    break
            if mp3_path:
                break

        if not mp3_path:
            return jsonify({"status": "error",
                            "error": "yt-dlp ERROR: No mp3 file produced."}), 500

        import uuid
        title = _get_song_title(url) or str(uuid.uuid4())
        title = re.sub(r'[^\w\-]', '_', title)

        with open(mp3_path, "rb") as fh:
            base64_audio = base64.b64encode(fh.read()).decode("utf-8")
            new_mp3_path = os.path.join(DOWNLOADS, f"{title}.mp3")
            # Temporary downloads and persistent storage may be separate mounts.
            # shutil.move falls back to copying when an atomic rename is unavailable.
            shutil.move(mp3_path, new_mp3_path)

        return jsonify({
            "status": "success",
            "file": base64_audio,
            "title": title,
        }), 200

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.decode("utf-8", errors="ignore").strip()
        return jsonify({"status": "error",
                        "error": f"yt-dlp ERROR: {error_message}"}), 500

    except FileNotFoundError:
        return jsonify({"status": "error",
                        "error": "yt-dlp not found. Please ensure yt-dlp is installed."}), 500

    except Exception as e:  # noqa: BLE001 - surface any failure to the client
        return jsonify({"status": "error", "error": f"Unexpected error: {str(e)}"}), 500
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        

@app.route('/api/music_cut/', methods=['POST'])
@app.route('/api/music_cut', methods=['POST'])
def cut_audio() -> tuple[object, int]:
    """Cut a stored mp3 into a short clip and return it as base64.

    Reads a previously downloaded track from ``DOWNLOADS`` (by title), slices
    it starting at ``start_time`` for ``duration`` seconds (1..30, default 8),
    and writes the clip to ``CUT_DIR``.

    Request JSON:
        { "start_time": "MM:SS", "title": "<song title>", "duration": 8 }

    Response JSON (success):
        { "status": "successfull", "file": "<base64 mp3 clip>" }

    Response JSON (error):
        { "status": "error", "error": "<message>" }
    """
    data: dict[str, object] = request.get_json(silent=True) or {}

    try:
        time = str(data['start_time'])
        filename = data['title']
        if not isinstance(filename, str) or not re.fullmatch(r'[\w\-]+', filename):
            return jsonify({"status": "error", "error": "Invalid track title."}), 400

        raw_duration = data.get('duration', 8)
        try:
            duration = int(raw_duration) if raw_duration is not None else 8
        except (TypeError, ValueError):
            duration = 8
        duration = max(1, min(30, duration))

        os.makedirs(CUT_DIR, exist_ok=True)

        if not re.fullmatch(r'\d+:[0-5]\d', time):
            return jsonify(status='error', error='Start time must use MM:SS format.'), 400
        minutes, seconds = time.split(":")
        minutes, seconds = int(minutes), int(seconds)

        # Converting to ms
        start_ms = (minutes * 60 + seconds) * 1000

        # Audio clip duration (1..30 seconds, default 8 seconds)
        end_ms = start_ms + (duration * 1000)

        from pydub import AudioSegment

        audio = AudioSegment.from_file(os.path.join(DOWNLOADS, f"{filename}.mp3"))
        if start_ms >= len(audio):
            return jsonify(status='error', error='Start time is beyond the end of the track.'), 400

        cut = audio[start_ms:end_ms]

        cut.export(
            f"{CUT_DIR}/{filename}_cut.mp3",
            format="mp3",
            bitrate="192k",
        )

        # Encoding in base64
        with open(f"{CUT_DIR}/{filename}_cut.mp3", 'rb') as file:
            cut_base64 = base64.b64encode(file.read()).decode('utf-8')

        return jsonify({
            "status": "successfull",
            "file": cut_base64
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

TITLE_FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "resources", "fonts", "DIN Condensed.ttf",
)


def _title_settings(data):
    font_name = str(data.get('title_font') or 'DIN Condensed.ttf')
    font_dir = os.path.dirname(TITLE_FONT_PATH)
    # Accept bundled filenames only, rather than arbitrary client-supplied paths.
    fonts = {name for name in os.listdir(font_dir) if name.lower().endswith(('.ttf', '.otf'))}
    if font_name not in fonts:
        raise ValueError('Unknown title font.')
    raw_size = data.get('title_font_size', 100)
    # Reject booleans explicitly because int(True) would otherwise succeed.
    if isinstance(raw_size, bool):
        raise ValueError('Title size must be a number between 48 and 144.')
    try:
        size = int(raw_size)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Title size must be a number between 48 and 144.')
    if not 48 <= size <= 144:
        raise ValueError('Title size must be between 48 and 144.')
    return os.path.join(font_dir, font_name), size


def _title_colors(data):
    colors = (data.get('title_first_line_color', '#ffffff'),
              data.get('title_other_lines_color', '#ffe735'))
    if any(not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color) for color in colors):
        raise ValueError('Title colors must use #RRGGBB format.')
    return colors


def _title_image(
    title: str,
    max_width: int,
    font_path: str = TITLE_FONT_PATH,
    font_size: int = 100,
    first_line_color: str = '#ffffff',
    other_lines_color: str = '#ffe735',
    effects: bool = True,
    max_height: int = 360,
    uppercase: bool = True,
):
    """Lay out a centered, wrapped title with safe margins and a layered shadow."""
    import numpy as np
    from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

    if uppercase:
        title = title.upper()
    padding = 24
    outline = 6 if effects else 1
    text_width = max_width - padding * 2
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    # Shrink long titles to keep the map visible. Character wrapping also handles
    # URLs and words that are wider than the available space.
    for fitted_size in range(font_size, 19, -4):
        font = ImageFont.truetype(font_path, fitted_size)
        lines = []
        for paragraph in title.splitlines() or [title]:
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}".strip()
                if measure.textlength(candidate, font=font) <= text_width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                line = ""
                for char in word:
                    if line and measure.textlength(line + char, font=font) > text_width:
                        lines.append(line)
                        line = ""
                    line += char
            lines.append(line)
        text = "\n".join(lines)
        spacing = round(fitted_size * 0.22)
        bounds = measure.multiline_textbbox((0, 0), text, font=font,
                                            spacing=spacing, align="center", stroke_width=outline)
        height = bounds[3] - bounds[1]
        if height <= max_height:
            break
    if height > max_height:
        raise ValueError("Video title is too long. Please shorten it.")

    size = (max_width, height + padding * 2)
    shadow = Image.new("RGBA", size)
    # Account for the font's vertical bounding-box offset inside the padded image.
    xy = (max_width / 2, padding - bounds[1])
    # Use one line advance for shadow and colored text. Different stroke widths
    # in multiline_text would otherwise change the spacing and clip lower rows.
    advance = measure.textbbox((0, 0), 'A', font=font, stroke_width=outline)[3] + outline + spacing
    shadow_draw = ImageDraw.Draw(shadow)
    for row, line in enumerate(lines):
        shadow_draw.text((xy[0], xy[1] + row * advance + 5), line,
                         font=font, anchor='ma', fill=(0, 0, 0, 255), stroke_width=outline)
    # Combine a soft halo with a tighter drop shadow for contrast on bright footage.
    # Preview and final video both use this image, so the shading stays consistent.
    image = shadow.filter(ImageFilter.GaussianBlur(8)) if effects else Image.new('RGBA', size)
    if effects:
        image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(2)))
    draw = ImageDraw.Draw(image)
    first_rgb = ImageColor.getrgb(first_line_color)
    other_rgb = ImageColor.getrgb(other_lines_color)
    for row, line in enumerate(lines):
        color = first_rgb if row == 0 else other_rgb
        draw.text((xy[0], xy[1] + row * advance), line, font=font, anchor='ma',
                  fill=color + (255,), stroke_width=outline,
                  stroke_fill=(0, 0, 0, 255) if effects else color + (220,))
    return np.array(image)


def _build_title_clip(
    title: str,
    duration: float,
    max_width: int,
    font_path: str = TITLE_FONT_PATH,
    font_size: int = 100,
    first_line_color: str = '#ffffff',
    other_lines_color: str = '#ffe735',
    effects: bool = True,
    uppercase: bool = True,
):
    from moviepy import ImageClip

    return ImageClip(
        _title_image(
            title,
            max_width,
            font_path,
            font_size,
            first_line_color,
            other_lines_color,
            effects,
            uppercase=uppercase,
        )
    ).with_duration(duration)


def _watermark_settings(data):
    text = data.get('watermark_text', '')
    color = data.get('watermark_color', '#a4e9ee')
    if not isinstance(text, str) or len(text) > 80:
        raise ValueError('Watermark text must be at most 80 characters.')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Watermark color must use #RRGGBB format.')
    return text.strip(), color


def _watermark_font_path(data):
    # Validate the watermark's own font against the bundled filenames.
    return _title_settings({'title_font': data.get('watermark_font', 'DIN Condensed.ttf')})[0]


def _round_watermark(image, diameter=230):
    """Center-crop the logo to a circle while preserving existing transparency."""
    from PIL import Image, ImageChops, ImageDraw, ImageOps

    image = ImageOps.fit(image.convert('RGBA'), (diameter, diameter), method=Image.Resampling.LANCZOS)
    # Draw the mask at higher resolution for a smooth circular edge.
    mask = Image.new('L', (diameter * 4, diameter * 4))
    ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
    mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    image.putalpha(ImageChops.multiply(image.getchannel('A'), mask))
    return image


def _with_shadow(image):
    """Add a soft silhouette shadow while keeping transparent areas transparent."""
    from PIL import Image, ImageFilter

    image = image.convert('RGBA')
    canvas = Image.new('RGBA', (image.width + 48, image.height + 48))
    alpha = Image.new('L', canvas.size)
    alpha.paste(image.getchannel('A'), (24, 32))
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(10)).point(lambda p: round(p * .8)))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(image, (24, 24))
    return canvas


ALLOWED_STICKERS = (
    'none', 'auto', 'santa', 'gift', 'snowflake', 'pumpkin',
    'globe', 'compass', 'pin', 'plane',
    'money', 'chart', 'diamond', 'crown',
    'shield', 'swords', 'rocket', 'bolt',
    'fire', 'trophy', 'star', 'heart', 'skull',
    'bear', 'paw', 'tree', 'wolf',
    'beer', 'coffee', 'wine', 'pizza',
    'soccer', 'car', 'brain', 'book', 'sun',
)

TWEMOJI_CODEPOINTS: dict[str, str] = {
    'santa': '1f385',
    'gift': '1f381',
    'snowflake': '2744',
    'pumpkin': '1f383',
    'globe': '1f30d',
    'compass': '1f9ed',
    'pin': '1f4cd',
    'plane': '2708',
    'money': '1f4b0',
    'chart': '1f4c8',
    'diamond': '1f48e',
    'crown': '1f451',
    'shield': '1f6e1',
    'swords': '2694',
    'rocket': '1f680',
    'bolt': '26a1',
    'fire': '1f525',
    'trophy': '1f3c6',
    'star': '2b50',
    'heart': '2764',
    'skull': '1f480',
    'bear': '1f43b',
    'paw': '1f43e',
    'tree': '1f332',
    'wolf': '1f43a',
    'beer': '1f37a',
    'coffee': '2615',
    'wine': '1f377',
    'pizza': '1f355',
    'soccer': '26bd',
    'car': '1f697',
    'brain': '1f9e0',
    'book': '1f4da',
    'sun': '2600',
}

_AUTO_STICKER_THEMES: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (
        ('bear', 'bears', 'grizzly', 'panda', 'wolf', 'wolves', 'fox', 'deer', 'moose',
         'boar', 'lion', 'tiger', 'dog', 'dogs', 'cat', 'cats', 'pet', 'pets',
         'animal', 'animals', 'wildlife', 'wild', 'forest', 'tree', 'trees', 'woods',
         'nature', 'park', 'hunt', 'bird', 'fish', 'shark', 'snake', 'horse', 'cow',
         'ведмід', 'ведмед', 'вовк', 'тварин', 'ліс', 'природ', 'собак', 'кіт'),
        ('bear', 'tree'),
    ),
    (
        ('beer', 'beers', 'wine', 'alcohol', 'vodka', 'whiskey', 'drink', 'drinks',
         'pub', 'bar', 'coffee', 'tea', 'cafe', 'pizza', 'burger', 'food', 'cuisine',
         'cheese', 'meat', 'bread', 'milk', 'water',
         'пиво', 'вино', 'алкогол', 'кава', 'чай', 'їжа', 'страв'),
        ('beer', 'coffee'),
    ),
    (
        ('iq', 'smart', 'intelligent', 'brain', 'genius', 'education', 'school',
         'university', 'student', 'literacy', 'book', 'books', 'read', 'language',
         'languages', 'speak', 'word', 'words', 'alphabet', 'name', 'names',
         'population', 'people', 'human',
         'мова', 'освіта', 'розум', 'книг', 'населення', 'люд', 'імен'),
        ('brain', 'book'),
    ),
    (
        ('car', 'cars', 'auto', 'vehicle', 'drive', 'driving', 'highway', 'road',
         'roads', 'train', 'rail', 'railway', 'metro', 'bus', 'traffic', 'fuel',
         'gas', 'petrol', 'oil', 'tesla', 'bmw',
         'авто', 'машин', 'дорог', 'поїзд', 'транспорт', 'бензин', 'нафт'),
        ('car', 'bolt'),
    ),
    (
        ('sport', 'sports', 'football', 'soccer', 'fifa', 'uefa', 'world cup',
         'basketball', 'hockey', 'tennis', 'olympic', 'olympics', 'medal', 'athlete',
         'gym', 'game', 'games', 'chess',
         'спорт', 'футбол', 'олімп', 'медал', 'гра', 'ігр'),
        ('soccer', 'trophy'),
    ),
    (
        ('cold', 'coldest', 'freeze', 'freezing', 'frost', 'ice', 'icy', 'snow',
         'snowy', 'blizzard', 'minus', 'subzero', 'arctic', 'glacier', 'chill',
         'холод', 'найхолод', 'мороз', 'сніг', 'лід', 'арктик'),
        ('snowflake', 'globe'),
    ),
    (
        ('weather', 'temperature', 'climate', 'sun', 'sunny', 'hot', 'hottest',
         'heat', 'warm', 'warmest', 'summer', 'rain', 'wind', 'celsius', 'desert',
         'погод', 'клімат', 'температур', 'сонц', 'спека', 'літо', 'тепл'),
        ('sun', 'compass'),
    ),
    (
        ('sea', 'ocean', 'river', 'lake', 'coast', 'island', 'water', 'mountain', 'mountains',
         'море', 'океан', 'річк', 'озеро', 'острів', 'вода', 'гор'),
        ('compass', 'globe'),
    ),
    (
        ('religion', 'christian', 'catholic', 'orthodox', 'islam', 'muslim', 'church',
         'mosque', 'god', 'faith', 'реліг', 'християн', 'церкв', 'іслам'),
        ('star', 'globe'),
    ),
    (
        ('nicholas', 'santa', 'christmas', 'xmas', 'winter', 'holiday', 'new year',
         'миколая', 'різдв', 'санта', 'зима', 'свято', 'новий рік'),
        ('santa', 'gift'),
    ),
    (
        ('halloween', 'spooky', 'horror', 'геловін', 'страш'),
        ('pumpkin', 'skull'),
    ),
    (
        ('gdp', 'rich', 'poor', 'money', 'salary', 'wage', 'economy', 'tax',
         'price', 'cost', 'billion', 'wealth',
         'ввп', 'багат', 'бідн', 'грош', 'зарплат', 'економік', 'цін', 'подат'),
        ('money', 'chart'),
    ),
    (
        ('war', 'army', 'military', 'empire', 'king', 'history', 'battle', 'nato',
         'війн', 'армі', 'імпері', 'історі', 'битв', 'королів'),
        ('shield', 'swords'),
    ),
    (
        ('queen', 'monarch', 'royal', 'president', 'power', 'ruler',
         'монарх', 'корол', 'президент', 'влад'),
        ('crown', 'star'),
    ),
    (
        ('space', 'nasa', 'tech', 'ai', 'internet', 'speed', 'energy', 'nuclear', 'science',
         'космос', 'технолог', 'інтернет', 'швидк', 'енерг', 'наук'),
        ('rocket', 'bolt'),
    ),
    (
        ('travel', 'tourist', 'visa', 'passport', 'flight', 'airport', 'visit',
         'подорож', 'туризм', 'віза', 'паспорт', 'літак'),
        ('plane', 'compass'),
    ),
    (
        ('danger', 'crime', 'dead', 'worst', 'risk',
         'небезпек', 'злочин', 'смерт', 'ризик'),
        ('skull', 'fire'),
    ),
    (
        ('top', 'best', 'most', 'highest', 'winner', 'champion', 'rank',
         'топ', 'найкращ', 'найбільш', 'рейтинг', 'чемпіон'),
        ('trophy', 'fire'),
    ),
    (
        ('happy', 'happi', 'happiness', 'love', 'health', 'life', 'safe',
         'щаст', 'любов', 'здоров', 'житт', 'безпек'),
        ('heart', 'star'),
    ),
)

_EXACT_WORD_KEYWORDS = {
    'ai', 'gdp', 'nato', 'top', 'iq', 'car', 'sun', 'pet', 'dog', 'cat',
    'bar', 'pub', 'tea', 'oil', 'gas', 'bus', 'gym', 'ice', 'hot', 'god',
    'sea', 'топ', 'ввп',
}


def _resolve_auto_stickers(title: str) -> tuple[str, str]:
    """Choose a thematic (left, right) sticker pair based on English or Ukrainian title keywords."""
    lowered = (title or '').lower()
    if not lowered.strip():
        return ('globe', 'pin')
    best_pair: tuple[str, str] | None = None
    best_count = 0
    for keywords, pair in _AUTO_STICKER_THEMES:
        count = 0
        for kw in keywords:
            if kw in _EXACT_WORD_KEYWORDS:
                pattern = rf'(?<!\w){re.escape(kw)}(?!\w)'
            elif kw == 'war':
                pattern = r'(?<!\w)war(?:s|fare|rior|riors)?(?!\w)'
            elif kw == 'king':
                pattern = r'(?<!\w)king(?:s|dom|doms)?(?!\w)'
            else:
                pattern = rf'(?<!\w){re.escape(kw)}'
            if re.search(pattern, lowered):
                count += 1
        if count > best_count:
            best_count = count
            best_pair = pair
    return best_pair if best_pair is not None else ('globe', 'pin')


def _star_polygon(cx: float, cy: float, outer_r: float, inner_r: float) -> list[tuple[float, float]]:
    """Return the 10 vertices of a 5-pointed star centered at (cx, cy)."""
    import math

    points: list[tuple[float, float]] = []
    for i in range(10):
        angle = -math.pi / 2 + i * (math.pi / 5)
        r = outer_r if i % 2 == 0 else inner_r
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def _draw_heart(draw, cx: float, cy: float, size: float, fill: tuple[int, int, int, int],
                outline: tuple[int, int, int, int] = (0, 0, 0, 255), width: int = 4) -> None:
    """Draw a smooth vector heart icon centered at (cx, cy)."""
    import math

    pts: list[tuple[float, float]] = []
    for step in range(64):
        t = (step / 64.0) * 2.0 * math.pi
        x = 16.0 * (math.sin(t) ** 3)
        y = -(13.0 * math.cos(t) - 5.0 * math.cos(2 * t) - 2.0 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * (size / 34.0), cy + y * (size / 34.0)))
    draw.polygon(pts, fill=fill, outline=outline, width=width)


def _subscribe_badge_image(
    text: str = 'Subscribe!',
    font_path: str = TITLE_FONT_PATH,
    effects: bool = True,
):
    """Render the dark rounded '[ ❤️ Subscribe! ▶️ ]' call-to-action pill badge."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    label = (text or 'Subscribe!').strip()[:40] or 'Subscribe!'
    scale = 2
    font_size = 52 * scale
    font = ImageFont.truetype(font_path, font_size)
    measure = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    bbox = measure.textbbox((0, 0), label, font=font, stroke_width=3 * scale)
    text_w = bbox[2] - bbox[0]

    heart_slot = 64 * scale
    play_w, play_h = 68 * scale, 48 * scale
    pad_x = 28 * scale
    gap = 18 * scale
    pill_h = 84 * scale
    pill_w = pad_x * 2 + heart_slot + gap * 2 + text_w + play_w

    canvas = Image.new('RGBA', (pill_w + 16 * scale, pill_h + 16 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    ox, oy = 8 * scale, 8 * scale

    # Rounded dark charcoal pill with subtle glossy border
    draw.rounded_rectangle(
        (ox, oy, ox + pill_w, oy + pill_h),
        radius=26 * scale,
        fill=(28, 30, 36, 228),
        outline=(255, 255, 255, 58),
        width=2 * scale,
    )

    # Left icon: glossy red heart
    heart_cx = ox + pad_x + heart_slot / 2
    heart_cy = oy + pill_h / 2 + 2 * scale
    _draw_heart(
        draw,
        heart_cx,
        heart_cy,
        54 * scale,
        fill=(255, 42, 75, 255),
        outline=(15, 15, 20, 255),
        width=3 * scale,
    )
    # Specular highlight on top-left lobe of heart
    draw.ellipse(
        (heart_cx - 16 * scale, heart_cy - 16 * scale, heart_cx - 6 * scale, heart_cy - 7 * scale),
        fill=(255, 190, 200, 190),
    )

    # Center text: bold white Subscribe! text with dark stroke
    text_x = ox + pad_x + heart_slot + gap + text_w / 2
    text_y = oy + pill_h / 2
    draw.text(
        (text_x, text_y),
        label,
        font=font,
        anchor='mm',
        fill=(255, 255, 255, 255),
        stroke_width=3 * scale,
        stroke_fill=(0, 0, 0, 240),
    )

    # Right icon: red YouTube-style rounded play button
    play_x0 = ox + pad_x + heart_slot + gap * 2 + text_w
    play_y0 = oy + (pill_h - play_h) / 2
    draw.rounded_rectangle(
        (play_x0, play_y0, play_x0 + play_w, play_y0 + play_h),
        radius=12 * scale,
        fill=(255, 32, 32, 255),
        outline=(15, 15, 20, 255),
        width=3 * scale,
    )
    tri_cx = play_x0 + play_w / 2 + 2 * scale
    tri_cy = play_y0 + play_h / 2
    tri = [
        (tri_cx - 11 * scale, tri_cy - 12 * scale),
        (tri_cx - 11 * scale, tri_cy + 12 * scale),
        (tri_cx + 13 * scale, tri_cy),
    ]
    draw.polygon(tri, fill=(255, 255, 255, 255))

    final_img = canvas.resize((canvas.width // scale, canvas.height // scale), Image.Resampling.LANCZOS)
    if effects:
        final_img = _with_shadow(final_img)
    return np.array(final_img)


def _stars_row_image(color: str = '#ffffff', effects: bool = True):
    """Render three 5-pointed stars ('★ ★ ★') for the bottom watermark footer."""
    import numpy as np
    from PIL import Image, ImageColor, ImageDraw

    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        color = '#ffffff'
    rgb = ImageColor.getrgb(color)

    scale = 2
    w, h = 320 * scale, 76 * scale
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    centers_x = (w * 0.22, w * 0.50, w * 0.78)
    cy = h * 0.52
    for cx in centers_x:
        pts = _star_polygon(cx, cy, outer_r=26 * scale, inner_r=11 * scale)
        draw.polygon(
            pts,
            fill=rgb + (255,),
            outline=(0, 0, 0, 240) if effects else rgb + (220,),
            width=4 * scale if effects else 2 * scale,
        )

    final_img = canvas.resize((w // scale, h // scale), Image.Resampling.LANCZOS)
    if effects:
        final_img = _with_shadow(final_img)
    return np.array(final_img)


def _parse_sticker_spec(raw_kind: str, title: str = '', side: str = 'left') -> tuple[str | None, str]:
    """Parse a sticker specification into ``(gen_style_or_none, base_kind)``."""
    raw = (raw_kind or 'none').strip().lower()
    gen_style: str | None = None
    if ':' in raw:
        prefix, _, base_kind = raw.partition(':')
        if prefix in ('gen', 'gen_diecut', 'gen_neon', 'gen_stamp'):
            gen_style = 'diecut' if prefix == 'gen' else prefix.removeprefix('gen_')
            raw = base_kind or 'auto'
    if raw == 'auto':
        left_auto, right_auto = _resolve_auto_stickers(title)
        raw = right_auto if side == 'right' else left_auto
    return gen_style, raw


def _style_generated_sticker(icon_canvas, gen_style: str, size: int):
    """Wrap a procedural vector icon in a generated sticker style (die-cut, neon, or stamp)."""
    from PIL import Image, ImageDraw, ImageFilter

    s = icon_canvas.width
    if gen_style == 'diecut':
        # Create a crisp white vinyl die-cut sticker border around the vector silhouette.
        alpha = icon_canvas.getchannel('A')
        dilate = max(3, int(round(s * 0.055))) | 1
        border_mask = alpha.filter(ImageFilter.MaxFilter(dilate))
        vinyl = Image.new('RGBA', (s, s), (255, 255, 255, 0))
        vinyl.putalpha(border_mask)
        white_layer = Image.new('RGBA', (s, s), (252, 253, 255, 255))
        white_layer.putalpha(border_mask)
        styled = Image.alpha_composite(white_layer, icon_canvas)
        styled = styled.rotate(6, resample=Image.Resampling.BICUBIC, expand=False)
        return styled.resize((size, size), Image.Resampling.LANCZOS)

    if gen_style == 'neon':
        badge = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        glow = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        pad = int(s * 0.08)
        gdraw.ellipse((pad, pad, s - pad, s - pad), outline=(56, 189, 248, 230), width=int(s * 0.055))
        glow = glow.filter(ImageFilter.GaussianBlur(max(4, s // 28)))
        bdraw = ImageDraw.Draw(badge)
        bdraw.ellipse((pad, pad, s - pad, s - pad), fill=(15, 23, 42, 238), outline=(99, 102, 241, 255), width=int(s * 0.032))
        bdraw.ellipse((pad + 6, pad + 6, s - pad - 6, s - pad - 6), outline=(56, 189, 248, 210), width=max(2, s // 70))
        badge = Image.alpha_composite(glow, badge)
        inner_size = int(s * 0.68)
        icon_small = icon_canvas.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
        offset = (s - inner_size) // 2
        badge.alpha_composite(icon_small, (offset, offset))
        return badge.resize((size, size), Image.Resampling.LANCZOS)

    # 'stamp': tilted editorial badge with double accent frame and corner sparkles
    stamp = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stamp)
    pad = int(s * 0.08)
    sdraw.rounded_rectangle((pad, pad, s - pad, s - pad), radius=int(s * 0.22),
                            fill=(30, 27, 75, 235), outline=(251, 191, 36, 255), width=int(s * 0.03))
    sdraw.rounded_rectangle((pad + 10, pad + 10, s - pad - 10, s - pad - 10), radius=int(s * 0.17),
                            outline=(255, 255, 255, 110), width=max(2, s // 80))
    # Tiny decorative sparkle stars in top corners
    for sx, sy in ((s * 0.22, s * 0.22), (s * 0.78, s * 0.22)):
        sdraw.polygon(_star_polygon(sx, sy, s * 0.055, s * 0.022), fill=(253, 224, 71, 240))
    inner_size = int(s * 0.68)
    icon_small = icon_canvas.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
    offset = (s - inner_size) // 2
    stamp.alpha_composite(icon_small, (offset, offset + int(s * 0.03)))
    stamp = stamp.rotate(-6, resample=Image.Resampling.BICUBIC, expand=False)
    return stamp.resize((size, size), Image.Resampling.LANCZOS)


def _sticker_image(kind: str, size: int = 140, effects: bool = True, allow_remote: bool = False):
    """Render a decorative top-corner sticker (library Twemoji or procedural generated style)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageOps

    gen_style, kind = _parse_sticker_spec(kind)
    if kind not in ALLOWED_STICKERS or kind == 'none':
        return None

    # Library stickers (without a gen_ prefix) use Twemoji cache/CDN when available.
    codepoint = None if gen_style else TWEMOJI_CODEPOINTS.get(kind)
    if codepoint:
        stickers_dir = os.path.join(STORAGE_DIR, 'stickers')
        cache_path = os.path.join(stickers_dir, f'{kind}.png')
        if not os.path.isfile(cache_path) and allow_remote:
            try:
                import requests
                os.makedirs(stickers_dir, exist_ok=True)
                url = f'https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{codepoint}.png'
                resp = requests.get(url, timeout=1.5)
                if resp.status_code == 200 and resp.content:
                    with Image.open(io.BytesIO(resp.content)) as fetched:
                        fetched.convert('RGBA').save(cache_path, format='PNG')
            except Exception:
                pass
        if os.path.isfile(cache_path):
            try:
                with Image.open(cache_path) as cached:
                    final_img = ImageOps.contain(cached.convert('RGBA'), (size, size), method=Image.Resampling.LANCZOS)
                    if final_img.size != (size, size):
                        padded = Image.new('RGBA', (size, size), (0, 0, 0, 0))
                        padded.paste(final_img, ((size - final_img.width) // 2, (size - final_img.height) // 2))
                        final_img = padded
                    if effects:
                        final_img = _with_shadow(final_img)
                    return np.array(final_img)
            except Exception:
                pass

    scale = 2
    s = size * scale
    canvas = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    outline = (18, 18, 24, 255)
    ow = 4 * scale

    if kind == 'santa':
        # Fluffy white beard base
        draw.ellipse((s * 0.16, s * 0.44, s * 0.84, s * 0.94), fill=(250, 252, 255, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.12, s * 0.52, s * 0.46, s * 0.84), fill=(250, 252, 255, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.54, s * 0.52, s * 0.88, s * 0.84), fill=(250, 252, 255, 255), outline=outline, width=ow)
        # Warm face
        draw.ellipse((s * 0.24, s * 0.30, s * 0.76, s * 0.72), fill=(255, 214, 186, 255), outline=outline, width=ow)
        # Re-fill inner beard over lower face
        draw.chord((s * 0.18, s * 0.42, s * 0.82, s * 0.92), start=0, end=180, fill=(250, 252, 255, 255), outline=outline, width=ow)
        # Rosy cheeks & eyes
        draw.ellipse((s * 0.28, s * 0.48, s * 0.39, s * 0.56), fill=(255, 138, 138, 210))
        draw.ellipse((s * 0.61, s * 0.48, s * 0.72, s * 0.56), fill=(255, 138, 138, 210))
        draw.ellipse((s * 0.35, s * 0.41, s * 0.42, s * 0.48), fill=outline)
        draw.ellipse((s * 0.58, s * 0.41, s * 0.65, s * 0.48), fill=outline)
        # Mustache & rosy nose
        draw.ellipse((s * 0.30, s * 0.53, s * 0.52, s * 0.65), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.48, s * 0.53, s * 0.70, s * 0.65), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.44, s * 0.47, s * 0.56, s * 0.57), fill=(244, 92, 92, 255), outline=outline, width=ow // 2)
        # Red Santa hat & white fur brim + pom-pom
        hat_pts = [(s * 0.18, s * 0.34), (s * 0.52, s * 0.06), (s * 0.86, s * 0.28), (s * 0.80, s * 0.36)]
        draw.polygon(hat_pts, fill=(228, 34, 46, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.15, s * 0.27, s * 0.83, s * 0.40), radius=14 * scale, fill=(252, 253, 255, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.76, s * 0.18, s * 0.94, s * 0.36), fill=(252, 253, 255, 255), outline=outline, width=ow)

    elif kind == 'gift':
        # Candy cane behind top-right of gift box
        draw.arc((s * 0.52, s * 0.06, s * 0.90, s * 0.46), start=180, end=20, fill=(235, 38, 50, 255), width=12 * scale)
        # Golden ribbon bow Loops on top
        draw.ellipse((s * 0.24, s * 0.14, s * 0.52, s * 0.38), fill=(255, 204, 42, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.48, s * 0.14, s * 0.76, s * 0.38), fill=(255, 204, 42, 255), outline=outline, width=ow)
        # Red gift box body
        draw.rounded_rectangle((s * 0.18, s * 0.36, s * 0.82, s * 0.90), radius=12 * scale, fill=(224, 36, 48, 255), outline=outline, width=ow)
        # Gift box lid
        draw.rounded_rectangle((s * 0.14, s * 0.28, s * 0.86, s * 0.44), radius=10 * scale, fill=(242, 52, 64, 255), outline=outline, width=ow)
        # Vertical & horizontal gold ribbon stripes
        draw.rectangle((s * 0.44, s * 0.28, s * 0.56, s * 0.90), fill=(255, 208, 46, 255), outline=outline, width=ow // 2)
        draw.rectangle((s * 0.18, s * 0.58, s * 0.82, s * 0.69), fill=(255, 208, 46, 255), outline=outline, width=ow // 2)
        # Holly leaves & berry accent
        draw.ellipse((s * 0.64, s * 0.20, s * 0.88, s * 0.34), fill=(40, 184, 86, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.44, s * 0.22, s * 0.56, s * 0.34), fill=(255, 230, 90, 255), outline=outline, width=ow // 2)

    elif kind == 'snowflake':
        import math
        draw.ellipse((s * 0.14, s * 0.14, s * 0.86, s * 0.86), fill=(48, 168, 255, 255), outline=outline, width=ow)
        cx, cy = s * 0.50, s * 0.50
        for i in range(6):
            ang = i * (math.pi / 3)
            x2 = cx + s * 0.30 * math.cos(ang)
            y2 = cy + s * 0.30 * math.sin(ang)
            draw.line((cx, cy, x2, y2), fill=(255, 255, 255, 255), width=6 * scale)
            bx = cx + s * 0.19 * math.cos(ang)
            by = cy + s * 0.19 * math.sin(ang)
            for delta in (-0.55, 0.55):
                tx = bx + s * 0.09 * math.cos(ang + delta)
                ty = by + s * 0.09 * math.sin(ang + delta)
                draw.line((bx, by, tx, ty), fill=(255, 255, 255, 255), width=4 * scale)
        draw.ellipse((s * 0.42, s * 0.42, s * 0.58, s * 0.58), fill=(225, 248, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'pumpkin':
        # Stem
        draw.polygon([(s * 0.45, s * 0.10), (s * 0.57, s * 0.10), (s * 0.54, s * 0.28), (s * 0.46, s * 0.28)],
                     fill=(46, 164, 72, 255), outline=outline, width=ow)
        # Ribbed orange pumpkin lobes
        draw.ellipse((s * 0.12, s * 0.24, s * 0.58, s * 0.88), fill=(242, 116, 28, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.42, s * 0.24, s * 0.88, s * 0.88), fill=(242, 116, 28, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.26, s * 0.22, s * 0.74, s * 0.90), fill=(255, 138, 36, 255), outline=outline, width=ow)
        # Glowing jack-o'-lantern eyes & grin
        draw.polygon([(s * 0.30, s * 0.48), (s * 0.40, s * 0.36), (s * 0.45, s * 0.48)], fill=(255, 232, 64, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.55, s * 0.48), (s * 0.60, s * 0.36), (s * 0.70, s * 0.48)], fill=(255, 232, 64, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.28, s * 0.64), (s * 0.50, s * 0.72), (s * 0.72, s * 0.64), (s * 0.64, s * 0.78), (s * 0.36, s * 0.78)],
                     fill=(255, 232, 64, 255), outline=outline, width=ow // 2)

    elif kind == 'globe':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(36, 146, 245, 255), outline=outline, width=ow)
        # Stylized green continents
        draw.polygon([(s * 0.26, s * 0.30), (s * 0.46, s * 0.24), (s * 0.48, s * 0.46), (s * 0.34, s * 0.66), (s * 0.22, s * 0.50)], fill=(58, 204, 112, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.56, s * 0.28), (s * 0.78, s * 0.36), (s * 0.74, s * 0.68), (s * 0.54, s * 0.58)], fill=(58, 204, 112, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), outline=outline, width=ow)

    elif kind == 'compass':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(245, 194, 48, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.20, s * 0.20, s * 0.80, s * 0.80), fill=(30, 42, 62, 255), outline=outline, width=ow // 2)
        # North red pointer & South white pointer
        draw.polygon([(s * 0.50, s * 0.24), (s * 0.60, s * 0.50), (s * 0.40, s * 0.50)], fill=(244, 54, 68, 255), outline=outline, width=ow // 2)
        draw.polygon([(s * 0.50, s * 0.76), (s * 0.60, s * 0.50), (s * 0.40, s * 0.50)], fill=(245, 248, 255, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.45, s * 0.45, s * 0.55, s * 0.55), fill=(255, 214, 64, 255), outline=outline, width=ow // 2)

    elif kind == 'pin':
        draw.polygon([(s * 0.24, s * 0.46), (s * 0.50, s * 0.90), (s * 0.76, s * 0.46)], fill=(238, 42, 62, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.20, s * 0.12, s * 0.80, s * 0.68), fill=(238, 42, 62, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.36, s * 0.26, s * 0.64, s * 0.54), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'plane':
        # Sky badge + white/blue jet plane
        draw.ellipse((s * 0.14, s * 0.14, s * 0.86, s * 0.86), fill=(46, 156, 255, 255), outline=outline, width=ow)
        wings = [(s * 0.18, s * 0.56), (s * 0.82, s * 0.24), (s * 0.64, s * 0.82), (s * 0.48, s * 0.58), (s * 0.32, s * 0.68)]
        draw.polygon(wings, fill=(250, 252, 255, 255), outline=outline, width=ow)

    elif kind == 'money':
        # Tie top + plump golden money sack
        draw.polygon([(s * 0.36, s * 0.14), (s * 0.64, s * 0.14), (s * 0.58, s * 0.30), (s * 0.42, s * 0.30)],
                     fill=(238, 188, 52, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.18, s * 0.26, s * 0.82, s * 0.90), fill=(246, 198, 58, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.34, s * 0.44, s * 0.66, s * 0.76), fill=(46, 192, 96, 255), outline=outline, width=ow // 2)

    elif kind == 'chart':
        draw.rounded_rectangle((s * 0.12, s * 0.14, s * 0.88, s * 0.88), radius=14 * scale, fill=(245, 248, 255, 255), outline=outline, width=ow)
        draw.rectangle((s * 0.22, s * 0.56, s * 0.36, s * 0.80), fill=(52, 152, 245, 255), outline=outline, width=ow // 2)
        draw.rectangle((s * 0.43, s * 0.40, s * 0.57, s * 0.80), fill=(46, 204, 113, 255), outline=outline, width=ow // 2)
        draw.rectangle((s * 0.64, s * 0.24, s * 0.78, s * 0.80), fill=(255, 196, 42, 255), outline=outline, width=ow // 2)

    elif kind == 'diamond':
        gem = [(s * 0.28, s * 0.18), (s * 0.72, s * 0.18), (s * 0.90, s * 0.40), (s * 0.50, s * 0.88), (s * 0.10, s * 0.40)]
        draw.polygon(gem, fill=(56, 214, 255, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.28, s * 0.18), (s * 0.72, s * 0.18), (s * 0.64, s * 0.40), (s * 0.36, s * 0.40)],
                     fill=(165, 242, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'shield':
        pts = [(s * 0.18, s * 0.14), (s * 0.82, s * 0.14), (s * 0.82, s * 0.50), (s * 0.50, s * 0.90), (s * 0.18, s * 0.50)]
        draw.polygon(pts, fill=(42, 118, 238, 255), outline=outline, width=ow)
        inner = [(s * 0.28, s * 0.22), (s * 0.72, s * 0.22), (s * 0.72, s * 0.48), (s * 0.50, s * 0.78), (s * 0.28, s * 0.48)]
        draw.polygon(inner, fill=(255, 204, 46, 255), outline=outline, width=ow // 2)

    elif kind == 'swords':
        # Crossed silver blades with gold guards
        draw.polygon([(s * 0.16, s * 0.22), (s * 0.24, s * 0.14), (s * 0.84, s * 0.74), (s * 0.76, s * 0.82)],
                     fill=(224, 232, 245, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.84, s * 0.22), (s * 0.76, s * 0.14), (s * 0.16, s * 0.74), (s * 0.24, s * 0.82)],
                     fill=(224, 232, 245, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.40, s * 0.40, s * 0.60, s * 0.60), fill=(255, 204, 46, 255), outline=outline, width=ow)

    elif kind == 'rocket':
        # Exhaust flame
        draw.polygon([(s * 0.38, s * 0.72), (s * 0.50, s * 0.94), (s * 0.62, s * 0.72)], fill=(255, 112, 34, 255), outline=outline, width=ow // 2)
        # Side fins
        draw.polygon([(s * 0.20, s * 0.74), (s * 0.34, s * 0.52), (s * 0.36, s * 0.74)], fill=(235, 44, 58, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.80, s * 0.74), (s * 0.66, s * 0.52), (s * 0.64, s * 0.74)], fill=(235, 44, 58, 255), outline=outline, width=ow)
        # Rocket fuselage & nose cone
        draw.polygon([(s * 0.50, s * 0.08), (s * 0.68, s * 0.34), (s * 0.66, s * 0.74), (s * 0.34, s * 0.74), (s * 0.32, s * 0.34)],
                     fill=(248, 250, 255, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.08), (s * 0.66, s * 0.30), (s * 0.34, s * 0.30)],
                     fill=(235, 44, 58, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.40, s * 0.38, s * 0.60, s * 0.58), fill=(58, 198, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'bolt':
        bolt_pts = [
            (s * 0.56, s * 0.08),
            (s * 0.22, s * 0.54),
            (s * 0.48, s * 0.54),
            (s * 0.38, s * 0.92),
            (s * 0.78, s * 0.44),
            (s * 0.52, s * 0.44),
        ]
        draw.polygon(bolt_pts, fill=(255, 214, 36, 255), outline=outline, width=ow)

    elif kind == 'fire':
        outer_flame = [(s * 0.50, s * 0.08), (s * 0.82, s * 0.48), (s * 0.74, s * 0.84), (s * 0.50, s * 0.92), (s * 0.26, s * 0.84), (s * 0.18, s * 0.48)]
        inner_flame = [(s * 0.50, s * 0.32), (s * 0.68, s * 0.58), (s * 0.62, s * 0.82), (s * 0.50, s * 0.88), (s * 0.38, s * 0.82), (s * 0.32, s * 0.58)]
        draw.polygon(outer_flame, fill=(255, 86, 34, 255), outline=outline, width=ow)
        draw.polygon(inner_flame, fill=(255, 214, 46, 255), outline=outline, width=ow // 2)

    elif kind == 'trophy':
        # Handles
        draw.ellipse((s * 0.12, s * 0.22, s * 0.40, s * 0.52), fill=(255, 204, 38, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.60, s * 0.22, s * 0.88, s * 0.52), fill=(255, 204, 38, 255), outline=outline, width=ow)
        # Cup body + stem + base
        draw.polygon([(s * 0.24, s * 0.16), (s * 0.76, s * 0.16), (s * 0.66, s * 0.58), (s * 0.34, s * 0.58)],
                     fill=(255, 214, 46, 255), outline=outline, width=ow)
        draw.rectangle((s * 0.44, s * 0.56, s * 0.56, s * 0.76), fill=(235, 180, 28, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.28, s * 0.74, s * 0.72, s * 0.88), radius=8 * scale, fill=(215, 152, 24, 255), outline=outline, width=ow)

    elif kind == 'crown':
        crown_pts = [
            (s * 0.14, s * 0.78),
            (s * 0.10, s * 0.28),
            (s * 0.34, s * 0.52),
            (s * 0.50, s * 0.18),
            (s * 0.66, s * 0.52),
            (s * 0.90, s * 0.28),
            (s * 0.86, s * 0.78),
        ]
        draw.polygon(crown_pts, fill=(255, 204, 38, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.14, s * 0.74, s * 0.86, s * 0.88), radius=8 * scale, fill=(235, 168, 20, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.45, s * 0.56, s * 0.55, s * 0.66), fill=(244, 42, 75, 255), outline=outline, width=ow // 2)

    elif kind == 'star':
        pts = _star_polygon(s * 0.50, s * 0.52, outer_r=s * 0.40, inner_r=s * 0.18)
        draw.polygon(pts, fill=(255, 216, 46, 255), outline=outline, width=ow)

    elif kind == 'heart':
        _draw_heart(draw, s * 0.50, s * 0.52, s * 0.78, fill=(255, 42, 78, 255), outline=outline, width=ow)

    elif kind == 'skull':
        draw.rounded_rectangle((s * 0.32, s * 0.56, s * 0.68, s * 0.88), radius=10 * scale, fill=(245, 247, 252, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.18, s * 0.14, s * 0.82, s * 0.74), fill=(245, 247, 252, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.28, s * 0.36, s * 0.44, s * 0.52), fill=outline)
        draw.ellipse((s * 0.56, s * 0.36, s * 0.72, s * 0.52), fill=outline)
        draw.polygon([(s * 0.50, s * 0.50), (s * 0.45, s * 0.60), (s * 0.55, s * 0.60)], fill=outline)

    elif kind == 'bear':
        # Round ears + warm inner ears
        draw.ellipse((s * 0.12, s * 0.12, s * 0.42, s * 0.42), fill=(142, 82, 42, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.58, s * 0.12, s * 0.88, s * 0.42), fill=(142, 82, 42, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.19, s * 0.19, s * 0.35, s * 0.35), fill=(216, 158, 108, 255))
        draw.ellipse((s * 0.65, s * 0.19, s * 0.81, s * 0.35), fill=(216, 158, 108, 255))
        # Plump brown bear head
        draw.ellipse((s * 0.16, s * 0.22, s * 0.84, s * 0.88), fill=(158, 94, 48, 255), outline=outline, width=ow)
        # Light tan muzzle + dark nose
        draw.ellipse((s * 0.32, s * 0.48, s * 0.68, s * 0.78), fill=(234, 192, 146, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.42, s * 0.50, s * 0.58, s * 0.62), fill=outline)
        # Eyes
        draw.ellipse((s * 0.31, s * 0.38, s * 0.39, s * 0.46), fill=outline)
        draw.ellipse((s * 0.61, s * 0.38, s * 0.69, s * 0.46), fill=outline)

    elif kind == 'paw':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(252, 211, 77, 255), outline=outline, width=ow)
        pad_color = (120, 53, 15, 255)
        draw.ellipse((s * 0.30, s * 0.48, s * 0.70, s * 0.78), fill=pad_color, outline=outline, width=ow // 2)
        for px, py in ((0.24, 0.34), (0.39, 0.24), (0.61, 0.24), (0.76, 0.34)):
            draw.ellipse((s * (px - 0.08), s * (py - 0.09), s * (px + 0.08), s * (py + 0.09)), fill=pad_color, outline=outline, width=ow // 2)

    elif kind == 'tree':
        draw.rectangle((s * 0.42, s * 0.68, s * 0.58, s * 0.90), fill=(132, 78, 42, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.34), (s * 0.86, s * 0.74), (s * 0.14, s * 0.74)], fill=(22, 163, 74, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.20), (s * 0.80, s * 0.56), (s * 0.20, s * 0.56)], fill=(34, 197, 94, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.50, s * 0.08), (s * 0.72, s * 0.38), (s * 0.28, s * 0.38)], fill=(74, 222, 128, 255), outline=outline, width=ow)

    elif kind == 'wolf':
        draw.polygon([(s * 0.18, s * 0.42), (s * 0.22, s * 0.10), (s * 0.42, s * 0.28)], fill=(100, 116, 139, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.82, s * 0.42), (s * 0.78, s * 0.10), (s * 0.58, s * 0.28)], fill=(100, 116, 139, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.16, s * 0.38), (s * 0.50, s * 0.22), (s * 0.84, s * 0.38), (s * 0.76, s * 0.68), (s * 0.50, s * 0.88), (s * 0.24, s * 0.68)],
                     fill=(148, 163, 184, 255), outline=outline, width=ow)
        draw.polygon([(s * 0.34, s * 0.54), (s * 0.50, s * 0.88), (s * 0.66, s * 0.54)], fill=(241, 245, 249, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.32, s * 0.42, s * 0.42, s * 0.50), fill=(250, 204, 21, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.58, s * 0.42, s * 0.68, s * 0.50), fill=(250, 204, 21, 255), outline=outline, width=ow // 2)
        draw.ellipse((s * 0.44, s * 0.74, s * 0.56, s * 0.84), fill=outline)

    elif kind == 'beer':
        draw.rounded_rectangle((s * 0.62, s * 0.38, s * 0.86, s * 0.76), radius=12 * scale, outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.22, s * 0.28, s * 0.70, s * 0.88), radius=10 * scale, fill=(245, 158, 11, 255), outline=outline, width=ow)
        for fx in (0.28, 0.44, 0.60):
            draw.ellipse((s * (fx - 0.12), s * 0.14, s * (fx + 0.12), s * 0.38), fill=(255, 255, 255, 255), outline=outline, width=ow // 2)

    elif kind == 'coffee':
        draw.rounded_rectangle((s * 0.64, s * 0.42, s * 0.86, s * 0.70), radius=12 * scale, outline=outline, width=ow)
        draw.chord((s * 0.18, s * 0.28, s * 0.74, s * 0.84), start=0, end=180, fill=(248, 250, 252, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.18, s * 0.32, s * 0.74, s * 0.48), fill=(120, 53, 15, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.12, s * 0.78, s * 0.82, s * 0.88), radius=6 * scale, fill=(226, 232, 240, 255), outline=outline, width=ow)

    elif kind == 'wine':
        draw.polygon([(s * 0.28, s * 0.14), (s * 0.72, s * 0.14), (s * 0.64, s * 0.54), (s * 0.36, s * 0.54)], fill=(225, 29, 72, 255), outline=outline, width=ow)
        draw.rectangle((s * 0.47, s * 0.54, s * 0.53, s * 0.82), fill=(226, 232, 240, 255), outline=outline, width=ow // 2)
        draw.rounded_rectangle((s * 0.32, s * 0.80, s * 0.68, s * 0.88), radius=6 * scale, fill=(226, 232, 240, 255), outline=outline, width=ow)

    elif kind == 'pizza':
        draw.polygon([(s * 0.50, s * 0.88), (s * 0.16, s * 0.22), (s * 0.84, s * 0.22)], fill=(250, 204, 21, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.14, s * 0.14, s * 0.86, s * 0.28), radius=10 * scale, fill=(217, 119, 6, 255), outline=outline, width=ow)
        for px, py in ((0.40, 0.40), (0.58, 0.44), (0.48, 0.62)):
            draw.ellipse((s * (px - 0.07), s * (py - 0.07), s * (px + 0.07), s * (py + 0.07)), fill=(220, 38, 38, 255), outline=outline, width=ow // 2)

    elif kind == 'soccer':
        draw.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(248, 250, 252, 255), outline=outline, width=ow)
        pent = _star_polygon(s * 0.50, s * 0.50, outer_r=s * 0.18, inner_r=s * 0.15)
        draw.polygon(pent, fill=outline)
        for vx, vy in pent[::2]:
            draw.line((vx, vy, s * 0.50 + (vx - s * 0.50) * 2.0, s * 0.50 + (vy - s * 0.50) * 2.0), fill=outline, width=ow // 2)

    elif kind == 'car':
        draw.polygon([(s * 0.24, s * 0.48), (s * 0.36, s * 0.28), (s * 0.66, s * 0.28), (s * 0.78, s * 0.48)], fill=(56, 189, 248, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.12, s * 0.46, s * 0.88, s * 0.74), radius=12 * scale, fill=(239, 68, 68, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.20, s * 0.62, s * 0.38, s * 0.80), fill=(30, 41, 59, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.62, s * 0.62, s * 0.80, s * 0.80), fill=(30, 41, 59, 255), outline=outline, width=ow)

    elif kind == 'brain':
        for bx in (0.36, 0.64):
            draw.ellipse((s * (bx - 0.22), s * 0.18, s * (bx + 0.22), s * 0.82), fill=(244, 114, 182, 255), outline=outline, width=ow)
        draw.line((s * 0.50, s * 0.18, s * 0.50, s * 0.82), fill=outline, width=ow)

    elif kind == 'book':
        draw.rounded_rectangle((s * 0.18, s * 0.58, s * 0.84, s * 0.82), radius=8 * scale, fill=(59, 130, 246, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.16, s * 0.38, s * 0.82, s * 0.62), radius=8 * scale, fill=(16, 185, 129, 255), outline=outline, width=ow)
        draw.rounded_rectangle((s * 0.20, s * 0.18, s * 0.86, s * 0.42), radius=8 * scale, fill=(244, 63, 94, 255), outline=outline, width=ow)

    elif kind == 'sun':
        pts = _star_polygon(s * 0.50, s * 0.50, outer_r=s * 0.42, inner_r=s * 0.28)
        draw.polygon(pts, fill=(251, 146, 60, 255), outline=outline, width=ow)
        draw.ellipse((s * 0.24, s * 0.24, s * 0.76, s * 0.76), fill=(250, 204, 21, 255), outline=outline, width=ow)

    if gen_style:
        final_img = _style_generated_sticker(canvas, gen_style, size)
    else:
        final_img = canvas.resize((size, size), Image.Resampling.LANCZOS)
    if effects:
        final_img = _with_shadow(final_img)
    return np.array(final_img)


def _apply_clip_fade_in(clip, total_duration: float, fade_start: float = 0.0, fade_duration: float = 0.6):
    """Smoothly fade a clip's alpha mask in starting at ``fade_start`` over ``fade_duration`` seconds."""
    import numpy as np
    from moviepy import VideoClip

    if fade_start <= 0.0 and fade_duration <= 0.0:
        return clip.with_duration(total_duration)
    base_frame = clip.get_frame(0)
    if base_frame.ndim == 3 and base_frame.shape[2] == 4:
        base_mask = base_frame[:, :, 3].astype(np.float32) / 255.0
    elif clip.mask is not None:
        base_mask = clip.mask.get_frame(0).astype(np.float32)
    else:
        base_mask = np.ones((clip.h, clip.w), dtype=np.float32)

    def _mask_frame(t: float) -> np.ndarray:
        if t < fade_start:
            return np.zeros_like(base_mask)
        if fade_duration <= 0.0 or t >= fade_start + fade_duration:
            return base_mask
        alpha = float((t - fade_start) / fade_duration)
        return base_mask * max(0.0, min(1.0, alpha))

    mask_clip = VideoClip(_mask_frame, is_mask=True, duration=total_duration)
    return clip.with_duration(total_duration).with_mask(mask_clip)


@app.route('/api/video_title_preview', methods=['POST'])
def video_title_preview():
    from PIL import Image

    data = request.get_json(silent=True) or {}
    preview_kind = str(data.get('preview_kind') or 'title').strip()
    title = str(data.get('video_title') or '').strip()
    if preview_kind in ('title', 'watermark') and (not title or len(title) > 1000):
        return jsonify(error='Enter a title of up to 1000 characters.'), 400
    try:
        font_path, font_size = _title_settings(data)
        first_color, other_color = _title_colors(data)
        effects = data.get('visual_effects', True) is not False
        title_uppercase = data.get('title_uppercase', True) is not False
        if preview_kind == 'watermark':
            text, color = _watermark_settings(data)
            image = _title_image(text, 936, _watermark_font_path(data), 96,
                                 color, color, effects, max_height=110)
        elif preview_kind == 'subscribe':
            sub_text = str(data.get('subscribe_text') or 'Subscribe!').strip()[:40] or 'Subscribe!'
            image = _subscribe_badge_image(sub_text, font_path, effects)
        elif preview_kind == 'stars':
            _, color = _watermark_settings(data)
            image = _stars_row_image(color, effects)
        elif preview_kind == 'sticker':
            raw_sticker = str(data.get('sticker_kind') or 'santa').strip().lower()
            side = str(data.get('sticker_side') or 'left').strip().lower()
            gen_style, base_kind = _parse_sticker_spec(raw_sticker, title=title, side=side)
            sticker_kind = f'gen_{gen_style}:{base_kind}' if gen_style else base_kind
            image = _sticker_image(
                sticker_kind,
                size=140,
                effects=effects,
                allow_remote=bool(data.get('allow_remote_stickers', True)),
            )
            if image is None:
                return jsonify(error='Unknown sticker kind.'), 400
        else:
            image = _title_image(title, 1040, font_path, font_size, first_color,
                                 other_color, effects, uppercase=title_uppercase)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    output = io.BytesIO()
    Image.fromarray(image).save(output, format='PNG')
    output.seek(0)
    return send_file(output, mimetype='image/png')


@app.route('/api/render_video', methods=['POST'])
@app.route('/api/render_video/', methods=['POST'])
def make_video() -> tuple[object, int]:
    data: dict[str, object] = request.get_json(silent=True) or {}

    map_b64 = str(data.get('map_file') or '')
    background_b64 = str(data.get('background_video') or '')
    title = str(data.get('video_title') or data.get('title') or '').strip()
    codec = str(data.get('codec') or 'libx264').strip()
    raw_duration = data.get('duration')

    # --- Validation -----------------------------------------------------------
    if not map_b64:
        return jsonify({"status": "error", "error": "Missing 'map_file'."}), 400
    if not background_b64:
        return jsonify({"status": "error", "error": "Missing 'background_video'."}), 400
    if not title:
        return jsonify({"status": "error", "error": "Missing 'video_title'."}), 400
    if len(title) > 1000:
        return jsonify(status='error', error='Title must be at most 1000 characters.'), 400
    try:
        font_path, font_size = _title_settings(data)
        first_color, other_color = _title_colors(data)
        watermark_text, watermark_color = _watermark_settings(data)
        watermark_font_path = _watermark_font_path(data)
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400
    if codec not in ALLOWED_CODECS:
        return jsonify({"status": "error",
                        "error": f"Unsupported codec '{codec}'. "
                                 f"Allowed: {', '.join(ALLOWED_CODECS)}."}), 400
    if codec != 'libx264' and codec not in _available_codecs():
        return jsonify(status='error', error=f"Codec '{codec}' is unavailable on the render worker. Select libx264 (CPU)."), 400

    def _decode_material(value: str, label: str) -> bytes:
        """Strip an optional `data:...;base64,` prefix and decode to bytes."""
        if value.startswith("data:"):
            # e.g. "data:image/png;base64,AAAA..."
            _, _, b64_part = value.partition(",")
        else:
            b64_part = value
        try:
            raw = base64.b64decode(b64_part, validate=False)
        except Exception:
            raise ValueError(f"{label} is not valid base64.")
        if not raw:
            raise ValueError(f"{label} is empty after decoding.")
        # Apply the upload limit to decoded bytes, excluding base64 overhead.
        if len(raw) > MAX_MATERIAL_BYTES:
            raise ValueError(f"{label} exceeds the 10 MB limit ({len(raw) // (1024 * 1024)} MB).")
        return raw

    try:
        map_bytes = _decode_material(map_b64, "map_file")
        bg_bytes = _decode_material(background_b64, "background_video")
        from PIL import Image, ImageOps, UnidentifiedImageError

        def _decode_optional_image(field: str, label: str):
            raw_val = data.get(field)
            if not raw_val:
                return None
            raw_bytes = _decode_material(str(raw_val), field)
            try:
                with Image.open(io.BytesIO(raw_bytes)) as uploaded:
                    if uploaded.width * uploaded.height > 20_000_000:
                        raise ValueError(f'{label} image must be at most 20 megapixels.')
                    return uploaded.convert('RGBA')
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValueError(f'{label} must be a valid image.')

        watermark_image = _decode_optional_image('watermark_image', 'Watermark')
        subscribe_custom_img = _decode_optional_image('subscribe_image', 'Subscribe')
        watermark_stars_custom_img = _decode_optional_image('watermark_stars_image', 'Watermark stars')
        top_left_custom_img = _decode_optional_image('top_left_sticker_image', 'Top-left sticker')
        top_right_custom_img = _decode_optional_image('top_right_sticker_image', 'Top-right sticker')
    except ValueError as ve:
        return jsonify({"status": "error", "error": str(ve)}), 400

    safe_title = _SAFE_TITLE_RE.sub('_', title)
    # The video headline and the selected song are independent identifiers.
    audio_title = data.get('audio_title', '')
    if not isinstance(audio_title, str) or (audio_title and not _AUDIO_TITLE_RE.fullmatch(audio_title)):
        return jsonify(status='error', error='Invalid audio track title.'), 400
    try:
        audio_bytes = _decode_material(str(data['audio_file']), 'audio_file') if data.get('audio_file') else None
    except ValueError as error:
        return jsonify(status='error', error=str(error)), 400
    cut_audio_path = os.path.join(CUT_DIR, f"{audio_title}_cut.mp3")
    # Prefer an edited track; fall back to the full download if no cut exists.
    audio_path = (
        cut_audio_path
        if os.path.exists(cut_audio_path)
        else os.path.join(DOWNLOADS, f"{audio_title}.mp3")
    )
    if 'audio_title' in data and audio_title and not audio_bytes and not os.path.isfile(audio_path):
        return jsonify(status='error', error='Selected audio is no longer available. Download and cut the track again.'), 400
    if data.get('include_audio') is True and not audio_bytes and not (audio_title and os.path.isfile(audio_path)):
        return jsonify(status='error', error='Selected audio is missing. Reload Video Studio and download the track again.'), 400
    # A fresh URL prevents the player from reusing an older, silent render.
    output_path = os.path.join(RENDER_DIR, f"{safe_title}_{uuid.uuid4().hex[:12]}_final.mp4")

    map_path = bg_path = audio_temp_path = None
    media_resources = []
    try:
        from moviepy import (VideoFileClip, CompositeVideoClip,
                             ImageClip, AudioFileClip)

        os.makedirs(RENDER_DIR, exist_ok=True)

        # Write the decoded materials to temp files on disk.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as map_file_temp:
            map_file_temp.write(map_bytes)
            map_path = map_file_temp.name

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as bg_video_temp:
            bg_video_temp.write(bg_bytes)
            bg_path = bg_video_temp.name

        if audio_bytes:
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as audio_temp:
                audio_temp.write(audio_bytes)
                audio_temp_path = audio_temp.name
                audio_path = audio_temp_path

        # Target clip length: 1..30 seconds (default 8.0), never longer than the source clips.
        try:
            TARGET = float(raw_duration) if raw_duration is not None else 8.0
        except (TypeError, ValueError):
            TARGET = 8.0
        TARGET = max(1.0, min(30.0, TARGET))

        background_video = VideoFileClip(bg_path)
        media_resources.append(background_video)
        background_video = background_video.without_audio()
        bg_duration = min(TARGET, background_video.duration or TARGET)
        background_video = background_video.subclipped(0, bg_duration)

        # Fill the portrait frame without stretching the source footage.
        canvas_w, canvas_h = 1080, 1920
        bg_scale = max(canvas_w / background_video.w, canvas_h / background_video.h)
        background_video = background_video.resized(bg_scale).cropped(
            x_center=background_video.w * bg_scale / 2,
            y_center=background_video.h * bg_scale / 2,
            width=canvas_w, height=canvas_h,
        )

        effects = data.get('visual_effects', True) is not False
        if effects:
            from PIL import ImageFilter
            # Blur a smaller frame for a soft background without blurring the overlays.
            def soften(frame):
                small = Image.fromarray(frame).resize((270, 480))
                return np.array(small.filter(ImageFilter.GaussianBlur(2)).resize((1080, 1920)))
            import numpy as np
            background_video = background_video.image_transform(soften)

        # Darken the edges and the area behind the title for readable white text.
        import numpy as np
        shade = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        y = np.linspace(-1, 1, canvas_h)
        shade[:, :, 3] = (25 + 65 * np.abs(y) ** 1.5).astype(np.uint8)[:, None]
        scrim = ImageClip(shade).with_duration(bg_duration)

        # Anchor the headline near the top and let the map fill the space below.
        title_uppercase = data.get('title_uppercase', True) is not False
        allow_remote_stickers = data.get('allow_remote_stickers', True) is not False
        left_gen, left_base = _parse_sticker_spec(str(data.get('top_left_sticker') or 'none'), title=title, side='left')
        right_gen, right_base = _parse_sticker_spec(str(data.get('top_right_sticker') or 'none'), title=title, side='right')
        top_left_sticker = f'gen_{left_gen}:{left_base}' if left_gen else left_base
        top_right_sticker = f'gen_{right_gen}:{right_base}' if right_gen else right_base

        if top_left_custom_img is not None:
            left_pil = ImageOps.contain(top_left_custom_img, (140, 140), method=Image.Resampling.LANCZOS)
            if effects:
                left_pil = _with_shadow(left_pil)
            left_sticker_arr = np.array(left_pil)
        else:
            left_sticker_arr = _sticker_image(top_left_sticker, size=140, effects=effects, allow_remote=allow_remote_stickers)

        if top_right_custom_img is not None:
            right_pil = ImageOps.contain(top_right_custom_img, (140, 140), method=Image.Resampling.LANCZOS)
            if effects:
                right_pil = _with_shadow(right_pil)
            right_sticker_arr = np.array(right_pil)
        else:
            right_sticker_arr = _sticker_image(top_right_sticker, size=140, effects=effects, allow_remote=allow_remote_stickers)

        has_top_stickers = left_sticker_arr is not None or right_sticker_arr is not None

        subscribe_enabled = bool(data.get('subscribe_badge', False))
        subscribe_text = str(data.get('subscribe_text') or 'Subscribe!').strip()[:40] or 'Subscribe!'
        try:
            subscribe_delay = float(data.get('subscribe_delay', 2.5))
        except (TypeError, ValueError):
            subscribe_delay = 2.5
        subscribe_delay = max(0.0, min(25.0, subscribe_delay))

        watermark_stars = bool(data.get('watermark_stars', False))
        has_stars = bool(watermark_stars or watermark_stars_custom_img is not None)
        intro_fade = bool(data.get('intro_fade', False))

        title_clip = _build_title_clip(
            title,
            bg_duration,
            1040,
            font_path,
            font_size,
            first_color,
            other_color,
            effects,
            uppercase=title_uppercase,
        )
        map_clip = ImageClip(map_path).with_duration(bg_duration)
        title_top = 175 if has_top_stickers else 70
        gap = 16

        subscribe_clip = None
        subscribe_top = title_top + title_clip.h + 8
        if subscribe_custom_img is not None or subscribe_enabled:
            if subscribe_custom_img is not None:
                sub_pil = ImageOps.contain(subscribe_custom_img, (680, 115), method=Image.Resampling.LANCZOS)
                if effects:
                    sub_pil = _with_shadow(sub_pil)
                sub_arr = np.array(sub_pil)
            else:
                sub_arr = _subscribe_badge_image(subscribe_text, font_path, effects)
            subscribe_clip = ImageClip(sub_arr).with_duration(bg_duration)
            fade_start = min(subscribe_delay, max(0.0, bg_duration - 0.5)) if bg_duration > 1.0 else 0.0
            fade_dur = min(0.6, max(0.15, bg_duration - fade_start)) if bg_duration > 0.4 else 0.0
            subscribe_clip = _apply_clip_fade_in(subscribe_clip, bg_duration, fade_start=fade_start, fade_duration=fade_dur)
            subscribe_clip = subscribe_clip.with_position(('center', subscribe_top))
            map_top = subscribe_top + subscribe_clip.h + gap
        else:
            map_top = title_top + title_clip.h + gap

        branded = bool(watermark_image is not None or watermark_text or has_stars)
        map_bottom = 1450 if branded else 1760
        map_area_height = max(240, map_bottom - map_top)
        # Fit both dimensions so the whole map stays inside the space below the title.
        scale = min(1040 / map_clip.w, map_area_height / map_clip.h)
        map_clip = map_clip.resized(scale)
        if effects:
            map_image = Image.open(map_path).convert('RGBA').resize((map_clip.w, map_clip.h))
            map_clip = ImageClip(np.array(_with_shadow(map_image))).with_duration(bg_duration)
        title_clip = title_clip.with_position(("center", title_top))
        map_clip = map_clip.with_position(("center", map_top - (24 if effects else 0)))
        clips = [background_video, scrim, map_clip, title_clip]

        if left_sticker_arr is not None:
            left_clip = ImageClip(left_sticker_arr).with_duration(bg_duration)
            clips.append(left_clip.with_position((145, 16)))
        if right_sticker_arr is not None:
            right_clip = ImageClip(right_sticker_arr).with_duration(bg_duration)
            clips.append(right_clip.with_position((canvas_w - 145 - right_clip.w, 16)))
        if subscribe_clip is not None:
            clips.append(subscribe_clip)

        if watermark_image is not None:
            watermark_image = _round_watermark(watermark_image)
            logo_top = 1470
            if effects:
                watermark_image = _with_shadow(watermark_image)
                logo_top -= 24
            logo = ImageClip(np.array(watermark_image)).with_duration(bg_duration)
            clips.append(logo.with_position(('center', logo_top)))
        if watermark_text:
            lettering = _title_image(watermark_text, 936, watermark_font_path, 96,
                                    watermark_color, watermark_color, effects, max_height=110)
            branding = ImageClip(lettering).with_duration(bg_duration)
            watermark_y = 1695 if has_stars else 1740
            clips.append(branding.with_position(('center', watermark_y)))
        if has_stars:
            if watermark_stars_custom_img is not None:
                stars_pil = ImageOps.contain(watermark_stars_custom_img, (480, 95), method=Image.Resampling.LANCZOS)
                if effects:
                    stars_pil = _with_shadow(stars_pil)
                stars_arr = np.array(stars_pil)
            else:
                stars_arr = _stars_row_image(watermark_color or '#ffffff', effects)
            stars_clip = ImageClip(stars_arr).with_duration(bg_duration)
            if bg_duration > 1.8:
                stars_start = min(1.8, bg_duration * 0.25)
                stars_clip = _apply_clip_fade_in(stars_clip, bg_duration, fade_start=stars_start, fade_duration=0.5)
            stars_y = 1805 if watermark_text else 1785
            clips.append(stars_clip.with_position(('center', stars_y)))
        if intro_fade:
            from moviepy import VideoClip
            fade_in_dur = min(0.75, max(0.2, bg_duration * 0.3))
            black_frame = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
            intro_veil = ImageClip(black_frame).with_duration(fade_in_dur)
            base_ones = np.ones((canvas_h, canvas_w), dtype=np.float32)

            def _veil_mask(t: float) -> np.ndarray:
                if t >= fade_in_dur or fade_in_dur <= 0:
                    return np.zeros_like(base_ones)
                alpha = 0.88 * (1.0 - float(t / fade_in_dur))
                return base_ones * max(0.0, min(1.0, alpha))

            veil_mask = VideoClip(_veil_mask, is_mask=True, duration=fade_in_dur)
            clips.append(intro_veil.with_mask(veil_mask))

        final_clip = CompositeVideoClip(clips, size=(canvas_w, canvas_h))
        media_resources.append(final_clip)

        # Attach the selected cut or full track when available; otherwise render silently.
        if audio_bytes or (audio_title and os.path.isfile(audio_path)):
            audio_clip = AudioFileClip(audio_path)
            media_resources.append(audio_clip)
            audio_duration = min(bg_duration, audio_clip.duration or bg_duration)
            final_clip = final_clip.with_audio(audio_clip.subclipped(0, audio_duration))
            final_clip = final_clip.with_duration(audio_duration)
        else:
            final_clip = final_clip.with_duration(bg_duration)

        final_clip.write_videofile(
            output_path,
            codec=codec,
            audio_codec="aac",
            fps=30,
            logger=None,
        )

        return jsonify({
            "status": "success",
            "file_path": output_path,
            "download_url": f"/api/download_video?path={output_path}",
            "has_audio": final_clip.audio is not None,
        }), 200
    
    except Exception as e:
        return jsonify({"status": "error", "error": f"Error processing video: {str(e)}"}), 500

    finally:
        # Release FFmpeg readers before removing their input files.
        for resource in reversed(media_resources):
            try:
                resource.close()
            except Exception:
                pass
        # Always clean up the temp input files.
        for p in (map_path, bg_path, audio_temp_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


@app.route('/api/download_video/', methods=['GET'])
@app.route('/api/download_video', methods=['GET'])
def download_video():
    """Stream a rendered video file by its server path.

    Query params:
        path - absolute (or storage-relative) path to the rendered .mp4.

    Only files inside RENDER_DIR are served (path traversal is rejected).
    """
    raw_path = (request.args.get('path') or '').strip()
    if not raw_path:
        return jsonify({"status": "error", "error": "Missing 'path' query param."}), 400

    # Normalise and enforce that the file lives under RENDER_DIR.
    # Resolve symlinks before checking containment in the render directory.
    candidate = os.path.realpath(raw_path)
    render_root = os.path.realpath(RENDER_DIR)
    if os.path.commonpath([candidate, render_root]) != render_root:
        return jsonify({"status": "error", "error": "Invalid path."}), 400
    if not os.path.isfile(candidate):
        return jsonify({"status": "error", "error": "File not found."}), 404

    return send_file(
        candidate,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=os.path.basename(candidate),
    )


if __name__ == "__main__":

    port = int(os.environ.get("PORT", "8080"))
    app.run(host='0.0.0.0', port=port, debug=True)
