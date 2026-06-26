## Context

AgentHub 后端（Python/FastAPI/PostgreSQL）已融合 AGI-memory 的 RAG 三路 RRF 融合检索主干（Milvus 语义 + ES BM25 + Neo4j 知识图谱），`rag_ingest` 工具可将纯文本切分入库。但文档管理体系缺失：入库后无法列出/查看/删除，文档与 chunk 无关联，上传与 RAG 脱节，无版本管理，无文件解析管道。详见 `specs/18-document-knowledge-base.md` 的六个断裂点分析。

现有 Attachment 系统（会话级文件附件，存磁盘不解析）保持完全独立，不在本次改动范围。RAG 的 HybridStore/Rewriter/Reranker/Splitter 全部不动，Document 体系作为 RAG 的上游管理者接入。

## Goals / Non-Goals

**Goals:**
- 建立全局共享的 Document+Version 数据模型，支持文档全生命周期管理
- 实现文件解析管道（PDF 三级降级 + 编码检测 + OCR 需求检测），支持上传文件一条龙入库
- 建立 Document↔RAG 双向关联（入库回填 document_id/version_id，删除同步清理 chunks）
- 提供前端知识库管理界面（列表/上传/版本历史/重入库/删除）
- 保持 Attachment 系统与 RAG 系统独立运行

**Non-Goals:**
- 不改动 RAG 的 HybridStore、Rewriter、Reranker、Splitter 内部实现
- 不改动 Attachment 系统（会话级附件）
- 不改动 `rag_search` 工具（只管检索 chunks，不感知 Document）
- 不实现 OCR 实际执行（仅检测需求并提示用户）
- 不实现文档全文编辑器（仅预览 content_md）
- 不引入用户权限/多租户隔离（当前 local-first 单用户模型）

## Decisions

### 决策 1：独立 documents/document_versions 两表，而非 rag_chunks 元数据扩展

**选择**：新增 `documents` + `document_versions` 两张表管理文档元信息与版本内容，而非在 `rag_chunks` 上堆叠 title/version 等字段。

**理由**：rag_chunks 是切分后的检索单元，一个文档对应多个 chunk；将文档元信息放在 chunk 表会导致冗余存储且无法表达"未入库的文档"。两张表的 1:N 关系清晰映射"文档→版本→chunks"的领域模型，与 AGI-memory 原版一致。

**替代方案**：单表 `documents` 内嵌 content_md（无版本）——被否决，因为无法保留历史版本，更新后旧 chunks 残留。

### 决策 2：rag_chunks 增加 document_id/version_id 可选溯源字段，而非用 doc_hash 关联

**选择**：在 `rag_chunks` 上 ALTER ADD `document_id`/`version_id`（均可空），入库后批量回填。`doc_hash` 保留不动（RAG 内部去重/清理用）。

**理由**：`doc_hash` 是内容哈希，同一内容多次入库哈希相同，但可能对应不同文档/版本；`document_id`/`version_id` 是上层管理的稳定外键溯源。可空设计保证现有裸入库（无文档）的 chunks 不受影响。

### 决策 3：Parser 三级降级策略（pdfplumber → PyPDF2 → pdftotext）

**选择**：PDF 解析按精度依次尝试 pdfplumber（最精准，纯 Python+依赖）→ PyPDF2（纯 Python 兜底）→ pdftotext（系统命令兜底）。非 PDF 走编码检测（UTF-8 → GBK → Latin-1）+ 文本规范化。

**理由**：搬运自 AGI-memory 已验证的成熟策略。三级降级保证在不同环境（有/无系统级 poppler）下均有可用解析路径。OCR 仅检测需求（text_chars < 80 且 pages > 0），不实际执行，避免引入重依赖。

**替代方案**：只用 pdfplumber——被否决，因为部署环境可能缺库；引入 OCR 引擎（Tesseract）——被否决，依赖过重且非本次目标。

### 决策 4：软删除文档 + 按 doc_hash 清理 RAG chunks

**选择**：删除文档时先 `UPDATE documents SET status='deleted'`（软删除，保留版本历史可恢复），再遍历所有版本的 doc_hash 清理 PG/ES/Milvus/Neo4j 四路 chunks。不删除 document_versions 行。

**理由**：软删除支持误删恢复；按 doc_hash 清理与 RAGEngine 内部去重机制一致。版本行保留是为了审计与恢复，不占检索空间（chunks 已清）。

### 决策 5：上传一条龙 API（parse → write_document → ingest）

**选择**：`POST /api/documents/upload` 单次调用完成 解析→建文档→入库 RAG 全流程，OCR 需求时提前返回不建文档。

**理由**：降低前端集成复杂度——一次 multipart 上传即完成全部，无需前端编排三次调用。OCR 检测时提前返回 `success:false` + 提示，避免建空文档。

### 决策 6：前端知识库作为 Sidebar 新增独立 Tab

**选择**：在 Sidebar 的 Mode 类型新增 `'knowledge'`，与 conversations/artifacts/agents/analytics 平级。知识库视图包含文档列表、上传对话框、文档详情（版本历史）。

**理由**：知识库是全局共享资源（不绑会话），与 artifacts（会话级产物）定位不同，应独立入口。Tab 平级符合用户心智模型。

## Risks / Trade-offs

- **[Parser 依赖缺失导致降级]** → 三级降级策略覆盖；系统级 pdftotext 缺失时仍有 pdfplumber/PyPDF2 兜底；requirements.txt 显式声明版本。
- **[doc_hash 冲突：同内容不同文档]** → document_id/version_id 作为独立溯源字段，不依赖 doc_hash 做文档关联。
- **[删除文档时 Neo4j 清理失败]** → KGStore 清理在后台线程，失败不阻塞主删除流程；PG/ES/Milvus 清理同步完成。
- **[大文件上传解析超时]** → FastAPI 默认上传限制需调大；Parser 对大 PDF 逐页解析，pdfplumber 内存占用需监控。
- **[rag_chunks 加列需迁移现有数据]** → 新列可空 DEFAULT NULL，现有数据无需回填，迁移零停机。
- **[前端知识库列表性能]** → 文档量不大时直接全量返回；未来可加分页。

## Migration Plan

1. **Phase 1 后端核心**：新增 ORM 模型 → Parser → DocumentService → API 路由 → main.py 集成 → 依赖。数据库通过 SQLAlchemy `create_all` 建新表（开发期），生产环境需 Alembic 迁移脚本。rag_chunks 加列通过 ALTER TABLE（可空，零停机）。
2. **Phase 2 前端**：类型定义 → API 函数 → Sidebar Tab → 列表/上传/详情组件 → 集成。
3. **Phase 3 Agent 工具升级（可选）**：rag_ingest 加 document_id 参数 → 新增 rag_list_documents/rag_delete_document → 注册到 ToolRegistry。
4. **回滚策略**：Document 体系与现有系统解耦——停用 documents 路由即回滚到无文档管理状态；rag_chunks 的 document_id/version_id 为可空列，不影响现有检索。

## Open Questions

- 生产环境是否需要 Alembic 迁移脚本？（开发期 create_all 足够，生产需评估）
- 前端知识库列表是否需要分页？（当前文档量不大，暂不分页）
- OCR 实际执行是否纳入后续迭代？（本次仅检测需求，不执行）
