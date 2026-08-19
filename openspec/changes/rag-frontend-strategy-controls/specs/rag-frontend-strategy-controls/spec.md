## Purpose

RAG 前端策略控制：将后端已有的分块策略（4 种 preset）和 OCR 引擎状态暴露给前端用户，使用户可以在上传时选择分块策略、在设置面板配置默认 RAG 参数和 OCR 引擎、并在扫描件检测到时看到可操作的引擎状态信息。

## ADDED Requirements

### Requirement: Upload dialog SHALL expose chunking preset selection

上传对话框必须提供分块策略选择器，列出所有可用 preset（general/qa/semantic/separator）及其描述。选择"跟随默认"时不传 `preset_id`（后端使用全局默认），选择具体策略时传对应 `preset_id`。

#### Scenario: User selects "QA" preset on upload
- **WHEN** 用户在上传对话框中选择"问答 (QA)"分块策略并上传文件
- **THEN** 前端在 FormData 中附加 `preset_id=qa`
- **AND** 后端 `DocumentService.upload_file()` 使用 `qa` preset 进行分块
- **AND** `Document.chunk_preset` 列被设为 `qa`

#### Scenario: User leaves preset as "default" on upload
- **WHEN** 用户在上传对话框中不修改分块策略（保持"跟随默认"）
- **THEN** 前端不传 `preset_id`（或传空字符串）
- **AND** 后端使用 `settings.rag_chunk_preset` 或 `UserSettings.rag_chunk_preset` 作为默认

#### Scenario: Semantic preset selected but embed_fn unavailable
- **WHEN** 用户选择"语义 (Semantic)"策略
- **THEN** 前端展示提示"需要嵌入模型，处理较慢"
- **AND** 后端 dispatcher 在 `embed_fn` 不可用时自动降级为 `general`（现有行为，不变）

### Requirement: Upload dialog SHALL show OCR engine status when needs_ocr is true

上传对话框收到 `needsOcr=true` 响应时，必须展示 OCR 引擎状态面板，而不仅是一行警告文字。面板内容包含：哪些 OCR 引擎可用、哪些未配置、当前使用的引擎（如果有）。

#### Scenario: Scanned PDF uploaded, RapidOCR available
- **WHEN** 用户上传扫描件 PDF
- **AND** `needsOcr=true` 返回
- **AND** RapidOCR 引擎可用
- **THEN** 上传对话框展示"检测到扫描件，已使用 RapidOCR 自动解析"
- **AND** 展示 OCR 引擎列表，标注 RapidOCR 为"当前使用"

#### Scenario: Scanned PDF uploaded, no OCR engine available
- **WHEN** 用户上传扫描件 PDF
- **AND** `needsOcr=true` 返回
- **AND** 没有任何 OCR 引擎可用
- **THEN** 上传对话框展示"检测到扫描件，但无可用 OCR 引擎"
- **AND** 展示引擎列表，每个引擎标注未可用原因（未安装 / 未配置 API Key / 服务不可达）
- **AND** 提供"前往设置配置 OCR 引擎"链接，跳转到设置面板的 RAG 配置 Tab

### Requirement: Settings dialog SHALL provide a RAG configuration tab

设置对话框必须新增"RAG 配置"Tab，包含：默认分块策略选择、分块参数（chunk_size / chunk_overlap）、OCR 引擎选择与状态展示。配置保存到 `UserSettings` 表（用户级），独立于 `.env` 全局配置。

#### Scenario: User changes default chunk preset in settings
- **WHEN** 用户在设置面板的 RAG Tab 中将默认分块策略从"通用"改为"问答"
- **AND** 点击保存
- **THEN** `UserSettings.rag_chunk_preset` 被更新为 `qa`
- **AND** 后续上传对话框的"跟随默认"选项使用 `qa` 作为默认策略

#### Scenario: User selects specific OCR engine in settings
- **WHEN** 用户在设置面板的 RAG Tab 中将 OCR 引擎从"auto"改为"deepseek_ocr"
- **AND** 点击保存
- **THEN** `UserSettings.ocr_engine` 被更新为 `deepseek_ocr`
- **AND** 后续文件解析使用 `deepseek_ocr` 引擎（而非 auto 优先级遍历）

#### Scenario: OCR engine status displayed in settings
- **WHEN** 用户打开设置面板的 RAG Tab
- **THEN** 每个 OCR 引擎旁边展示其可用性状态（可用 / 未安装 / 未配置 Key / 服务不可达）
- **AND** 状态通过调用 `GET /api/ocr-engines` 获取

### Requirement: Backend SHALL expose GET /api/rag/presets endpoint

后端必须提供 `GET /api/rag/presets` 接口，返回所有分块策略的 id、label、description 和默认值。前端动态加载策略列表，不硬编码。

#### Scenario: Fetch presets list
- **WHEN** 前端调用 `GET /api/rag/presets`
- **THEN** 返回 `{ "presets": [...], "default": "general" }`
- **AND** 每个 preset 包含 `id` / `label` / `description` 字段
- **AND** 数据来源于 `CHUNK_PRESETS` 字典

### Requirement: Backend SHALL expose GET /api/ocr-engines endpoint

后端必须提供 `GET /api/ocr-engines` 接口，返回所有已注册 OCR 引擎的 id、label、可用性和健康状态。前端用于展示引擎状态和选择。

#### Scenario: Fetch OCR engine status
- **WHEN** 前端调用 `GET /api/ocr-engines`
- **THEN** 返回 `{ "engines": [...], "current": "auto" }`
- **AND** 每个引擎包含 `id` / `label` / `available` / `status` 字段
- **AND** `status` 为 `"ok"` / `"not_installed"` / `"not_configured"` / `"unreachable"` 之一
- **AND** 数据来源于 `PROCESSOR_TYPES` 注册表和 `_load_processor()` 健康检查

### Requirement: UserSettings SHALL persist RAG configuration fields

`UserSettings` 表必须新增 4 个 RAG 配置字段，支持用户级持久化。这些字段覆盖 `.env` 全局配置，优先级：`UserSettings` > `settings (config.py)` > 默认值。

#### Scenario: User has custom RAG settings
- **WHEN** 用户在 `UserSettings` 中设置了 `rag_chunk_preset=qa` / `rag_chunk_size=400` / `ocr_engine=deepseek_ocr`
- **THEN** 文档上传和入库时使用这些用户级配置（而非 `.env` 的 `rag_chunk_preset` / `ocr_engine`）
- **AND** 其他用户不受影响（多用户隔离）

#### Scenario: User has no custom RAG settings
- **WHEN** `UserSettings` 的 RAG 字段为 `None`
- **THEN** 回退到 `config.py` 的 `settings.rag_chunk_preset` / `settings.ocr_engine` 全局默认值
