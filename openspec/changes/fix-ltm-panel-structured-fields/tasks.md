## 1. 后端 API 序列化补齐

- [x] 1.1 `api/memory.py` — `_ltm_item_to_dict` 返回值新增 `summary`、`keywords`、`contentScope` 字段
- [x] 1.2 `api/memory.py` — `_ltm_row_to_dict` 返回值新增 `summary`、`keywords`、`contentScope` 字段
- [x] 1.3 `api/memory.py` — `LTMUpdateRequest` Pydantic 模型新增 `summary: str | None`、`keywords: list[str] | None`、`contentScope: str | None` 可选字段
- [x] 1.4 `api/memory.py` — `update_ltm_memory` 端点将新字段透传给 `svc.ltm.update_item`；summary 变化时触发 `_recompute_embedding`（用新 summary 而非 content）

## 2. 后端 update_item 签名扩展

- [x] 2.1 `long_term.py` — `update_item` 方法签名新增 `summary: str | None = None`、`keywords: list[str] | None = None`、`content_scope: str | None = None` 参数
- [x] 2.2 `long_term.py` — `update_item` 方法体中 non-None 时赋值到 `target` 对象（与 content / importance / category / tags 模式一致）

## 3. 前端类型补齐

- [x] 3.1 `src/lib/api/memory.ts` — `LongTermMemoryItem` 接口新增 `summary: string`、`keywords: string[]`、`contentScope: string` 字段
- [x] 3.2 `src/lib/api/memory.ts` — `LTMUpdateBody` 接口新增 `summary?: string`、`keywords?: string[]`、`contentScope?: string` 可选字段

## 4. 前端 LTM 面板展示改造

- [x] 4.1 `long-term-memory-panel.tsx` — `CATEGORIES` 数组对齐后端实际值（`""` → 通用、`fact`、`preference`、`policy`、`tool_failure`、`identity`、`case`），移除 `general` / `skill` / `project`
- [x] 4.2 `long-term-memory-panel.tsx` — `CATEGORY_STYLES` / `CATEGORY_LABELS` 新增 `case`（任务经验）样式和标签，补齐 `policy` / `tool_failure` / `identity` 的样式和标签
- [x] 4.3 `long-term-memory-panel.tsx` — 卡片展示区：summary 非空时在 content 上方显示标题行（`font-medium`），空时隐藏
- [x] 4.4 `long-term-memory-panel.tsx` — 卡片展示区：keywords 非空时以 `#` 前缀 + 独立颜色样式展示，区别于 tags
- [x] 4.5 `long-term-memory-panel.tsx` — 卡片元数据行：contentScope 非空时以 `Folder` 图标 + 等宽字体显示路径标注

## 5. 前端 LTM 面板编辑改造

- [x] 5.1 `long-term-memory-panel.tsx` — 新增 `editSummary` / `editKeywords` / `editContentScope` state 变量
- [x] 5.2 `long-term-memory-panel.tsx` — `startEdit` 函数从 item 填充新 state（summary、keywords.join(', ')、contentScope）
- [x] 5.3 `long-term-memory-panel.tsx` — `cancelEdit` 函数清空新 state
- [x] 5.4 `long-term-memory-panel.tsx` — 编辑表单 UI 新增 summary Input（content 上方）、keywords Input（逗号分隔，tags 下方）、contentScope Input（keywords 下方）
- [x] 5.5 `long-term-memory-panel.tsx` — `handleSave` 函数将新字段加入 `updateLongTermMemory` 调用参数（keywords 从逗号分隔转为数组）

## 6. 测试与验证

- [x] 6.1 后端 `ruff check .` 通过
- [x] 6.2 后端 `pytest` 通过（不新增测试，确认无回归）
- [x] 6.3 前端 `pnpm typecheck` 通过
- [x] 6.4 前端 `pnpm lint` 通过
- [ ] 6.5 手动验证：LTM 面板能展示 summary / keywords / contentScope，编辑后保存生效，分类过滤含 case 选项
