import json
from dataclasses import dataclass
from datetime import datetime
import time
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

    RUN_TIMEOUT_SECONDS = 60
    MAX_STALL_STEPS = 8
    MAX_SAME_TOOL_FAILURES = 3
    NON_RETRIABLE_ERROR_SIGNATURES = (
        "approval required",
        "allowlist miss",
        "permission denied",
        "权限缺失",
        "权限不足",
        "forbidden",
        "not authorized",
        "节点不在前台",
        "not in foreground",
    )

    def __init__(self, client, memory_manager, tool_config, available_tools, personalization: dict):
        self.client = client
        self.memory_manager = memory_manager
        self.tool_config = tool_config
        self.available_tools = available_tools
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


    @staticmethod
    def _build_tool_error_message(tool_call_id: str, name: str, reason: str) -> dict:
        return {
            "role": "tool",
            "content": f"tool call not completed: {reason}",
            "tool_call_id": tool_call_id,
            "name": name,
        }

    def _repair_history(self, history: list[dict]) -> list[dict]:
        """修复悬空 tool_calls，保证给模型和持久化前的历史结构合法。"""
        repaired = []
        i = 0
        while i < len(history):
            item = history[i]
            repaired.append(item)
            tool_calls = item.get("tool_calls") if isinstance(item, dict) else None
            if item.get("role") == "assistant" and tool_calls:
                required_ids = [tc.get("id") for tc in tool_calls if tc.get("id")]
                j = i + 1
                matched_ids = set()
                buffered_following = []

                while j < len(history):
                    nxt = history[j]
                    if isinstance(nxt, dict) and nxt.get("role") == "tool":
                        buffered_following.append(nxt)
                        tool_call_id = nxt.get("tool_call_id")
                        if tool_call_id in required_ids:
                            matched_ids.add(tool_call_id)
                        j += 1
                        continue
                    break

                repaired.extend(buffered_following)
                missing_ids = [tid for tid in required_ids if tid not in matched_ids]
                if missing_ids:
                    for tc in tool_calls:
                        tc_id = tc.get("id")
                        if tc_id in missing_ids:
                            name = ((tc.get("function") or {}).get("name") if isinstance(tc, dict) else "") or "unknown"
                            repaired.append(self._build_tool_error_message(tc_id, name, "missing tool response patched"))

                i = j
                continue
            i += 1
        return repaired

    def _plan(self, user_scope_id: str, history: list[dict]) -> LoopDecision:
        history[:] = self._repair_history(history)
        self._debug("plan_start")
        response = self.client.chat.completions.create(
            model=self.personalization["models"]["fsm"],
            messages=[
                {
                    "role": "system",
                    "content": "你是 Planner。优先直接回答；需要工具时发起 tool_calls；当问题需要多轮检索和网页阅读时优先调用 deep_search；危险工具先请求审批。",
                },
                {"role": "system", "content": self.memory_manager.build_system_context(user_scope_id=user_scope_id)},
                *history,
            ],
            tools=self.tool_config,
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

    @staticmethod
    def _tool_failure_signature(tool_name: str, args: str) -> str:
        return f"{tool_name}::{args}"

    @staticmethod
    def _is_tool_failure(text: str) -> bool:
        low = (text or "").lower()
        markers = (
            "error",
            "failed",
            "failure",
            "exception",
            "命令执行失败",
            "命令执行异常",
            "json decode error",
            "not found",
        )
        return any(m in low for m in markers)

    def _match_non_retriable_signature(self, text: str) -> str:
        low = (text or "").lower()
        for sig in self.NON_RETRIABLE_ERROR_SIGNATURES:
            if sig in low:
                return sig
        return ""

    def _execute(self, tool_calls: list[dict], failure_counts: dict[str, int]) -> tuple[list[dict], str]:
        self._debug("execute_start", f"count={len(tool_calls)}")
        tool_results = []
        hard_stop_reason = ""
        for idx, tool_call in enumerate(tool_calls, start=1):
            func_name = tool_call.get("name", "")
            func_args_str = tool_call.get("arguments", "{}")
            call_id = tool_call.get("id", f"tool-{idx}")
            sig = self._tool_failure_signature(func_name, func_args_str)

            result = f"error: tool '{func_name}' not found."
            if func_name in self.available_tools:
                func = self.available_tools.get(func_name)
                try:
                    json_args = json.loads(func_args_str)
                    result = func(**json_args)
                except json.JSONDecodeError as e:
                    result = f"Tool arguments JSON decode error: {str(e)}"
                except Exception as e:
                    result = f"Error executing tool '{func_name}': {str(e)}"

            result_text = str(result)
            non_retry_sig = self._match_non_retriable_signature(result_text)
            if non_retry_sig:
                hard_stop_reason = (
                    f"检测到不可重试错误签名: `{non_retry_sig}`。"
                    f"工具 `{func_name}` 返回：{result_text}"
                )

            if self._is_tool_failure(result_text):
                failure_counts[sig] = failure_counts.get(sig, 0) + 1
                if failure_counts[sig] >= self.MAX_SAME_TOOL_FAILURES and not hard_stop_reason:
                    hard_stop_reason = (
                        f"同一工具与参数连续失败已达 {self.MAX_SAME_TOOL_FAILURES} 次，"
                        f"停止重试。工具=`{func_name}` 参数=`{func_args_str}` 最近报错：{result_text}"
                    )
            else:
                failure_counts[sig] = 0

            tool_results.append(
                {
                    "role": "tool",
                    "content": result_text,
                    "tool_call_id": call_id,
                    "name": func_name,
                }
            )

            if hard_stop_reason:
                break

        self._debug("execute_end")
        return tool_results, hard_stop_reason

    def run(self, msg: RuntimeMessage) -> dict:
        run_id = f"run-{uuid4().hex[:8]}"
        session_id = msg.session_id
        user_input = (msg.text or "").strip()
        user_scope_id = (msg.user_scope_id or session_id).strip() or session_id
        conversation_id = self._resolve_conversation_id(session_id, msg.conversation_id)
        queue_wait_ms = max(0, int(time.time() * 1000) - int(getattr(msg, "enqueue_ts_ms", 0) or 0))
        llm_ms_total = 0
        tool_ms_total = 0
        memory_ms_total = 0

        if not user_input:
            self._debug(
                "run_metrics",
                f"run_id={run_id} session_id={session_id} conversation_id={conversation_id} "
                f"queue_wait_ms={queue_wait_ms} llm_ms={llm_ms_total} tool_ms={tool_ms_total} "
                f"memory_ms={memory_ms_total} dedup_key={getattr(msg, 'dedup_key', '')}",
            )
            return {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "user_scope_id": user_scope_id,
                "text": "Empty input.",
            }

        if user_input == "/reset":
            new_conversation_id = self._reset_conversation(session_id)
            self._debug(
                "run_metrics",
                f"run_id={run_id} session_id={session_id} conversation_id={new_conversation_id} "
                f"queue_wait_ms={queue_wait_ms} llm_ms={llm_ms_total} tool_ms={tool_ms_total} "
                f"memory_ms={memory_ms_total} dedup_key={getattr(msg, 'dedup_key', '')}",
            )
            return {
                "session_id": session_id,
                "conversation_id": new_conversation_id,
                "user_scope_id": user_scope_id,
                "text": f"会话已重置，新短期会话ID: {new_conversation_id}",
            }

        history = self._get_or_create_history(conversation_id)
        history.append({"role": "user", "content": user_input})

        state = "PLANNING"
        pending_tool_calls = []
        final_answer = ""
        loop_start = time.perf_counter()
        failure_counts: dict[str, int] = {}
        last_snapshot = ""
        stall_steps = 0
        for _ in range(64):
            if (time.perf_counter() - loop_start) > self.RUN_TIMEOUT_SECONDS:
                final_answer = "本轮处理超过 60 秒已强制结束，请你根据当前报错继续排障，我已把控制权交还给你。"
                break

            snapshot = f"{state}|{len(history)}|{len(pending_tool_calls)}|{final_answer}"
            if snapshot == last_snapshot:
                stall_steps += 1
            else:
                stall_steps = 0
                last_snapshot = snapshot
            if stall_steps >= self.MAX_STALL_STEPS:
                final_answer = "连续 8 步无状态变化，已终止本轮处理。请提供更具体输入或调整权限/参数后重试。"
                break

            if state == "PLANNING":
                t_llm_start = time.perf_counter()
                decision = self._plan(user_scope_id=user_scope_id, history=history)
                llm_ms_total += int((time.perf_counter() - t_llm_start) * 1000)
                if decision.kind == "answer":
                    final_answer = decision.answer or ""
                    break
                if decision.kind == "tool_calls":
                    pending_tool_calls = decision.tool_calls or []
                    state = "EXECUTING"
                    continue
                if decision.kind == "need_approval":
                    approval_result = self._request_approval(msg, decision.approval_request or {})
                    self._debug("approval", approval_result)
                    pending_tool_calls = (decision.approval_request or {}).get("tool_calls", [])
                    state = "EXECUTING"
                    continue

            if state == "EXECUTING":
                t_tool_start = time.perf_counter()
                tool_results, hard_stop_reason = self._execute(pending_tool_calls, failure_counts=failure_counts)
                tool_ms_total += int((time.perf_counter() - t_tool_start) * 1000)
                history.extend(tool_results)
                if hard_stop_reason:
                    final_answer = (
                        "工具执行已停止，原因如下：\n"
                        f"{hard_stop_reason}\n\n"
                        "这类错误通常不应继续自动重试，请你确认权限、allowlist、审批状态或前台节点状态后再继续。"
                    )
                    break
                state = "OBSERVING"
                continue

            if state == "OBSERVING":
                state = "PLANNING"
                continue
        else:
            final_answer = "处理超出最大轮次，请简化问题后重试。"

        if final_answer:
            history.append({"role": "assistant", "content": final_answer})

        history[:] = self._repair_history(history)

        t_memory_start = time.perf_counter()
        self.memory_manager.update_memory(user_input, final_answer, user_scope_id=user_scope_id)
        self.memory_manager.maybe_update_soul(user_scope_id=user_scope_id)

        compacted_history = self.memory_manager.compact_history_if_needed(
            history,
            max_chars=256000,
            user_scope_id=user_scope_id,
        )
        memory_ms_total += int((time.perf_counter() - t_memory_start) * 1000)
        if compacted_history is not history:
            new_conversation_id = self._new_conversation_id()
            self.session_conversations[session_id] = new_conversation_id
            self.session_histories[new_conversation_id] = compacted_history
            self.session_histories.pop(conversation_id, None)
            conversation_id = new_conversation_id
        else:
            self.session_histories[conversation_id] = compacted_history

        self._debug(
            "run_metrics",
            f"run_id={run_id} session_id={session_id} conversation_id={conversation_id} "
            f"queue_wait_ms={queue_wait_ms} llm_ms={llm_ms_total} tool_ms={tool_ms_total} "
            f"memory_ms={memory_ms_total} dedup_key={getattr(msg, 'dedup_key', '')}",
        )

        return {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "user_scope_id": user_scope_id,
            "text": final_answer,
        }
