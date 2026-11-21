"""Run the agent's scheduling, command, and approval orchestration loop.

Start with ``python -m agent.runner``. The runner constructs database, media,
AutoMap, YouTube, and Telegram clients, reloads saved credentials, and polls
queued web commands and authorized Telegram updates. Daily sync and posting
slots use configured timezone/hours; slot history is persisted in AgentDB.
Candidate rendering delegates to ``video_generation.py``. Publishing claims an
experiment before upload to prevent duplicate approval side effects. The loop
also updates heartbeat state; AGENT_ENABLED controls whether it starts work."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .automap_client import AutoMapClient, AutoMapClientError
from .db import AgentDB, utc_now_iso
from .media_pool import MediaPool
from .optimizer import AgentOptimizer, render_video_description
from .telegram_bot import TelegramBot
from .youtube_intel import YouTubeIntel
from .video_generation import CandidateVideoGenerator

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
        self._triggered_slots: set[str] = set()
        if isinstance(raw_slots, list):
            for raw_slot in raw_slots:
                slot = str(raw_slot).strip()
                if slot:
                    self._triggered_slots.add(slot)
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
        """Render a candidate using the runner's current clients and credentials."""
        generator = CandidateVideoGenerator(
            db=self.db,
            client=self.client,
            bot=self.bot,
            media_pool=self.media_pool,
            optimizer=self.optimizer,
            renders_dir=self.renders_dir,
        )
        return generator.generate(
            posting_hour=posting_hour,
            scheduled_iso=scheduled_iso,
            forced_topic=forced_topic,
        )

    def handle_experiment_action(self, exp_id: int, action: str) -> str:
        """Apply an approval decision once and persist the resulting experiment state.

        Reject/regenerate are local workflow decisions; publish/schedule/private upload
        the recorded MP4 through YouTubeIntel. Claiming the row precedes these external
        side effects. Missing files and failed uploads mark the experiment failed."""
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
            publish_at = None
            if act == "schedule":
                publish_at = str(exp.get("scheduled_for") or "")
                now = datetime.now(timezone.utc)
                try:
                    slot = datetime.fromisoformat(publish_at or "")
                except ValueError:
                    slot = now
                if slot.tzinfo is None or slot <= now:
                    publish_at = (now + timedelta(minutes=30)).replace(microsecond=0).isoformat()
            cfg = self.db.get_config()
            exp_params = exp.get("params")
            if not isinstance(exp_params, dict):
                exp_params = {}
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
            upload_result = {
                "status": new_status,
                "requested_action": act,
                "requested_privacy": privacy,
                "youtube_video_id": vid_id,
                "youtube_url": vid_url,
                "published_at": utc_now_iso(),
                "error_message": "",
            }
            # Preserve the existing schedule unless this upload supplied a new one.
            if publish_at:
                upload_result["scheduled_for"] = publish_at
            self.db.update_experiment(exp_id, **upload_result)
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

    def _handle_telegram_message(self, text: str) -> None:
        """Handle a text idea or command sent directly in the authorized Telegram chat."""
        raw = str(text or "").strip()
        if not raw:
            return
        low = raw.lower()
        if low in ("/start", "/help", "help"):
            self.bot.send_message(
                "👋 Надішли мені будь-яку тему або ідею для карти (наприклад: "
                "`Average sleep hours in Europe`), і я згенерую з неї Shorts та надішлю на підтвердження.\n\n"
                "Команди:\n"
                "• /sync — оновити аналітику YouTube та конкурентів"
            )
            return
        if low == "/sync":
            self.bot.send_message("🔄 Оновлюю аналітику каналу та конкурентів...")
            try:
                res = self.run_daily_sync()
                self.bot.send_message(
                    f"✅ Синхронізація завершена: оновлено {res['analytics_updated']} експериментів, "
                    f"проскановано {res['competitor_updated']} відео конкурентів."
                )
            except Exception as exc:
                log.exception("Telegram /sync failed")
                self.bot.send_message(f"❌ Помилка синхронізації: {exc}")
            return

        for prefix in ("/idea ", "/topic "):
            if low.startswith(prefix):
                raw = raw[len(prefix) :].strip()
                break

        if raw.startswith("/"):
            self.bot.send_message("Невідома команда. Надішли просто текст ідеї або /help.")
            return

        idea = raw[:140].strip()
        if len(idea) < 3:
            self.bot.send_message("Напиши, будь ласка, трохи детальнішу ідею (мін. 3 символи).")
            return

        self.bot.send_message(f"🎬 Прийняв ідею: «{idea}»\nГенерую карту та рендерю Shorts...")
        try:
            self.generate_candidate_video(forced_topic=idea)
        except Exception as exc:
            log.exception("Telegram custom idea generation failed: %s", idea)
            self.bot.send_message(f"❌ Не вдалося згенерувати відео для «{idea}»: {exc}")

    def process_telegram_callbacks(self) -> None:
        """Handle inline button taps and text ideas from Telegram."""
        callbacks, messages = self.bot.poll_updates(timeout=1)
        for cb in callbacks:
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
        for msg_item in messages:
            self._handle_telegram_message(str(msg_item.get("text") or ""))

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
        """Run due daily synchronization and candidate-generation slots.

        Times are evaluated in the configured local timezone. Persist a slot claim
        before generation so another tick does not duplicate it; a claimed slot stays
        consumed even when the daily limit is reached or generation fails."""
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
