import threading
import time
from pathlib import Path
from uuid import uuid4
from datetime import datetime


class AgentRuntime:
    """Runtime orchestrator: routing, model calls, memory updates, heartbeat, and input loop."""

    def __init__(self, client, memory_manager, deep_search_agent, fsm_agent, personalization: dict):
        self.client = client
        self.memory_manager = memory_manager
        self.deep_search_agent = deep_search_agent
        self.fsm_agent = fsm_agent
        self.personalization = personalization
        self.session_histories = {}
        self.session_conversations = {}
        self.heartbeat_log_path = Path(__file__).resolve().parent / "heartbeat.log"

    @staticmethod
    def _debug(stage: str, detail: str = ""):
        now = datetime.now().strftime("%H:%M:%S")
        suffix = f" | {detail}" if detail else ""
        print(f"[DEBUG] {now} {stage}{suffix}")

    @staticmethod
    def _new_conversation_id() -> str:
        return f"conv-{uuid4().hex[:10]}"

    def _resolve_conversation_id(self, session_id: str, requested_conversation_id: str = "") -> str:
        conversation_id = (requested_conversation_id or "").strip()
        if conversation_id:
            self.session_conversations[session_id] = conversation_id
            return conversation_id
        if session_id not in self.session_conversations:
            self.session_conversations[session_id] = self._new_conversation_id()
        return self.session_conversations[session_id]

    def _reset_conversation(self, session_id: str) -> str:
        new_id = self._new_conversation_id()
        self.session_conversations[session_id] = new_id
        self.session_histories[new_id] = []
        return new_id

    def _get_or_create_history(self, conversation_id: str):
        if conversation_id not in self.session_histories:
            self.session_histories[conversation_id] = []
        return self.session_histories[conversation_id]

    def _normalize_input(self, payload):
        if isinstance(payload, dict):
            session_id = (payload.get("session_id") or "").strip() or f"session-{uuid4().hex[:8]}"
            user_input = str(payload.get("text", "")).strip()
            metadata = payload
            user_scope_id = str(payload.get("user_scope_id") or session_id).strip() or session_id
            requested_conversation_id = str(payload.get("conversation_id") or "").strip()
        else:
            session_id = "local-cli"
            user_input = str(payload).strip()
            metadata = {"source": "cli", "session_id": session_id, "text": user_input}
            user_scope_id = session_id
            requested_conversation_id = ""

        conversation_id = self._resolve_conversation_id(session_id, requested_conversation_id)
        return session_id, conversation_id, user_scope_id, user_input, metadata

    def _call_model(self, conversation_id: str, user_scope_id: str, user_input: str):
        self._debug("model_start", f"conversation={conversation_id}")
        chat_history = self._get_or_create_history(conversation_id)
        messages = [{"role": "system", "content": self.memory_manager.build_system_context(user_scope_id=user_scope_id)}]
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
        self._debug("model_end", f"conversation={conversation_id} len={len(content)}")
        return content

    def _route_task(self, user_input: str) -> str:
        self._debug("router_start")
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
        route = "COMPLEX" if "COMPLEX" in result else "SIMPLE"
        self._debug("router_end", f"route={route}")
        return route

    def _start_heartbeat(self, stop_event: threading.Event):
        while not stop_event.is_set():
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            with open(self.heartbeat_log_path, "a", encoding="utf-8") as f:
                f.write(f"[heartbeat] main online @ {timestamp}\n")
            stop_event.wait(20)

    def handle_input(self, payload) -> dict:
        session_id, conversation_id, user_scope_id, user_input, metadata = self._normalize_input(payload)
        if not user_input:
            return {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "user_scope_id": user_scope_id,
                "text": "Empty input.",
                "metadata": metadata,
            }

        if user_input == "/reset":
            new_conversation_id = self._reset_conversation(session_id)
            return {
                "session_id": session_id,
                "conversation_id": new_conversation_id,
                "user_scope_id": user_scope_id,
                "text": f"会话已重置，新短期会话ID: {new_conversation_id}",
                "metadata": metadata,
            }

        self._get_or_create_history(conversation_id)
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
                result = self._call_model(conversation_id, user_scope_id, user_input)

        self._debug("memory_start", f"user_scope={user_scope_id}")
        self.memory_manager.update_memory(user_input, result, user_scope_id=user_scope_id)
        self._debug("memory_end", f"user_scope={user_scope_id}")
        self._debug("soul_start", f"user_scope={user_scope_id}")
        self.memory_manager.maybe_update_soul(user_scope_id=user_scope_id)
        self._debug("soul_end", f"user_scope={user_scope_id}")
        self._debug("compress_start", f"conversation={conversation_id}")
        chat_history = self._get_or_create_history(conversation_id)
        compacted_history = self.memory_manager.compact_history_if_needed(
            chat_history,
            max_chars=256000,
            user_scope_id=user_scope_id,
        )
        if compacted_history is not chat_history:
            new_conversation_id = self._new_conversation_id()
            self.session_conversations[session_id] = new_conversation_id
            self.session_histories[new_conversation_id] = compacted_history
            self.session_histories.pop(conversation_id, None)
            conversation_id = new_conversation_id
        else:
            self.session_histories[conversation_id] = compacted_history

        self._debug(
            "compress_end",
            f"conversation={conversation_id} size={len(self.session_histories[conversation_id])}",
        )
        return {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "user_scope_id": user_scope_id,
            "text": result,
            "metadata": metadata,
        }

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
