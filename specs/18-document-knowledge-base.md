# Spec 18 — 文档管理与知识库

> 本 spec 定义 Document+Version 体系：全局知识库文档的生命周期管理（创建、版本、解析、入库、检索、删除），以及与现有 RAG 系统和 Attachment 系统的关系。**修改此文档需先讨论。**

源文件：`待融合项目/AGI-memory/internal/document/`、`待融合项目/AGI-memory/internal/repo/documentrepo.py`、`待融合项目/AGI-memory/internal/handler/handler.py`、`backend/app/rag/`、`backend/app/tools/memory_rag.py`、`backend/app/db/models.py`、`backend/app/api/attachments.py`、`backend/app/services/attachment_service.py`、`src/components/sidebar.tsx`、`src/components/attachment-chip.tsx`、`src/components/message-input.tsx`

---

## 1. 背景与问题

### 1.1 现状

AChat 已融合 AGI-memory 的 RAG 主干能力（三路 RRF 融合检索：Milvus 语义 + ES BM25 + Neo4j 知识图谱），但 **Document+Version 文档管理体系尚未迁移**。具体表现为：

| 能力 | AGI-memory 原版 | AChat 现状 |
|------|----------------|--------------|
| 文档生命周期管理 | `documents` + `document_versions` 两张表，支持 CRUD | 无 |
| 版本控制 | 每次更新创建新版本，保留历史 | 无 |
| 文件解析管道 | PDF 三级降级（pdfplumber → PyPDF2 → pdftotext）+ 编码检测 + OCR 需求检测 | 无，`rag_ingest` 工具只接受纯文本 |
| 文档 ↔ RAG 关联 | `document_versions` → `rag.ingest()` → chunks，可按 doc_hash 清理 | 无关联，`rag_ingest` 只传纯文本，无文档概念 |
| 文档管理 API | 6 个路由（list/create/get/delete/ingest/upload） | 无 |
| 知识库可见性 | `GET /api/documents` 列出所有文档及元信息 | 无，知识库是"黑洞" |

### 1.2 六个断裂点

1. **知识库不可管理** — 入库后无法列出、查看、删除
2. **文档 ↔ chunk 无关联** — 检索结果无来源追溯
3. **上传与 RAG 脱节** — Attachment 存文件但不解析不入库；`rag_ingest` 只接受纯文本
4. **无版本管理** — 文档更新后旧 chunks 残留，检索可能返回过时内容
5. **无文件解析管道** — PDF/Word 等格式无法直接入库
6. **Agent 无法管理知识** — Agent 只能 `rag_ingest` 写入，不能列出、更新、删除

### 1.3 Attachment vs Document 的定位区分

两者是**完全独立**的系统，不可混淆：

```
Attachment（会话级附件）
  • 绑定到 conversation_id，会话删除则级联删除
  • 原始文件存磁盘 uploads/{id}{ext}
  • 不解析内容，不入 RAG
  • 用途: 对话中分享文件（图片、PDF 下载等）
  • 表: attachments（已有，不动）

Document（全局知识库文档）
  • 不绑会话，全局存在，所有 Agent 共享
  • 内容解析为文本，切分入库 RAG
  • 有版本管理，每次更新创建新版本
  • 用途: 构建可检索的知识库
  • 表: documents + document_versions（新增）
```

---

## 2. 设计原则

1. **全局共享** — Document 独立于会话，所有 Agent 通过 `rag_search` 检索到
2. **版本链** — 每次内容更新创建新 version，保留历史，可回溯
3. **解析一条龙** — 上传文件 → 解析 → 建文档 → 入库 RAG，一个 API 调用完成
4. **文档 ↔ RAG 双向关联** — 文档可重新入库；删除文档时同步清理 RAG chunks
5. **Attachment 不动** — 保持会话级附件系统独立运行
6. **优雅降级** — Parser 三级降级；RAG 入库按基础设施可用性自动选择模式

---

## 3. 数据模型

### 3.1 新增表：documents

