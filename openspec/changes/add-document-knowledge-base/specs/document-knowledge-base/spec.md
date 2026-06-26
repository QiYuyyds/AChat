## ADDED Requirements

### Requirement: Document 数据模型 SHALL 持久化文档元信息与版本链

系统 MUST 新增 `documents` 和 `document_versions` 两张 PostgreSQL 表。`documents` 表存储文档元信息（id, title, doc_type, source, status, created_by, created_at, updated_at, latest_version, latest_version_id）；`document_versions` 表存储版本内容（id, document_id, version, content_md, summary, metadata, created_at），通过 `document_id` 外键关联 documents 且 ON DELETE CASCADE。`document_versions` MUST 有 `(document_id, version)` 唯一约束和按 `version DESC` 的索引。

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

### Requirement: rag_chunks SHALL 增加可选文档溯源字段

系统 MUST 在现有 `rag_chunks` 表上增加 `document_id`（VARCHAR(64)，DEFAULT NULL，REFERENCES documents(id) ON DELETE SET NULL）和 `version_id`（VARCHAR(64)，DEFAULT NULL，REFERENCES document_versions(id) ON DELETE SET NULL）两个可空字段。`doc_hash` 字段 MUST 保留不动。

#### Scenario: 裸入库的 chunks 无文档关联
- **WHEN** 通过 rag_ingest 工具直接入库纯文本（未关联 Document）
- **THEN** rag_chunks 行的 document_id 和 version_id 为 NULL
- **AND** 现有检索行为不受影响

#### Scenario: 文档入库的 chunks 回填溯源字段
- **WHEN** 通过 DocumentService 入库某文档版本到 RAG
- **THEN** 生成的 rag_chunks 行的 document_id 和 version_id 被回填为对应文档和版本 ID

### Requirement: DocumentService SHALL 提供文档 CRUD 与版本管理

系统 MUST 实现 `DocumentService`，提供以下接口：`list_documents()`（列出所有 status != 'deleted' 的文档，按 updated_at DESC 排序，含最新版本元数据）、`write_document(req, ingest_to_rag)`（创建或更新文档，更新即创建新版本）、`get_document(id)`（返回文档+最新版本）、`list_versions(id)`（列出所有版本）、`get_version(version_id)`、`delete_document(id)`、`ingest_version(document_id, version_id)`、`upload_file(filename, content_type, data)`。

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

### Requirement: 文档管理 API SHALL 暴露 8 个 REST 路由

系统 MUST 在 `/api/documents` 下注册 8 个路由：`GET /api/documents`（列出）、`POST /api/documents`（创建/更新）、`GET /api/documents/{id}`（读取）、`GET /api/documents/{id}/versions`（版本历史）、`GET /api/documents/{id}/versions/{ver_id}`（特定版本）、`DELETE /api/documents/{id}`（删除）、`POST /api/documents/{id}/ingest`（入库 RAG）、`POST /api/documents/upload`（上传一条龙）。

#### Scenario: 列出文档 API 返回结构与类型
- **WHEN** GET /api/documents 被调用
- **THEN** 返回 `{ "documents": [ { id, title, doc_type, source, status, created_by, created_at, updated_at, latest_version, latest_version_id, latest_metadata, latest_content_chars, latest_parser } ] }`

#### Scenario: 创建文档 API 可选入库
- **WHEN** POST /api/documents 收到 `{ document_id: "", title, doc_type, content_md, ingest_to_rag: true }`
- **THEN** 返回 `{ document: {...}, version: {...}, created: true, ingest: { chunk_count, doc_hash, indexed_count } }`
- **AND** 若 ingest_to_rag 为 false 或缺省，响应中不含 ingest 字段

#### Scenario: 删除文档 API 清理 RAG
- **WHEN** DELETE /api/documents/{id} 被调用
- **THEN** 文档 status 置为 'deleted'
- **AND** 关联 RAG chunks 被清理
- **THEN** 返回 `{ ok: true, deleted_chunks: <N> }`

#### Scenario: 入库指定版本 API
- **WHEN** POST /api/documents/{id}/ingest 收到 `{ version_id }`
- **THEN** 读取该版本 content_md，调用 RAGEngine.ingest 切分入库
- **AND** 回填 chunks 的 document_id/version_id
- **THEN** 返回 `{ version_id, chunk_count, doc_hash }`

### Requirement: Parser 管道 SHALL 三级降级解析 PDF 并检测 OCR 需求

系统 MUST 实现 `parse_bytes(filename, content_type, data)` 函数，返回 `ParseResult`（filename, content_type, parser, content, pages, text_chars, needs_ocr）。PDF 解析按精度依次尝试 pdfplumber → PyPDF2 → pdftotext；非 PDF 走编码检测（UTF-8 → GBK → Latin-1）+ 文本规范化（修复 \r\n、去 \x00、修复断行连字符、压缩空行）。当 PDF 的 text_chars < 80 且 pages > 0 时，needs_ocr MUST 置为 true。

#### Scenario: pdfplumber 成功解析 PDF
- **WHEN** parse_bytes 收到一个 PDF 文件且 pdfplumber 可用
- **THEN** parser 字段为 "pdfplumber"
- **AND** content 为提取的文本，pages 为页数，text_chars 为字符数，needs_ocr 为 false

#### Scenario: pdfplumber 缺失时降级到 PyPDF2
- **WHEN** pdfplumber 不可用但 PyPDF2 可用
- **THEN** 使用 PyPDF2 解析，parser 字段为 "pdf_text"

#### Scenario: 扫描件 PDF 触发 OCR 需求检测
- **WHEN** PDF 解析后 text_chars < 80 且 pages > 0
- **THEN** needs_ocr 置为 true
- **AND** content 为提取到的少量文本

