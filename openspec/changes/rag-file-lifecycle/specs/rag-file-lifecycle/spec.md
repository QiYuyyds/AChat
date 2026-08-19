## persistence

### `documents` table changes

- New column: `parent_id VARCHAR(64)` — self-referencing FK to `documents.id`, `ON DELETE SET NULL`, nullable. Establishes virtual directory hierarchy.
- New column: `is_folder BOOLEAN NOT NULL DEFAULT FALSE` — distinguishes folder nodes from file nodes in the tree.
- New index: `idx_documents_parent_id` on `parent_id` for tree traversal queries.

### `ParseResult` data structure extension

- New field: `images: list[ExtractedImage]` — extracted embedded images from DOCX/PPTX/PDF parsing.
- `ExtractedImage` dataclass: `filename: str`, `data: bytes`, `content_type: str`.

### Image storage convention

- Images are persisted to `<workspace_cwd>/documents/<document_id>/images/<filename>`.
- Image path list is stored in `DocumentVersion.meta["images"]` as `list[dict]` with keys `filename`, `path`, `content_type`.

## rag-file-lifecycle

### File tree management

- `list_tree()` now supports all `source` types (not just `obsidian_sync`), queried by `parent_id`.
- Folders are `Document` rows with `is_folder=True`; files are regular `Document` rows with `is_folder=False`.
- `create_folder(user_id, parent_id, name)` creates a folder Document.
- `move_document(document_id, target_parent_id)` updates `parent_id`.

### File preview

- `GET /api/documents/{id}/preview` returns the latest version's `content_md` (parsed Markdown) + image metadata from `meta["images"]`.
- No Office→PDF conversion; preview reads parsed Markdown only.

### Image extraction

- Controlled by `rag_extract_images: bool = True` setting.
- DOCX: extract from `document.inline_shapes` via python-docx.
- PPTX: extract from `slide.shapes` Picture type via python-pptx.
- PDF: extract from `page.images` via pdfplumber.
- Images written to workspace by `DocumentService.upload_file()` after parsing.
