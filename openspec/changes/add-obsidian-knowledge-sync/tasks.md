## 1. 数据库 Schema 变更

- [ ] 1.1 `backend/app/db/models.py`: Document 模型新增 `source_path` (String(1024), default="") 和 `content_hash` (String(64), nullable=True) 字段
- [ ] 1.2 `backend/app/db/models.py`: UserSettings 模型新增 `obsidian_vault_path` (String(1024), nullable=True) 字段
- [ ] 1.3 编写数据库迁移脚本：ALTER TABLE documents ADD COLUMN source_path / content_hash；ALTER TABLE user_settings ADD COLUMN obsidian_vault_path

## 2. Obsidian Markdown 预处理管道

- [ ] 2.1 创建 `backend/app/rag/obsidian_preprocessor.py`，实现 `ObsidianPreprocessor` 类骨架和 `process(content, vault_path)` 方法签名
- [ ] 2.2 实现 frontmatter 提取 step：解析 `---` YAML 块，提取 title/tags/aliases 到 metadata，从内容剥离
- [ ] 2.3 实现 transclusion 展开 step：`![[note]]` 递归展开 .md 内容（最大深度 2，visited set 环检测，环位置标记 `[circular: name]`），`![[image.png]]` 替换为 `[image: image.png]`
- [ ] 2.4 实现 wikilink 归一化 step：`[[Target]]` → `"Target"`，`[[Target|Alias]]` → `"Alias"`，收集 linked_entities 到 metadata
- [ ] 2.5 实现注释剥离 step：删除 `%%comment%%` 块
- [ ] 2.6 实现 dataview 剥离 step：删除 ` ```dataview ` 代码块
- [ ] 2.7 实现 callout 简化 step：`> [!type] Title\n> content` → `**Title**\ncontent`
- [ ] 2.8 实现 inline tag 提取 step：保留 `#tag` inline，额外收集到 metadata.tags
- [ ] 2.9 实现 highlight 简化 step：`==text==` → `**text**`
- [ ] 2.10 编写单元测试 `backend/tests/test_obsidian_preprocessor.py`，覆盖所有 step 和边界场景（环检测、深度限制、空 frontmatter、嵌套嵌入等）

## 3. Splitter Math Block 保护

- [ ] 3.1 `backend/app/rag/splitter.py`: 扩展 `_FENCE_RE` 正则，增加 `^\$\$.*?^\$\$\n?` 匹配（multiline + dotall），保护独占行 `$$...$$` 块
- [ ] 3.2 更新 splitter 测试，验证 math block 不被切碎且 inline `$x$` 不受影响

## 4. .obsidianignore 排除规则

- [ ] 4.1 确认 `pathspec` 是否已在项目依赖中；若没有，在 `backend/requirements.txt` 新增 `pathspec>=0.12.0`
- [ ] 4.2 在 `obsidian_sync_service.py` 中实现 `.obsidianignore` 加载逻辑：读取 vault 根目录 `.obsidianignore`，用 `pathspec.PathSpec.from_lines` 编译；默认排除 `.obsidian/` 和 `Templates/`，与用户规则取并集
- [ ] 4.3 实现文件过滤函数 `is_ignored(relative_path, spec)` — 匹配则跳过该文件
- [ ] 4.4 编写测试验证默认排除规则和自定义规则合并

## 5. ObsidianSyncService 增量同步引擎

- [ ] 5.1 创建 `backend/app/services/obsidian_sync_service.py`，实现 `ObsidianSyncService` 类骨架（注入 document_service 和 settings）
- [ ] 5.2 实现 `scan_vault(vault_path, ignore_spec)` 方法：递归遍历 vault 收集所有 .md 文件，返回 `[(relative_path, content_hash), ...]`
- [ ] 5.3 实现 `_diff_files(scanned, db_records)` 方法：对比扫描结果与 DB 记录（按 source_path 匹配），返回 added/updated/deleted/skipped 四类列表
- [ ] 5.4 实现 `_process_added(relative_path, content, vault_path, user_id)` 方法：ObsidianPreprocessor 处理 → DocumentService.write_document(source='obsidian_sync', source_path=relative_path, content_hash=hash, ingest_to_rag=True)
- [ ] 5.5 实现 `_process_updated(document_id, relative_path, content, vault_path, user_id)` 方法：预处理 → write_document（更新，创建新 version）→ 自动触发 ingest
- [ ] 5.6 实现 `_process_deleted(document_id)` 方法：DocumentService.delete_document（soft-delete + RAG chunks 清理）
- [ ] 5.7 实现 `sync_vault(vault_path, user_id)` 主方法：scan → diff → 逐个处理 → 收集 errors → 返回同步报告 dict { scanned, added, updated, deleted, skipped, errors }
- [ ] 5.8 实现 `get_status(user_id)` 方法：读取 user_settings.obsidian_vault_path，检查路径是否存在，统计 .md 文件数，返回上次同步信息
- [ ] 5.9 编写集成测试 `backend/tests/test_obsidian_sync.py`，覆盖首次同步、增量同步、删除同步、幂等性、错误处理

## 6. DocumentService 扩展

