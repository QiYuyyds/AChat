## Purpose

文档解析注册表系统：定义 OCR 引擎注册、lazy import 加载、统一调度入口的行为契约，使多种文档格式（PDF 扫描件、图片、DOCX、PPTX、ZIP）可以被解析为纯文本。

## ADDED Requirements

### Requirement: ParserRegistry SHALL lazy-load OCR engines with graceful degradation

The parser registry MUST load OCR engine implementations via lazy import. If a engine's Python dependency is not installed, that engine is marked as unavailable and the registry continues with other engines. Missing dependencies MUST NOT cause startup failure or import errors.

#### Scenario: RapidOCR not installed
- **WHEN** the backend starts without `rapidocr-onnxruntime` installed
- **THEN** the RapidOCR engine is marked as unavailable
- **AND** a log message is emitted at INFO level
- **AND** other engines function normally

#### Scenario: All OCR engines missing
- **WHEN** no OCR engine dependencies are installed
- **THEN** PDF files with embedded text are still parsed via the three-level fallback (pdfplumber/pypdf/pdftotext)
- **AND** scanned PDFs and images return `needs_ocr=True`
- **AND** text and Markdown files are decoded normally

### Requirement: Unified parser SHALL select engine by file type and configuration

The `parse_document(source, params)` entry point MUST select the appropriate parser based on file type and `ocr_engine` configuration. In `auto` mode, it dispatches by file extension: text/Markdown → direct decode, PDF with embedded text → three-level fallback, scanned PDF/image → first available OCR engine by priority, DOCX → python-docx, PPTX → python-pptx, ZIP → recursive unpacking.

#### Scenario: Plain text file uploaded
- **WHEN** a `.txt` or `.md` file is uploaded
- **THEN** the file is decoded as text (UTF-8 → GBK → Latin-1 fallback)
- **AND** no OCR engine is invoked

#### Scenario: Scanned PDF uploaded with RapidOCR available
- **WHEN** a scanned PDF (text < 80 runes) is uploaded
- **AND** `ocr_engine` is set to `"auto"`
- **AND** RapidOCR is installed
- **THEN** RapidOCR is used to extract text from the PDF images

#### Scenario: DOCX file uploaded
- **WHEN** a `.docx` file is uploaded
- **THEN** `python-docx` is used to extract text
- **AND** the extracted text includes paragraph contents

#### Scenario: ZIP file uploaded
- **WHEN** a `.zip` file is uploaded
- **THEN** the ZIP is unpacked
- **AND** each contained file is parsed recursively via `parse_document`

### Requirement: BaseDocumentProcessor SHALL define standard interface

All parser implementations MUST extend `BaseDocumentProcessor` with `process_file()`, `check_health()`, `supported_extensions`, `service_name`, and `display_name` attributes.

#### Scenario: Engine health check
- **WHEN** `check_health()` is called on an engine
- **THEN** it returns `True` if the engine's dependency is installed and configured
- **AND** it returns `False` otherwise
