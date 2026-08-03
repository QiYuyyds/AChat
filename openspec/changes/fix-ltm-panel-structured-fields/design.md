## Context

`add-structured-memory-items` 在记忆子系统内部（`consolidation.py` / `long_term.py` / `memory_writer.py` / `memory_service.py` / `memory_store.py` / `memory_rag.py`）完成了 `summary` / `keywords` / `content_scope` 三字段的提取、双路检索、合并和迁移。但 HTTP API 层（`api/memory.py`）的序列化函数和请求模型没有同步更新，前端类型定义和 LTM 面板也没有跟进。

当前状态：
- 数据库有列、`Item` dataclass 有字段、`store_classified` 能写入、`recall` 能用双路打分
- 但 `GET /api/memory/long-term` 返回的 JSON 里没有这三个字段
- `PUT /api/memory/long-term/{id}` 不接受这三个字段
- 前端面板只展示 content / category / importance / tags，分类过滤列表与后端脱节

约束：
- 纯"加字段"改动，不改变现有字段语义
- 不改数据库 schema（列已存在）
- 不改记忆子系统内部逻辑（已在上一变更完成）

## Goals / Non-Goals

**Goals:**

- HTTP API 序列化返回 `summary` / `keywords` / `contentScope`
- HTTP API 更新接口接受并透传这三个字段
- 前端 LTM 面板展示 summary（标题）、keywords（检索标签）、contentScope（路径标注）
- 前端编辑表单可编辑这三个字段
- 分类过滤列表对齐后端实际产出的 category 值

**Non-Goals:**

- 不改记忆提取 / 检索 / 合并 / 迁移逻辑（已完成）
- 不改 Preference 面板和 SessionMemory 面板
- 不改 `memory_recall` 工具返回格式（已在上一变更完成）
- 不做 summary / keywords 的前端校验（后端提取时已保证质量，手动编辑时允许空值）

## Decisions

### D1: summary 在卡片中作为标题行显示

**选择**：summary 显示在 content 上方，用 `font-medium` + 略小字号，空时隐藏整行。

**理由**：summary 是 content 的浓缩标题，视觉上应做"先看标题再看详情"的层次。空 summary（未迁移的存量记忆）隐藏标题行，降级为当前的纯 content 展示，零破坏。

### D2: keywords 用独立样式区别于 tags

**选择**：keywords 用带 `#` 前缀的标签样式（`bg-primary/5 text-primary/70`），tags 保持现有 `bg-muted/60` 样式。

**理由**：设计文档 D3 明确 keywords 和 tags 语义不同——tags 是结构化分类标签（少，固定枚举），keywords 是检索关键词（多，自由文本）。视觉上区分避免用户混淆。

### D3: contentScope 以路径标注形式显示

**选择**：contentScope 非空时在卡片底部元数据行显示，用等宽字体 + `Folder` 图标前缀，空时隐藏。

**理由**：contentScope 是项目路径，属于元数据而非主要内容。放在底部和 agentId / createdAt 同行，视觉权重低但可读。

### D4: 分类列表对齐后端实际值

**选择**：将 `CATEGORIES` 改为后端实际产出的值集合，加中文 label：

| category | label | 来源 |
|----------|-------|------|
| `""` (空) | 通用 | 常规 LTM 提取 |
| `fact` | 事实 | memory_store 工具 |
| `preference` | 偏好 | classify_memory_content |
| `policy` | 策略 | memory_store / classify |
| `tool_failure` | 工具失败 | memory_store / classify |
| `identity` | 身份 | classify_memory_content |
| `case` | 任务经验 | case 提取（新增） |

**替代**：保留旧的 `general` / `skill` / `project` 并在后端也产出这些值——但后端从未产出过这些值，改后端无意义。

**理由**：前端过滤栏选不存在的 category 永远返回空结果，是实际 bug。对齐后端实际值是唯一正确做法。

### D5: 编辑表单 summary / keywords / contentScope 布局

**选择**：
- summary：单行 Input，放在 content textarea 上方
- keywords：单行 Input，逗号分隔（与 tags 编辑方式一致）
- contentScope：单行 Input，放在 tags 下方

**理由**：与现有编辑表单的 Input 风格一致。keywords 用逗号分隔与 tags 的编辑方式统一，降低用户认知成本。

### D6: update_item 签名扩展

**选择**：`update_item` 新增 `summary: str | None = None`、`keywords: list[str] | None = None`、`content_scope: str | None = None` 参数，仅 non-None 时更新（与现有 content / importance / category / tags 的模式一致）。

**理由**：保持与现有字段的更新模式一致——部分更新，不传的字段不动。`update_item` 已在 PG write 时带上 `target.summary` 等已有值（`long_term.py` line 999-1001），只需加参数赋值逻辑。

## Risks / Trade-offs

### R1: 存量记忆 summary 为空

**风险**：未迁移的存量记忆 summary 为空字符串，面板标题行不显示。

**缓解**：D1 设计为空时隐藏标题行，降级为纯 content 展示，与当前行为一致。迁移完成后标题自动出现。

### R2: 手动编辑 summary 后 embedding 不同步

**风险**：用户手动改了 summary，但 `update_item` 不重算 embedding（embedding 仍基于旧 summary）。

**缓解**：与现有 content 编辑的行为一致——`update_ltm_memory` 端点在 content 变化时触发 `_recompute_embedding`。将同样的触发逻辑扩展到 summary 变化：summary 变化时用新 summary 重算 embedding。这是正确做法，因为 D1 决定 embedding 基于 summary。

## Migration Plan

无需数据库 migration（列已存在）。改动纯代码层面，部署即生效。

回滚：还原 `api/memory.py` / `long_term.py` / `memory.ts` / `long-term-memory-panel.tsx` 四个文件即可。新字段有默认值，不影响存量数据。

## Open Questions

（无——改动范围明确，纯补齐断层）
