import json
import os
import time
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from channel_layer import runtime_payload_from_feishu


class FeishuEntry:
    """Feishu long-connection entry. It cleans incoming messages and forwards them to AgentRuntime."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.app_id = os.getenv("LARK_APP_ID", "").strip()
        self.app_secret = os.getenv("LARK_APP_SECRET", "").strip()
        if not self.app_id or not self.app_secret:
            raise RuntimeError("Missing LARK_APP_ID/LARK_APP_SECRET for feishu message entry.")

        self.client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()
        self._dedup_ttl_seconds = int(os.getenv("FEISHU_DEDUP_TTL_SECONDS", "60"))
        self._recent_event_keys = {}
        self._dedup_lock = threading.Lock()
        self.event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )
        self.ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=self.event_handler,
            log_level=lark.LogLevel.DEBUG,
        )

    def _send_text(self, data: P2ImMessageReceiveV1, text: str) -> None:
        content = json.dumps({"text": text})
        message = data.event.message

        if message.chat_type == "p2p":
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(message.chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            resp = self.client.im.v1.message.create(request)
            if not resp.success():
                raise RuntimeError(f"send p2p failed: {resp.code}, {resp.msg}, {resp.get_log_id()}")
            return

        request = (
            ReplyMessageRequest.builder()
            .message_id(message.message_id)
            .request_body(
                ReplyMessageRequestBody.builder().content(content).msg_type("text").build()
            )
            .build()
        )
        resp = self.client.im.v1.message.reply(request)
        if not resp.success():
            raise RuntimeError(f"reply group failed: {resp.code}, {resp.msg}, {resp.get_log_id()}")

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        payload = runtime_payload_from_feishu(data)
        dedup_key = self._build_dedup_key(data, payload)
        if self._is_duplicate_event(dedup_key):
            print(f"[DEBUG] skip duplicated feishu event: {dedup_key}")
            return
        if not payload["text"]:
            self._send_text(data, "parse message failed, please send text message")
            return

        result = self.runtime.handle_input(payload)
        self._send_text(data, result["text"])

    def _build_dedup_key(self, data: P2ImMessageReceiveV1, payload: dict) -> str:
        event_id = ""
        try:
            event_id = (data.header.event_id or "").strip()
        except Exception:
            event_id = ""
        message_id = str(payload.get("message_id", "")).strip()
        if event_id:
            return f"event_id:{event_id}"
        if message_id:
            return f"message_id:{message_id}"
        return f"session_text:{payload.get('session_id', '')}:{payload.get('text', '')}"

    def _is_duplicate_event(self, dedup_key: str) -> bool:
        now = time.time()
        with self._dedup_lock:
            # clean expired keys first
            expired_keys = [
                key for key, expires_at in self._recent_event_keys.items()
                if expires_at <= now
            ]
            for key in expired_keys:
                self._recent_event_keys.pop(key, None)

            if dedup_key in self._recent_event_keys:
                return True
            self._recent_event_keys[dedup_key] = now + self._dedup_ttl_seconds
            return False

    def run(self) -> None:
        self.ws_client.start()