```sql
CREATE TABLE documents (
    id            VARCHAR(64) PRIMARY KEY,          -- doc_<hex16>
    title         VARCHAR(512) NOT NULL,
    doc_type      VARCHAR(64)  NOT NULL DEFAULT 'note',   -- note | report | spec | upload | ...
    source        VARCHAR(32)  NOT NULL DEFAULT 'agent_generated',  -- agent_generated | user_upload
    status        VARCHAR(16)  NOT NULL DEFAULT 'active',  -- active | deleted
    created_by    VARCHAR(64)  NOT NULL DEFAULT 'agent',
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    latest_version    INTEGER NOT NULL DEFAULT 0,
    latest_version_id VARCHAR(64) NOT NULL DEFAULT ''
);
```

### 3.2 新增表：document_versions

```sql
CREATE TABLE document_versions (
    id            VARCHAR(64) PRIMARY KEY,          -- ver_<hex16>
    document_id   VARCHAR(64) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version       INTEGER     NOT NULL,             -- 自增，从 1 开始
    content_md    TEXT        NOT NULL,             -- 解析后的 Markdown 文本
    summary       TEXT,                              -- 可选摘要
    metadata      JSONB       NOT NULL DEFAULT '{}', -- {filename, content_type, parser, pages, text_chars, needs_ocr}
    created_at    TIMESTAMP   NOT NULL DEFAULT NOW(),
    UNIQUE(document_id, version)
);
CREATE INDEX idx_doc_versions_doc_id ON document_versions(document_id, version DESC);
```

### 3.3 现有表修改：rag_chunks

在现有 `rag_chunks` 表上增加可选字段，建立文档关联：

```sql
ALTER TABLE rag_chunks ADD COLUMN document_id VARCHAR(64) DEFAULT NULL REFERENCES documents(id) ON DELETE SET NULL;
ALTER TABLE rag_chunks ADD COLUMN version_id   VARCHAR(64) DEFAULT NULL REFERENCES document_versions(id) ON DELETE SET NULL;
```

> `doc_hash` 保留不动（RAG 内部按 doc_hash 去重和清理），`document_id` / `version_id` 是上层管理用的溯源字段。

### 3.4 ORM 模型（SQLAlchemy）

```python
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False, default="note")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="agent_generated")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_version_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("idx_doc_versions_doc_id", "document_id", "version"),
        UniqueConstraint("document_id", "version"),
    )
```

---

## 4. API 设计

