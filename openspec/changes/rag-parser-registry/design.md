## Context

现有 `parser.py` 提供 `parse_bytes(filename, content_type, data) -> ParseResult`，仅支持 PDF 三级 fallback + plain text。方案决策点 6 要求移植 Fidi-Intelli 的 7 种 OCR 引擎，决策点 10 要求 lazy import + try/except 依赖管理。

## Goals / Non-Goals

**Goals:**
- 实现 7 种 OCR 引擎的注册和调度（RapidOCR、MinerU、MinerU Official、PP-Structure-V3、DeepSeek-OCR、PaddleOCR-VL-1.6、PaddleOCR-PP-OCRv6）
- 新增 DOCX/PPTX 解析能力
- 新增 ZIP 批量上传解包
- 保持核心安装轻量（OCR 依赖不写入 pyproject.toml）

**Non-Goals:**
- 不修改前端文件上传 UI
- 不实现 OCR 引擎的前端选择 UI
- 不修改 PDF 三级 fallback 的实现逻辑（它被降级为 BaseDocumentProcessor 的一个实现）
- 不实现文件生命周期状态机（提案 rag-graph-build-task 中处理）

## Decisions

### Decision 1: lazy import + try/except 而非 optional extras

**Choice**: OCR 引擎依赖不写入 `pyproject.toml`（包括 optional extras），用户手动 `pip install` 需要的引擎。

**Rationale**: 方案决策点 10。OCR 引擎依赖（如 `paddleocr`）体积大、依赖链复杂，写入 pyproject.toml 会让核心安装变得臃肿。lazy import + try/except 确保缺引擎时系统正常工作。

**Alternative considered**: 使用 `pyproject.toml` 的 `[project.optional-dependencies]`。否决——用户仍需知道哪个 extra 对应哪个引擎，且 extras 安装时会拉入完整依赖树。

### Decision 2: MinerU / DeepSeek-OCR / PaddleOCR API 引擎通过 HTTP 调用

**Choice**: MinerU（自建）、MinerU Official（云）、DeepSeek-OCR（SiliconFlow）、PaddleOCR API 引擎通过 HTTP API 调用，不需要安装 Python 依赖。

**Rationale**: 这些引擎要么是远程服务，要么通过 API key 调用。HTTP 调用不需要 Python 依赖，简化了依赖管理。

### Decision 3: `parser.py` 降级为 BaseDocumentProcessor 实现

**Choice**: 现有 `parser.py` 的 `parse_bytes()` 保留为 PDF 三级 fallback + plain text 的实现，新增 DOCX/PPTX 解析。它注册为 `parser_registry` 中的一个 processor。

**Rationale**: 方案要求"从入口角色降级为 `parsers/base.py` 的一个实现"。保留现有代码避免回归风险。

### Decision 4: `auto` 模式按文件类型选择 parser

**Choice**: `ocr_engine="auto"` 时按文件扩展名分派——纯文本/Markdown 直接解码，PDF 先尝试三级 fallback，扫描件走 OCR 引擎优先级。

**Rationale**: 对齐 Fidi-Intelli 的 `auto` 模式逻辑。

## Risks / Trade-offs

- **[Risk] 用户不知道该装哪个 OCR 引擎** → 文档中说明每个引擎的 `pip install` 命令和适用场景
- **[Risk] OCR API 引擎的网络超时** → 每个 HTTP 调用设置超时，失败后尝试下一个可用引擎
- **[Risk] ZIP 解包可能导致递归炸弹** → 限制解包深度（如 3 层）和文件数量上限

## Open Questions

无。
