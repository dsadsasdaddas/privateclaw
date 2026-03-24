import threading
import time
from pathlib import Path


class AgentRuntime:
    """Runtime orchestrator: routing, model calls, memory updates, heartbeat, and input loop."""

    def __init__(self, client, memory_manager, deep_search_agent, fsm_agent, personalization: dict):
        self.client = client
        self.memory_manager = memory_manager
        self.deep_search_agent = deep_search_agent
        self.fsm_agent = fsm_agent
        self.personalization = personalization
        self.chat_history = []
        self.heartbeat_log_path = Path(__file__).resolve().parent / "heartbeat.log"

    def _call_model(self, user_input: str):
        messages = [{"role": "system", "content": self.memory_manager.build_system_context()}]
        messages.extend(self.chat_history)
        messages.append({"role": "user", "content": user_input})

        response = self.client.chat.completions.create(
            model=self.personalization["models"]["chat"],
            messages=messages,
            stream=False,
        )
        message = response.choices[0].message
        content = message.content or ""

        self.chat_history.append({"role": "user", "content": user_input})
        self.chat_history.append({"role": "assistant", "content": content})
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

    def handle_input(self, user_input: str) -> str:
        if self.personalization["deepsearch_trigger_keyword"] in user_input:
            query = user_input.replace(self.personalization["deepsearch_trigger_keyword"], "", 1).strip()
            if not query:
                query = user_input
            result = self.deep_search_agent.run(query)
        else:
            task_type = self._route_task(user_input)
            if task_type == "COMPLEX":
                result = self.fsm_agent.run(user_input)
            else:
                result = self._call_model(user_input)

        self.memory_manager.update_memory(user_input, result)
        self.memory_manager.maybe_update_soul()
        self.chat_history = self.memory_manager.compact_history_if_needed(self.chat_history)
        return result

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
                print(f"output:{result}")
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=1)
