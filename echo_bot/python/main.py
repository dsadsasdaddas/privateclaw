import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    ReplyMessageResponse,
)

from config import load_lark_config


config = load_lark_config()

# Create Lark client for OpenAPI requests.
client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()


# Register message receive event handler.
# https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    if data.event.message.message_type == "text":
        res_content = json.loads(data.event.message.content).get("text", "")
    else:
        res_content = "parse message failed, please send text message"

    content = json.dumps({"text": f"Received message: {res_content}"})

    # p2p: send message back to peer chat
    if data.event.message.chat_type == "p2p":
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(data.event.message.chat_id)
                .msg_type("text")
                .content(content)
                .build()
            )
            .build()
        )
        response = client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(
                f"client.im.v1.message.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}"
            )
    else:
        # group: reply to mentioned message
        request: ReplyMessageRequest = (
            ReplyMessageRequest.builder()
            .message_id(data.event.message.message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(content)
                .msg_type("text")
                .build()
            )
            .build()
        )
        response: ReplyMessageResponse = client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(
                f"client.im.v1.message.reply failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}"
            )


# Register event dispatcher.
event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
    .build()
)


# Create websocket long-connection client.
ws_client = lark.ws.Client(
    config.app_id,
    config.app_secret,
    event_handler=event_handler,
    log_level=lark.LogLevel.DEBUG,
)


def main():
    # Start long connection and subscribe events.
    ws_client.start()


if __name__ == "__main__":
    main()
