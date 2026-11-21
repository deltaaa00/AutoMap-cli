"""SQLite persistence shared by the dashboard and the standalone runner.

AgentDB stores configuration, credentials, experiments, competitor statistics,
and queued commands in AGENT_DB_PATH or the agent instance directory. Creating
an AgentDB initializes/migrates its schema. Each operation opens a connection
with WAL and foreign keys, commits on success, and rolls back on exceptions.
Experiment-action claims are conditional database updates before external
uploads; command consumers and publish workflows are in ``runner.py``.
Keep serialized field decoding here so callers receive ordinary Python values."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_CONFIG: dict[str, Any] = {
    "posts_per_day": 2,
    "timezone": "Europe/Kyiv",
    "default_privacy": "private",
    "autopilot": False,
    "competitor_channels": [],
    "ai_provider": "gemini",
    "ai_model": "gemini-3.8-flash",
    "video_duration": 11,
    "watermark_text": "TERRAMAPPER",
    "watermark_stars": True,
    "series_size": 4,
    "default_hours": [14, 20],
    "last_sync_at": None,
    "last_heartbeat_at": None,
    "own_top_topics": [],
    "description_template": "{topic}\n\n{hashtags}",
    "automap_url": "",
    "web_search_enabled": True,
    "max_search_requests": 5,
}


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path() -> Path:
    """Resolve the shared SQLite path inside /app/instance or local instance/."""
    env_path = os.environ.get("AGENT_DB_PATH", "").strip()
    if env_path:
        return Path(env_path)
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "instance" / "agent.db"


class AgentDB:
    """Lightweight SQLite wrapper for experiments, competitor intelligence, and queue jobs."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open one transactional connection and always release it.

        Successful callers commit their writes; exceptions roll back before propagating.
        WAL permits the dashboard and runner to share a database across processes."""
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Create tables if they do not exist."""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_config (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS competitor_videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    channel_title TEXT NOT NULL DEFAULT '',
                    video_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    views INTEGER NOT NULL DEFAULT 0,
                    likes INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    channel_median REAL NOT NULL DEFAULT 1.0,
                    outlier_ratio REAL NOT NULL DEFAULT 1.0,
                    published_at TEXT NOT NULL DEFAULT '',
                    music_hint TEXT NOT NULL DEFAULT '',
                    music_imported INTEGER NOT NULL DEFAULT 0,
                    music_notified INTEGER NOT NULL DEFAULT 0,
                    used INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    arm TEXT NOT NULL,
                    rationale TEXT NOT NULL DEFAULT '',
                    source_competitor_video_id TEXT NOT NULL DEFAULT '',
                    params_json TEXT NOT NULL,
                    mp4_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending_approval',
                    requested_action TEXT NOT NULL DEFAULT '',
                    requested_privacy TEXT NOT NULL DEFAULT 'private',
                    scheduled_for TEXT NOT NULL DEFAULT '',
                    posting_hour INTEGER NOT NULL DEFAULT 14,
                    youtube_video_id TEXT NOT NULL DEFAULT '',
                    youtube_url TEXT NOT NULL DEFAULT '',
                    telegram_message_id INTEGER,
                    error_message TEXT NOT NULL DEFAULT '',
                    views_24h INTEGER,
                    views_72h INTEGER,
                    avg_view_pct REAL,
                    avg_view_duration_sec REAL,
                    duration_sec INTEGER,
                    likes_72h INTEGER,
                    dislikes_72h INTEGER,
                    comments_72h INTEGER,
                    shares_72h INTEGER,
                    subscribers_gained INTEGER,
                    subscribers_lost INTEGER,
                    estimated_minutes_watched REAL,
                    hook_retention_pct REAL,
                    end_retention_pct REAL,
                    relative_retention REAL,
                    retention_curve_json TEXT NOT NULL DEFAULT '[]',
                    analytics_synced_at TEXT NOT NULL DEFAULT '',
                    score REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS agent_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    processed_at TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._ensure_column(conn, "competitor_videos", "likes", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "competitor_videos", "comments", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "experiments", "avg_view_duration_sec", "REAL")
            self._ensure_column(conn, "experiments", "duration_sec", "INTEGER")
            self._ensure_column(conn, "experiments", "dislikes_72h", "INTEGER")
            self._ensure_column(conn, "experiments", "comments_72h", "INTEGER")
            self._ensure_column(conn, "experiments", "shares_72h", "INTEGER")
            self._ensure_column(conn, "experiments", "subscribers_lost", "INTEGER")
            self._ensure_column(conn, "experiments", "estimated_minutes_watched", "REAL")
            self._ensure_column(conn, "experiments", "hook_retention_pct", "REAL")
            self._ensure_column(conn, "experiments", "end_retention_pct", "REAL")
            self._ensure_column(conn, "experiments", "relative_retention", "REAL")
            self._ensure_column(conn, "experiments", "retention_curve_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "experiments", "analytics_synced_at", "TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
        """Safely add a column to an existing SQLite table if it does not exist yet."""
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")

    # --- Configuration ---

    def get_config(self) -> dict[str, Any]:
        """Return merged configuration with defaults and env overrides for empty keys."""
        cfg = dict(DEFAULT_CONFIG)
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value_json FROM agent_config").fetchall()
        for row in rows:
            if row["key"] == "__secrets__":
                continue
            try:
                cfg[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                continue

        if not cfg.get("competitor_channels"):
            env_channels = os.environ.get("AGENT_COMPETITOR_CHANNELS", "").strip()
            if env_channels:
                cfg["competitor_channels"] = [
                    ch.strip() for ch in env_channels.split(",") if ch.strip()
                ]
        if os.environ.get("AGENT_TZ") and "timezone" not in {r["key"] for r in rows}:
            cfg["timezone"] = os.environ["AGENT_TZ"].strip()
        if os.environ.get("AGENT_POSTS_PER_DAY") and "posts_per_day" not in {r["key"] for r in rows}:
            try:
                cfg["posts_per_day"] = max(1, min(6, int(os.environ["AGENT_POSTS_PER_DAY"])))
            except ValueError:
                pass
        return cfg

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Persist configuration keys and return the full updated config."""
        now = utc_now_iso()
        with self._connect() as conn:
            for key, value in updates.items():
                conn.execute(
                    """
                    INSERT INTO agent_config (key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                    """,
                    (key, json.dumps(value), now),
                )
        return self.get_config()

    def touch_heartbeat(self) -> None:
        """Record that the background agent runner is alive."""
        self.update_config({"last_heartbeat_at": utc_now_iso()})

    # --- Secrets (Telegram / YouTube API Key saved from Web UI) ---

    def get_secrets(self) -> dict[str, str]:
        """Return agent integration secrets from DB with environment fallbacks."""
        stored: dict[str, Any] = {}
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM agent_config WHERE key = '__secrets__'"
            ).fetchone()
        if row:
            try:
                parsed = json.loads(row["value_json"])
                if isinstance(parsed, dict):
                    stored = parsed
            except json.JSONDecodeError:
                pass
        return {
            "telegram_bot_token": str(
                stored.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN") or ""
            ).strip(),
            "telegram_chat_id": str(
                stored.get("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID") or ""
            ).strip(),
            "youtube_api_key": str(
                stored.get("youtube_api_key") or os.environ.get("YOUTUBE_API_KEY") or ""
            ).strip(),
            "gemini_api_key": str(
                stored.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or ""
            ).strip(),
            "deepseek_api_key": str(
                stored.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY") or ""
            ).strip(),
            "tavily_api_key": str(
                stored.get("tavily_api_key") or os.environ.get("TAVILY_API_KEY") or ""
            ).strip(),
            "automap_login": str(
                stored.get("automap_login") or os.environ.get("WEB_LOGIN") or ""
            ).strip(),
            "automap_password": str(
                stored.get("automap_password") or os.environ.get("WEB_PASSWORD") or ""
            ).strip(),
        }

    def update_secrets(self, updates: dict[str, str]) -> dict[str, bool]:
        """Update non-empty integration secrets (or clear when explicitly set to empty string)."""
        current = self.get_secrets()
        for key in (
            "telegram_bot_token",
            "telegram_chat_id",
            "youtube_api_key",
            "gemini_api_key",
            "deepseek_api_key",
            "tavily_api_key",
            "automap_login",
            "automap_password",
        ):
            if key in updates and updates[key] is not None:
                current[key] = str(updates[key]).strip()
        self.update_config({"__secrets__": current})
        return {
            "telegram_configured": bool(current["telegram_bot_token"] and current["telegram_chat_id"]),
            "youtube_api_configured": bool(current["youtube_api_key"]),
            "gemini_api_configured": bool(current["gemini_api_key"]),
            "deepseek_api_configured": bool(current["deepseek_api_key"]),
            "tavily_api_configured": bool(current["tavily_api_key"]),
            "automap_auth_configured": bool(current["automap_login"] and current["automap_password"]),
        }

    # --- Competitor Videos ---

    def upsert_competitor_video(self, item: dict[str, Any]) -> int:
        """Insert or update a competitor video record and return its row id."""
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO competitor_videos (
                    channel_id, channel_title, video_id, title, description,
                    views, likes, comments, channel_median, outlier_ratio, published_at, music_hint, discovered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    channel_title = excluded.channel_title,
                    title = excluded.title,
                    description = excluded.description,
                    views = excluded.views,
                    likes = excluded.likes,
                    comments = excluded.comments,
                    channel_median = excluded.channel_median,
                    outlier_ratio = excluded.outlier_ratio,
                    music_hint = CASE
                        WHEN excluded.music_hint != '' THEN excluded.music_hint
                        ELSE competitor_videos.music_hint
                    END
                """,
                (
                    str(item.get("channel_id") or ""),
                    str(item.get("channel_title") or ""),
                    str(item["video_id"]),
                    str(item.get("title") or ""),
                    str(item.get("description") or "")[:2000],
                    int(item.get("views") or 0),
                    int(item.get("likes") or 0),
                    int(item.get("comments") or 0),
                    float(item.get("channel_median") or 1.0),
                    float(item.get("outlier_ratio") or 1.0),
                    str(item.get("published_at") or ""),
                    str(item.get("music_hint") or ""),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM competitor_videos WHERE video_id = ?",
                (str(item["video_id"]),),
            ).fetchone()
            return int(row["id"])

    def list_competitor_outliers(
        self,
        *,
        limit: int = 25,
        unused_only: bool = False,
        min_ratio: float = 1.0,
        max_age_days: int | None = 7,
    ) -> list[dict[str, Any]]:
        """Return competitor videos ordered by outlier_ratio DESC."""
        clauses = ["outlier_ratio >= ?"]
        params: list[Any] = [float(min_ratio)]
        if unused_only:
            clauses.append("used = 0")
        if max_age_days is not None and max_age_days > 0:
            cutoff = (
                (datetime.now(timezone.utc) - timedelta(days=int(max_age_days)))
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            clauses.append("published_at >= ?")
            params.append(cutoff)
        where_sql = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM competitor_videos
                WHERE {where_sql}
                ORDER BY outlier_ratio DESC, views DESC
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_competitor_video(self, comp_id: int) -> dict[str, Any] | None:
        """Fetch a single competitor video by row id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM competitor_videos WHERE id = ?",
                (int(comp_id),),
            ).fetchone()
        return dict(row) if row else None

    def mark_competitor_used(self, video_id: str) -> None:
        """Mark a competitor video as already adapted into a topic."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE competitor_videos SET used = 1 WHERE video_id = ?",
                (str(video_id),),
            )

    def mark_competitor_music_imported(self, comp_id: int) -> None:
        """Mark a competitor video's music track as imported into agent_media/music/."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE competitor_videos SET music_imported = 1, music_notified = 1 WHERE id = ?",
                (int(comp_id),),
            )

    def mark_competitor_music_notified(self, comp_id: int) -> None:
        """Mark a competitor music suggestion as already sent to Telegram."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE competitor_videos SET music_notified = 1 WHERE id = ?",
                (int(comp_id),),
            )

    def list_unnotified_music_suggestions(
        self,
        min_ratio: float = 2.0,
        limit: int = 3,
        max_age_days: int | None = 7,
    ) -> list[dict[str, Any]]:
        """Return viral competitor videos whose music has not yet been suggested in Telegram."""
        clauses = ["music_notified = 0", "music_imported = 0", "outlier_ratio >= ?"]
        params: list[Any] = [float(min_ratio)]
        if max_age_days is not None and max_age_days > 0:
            cutoff = (
                (datetime.now(timezone.utc) - timedelta(days=int(max_age_days)))
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            clauses.append("published_at >= ?")
            params.append(cutoff)
        where_sql = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM competitor_videos
                WHERE {where_sql}
                ORDER BY outlier_ratio DESC, views DESC
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Experiments ---

    def _decode_experiment(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        try:
            item["params"] = json.loads(item.get("params_json") or "{}")
        except json.JSONDecodeError:
            item["params"] = {}
        try:
            raw_curve = json.loads(item.get("retention_curve_json") or "[]")
            item["retention_curve"] = raw_curve if isinstance(raw_curve, list) else []
        except json.JSONDecodeError:
            item["retention_curve"] = []
        return item

    def create_experiment(
        self,
        *,
        topic: str,
        arm: str,
        params: dict[str, Any],
        rationale: str = "",
        source_competitor_video_id: str = "",
        mp4_path: str = "",
        posting_hour: int = 14,
        scheduled_for: str = "",
        status: str = "pending_approval",
    ) -> dict[str, Any]:
        """Insert a new experiment row and return the decoded record."""
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO experiments (
                    topic, arm, rationale, source_competitor_video_id,
                    params_json, mp4_path, status, scheduled_for, posting_hour,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic,
                    arm,
                    rationale,
                    source_competitor_video_id,
                    json.dumps(params),
                    mp4_path,
                    status,
                    scheduled_for,
                    int(posting_hour),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM experiments WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        decoded = self._decode_experiment(row)
        assert decoded is not None
        return decoded

    def get_experiment(self, exp_id: int) -> dict[str, Any] | None:
        """Fetch an experiment by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE id = ?",
                (int(exp_id),),
            ).fetchone()
        return self._decode_experiment(row)

    def list_experiments(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List experiments newest first, optionally filtered by status."""
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM experiments WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM experiments ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [item for row in rows if (item := self._decode_experiment(row)) is not None]

    def count_created_experiments(self) -> int:
        """Count retained candidates to determine the current media series."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM experiments
                WHERE status NOT IN ('rejected', 'rendering')
                  AND NOT (status = 'processing' AND requested_action = 'regenerate')
                  AND NOT (status = 'failed' AND mp4_path = '')
                """
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def list_recent_topics(self, limit: int = 50) -> list[str]:
        """Return recently generated topics to prevent duplicates."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT topic FROM experiments ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [str(r["topic"]) for r in rows if str(r["topic"]).strip()]

    def update_experiment(self, exp_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update allowed experiment columns."""
        allowed = {
            "status",
            "requested_action",
            "requested_privacy",
            "scheduled_for",
            "mp4_path",
            "youtube_video_id",
            "youtube_url",
            "telegram_message_id",
            "error_message",
            "views_24h",
            "views_72h",
            "avg_view_pct",
            "avg_view_duration_sec",
            "duration_sec",
            "likes_72h",
            "dislikes_72h",
            "comments_72h",
            "shares_72h",
            "subscribers_gained",
            "subscribers_lost",
            "estimated_minutes_watched",
            "hook_retention_pct",
            "end_retention_pct",
            "relative_retention",
            "retention_curve_json",
            "analytics_synced_at",
            "score",
            "published_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_experiment(exp_id)
        updates["updated_at"] = utc_now_iso()
        cols = ", ".join(f"{k} = ?" for k in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE experiments SET {cols} WHERE id = ?",
                (*updates.values(), int(exp_id)),
            )
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (int(exp_id),)).fetchone()
        return self._decode_experiment(row)

    def claim_experiment_action(self, exp_id: int, action: str) -> bool:
        """Atomically reserve a pending experiment before an upload or other action.

        Return False if another worker already claimed it or a video is already linked.
        Callers must stop on False so a repeated approval cannot publish twice."""
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE experiments
                SET status = 'processing', requested_action = ?, updated_at = ?
                WHERE id = ? AND status = 'pending_approval' AND COALESCE(youtube_video_id, '') = ''
                """,
                (action, utc_now_iso(), exp_id),
            )
        return result.rowcount == 1

    def count_experiments_for_date(self, date_prefix: str) -> int:
        """Count non-rejected experiments created on a YYYY-MM-DD UTC prefix."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM experiments
                WHERE status NOT IN ('rejected', 'failed')
                  AND (created_at LIKE ? OR scheduled_for LIKE ?)
                """,
                (f"{date_prefix}%", f"{date_prefix}%"),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def get_arm_leaderboard(self) -> list[dict[str, Any]]:
        """Aggregate experiment performance by A/B effect arm."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    arm,
                    COUNT(*) AS total_runs,
                    SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END) AS measured_runs,
                    AVG(COALESCE(views_72h, views_24h)) AS avg_views,
                    AVG(avg_view_pct) AS avg_retention,
                    AVG(avg_view_duration_sec) AS avg_watch_sec,
                    AVG(hook_retention_pct) AS avg_hook_retention,
                    AVG(likes_72h) AS avg_likes,
                    AVG(comments_72h) AS avg_comments,
                    AVG(shares_72h) AS avg_shares,
                    AVG(subscribers_gained) AS avg_subs_gained,
                    AVG(score) AS avg_score
                FROM experiments
                WHERE status IN ('published', 'scheduled', 'approved', 'pending_approval')
                GROUP BY arm
                ORDER BY avg_score DESC NULLS LAST, total_runs DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_hour_performance(self) -> list[dict[str, Any]]:
        """Aggregate measured experiment performance by posting hour."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    posting_hour,
                    COUNT(*) AS runs,
                    AVG(score) AS avg_score,
                    AVG(COALESCE(views_72h, views_24h)) AS avg_views
                FROM experiments
                WHERE score IS NOT NULL
                GROUP BY posting_hour
                ORDER BY avg_score DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Commands (Web UI -> Runner) ---

    def enqueue_command(self, command: str, payload: dict[str, Any] | None = None) -> int:
        """Enqueue an asynchronous action from the Web UI for the background runner."""
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO agent_commands (command, payload_json, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (command, json.dumps(payload or {}), now),
            )
            return int(cur.lastrowid)

    def list_pending_commands(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return pending web UI commands oldest-first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_commands WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload_json") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            result.append(item)
        return result

    def complete_command(self, cmd_id: int, *, status: str = "done", message: str = "") -> None:
        """Mark a queued command as done or failed."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_commands
                SET status = ?, result_message = ?, processed_at = ?
                WHERE id = ?
                """,
                (status, message, utc_now_iso(), int(cmd_id)),
            )
