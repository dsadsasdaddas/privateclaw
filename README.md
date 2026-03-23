# PrivateClaw / 私爪助手

A local CLI AI assistant with persistent memory, deep search, and controlled command execution.  
一个支持长期记忆、深度搜索与受控命令执行的本地命令行 AI 助手。

---

## Features / 功能特性

### English
- **Persistent Memory** via `identity.md`, `soul.md`, and `memory.md`.
- **Automatic Context Compression** when history becomes large.
- **Deep Search Workflow** with multi-round query planning, page reading, reflection, and summarization.
- **Safe CLI Execution** through `exec_cli_command` with dangerous-command blocking and human confirmation.
- **Heartbeat Runtime** while `main.py` is running.

### 中文
- 通过 `identity.md`、`soul.md`、`memory.md` 实现**持久记忆**。
- 当上下文过长时自动执行**历史压缩**。
- 提供**深度搜索流程**：多轮检索、页面读取、反思与总结。
- 提供**安全命令执行**：`exec_cli_command` 可拦截危险命令并在执行前征求确认。
- `main.py` 运行期间有**心跳输出**。

---

## Project Structure / 项目结构

```text
.
├── main.py               # Main CLI entry / 主入口
├── state_thinking.py     # FSM agent loop / 状态机执行循环
├── deepsearch.py         # Deep search workflow / 深度搜索流程
├── context_memory.py     # Memory manager / 记忆管理器
├── tools.py              # Tool implementations / 工具实现
├── tool_config.yaml      # Tool schemas / 工具声明
├── dynamic_config.yaml   # Dynamic tool configs / 动态工具配置
├── skills/               # Skill scripts / 技能脚本
├── requirement.md        # Setup notes / 安装说明
└── README.md
```

---

## Installation / 安装

### English
1. Use Python 3.10+.
2. Install dependencies:

```bash
pip install openai pyyaml ddgs langgraph playwright
python -m playwright install chromium
```

3. Set API key:

```bash
export DASHSCOPE_API_KEY="your_api_key"
```

### 中文
1. 使用 Python 3.10+。
2. 安装依赖：

```bash
pip install openai pyyaml ddgs langgraph playwright
python -m playwright install chromium
```

3. 配置 API Key：

```bash
export DASHSCOPE_API_KEY="你的key"
```

Windows PowerShell:

```powershell
$env:DASHSCOPE_API_KEY="你的key"
```

---

## Run / 运行

```bash
python main.py
```

- Type `quit` to exit. / 输入 `quit` 退出。
- Input containing `深度搜索` triggers deep search. / 输入包含 `深度搜索` 会触发深度搜索。

---

## Tool: `exec_cli_command` / 命令执行工具

### English
- The agent decides whether command execution is needed.
- Before execution, the agent asks for explicit human permission.
- Dangerous commands (for example `rm`, `shutdown`, `mkfs`) are blocked.

### 中文
- 由 Agent 自主判断是否需要执行命令。
- 执行前会先征求人类明确同意。
- 危险命令（例如 `rm`、`shutdown`、`mkfs`）会被拦截。

---

## Memory Files / 记忆文件

- `identity.md`: stable assistant identity constraints / 稳定身份约束。
- `soul.md`: long-term style and preferences / 长期风格与偏好。
- `memory.md`: daily summaries and recent interactions / 每日总结与近期交互。

---

## Notes / 备注

- Keep API keys in environment variables; never hardcode secrets.  
  请将密钥保存在环境变量中，不要硬编码到仓库。
- This project is currently optimized for interactive local CLI usage.  
  当前项目主要面向本地 CLI 交互场景。
