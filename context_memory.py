from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import yaml


def _load_model_router() -> str:
    try:
        with open("personalization.yaml", "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            return (raw.get("models", {}) or {}).get("router", "qwen-turbo")
    except Exception:
        return "qwen-turbo"


ROUTER_MODEL = _load_model_router()


@dataclass
class MemoryStore:
    """仅负责 identity/soul/memory 的存储和读取。"""

    root_dir: Path

    def __post_init__(self):
        self.identity_md = self.root_dir / "identity.md"
        self.soul_md = self.root_dir / "soul.md"
        self.memory_md = self.root_dir / "memory.md"

    def ensure_md_files(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        if not self.identity_md.exists():
            self.identity_md.write_text(
                "# Identity\n\n- 你是一个可靠、务实、尊重用户意图的 AI 助手。\n",
                encoding="utf-8",
            )
        if not self.soul_md.exists():
            self.soul_md.write_text(
                "# Soul\n\n- 当前还没有稳定偏好，先保持简洁清晰。\n",
                encoding="utf-8",
            )
        if not self.memory_md.exists():
            self.memory_md.write_text(
                "# Memory\n\n## Daily Summaries\n\n## Recent Interactions\n",
                encoding="utf-8",
            )

    def read_identity(self) -> str:
        return self._read_text(self.identity_md)

    def read_soul(self) -> str:
        return self._read_text(self.soul_md)

    def write_soul(self, soul_text: str) -> None:
        self.soul_md.write_text(soul_text.strip() + "\n", encoding="utf-8")

    def read_memory(self) -> str:
        return self._read_text(self.memory_md)

    def write_memory(self, memory_text: str) -> None:
        self.memory_md.write_text(memory_text, encoding="utf-8")

    @staticmethod
    def parse_memory_sections(memory_text: str):
        marker = "## Recent Interactions"
        idx = memory_text.find(marker)
        if idx == -1:
            memory_text = "# Memory\n\n## Daily Summaries\n\n## Recent Interactions\n"
            idx = memory_text.find(marker)
        summary_part = memory_text[:idx].rstrip()
        recent_part = memory_text[idx + len(marker) :].strip()
        recent_lines = [line for line in recent_part.splitlines() if line.strip().startswith("-")]
        return summary_part, recent_lines

    @staticmethod
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8").strip()


@dataclass
class MemoryRefiner:
    """负责提取/整理对话为 memory，并把稳定偏好提炼到 soul。"""

    client: object
    store: MemoryStore
    max_recent_lines: int = 40

    def update_memory(self, user_input: str, assistant_output: str) -> None:
        memory_text = self.store.read_memory()
        summary_part, recent_lines = self.store.parse_memory_sections(memory_text)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        safe_user = user_input.replace("\n", " ").strip()
        safe_assistant = assistant_output.replace("\n", " ").strip()
        recent_lines.append(f"- [{now}] U: {safe_user} | A: {safe_assistant[:300]}")

        if len(recent_lines) > self.max_recent_lines:
            cut = len(recent_lines) // 2
            to_compress = recent_lines[:cut]
            compressed = self._compress_with_llm(to_compress)
            day = datetime.now().strftime("%Y-%m-%d")

            daily_head = "## Daily Summaries"
            if daily_head not in summary_part:
                summary_part = "# Memory\n\n## Daily Summaries"
            summary_part += f"\n- {day} 压缩记忆:\n{compressed}\n"
            recent_lines = recent_lines[cut:]

        new_memory = (
            f"{summary_part.strip()}\n\n## Recent Interactions\n"
            + ("\n".join(recent_lines) if recent_lines else "")
            + "\n"
        )
        self.store.write_memory(new_memory)

    def maybe_update_soul(self) -> None:
        memory_text = self.store.read_memory()
        _, recent_lines = self.store.parse_memory_sections(memory_text)
        if len(recent_lines) == 0 or len(recent_lines) % 8 != 0:
            return

        soul_text = self.store.read_soul()
        recent_context = "\n".join(recent_lines[-12:])
        try:
            response = self.client.chat.completions.create(
                model=ROUTER_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "你是长期偏好提炼器。根据最近对话，更新 Soul，保留稳定偏好，避免冗长。输出 markdown。",
                    },
                    {
                        "role": "user",
                        "content": f"旧 Soul:\n{soul_text}\n\n最近对话:\n{recent_context}",
                    },
                ],
                temperature=0.2,
                stream=False,
            )
            updated = (response.choices[0].message.content or "").strip()
            if updated:
                self.store.write_soul(updated)
        except Exception:
            pass

    def compact_history_if_needed(self, history_list: list, max_chars: int = 12000) -> list:
        """
        当上下文接近上限时自动压缩历史，进入下一段对话。
        返回压缩后的新 history_list（保留近期消息，老消息写入 memory.md）。
        """
        total_chars = sum(len((m.get("content") or "")) for m in history_list if isinstance(m, dict))
        if total_chars < max_chars or len(history_list) < 12:
            return history_list

        cut = int(len(history_list) * 0.7)
        old_chunk = history_list[:cut]
        keep_chunk = history_list[cut:]

        compact_source = []
        for msg in old_chunk:
            role = msg.get("role", "")
            content = (msg.get("content") or "").replace("\n", " ")
            compact_source.append(f"{role}: {content[:300]}")

        summary = self._compress_with_llm(compact_source)
        self.update_memory("系统自动压缩上下文", summary)
        return keep_chunk

    def _compress_with_llm(self, lines: list[str]) -> str:
        joined = "\n".join(lines)
        try:
            response = self.client.chat.completions.create(
                model=ROUTER_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "你是记忆压缩器。请把对话压缩成 3-6 条可复用记忆，聚焦偏好、目标、约束。",
                    },
                    {"role": "user", "content": joined},
                ],
                temperature=0.2,
                stream=False,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            preview = " | ".join(lines[:3])
            return f"- 历史对话压缩（fallback）: {preview[:200]}"


