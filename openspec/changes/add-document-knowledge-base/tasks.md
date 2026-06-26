## 1. 后端数据模型与依赖

- [x] 1.1 在 `backend/app/db/models.py` 新增 `Document` ORM 模型（id, title, doc_type, source, status, created_by, created_at, updated_at, latest_version, latest_version_id）
- [x] 1.2 在 `backend/app/db/models.py` 新增 `DocumentVersion` ORM 模型（id, document_id FK CASCADE, version, content_md, summary, metadata JSONB, created_at），含 `(document_id, version)` 唯一约束和 `idx_doc_versions_doc_id` 索引
- [x] 1.3 在 `backend/app/db/models.py` 的 `RagChunk` 模型上新增 `document_id`（可空，FK ON DELETE SET NULL）和 `version_id`（可空，FK ON DELETE SET NULL）字段
- [x] 1.4 在 `backend/requirements.txt` 新增 `pdfplumber>=0.9.0` 和 `PyPDF2>=3.0.0` 依赖并安装

## 2. Parser 管道

- [x] 2.1 创建 `backend/app/rag/parser.py`，实现 `ParseResult` dataclass（filename, content_type, parser, content, pages, text_chars, needs_ocr）
- [x] 2.2 实现 `parse_bytes(filename, content_type, data)` 入口函数，按 content_type/扩展名分发 PDF 与非 PDF 路径
- [x] 2.3 实现 `_parse_pdf()` 三级降级：`_extract_pdf_with_pdfplumber()` → `_extract_pdf_with_pypdf2()` → `_extract_pdf_with_pdftotext()`，每级 try/except 降级
- [x] 2.4 实现 OCR 需求检测：PDF 解析后 text_chars < 80 且 pages > 0 时 needs_ocr=true
- [x] 2.5 实现 `_decode_text()` 编码检测（UTF-8 → GBK → Latin-1）和 `_normalize_text()`（修复 \r\n、去 \x00、修复断行连字符、压缩空行）

## 3. Pydantic Schema 与 DocumentService

- [x] 3.1 创建 `backend/app/schemas/document.py`，定义 WriteRequest、WriteResult、IngestResult、UploadResult 及各 API 响应模型
- [x] 3.2 创建 `backend/app/services/document_service.py`，实现 `DocumentService.__init__(db, rag)` 注入
- [x] 3.3 实现 `list_documents()`：查询 status != 'deleted' 文档，JOIN 最新版本获取 metadata，按 updated_at DESC 排序
- [x] 3.4 实现 `write_document(req, ingest_to_rag)`：document_id 空→新建+version 1；非空→更新+version MAX+1；ingest_to_rag=true 时调用 RAGEngine.ingest 并回填 document_id/version_id
- [x] 3.5 实现 `get_document(id)`、`list_versions(id)`、`get_version(version_id)` 读取接口
- [x] 3.6 实现 `delete_document(id)`：软删除 status='deleted' + 遍历版本 doc_hash 清理 PG/ES/Milvus/Neo4j chunks，返回 deleted_chunks 数
- [x] 3.7 实现 `ingest_version(document_id, version_id)`：读取版本 content_md → RAGEngine.ingest → 回填溯源字段
- [x] 3.8 实现 `upload_file(filename, content_type, data)`：parse_bytes → needs_ocr 检查 → write_document(ingest_to_rag=true) 一条龙

## 4. API 路由与 main.py 集成

- [x] 4.1 创建 `backend/app/api/documents.py`，注册 8 个路由
- [x] 4.2 在 `backend/app/main.py` lifespan 中初始化 `_document_service = DocumentService(db=get_db, rag=_rag_service)`
- [x] 4.3 在 `backend/app/main.py` 注册 documents_router 到 `/api` 前缀
- [x] 4.4 验证后端启动后 /api/documents/* 路由可访问（语法检查通过）

## 5. 后端测试

- [x] 5.1 创建 `backend/tests/test_api_documents.py`，测试 GET/POST/DELETE /api/documents 基本流程
- [x] 5.2 测试版本管理：创建文档→更新→list_versions 返回多个版本且 version 递增
- [x] 5.3 测试 upload 一条龙：上传文本文件→解析→建文档→入库成功
- [x] 5.4 测试 OCR 需求检测：上传低文本量 PDF→needs_ocr=true→不建文档
- [x] 5.5 测试删除清理：删除文档→status='deleted'→rag_chunks 对应 doc_hash 被清理
- [x] 5.6 运行 `pytest backend/tests/test_api_documents.py` 全部通过

## 6. 前端类型与 API

- [x] 6.1 在 `src/shared/schema.ts`（或对应类型文件）新增 `DocumentRow`、`VersionRow`、`CreateDocumentRequest`、`WriteDocumentResponse`、`IngestResult`、`UploadResult` 类型定义
- [x] 6.2 在 `src/shared/api.ts`（或对应 API 文件）新增 `fetchDocuments()`、`createDocument()`、`getDocument()`、`listVersions()`、`deleteDocument()`、`ingestDocument()`、`uploadDocument()` 函数

## 7. 前端 Sidebar 与知识库组件

- [x] 7.1 在 `src/components/sidebar.tsx` 的 Mode 类型新增 `'knowledge'`，新增知识库 TabButton（BookOpen 图标）
- [x] 7.2 创建 `src/components/knowledge-library.tsx`：文档列表视图（搜索框 + 文档卡片列表 + 上传按钮），每个卡片显示标题/类型/版本/来源/字数/parser/页数/更新时间 + 重入库/删除按钮
- [x] 7.3 创建 `src/components/upload-document-dialog.tsx`：拖拽/选择文件 + 标题/类型输入 + 自动入库勾选 + 解析/入库状态展示
- [x] 7.4 创建 `src/components/document-detail.tsx`：返回按钮 + 文档元信息 + 版本历史列表（每条含查看内容/入库按钮）+ content_md 预览
- [x] 7.5 创建 `src/components/document-version-item.tsx`：版本条目（可展开内容 + 重入库按钮）
- [x] 7.6 在 `src/components/sidebar.tsx` 内容区集成：mode='knowledge' 时渲染 KnowledgeLibrary 组件
- [x] 7.7 验证前端知识库 Tab 切换、列表加载、上传、详情跳转交互正常

## 8. Agent 工具升级（可选）

- [x] 8.1 在 `backend/app/tools/memory_rag.py` 升级 `rag_ingest` 工具：新增可选 `title`、`doc_type`、`document_id` 参数，title 存在时走 DocumentService 创建管理文档
- [x] 8.2 在 `backend/app/tools/memory_rag.py` 新增 `rag_list_documents` 工具（无参数，列出所有文档）
- [x] 8.3 在 `backend/app/tools/memory_rag.py` 新增 `rag_delete_document` 工具（参数 document_id）
- [x] 8.4 在 `backend/app/tools/registry.py` 注册新增的 rag_list_documents 和 rag_delete_document 工具
- [x] 8.5 验证 Agent 可调用新工具完成文档列出/删除操作
