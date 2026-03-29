# PrivateClaw / 私爪助手

A local AI assistant with persistent memory, deep search, controlled command execution, and Feishu message ingress.  
一个支持长期记忆、深度搜索、受控命令执行与飞书消息入口的 AI 助手。

---

## Features / 功能特性

### English
- **Persistent Memory** via `MEMORY.md` and daily logs in `memory/YYYY-MM-DD.md`.
- **Automatic Context Compression** when history becomes large.
- **Deep Search Workflow** with multi-round query planning, page reading, reflection, and summarization.
- **Safe CLI Execution** through `exec_cli_command` with dangerous-command blocking and human confirmation.
- **Scheduled Execution** through `schedule_cli_command` (run command after a delay).
- **Feishu Single-Channel Ingress** via long connection in `main.py`.
- **Heartbeat Runtime** while `main.py` is running.

### 中文
- 通过 `MEMORY.md` 与 `memory/YYYY-MM-DD.md` 实现**持久记忆**。
- 当上下文过长时自动执行**历史压缩**。
- 提供**深度搜索流程**：多轮检索、页面读取、反思与总结。
- 提供**安全命令执行**：`exec_cli_command` 可拦截危险命令并在执行前征求确认。
- 提供**定时执行能力**：`schedule_cli_command` 可在延迟后执行命令。
- 提供**飞书单通道接入**（长连接消息入口）。
- `main.py` 运行期间有**心跳输出**。

---

## Project Structure / 项目结构

```text
.
├── main.py               # Main entry (Feishu by default) / 主入口（默认飞书）
├── agent_runtime.py      # Runtime orchestration / 运行时编排
├── channel_layer.py      # Channel payload normalization / 渠道消息清洗层
├── feishu_entry.py       # Feishu long-connection ingress / 飞书长连接入口
├── agent_loop.py         # Unified AgentLoop planning/execution loop / 统一思考执行循环
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
pip install openai pyyaml ddgs langgraph playwright lark-oapi
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
pip install openai pyyaml ddgs langgraph playwright lark-oapi
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

Set Feishu credentials:

```bash
export LARK_APP_ID="your_app_id"
export LARK_APP_SECRET="your_app_secret"
python main.py
```

Windows PowerShell:

```powershell
$env:LARK_APP_ID="your_app_id"
$env:LARK_APP_SECRET="your_app_secret"
python main.py
```

- Default entry is Feishu single-channel ingress.
- If you need local CLI mode temporarily: `MESSAGE_ENTRY=cli python main.py`.

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

## Tool: `schedule_cli_command` / 定时命令工具

### English
- Use this when users ask to execute a command after a delay.
- Example intent: “Run `echo hello` after 30 seconds”.
- Dangerous commands are rejected automatically.

### 中文
- 当用户提出“过一段时间再执行命令”时使用。
- 示例意图：“30 秒后执行 `echo hello`”。
- 危险命令会被自动拒绝。

---

## Memory Files / 记忆文件

- `MEMORY.md`: stable preferences, rules, identity, and project conventions / 长期稳定偏好、规则、身份信息、项目约定。
- `memory/YYYY-MM-DD.md`: what was done today, temporary decisions, and active troubleshooting items / 今天做了什么、临时决定、正在排查的问题。

---

## Notes / 备注

- Keep API keys in environment variables; never hardcode secrets.  
  请将密钥保存在环境变量中，不要硬编码到仓库。
- This project is currently optimized for Feishu single-channel ingress.  
  当前项目主要面向飞书单通道消息接入场景。
- Personalized options are configured in `personalization.yaml` (API key env name, base URL, model choices).  
  个性化选项通过 `personalization.yaml` 配置（API Key 环境变量名、Base URL、模型选择）。
- See `TROUBLESHOOTING.md` for common non-retriable error signatures and stop conditions.  
  常见不可重试错误签名与自动停止条件见 `TROUBLESHOOTING.md`。
