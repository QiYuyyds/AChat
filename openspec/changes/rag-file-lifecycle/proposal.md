## Why

AChat RAG 系统当前缺少 Fidi-Intelli 已有的文件管理能力：文件树导航、workspace 代理的图像提取、以及基于已解析 Markdown 的文件预览。当前 `Document` 模型是扁平结构，无法表达目录层级；`ParseResult` 不返回提取的图像；文档预览只能查看 `content_md` 原文。

本提案对齐 Fidi-Intelli 的文件生命周期管理，在不引入新基础设施的前提下，利用 workspace 文件系统实现图像存储和文件预览。

## What Changes

- **Document 模型新增 `parent_id` 和 `is_folder` 列**：支持虚拟目录树，所有 source 类型统一可用（不限于 obsidian_sync）
- **`ParseResult` 新增 `images` 字段**：解析器在解析 DOCX/PPTX/PDF 时提取内嵌图片，以 base64 或 bytes 返回
- **图像存储由 workspace 代理**：提取的图像写入 `<workspace>/documents/<document_id>/images/`，路径回填到 `DocumentVersion.meta.images`
- **文件预览 API**：`GET /api/documents/{id}/preview` 返回已解析的 `parsed_md`（从 `DocumentVersion.content_md` 读取），不做 Office→PDF 转换
- **统一文件树 API**：扩展现有 `list_tree` 支持所有 source 类型（当前仅 obsidian_sync），通过 `parent_id` 构建层级
- **Document schema 约束**：`source_path` 仍存虚拟路径用于排序/展示；`parent_id` 是结构化外键用于层级查询
- **新增配置项**：`rag_extract_images: bool = True`（控制是否在解析时提取图像）

## Capabilities

### New Capabilities

- `rag-file-lifecycle`: 文件生命周期增强——文件树管理 + 图像提取 + 文件预览

### Modified Capabilities

- `persistence`: `documents` 表新增 `parent_id` 和 `is_folder` 列；`ParseResult` 数据结构扩展

## Impact

- **修改文件**: `backend/app/db/models.py`（Document 新增列）、`backend/app/rag/parsers/base.py`（ParseResult 新增 images 字段）、`backend/app/rag/parser.py` + `backend/app/rag/parsers/*.py`（各解析器实现图像提取）、`backend/app/services/document_service.py`（upload_file 写入图像到 workspace + 统一文件树）、`backend/app/api/documents.py`（新增 preview 端点 + 扩展 tree 端点）、`backend/app/schemas/document.py`（新增 PreviewResponse 等）、`backend/app/config.py`（新增 rag_extract_images 配置项）
- **新增 API**: `GET /api/documents/{id}/preview`
- **DB 迁移**: `documents` 表新增 `parent_id VARCHAR(64) NULL` + `is_folder BOOLEAN DEFAULT FALSE`
- **不放宽文件配额**：sandbox 模式仍保持 100MB / 1000 文件上限
- **不引入新依赖**：图像提取使用 python-docx / python-pptx / pdfplumber 已有的内置能力
