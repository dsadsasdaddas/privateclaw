import os
from pathlib import Path
import threading
import time

from openai import OpenAI
import yaml
from tools import AVAILABLE_TOOLS
from state_thinking import AgentFSM
from deepsearch import DeepSearch
from context_memory import MemoryContextManager


def _get_api_key() -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "未检测到 DASHSCOPE_API_KEY。请先在环境变量中设置，例如："
            "export DASHSCOPE_API_KEY='your_api_key'"
        )
    return api_key


client = OpenAI(
    api_key=_get_api_key(),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

memory_manager = MemoryContextManager(client=client, root_dir=Path(__file__).resolve().parent)


# CALL THE LLM

def call_model(user_input: str, history_list: list):
    messages = [{"role": "system", "content": memory_manager.build_system_context()}]
    messages.extend(history_list)
    messages.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model="qwen3.5-plus",
        messages=messages,
        stream=False,
    )

    message = response.choices[0].message
    msg_dict = message.model_dump(exclude_none=True)
    if msg_dict.get("content") is None:
        msg_dict["content"] = ""

    history_list.append({"role": "user", "content": user_input})
    history_list.append({"role": "assistant", "content": msg_dict["content"]})
    return msg_dict["content"]



def llm_router(user_input: str) -> str:
    messages = [
        {
            "role": "system",
            "content": "你是一个严格的意图分类器。如果用户输入需要联网、工具调用、代码执行或复杂任务，回复COMPLEX；日常闲聊回复SIMPLE。仅输出一个词。",
        },
        {"role": "user", "content": user_input},
    ]

    response = client.chat.completions.create(
        model="qwen-turbo",
        messages=messages,
        temperature=0.0,
        stream=False,
    )

    result = (response.choices[0].message.content or "").strip().upper()
    if "COMPLEX" in result:
        return "COMPLEX"
    return "SIMPLE"


# deactivate python -m venv myenv  .\myenv\Scripts\activate python main.py
def _read_yaml_file(file_path: str) -> list:
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
            if data is None:
                return []
            return data
    except Exception:
        return []


def load_tool_config(file_path: str) -> list:  # 载入文件
    core_tools = _read_yaml_file("tool_config.yaml")
    dynamic_tools = _read_yaml_file("dynamic_config.yaml")

    if isinstance(dynamic_tools, dict):
        dynamic_tools = [dynamic_tools]
    if not isinstance(dynamic_tools, list):
        dynamic_tools = []
    return core_tools + dynamic_tools


def _start_heartbeat(stop_event: threading.Event):
    while not stop_event.is_set():
        print(f"[heartbeat] main online @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
        stop_event.wait(20)


def main():
    memory_manager.ensure_md_files()
    tool_config = load_tool_config("tool_config.yaml")

    fsm_agent = AgentFSM(client, tool_config, AVAILABLE_TOOLS)
    deep_search_agent = DeepSearch(client)

    chat_history = []
    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(target=_start_heartbeat, args=(stop_event,), daemon=True)
    heartbeat_thread.start()

    try:
        while True:
            user_input = input("User:")
            if user_input == "quit":
                break

            if "深度搜索" in user_input:
                query = user_input.replace("深度搜索", "", 1).strip()
                if not query:
                    query = user_input
                result = deep_search_agent.run(query)
                print(f"output:{result}")
                memory_manager.update_memory(user_input, result)
                memory_manager.maybe_update_soul()
                chat_history = memory_manager.compact_history_if_needed(chat_history)
                continue

            task_type = llm_router(user_input)

            if task_type == "COMPLEX":
                result = fsm_agent.run(user_input)
            else:
                result = call_model(user_input, chat_history)

            print(f"output:{result}")
            memory_manager.update_memory(user_input, result)
            memory_manager.maybe_update_soul()
            chat_history = memory_manager.compact_history_if_needed(chat_history)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=1)


if __name__ == "__main__":
    main()