### 4.1 路由总览

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/documents` | 列出所有文档（含最新版本元数据） |
| `POST` | `/api/documents` | 创建或更新文档（可选 `ingest_to_rag`） |
| `GET` | `/api/documents/{id}` | 读取文档 + 最新版本 |
| `GET` | `/api/documents/{id}/versions` | 列出文档的所有版本 |
| `GET` | `/api/documents/{id}/versions/{ver_id}` | 读取特定版本内容 |
| `DELETE` | `/api/documents/{id}` | 删除文档 + 关联 RAG chunks |
| `POST` | `/api/documents/{id}/ingest` | 把指定版本入库到 RAG |
| `POST` | `/api/documents/upload` | 上传文件 → 解析 → 建文档 → 入库（一条龙） |

### 4.2 GET /api/documents — 列出文档

**响应：**
```json
{
  "documents": [
    {
      "id": "doc_a1b2c3d4",
      "title": "产品需求文档.pdf",
      "doc_type": "report",
      "source": "user_upload",
      "status": "active",
      "created_by": "user",
      "created_at": 1719302400.0,
      "updated_at": 1719388800.0,
      "latest_version": 3,
      "latest_version_id": "ver_e5f6g7h8",
      "latest_metadata": {
        "filename": "产品需求文档.pdf",
        "parser": "pdfplumber",
        "pages": 15,
        "text_chars": 12450,
        "needs_ocr": false
      },
      "latest_content_chars": 12450,
      "latest_parser": "pdfplumber"
    }
  ]
}
```

> 只返回 `status != 'deleted'` 的文档，按 `updated_at DESC` 排序。对每个文档 JOIN 最新版本获取 metadata。

### 4.3 POST /api/documents — 创建/更新文档

**请求体：**
```json
{
  "document_id": "",              // 空=新建，非空=更新（创建新版本）
  "title": "API设计规范",
  "doc_type": "spec",
  "source": "agent_generated",    // 可选，默认 agent_generated
  "created_by": "agent",          // 可选
  "content_md": "# API设计规范\n...",  // 必填
  "summary": "定义了 RESTful API 的命名和版本规范",  // 可选
  "metadata": {},                 // 可选
  "ingest_to_rag": true           // 可选，默认 false
}
}
```

**响应：**
```json
{
  "document": { "id": "doc_a1b2c3d4", "title": "API设计规范", ... },
  "version": { "id": "ver_e5f6g7h8", "version": 1, ... },
  "created": true,
  "ingest": {                      // 仅 ingest_to_rag=true 时存在
    "chunk_count": 42,
    "doc_hash": "a1b2c3d4e5f6g7h8",
    "indexed_count": 42
  }
}
```

**逻辑：**
- `document_id` 为空 → INSERT documents + INSERT document_versions (version=1)
- `document_id` 非空 → UPDATE documents + INSERT document_versions (version=MAX+1)
- `ingest_to_rag=true` → 调用 `RAGEngine.ingest(content_md)`，将 chunk 的 `document_id` / `version_id` 回填

### 4.4 GET /api/documents/{id} — 读取文档

**响应：**
```json
{
  "document": { "id": "doc_a1b2c3d4", ... },
  "version": { "id": "ver_e5f6g7h8", "version": 3, "content_md": "...", ... }
}
```

### 4.5 GET /api/documents/{id}/versions — 版本历史

**响应：**
```json
{
  "versions": [
    { "id": "ver_e5f6g7h8", "version": 3, "summary": "补充支付模块", "created_at": 1719388800.0 },
    { "id": "ver_i9j0k1l2", "version": 2, "summary": "重构认证流程", "created_at": 1719216000.0 },
    { "id": "ver_m3n4o5p6", "version": 1, "summary": "初始版本", "created_at": 1719043200.0 }
  ]
}
```

### 4.6 DELETE /api/documents/{id} — 删除文档

**逻辑：**
1. `UPDATE documents SET status = 'deleted'`（软删除）
2. 按 `doc_hash` 删除 RAG chunks（PG + Milvus + ES + Neo4j）
3. 不删除 `document_versions` 行（保留历史，可恢复）

**响应：**
```json
{ "ok": true, "deleted_chunks": 42 }
```

### 4.7 POST /api/documents/{id}/ingest — 入库到 RAG

**请求体：**
```json
{ "version_id": "ver_e5f6g7h8" }
```

**逻辑：**
1. 读取指定版本的 `content_md`
2. 调用 `RAGEngine.ingest(content_md)` 切分入库
3. 回填 chunk 的 `document_id` / `version_id`

**响应：**
```json
{
  "version_id": "ver_e5f6g7h8",
  "chunk_count": 42,
  "doc_hash": "a1b2c3d4e5f6g7h8"
}
```

### 4.8 POST /api/documents/upload — 上传文件（一条龙）

**请求：** `multipart/form-data`，字段 `file`

**逻辑：**
1. `parse_bytes(filename, content_type, data)` 解析文件
2. 如果 `needs_ocr=true` → 返回提示，不建文档
3. `write_document(WriteRequest(...))` 创建 Document + Version
4. 自动 `ingest_to_rag=true` → 入库 RAG
5. 返回解析元信息 + 文档信息 + 入库信息

**响应：**
```json
{
  "filename": "产品需求文档.pdf",
  "content_type": "application/pdf",
  "parser": "pdfplumber",
  "pages": 15,
  "text_chars": 12450,
  "needs_ocr": false,
  "chunk_count": 42,
  "doc_hash": "a1b2c3d4e5f6g7h8",
  "document": { "id": "doc_a1b2c3d4", ... },
  "version": { "id": "ver_e5f6g7h8", ... },
  "success": true
}
```

**OCR 需要时的响应：**
```json
{
  "filename": "扫描件.pdf",
  "needs_ocr": true,
  "pages": 10,
  "text_chars": 45,
  "chunk_count": 0,
  "message": "PDF 文本抽取结果过少，可能是扫描件，需要 OCR 后再入库",
  "success": false
}
```

---

## 5. Parser 管道

### 5.1 搬运自 AGI-memory

源文件：`待融合项目/AGI-memory/internal/document/parser.py`

文件 → `backend/app/rag/parser.py`（或 `backend/app/services/document_parser.py`）

### 5.2 解析策略

```
parse_bytes(filename, content_type, data)
  │
  ├── PDF? → _parse_pdf()
  │     ├── _extract_pdf_with_pdfplumber()  ← 最精准，需 pdfplumber 库
  │     ├── _extract_pdf_with_pypdf2()      ← 纯 Python 兜底，需 PyPDF2 库
  │     └── _extract_pdf_with_pdftotext()   ← 系统命令兜底，需 pdftotext 可执行
  │     → 如果 text_chars < 80 且 pages > 0 → needs_ocr = true
  │
  └── 非 PDF → _decode_text()
        ├── UTF-8
        ├── GBK
        └── Latin-1
        → _normalize_text(): 修复 \r\n, 去除 \x00, 修复断行连字符, 压缩空行
