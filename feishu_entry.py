import json
import os
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
        self.pending_approvals = {}
        self.session_context = {}
        self.runtime.fsm_agent.approval_handler = self.request_approval
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

    def _send_text_by_session(self, session_id: str, text: str) -> None:
        ctx = self.session_context.get(session_id)
        if not ctx:
            return

        content = json.dumps({"text": text})
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(ctx["chat_id"])
                .msg_type("text")
                .content(content)
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"send approval prompt failed: {resp.code}, {resp.msg}, {resp.get_log_id()}")

    def request_approval(self, session_id: str, prompt: str, timeout_seconds: int = 180) -> bool:
        evt = threading.Event()
        self.pending_approvals[session_id] = {"event": evt, "answer": None}
        self._send_text_by_session(session_id, f"{prompt}\n（请在飞书回复 yes 或 no）")

        evt.wait(timeout_seconds)
        result = self.pending_approvals.get(session_id, {}).get("answer")
        self.pending_approvals.pop(session_id, None)
        return bool(result)

    def _try_handle_approval_reply(self, payload: dict) -> bool:
        session_id = payload["session_id"]
        pending = self.pending_approvals.get(session_id)
        if not pending:
            return False

        text = payload["text"].strip().lower()
        if text in {"yes", "y", "同意", "确认"}:
            pending["answer"] = True
            pending["event"].set()
            self._send_text_by_session(session_id, "已收到审批：同意。")
            return True
        if text in {"no", "n", "拒绝"}:
            pending["answer"] = False
            pending["event"].set()
            self._send_text_by_session(session_id, "已收到审批：拒绝。")
            return True
        self._send_text_by_session(session_id, "审批中：请回复 yes 或 no。")
        return True

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        payload = runtime_payload_from_feishu(data)
        self.session_context[payload["session_id"]] = {
            "chat_id": payload["chat_id"],
            "chat_type": payload["chat_type"],
            "message_id": payload["message_id"],
        }

        if self._try_handle_approval_reply(payload):
            return

        if not payload["text"]:
            self._send_text(data, "parse message failed, please send text message")
            return

        result = self.runtime.handle_input(payload)
        self._send_text(data, result["text"])

    def run(self) -> None:
        self.ws_client.start()
