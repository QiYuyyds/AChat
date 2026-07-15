## MODIFIED Requirements

### Requirement: Document 数据模型 SHALL 持久化文档元信息与版本链

系统 MUST 新增 `documents` 和 `document_versions` 两张 PostgreSQL 表。`documents` 表存储文档元信息（id, title, doc_type, source, status, created_by, created_at, updated_at, latest_version, latest_version_id, source_path, content_hash）；`document_versions` 表存储版本内容（id, document_id, version, content_md, summary, metadata, created_at），通过 `document_id` 外键关联 documents 且 ON DELETE CASCADE。`document_versions` MUST 有 `(document_id, version)` 唯一约束和按 `version DESC` 的索引。`documents.source_path` 为 vault 内相对路径（如 `"Projects/AChat/架构设计.md"`），空字符串表示根目录。`documents.content_hash` 为文件内容 sha256[:16]，用于增量同步判断变化，非 Obsidian 文档可为 NULL。

#### Scenario: 新建文档生成 version 1
- **WHEN** 通过 API 创建一个新文档（document_id 为空）
- **THEN** 系统在 documents 表 INSERT 一行（status='active', latest_version=1）
- **AND** 在 document_versions 表 INSERT 一行（version=1，content_md 为提交内容）
- **AND** documents.latest_version_id 指向该版本行

#### Scenario: 更新已有文档生成新版本
- **WHEN** 通过 API 更新一个已存在的文档（document_id 非空）
- **THEN** 系统在 document_versions 表 INSERT 一行（version = 当前最大 version + 1）
- **AND** 更新 documents.latest_version 和 latest_version_id 指向新版本
- **AND** 旧版本行保留不动

#### Scenario: 删除文档时版本行级联保留
- **WHEN** 文档被软删除（status 置为 'deleted'）
- **THEN** document_versions 行不删除（保留历史可恢复）
- **AND** 关联的 RAG chunks 被清理

#### Scenario: Obsidian 同步的文档携带 source_path 和 content_hash
- **WHEN** ObsidianSyncService 创建一个新文档
- **THEN** documents.source_path 填入 vault 内相对路径（如 "Projects/design.md"）
- **AND** documents.content_hash 填入文件内容 sha256[:16]
- **AND** documents.source 填入 "obsidian_sync"

#### Scenario: 手动上传文档的 source_path 为空
- **WHEN** 用户通过上传 API 创建文档且未指定 source_path
- **THEN** documents.source_path 为空字符串（根目录）
- **AND** documents.content_hash 为 NULL

### Requirement: DocumentService SHALL 提供文档 CRUD 与版本管理

系统 MUST 实现 `DocumentService`，提供以下接口：`list_documents()`（列出所有 status != 'deleted' 的文档，按 updated_at DESC 排序，含最新版本元数据，包含所有来源）、`write_document(req, ingest_to_rag)`（创建或更新文档，更新即创建新版本，支持可选 `source_path` 和 `content_hash` 参数）、`get_document(id)`（返回文档+最新版本）、`list_versions(id)`（列出所有版本）、`get_version(version_id)`、`delete_document(id)`、`ingest_version(document_id, version_id)`、`upload_file(filename, content_type, data)`、`list_tree(path, user_id)`（返回指定路径下的虚拟目录树——只查询 `source='obsidian_sync'` 的文档，子文件夹列表 + 文件列表）、`list_flat(user_id, sources)`（返回指定来源的扁平文档列表，用于手动上传和 Agent 产物的展示）。

#### Scenario: 列出所有活跃文档
- **WHEN** 调用 list_documents()
- **THEN** 返回所有 status != 'deleted' 的文档
- **AND** 每个文档 JOIN 其最新版本，包含 latest_metadata（filename, parser, pages, text_chars, needs_ocr）
- **AND** 结果按 updated_at DESC 排序

#### Scenario: 读取文档最新版本
- **WHEN** 调用 get_document(id)
- **THEN** 返回文档元信息和 latest_version_id 对应的版本内容（含 content_md）

#### Scenario: 列出文档版本历史
- **WHEN** 调用 list_versions(id)
- **THEN** 返回该文档所有版本（id, version, summary, created_at），按 version DESC 排序

