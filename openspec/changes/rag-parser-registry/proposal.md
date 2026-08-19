## Why

AChat RAG 当前文档解析仅有 PDF 三级 fallback（pdfplumber/pypdf/pdftotext）+ plain text 解码，无法处理扫描件、图片、DOCX、PPTX 等格式。Fidi-Intelli 支持 7 种 OCR 引擎和多种文档格式，AChat 需要移植这些能力来覆盖个人知识库中的多样化文档类型（笔记截图、论文 PDF 扫描件、DOCX 简历等）。

## What Changes

- 新增 `backend/app/rag/parser_registry.py`：OCR 引擎注册表 + 工厂，通过 lazy import + try/except 按需加载引擎
- 新增 `backend/app/rag/parsers/` 包，包含 `BaseDocumentProcessor` 抽象基类和 7 种 OCR 引擎实现（RapidOCR、MinerU、MinerU Official、PP-Structure-V3、DeepSeek-OCR、PaddleOCR-VL-1.6、PaddleOCR-PP-OCRv6）
- 新增 `backend/app/rag/parsers/unified.py`：统一调度入口 `parse_document(source, params)`
- 新增 `backend/app/rag/parsers/zip_utils.py`：ZIP 批量上传解包
- 现有 `backend/app/rag/parser.py` 降级为 `BaseDocumentProcessor` 的一个实现，新增 DOCX/PPTX 解析
- `DocumentService.upload_file()` 改为调用 `parser_registry.parse_document()` 替代直接调 `parse_bytes()`
- OCR 引擎依赖不写入 `pyproject.toml` 核心依赖，通过 lazy import + try/except 按需加载

## Capabilities

### New Capabilities

- `rag-parser-registry`: 文档解析注册表系统——7 种 OCR 引擎（lazy import）+ PDF 三级 fallback + DOCX/PPTX 解析 + 统一调度入口 + ZIP 解包

### Modified Capabilities

（无——DocumentService 的行为变更属于实现细节，不影响 spec 级别的 requirement）

## Impact

- **新增文件**: `parser_registry.py`、`parsers/__init__.py`、`parsers/base.py`、`parsers/rapid_ocr.py`、`parsers/mineru.py`、`parsers/mineru_official.py`、`parsers/pp_structure_v3.py`、`parsers/deepseek_ocr.py`、`parsers/paddleocr_api.py`、`parsers/unified.py`、`parsers/zip_utils.py`（共 11 个文件）
- **修改文件**: `backend/app/rag/parser.py`（降级为 BaseDocumentProcessor 实现 + DOCX/PPTX）、`backend/app/services/document_service.py`（upload_file 改用 parser_registry）
- **新增核心依赖**: `python-docx>=1.1.0`、`python-pptx>=0.6.23`、`json-repair>=0.25.0`（写入 `pyproject.toml`）
- **可选依赖**: `rapidocr-onnxruntime`、`paddleocr` 等不写入 `pyproject.toml`，用户按需安装
- **依赖**: `rag-overhaul-foundation` 提案（`ocr_engine` 等配置项）
