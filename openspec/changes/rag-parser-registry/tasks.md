## 1. 包结构和基类

- [x] 1.1 创建 `backend/app/rag/parsers/__init__.py`
- [x] 1.2 创建 `backend/app/rag/parsers/base.py`：`BaseDocumentProcessor` 抽象基类，定义 `process_file()`、`check_health()`、`supported_extensions`、`service_name`、`display_name` 属性

## 2. OCR 引擎实现

- [x] 2.1 创建 `backend/app/rag/parsers/rapid_ocr.py`：`RapidOCRParser` — 使用 `rapidocr-onnxruntime` 本地 OCR，lazy import
- [x] 2.2 创建 `backend/app/rag/parsers/mineru.py`：`MinerUParser` — 通过 HTTP API 调用自建 MinerU 服务
- [x] 2.3 创建 `backend/app/rag/parsers/mineru_official.py`：`MinerUOfficialParser` — 通过官方云 API 调用
- [x] 2.4 创建 `backend/app/rag/parsers/pp_structure_v3.py`：`PPStructureV3Parser` — 使用 PaddleOCR PP-Structure-V3 版面解析，lazy import
- [x] 2.5 创建 `backend/app/rag/parsers/deepseek_ocr.py`：`DeepSeekOCRParser` — 通过 SiliconFlow API 调用 DeepSeek-OCR
- [x] 2.6 创建 `backend/app/rag/parsers/paddleocr_api.py`：`PaddleOCRVLParser` + `PaddleOCRPPOCRv6Parser` — 通过云端 API 调用

## 3. 注册表和调度入口

- [x] 3.1 创建 `backend/app/rag/parser_registry.py`：`PROCESSOR_TYPES` 字典定义 7 种引擎的 module path + class name
- [x] 3.2 实现 `_load_processor(proc_type)` 函数：lazy import + try/except，返回 processor 实例或 None
- [x] 3.3 实现 `parse_document(source, params)` 统一调度入口：根据文件类型和 `ocr_engine` 配置选择 parser
- [x] 3.4 实现 `auto` 模式引擎选择策略：纯文本→直接解码、PDF 有文本→三级 fallback、扫描件→按优先级选已安装 OCR 引擎

## 4. 现有 parser 降级和格式扩展

- [x] 4.1 `backend/app/rag/parser.py`：将 `parse_bytes()` 改为继承 `BaseDocumentProcessor` 的实现
- [x] 4.2 `backend/app/rag/parser.py`：新增 DOCX 解析（使用 `python-docx`）
- [x] 4.3 `backend/app/rag/parser.py`：新增 PPTX 解析（使用 `python-pptx`）

## 5. ZIP 解包工具

- [x] 5.1 创建 `backend/app/rag/parsers/zip_utils.py`：ZIP 解包函数，限制递归深度（3 层）和文件数量上限（1000）

## 6. 统一调度入口

- [x] 6.1 创建 `backend/app/rag/parsers/unified.py`：`parse_document(source, params)` 统一入口，整合 parser_registry 和 zip_utils

## 7. DocumentService 接入

- [x] 7.1 `backend/app/services/document_service.py`：`upload_file()` 改为调用 `parser_registry.parse_document()` 替代直接调 `parse_bytes()`
- [x] 7.2 确保 `needs_ocr` 逻辑兼容新的 parser_registry 返回值

## 8. 依赖管理

- [x] 8.1 `pyproject.toml` 新增核心依赖：`python-docx>=1.1.0`、`python-pptx>=0.6.23`、`json-repair>=0.25.0`
- [x] 8.2 确认 OCR 引擎依赖不写入 `pyproject.toml`

## 9. 验证

- [x] 9.1 `ruff check .` 通过
- [x] 9.2 `pytest` 通过
- [x] 9.3 手动测试：上传 DOCX 文件，确认文本被正确提取
- [x] 9.4 手动测试：上传纯文本文件，确认行为与改动前一致
- [x] 9.5 手动测试：不安装任何 OCR 引擎时上传扫描件 PDF，确认返回 `needs_ocr=True`