#### Scenario: 虚拟目录树只返回 obsidian_sync 文档
- **WHEN** 调用 list_tree(path="Projects/", user_id="user_1")
- **THEN** 返回该路径下的子文件夹列表（name, path, doc_count）和文件列表（id, title, source_path, doc_type, source, updated_at）
- **AND** 文件夹从 documents.source_path 派生（解析路径组件）
- **AND** 只返回 source='obsidian_sync' 的文档（手动上传和 Agent 产物不在此列表中）
- **AND** 只返回该用户的文档（user_id 过滤）
- **AND** 不返回 status='deleted' 的文档

#### Scenario: 虚拟目录树根路径
- **WHEN** 调用 list_tree(path="", user_id="user_1")
- **THEN** 返回根目录下的子文件夹和文件
- **AND** 只有 source='obsidian_sync' 的文档参与树构建
- **AND** source_path 为 "README.md" 的 obsidian_sync 文档出现在文件列表中
- **AND** source_path 为 "Projects/AChat/design.md" 的 obsidian_sync 文档，"Projects" 出现在文件夹列表中

#### Scenario: 扁平列表返回指定来源文档
- **WHEN** 调用 list_flat(user_id="user_1", sources=["user_upload", "agent_generated", "artifact_import"])
- **THEN** 返回这些来源的活跃文档列表（按 updated_at DESC 排序）
- **AND** 不包含 source='obsidian_sync' 的文档
- **AND** 每个文档包含 id, title, doc_type, source, updated_at, latest_version

### Requirement: 文档管理 API SHALL 暴露文档 CRUD 路由

系统 MUST 在 `/api/documents` 下注册路由：`GET /api/documents`（列出所有来源文档）、`POST /api/documents`（创建/更新）、`GET /api/documents/{id}`（读取）、`GET /api/documents/{id}/versions`（版本历史）、`GET /api/documents/{id}/versions/{ver_id}`（特定版本）、`DELETE /api/documents/{id}`（删除）、`POST /api/documents/{id}/ingest`（入库 RAG）、`POST /api/documents/upload`（上传一条龙）、`GET /api/documents/tree`（Obsidian 虚拟目录树，可选 `path` query 参数，只返回 `source='obsidian_sync'` 的文档）、`GET /api/documents/flat`（扁平列表，可选 `sources` query 参数过滤来源，默认返回 `user_upload` + `agent_generated` + `artifact_import`）。

#### Scenario: 列出文档 API 返回结构与类型
- **WHEN** GET /api/documents 被调用
- **THEN** 返回 `{ "documents": [ { id, title, doc_type, source, status, created_by, created_at, updated_at, latest_version, latest_version_id, source_path, content_hash, latest_metadata, latest_content_chars, latest_parser } ] }`

#### Scenario: 创建文档 API 可选入库
- **WHEN** POST /api/documents 收到 `{ document_id: "", title, doc_type, content_md, ingest_to_rag: true }`
- **THEN** 返回 `{ document: {...}, version: {...}, created: true, ingest: { chunk_count, doc_hash, indexed_count } }`
- **AND** 若 ingest_to_rag 为 false 或缺省，响应中不含 ingest 字段

#### Scenario: 删除文档 API 清理 RAG
- **WHEN** DELETE /api/documents/{id} 被调用
- **THEN** 文档 status 置为 'deleted'
- **AND** 关联 RAG chunks 被清理
- **THEN** 返回 `{ ok: true, deleted_chunks: <N> }`

#### Scenario: 虚拟目录树 API 只返回 Obsidian 文档
- **WHEN** GET /api/documents/tree?path=Projects/ 被调用
- **THEN** 返回 `{ "current_path": "Projects/", "folders": [...], "files": [...] }`
- **AND** folders 每项包含 { name, path, doc_count }
- **AND** files 每项包含 { id, title, source_path, doc_type, source, updated_at }
- **AND** 只返回 source='obsidian_sync' 的文档

#### Scenario: 扁平列表 API 返回非 Obsidian 文档
- **WHEN** GET /api/documents/flat 被调用（不带 sources 参数）
- **THEN** 返回 `{ "documents": [...] }`
- **AND** 只包含 source 为 user_upload、agent_generated、artifact_import 的文档
- **AND** 不包含 source='obsidian_sync' 的文档
- **AND** 按 updated_at DESC 排序

#### Scenario: 入库指定版本 API
- **WHEN** POST /api/documents/{id}/ingest 收到 `{ version_id }`
- **THEN** 读取该版本 content_md，调用 RAGEngine.ingest 切分入库
- **AND** 回填 chunks 的 document_id/version_id
- **THEN** 返回 `{ version_id, chunk_count, doc_hash }`
