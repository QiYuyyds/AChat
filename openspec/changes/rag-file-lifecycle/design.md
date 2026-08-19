## Context

AChat RAG 系统已实现 Document 生命周期状态机（`FileStatus` 11 种状态）和 obsidian_sync 专用的虚拟目录树。但对齐 Fidi-Intelli 需要：

1. **通用文件树**：当前 `list_tree` 仅支持 `source='obsidian_sync'` 的文档，需要扩展到所有 source 类型
2. **图像提取**：Fidi-Intelli 在解析 DOCX/PPTX 时提取内嵌图片存到 workspace，AChat 当前 `ParseResult` 只有 `content` 字段
3. **文件预览**：Fidi-Intelli 有文件预览面板，AChat 前端只能查看 `content_md` 原文

用户明确要求：
- Office 文件（DOCX/PPTX）预览只读 `parsed.md`（已解析的 Markdown），不做 Office→PDF 转换
- 文件预览的范围、Schema 约束、图像提取与 Fidi-Intelli 对齐
- 图像提取存储在 workspace 中
- 不放宽文件配额

## Goals / Non-Goals

**Goals:**
- Document 模型支持 `parent_id` + `is_folder` 构建虚拟目录树
- `ParseResult` 支持 `images` 字段返回提取的内嵌图片
- 图像写入 `<workspace>/documents/<document_id>/images/`，路径回填到 version meta
- 文件预览 API 返回已解析 Markdown，不做格式转换
- 统一文件树 API 支持所有 source 类型

**Non-Goals:**
- 不做 Office→PDF 转换（预览只读 `parsed_md`）
- 不放宽文件配额（sandbox 仍 100MB / 1000 文件）
- 不实现前端预览组件（前端改动由后续 UI 提案处理）
- 不修改 chunking 或 embedding 逻辑
- 不实现图谱配置锁定（用户明确不做）

## Decisions

### Decision 1: `parent_id` + `is_folder` 而非嵌套 set 模型

**Choice**: 在 `documents` 表新增 `parent_id VARCHAR(64) NULL`（自引用 FK）和 `is_folder BOOLEAN DEFAULT FALSE`，用邻接表模型表达层级。

**Rationale**: 邻接表模型简单、查询直观（递归 CTE 或应用层组装）。Fidi-Intelli 也是 `parent_id` 模型。嵌套 set 模型写入开销大，不适合频繁变动的知识库。`source_path` 仍保留用于排序/展示，但层级关系由 `parent_id` 表达。

**Alternative considered**: 物化路径（`path` like `/a/b/c`）。否决——与 `source_path` 语义重叠，维护两套路径增加复杂度。

### Decision 2: 图像存储路径 `workspace/documents/{document_id}/images/`

**Choice**: 图像文件写入 `<effective_cwd>/documents/<document_id>/images/<filename>`，路径列表回填到 `DocumentVersion.meta["images"]` 数组。

**Rationale**: workspace 代理模式，与现有 `fs_write` 等工具共用沙箱路径检查。`document_id` 隔离不同文档的图像。version meta 记录图像路径列表，前端可按路径访问。

**Alternative considered**: 存到 MinIO 等对象存储。否决——用户明确要求 workspace 代理，不引入新基础设施。

### Decision 3: `ParseResult.images` 格式

**Choice**: `ParseResult` 新增 `images: list[ExtractedImage]` 字段，`ExtractedImage` 包含 `filename: str`、`data: bytes`、`content_type: str`。

**Rationale**: 解析器在 `process_file()` 时提取图像 bytes，`DocumentService.upload_file()` 负责将 bytes 写入 workspace 文件系统并记录路径。分离职责：解析器只管提取，service 只管存储。

**Alternative considered**: 解析器直接写文件。否决——解析器不应有副作用，且 workspace 路径解析需要 service 层上下文。

### Decision 4: 文件预览只读 `parsed_md`

**Choice**: `GET /api/documents/{id}/preview` 返回 `DocumentVersion.content_md`（即已解析的 Markdown），不做任何格式转换。

**Rationale**: 用户明确要求 Office 文件预览只读已解析的 Markdown。Office→PDF 转换需要 LibreOffice 等重依赖，不值得引入。`content_md` 已由 `parse_document()` 生成，质量足够。

**Alternative considered**: Office→PDF 转换。否决——重依赖，用户明确不做。

### Decision 5: 统一文件树 API 扩展 `list_tree`

**Choice**: 扩展现有 `list_tree` 方法，移除 `source == 'obsidian_sync'` 过滤，改为按 `parent_id` 构建层级。根路径 `parent_id IS NULL`，文件夹 `is_folder=True`。

**Rationale**: 当前 `list_tree` 硬编码 `source='obsidian_sync'`，无法支持 user_upload 等类型的目录浏览。用 `parent_id` 查询更通用。旧 `source_path` 派生的文件夹逻辑保留为兼容路径（如果 `parent_id` 全为 NULL 则回退到 source_path 派生）。

## Risks / Trade-offs

- **[Risk] `parent_id` 自引用 FK 在 SQLite 上不支持 RESTRICT 级联** → 使用 `SET NULL` 级联，删除文件夹时子文档变为根文档
- **[Risk] 图像提取增加解析时间** → 受 `rag_extract_images` 配置项控制，默认 True 但可在 `.env` 关闭
- **[Risk] workspace 图像文件超出配额** → sandbox 模式 100MB / 1000 文件上限仍生效；图像通常较小（< 1MB），不会触发配额
- **[Risk] 文件预览返回大 Markdown 导致响应缓慢** → `content_md` 通常 < 1MB，FastAPI JSON 序列化足够快；超大文档可后续加分页

## Migration Plan

1. 迁移脚本在 `rag_overhaul_migration.py` 中追加：
   - `ALTER TABLE documents ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64) REFERENCES documents(id) ON DELETE SET NULL`
   - `ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_folder BOOLEAN DEFAULT FALSE`
2. 回填：`UPDATE documents SET is_folder = FALSE WHERE is_folder IS NULL`
3. `models.py` 中同步声明新列
4. 旧数据不需要设置 `parent_id`（NULL 表示根文档）

## Open Questions

无——所有决策点已在讨论中确认。
