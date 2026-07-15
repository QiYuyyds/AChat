## Why

AChat 的 RAG 知识库当前只能通过手动上传文件入库，无法与外部知识源建立持续同步。用户在 Obsidian 等 Markdown 编辑器中维护了大量个人笔记，但每次内容更新都需要手动重新上传，导致知识库内容滞后于实际笔记。需要一个增量同步机制，让 AChat 的 RAG 系统能直接连接本地 Obsidian vault，按需同步并自动处理 Obsidian 特有的 Markdown 语法，同时支持文件系统式的文档组织。

## What Changes

- 新增 `ObsidianSyncService`——手动触发 + 增量同步 Obsidian vault 到 RAG 知识库（扫描 → diff → 处理新增/更新/删除）
- 新增 `ObsidianPreprocessor`——解析 Obsidian 特有 Markdown 语法（frontmatter 提取、transclusion 展开、wikilink 归一化、注释/dataview 剥离、math block 保护、inline tag 提取）
- `documents` 表新增 `source_path` 和 `content_hash` 字段，支持文件系统式路径组织和增量同步判断
- 新增虚拟目录树 API（`GET /api/documents/tree`），前端按文件夹层级浏览知识库
- 新增 Obsidian 同步 API（`POST /api/obsidian/sync`、`GET /api/obsidian/status`）
- `user_settings` 表新增 `obsidian_vault_path` 字段，用户在设置面板全局配置 vault 路径
- 支持 `.obsidianignore` 排除规则（排除 `.obsidian/`、`Templates/` 等目录）
- 扩展 `RecursiveSplitter` 的 `_FENCE_RE`，保护 `$$math$$` 块不被切碎
- 前端知识库视图重构为文件树浏览模式，新增"同步 Obsidian"按钮和同步状态展示
- 前端设置面板新增 Obsidian vault 路径配置项

## Capabilities

### New Capabilities

- `obsidian-sync`: Obsidian vault 到 RAG 知识库的增量同步能力——包含 ObsidianPreprocessor（Markdown 预处理管道）、ObsidianSyncService（增量同步引擎）、`.obsidianignore` 排除规则、同步 API 和同步状态报告

### Modified Capabilities

- `document-knowledge-base`: documents 表新增 `source_path` 和 `content_hash` 字段；DocumentService 新增 `list_tree()` 虚拟目录树方法；文档列表 API 新增路径过滤参数
- `persistence`: `user_settings` 表新增 `obsidian_vault_path` 字段
- `frontend`: 知识库视图重构为文件树浏览模式，设置面板新增 vault 路径配置

## Impact

- **后端新增文件**：`backend/app/services/obsidian_sync_service.py`、`backend/app/rag/obsidian_preprocessor.py`、`backend/app/api/obsidian.py`、`backend/app/schemas/obsidian.py`
- **后端修改文件**：`backend/app/db/models.py`（Document 加字段、UserSettings 加字段）、`backend/app/services/document_service.py`（加 `list_tree()`）、`backend/app/rag/splitter.py`（扩展 `_FENCE_RE`）、`backend/app/config.py`（加 obsidian 配置）、`backend/app/main.py`（初始化 + 注册路由）
- **前端修改文件**：`src/components/knowledge-library.tsx`（文件树视图重构）、`src/components/settings-panel.tsx`（vault 路径配置）、`src/shared/*.ts`（类型 + API 函数）
- **数据库**：`documents` 表加 2 列（`source_path`、`content_hash`）；`user_settings` 表加 1 列（`obsidian_vault_path`）；需迁移脚本
- **无新依赖**：复用现有 `pyyaml`（frontmatter 解析）、`pathspec`（.obsidianignore glob 匹配，若已有则复用，否则新增轻量依赖）
