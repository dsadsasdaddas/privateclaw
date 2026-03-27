import json
from dataclasses import dataclass


@dataclass
class RuntimeMessage:
    session_id: str
    text: str
    source: str
    chat_type: str
    chat_id: str
    message_id: str
    user_scope_id: str
    conversation_id: str = ""


def _safe_get_sender_open_id(data) -> str:
    try:
        return data.event.sender.sender_id.open_id or "unknown"
    except Exception:
        return "unknown"


def normalize_feishu_event(data) -> RuntimeMessage:
    message = data.event.message
    if message.message_type == "text":
        text = json.loads(message.content).get("text", "").strip()
    else:
        text = ""

    sender_open_id = _safe_get_sender_open_id(data)
    if message.chat_type == "p2p":
        session_id = f"feishu:p2p:{message.chat_id}"
    else:
        # Group session is split by chat + sender, so each user keeps an isolated task dialogue.
        session_id = f"feishu:group:{message.chat_id}:{sender_open_id}"

    return RuntimeMessage(
        session_id=session_id,
        text=text,
        source="feishu",
        chat_type=message.chat_type,
        chat_id=message.chat_id,
        message_id=message.message_id,
        user_scope_id=session_id,
    )


def runtime_payload_from_feishu(data) -> dict:
    normalized = normalize_feishu_event(data)
    return {
        "session_id": normalized.session_id,
        "text": normalized.text,
        "source": normalized.source,
        "chat_type": normalized.chat_type,
        "chat_id": normalized.chat_id,
        "message_id": normalized.message_id,
        "user_scope_id": normalized.user_scope_id,
        "conversation_id": normalized.conversation_id,
    }