#### Scenario: 非 PDF 文件编码检测
- **WHEN** parse_bytes 收到一个 .md 文件（UTF-8 编码）
- **THEN** parser 字段为 "plain_text"
- **AND** content 为解码并规范化后的文本，pages 为 0

#### Scenario: GBK 编码文本文件降级解码
- **WHEN** parse_bytes 收到一个 GBK 编码的 .txt 文件
- **THEN** UTF-8 解码失败后尝试 GBK 成功
- **AND** content 为正确解码的文本

### Requirement: 上传文件 SHALL 一条龙完成解析建文档入库

`POST /api/documents/upload` 接收 multipart/form-data（字段 file），MUST 依次执行：parse_bytes → 判断 needs_ocr → write_document → ingest_to_rag=true。若 needs_ocr=true 则提前返回 success=false 且不建文档。

#### Scenario: 上传 PDF 一条龙成功
- **WHEN** POST /api/documents/upload 收到一个 PDF 文件
- **AND** 解析成功且 needs_ocr=false
- **THEN** 创建 Document + Version（source='user_upload'）
- **AND** 自动入库 RAG 并回填溯源字段
- **THEN** 返回 `{ filename, content_type, parser, pages, text_chars, needs_ocr: false, chunk_count, doc_hash, document, version, success: true }`

#### Scenario: 上传扫描件 PDF 需 OCR
- **WHEN** 上传的 PDF 解析后 needs_ocr=true
- **THEN** 不创建文档，不入库
- **AND** 返回 `{ filename, needs_ocr: true, pages, text_chars, chunk_count: 0, message: "PDF 文本抽取结果过少，可能是扫描件，需要 OCR 后再入库", success: false }`

### Requirement: 删除文档 SHALL 同步清理四路 RAG chunks

`delete_document(document_id)` MUST 先软删除文档（status='deleted'），再遍历所有版本的 doc_hash，按 doc_hash 清理 PG rag_chunks + ES 索引 + Milvus 向量 + Neo4j 知识图谱。document_versions 行 MUST 保留。

#### Scenario: 删除文档清理 PG 和 ES
- **WHEN** 删除一个已入库的文档
- **THEN** documents.status 置为 'deleted'
- **AND** PG rag_chunks 中对应 doc_hash 的行被删除
- **AND** ES 中对应 doc_hash 的索引被删除

#### Scenario: 删除文档清理 Milvus 和 Neo4j
- **WHEN** 删除一个已入库的文档
- **THEN** Milvus 中对应 doc_hash 的向量被删除
- **AND** Neo4j KGStore 中对应 doc_hash 的知识图谱节点被删除

#### Scenario: 软删除后版本历史仍可查询
- **WHEN** 文档被软删除后调用 list_versions
- **THEN** 仍返回该文档的所有版本行（未被物理删除）

### Requirement: 前端 Sidebar SHALL 新增知识库 Tab

前端 Sidebar 的 Mode 类型 MUST 新增 `'knowledge'`，与 conversations/artifacts/agents/analytics 平级。知识库 Tab MUST 包含文档列表视图、上传文档对话框、文档详情（版本历史）视图。

#### Scenario: 切换到知识库 Tab
- **WHEN** 用户点击 Sidebar 中的"知识库"TabButton
- **THEN** Sidebar 内容区切换为知识库列表视图
- **AND** 显示所有文档及其最新版本元信息

#### Scenario: 知识库列表展示文档元信息
- **WHEN** 知识库列表视图加载
- **THEN** 每个文档卡片显示标题、doc_type、version 号、source、字数、parser、页数、更新时间
- **AND** 每个卡片有"重入库"和"删除"操作按钮

#### Scenario: 上传文档对话框
- **WHEN** 用户点击"上传文档"按钮
- **THEN** 弹出对话框支持拖拽/选择文件（PDF/Markdown/TXT）
- **AND** 可填标题、选类型、勾选自动入库
- **AND** 上传后显示解析结果（parser/页数/字数）和入库结果（chunk 数）

#### Scenario: 文档详情展示版本历史
- **WHEN** 用户点击文档标题
- **THEN** 切换到文档详情视图，展示版本历史列表
- **AND** 每个版本条目显示 version 号、创建时间、summary、parser、字数、页数
- **AND** 每个版本有"查看内容"和"入库到RAG"按钮
- **AND** 底部展示当前版本 content_md 预览

### Requirement: Document 体系 SHALL 与 Attachment 系统完全独立

Document（全局知识库文档）与 Attachment（会话级附件）MUST 保持完全独立。Attachment 表不动，不与 documents/document_versions 交互；Document 不绑会话，全局共享。

#### Scenario: 对话中上传图片走 Attachment
- **WHEN** 用户在对话中上传一张图片
- **THEN** 走现有 Attachment 系统（存磁盘，显示在消息气泡），不创建 Document

#### Scenario: 知识库上传 PDF 走 Document
- **WHEN** 用户在知识库 Tab 上传一个 PDF
- **THEN** 走 Document 体系（解析→建文档→入库 RAG），不创建 Attachment

### Requirement: DocumentService SHALL 在 main.py 生命周期中初始化

`backend/app/main.py` 的 lifespan 中 MUST 初始化 `DocumentService`（注入 db 和 rag_service），并注册 documents 路由到 `/api` 前缀。

#### Scenario: 后端启动时 DocumentService 就绪
- **WHEN** FastAPI 应用启动（lifespan 执行）
- **THEN** DocumentService 被实例化并持有 db 和 rag_service 引用
- **AND** /api/documents/* 路由可访问