```

### 5.3 依赖

```
# requirements.txt 新增
pdfplumber>=0.9.0    # PDF 解析首选
PyPDF2>=3.0.0        # PDF 解析降级
# pdftotext 是系统级工具（poppler-utils），Docker 镜像内安装
```

### 5.4 ParseResult 结构

```python
@dataclass
class ParseResult:
    filename: str        # 原始文件名
    content_type: str    # MIME 类型
    parser: str          # "plain_text" | "pdfplumber" | "pdf_text" | "pdftotext"
    content: str         # 解析后的纯文本
    pages: int           # PDF 页数（非 PDF 为 0）
    text_chars: int      # 文本字符数
    needs_ocr: bool      # 是否需要 OCR
```

---

## 6. Document → RAG 桥接

### 6.1 入库流程

```
POST /api/documents/upload (file)
  │
  ▼
parse_bytes() → ParseResult.content
  │
  ▼
DocumentService.write_document()
  → INSERT documents + document_versions
  │
  ▼ (ingest_to_rag=true)
RAGEngine.ingest(content_md)
  │
  ├── parent_splitter.split() → parent chunks
  ├── child_splitter.split()  → child chunks (含 parent_content)
  ├── LLM embed(child.content) → embeddings
  └── HybridStore.index_with_parents(doc_hash, contents, parents, embeddings)
        ├── PG: rag_chunks 表 (doc_hash, chunk_idx, content, parent_content, embedding)
        │         + 回填 document_id, version_id
        ├── ES: index_es(pg_id, content, doc_hash, idx)
        ├── Milvus: insert_milvus(pg_ids, contents, embeddings)
        └── Neo4j: KGStore.index_document(doc_hash, chunks) [后台线程]
```

### 6.2 回填 document_id / version_id

在 `HybridStore.index_with_parents()` 返回 `pg_ids` 后，DocumentService 批量更新：

```python
# 伪代码
pg_ids = await hybrid.index_chunks(doc_hash, contents, parents, embeddings)
# 回填溯源字段
await db.execute(
    update(RagChunk)
    .where(RagChunk.id.in_(pg_ids))
    .values(document_id=document_id, version_id=version_id)
)
```

### 6.3 删除文档时清理 RAG

```python
# 伪代码
async def delete_document(document_id: str):
    # 1. 软删除文档
    await db.execute(update(Document).where(...).values(status="deleted"))
    # 2. 获取所有版本的 doc_hash
    versions = await get_all_versions(document_id)
    for ver in versions:
        doc_hash = hashlib.sha256(ver.content_md.encode()).hexdigest()[:16]
        # 3. 清理 PG rag_chunks
        await db.execute(delete(RagChunk).where(RagChunk.doc_hash == doc_hash))
        # 4. 清理 ES
        await es_delete_by_doc_hash(doc_hash)
        # 5. 清理 Milvus
        await milvus_delete_by_doc_hash(doc_hash)
        # 6. 清理 Neo4j
        if kg_store: await kg_store.delete_document(doc_hash)
