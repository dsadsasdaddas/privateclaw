# 项目依赖与运行说明

## 1) Python 版本
- 推荐：Python **3.10+**

## 2) 安装依赖
在项目根目录执行：

```bash
pip install openai pyyaml ddgs langgraph playwright lark-oapi
python -m playwright install chromium
```

## 3) 安全填写 API Key（不要写进代码）
本项目通过环境变量读取 DashScope Key：`DASHSCOPE_API_KEY`。

### Linux / macOS
```bash
export DASHSCOPE_API_KEY="你的DashScopeKey"
python main.py
```

### Windows PowerShell
```powershell
$env:DASHSCOPE_API_KEY="你的DashScopeKey"
python main.py
```

> 注意：不要把 key 直接写在 `main.py` 里，也不要提交到 git。

## 4) 运行（默认飞书单通道）
```bash
python main.py
```

## 5) 功能触发
- 输入包含 **“深度搜索”** 的内容时，会走 DeepSearch 多轮搜索流程。
- 其他输入会继续走原有路由（SIMPLE / COMPLEX）。

## 6) 飞书长连接配置（单通道入口）
`main.py` 内置飞书长连接入口，需要以下环境变量：

```bash
export LARK_APP_ID="你的飞书AppID"
export LARK_APP_SECRET="你的飞书AppSecret"
```

如需临时切回本地 CLI：

```bash
MESSAGE_ENTRY=cli python main.py
```

## 7) 个性化配置（YAML）
使用 `personalization.yaml` 进行个性化配置（仅保留必要项）：

- `api_key_env`
- `base_url`
- `models.chat`
- `models.router`
- `models.fsm`
- `models.plan`
- `models.summary`
- `deepsearch_trigger_keyword`

## 8) 工具审批方式（飞书）
- `exec_cli_command` 与 `schedule_cli_command` 的审批不再使用终端 `input()`。
- Agent 会在飞书会话中发送审批请求，用户通过回复 `yes/no` 完成确认。
