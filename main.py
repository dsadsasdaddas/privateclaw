import os
from pathlib import Path
import yaml
from openai import OpenAI

from tools import AVAILABLE_TOOLS
from state_thinking import AgentFSM
from deepsearch import DeepSearch
from context_memory import MemoryContextManager
from agent_loop import AgentLoop
from agent_runtime import AgentRuntime


def load_personalization() -> dict:
    defaults = {
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": {
            "chat": "qwen3.5-plus",
            "router": "qwen-turbo",
            "fsm": "qwen-max",
            "plan": "qwen-turbo",
            "summary": "qwen-plus",
        },
        "deepsearch_trigger_keyword": "深度搜索",
    }
    try:
        with open("personalization.yaml", "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            defaults["api_key_env"] = raw.get("api_key_env", defaults["api_key_env"])
            defaults["base_url"] = raw.get("base_url", defaults["base_url"])
            models = raw.get("models", {})
            for key in defaults["models"]:
                defaults["models"][key] = models.get(key, defaults["models"][key])
            defaults["deepsearch_trigger_keyword"] = raw.get(
                "deepsearch_trigger_keyword", defaults["deepsearch_trigger_keyword"]
            )
    except Exception:
        pass
    return defaults


def load_tool_config() -> list:
    def _read_yaml(file_path: str):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or []
        except Exception:
            return []

    core_tools = _read_yaml("tool_config.yaml")
    dynamic_tools = _read_yaml("dynamic_config.yaml")
    if isinstance(dynamic_tools, dict):
        dynamic_tools = [dynamic_tools]
    if not isinstance(dynamic_tools, list):
        dynamic_tools = []
    return core_tools + dynamic_tools


def build_client(personalization: dict) -> OpenAI:
    api_key_env = personalization["api_key_env"]
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"Missing {api_key_env}. Please set it in environment variables.")
    return OpenAI(api_key=api_key, base_url=personalization["base_url"])


def main():
    personalization = load_personalization()
    client = build_client(personalization)

    memory_manager = MemoryContextManager(client=client, root_dir=Path(__file__).resolve().parent)
    memory_manager.ensure_md_files()

    tool_config = load_tool_config()
    fsm_agent = AgentFSM(client, tool_config, AVAILABLE_TOOLS)
    deep_search_agent = DeepSearch(client)

    agent_loop = AgentLoop(
        client=client,
        memory_manager=memory_manager,
        deep_search_agent=deep_search_agent,
        fsm_agent=fsm_agent,
        personalization=personalization,
    )
    runtime = AgentRuntime(agent_loop=agent_loop)
    message_entry = os.getenv("MESSAGE_ENTRY", "feishu").strip().lower()
    if message_entry == "cli":
        runtime.run()
    else:
        from feishu_entry import FeishuEntry
        FeishuEntry(runtime).run()


if __name__ == "__main__":
    main()
