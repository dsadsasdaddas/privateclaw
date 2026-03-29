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
            return (raw.get("models", {}) or {}).get("router", "qwen-max")
    except Exception:
        return "qwen-max"


ROUTER_MODEL = _load_model_router()


@dataclass
class MemoryStore:
    """长期记忆与每日日志存储。"""

    root_dir: Path

    def __post_init__(self):
        self.memory_md = self.root_dir / "MEMORY.md"
        self.daily_dir = self.root_dir / "memory"

    def ensure_md_files(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_md.exists():
            self.memory_md.write_text(
                "# MEMORY\n\n"
                "## 长期稳定信息（偏好/规则/身份/项目约定）\n"
                "- 你是一个可靠、务实、尊重用户意图的 AI 助手。\n\n"
                "## 稳定偏好提炼\n"
                "- 暂无。\n\n"
                "## 归档摘要\n"
                "- 暂无。\n",
                encoding="utf-8",
            )
        today_path = self.get_daily_file_path()
        if not today_path.exists():
            today_path.write_text(
                f"# {datetime.now().strftime('%Y-%m-%d')} 工作记忆\n\n"
                "## 今天做了什么\n\n"
                "## 临时决定\n\n"
                "## 正在排查的问题\n\n"
                "## 对话记录\n",
                encoding="utf-8",
            )

    def read_memory(self) -> str:
        return self._read_text(self.memory_md)

    def write_memory(self, memory_text: str) -> None:
        self.memory_md.write_text(memory_text, encoding="utf-8")

    def get_daily_file_path(self, day: datetime | None = None) -> Path:
        day = day or datetime.now()
        return self.daily_dir / f"{day.strftime('%Y-%m-%d')}.md"

    def append_daily_dialogue(self, user_input: str, assistant_output: str) -> None:
        path = self.get_daily_file_path()
        if not path.exists():
            self.ensure_md_files()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        safe_user = user_input.replace("\n", " ").strip()
        safe_assistant = assistant_output.replace("\n", " ").strip()
        line = f"- [{now}] U: {safe_user} | A: {safe_assistant[:300]}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

    def read_recent_daily_lines(self, limit: int = 40) -> list[str]:
        if not self.daily_dir.exists():
            return []
        collected: list[str] = []
        for p in sorted(self.daily_dir.glob("*.md"), reverse=True):
            text = p.read_text(encoding="utf-8")
            lines = [ln for ln in text.splitlines() if ln.strip().startswith("- [")]
            collected.extend(lines[::-1])
            if len(collected) >= limit:
                break
        return collected[:limit]

    def append_memory_section(self, section_title: str, content: str) -> None:
        memory_text = self.read_memory()
        marker = f"## {section_title}"
        if marker not in memory_text:
            memory_text = f"{memory_text.rstrip()}\n\n{marker}\n"
        memory_text = f"{memory_text.rstrip()}\n- {content.strip()}\n"
        self.write_memory(memory_text)

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
        self.store.append_daily_dialogue(user_input, assistant_output)

        recent_lines = self.store.read_recent_daily_lines(limit=self.max_recent_lines + 8)
        if len(recent_lines) > self.max_recent_lines:
            cut = len(recent_lines) // 2
            compressed = self._compress_with_llm(recent_lines[:cut])
            day = datetime.now().strftime("%Y-%m-%d")
            self.store.append_memory_section("归档摘要", f"{day} 压缩记忆: {compressed}")

    def maybe_update_soul(self) -> None:
        recent_lines = self.store.read_recent_daily_lines(limit=24)
        if len(recent_lines) == 0 or len(recent_lines) % 8 != 0:
            return

        memory_text = self.store.read_memory()
        recent_context = "\n".join(recent_lines[-12:])
        try:
            response = self.client.chat.completions.create(
                model=ROUTER_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "你是长期偏好提炼器。根据最近对话提炼稳定偏好/规则/项目约定，输出 3-6 条短句。",
                    },
                    {
                        "role": "user",
                        "content": f"当前 MEMORY:\n{memory_text}\n\n最近对话:\n{recent_context}",
                    },
                ],
                temperature=0.2,
                stream=False,
            )
            updated = (response.choices[0].message.content or "").strip()
            if updated:
                self.store.append_memory_section("稳定偏好提炼", updated)
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
        memory = self.store.read_memory()
        daily_lines = self.store.read_recent_daily_lines(limit=20)
        recent_daily = "\n".join(daily_lines[-12:]) if daily_lines else "- 暂无今日记录"
        recent_preview = memory[-2400:] if len(memory) > 2400 else memory
        return (
            "请严格遵循以下长期上下文（MEMORY.md）与最近工作日志（memory/YYYY-MM-DD.md）：\n\n"
            f"{recent_preview}\n\n## 最近工作日志片段\n{recent_daily}"
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
