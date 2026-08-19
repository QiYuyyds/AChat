## Context

后端已有完整的分块策略调度（`chunking/dispatcher.py` + 4 种 preset）和 OCR 引擎注册表（`parser_registry.py` + 7 种引擎 + `get_available_ocr_engines()`），但前端没有暴露这些能力。`UserSettings` 表目前只存 API Key 和移动端配置，没有 RAG 相关字段。设置面板（`settings-dialog.tsx`）有 3 个 Tab（keys / mobile / publish），上传对话框（`upload-document-dialog.tsx`）不传 `preset_id`。

## Goals / Non-Goals

**Goals:**
- 前端补齐 `preset_id` 传参链路，上传对话框可选分块策略
- OCR 引擎状态对用户可见（上传失败时 + 设置面板中）
- 用户级 RAG 配置持久化（`UserSettings` 表），支持多用户不同偏好
- 两个查询 API（presets / ocr-engines）复用已有后端能力，无新逻辑

**Non-Goals:**
- 不做 OCR 异步化（RagTask 队列处理 OCR），保持同步解析
- 不做分块策略自动推荐（内容嗅探），用户手动选择
- 不做 per-preset 参数 UI（`rag_chunk_parser_config` JSON 覆盖仍走 `.env`，不暴露给 UI）
- 不修改后端解析/分块的核心逻辑，只加查询 API 和配置传递

## Decisions

### Decision 1: RAG 配置存 UserSettings 而非 GlobalSettings

**选择**: `rag_chunk_preset` / `rag_chunk_size` / `rag_chunk_overlap` / `ocr_engine` 存入 `UserSettings`（per-user）。

**理由**: 多用户场景下每个用户的文档类型偏好不同（A 专做面经、B 专做论文），RAG 配置应用户级隔离。`GlobalSettings` 只存部署级配置（publish dir 等）。

**替代方案**: 存 `GlobalSettings`（全局共享）——简单但多用户场景不合理。不选。

### Decision 2: OCR 引擎查询 API 实时健康检查，不缓存

**选择**: `GET /api/ocr-engines` 每次请求都调用 `_load_processor()` + `check_health()` 做实时检查。

**理由**: OCR 引擎可用性取决于运行时环境（依赖是否安装、API Key 是否配置、服务是否可达），缓存会过时。引擎列表固定 7 个，每次遍历 lazy import + health check 的性能开销可接受（<100ms）。

**替代方案**: 启动时检查一次缓存到内存——如果用户中途配置了 Key 或安装了依赖，缓存不会更新。不选。

### Decision 3: 上传对话框分块策略选择器用动态加载而非硬编码

**选择**: 调用 `GET /api/rag/presets` 动态加载策略列表。

**理由**: 后端 `CHUNK_PRESETS` 字典可能扩展（加新 preset），动态加载避免前后端不同步。策略描述（label / description）由后端单一数据源提供。

**替代方案**: 前端硬编码 4 个 preset——简单但跟后端 `CHUNK_PRESETS` 可能不同步。不选。

### Decision 4: 上传时 preset_id 优先级：表单显式选择 > UserSettings > config.py 默认

**选择**: `DocumentService.upload_file()` 解析 `preset_id` 时，空值回退到 `UserSettings.rag_chunk_preset`，再回退到 `settings.rag_chunk_preset`。

**理由**: 保持与 API Key 优先级一致（agent > user_settings > .env）。用户在设置面板选了默认策略后，上传时不用每次手动选。

**实现**: `DocumentService.upload_file()` 新增 `_resolve_preset_id(user_id, preset_id)` 方法，从 `UserSettings` 读取 fallback。

### Decision 5: OCR 引擎状态展示用 4 级状态枚举

**选择**: 引擎状态用 `ok` / `not_installed` / `not_configured` / `unreachable` 四级。

**理由**: 用户需要知道引擎为什么不可用才能采取行动——`not_installed` 提示安装依赖，`not_configured` 提示配置 Key，`unreachable` 提示检查服务。

**实现**: `_load_processor()` 的失败路径已有 `ImportError`（→ `not_installed`）和 `check_health() == False`（→ 需要细分为 `not_configured` vs `unreachable`）。需要在 `BaseDocumentProcessor` 或 registry 层加状态分类逻辑。

### Decision 6: 设置面板加 "RAG" Tab，不加新 Dialog

**选择**: 在现有 `settings-dialog.tsx` 的 Tabs 里加第 4 个 Tab "RAG"。

**理由**: 复用现有 Dialog 结构和保存逻辑，用户不用学习新入口。Tab 图标用 `FileText` 或 `Layers`。

## Risks / Trade-offs

- [UserSettings 表新增 4 列需要 migration] → 使用 `backend/app/db/migrations/` 已有的 migration 机制，ALTER TABLE ADD COLUMN（nullable，默认 NULL），不阻塞已有数据
- [OCR 引擎健康检查 API 调用可能慢] → 7 个引擎 lazy import + check_health 总耗时 < 100ms（主要是 import 尝试，失败的 try/except 很快），可接受
- [前端动态加载 preset 列表增加一次 API 调用] → 上传对话框打开时加载，不阻塞文件选择操作；API 响应很小（4 个 preset 的 label + description）
- [UserSettings RAG 字段覆盖 config.py 但 CLI 启动时还没读 DB] → CLI agent 不涉及 RAG 配置；RAG 操作都在 HTTP 请求上下文中（已有 user_id），不影响启动流程