- [ ] 6.1 `backend/app/services/document_service.py`: `write_document()` 方法新增可选参数 `source_path: str = ""` 和 `content_hash: str | None = None`，写入 documents 表
- [ ] 6.2 实现 `list_tree(path: str, user_id: str)` 方法：查询 user_id 下 `source='obsidian_sync'` 且 source_path 以指定 path 开头的 active 文档，解析 source_path 派生子文件夹列表和文件列表，返回 { current_path, folders, files }
- [ ] 6.3 实现 `list_flat(user_id: str, sources: list[str])` 方法：查询指定来源（user_upload/agent_generated/artifact_import）的 active 文档，按 updated_at DESC 排序，返回扁平列表
- [ ] 6.4 更新 `_doc_to_dict()` 包含 `source_path` 和 `content_hash` 字段
- [ ] 6.5 编写 `list_tree` 单元测试，覆盖多层嵌套路径、空文件夹、根目录场景，验证只返回 obsidian_sync 文档
- [ ] 6.6 编写 `list_flat` 单元测试，验证只返回指定来源文档，obsidian_sync 文档不出现

## 7. API 路由

- [ ] 7.1 创建 `backend/app/schemas/obsidian.py`：Pydantic 模型（SyncRequest、SyncResponse、SyncStatus、SyncError）
- [ ] 7.2 创建 `backend/app/api/obsidian.py`：`POST /api/obsidian/sync` 路由（认证 → 读 vault_path → 调 sync_vault → 返回报告）
- [ ] 7.3 `backend/app/api/obsidian.py`: `GET /api/obsidian/status` 路由（返回 vault 配置 + 上次同步信息）
- [ ] 7.4 `backend/app/api/documents.py`: 新增 `GET /api/documents/tree` 路由（可选 `path` query 参数，调 list_tree，只返回 obsidian_sync 文档）
- [ ] 7.4b `backend/app/api/documents.py`: 新增 `GET /api/documents/flat` 路由（可选 `sources` query 参数，默认返回 user_upload + agent_generated + artifact_import，调 list_flat）
- [ ] 7.5 `backend/app/api/documents.py`: 更新 `POST /api/documents` 和 upload 路由，接受可选 `source_path` 参数
- [ ] 7.6 更新 `backend/app/api/settings.py`：支持 `obsidian_vault_path` 字段的读写
- [ ] 7.7 `backend/app/main.py`: lifespan 中初始化 `ObsidianSyncService`（注入 document_service），注册 obsidian 路由
- [ ] 7.8 编写 API 测试 `backend/tests/test_api_obsidian.py`

## 8. 配置

- [ ] 8.1 `backend/app/config.py`: 新增 `obsidian_max_embed_depth: int = 2`、`obsidian_default_ignore: list[str] = [".obsidian/", "Templates/"]` 配置项
- [ ] 8.2 `backend/.env.example`: 新增 `OBSIDIAN_MAX_EMBED_DEPTH=2` 注释说明

## 9. 前端——文件树视图

- [ ] 9.1 `src/shared/types.ts`（或对应类型文件）：新增 `DocumentTreeNode`、`FolderNode`、`FileNode`、`SyncReport`、`SyncStatus` 类型定义
- [ ] 9.2 `src/lib/api.ts`: 新增 `fetchDocumentTree(path?: string)`、`fetchDocumentFlat(sources?: string[])`、`syncObsidian()`、`getObsidianStatus()` API 函数
- [ ] 9.3 重构 `src/components/knowledge-library.tsx`：分为两个独立区域——"Obsidian Vault" 文件树视图（面包屑导航、文件夹展开/折叠）和"我的文档" 扁平列表视图（手动上传 + Agent 产物的文档，按更新时间排序），两个区域视觉分隔，不混合展示
- [ ] 9.4 新增"同步 Obsidian"按钮：点击调 `syncObsidian()`，loading 状态 + 成功 toast 展示同步摘要
- [ ] 9.5 新增同步状态展示区域：调用 `getObsidianStatus()`，显示 vault 路径、.md 文件总数、上次同步时间和摘要
- [ ] 9.6 Obsidian Vault 文件夹行显示文档数量，文件行显示标题、source、version、更新时间，保留"重入库"和"删除"操作按钮
- [ ] 9.7 我的文档列表行显示标题、source（upload/agent/artifact）、version、更新时间，保留"重入库"和"删除"操作按钮

## 10. 前端——设置面板

- [ ] 10.1 `src/components/settings-panel.tsx`（或对应组件）：新增 Obsidian vault 路径配置输入框
- [ ] 10.2 路径配置变化时调 `PATCH /api/settings` 保存 `obsidian_vault_path`
- [ ] 10.3 路径为空或无效时禁用知识库页面的"同步 Obsidian"按钮，并显示提示

## 11. 集成验证

- [ ] 11.1 端到端测试：配置 vault 路径 → 首次同步 → 知识库文件树展示 → rag_search 检索同步内容
- [ ] 11.2 端到端测试：修改 vault 文件 → 再次同步 → 验证增量更新（只有变化的文件重新入库）
- [ ] 11.3 端到端测试：删除 vault 文件 → 同步 → 验证文档被软删除且 RAG chunks 被清理
- [ ] 11.4 验证 .obsidianignore 排除规则生效
- [ ] 11.5 验证 Obsidian 特有语法（wikilink、transclusion、frontmatter）被正确预处理后入库
- [ ] 11.6 后端 `ruff check .` 和 `pytest` 全部通过
- [ ] 11.7 前端 `pnpm typecheck` 和 `pnpm lint` 全部通过
