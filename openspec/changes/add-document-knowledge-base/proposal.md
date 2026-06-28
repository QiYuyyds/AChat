## Why

AChat 已融合 AGI-memory 的 RAG 三路融合检索主干，但 Document+Version 文档管理体系尚未迁移。当前知识库是"黑洞"：入库后无法列出、查看、删除；文档与 chunk 无关联导致检索结果无来源追溯；上传与 RAG 脱节（Attachment 存文件但不解析，`rag_ingest` 只接受纯文本）；无版本管理导致旧 chunks 残留；无文件解析管道导致 PDF/Word 无法直接入库。补上此体系是完成知识库闭环的最后一块拼图，详见 `specs/18-document-knowledge-base.md`。

## What Changes

- 新增 `documents` + `document_versions` 两张 PostgreSQL 表，支持文档全生命周期管理（创建、版本链、软删除）
- 在现有 `rag_chunks` 表上增加 `document_id` / `version_id` 可选字段，建立文档 ↔ chunk 双向溯源
- 搬运 AGI-memory 的 Parser 管道（PDF 三级降级 pdfplumber → PyPDF2 → pdftotext + 编码检测 + OCR 需求检测）到 `backend/app/rag/parser.py`
- 新增 `DocumentService`（CRUD + 版本管理 + RAG 桥接）和 8 个 API 路由（list/create/get/versions/delete/ingest/upload）
- 新增前端"知识库"Tab，包含文档列表、上传对话框、版本历史详情页
- 可选升级 `rag_ingest` 工具支持 `document_id` 关联，新增 `rag_list_documents` / `rag_delete_document` 工具
- 新增 `pdfplumber`、`PyPDF2` 依赖

## Capabilities

### New Capabilities

- `document-knowledge-base`: 全局知识库文档的完整生命周期管理——Document+Version 数据模型、文件解析管道（Parser）、8 个文档管理 API、Document→RAG 桥接（入库回填 + 删除清理）、前端知识库视图（列表/上传/版本历史）

### Modified Capabilities

<!-- 无。RAG 的 HybridStore/Rewriter/Reranker/Splitter 全部不动；Attachment 系统保持独立；tools spec 不涉及 RAG 工具契约（rag_ingest 升级为可选实现细节）。 -->

## Impact

- **后端新增文件**：`backend/app/api/documents.py`、`backend/app/services/document_service.py`、`backend/app/rag/parser.py`、`backend/app/schemas/document.py`
- **后端修改文件**：`backend/app/db/models.py`（新增 Document/DocumentVersion 模型 + RagChunk 加字段）、`backend/app/main.py`（初始化 DocumentService + 注册路由）、`backend/requirements.txt`（新增依赖）
- **前端新增文件**：`src/components/knowledge-library.tsx`、`upload-document-dialog.tsx`、`document-detail.tsx`、`document-version-item.tsx`
- **前端修改文件**：`src/components/sidebar.tsx`（新增 knowledge Tab）、`src/shared/*.ts`（类型 + API 函数）
- **可选修改**：`backend/app/tools/memory_rag.py`（rag_ingest 升级）、`backend/app/tools/registry.py`（注册新工具）
- **依赖**：新增 `pdfplumber>=0.9.0`、`PyPDF2>=3.0.0`；系统级 `pdftotext`（poppler-utils）
- **数据库**：PostgreSQL 新增 2 张表 + 1 个索引 + rag_chunks 加 2 列；需迁移脚本
