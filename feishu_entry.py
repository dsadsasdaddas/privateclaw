import json
import os
import queue
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

from channel_layer import normalize_feishu_event


class FeishuEntry:
    """Channel 适配层：收飞书消息、去重、标准化、入队、发回复。"""

    def __init__(self, runtime):
        self.runtime = runtime
        self.app_id = os.getenv("LARK_APP_ID", "").strip()
        self.app_secret = os.getenv("LARK_APP_SECRET", "").strip()
        if not self.app_id or not self.app_secret:
            raise RuntimeError("Missing LARK_APP_ID/LARK_APP_SECRET for feishu message entry.")

        self.client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()
        self._dedup_ttl_seconds = int(os.getenv("FEISHU_DEDUP_TTL_SECONDS", "7200"))
        self._recent_event_keys = {}
        self._dedup_lock = threading.Lock()

        # 队列只负责串行处理消息：上一条完成后再处理下一条。
        self._message_queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

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

    def send_reply(self, data: P2ImMessageReceiveV1, text: str) -> None:
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
        msg = normalize_feishu_event(data)
        dedup_key = self._build_dedup_key(data, msg.message_id, msg.session_id, msg.text)

        if self._is_duplicate_event(dedup_key):
            print(f"[DEBUG] skip duplicated feishu event: {dedup_key}")
            return

        if not msg.text:
            self._message_queue.put(("reply_text", data, "parse message failed, please send text message"))
            return

        self._message_queue.put(("handle_runtime", data, msg))

    def _worker_loop(self) -> None:
        while True:
            task_type, data, content = self._message_queue.get()
            try:
                if task_type == "reply_text":
                    self.send_reply(data, content)
                elif task_type == "handle_runtime":
                    self.runtime.process_channel_message(self, data, content)
            except Exception as e:
                print(f"[ERROR] worker handle message failed: {e}")
                try:
                    self.send_reply(data, "处理消息时发生错误，请稍后重试。")
                except Exception as send_err:
                    print(f"[ERROR] send error message failed: {send_err}")
            finally:
                self._message_queue.task_done()

    def _build_dedup_key(self, data: P2ImMessageReceiveV1, message_id: str, session_id: str, text: str) -> str:
        event_id = ""
        try:
            event_id = (data.header.event_id or "").strip()
        except Exception:
            event_id = ""
        if event_id:
            return f"event_id:{event_id}"
        if message_id:
            return f"message_id:{message_id}"
        return f"session_text:{session_id}:{text}"

    def _is_duplicate_event(self, dedup_key: str) -> bool:
        now = time.time()
        with self._dedup_lock:
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