```

---

## 7. 后端实现

### 7.1 新增文件

```
backend/app/
├── api/
│   └── documents.py              # 8 个 API 路由
├── services/
│   └── document_service.py       # 文档 CRUD + 版本管理 + RAG 桥接
├── rag/
│   └── parser.py                 # 文件解析管道（搬运自 AGI-memory）
├── db/
│   └── models.py                 # 新增 Document, DocumentVersion 模型
└── schemas/
    └── document.py               # Pydantic 请求/响应模型
```

### 7.2 DocumentService 核心接口

```python
class DocumentService:
    """文档库服务：CRUD + 版本管理 + RAG 桥接。"""

    async def list_documents(self) -> list[Document]:
        """列出所有 active 文档（含最新版本元数据）。"""

    async def write_document(self, req: WriteRequest, ingest_to_rag: bool) -> WriteResult:
        """创建或更新文档（创建新版本），可选入库 RAG。"""

    async def get_document(self, document_id: str) -> tuple[Document, DocumentVersion]:
        """读取文档 + 最新版本。"""

    async def list_versions(self, document_id: str) -> list[DocumentVersion]:
        """列出文档的所有版本。"""

    async def get_version(self, version_id: str) -> DocumentVersion:
        """读取特定版本。"""

    async def delete_document(self, document_id: str) -> int:
        """软删除文档 + 清理关联 RAG chunks。返回删除的 chunk 数。"""

    async def ingest_version(self, document_id: str, version_id: str) -> IngestResult:
        """把指定版本入库到 RAG。"""

    async def upload_file(self, filename: str, content_type: str, data: bytes) -> UploadResult:
        """上传文件 → 解析 → 建文档 → 入库（一条龙）。"""
```

### 7.3 修改现有文件

| 文件 | 修改内容 |
|------|---------|
| `backend/app/db/models.py` | 新增 `Document`, `DocumentVersion` 类；`RagChunk` 加 `document_id`, `version_id` 字段 |
| `backend/app/main.py` | 初始化 `DocumentService`，注册 `documents` 路由 |
| `backend/app/tools/memory_rag.py` | `rag_ingest` 工具可选升级：支持传 `document_id` 关联已有文档 |
| `backend/requirements.txt` | 新增 `pdfplumber`, `PyPDF2` 依赖 |

### 7.4 main.py 集成

```python
# 在 main.py 的 lifespan 中初始化
from app.services.document_service import DocumentService
from app.rag.parser import parse_bytes  # 注册路由时使用

_document_service: Optional[DocumentService] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _document_service
    # ... 已有的 _rag_service, _memory_service 初始化 ...
    _document_service = DocumentService(db=get_db, rag=_rag_service)
    yield
    # cleanup

# 注册路由
from app.api.documents import router as documents_router
app.include_router(documents_router, prefix="/api")
```

---

## 8. 前端实现

### 8.1 Sidebar 新增 Tab

当前 Sidebar 有 4 个 Tab：

```typescript
// 现有
type Mode = 'conversations' | 'artifacts' | 'agents' | 'analytics'

// 修改后
type Mode = 'conversations' | 'artifacts' | 'knowledge' | 'agents' | 'analytics'
//                                          ^^^^^^^^^ 新增
```

新增 TabButton：
```tsx
<TabButton
  mode={mode}
  self="knowledge"
  collapsed={collapsed}
  onClick={() => setMode('knowledge')}
  icon={<BookOpen className="size-4" />}
  label="知识库"
