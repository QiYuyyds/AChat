## Context

长期记忆面板在 `LongTermMemoryPanel`（`src/components/settings/memory-management/long-term-memory-panel.tsx`）内维护 `filterBucket: 'all' | 'procedure' | 'wiki' | 'daily'`，经 `fetchMemoryFiles({ bucket })` 拉列表。UI 现为工具栏内 `<select>`，文案「全部分类 / 经验 / 知识 / 日常」。同文件已有 `BUCKET_CONFIG`（蓝/绿/琥珀）用于卡片边色与兴趣话题 pills。

约束：纯前端；不改 API / bucket 语义；颜色必须复用现有配置，不新增色板。

## Goals / Non-Goals

**Goals:**
- 类型筛选改为一行单选 chips，放在搜索行下方
- 选项固定：全部 / 经验 / 知识 / 日常；无数量徽章
- 选中/未选中色与卡片、兴趣话题一致
- 去掉「筛选」标签与类型下拉；主栏去掉 Agent ID 输入

**Non-Goals:**
- 不改 bucket 后端模型、不新增类型
- 不做多选、不做数量统计
- 不恢复 Agent ID 的「更多」入口（若以后需要另开 change）
- 不改精选/全部记忆网格布局

## Decisions

### 1. 布局：搜索行 + chips 行（方案 B）

```
[搜索...]                    [刷新] [手动精炼]
[全部] [经验] [知识] [日常]
```

- **为何不用同行 chips**：工具栏已挤；chips 作为一级视图切换应有独立行
- **为何不分组 section**：用户要的是快速过滤，不是归档浏览

### 2. 颜色：只复用 `BUCKET_CONFIG` + 中性「全部」

| chip | 选中态 | 未选中 |
|------|--------|--------|
| 全部 | `bg-primary/10 text-primary` 或 `bg-foreground/10` | muted |
| 经验/知识/日常 | 与兴趣话题 pill 同款：`border-*-500/40 bg-*-500/15 text-*-700` + 色点 | 透明底 + muted，hover 露对应色 |

实现上抽小 helper 或内联 map，**禁止**另写一套 hex/class 色表。

### 3. 交互

- 默认 `all`
- 单选：点击即 `setFilterBucket`，并 `setSearchMode(false)`（与现 select 行为一致，避免搜索结果与类型 chip 语义打架）
- 进入搜索（Enter）：chips 视觉可保留当前选中，但 load 走搜索路径；清除搜索后按当前 chip 重新拉列表
- 无「再点取消」——取消即点「全部」

### 4. 移除项

- `<select>` + 「筛选」Filter 标签
- Agent ID `Input` 与 `filterAgent` 状态（load 不再传 `agentId`）

### 5. 状态 / 数据流

保持现有：

```
filterBucket ──► load() ──► fetchMemoryFiles({ bucket })  或 searchMemoryFiles
```

仅 UI 控件替换；`BUCKETS` 常量可保留或改为 chips 配置数组（含 `all` 的 label）。

## Risks / Trade-offs

- **[Risk] 搜索中 chip 可点导致用户以为已过滤搜索结果** → Mitigation：点 chip 时退出搜索模式并按 bucket 重载（与现 select onChange 一致）
- **[Risk] 去掉 Agent ID 后高级用户无法按 agent 过滤** → Mitigation：本次明确 Non-Goal；API 仍支持 `agentId`，后续可加
- **[Trade-off] chips 占一行垂直空间** → 可接受：比下拉更清晰，且选项仅 4 个

## Migration Plan

- 无数据迁移；发版即生效
- 回滚：还原该文件筛选栏即可

## Open Questions

- 无阻塞项。若设计评审希望「全部」用 `bg-muted` 而非 primary tint，实现时可微调一类名，不影响契约。
