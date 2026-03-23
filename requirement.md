# 项目依赖与运行说明

## 1) Python 版本
- 推荐：Python **3.10+**

## 2) 安装依赖
在项目根目录执行：

```bash
pip install openai pyyaml ddgs langgraph playwright
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

## 4) 运行
```bash
python main.py
```

## 5) 功能触发
- 输入包含 **“深度搜索”** 的内容时，会走 DeepSearch 多轮搜索流程。
- 其他输入会继续走原有路由（SIMPLE / COMPLEX）。
