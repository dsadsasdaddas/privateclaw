# 排障页（Troubleshooting）

## 自动停止重试的常见错误签名

以下错误通常不是“多试几次”能解决的，系统会尽快停止自动重试并把控制权交回用户：

- `approval required`
- `allowlist miss`
- `permission denied` / `forbidden` / `not authorized`
- `权限缺失` / `权限不足`
- `节点不在前台` / `not in foreground`

## 触发保护阈值

- **同一工具 + 基本相同参数连续失败 3 次**：停止重试并返回最近报错。
- **连续 8 步无状态变化**：终止当前 run。
- **单轮 run 超过 60 秒**：强制结束并把控制权交回用户。

## 处理建议

1. 先看报错是否属于权限/审批/allowlist/前台节点类问题。
2. 如是上述问题，先修复环境条件（开权限、过审批、补 allowlist、切回前台节点）。
3. 确认后再重试，而不是让 Agent 在同条件下重复调用工具。
