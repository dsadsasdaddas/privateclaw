import json
import os

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
        if not payload["text"]:
            self._send_text(data, "parse message failed, please send text message")
            return

        result = self.runtime.handle_input(payload)
        self._send_text(data, result["text"])

    def run(self) -> None:
        self.ws_client.start()