/>
```

### 8.2 新增组件

```
src/components/
├── knowledge-library.tsx          # 知识库主视图（文档列表）
├── upload-document-dialog.tsx     # 上传文档对话框
├── document-detail.tsx            # 文档详情（版本历史 + 内容预览）
└── document-version-item.tsx      # 版本条目（可展开内容 + 重入库按钮）
```

### 8.3 知识库列表视图（knowledge-library.tsx）

```
┌─────────────────────────────────────────────────────────────┐
│  知识库                                          [上传文档]  │
│  ─────────────────────────────────────────────────────────  │
│                                                             │
│  [搜索框: 过滤文档标题]                                     │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 📄 产品需求文档.pdf               report  | ver 3     │  │
│  │    user_upload | 12,450字 | pdfplumber | 15页         │  │
│  │    更新: 2026-06-25                   [重入库] [删除]  │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ 📝 API设计规范.md                 note    | ver 1     │  │
│  │    agent_generated | 3,200字 | plain_text             │  │
│  │    更新: 2026-06-24                   [重入库] [删除]  │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ 📊 用户调研报告.pdf               report  | ver 2     │  │
│  │    user_upload | 8,100字 | needs_ocr ⚠               │  │
│  │    更新: 2026-06-20                   [重入库] [删除]  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

点击文档标题 → 展开 `document-detail.tsx`（版本历史）

### 8.4 上传文档对话框（upload-document-dialog.tsx）

```
┌───────────────────────────────────────┐
│  上传文档到知识库                      │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │     拖拽文件到此处 / 点击选择     │  │
│  │     支持 PDF / Markdown / TXT    │  │
│  └─────────────────────────────────┘  │
│                                       │
│  标题: [________________________]     │
│  类型: [report ▾]                     │
│  ☑ 上传后自动入库到 RAG               │
│                                       │
│  上传状态:                             │
│  ✅ 解析成功: pdfplumber, 15页, 12450字│
│  ✅ 入库成功: 42 chunks                │
│  (或 ⚠️ 需要OCR, 跳过入库)            │
│                                       │
│              [关闭]                    │
└───────────────────────────────────────┘
```

### 8.5 文档详情视图（document-detail.tsx）

```
┌─────────────────────────────────────────────────────────────┐
│  ← 返回列表    产品需求文档.pdf                              │
│  ─────────────────────────────────────────────────────────  │
│  类型: report    来源: user_upload    创建: 2026-06-20       │
│                                                             │
│  版本历史:                                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ ver 3 (当前)  2026-06-25  "补充了支付模块需求"        │  │
│  │   parser: pdfplumber | 12,450字 | 15页                │  │
│  │   [查看内容] [入库到RAG]                               │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ ver 2        2026-06-23  "重构了用户认证流程"         │  │
│  │   parser: pdfplumber | 10,200字 | 12页                │  │
│  │   [查看内容] [入库到RAG]                               │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ ver 1        2026-06-20  "初始版本"                   │  │
│  │   parser: pdfplumber | 8,000字 | 10页                 │  │
│  │   [查看内容] [入库到RAG]                               │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  内容预览 (ver 3):                                          │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ # 产品需求文档                                        │  │
│  │ ## 1. 概述                                            │  │
│  │ 本文档定义了...                                       │  │
│  │ ## 2. 支付模块                                        │  │
│  │ ...                                                   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 8.6 API 函数（api.ts 新增）

```typescript
// 文档管理 API
export async function fetchDocuments(): Promise<DocumentRow[]>
export async function createDocument(req: CreateDocumentRequest): Promise<WriteDocumentResponse>
export async function getDocument(id: string): Promise<{ document: DocumentRow; version: VersionRow }>
export async function listVersions(id: string): Promise<VersionRow[]>
export async function deleteDocument(id: string): Promise<{ ok: boolean; deletedChunks: number }>
export async function ingestDocument(id: string, versionId: string): Promise<IngestResult>
export async function uploadDocument(file: File, opts?: { title?: string; docType?: string }): Promise<UploadResult>
```

### 8.7 类型定义（schema.ts 新增）

```typescript
interface DocumentRow {
  id: string
  title: string
  docType: string
  source: 'agent_generated' | 'user_upload'
  status: 'active' | 'deleted'
  createdBy: string
  createdAt: number
  updatedAt: number
  latestVersion: number
  latestVersionId: string
  latestMetadata?: {
    filename?: string
    parser?: string
    pages?: number
    textChars?: number
    needsOcr?: boolean
  }
}

