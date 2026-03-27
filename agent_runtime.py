from channel_layer import RuntimeMessage


class AgentRuntime:
    """Runtime 只做编排。"""

    def __init__(self, agent_loop):
        self.agent_loop = agent_loop

    def handle_message(self, msg: RuntimeMessage) -> dict:
        return self.agent_loop.run(msg)

    def handle_input(self, payload) -> dict:
        if isinstance(payload, RuntimeMessage):
            return self.handle_message(payload)

        if isinstance(payload, dict):
            msg = RuntimeMessage(
                session_id=str(payload.get("session_id", "")).strip() or "local-cli",
                text=str(payload.get("text", "")),
                source=str(payload.get("source", "cli")),
                chat_type=str(payload.get("chat_type", "cli")),
                chat_id=str(payload.get("chat_id", "local")),
                message_id=str(payload.get("message_id", "")),
                user_scope_id=str(payload.get("user_scope_id") or payload.get("session_id") or "local-cli"),
                conversation_id=str(payload.get("conversation_id", "")),
            )
            return self.handle_message(msg)

        msg = RuntimeMessage(
            session_id="local-cli",
            text=str(payload),
            source="cli",
            chat_type="cli",
            chat_id="local",
            message_id="",
            user_scope_id="local-cli",
        )
        return self.handle_message(msg)

    def process_channel_message(self, channel, data, msg: RuntimeMessage) -> dict:
        result = self.handle_message(msg)
        channel.send_reply(data, result["text"])
        return result

    def run(self):
        while True:
            user_input = input("User:")
            if user_input == "quit":
                break
            result = self.handle_input(user_input)
            print(f"output:{result['text']}")
