import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4
from typing import Optional

from channel_layer import RuntimeMessage


@dataclass
class LoopDecision:
    kind: str  # "answer" | "tool_calls" | "need_approval"
    answer: str = ""
    tool_calls: Optional[list] = None
    approval_request: Optional[dict] = None


class AgentLoop:
    """Plan / Execute / Observe：planner 只决策，executor 只执行。"""

    def __init__(self, client, memory_manager, deep_search_agent, fsm_agent, personalization: dict):
        self.client = client
        self.memory_manager = memory_manager
        self.deep_search_agent = deep_search_agent
        self.fsm_agent = fsm_agent
        self.personalization = personalization
        self.session_histories = {}
        self.session_conversations = {}

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

    def _plan(self, user_scope_id: str, history: list[dict]) -> LoopDecision:
        self._debug("plan_start")
        response = self.client.chat.completions.create(
            model=self.personalization["models"]["fsm"],
            messages=[
                {
                    "role": "system",
                    "content": "你是 Planner。优先直接回答；需要工具时发起 tool_calls；危险工具先请求审批。",
                },
                {"role": "system", "content": self.memory_manager.build_system_context(user_scope_id=user_scope_id)},
                *history,
            ],
            tools=self.fsm_agent.tool_config,
            stream=False,
        )
        message = response.choices[0].message
        msg_dict = message.model_dump(exclude_none=True)
        if msg_dict.get("content") is None:
            msg_dict["content"] = ""
        history.append(msg_dict)

        if message.tool_calls:
            tool_calls = [
                {
                    "id": t.id,
                    "name": t.function.name,
                    "arguments": t.function.arguments,
                }
                for t in message.tool_calls
            ]
            if self._needs_approval(tool_calls):
                return LoopDecision(
                    kind="need_approval",
                    approval_request={"reason": "sensitive_tool", "tool_calls": tool_calls},
                )
            return LoopDecision(kind="tool_calls", tool_calls=tool_calls)

        self._debug("plan_end", "answer")
        return LoopDecision(kind="answer", answer=(message.content or "").strip())

    @staticmethod
    def _needs_approval(tool_calls: list[dict]) -> bool:
        sensitive_keywords = ("delete", "remove", "exec", "shell", "write", "drop")
        for call in tool_calls:
            name = (call.get("name") or "").lower()
            if any(word in name for word in sensitive_keywords):
                return True
        return False

    @staticmethod
    def _request_approval(_msg: RuntimeMessage, approval_request: dict) -> str:
        tool_names = [c.get("name", "") for c in (approval_request or {}).get("tool_calls", [])]
        return f"审批结果：自动批准。tools={','.join(tool_names)}"

    def _execute(self, tool_calls: list[dict]) -> list[dict]:
        self._debug("execute_start", f"count={len(tool_calls)}")
        tool_results = []
        for idx, tool_call in enumerate(tool_calls, start=1):
            func_name = tool_call.get("name", "")
            func_args_str = tool_call.get("arguments", "{}")
            call_id = tool_call.get("id", f"tool-{idx}")

            result = f"error: tool '{func_name}' not found."
            if func_name in self.fsm_agent.available_tools:
                func = self.fsm_agent.available_tools.get(func_name)
                try:
                    json_args = json.loads(func_args_str)
                    result = func(**json_args)
                except json.JSONDecodeError as e:
                    result = f"Tool arguments JSON decode error: {str(e)}"
                except Exception as e:
                    result = f"Error executing tool '{func_name}': {str(e)}"

            tool_results.append(
                {
                    "role": "tool",
                    "content": str(result),
                    "tool_call_id": call_id,
                    "name": func_name,
                }
            )

        self._debug("execute_end")
        return tool_results

    def run(self, msg: RuntimeMessage) -> dict:
        session_id = msg.session_id
        user_input = (msg.text or "").strip()
        user_scope_id = (msg.user_scope_id or session_id).strip() or session_id
        conversation_id = self._resolve_conversation_id(session_id, msg.conversation_id)

        if not user_input:
            return {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "user_scope_id": user_scope_id,
                "text": "Empty input.",
            }

        if user_input == "/reset":
            new_conversation_id = self._reset_conversation(session_id)
            return {
                "session_id": session_id,
                "conversation_id": new_conversation_id,
                "user_scope_id": user_scope_id,
                "text": f"会话已重置，新短期会话ID: {new_conversation_id}",
            }

        if self.personalization["deepsearch_trigger_keyword"] in user_input:
            query = user_input.replace(self.personalization["deepsearch_trigger_keyword"], "", 1).strip() or user_input
            result_text = self.deep_search_agent.run(query)
            self.memory_manager.update_memory(user_input, result_text, user_scope_id=user_scope_id)
            return {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "user_scope_id": user_scope_id,
                "text": result_text,
            }

        history = self._get_or_create_history(conversation_id)
        history.append({"role": "user", "content": user_input})

        state = "PLANNING"
        pending_tool_calls = []
        final_answer = ""
        for _ in range(8):
            if state == "PLANNING":
                decision = self._plan(user_scope_id=user_scope_id, history=history)
                if decision.kind == "answer":
                    final_answer = decision.answer or ""
                    break
                if decision.kind == "tool_calls":
                    pending_tool_calls = decision.tool_calls or []
                    state = "EXECUTING"
                    continue
                if decision.kind == "need_approval":
                    approval_result = self._request_approval(msg, decision.approval_request or {})
                    history.append({"role": "system", "content": approval_result})
                    state = "OBSERVING"
                    continue

            if state == "EXECUTING":
                tool_results = self._execute(pending_tool_calls)
                history.extend(tool_results)
                state = "OBSERVING"
                continue

            if state == "OBSERVING":
                state = "PLANNING"
                continue
        else:
            final_answer = "处理超出最大轮次，请简化问题后重试。"

        if final_answer:
            history.append({"role": "assistant", "content": final_answer})

        self.memory_manager.update_memory(user_input, final_answer, user_scope_id=user_scope_id)
        self.memory_manager.maybe_update_soul(user_scope_id=user_scope_id)

        compacted_history = self.memory_manager.compact_history_if_needed(
            history,
            max_chars=256000,
            user_scope_id=user_scope_id,
        )
        if compacted_history is not history:
            new_conversation_id = self._new_conversation_id()
            self.session_conversations[session_id] = new_conversation_id
            self.session_histories[new_conversation_id] = compacted_history
            self.session_histories.pop(conversation_id, None)
            conversation_id = new_conversation_id
        else:
            self.session_histories[conversation_id] = compacted_history

        return {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "user_scope_id": user_scope_id,
            "text": final_answer,
        }
