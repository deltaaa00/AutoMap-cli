"""24/7 autonomous Shorts agent runner coordinating AutoMap, YouTube, Telegram, and /agent UI."""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent.automap_client import AutoMapClient, AutoMapClientError
from agent.db import AgentDB, utc_now_iso
from agent.media_pool import MediaPool
from agent.optimizer import AgentOptimizer, infer_label_suffix, render_video_description
from agent.telegram_bot import TelegramBot
from agent.youtube_intel import YouTubeIntel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [automap-agent] %(levelname)s: %(message)s",
)
log = logging.getLogger("automap-agent")


class AgentRunner:
    """Orchestrates daily competitor/analytics sync, Short rendering, and 1-click publishing."""

    def __init__(
        self,
        *,
        db: AgentDB | None = None,
        client: AutoMapClient | None = None,
        intel: YouTubeIntel | None = None,
        bot: TelegramBot | None = None,
        media_pool: MediaPool | None = None,
    ) -> None:
        self.db = db or AgentDB()
        cfg = self.db.get_config()
        self.client = client or AutoMapClient()
        self.intel = intel or YouTubeIntel(self.db)
        self.bot = bot or TelegramBot()
        self.media_pool = media_pool or MediaPool(series_size=int(cfg.get("series_size") or 4))
        self.optimizer = AgentOptimizer(self.db)
        self.renders_dir = self.db.path.parent / "agent_renders"
        self.renders_dir.mkdir(parents=True, exist_ok=True)
        self.media_pool.ensure_dirs()
        self._last_sync_date = ""
        last_sync_raw = str(cfg.get("last_sync_at") or "").strip()
        if last_sync_raw:
            try:
                dt = datetime.fromisoformat(last_sync_raw.replace("Z", "+00:00"))
                tz_name = str(cfg.get("timezone") or "Europe/Kyiv")
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = timezone.utc
                self._last_sync_date = dt.astimezone(tz).strftime("%Y-%m-%d")
            except ValueError:
                pass
        raw_slots = cfg.get("triggered_slots")
        self._triggered_slots: set[str] = {
            str(s).strip() for s in (raw_slots if isinstance(raw_slots, list) else []) if str(s).strip()
        }
        self.refresh_runtime_secrets()

    def refresh_runtime_secrets(self) -> None:
        """Reload Telegram, YouTube, and AutoMap server credentials saved from the UI."""
        sec = self.db.get_secrets()
        cfg = self.db.get_config()
        self.bot.token = sec["telegram_bot_token"]
        self.bot.chat_id = sec["telegram_chat_id"]
        self.intel.api_key = sec["youtube_api_key"]
        desired_url = (
            str(cfg.get("automap_url") or "").strip()
            or os.environ.get("AUTOMAP_URL")
            or "http://host.docker.internal:3000"
        ).rstrip("/")
        if desired_url and desired_url != self.client.base_url:
            self.client.base_url = desired_url
            self.client._logged_in = False
        if sec.get("automap_login") and sec["automap_login"] != self.client.login_user:
            self.client.login_user = sec["automap_login"]
            self.client._logged_in = False
        if sec.get("automap_password") and sec["automap_password"] != self.client.login_password:
            self.client.login_password = sec["automap_password"]
            self.client._logged_in = False

    def _local_now(self, tz_name: str) -> datetime:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
        return datetime.now(tz)

    def _pick_zoom_target(
        self, map_type: str, map_values: dict[str, Any] | None
    ) -> tuple[str, float, float, bool]:
        """Pick the unique highest-value country for zoom; return ``is_unique=False`` on ties."""
        map_kind = "world" if map_type in ("world", "world_countries") else "europe"
        try:
            countries = self.client.get_zoom_countries().get(map_kind) or []
        except Exception:
            countries = []
        if not countries or not map_values:
            return ("", 0.0, 0.0, False)

        by_key = {str(c.get("key") or "").lower(): c for c in countries}
        numeric_by_key: dict[str, float] = {}
        for k, v in map_values.items():
            norm = str(k).strip().lower().replace(" ", "_").replace("-", "_")
            if norm not in by_key:
                continue
            try:
                num = float(str(v).replace(",", "").strip())
            except ValueError:
                continue
            numeric_by_key[norm] = num

        if not numeric_by_key:
            return ("", 0.0, 0.0, False)

        max_val = max(numeric_by_key.values())
        highest_keys = [k for k, num in numeric_by_key.items() if abs(num - max_val) < 1e-9]
        if len(highest_keys) != 1:
            return ("", 0.0, 0.0, False)

        best_key = highest_keys[0]
        entry = by_key[best_key]
        return best_key, float(entry["x"]), float(entry["y"]), True

    def run_daily_sync(self) -> dict[str, int]:
        """Sync own YouTube Analytics and scan competitor channels for viral outliers & music."""
        cfg = self.db.get_config()
        channels = [str(c) for c in (cfg.get("competitor_channels") or []) if str(c).strip()]
        analytics_updated = self.intel.sync_own_analytics()
        competitor_updated = self.intel.scan_all_competitors(channels)
        self.db.update_config({"last_sync_at": utc_now_iso()})

        # Push up to 2 viral competitor music suggestions to Telegram.
        for comp in self.db.list_unnotified_music_suggestions(min_ratio=2.0, limit=2):
            msg_id = self.bot.send_music_suggestion(comp)
            if msg_id is not None:
                self.db.mark_competitor_music_notified(int(comp["id"]))

        log.info(
            "Daily sync complete: analytics_updated=%s competitor_videos=%s",
            analytics_updated,
            competitor_updated,
        )
        return {"analytics_updated": analytics_updated, "competitor_updated": competitor_updated}

    def generate_candidate_video(
        self,
        *,
        posting_hour: int | None = None,
        scheduled_iso: str = "",
        forced_topic: str = "",
    ) -> dict[str, Any]:
        """Pick an A/B arm + topic + 4-video series media, render Short via AutoMap, queue approval."""
        cfg = self.db.get_config()
        self.media_pool.series_size = max(1, int(cfg.get("series_size") or 4))

        series_video_idx = self.db.count_created_experiments()
        media = self.media_pool.select_for_video_index(series_video_idx)
        if media.background is None:
            raise RuntimeError(
                f"No background video found in {self.media_pool.backgrounds_dir}. "
                "Add at least one .mp4/.mov/.webm (<= 15 MB)."
            )

        arm, arm_reason = self.optimizer.choose_arm()
        recent_topics = self.db.list_recent_topics(limit=50)
        provider = str(cfg.get("ai_provider") or "gemini")
        model = str(cfg.get("ai_model") or "gemini-2.5-flash")

        source_comp_vid = ""
        topic = forced_topic.strip()
        mode = "number"
        palette_theme = "auto"
        font = "Bips.ttf"
        extra_option = "show_most_least"
        topic_reason = ""

        if not topic:
            comp = self.optimizer.pick_competitor_inspiration()
            if comp is not None:
                source_comp_vid = str(comp["video_id"])
                cleaned = self.optimizer.clean_competitor_title_to_topic(str(comp["title"]))
                if cleaned:
                    topic = cleaned
                    topic_reason = (
                        f"Inspired by competitor '{comp.get('channel_title')}' "
                        f"({comp.get('outlier_ratio')}x views)"
                    )

        secrets = self.db.get_secrets()
        ai_api_key = secrets["deepseek_api_key"] if provider == "deepseek" else secrets["gemini_api_key"]
        gemini_fallbacks = (
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash",
            "gemini-4-hydrogen",
            "gemini-4-carbon",
            "gemma-4-26b-a4b-it",
            "gemma-4-31b-it",
        )
        candidate_models = (
            [model] + [m for m in gemini_fallbacks if m != model]
            if provider == "gemini"
            else [model]
        )

        def _is_retryable_model_error(err: AutoMapClientError) -> bool:
            msg_low = str(err).lower()
            if "api key" in msg_low or "rejected" in msg_low:
                return False
            return any(
                tok in msg_low
                for tok in (
                    "overloaded",
                    "temporarily unavailable",
                    "could not be used",
                    "not available",
                    "quota",
                    "rate limit",
                )
            )

        own_top_topics: list[str] = []
        for item in (cfg.get("own_top_topics") or []):
            if not isinstance(item, dict):
                continue
            t_title = str(item.get("title") or "").strip()
            if not t_title:
                continue
            stats_parts: list[str] = []
            if item.get("views"):
                stats_parts.append(f"views: {int(item['views']):,}")
            if item.get("avg_view_pct") is not None:
                stats_parts.append(f"retention: {float(item['avg_view_pct']):.1f}%")
            if item.get("hook_retention_pct") is not None:
                stats_parts.append(f"hook: {float(item['hook_retention_pct']):.0f}%")
            if item.get("likes"):
                stats_parts.append(f"likes: {int(item['likes'])}")
            if item.get("comments"):
                stats_parts.append(f"comments: {int(item['comments'])}")
            if item.get("shares"):
                stats_parts.append(f"shares: {int(item['shares'])}")
            own_top_topics.append(f"{t_title} ({', '.join(stats_parts)})" if stats_parts else t_title)
            if len(own_top_topics) >= 10:
                break

        if not topic:
            idea_resp = None
            last_idea_err: AutoMapClientError | None = None
            for cand_model in candidate_models:
                try:
                    idea_resp = self.client.generate_idea(
                        provider=provider,
                        model=cand_model,
                        with_params=True,
                        avoid_ideas=recent_topics,
                        top_channel_topics=own_top_topics,
                        api_key=ai_api_key,
                    )
                    model = cand_model
                    break
                except AutoMapClientError as exc:
                    last_idea_err = exc
                    if _is_retryable_model_error(exc):
                        log.warning("Model %s unavailable during generate_idea (%s), trying fallback...", cand_model, exc)
                        continue
                    raise
            if idea_resp is None and last_idea_err is not None:
                raise last_idea_err
            assert idea_resp is not None
            topic = str(idea_resp.get("idea") or "").strip()
            p = idea_resp.get("params") or {}
            mode = str(p.get("mode") or "number")
            palette_theme = str(p.get("palette_theme") or "auto")
            font = str(p.get("font") or "Bips.ttf")
            extra_option = str(p.get("extra_option") or ("show_most_least" if mode == "number" else "none"))
            topic_reason = "Fresh AI-generated topic avoiding recent history"

        zoom_roll = arm.zoom_enabled and arm.reveal_enabled and (random.random() < 0.20)
        if zoom_roll:
            mode = "number"
            extra_option = "show_most_least"

        label_suffix = infer_label_suffix(topic, mode)
        map_payload: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "topic": topic,
            "mode": mode,
            "map_type": arm.map_type,
            "gsm": 15,
            "extra_option": extra_option if mode == "number" else "none",
            "font": font,
            "palette_theme": palette_theme,
            "auto_style": True,
            "show_legend": False,
            "highlight_extrema": zoom_roll,
            "label_suffix": label_suffix,
        }
        if ai_api_key:
            map_payload["api_key"] = ai_api_key
        map_resp = None
        last_map_err: AutoMapClientError | None = None
        for cand_model in ([model] + [m for m in candidate_models if m != model]):
            map_payload["model"] = cand_model
            try:
                map_resp = self.client.generate_map(map_payload)
                model = cand_model
                break
            except AutoMapClientError as exc:
                last_map_err = exc
                if _is_retryable_model_error(exc):
                    log.warning("Model %s unavailable during generate_map (%s), trying fallback...", cand_model, exc)
                    continue
                raise
        if map_resp is None and last_map_err is not None:
            raise last_map_err
        assert map_resp is not None
        map_data_url = str(map_resp["image_url"])
        map_values = map_resp.get("map_values") if isinstance(map_resp.get("map_values"), dict) else {}

        zoom_key, zoom_x, zoom_y, has_unique_highest = (
            self._pick_zoom_target(arm.map_type, map_values)
            if zoom_roll
            else ("", 0.0, 0.0, False)
        )
        effective_zoom = zoom_roll and has_unique_highest
        effective_reveal = arm.reveal_enabled and effective_zoom
        effective_zoom_out = arm.zoom_out_enabled and effective_zoom
        if zoom_roll and not has_unique_highest:
            log.info("Skipping zoom/reveal for '%s': no unique highest-value country.", topic)

        reveal_clean_map_file = ""
        if effective_reveal and zoom_key and map_values:
            try:
                clean_payload = dict(map_payload)
                clean_payload["ai_answer_override"] = map_values
                clean_payload["skip_label_countries"] = [zoom_key]
                clean_resp = self.client.generate_map(clean_payload)
                reveal_clean_map_file = str(clean_resp.get("image_url") or "")
            except Exception:
                log.warning("Could not render clean reveal base map for %s", zoom_key, exc_info=True)
        if effective_reveal and not reveal_clean_map_file:
            effective_reveal = False
            effective_zoom = False
            effective_zoom_out = False
            log.warning("Skipping zoom/reveal for %s because its clean map is unavailable", zoom_key)

        duration = max(9, min(15, int(cfg.get("video_duration") or 11)))
        render_extra: dict[str, Any] = {
            "intro_fade": arm.intro_fade,
            "subscribe_badge": arm.subscribe_badge,
            "watermark_text": str(cfg.get("watermark_text") or "TERRAMAPPER"),
            "watermark_stars": bool(cfg.get("watermark_stars", True)),
            "top_left_sticker": "gen_diecut:auto",
            "top_right_sticker": "gen_diecut:auto",
            "zoom_enabled": effective_zoom,
            "zoom_x": zoom_x,
            "zoom_y": zoom_y,
            "zoom_country_key": zoom_key,
            "zoom_scale": 2.0,
            "zoom_start": 1.5,
            "zoom_duration": 2.5,
            "zoom_out_enabled": effective_zoom_out,
            "zoom_out_duration": 2.0,
            "reveal_enabled": effective_reveal,
            "reveal_clean_map_file": reveal_clean_map_file,
            "reveal_time": 5.0,
        }
        render_resp = self.client.render_video(
            map_data_url=map_data_url,
            background_path=media.background,
            video_title=topic,
            duration=duration,
            audio_path=media.music,
            extra_params=render_extra,
        )

        rationale = f"{topic_reason}. {arm_reason}."
        params_record = {
            "mode": mode,
            "map_type": arm.map_type,
            "palette_theme": palette_theme,
            "font": font,
            "label_suffix": label_suffix,
            "zoom_enabled": effective_zoom,
            "zoom_country_key": zoom_key,
            "reveal_enabled": effective_reveal,
            "zoom_out_enabled": effective_zoom_out,
            "background_file": media.background.name,
            "music_file": media.music.name if media.music else "",
            "series_index": media.series_index + 1,
            "slot_in_series": media.slot_in_series,
            "series_size": media.series_size,
            "duration": duration,
        }

        exp = self.db.create_experiment(
            topic=topic,
            arm=arm.name,
            params=params_record,
            rationale=rationale,
            source_competitor_video_id=source_comp_vid,
            posting_hour=posting_hour if posting_hour is not None else 14,
            scheduled_for=scheduled_iso,
            status="rendering",
        )
        exp_id = int(exp["id"])
        dest_mp4 = self.renders_dir / f"exp_{exp_id}.mp4"
        try:
            self.client.download_rendered_video(str(render_resp["download_url"]), dest_mp4)
        except Exception as exc:
            self.db.update_experiment(exp_id, status="failed", error_message=str(exc))
            raise
        exp = self.db.update_experiment(exp_id, status="pending_approval", mp4_path=str(dest_mp4)) or exp
        if source_comp_vid:
            self.db.mark_competitor_used(source_comp_vid)

        media_note = (
            f"Series #{media.series_index + 1} ({media.slot_in_series}/{media.series_size}): "
            f"{media.background.name}" + (f" + {media.music.name}" if media.music else "")
        )
        sched_label = scheduled_iso[11:16] if len(scheduled_iso) >= 16 else f"{exp['posting_hour']:02d}:00"
        msg_id = self.bot.send_approval_video(
            exp_id=int(exp["id"]),
            mp4_path=dest_mp4,
            topic=topic,
            arm=arm.name,
            rationale=rationale,
            scheduled_label=sched_label,
            media_note=media_note,
        )
        if msg_id is not None:
            self.db.update_experiment(int(exp["id"]), telegram_message_id=msg_id)

        log.info("Generated candidate experiment #%s (%s, arm=%s)", exp["id"], topic, arm.name)
        return self.db.get_experiment(int(exp["id"])) or exp

    def handle_experiment_action(self, exp_id: int, action: str) -> str:
        """Execute publish / schedule / private / regenerate / reject on an experiment."""
        exp = self.db.get_experiment(exp_id)
        if exp is None:
            raise ValueError(f"Experiment #{exp_id} not found.")

        act = action.strip().lower()
        if act not in {"publish", "schedule", "private", "regenerate", "reject"}:
            raise ValueError(f"Unknown action: {action}")
        if not self.db.claim_experiment_action(exp_id, act):
            raise ValueError(f"Short #{exp_id} is no longer awaiting approval.")

        if act == "reject":
            self.db.update_experiment(exp_id, status="rejected", requested_action="reject")
            return f"Short #{exp_id} rejected."

        if act == "regenerate":
            try:
                new_exp = self.generate_candidate_video(
                    posting_hour=int(exp.get("posting_hour") or 14),
                    scheduled_iso=str(exp.get("scheduled_for") or ""),
                )
            except Exception:
                self.db.update_experiment(exp_id, status="pending_approval", requested_action="")
                raise
            self.db.update_experiment(exp_id, status="rejected")
            return f"Generated new variant Short #{new_exp['id']}: {new_exp['topic']}"

        if act in ("publish", "schedule", "private"):
            mp4_path = Path(str(exp.get("mp4_path") or ""))
            if not mp4_path.is_file():
                self.db.update_experiment(exp_id, status="failed", error_message="Rendered MP4 file missing.")
                raise ValueError(f"Rendered MP4 for Short #{exp_id} is missing.")
            privacy = "public" if act == "publish" else "private"
            publish_at = str(exp.get("scheduled_for") or "") if act == "schedule" else None
            if act == "schedule":
                now = datetime.now(timezone.utc)
                try:
                    slot = datetime.fromisoformat(publish_at or "")
                except ValueError:
                    slot = now
                if slot.tzinfo is None or slot <= now:
                    publish_at = (now + timedelta(minutes=30)).replace(microsecond=0).isoformat()
            cfg = self.db.get_config()
            exp_params = exp.get("params") if isinstance(exp.get("params"), dict) else {}
            map_type = str(exp_params.get("map_type") or "default")
            desc, tags = render_video_description(
                topic=str(exp["topic"]),
                map_type=map_type,
                template=str(cfg.get("description_template") or ""),
            )
            try:
                vid_id, vid_url = self.intel.upload_video(
                    mp4_path=mp4_path,
                    title=str(exp["topic"]),
                    description=desc,
                    tags=tags,
                    privacy=privacy,
                    publish_at=publish_at,
                )
            except Exception as exc:
                self.db.update_experiment(exp_id, status="failed", error_message=str(exc))
                raise RuntimeError(f"Upload failed for #{exp_id}: {exc}") from exc

            new_status = "scheduled" if act == "schedule" else "published"
            self.db.update_experiment(
                exp_id,
                status=new_status,
                requested_action=act,
                requested_privacy=privacy,
                youtube_video_id=vid_id,
                youtube_url=vid_url,
                published_at=utc_now_iso(),
                error_message="",
                **({"scheduled_for": publish_at} if publish_at else {}),
            )
            return f"Uploaded Short #{exp_id} ({act}): {vid_url}"

    def import_competitor_music(self, comp_id: int) -> str:
        """Download audio from a viral competitor Short into agent_media/music/."""
        comp = self.db.get_competitor_video(comp_id)
        if comp is None:
            return f"Competitor video #{comp_id} not found."
        url = f"https://www.youtube.com/shorts/{comp['video_id']}"
        title, audio_bytes = self.client.download_music_from_url(url)
        saved = self.media_pool.save_music_bytes(title, audio_bytes, ext=".mp3")
        self.db.mark_competitor_music_imported(comp_id)
        return f"Added track to pool: {saved.name}"

    def process_telegram_callbacks(self) -> None:
        """Handle inline button taps from Telegram."""
        for cb in self.bot.poll_callbacks(timeout=1):
            data = cb["data"]
            parts = data.split(":")
            msg = "Done."
            try:
                if len(parts) == 3 and parts[0] == "exp":
                    msg = self.handle_experiment_action(int(parts[2]), parts[1])
                elif len(parts) == 3 and parts[0] == "music":
                    comp_id = int(parts[2])
                    if parts[1] == "import":
                        msg = self.import_competitor_music(comp_id)
                    else:
                        self.db.mark_competitor_music_notified(comp_id)
                        msg = "Skipped."
            except Exception as exc:
                msg = f"Error: {exc}"
                log.exception("Telegram callback failed: %s", data)
            self.bot.answer_callback(cb["callback_id"], msg)
            if msg and not msg.startswith("Skipped"):
                self.bot.send_message(msg)

    def process_web_commands(self) -> None:
        """Execute queued actions triggered from the /agent web dashboard."""
        for cmd in self.db.list_pending_commands():
            cmd_id = int(cmd["id"])
            name = str(cmd["command"])
            payload = cmd["payload"]
            try:
                if name == "trigger_generate":
                    exp = self.generate_candidate_video(
                        forced_topic=str(payload.get("topic") or ""),
                    )
                    self.db.complete_command(cmd_id, status="done", message=f"Created #{exp['id']}")
                elif name == "sync_now":
                    res = self.run_daily_sync()
                    self.db.complete_command(cmd_id, status="done", message=str(res))
                elif name == "experiment_action":
                    res_msg = self.handle_experiment_action(
                        int(payload["exp_id"]),
                        str(payload["action"]),
                    )
                    self.db.complete_command(cmd_id, status="done", message=res_msg)
                elif name == "import_music":
                    res_msg = self.import_competitor_music(int(payload["comp_id"]))
                    self.db.complete_command(cmd_id, status="done", message=res_msg)
                elif name == "import_music_url":
                    url = str(payload.get("url") or "").strip()
                    title, audio_bytes = self.client.download_music_from_url(url)
                    saved = self.media_pool.save_music_bytes(title, audio_bytes, ext=".mp3")
                    self.db.complete_command(cmd_id, status="done", message=f"Додано трек: {saved.name}")
                else:
                    self.db.complete_command(cmd_id, status="failed", message=f"Unknown command: {name}")
            except Exception as exc:
                log.exception("Command %s (#%s) failed", name, cmd_id)
                self.db.complete_command(cmd_id, status="failed", message=str(exc))

    def check_schedule_tick(self) -> None:
        """Check whether daily sync or one of the daily Short slots should run now."""
        cfg = self.db.get_config()
        now_local = self._local_now(str(cfg.get("timezone") or "Europe/Kyiv"))
        today_str = now_local.strftime("%Y-%m-%d")

        if self._last_sync_date != today_str and now_local.hour >= 8:
            try:
                self.run_daily_sync()
            except Exception:
                log.exception("Daily sync failed")
            self._last_sync_date = today_str

        posts_per_day = max(1, min(6, int(cfg.get("posts_per_day") or 2)))
        hours = self.optimizer.choose_posting_hours(
            posts_per_day=posts_per_day,
            fallback_hours=cfg.get("default_hours"),
        )
        for hour in hours:
            slot_key = f"{today_str}@{hour:02d}"
            if slot_key in self._triggered_slots:
                continue
            slot_dt = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
            trigger_dt = slot_dt - timedelta(minutes=30)
            if trigger_dt <= now_local <= slot_dt + timedelta(minutes=15):
                self._triggered_slots.add(slot_key)
                self.db.update_config({"triggered_slots": sorted(self._triggered_slots)[-20:]})
                if self.db.count_experiments_for_date(today_str) >= posts_per_day:
                    continue
                try:
                    self.generate_candidate_video(
                        posting_hour=hour,
                        scheduled_iso=slot_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    )
                except (AutoMapClientError, RuntimeError) as exc:
                    log.warning("Scheduled generation skipped for %s: %s", slot_key, exc)
                except Exception:
                    log.exception("Scheduled generation failed for %s", slot_key)

    def run_forever(self) -> None:
        """Main 24/7 loop."""
        log.info("automap-agent started (db=%s, media=%s)", self.db.path, self.media_pool.root)
        last_heartbeat = 0.0
        last_schedule = 0.0
        while True:
            self.refresh_runtime_secrets()
            now_mono = time.monotonic()
            if now_mono - last_heartbeat >= 30.0:
                try:
                    self.db.touch_heartbeat()
                except Exception:
                    log.exception("Heartbeat update failed")
                last_heartbeat = now_mono

            self.process_web_commands()
            self.process_telegram_callbacks()

            if now_mono - last_schedule >= 60.0:
                self.check_schedule_tick()
                last_schedule = now_mono

            time.sleep(3.0)


def main() -> None:
    if os.environ.get("AGENT_ENABLED", "TRUE").strip().upper() in ("0", "FALSE", "NO", "OFF"):
        log.info("AGENT_ENABLED is FALSE; idling cleanly.")
        while True:
            time.sleep(300)
    AgentRunner().run_forever()


if __name__ == "__main__":
    main()
