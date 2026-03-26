import threading
import time
from pathlib import Path
from uuid import uuid4


class AgentRuntime:
    """Runtime orchestrator: routing, model calls, memory updates, heartbeat, and input loop."""

    def __init__(self, client, memory_manager, deep_search_agent, fsm_agent, personalization: dict):
        self.client = client
        self.memory_manager = memory_manager
        self.deep_search_agent = deep_search_agent
        self.fsm_agent = fsm_agent
        self.personalization = personalization
        self.session_histories = {}
        self.heartbeat_log_path = Path(__file__).resolve().parent / "heartbeat.log"

    def _get_or_create_history(self, session_id: str):
        if session_id not in self.session_histories:
            self.session_histories[session_id] = []
        return self.session_histories[session_id]

    def _normalize_input(self, payload):
        if isinstance(payload, dict):
            session_id = (payload.get("session_id") or "").strip() or f"session-{uuid4().hex[:8]}"
            user_input = str(payload.get("text", "")).strip()
            metadata = payload
        else:
            session_id = "local-cli"
            user_input = str(payload).strip()
            metadata = {"source": "cli", "session_id": session_id, "text": user_input}
        return session_id, user_input, metadata

    def _call_model(self, session_id: str, user_input: str):
        chat_history = self._get_or_create_history(session_id)
        messages = [{"role": "system", "content": self.memory_manager.build_system_context()}]
        messages.extend(chat_history)
        messages.append({"role": "user", "content": user_input})

        response = self.client.chat.completions.create(
            model=self.personalization["models"]["chat"],
            messages=messages,
            stream=False,
        )
        message = response.choices[0].message
        content = message.content or ""

        chat_history.append({"role": "user", "content": user_input})
        chat_history.append({"role": "assistant", "content": content})
        return content

    def _route_task(self, user_input: str) -> str:
        response = self.client.chat.completions.create(
            model=self.personalization["models"]["router"],
            messages=[
                {
                    "role": "system",
                    "content": "你是一个严格的意图分类器。如果用户输入需要联网、工具调用、代码执行或复杂任务，回复COMPLEX；日常闲聊回复SIMPLE。仅输出一个词。",
                },
                {"role": "user", "content": user_input},
            ],
            temperature=0.0,
            stream=False,
        )
        result = (response.choices[0].message.content or "").strip().upper()
        return "COMPLEX" if "COMPLEX" in result else "SIMPLE"

    def _start_heartbeat(self, stop_event: threading.Event):
        while not stop_event.is_set():
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            with open(self.heartbeat_log_path, "a", encoding="utf-8") as f:
                f.write(f"[heartbeat] main online @ {timestamp}\n")
            stop_event.wait(20)

    def handle_input(self, payload) -> dict:
        session_id, user_input, metadata = self._normalize_input(payload)
        if not user_input:
            return {"session_id": session_id, "text": "Empty input.", "metadata": metadata}

        self._get_or_create_history(session_id)
        if self.personalization["deepsearch_trigger_keyword"] in user_input:
            query = user_input.replace(self.personalization["deepsearch_trigger_keyword"], "", 1).strip()
            if not query:
                query = user_input
            result = self.deep_search_agent.run(query)
        else:
            task_type = self._route_task(user_input)
            if task_type == "COMPLEX":
                result = self.fsm_agent.run(user_input, session_id=session_id)
            else:
                result = self._call_model(session_id, user_input)

        self.memory_manager.update_memory(user_input, result)
        self.memory_manager.maybe_update_soul()
        chat_history = self._get_or_create_history(session_id)
        self.session_histories[session_id] = self.memory_manager.compact_history_if_needed(chat_history)
        return {"session_id": session_id, "text": result, "metadata": metadata}

    def run(self):
        stop_event = threading.Event()
        heartbeat_thread = threading.Thread(target=self._start_heartbeat, args=(stop_event,), daemon=True)
        heartbeat_thread.start()

        try:
            while True:
                user_input = input("User:")
                if user_input == "quit":
                    break
                result = self.handle_input(user_input)
                print(f"output:{result['text']}")
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=1)
