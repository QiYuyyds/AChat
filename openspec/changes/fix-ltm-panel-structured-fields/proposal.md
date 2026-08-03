## Why

`add-structured-memory-items` 在记忆子系统内部完成了 `summary` / `keywords` / `content_scope` 三个结构化字段的双路检索改造，但从 HTTP API 序列化层到前端展示层完全没跟进——API 不返回新字段、前端类型缺定义、LTM 面板不展示也不可编辑、分类过滤列表与后端实际产出的 category 脱节。用户在记忆管理面板看不到任何结构化改造的成果。

## What Changes

**A. 后端 API 序列化补齐**

- `api/memory.py` 的 `_ltm_item_to_dict` / `_ltm_row_to_dict` 返回 `summary`、`keywords`、`contentScope` 三个字段
- `LTMUpdateRequest` Pydantic 模型新增 `summary` / `keywords` / `contentScope` 可选字段
- `update_ltm_memory` 端点透传新字段给 `LongTerm.update_item`
- `LongTerm.update_item` 方法签名新增 `summary` / `keywords` / `content_scope` 参数

**B. 前端类型补齐**

- `LongTermMemoryItem` 接口新增 `summary` / `keywords` / `contentScope` 字段
- `LTMUpdateBody` 接口新增对应可选字段

**C. LTM 面板展示 + 编辑 + 分类对齐**

- 卡片展示：`summary` 作为标题行显示在 content 上方；`keywords` 以独立标签样式展示（区别于 `tags`）；`contentScope` 以路径标注形式显示
- 编辑表单新增 `summary` / `keywords` / `contentScope` 输入框
- `CATEGORIES` 过滤列表对齐后端实际产出的 category 值，新增 `case` 分类
- `startEdit` / `handleSave` 处理新字段的读取与提交

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `memory-persistence`: LTM HTTP API 序列化层 SHALL 返回并接受 `summary` / `keywords` / `contentScope` 字段；`update_item` SHALL 支持更新这三个字段
- `frontend`: LTM 管理面板 SHALL 展示和编辑结构化字段；分类过滤列表 SHALL 与后端实际 category 值对齐

## Impact

- **后端代码**：`backend/app/api/memory.py`（序列化 + 请求模型 + 端点透传）、`backend/app/memory/long_term.py`（`update_item` 签名）
- **前端代码**：`src/lib/api/memory.ts`（类型定义）、`src/components/settings/memory-management/long-term-memory-panel.tsx`（展示 + 编辑 + 分类）
- **不改动**：记忆子系统内部（提取 / 检索 / 合并 / 迁移已在 `add-structured-memory-items` 完成）、数据库 schema（列已存在）、Preference / SessionMemory 面板
- **风险**：低——纯"加字段"改动，不改变现有字段语义；未迁移的存量记忆 summary 为空，面板显示时降级为只显示 content（与当前行为一致）
