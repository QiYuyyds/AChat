## 1. Document 模型扩展

- [x] 1.1 `backend/app/db/models.py`: `Document` 新增 `parent_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("documents.id", ondelete="SET NULL"), name="parent_id", nullable=True)`
- [x] 1.2 `backend/app/db/models.py`: `Document` 新增 `is_folder: Mapped[bool] = mapped_column(Boolean, name="is_folder", nullable=False, default=False)`
- [x] 1.3 `backend/app/db/models.py`: `__table_args__` 新增 `Index("idx_documents_parent_id", "parent_id")`
- [x] 1.4 `backend/app/db/migrations/rag_overhaul_migration.py`: 追加 `ALTER TABLE documents ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64)` + `ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_folder BOOLEAN DEFAULT FALSE`
- [x] 1.5 迁移脚本追加回填：`UPDATE documents SET is_folder = FALSE WHERE is_folder IS NULL`

## 2. ParseResult 图像字段

- [x] 2.1 `backend/app/rag/parsers/base.py`: 新增 `ExtractedImage` dataclass（`filename: str`、`data: bytes`、`content_type: str`）
- [x] 2.2 `backend/app/rag/parsers/base.py`: `ParseResult` 新增 `images: list[ExtractedImage] = field(default_factory=list)` 字段
- [x] 2.3 `backend/app/rag/parser.py`: `DocumentProcessor.process_file()` 在解析 DOCX 时提取内嵌图片（`python-docx` 的 `document.inline_shapes`）
- [x] 2.4 `backend/app/rag/parser.py`: `DocumentProcessor.process_file()` 在解析 PPTX 时提取幻灯片图片（`python-pptx` 的 `slide.shapes` 中 `Picture` 类型）
- [x] 2.5 `backend/app/rag/parser.py`: `DocumentProcessor.process_file()` 在解析 PDF 时提取图片（`pdfplumber` 的 `page.images`），受 `rag_extract_images` 配置控制

## 3. 图像写入 workspace

- [x] 3.1 `backend/app/config.py`: 新增 `rag_extract_images: bool = True` 配置项
- [x] 3.2 `backend/app/services/document_service.py`: `upload_file()` 在解析完成后，如果 `result.images` 非空，将图像写入 `<effective_cwd>/documents/<document_id>/images/<filename>`
- [x] 3.3 `backend/app/services/document_service.py`: 图像路径列表回填到 `DocumentVersion.meta["images"]`（格式：`[{"filename": "...", "path": "documents/<doc_id>/images/<filename>", "content_type": "image/png"}]`）
- [x] 3.4 `backend/app/services/document_service.py`: 路径解析复用 workspace 沙箱逻辑（`workspace.rootPath` 或 `workspace.boundPath`）

## 4. 文件预览 API

- [x] 4.1 `backend/app/schemas/document.py`: 新增 `PreviewResponse` Pydantic 模型（`document_id`、`version_id`、`content_md`、`images: list[dict]`、`parser`、`pages`）
- [x] 4.2 `backend/app/api/documents.py`: 新增 `GET /api/documents/{id}/preview` 端点，返回最新版本的 `content_md` + meta 中的图像路径列表
- [x] 4.3 `backend/app/api/documents.py`: preview 端点调用 `DocumentService.get_document()` 获取最新版本内容
- [x] 4.4 `backend/app/api/documents.py`: preview 端点调用 `verify_document_ownership` 确保用户隔离

## 5. 统一文件树 API

- [x] 5.1 `backend/app/services/document_service.py`: `list_tree()` 移除 `source == 'obsidian_sync'` 过滤，改为按 `parent_id` 查询
- [x] 5.2 `backend/app/services/document_service.py`: `list_tree()` 接受 `parent_id: str | None = None` 参数，查询 `WHERE parent_id = ? OR (parent_id IS NULL AND ? IS NULL)`
- [x] 5.3 `backend/app/services/document_service.py`: `list_tree()` 返回结果中包含 `is_folder` 字段，文件夹排在文件前面
- [x] 5.4 `backend/app/services/document_service.py`: 新增 `create_folder(user_id, parent_id, name) -> dict` 方法，创建 `is_folder=True` 的 Document 行
- [x] 5.5 `backend/app/services/document_service.py`: 新增 `move_document(document_id, target_parent_id) -> dict` 方法，更新 `parent_id`
- [x] 5.6 `backend/app/api/documents.py`: 新增 `POST /api/documents/folder` 端点（创建文件夹）
- [x] 5.7 `backend/app/api/documents.py`: 新增 `PATCH /api/documents/{id}/move` 端点（移动文档/文件夹）
- [x] 4.2 `backend/app/schemas/document.py`: `FileNode` 新增 `is_folder: bool = False` 和 `parent_id: str | None = None` 字段

## 6. 前端 Schema 同步

- [x] 6.1 `src/lib/api/memory.ts` 或对应 documents API 文件: 新增 `getDocumentPreview(id)` 函数
- [x] 6.2 `src/lib/api/memory.ts` 或对应 documents API 文件: 扩展 `listTree(path, parentId)` 支持按 `parent_id` 查询
- [x] 6.3 `src/shared/` 中同步 `PreviewResponse` 和 `FileNode` 扩展字段类型定义

## 7. 验证

- [x] 7.1 `ruff check .` 通过
- [x] 7.2 `pytest` 通过
- [ ] 7.3 手动测试：上传 DOCX 文件，确认 `images/` 目录下有提取的图片
- [ ] 7.4 手动测试：`GET /api/documents/{id}/preview` 返回 `content_md` 和 images 列表
- [ ] 7.5 手动测试：`GET /api/documents/tree` 返回所有 source 类型的目录树
- [ ] 7.6 手动测试：创建文件夹 + 移动文档到文件夹，确认 `parent_id` 正确更新
- [ ] 7.7 手动测试：关闭 `rag_extract_images=False` 时不上传图像
