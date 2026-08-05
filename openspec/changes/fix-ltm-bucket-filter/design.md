## Context

File-native 记忆采用三级生命周期：`session/` → `daily/` → `digest/{procedure,wiki}/`。

前端「长期记忆」筛选把三层语义压成一个下拉：

| UI | 参数 | 预期数据源 |
|----|------|-----------|
| 全部 | 无 bucket | digest 全量 + daily 全量 |
| 经验 | `bucket=procedure` | 仅 digest/procedure |
| 知识 | `bucket=wiki` | 仅 digest/wiki |
| 日常 | `bucket=daily` | 仅 daily/ |

当前 `GET /api/memory/files`：

1. 用 `list_digest_files(bucket=...)` 拉 digest（bucket 过滤有效）
2. **无条件** `list_daily_files()` 并入，且响应里把 daily 标成 `"bucket": "daily"`

因此 procedure/wiki 筛选会混入日常卡。日常/全部「正常」是巧合（daily 侧全量 + digest 在 `bucket=daily` 时目录不存在为空）。

约束：

- 单用户 local-first；不引入 user_id 过滤
- 最小改动：优先只改 API 列表层，不碰 pipeline / 前端 / 搜索
- frontmatter.bucket 合法值仍只有 `procedure` | `wiki`；`"daily"` 仅是列表 API 给 UI 的展示标签

## Goals / Non-Goals

**Goals:**

- 列表 API 的 bucket 语义与产品/生命周期一致
- 经验/知识筛选不再出现 daily 卡
- 日常筛选只返回 daily 卡
- 全部分类行为不变
- 用测试锁住回归

**Non-Goals:**

- 不改 UI 文案、布局、卡片渲染
- 不改搜索 API / HybridSearch
- 不改 auto_memory / auto_dream / auto_index
- 不清理已精炼但仍留在 daily/ 的历史原料卡
- 不修 agent_id 对 daily 的过滤（同源缺口，本次明确不做，避免扩大范围）
- 不改 Preference / session memory

## Decisions

### D1. 只在 API 层条件并入 daily

**选择**：在 `list_memory_files` 中：

```
include_daily = bucket is None or bucket == "daily"
include_digest = bucket is None or bucket in ("procedure", "wiki")
```

- `include_digest` 时继续调用 `list_digest_files(bucket=bucket, agent_id=...)`（`bucket is None` 时传 `None`）
- `include_daily` 时才 `list_daily_files()`

**替代**：

1. 前端客户端再滤一遍 → 其他调用方仍踩坑；API 契约仍错
2. 改 `list_digest_files` 认识 `daily` → 把生命周期阶段塞进 digest 列表函数，语义混淆

**理由**：bug 在列表组装；workspace 两个 list 函数各自职责正确。API 是正确边界。

### D2. `bucket=daily` 时跳过 digest 枚举

**选择**：`bucket == "daily"` 时不调用 `list_digest_files`。

**替代**：继续调用（会扫 `digest/daily/`，目录不存在，结果为空）。

**理由**：语义更清晰，避免依赖「不存在的子目录」这种隐式行为；也避免将来若误建 `digest/daily/` 时行为怪异。

### D3. 未知 bucket 值

**选择**：未知 bucket（非 `procedure`/`wiki`/`daily`/空）时返回空列表（digest 不匹配、daily 不并入）。

**替代**：400 报错；或 fallback 到全部。

**理由**：前端只发白名单值；空列表比 400/泄漏全量更安全。不新增校验复杂度。

### D4. agent_id 与 daily 的交互本次不动

**选择**：即使本次修了 bucket，`agent_id` 过滤仍只作用在 digest 侧（现有行为）。

**理由**：用户明确要求不影响其他功能、范围最小；agent 过滤 daily 是独立产品问题，可另开 change。

## Risks / Trade-offs

- **[日常卡与精炼后 digest 内容重叠]** 同一事实可能同时出现在「日常」和「经验/知识」→ 这是生命周期设计，非本次 bug；不在此 change 清 daily
- **[其他调用方依赖「任意 bucket 都带 daily」]** 当前仅前端 LTM 面板用该参数 → 低风险；修后语义才正确
- **[测试缺 MemoryService 初始化]** API 测试需 mock / 临时 workspace → 沿用 `test_api_memory.py` 现有 fixture 模式

## Migration Plan

- 纯行为修正，无数据迁移
- 部署后前端下拉立即按正确集合展示
- 回滚：还原 `list_memory_files` 条件即可

## Open Questions

- （无阻塞项）agent_id × daily 过滤是否后续单开 change — 默认是，不在本 change 范围