interface VersionRow {
  id: string
  documentId: string
  version: number
  contentMd: string
  summary?: string
  metadata: Record<string, any>
  createdAt: number
}
```

---

## 9. Agent 工具升级（可选）

### 9.1 现有 rag_ingest 工具

当前 `rag_ingest` 只接受纯文本：

```python
rag_ingest_tool = ToolDef(
    name="rag_ingest",
    description="Ingest a document into the knowledge base...",
    parameters={
        "type": "object",
        "properties": {
            "document": { "type": "string", "description": "The document content..." }
        },
        "required": ["document"],
    },
    handler=rag_ingest_handler,
)
```

### 9.2 升级方案

增加可选的 `title`、`doc_type`、`document_id` 参数：

```python
rag_ingest_tool = ToolDef(
    name="rag_ingest",
    description="Ingest a document into the knowledge base. "
                "If document_id is provided, creates a new version of an existing document; "
                "otherwise creates a new document.",
    parameters={
        "type": "object",
        "properties": {
            "document": { "type": "string", "description": "The document content to ingest." },
            "title": { "type": "string", "description": "Document title (creates a managed document)." },
            "doc_type": { "type": "string", "description": "Document type: note, report, spec, etc." },
            "document_id": { "type": "string", "description": "Existing document ID to create a new version." },
        },
        "required": ["document"],
    },
    handler=rag_ingest_handler,
)
```

如果 `title` 存在 → 走 DocumentService 创建管理文档 + 入库；
如果只有 `document` → 保持现有行为（裸入库，无文档管理）。

### 9.3 新增 Agent 工具（可选）

```python
rag_list_documents_tool = ToolDef(
    name="rag_list_documents",
    description="List all documents in the knowledge base with their latest version info.",
    parameters={"type": "object", "properties": {}},
    handler=rag_list_documents_handler,
)

rag_delete_document_tool = ToolDef(
    name="rag_delete_document",
    description="Delete a document and its associated RAG chunks from the knowledge base.",
    parameters={
        "type": "object",
        "properties": {
            "document_id": { "type": "string", "description": "The document ID to delete." }
        },
        "required": ["document_id"],
    },
    handler=rag_delete_document_handler,
)
```

---

## 10. 实施任务

### Phase 1: 后端核心

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | 新增 `Document`, `DocumentVersion` ORM 模型 | `models.py` | - |
| 2 | `RagChunk` 模型加 `document_id`, `version_id` 字段 | `models.py` | 1 |
| 3 | 搬运 Parser 管道 | `rag/parser.py` | - |
| 4 | 新增 Pydantic schema | `schemas/document.py` | 1 |
| 5 | 实现 `DocumentService` | `services/document_service.py` | 1,3 |
| 6 | 实现 API 路由 | `api/documents.py` | 5 |
| 7 | `main.py` 集成初始化 + 路由注册 | `main.py` | 6 |
| 8 | 新增依赖 | `requirements.txt` | 3 |
| 9 | 编写测试 | `tests/test_api_documents.py` | 7 |

### Phase 2: 前端

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 10 | 类型定义 | `schema.ts` | - |
| 11 | API 函数 | `api.ts` | 10 |
| 12 | Sidebar 新增 Tab | `sidebar.tsx` | - |
| 13 | 知识库列表组件 | `knowledge-library.tsx` | 11,12 |
| 14 | 上传文档对话框 | `upload-document-dialog.tsx` | 11 |
| 15 | 文档详情组件 | `document-detail.tsx` | 11 |
| 16 | 集成到 Sidebar 内容区 | `sidebar.tsx` | 13,14,15 |

### Phase 3: Agent 工具升级（可选）

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 17 | `rag_ingest` 工具升级支持 `document_id` | `tools/memory_rag.py` | 5 |
| 18 | 新增 `rag_list_documents` 工具 | `tools/memory_rag.py` | 5 |
| 19 | 新增 `rag_delete_document` 工具 | `tools/memory_rag.py` | 5 |
| 20 | 注册新工具到 ToolRegistry | `tools/registry.py` | 17,18,19 |

---

## 11. 与现有系统的关系

### 11.1 Attachment 系统

**不动。** Attachment 是会话级文件附件，与 Document 体系完全独立：

```
用户在对话中上传图片 → Attachment（存磁盘，显示在消息气泡）
用户在知识库上传 PDF  → Document（解析，切分，入库 RAG）
```

两者可以共存于同一个应用中，互不干扰。

### 11.2 RAG 系统

Document 是 RAG 的**上游管理者**：

```
Document (管理文档生命周期)
  → RAGEngine.ingest (切分 + 向量化)
    → HybridStore (三路入库: PG/Milvus/ES/Neo4j)
      → rag_search (三路 RRF 融合检索)
