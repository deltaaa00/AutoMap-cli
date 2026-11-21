"""YouTube competitor intelligence, Analytics metrics sync, and video upload helper."""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .private_files import write_private_text

from .db import AgentDB, utc_now_iso
from .optimizer import compute_video_score

log = logging.getLogger("automap-agent")

YOUTUBE_DATA_API_BASE = "https://www.googleapis.com/youtube/v3"
AGENT_YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)

_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_iso8601_duration(raw: str) -> int | None:
    """Parse an ISO-8601 YouTube duration string (e.g. 'PT12S', 'PT1M5S') into seconds."""
    match = _DURATION_RE.match(str(raw or "").strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None

_MUSIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|\n)\s*(?:music|song|track|audio|sound)\s*[:\-–]\s*(.{3,80})", re.IGNORECASE),
    re.compile(r"Provided to YouTube by .*?\n([^\n]{3,80})", re.IGNORECASE),
    re.compile(r"♪\s*([^♪\n]{3,70})"),
)


def extract_music_hint(title: str, description: str) -> str:
    """Extract a human-readable music/track hint from a competitor video's metadata."""
    for pattern in _MUSIC_PATTERNS:
        match = pattern.search(description or "")
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1)).strip(" -|•")
            if candidate:
                return candidate[:90]
    if "shorts" in (title or "").lower() or "music" in (description or "").lower():
        return f"Audio from: {(title or '').strip()[:65]}"
    return f"Sound from Short: {(title or '').strip()[:65]}"


