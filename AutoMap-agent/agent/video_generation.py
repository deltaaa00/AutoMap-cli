"""Generate one candidate Short from topic selection through approval delivery.

AgentRunner supplies explicit database, AutoMap client, Telegram bot, optimizer,
media pool, and render-directory dependencies. The generator picks a preset and
topic, handles model/search fallbacks, prepares optional zoom/reveal inputs,
and asks AutoMap to render. It records the experiment, downloads the MP4, and
moves it to pending approval before notifying Telegram. Rendering uses remote
HTTP calls and local file writes; publishing and scheduling remain in runner.py.
Map values are untrusted service data and may not contain a unique zoom target."""

import logging
import random
from pathlib import Path
from typing import Any

from .automap_client import AutoMapClient
from .automap_client import AutoMapClientError
from .db import AgentDB
from .media_pool import MediaPool
from .optimizer import AgentOptimizer, infer_label_suffix
from .telegram_bot import TelegramBot

log = logging.getLogger("automap-agent")


class CandidateVideoGenerator:
    """Coordinate topic selection, map rendering, media, and approval delivery."""

    def __init__(
        self,
        *,
        db: AgentDB,
        client: AutoMapClient,
        bot: TelegramBot,
        media_pool: MediaPool,
        optimizer: AgentOptimizer,
        renders_dir: Path,
    ) -> None:
        self.db = db
        self.client = client
        self.bot = bot
        self.media_pool = media_pool
        self.optimizer = optimizer
        self.renders_dir = renders_dir

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

    def generate(
        self,
        *,
        posting_hour: int | None = None,
        scheduled_iso: str = "",
        forced_topic: str = "",
    ) -> dict[str, Any]:
        """Select topic/media, render a candidate, persist it, and request approval.

        A forced topic skips idea selection; otherwise competitor inspiration or AI
        provides it. Model/search fallbacks are limited to recognized service failures.
        No suitable background raises before rendering. Download failure records a
        failed experiment; successful output becomes pending_approval, never published."""
        cfg = self.db.get_config()
        self.media_pool.series_size = max(1, int(cfg.get("series_size") or 4))

        series_video_idx = self.db.count_created_experiments()
        media = self.media_pool.select_for_video_index(series_video_idx)
        if media.background is None:
            raise RuntimeError(
                f"No background video found in {self.media_pool.backgrounds_dir}. "
                "Add at least one .mp4/.mov/.webm (<= 100 MB)."
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
        topic_reason = "Custom user idea" if topic else ""

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
            topic_summary = t_title
            if stats_parts:
                topic_summary += f" ({', '.join(stats_parts)})"
            own_top_topics.append(topic_summary)
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
            default_extra_option = "none"
            if mode == "number":
                default_extra_option = "show_most_least"
            extra_option = str(p.get("extra_option") or default_extra_option)
            topic_reason = "Fresh AI-generated topic avoiding recent history"

        zoom_roll = arm.zoom_enabled and arm.reveal_enabled and (random.random() < 0.20)
        if zoom_roll:
            mode = "number"
            extra_option = "show_most_least"

        label_suffix = infer_label_suffix(topic, mode)
        web_search_enabled = bool(cfg.get("web_search_enabled", True))
        max_search_requests = max(1, min(5, int(cfg.get("max_search_requests") or 5)))
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
        if web_search_enabled:
            map_payload["web_search"] = True
            map_payload["max_search_requests"] = max_search_requests
            if secrets.get("tavily_api_key"):
                map_payload["tavily_api_key"] = secrets["tavily_api_key"]
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
                err_low = str(exc).lower()
                if map_payload.get("web_search") and any(
                    tok in err_low for tok in ("tavily_api_key", "brave_search_api_key", "web search requires")
                ):
                    log.warning("Web search key missing on server; retrying '%s' without web_search.", topic)
                    map_payload.pop("web_search", None)
                    map_payload.pop("max_search_requests", None)
                    map_payload.pop("tavily_api_key", None)
                    try:
                        map_resp = self.client.generate_map(map_payload)
                        model = cand_model
                        break
                    except AutoMapClientError as retry_exc:
                        last_map_err = retry_exc
                        exc = retry_exc
                if _is_retryable_model_error(exc):
                    log.warning("Model %s unavailable during generate_map (%s), trying fallback...", cand_model, exc)
                    continue
                raise
        if map_resp is None and last_map_err is not None:
            raise last_map_err
        assert map_resp is not None
        map_data_url = str(map_resp["image_url"])
        map_values = map_resp.get("map_values")
        if not isinstance(map_values, dict):
            map_values = {}

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
                clean_payload.pop("web_search", None)
                clean_payload.pop("max_search_requests", None)
                clean_payload.pop("tavily_api_key", None)
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