```

- `rag_search` 工具不需要改动，它只管检索 chunks
- `rag_ingest` 工具可选升级（Phase 3），支持关联到 Document
- RAG 的 HybridStore、Rewriter、Reranker、Splitter 全部不动

### 11.3 Memory 系统

Document 与 Memory 是**并行的知识层**：

```
Document → RAG chunks (显式上传的文档知识)
Memory   → LongTerm/Preference/Graph (Agent 运行时积累的记忆)
```

`rag_search` 搜的是 Document 入库的 chunks；
`memory_recall` 搜的是 Agent 运行时写入的长期记忆。
两者互不干扰，Agent 可以同时使用。

---

## 12. 数据流总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        完整数据流                                    │
│                                                                     │
│  ┌──────────┐                                                      │
│  │ 前端      │                                                      │
│  │           │                                                      │
│  │ 知识库Tab │                                                      │
│  │ [上传文档]│──POST /api/documents/upload (file)──▶ 后端            │
│  │ [文档列表]│◀──GET /api/documents───────────────── 后端            │
│  │ [版本历史]│◀──GET /api/documents/{id}/versions── 后端            │
│  │ [重入库]  │──POST /api/documents/{id}/ingest───▶ 后端            │
│  │ [删除]    │──DELETE /api/documents/{id}─────────▶ 后端            │
│  └──────────┘                                                      │
│                                                                     │
│  ┌──────────┐                                                      │
│  │ 对话Tab   │                                                      │
│  │ 用户提问  │──POST /api/messages─────────────────▶ 后端            │
│  │           │    Agent 调用 rag_search(query)                      │
│  │           │    ← 返回答案 + 来源 chunks                           │
│  │ 📎附件    │──POST /attachments────────────────────▶ 后端 (独立)   │
│  └──────────┘                                                      │
│                                                                     │
│  ════════════════════════ 后端 ═══════════════════════════════       │
│                                                                     │
│  ┌──────────────────┐     ┌──────────────────┐     ┌─────────────┐  │
│  │ DocumentService   │    │ RAGEngine        │     │ HybridStore │  │
│  │                   │    │                  │     │             │  │
│  │ • write_document  │───▶│ • ingest()       │────▶│ • PG index  │  │
│  │ • delete_document │    │ • query()        │     │ • ES index  │  │
│  │ • ingest_version  │───▶│ • _compose_answer│     │ • Milvus    │  │
│  │ • upload_file     │    │                  │     │ • Neo4j KG  │  │
│  └──────────────────┘     └──────────────────┘     └─────────────┘  │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────┐     ┌──────────────────┐                  │
│  │ documents 表      │    │ document_versions │                  │
│  │ (文档元信息)      │─1:N▶│ (版本+内容)       │                  │
│  └──────────────────┘     └────────┬─────────┘                  │
│                                    │                              │
│                           doc_hash + document_id                  │
│                           + version_id                             │
│                                    ▼                              │
│                          ┌─────────────┐                         │
│                          │  rag_chunks │                         │
│                          │ (切分+向量)  │                         │
│                          └─────────────┘                         │
│                                                                    │
│  ┌──────────────────┐                                            │
│  │ AttachmentService │ ← 完全独立，不与 Document 交互              │
│  │ attachments 表    │                                            │
│  └──────────────────┘                                            │
└─────────────────────────────────────────────────────────────────────┘
```
