"""Resolve database and media paths for dashboard route handlers.

These helpers require a Flask application/request context. Explicit app config
wins over environment settings; otherwise paths use the agent instance/media
directories. This allows tests and deployments to isolate persistent state
without route-specific globals. get_db constructs AgentDB and may initialize
its schema; get_media_pool constructs a pool without uploading or downloading.
The runner configures its own dependencies outside Flask."""

import os
from pathlib import Path

from flask import current_app

from .db import AgentDB
from .media_pool import MediaPool

BASE_DIR = Path(__file__).resolve().parent.parent


def get_db() -> AgentDB:
    db_path = current_app.config.get("AGENT_DB_PATH") or os.environ.get("AGENT_DB_PATH")
    if not db_path:
        db_path = Path(current_app.instance_path) / "agent.db"
    return AgentDB(db_path)


def get_media_pool(series_size: int = 4) -> MediaPool:
    media_dir = current_app.config.get("AGENT_MEDIA_DIR") or os.environ.get("AGENT_MEDIA_DIR")
    if not media_dir:
        media_dir = BASE_DIR / "agent_media"
    return MediaPool(media_dir, series_size=series_size)
