## Why

沉淀 → 记忆 → 长期记忆的类型筛选用 4 项下拉（全部分类 / 经验 / 知识 / 日常），多一次点击还藏着当前状态；选项少却走「高级筛选」形态，且与卡片、兴趣话题已有的彩色语言脱节。类型是一级视图切换，应一键可见可点。

## What Changes

- 移除类型 `<select>`（含「全部分类」文案）
- 在搜索行**下方**增加单选 chips：`全部` / `经验` / `知识` / `日常`
- chips **不带数量**
- 颜色与卡片 / 兴趣话题统一复用现有 `BUCKET_CONFIG`（蓝 / 绿 / 琥珀；全部为中性）
- 去掉工具栏「筛选」标签；`Agent ID` 输入从主栏移除（低频，不在本次加「更多」入口）
- 搜索与类型筛选关系：非搜索模式下 chips 驱动 `bucket` 拉取；进入搜索模式时 chips 暂不改变搜索请求，清除搜索后恢复当前 chip 对应列表

## Capabilities

### New Capabilities

- `ltm-bucket-filter`: 长期记忆面板的类型筛选 UI 契约（chips 布局、选项、选中态色系、与列表加载的关系）

### Modified Capabilities

- （无）现有 OpenSpec 主 specs 未定义 LTM 面板筛选行为

## Impact

- **前端**：`src/components/settings/memory-management/long-term-memory-panel.tsx`（筛选栏 + `filterBucket` 状态；`BUCKETS` / `BUCKET_CONFIG` 复用）
- **后端 / API**：无变更（仍用 `fetchMemoryFiles({ bucket })`）
- **依赖**：无新增
