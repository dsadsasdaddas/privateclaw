# 项目依赖与运行说明（TypeScript 版）

## 1) Node.js 版本
- 推荐：Node.js **20+**

## 2) 安装依赖
在项目根目录执行：

```bash
npm install
```

如需使用 DeepSearch 的网页读取能力，并且当前环境尚未安装 Playwright 浏览器：

```bash
npx playwright install chromium
```

## 3) 安全填写 API Key（不要写进代码）
本项目通过环境变量读取 DashScope Key：`DASHSCOPE_API_KEY`。

### Linux / macOS
```bash
export DASHSCOPE_API_KEY="你的DashScopeKey"
npm run dev
```

### Windows PowerShell
```powershell
$env:DASHSCOPE_API_KEY="你的DashScopeKey"
npm run dev
```

> 注意：不要把 key 直接写在源码里，也不要提交到 git。

## 4) 运行（默认飞书单通道）
```bash
npm run dev
```

生产构建：

```bash
npm run build
npm start
```

## 5) 功能触发
- 复杂检索问题会优先由模型调用 `deep_search` 工具，执行多轮搜索、页面读取和反思总结。
- 其他输入统一走 `src/agent-loop.ts` 的单一思考执行循环（Plan / Execute / Observe）。

## 6) 飞书长连接配置（单通道入口）
需要以下环境变量：

```bash
export LARK_APP_ID="你的飞书AppID"
export LARK_APP_SECRET="你的飞书AppSecret"
```

如需临时切回本地 CLI：

```bash
MESSAGE_ENTRY=cli npm run dev
```

## 7) 个性化配置（YAML）
使用 `personalization.yaml` 进行个性化配置：

- `api_key_env`
- `base_url`
- `models.chat`
- `models.router`
- `models.fsm`
- `models.plan`
- `models.summary`
- `deepsearch_trigger_keyword`（可选）

## 8) 校验
```bash
npm run typecheck
npm run build
```
