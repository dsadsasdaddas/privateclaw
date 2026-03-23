from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class MemoryContextManager:
    client: object
    root_dir: Path
    max_recent_lines: int = 40

    def __post_init__(self):
        self.identity_md = self.root_dir / "identity.md"
        self.soul_md = self.root_dir / "soul.md"
        self.memory_md = self.root_dir / "memory.md"

    def ensure_md_files(self) -> None:
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

    def build_system_context(self) -> str:
        identity = self._read_text(self.identity_md)
        soul = self._read_text(self.soul_md)
        memory = self._read_text(self.memory_md)
        recent_preview = memory[-2800:] if len(memory) > 2800 else memory
        return (
            "请严格遵循以下长期上下文（Identity/Soul/Memory）：\n\n"
            f"{identity}\n\n{soul}\n\n{recent_preview}"
        )

    def update_memory(self, user_input: str, assistant_output: str) -> None:
        memory_text = self._read_text(self.memory_md)
        summary_part, recent_lines = self._parse_memory_sections(memory_text)

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
        self.memory_md.write_text(new_memory, encoding="utf-8")

    def maybe_update_soul(self) -> None:
        memory_text = self._read_text(self.memory_md)
        _, recent_lines = self._parse_memory_sections(memory_text)
        if len(recent_lines) == 0 or len(recent_lines) % 8 != 0:
            return

        soul_text = self._read_text(self.soul_md)
        recent_context = "\n".join(recent_lines[-12:])
        try:
            response = self.client.chat.completions.create(
                model="qwen-turbo",
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
                self.soul_md.write_text(updated + "\n", encoding="utf-8")
        except Exception:
            pass

    def _compress_with_llm(self, lines: list[str]) -> str:
        joined = "\n".join(lines)
        try:
            response = self.client.chat.completions.create(
                model="qwen-turbo",
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

    @staticmethod
    def _parse_memory_sections(memory_text: str):
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
