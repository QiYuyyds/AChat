# Aeval Dashboard

Aeval 评测框架的独立可视化前端 (change: add-aeval-integration-dashboard)。

## 启动

```bash
pnpm install
pnpm --filter eval-dashboard dev   # http://localhost:3100
```

- API 默认走同源 `/api/eval/*`,由 Next rewrites 代理到 `EVAL_BACKEND_URL`(默认 `http://localhost:8000`)
- 若代理对 SSE 有缓冲,可在 Dashboard 的 Settings 页把后端地址改为直连后端
- 后端需开启 `EVAL_HARNESS_ENABLED=true` 并配置 `EVAL_AGENT_ID`

## 页面

| 路由 | 说明 |
|---|---|
| `/` | 总览:统计卡片 / 最近 runs / 分数趋势 |
| `/suites` | Suite 列表 + YAML 导入 |
| `/suites/[name]` | Suite 详情:任务清单 / 运行历史 / 运行按钮 |
| `/runs/[runId]` | Run 报告:pass@k 卡片 / 任务表 / 失败明细 (SSE 实时) |
| `/runs/[runId]/trials/[taskId]/[trialIndex]` | Trial 下钻:grader 分解 / transcript / outcome / Phoenix 外链 |
| `/compare` | A/B 对比 |
| `/settings` | 后端 API 地址与 Phoenix endpoint 配置 + 连接测试 |

断线恢复遵循快照+增量协议 (设计文档 §17.3):页面加载先拉全量快照,再订阅
`GET /runs/{id}/stream`;连接断开自动重拉快照,不依赖事件回放。