class YouTubeIntel:
    """Handles low-quota competitor scanning, own-channel analytics, and YouTube upload."""

    def __init__(
        self,
        db: AgentDB,
        *,
        api_key: str | None = None,
        token_path: str | Path | None = None,
        http: requests.Session | None = None,
    ) -> None:
        self.db = db
        self.api_key = (api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY", "")).strip()
        env_token = os.environ.get("AGENT_YOUTUBE_TOKEN_PATH", "").strip()
        default_tok = Path(__file__).resolve().parent.parent / "instance" / "agent_youtube_token.json"
        self.token_path = Path(token_path or env_token or default_tok)
        self.http = http or requests.Session()

    # --- Competitor Scanning (YouTube Data API v3, low quota) ---

    def resolve_channel_uploads_playlist(self, channel_ref: str) -> tuple[str, str, str] | None:
        """Resolve a channel ID or @handle to (channel_id, channel_title, uploads_playlist_id)."""
        if not self.api_key:
            return None
        ref = str(channel_ref or "").strip()
        if not ref:
            return None
        params: dict[str, str] = {"part": "snippet,contentDetails", "key": self.api_key}
        if ref.startswith("@"):
            params["forHandle"] = ref.lstrip("@")
        elif "youtube.com/@" in ref:
            handle = ref.split("youtube.com/@", 1)[1].split("/", 1)[0].split("?", 1)[0]
            params["forHandle"] = handle
        elif "youtube.com/channel/" in ref:
            cid = ref.split("youtube.com/channel/", 1)[1].split("/", 1)[0].split("?", 1)[0]
            params["id"] = cid
        elif ref.startswith("UC"):
            params["id"] = ref
        else:
            params["forHandle"] = ref

        resp = self.http.get(f"{YOUTUBE_DATA_API_BASE}/channels", params=params, timeout=20)
        if not resp.ok:
            return None
        items = (resp.json() or {}).get("items") or []
        if not items:
            return None
        ch = items[0]
        uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not uploads:
            return None
        return str(ch["id"]), str((ch.get("snippet") or {}).get("title") or ref), str(uploads)

    def scan_competitor_channel(self, channel_ref: str, *, max_videos: int = 20) -> list[dict[str, Any]]:
        """Fetch recent videos for one competitor channel and score outliers vs channel median."""
        resolved = self.resolve_channel_uploads_playlist(channel_ref)
        if resolved is None:
            return []
        channel_id, channel_title, uploads_playlist = resolved

        pl_resp = self.http.get(
            f"{YOUTUBE_DATA_API_BASE}/playlistItems",
            params={
                "part": "contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": max(5, min(50, int(max_videos))),
                "key": self.api_key,
            },
            timeout=20,
        )
        if not pl_resp.ok:
            return []
        video_ids = [
            str((it.get("contentDetails") or {}).get("videoId") or "")
            for it in (pl_resp.json() or {}).get("items") or []
        ]
        video_ids = [vid for vid in video_ids if vid]
        if not video_ids:
            return []

        vid_resp = self.http.get(
            f"{YOUTUBE_DATA_API_BASE}/videos",
            params={
                "part": "snippet,statistics",
                "id": ",".join(video_ids[:50]),
                "key": self.api_key,
            },
            timeout=20,
        )
        if not vid_resp.ok:
            return []
        raw_items = (vid_resp.json() or {}).get("items") or []
        view_counts = [
            int(((v.get("statistics") or {}).get("viewCount") or 0))
            for v in raw_items
        ]
        positive_views = [v for v in view_counts if v > 0]
        median_views = float(statistics.median(positive_views)) if positive_views else 1.0
        median_views = max(1.0, median_views)

        saved: list[dict[str, Any]] = []
        for v in raw_items:
            vid = str(v.get("id") or "")
            snippet = v.get("snippet") or {}
            stats = v.get("statistics") or {}
            views = int(stats.get("viewCount") or 0)
            ratio = round(views / median_views, 2)
            title = str(snippet.get("title") or "").strip()
            description = str(snippet.get("description") or "")
            record = {
                "channel_id": channel_id,
                "channel_title": channel_title,
                "video_id": vid,
                "title": title,
                "description": description,
                "views": views,
                "likes": int(stats.get("likeCount") or 0),
                "comments": int(stats.get("commentCount") or 0),
                "channel_median": round(median_views, 1),
                "outlier_ratio": ratio,
                "published_at": str(snippet.get("publishedAt") or ""),
                "music_hint": extract_music_hint(title, description),
            }
            self.db.upsert_competitor_video(record)
            saved.append(record)
        return saved

    def scan_all_competitors(self, channels: list[str]) -> int:
        """Scan all configured competitor channels and return total videos updated."""
        if not self.api_key or not channels:
            return 0
        total = 0
        for ch in channels:
            try:
                total += len(self.scan_competitor_channel(ch))
            except requests.RequestException:
                continue
        return total

    # --- OAuth Credentials Loader (for Analytics + Upload) ---

    def _load_oauth_credentials(self):
        """Load Google OAuth2 credentials from AGENT_YOUTUBE_TOKEN_PATH or AutoMap's instance/youtube."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not self.token_path.is_file():
            return None
        info = json.loads(self.token_path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(info)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            write_private_text(self.token_path, creds.to_json())
        return creds

    def has_upload_credentials(self) -> bool:
        """Return True if an OAuth token file is configured for the agent."""
        return self.token_path.is_file()

    def _fetch_analytics_video_metrics(
        self,
        analytics: Any,
        video_ids: list[str],
        *,
        start_date: str,
        end_date: str,
    ) -> dict[str, dict[str, Any]]:
        """Query YouTube Analytics API v2 for full engagement and retention metrics per video."""
        if not video_ids:
            return {}
        full_metrics = (
            "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
            "likes,dislikes,comments,shares,subscribersGained,subscribersLost"
        )
        fallback_metrics = "views,averageViewPercentage,likes,subscribersGained"
        results: dict[str, dict[str, Any]] = {}

        for start in range(0, len(video_ids), 50):
            batch = video_ids[start : start + 50]
            res = None
            for metric_str in (full_metrics, fallback_metrics):
                try:
                    res = (
                        analytics.reports()
                        .query(
                            ids="channel==MINE",
                            startDate=start_date,
                            endDate=end_date,
                            metrics=metric_str,
                            dimensions="video",
                            filters="video==" + ",".join(batch),
                        )
                        .execute()
                    )
                    break
                except Exception as exc:
                    if metric_str == fallback_metrics:
                        log.warning("YouTube Analytics video query failed: %s", exc)
                    continue
            if not res:
                continue
            headers = [str(col.get("name") or "") for col in (res.get("columnHeaders") or [])]
            for row in res.get("rows") or []:
                mapped = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
                vid = str(mapped.get("video") or "")
                if not vid:
                    continue
                results[vid] = {
                    "views": int(mapped["views"]) if mapped.get("views") is not None else None,
                    "estimated_minutes_watched": (
                        round(float(mapped["estimatedMinutesWatched"]), 2)
                        if mapped.get("estimatedMinutesWatched") is not None
                        else None
                    ),
                    "avg_view_duration_sec": (
                        round(float(mapped["averageViewDuration"]), 2)
                        if mapped.get("averageViewDuration") is not None
                        else None
                    ),
                    "avg_view_pct": (
                        round(float(mapped["averageViewPercentage"]), 2)
                        if mapped.get("averageViewPercentage") is not None
                        else None
                    ),
                    "likes": int(mapped["likes"]) if mapped.get("likes") is not None else None,
                    "dislikes": int(mapped["dislikes"]) if mapped.get("dislikes") is not None else None,
                    "comments": int(mapped["comments"]) if mapped.get("comments") is not None else None,
                    "shares": int(mapped["shares"]) if mapped.get("shares") is not None else None,
                    "subscribers_gained": (
                        int(mapped["subscribersGained"])
                        if mapped.get("subscribersGained") is not None
                        else None
                    ),
                    "subscribers_lost": (
                        int(mapped["subscribersLost"])
                        if mapped.get("subscribersLost") is not None
                        else None
                    ),
                }
        return results

    def _fetch_retention_curve(
        self,
        analytics: Any,
        video_id: str,
        *,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """Fetch audienceWatchRatio & relativeRetentionPerformance curve for one video."""
        try:
            res = (
                analytics.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="audienceWatchRatio,relativeRetentionPerformance",
                    dimensions="elapsedVideoTimeRatio",
                    filters=f"video=={video_id}",
                    sort="elapsedVideoTimeRatio",
                )
                .execute()
            )
        except Exception:
            return {}

        rows = res.get("rows") or []
        if not rows:
            return {}

        points: list[tuple[float, float, float]] = []
        for row in rows:
            try:
                ratio = float(row[0])
                watch = float(row[1]) * 100.0
                rel = float(row[2]) if len(row) > 2 and row[2] is not None else 1.0
                points.append((ratio, watch, rel))
            except (TypeError, ValueError, IndexError):
                continue

        if not points:
            return {}

        def _nearest(target_ratio: float) -> tuple[float, float, float]:
            return min(points, key=lambda pt: abs(pt[0] - target_ratio))

        hook_pt = _nearest(0.12)
        end_pt = _nearest(0.90)
        avg_rel = sum(pt[2] for pt in points) / len(points)
        sampled: list[dict[str, float | int]] = []
        for pct in range(10, 101, 10):
            pt = _nearest(pct / 100.0)
            sampled.append({
                "pct": pct,
                "watch": round(pt[1], 1),
                "rel": round(pt[2], 2),
            })

        return {
            "hook_retention_pct": round(hook_pt[1], 1),
            "end_retention_pct": round(end_pt[1], 1),
            "relative_retention": round(avg_rel, 2),
            "retention_curve": sampled,
        }

    def sync_own_top_topics(self, limit: int = 15) -> list[dict[str, Any]]:
        """Fetch the user's own YouTube channel videos with full retention & engagement analytics."""
        try:
            creds = self._load_oauth_credentials()
            if creds is None:
                return []
            from googleapiclient.discovery import build

            yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
            ch_res = yt.channels().list(part="contentDetails", mine=True).execute()
            ch_items = ch_res.get("items") or []
            if not ch_items:
                return []
            uploads_id = (
                (ch_items[0].get("contentDetails") or {})
                .get("relatedPlaylists", {})
                .get("uploads")
            )
            if not uploads_id:
                return []
            pl_res = yt.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_id,
                maxResults=50,
            ).execute()
            vids = [
                str((it.get("contentDetails") or {}).get("videoId") or "")
                for it in (pl_res.get("items") or [])
            ]
            vids = [v for v in vids if v]
            if not vids:
                return []
            v_res = yt.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(vids[:50]),
            ).execute()

            end_date = datetime.now(timezone.utc).date().isoformat()
            start_date = (datetime.now(timezone.utc).date() - timedelta(days=365)).isoformat()
            analytics = None
            analytics_by_id: dict[str, dict[str, Any]] = {}
            try:
                analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
                analytics_by_id = self._fetch_analytics_video_metrics(
                    analytics, vids[:50], start_date=start_date, end_date=end_date
                )
            except Exception:
                analytics = None

            ranked: list[dict[str, Any]] = []
            for item in v_res.get("items") or []:
                vid = str(item.get("id") or "")
                snip = item.get("snippet") or {}
                stats = item.get("statistics") or {}
                cdetails = item.get("contentDetails") or {}
                raw_title = str(snip.get("title") or "").strip()
                clean_title = re.sub(r"#\w+", "", raw_title).strip()
                clean_title = re.sub(r"\s+", " ", clean_title).strip(" -|:•")
                if not clean_title:
                    continue

                an = analytics_by_id.get(vid, {})
                views = int(stats.get("viewCount") or an.get("views") or 0)
                likes = int(stats.get("likeCount") or an.get("likes") or 0)
                comments = int(stats.get("commentCount") or an.get("comments") or 0)
                shares = int(an.get("shares") or 0)
                dislikes = int(an.get("dislikes") or 0)
                subs_gained = int(an.get("subscribers_gained") or 0)
                subs_lost = int(an.get("subscribers_lost") or 0)
                avg_pct = an.get("avg_view_pct")
                avg_dur = an.get("avg_view_duration_sec")
                est_min = an.get("estimated_minutes_watched")
                duration_sec = parse_iso8601_duration(str(cdetails.get("duration") or ""))
                eng_rate = round(((likes + comments + shares) / max(1, views)) * 100.0, 2) if views > 0 else 0.0
                score = compute_video_score(
                    views,
                    views,
                    avg_pct,
                    likes,
                    comments=comments,
                    shares=shares,
                    subscribers_gained=subs_gained,
                    subscribers_lost=subs_lost,
                )
                ranked.append(
                    {
                        "video_id": vid,
                        "title": clean_title,
                        "published_at": str(snip.get("publishedAt") or ""),
                        "duration_sec": duration_sec,
                        "views": views,
                        "likes": likes,
                        "dislikes": dislikes,
                        "comments": comments,
                        "shares": shares,
                        "subscribers_gained": subs_gained,
                        "subscribers_lost": subs_lost,
                        "estimated_minutes_watched": est_min,
                        "avg_view_pct": avg_pct,
                        "avg_view_duration_sec": avg_dur,
                        "engagement_rate": eng_rate,
                        "score": score or float(views),
                    }
                )
            ranked.sort(key=lambda r: (float(r.get("score") or 0.0), int(r["views"])), reverse=True)
            top = ranked[: max(1, int(limit))]

            if analytics is not None:
                for entry in top[:10]:
                    curve = self._fetch_retention_curve(
                        analytics, entry["video_id"], start_date=start_date, end_date=end_date
                    )
                    if curve:
                        entry.update(curve)

            if top:
                self.db.update_config({
                    "own_top_topics": top,
                    "own_analytics_synced_at": utc_now_iso(),
                })
            return top
        except Exception as exc:
            log.warning("sync_own_top_topics failed: %s", exc)
            return []

    # --- Own-Channel Analytics Sync ---

    def sync_own_analytics(self) -> int:
        """Update full views/retention/engagement/curve metrics for published experiments."""
        self.sync_own_top_topics(limit=15)
        published = self.db.list_experiments(status="published", limit=60)
        targets = [exp for exp in published if exp.get("youtube_video_id")]
        if not targets:
            return 0

        video_ids = [str(exp["youtube_video_id"]) for exp in targets]
        data_by_id: dict[str, dict[str, Any]] = {}
        analytics_by_id: dict[str, dict[str, Any]] = {}
        curves_by_id: dict[str, dict[str, Any]] = {}

        # 1. Basic views, likes, comments & duration via YouTube Data API (API key or OAuth).
        if self.api_key:
            for start in range(0, len(video_ids), 50):
                batch = video_ids[start : start + 50]
                try:
                    resp = self.http.get(
                        f"{YOUTUBE_DATA_API_BASE}/videos",
                        params={"part": "statistics,contentDetails", "id": ",".join(batch), "key": self.api_key},
                        timeout=20,
                    )
                    if resp.ok:
                        for item in (resp.json() or {}).get("items") or []:
                            stats = item.get("statistics") or {}
                            cdetails = item.get("contentDetails") or {}
                            data_by_id[str(item["id"])] = {
                                "views": int(stats.get("viewCount") or 0),
                                "likes": int(stats.get("likeCount") or 0),
                                "comments": int(stats.get("commentCount") or 0),
                                "duration_sec": parse_iso8601_duration(str(cdetails.get("duration") or "")),
                            }
                except requests.RequestException:
                    continue

        # 2. Full Analytics metrics + retention curve via OAuth when credentials exist.
        try:
            creds = self._load_oauth_credentials()
            if creds is not None:
                from googleapiclient.discovery import build

                if not data_by_id:
                    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
                    for start in range(0, len(video_ids), 50):
                        batch = video_ids[start : start + 50]
                        v_res = yt.videos().list(part="statistics,contentDetails", id=",".join(batch)).execute()
                        for item in v_res.get("items") or []:
                            stats = item.get("statistics") or {}
                            cdetails = item.get("contentDetails") or {}
                            data_by_id[str(item["id"])] = {
                                "views": int(stats.get("viewCount") or 0),
                                "likes": int(stats.get("likeCount") or 0),
                                "comments": int(stats.get("commentCount") or 0),
                                "duration_sec": parse_iso8601_duration(str(cdetails.get("duration") or "")),
                            }

                analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
                end_date = datetime.now(timezone.utc).date().isoformat()
                start_date = (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()
                analytics_by_id = self._fetch_analytics_video_metrics(
                    analytics, video_ids, start_date=start_date, end_date=end_date
                )
                for vid in video_ids[:15]:
                    curve = self._fetch_retention_curve(
                        analytics, vid, start_date=start_date, end_date=end_date
                    )
                    if curve:
                        curves_by_id[vid] = curve
        except Exception as exc:
            log.warning("YouTube Analytics sync skipped or failed: %s", exc)

        updated = 0
        now = datetime.now(timezone.utc)
        synced_at = utc_now_iso()
        for exp in targets:
            vid = str(exp["youtube_video_id"])
            if vid not in data_by_id and vid not in analytics_by_id and vid not in curves_by_id:
                continue

            d_info = data_by_id.get(vid, {})
            a_info = analytics_by_id.get(vid, {})
            c_info = curves_by_id.get(vid, {})

            views = int(
                d_info.get("views")
                if d_info.get("views") is not None
                else (a_info.get("views") if a_info.get("views") is not None else (exp.get("views_72h") or exp.get("views_24h") or 0))
            )
            likes = int(
                d_info.get("likes")
                if d_info.get("likes") is not None
                else (a_info.get("likes") if a_info.get("likes") is not None else (exp.get("likes_72h") or 0))
            )
            comments = (
                d_info.get("comments")
                if d_info.get("comments") is not None
                else (a_info.get("comments") if a_info.get("comments") is not None else exp.get("comments_72h"))
            )
            shares = a_info.get("shares") if a_info.get("shares") is not None else exp.get("shares_72h")
            dislikes = a_info.get("dislikes") if a_info.get("dislikes") is not None else exp.get("dislikes_72h")
            avg_pct = a_info.get("avg_view_pct") if a_info.get("avg_view_pct") is not None else exp.get("avg_view_pct")
            avg_dur = (
                a_info.get("avg_view_duration_sec")
                if a_info.get("avg_view_duration_sec") is not None
                else exp.get("avg_view_duration_sec")
            )
            duration_sec = d_info.get("duration_sec") if d_info.get("duration_sec") is not None else exp.get("duration_sec")
            subs_gained = (
                a_info.get("subscribers_gained")
                if a_info.get("subscribers_gained") is not None
                else exp.get("subscribers_gained")
            )
            subs_lost = (
                a_info.get("subscribers_lost")
                if a_info.get("subscribers_lost") is not None
                else exp.get("subscribers_lost")
            )
            est_min = (
                a_info.get("estimated_minutes_watched")
                if a_info.get("estimated_minutes_watched") is not None
                else exp.get("estimated_minutes_watched")
            )

            age_hours = 72.0
            if exp.get("published_at"):
                try:
                    pub_dt = datetime.fromisoformat(str(exp["published_at"]).replace("Z", "+00:00"))
                    age_hours = (now - pub_dt).total_seconds() / 3600.0
                except ValueError:
                    pass

            fields: dict[str, Any] = {
                "likes_72h": likes,
                "analytics_synced_at": synced_at,
            }
            if age_hours < 48.0:
                fields["views_24h"] = views
            else:
                fields["views_72h"] = views
                if exp.get("views_24h") is None:
                    fields["views_24h"] = views

            if avg_pct is not None:
                fields["avg_view_pct"] = round(float(avg_pct), 2)
            if avg_dur is not None:
                fields["avg_view_duration_sec"] = round(float(avg_dur), 2)
            if duration_sec is not None:
                fields["duration_sec"] = int(duration_sec)
            if comments is not None:
                fields["comments_72h"] = int(comments)
            if shares is not None:
                fields["shares_72h"] = int(shares)
            if dislikes is not None:
                fields["dislikes_72h"] = int(dislikes)
            if subs_gained is not None:
                fields["subscribers_gained"] = int(subs_gained)
            if subs_lost is not None:
                fields["subscribers_lost"] = int(subs_lost)
            if est_min is not None:
                fields["estimated_minutes_watched"] = round(float(est_min), 2)
            if c_info:
                fields["hook_retention_pct"] = c_info["hook_retention_pct"]
                fields["end_retention_pct"] = c_info["end_retention_pct"]
                fields["relative_retention"] = c_info["relative_retention"]
                fields["retention_curve_json"] = json.dumps(c_info["retention_curve"])

            score = compute_video_score(
                fields.get("views_24h", exp.get("views_24h")),
                fields.get("views_72h", exp.get("views_72h")),
                fields.get("avg_view_pct", exp.get("avg_view_pct")),
                likes,
                comments=fields.get("comments_72h"),
                shares=fields.get("shares_72h"),
                subscribers_gained=fields.get("subscribers_gained"),
                subscribers_lost=fields.get("subscribers_lost"),
            )
            if score is not None:
                fields["score"] = score
            self.db.update_experiment(int(exp["id"]), **fields)
            updated += 1
        return updated

    # --- Video Upload ---

    def upload_video(
        self,
        *,
        mp4_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "private",
        publish_at: str | None = None,
    ) -> tuple[str, str]:
        """Upload an MP4 file to YouTube and return (video_id, watch_url)."""
        creds = self._load_oauth_credentials()
        if creds is None:
            raise RuntimeError(
                f"Agent YouTube OAuth token not found at {self.token_path}. "
                "Connect YouTube OAuth credentials before uploading."
            )
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        vis = privacy if privacy in ("private", "unlisted", "public") else "private"
        status_body: dict[str, Any] = {"privacyStatus": vis, "selfDeclaredMadeForKids": False}
        if publish_at:
            # YouTube requires privacyStatus='private' whenever publishAt is set.
            status_body["privacyStatus"] = "private"
            status_body["publishAt"] = publish_at

        body = {
            "snippet": {
                "title": title.strip()[:100],
                "description": description[:5000],
                "tags": tags[:20],
                "categoryId": "22",
            },
            "status": status_body,
        }
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        media = MediaFileUpload(str(mp4_path), mimetype="video/mp4", resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = str(response["id"])
        return video_id, f"https://youtube.com/shorts/{video_id}"