@dataclass
class ContextAssembler:
    """负责拼接模型需要的系统上下文。"""

    store: MemoryStore

    def build_system_context(self) -> str:
        identity = self.store.read_identity()
        soul = self.store.read_soul()
        memory = self.store.read_memory()
        recent_preview = memory[-2800:] if len(memory) > 2800 else memory
        return (
            "请严格遵循以下长期上下文（Identity/Soul/Memory）：\n\n"
            f"{identity}\n\n{soul}\n\n{recent_preview}"
        )


@dataclass
class MemoryContextManager:
    """
    兼容旧接口的门面类，内部拆分为：
    - MemoryStore（长期记忆存储）
    - MemoryRefiner（记忆提取/整理/提炼）
    - ContextAssembler（模型上下文拼接）

    支持 user_scope_id：每个用户范围独立长期记忆目录。
    """

    client: object
    root_dir: Path
    max_recent_lines: int = 40

    def __post_init__(self):
        self.memory_scopes_dir = self.root_dir / "memory_scopes"
        self._stores: dict[str, MemoryStore] = {}
        self._refiners: dict[str, MemoryRefiner] = {}
        self._assemblers: dict[str, ContextAssembler] = {}

    @staticmethod
    def _sanitize_scope_id(user_scope_id: str | None) -> str:
        raw = (user_scope_id or "default").strip() or "default"
        return re.sub(r"[^a-zA-Z0-9._-]", "_", raw)

    def _ensure_scope_components(self, user_scope_id: str | None):
        scope = self._sanitize_scope_id(user_scope_id)
        if scope not in self._stores:
            scope_dir = self.memory_scopes_dir / scope
            store = MemoryStore(root_dir=scope_dir)
            refiner = MemoryRefiner(
                client=self.client,
                store=store,
                max_recent_lines=self.max_recent_lines,
            )
            assembler = ContextAssembler(store=store)
            self._stores[scope] = store
            self._refiners[scope] = refiner
            self._assemblers[scope] = assembler
        return scope

    def ensure_md_files(self, user_scope_id: str | None = None) -> None:
        scope = self._ensure_scope_components(user_scope_id)
        self._stores[scope].ensure_md_files()

    def build_system_context(self, user_scope_id: str | None = None) -> str:
        scope = self._ensure_scope_components(user_scope_id)
        self._stores[scope].ensure_md_files()
        return self._assemblers[scope].build_system_context()

    def update_memory(self, user_input: str, assistant_output: str, user_scope_id: str | None = None) -> None:
        scope = self._ensure_scope_components(user_scope_id)
        self._stores[scope].ensure_md_files()
        self._refiners[scope].update_memory(user_input, assistant_output)

    def maybe_update_soul(self, user_scope_id: str | None = None) -> None:
        scope = self._ensure_scope_components(user_scope_id)
        self._stores[scope].ensure_md_files()
        self._refiners[scope].maybe_update_soul()

    def compact_history_if_needed(
        self,
        history_list: list,
        max_chars: int = 12000,
        user_scope_id: str | None = None,
    ) -> list:
        scope = self._ensure_scope_components(user_scope_id)
        self._stores[scope].ensure_md_files()
        return self._refiners[scope].compact_history_if_needed(history_list, max_chars=max_chars)
