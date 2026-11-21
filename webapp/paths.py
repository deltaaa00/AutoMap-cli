"""Anchor web assets and configuration to the repository, not the caller's cwd.

BASE_DIR is the project root; FONTS_DIR contains bundled selectable fonts.
Use these paths for web resources instead of relative strings or changing the
process working directory. Worker font paths are defined separately in media.text.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FONTS_DIR = BASE_DIR / 'resources' / 'fonts'
