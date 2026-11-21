"""Lightweight Telegram Bot API client for 1-click Short approvals and competitor music suggestions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests


class TelegramBot:
    """Minimal Telegram Bot API wrapper restricted to a single authorized chat ID."""

    def __init__(
        self,
        *,
        token: str | None = None,
        chat_id: str | None = None,
        http: requests.Session | None = None,
    ) -> None:
        self.token = (token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = str(chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        self.http = http or requests.Session()
        self._offset = 0
        self._buffered_messages: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        """Return True when both bot token and target chat ID are configured."""
        return bool(self.token and self.chat_id)

    @property
    def _base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def send_message(self, text: str, *, reply_markup: dict[str, Any] | None = None) -> int | None:
        """Send a plain-text message with optional inline keyboard."""
        if not self.enabled:
            return None
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": text[:4000]}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        resp = self.http.post(f"{self._base}/sendMessage", json=payload, timeout=20)
        if not resp.ok:
            return None
        return (resp.json().get("result") or {}).get("message_id")

    def send_approval_video(
        self,
        *,
        exp_id: int,
        mp4_path: Path,
        topic: str,
        arm: str,
        rationale: str,
        scheduled_label: str,
        media_note: str,
    ) -> int | None:
        """Send rendered Short MP4 with 1-click approval buttons to Telegram."""
        if not self.enabled or not mp4_path.is_file():
            return None
        caption = (
            f"🎬 Short #{exp_id}\n"
            f"Topic: {topic}\n"
            f"Effects: {arm}\n"
            f"Media: {media_note}\n"
            f"Why: {rationale}\n"
            f"Slot: {scheduled_label}"
        )[:1000]
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Publish Now", "callback_data": f"exp:publish:{exp_id}"},
                    {"text": f"🕒 Schedule ({scheduled_label})", "callback_data": f"exp:schedule:{exp_id}"},
                ],
                [
                    {"text": "🔒 Upload Private", "callback_data": f"exp:private:{exp_id}"},
                    {"text": "🔄 Regenerate", "callback_data": f"exp:regenerate:{exp_id}"},
                ],
                [
                    {"text": "❌ Reject", "callback_data": f"exp:reject:{exp_id}"},
                ],
            ]
        }
        with mp4_path.open("rb") as fh:
            resp = self.http.post(
                f"{self._base}/sendVideo",
                data={
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "reply_markup": json.dumps(keyboard),
                },
                files={"video": (mp4_path.name, fh, "video/mp4")},
                timeout=120,
            )
        if not resp.ok:
            return None
        return (resp.json().get("result") or {}).get("message_id")

    def send_music_suggestion(self, competitor: dict[str, Any]) -> int | None:
        """Suggest importing audio from a viral competitor Short with a 1-click button."""
        if not self.enabled:
            return None
        comp_id = int(competitor["id"])
        video_id = str(competitor["video_id"])
        ratio = competitor.get("outlier_ratio", 1.0)
        views = int(competitor.get("views") or 0)
        hint = str(competitor.get("music_hint") or competitor.get("title") or "")
        text = (
            f"🎵 Music suggestion from a viral competitor video ({ratio}x, {views:,} views):\n"
            f"Channel: {competitor.get('channel_title') or competitor.get('channel_id')}\n"
            f"Track: {hint}\n"
            f"https://youtube.com/shorts/{video_id}"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🎵 Add Track to Pool", "callback_data": f"music:import:{comp_id}"},
                    {"text": "Skip", "callback_data": f"music:skip:{comp_id}"},
                ]
            ]
        }
        return self.send_message(text, reply_markup=keyboard)

    def poll_updates(self, *, timeout: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Poll pending inline button callbacks and text messages from the authorized chat only."""
        buffered = list(self._buffered_messages)
        self._buffered_messages.clear()
        if not self.enabled:
            return [], buffered
        try:
            resp = self.http.get(
                f"{self._base}/getUpdates",
                params={
                    "offset": self._offset,
                    "timeout": timeout,
                    "allowed_updates": '["callback_query","message"]',
                },
                timeout=timeout + 10,
            )
        except requests.RequestException:
            return [], buffered
        if not resp.ok:
            return [], buffered
        updates = resp.json().get("result") or []
        callbacks: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = buffered
        for upd in updates:
            self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
            cq = upd.get("callback_query")
            if cq:
                msg = cq.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id") or "")
                if chat_id != self.chat_id:
                    continue
                callbacks.append(
                    {
                        "callback_id": str(cq.get("id") or ""),
                        "data": str(cq.get("data") or ""),
                        "message_id": msg.get("message_id"),
                    }
                )
                continue
            msg = upd.get("message")
            if msg:
                chat_id = str((msg.get("chat") or {}).get("id") or "")
                text = str(msg.get("text") or "").strip()
                if chat_id != self.chat_id or not text:
                    continue
                messages.append(
                    {
                        "message_id": msg.get("message_id"),
                        "text": text,
                    }
                )
        return callbacks, messages

    def poll_callbacks(self, *, timeout: int = 1) -> list[dict[str, Any]]:
        """Poll pending inline button callbacks from the authorized chat only."""
        callbacks, messages = self.poll_updates(timeout=timeout)
        if messages:
            self._buffered_messages.extend(messages)
        return callbacks

    def answer_callback(self, callback_id: str, text: str) -> None:
        """Acknowledge an inline button tap in Telegram."""
        if not self.enabled or not callback_id:
            return
        try:
            self.http.post(
                f"{self._base}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text[:180]},
                timeout=10,
            )
        except requests.RequestException:
            pass
