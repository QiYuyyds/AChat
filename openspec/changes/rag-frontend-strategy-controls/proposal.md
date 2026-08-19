## Why

AChat RAG 后端已具备完整的文件解析策略（7 种 OCR 引擎 + PDF 三级降级）和 4 种文档分块策略（general/qa/semantic/separator），但前端完全没有暴露这些能力：上传对话框不传 `preset_id`，扫描件检测到 `needs_ocr` 后只展示一行文字无操作入口，用户无法查看或配置 OCR 引擎状态。结果是所有文档都走默认 `general` 分块，扫描件上传直接失败，用户束手无策。

## What Changes

- 上传对话框新增分块策略选择器（下拉选择 general/qa/semantic/separator），前端 API 层补传 `preset_id`
- 上传对话框收到 `needsOcr=true` 时展示 OCR 引擎状态面板（哪些可用、哪些未配置），不再只展示一行警告
- 设置面板新增「RAG 配置」Tab：默认分块策略选择 + 分块参数（chunk_size / overlap）+ OCR 引擎选择与状态展示
- 后端新增 `GET /api/rag/presets` 返回分块策略列表与描述
- 后端新增 `GET /api/ocr-engines` 返回 OCR 引擎可用性与健康状态
- `UserSettings` 新增 `rag_chunk_preset` / `rag_chunk_size` / `rag_chunk_overlap` / `ocr_engine` 字段（用户级 RAG 配置，独立于 `.env`）
- 前端 TS 类型补齐 `presetId` / `chunkPreset` / OCR 引擎相关类型定义

## Capabilities

### New Capabilities

- `rag-frontend-strategy-controls`: RAG 前端策略控制——分块策略选择 UI + OCR 引擎状态面板 + RAG 配置设置 Tab + 两个查询 API

### Modified Capabilities

（无——不修改现有 capability spec 的 requirement，仅新增前端 UI 和查询 API）

## Impact

- **新增后端文件**: `backend/app/api/rag_config.py`（`GET /api/rag/presets` + `GET /api/ocr-engines`）
- **修改后端文件**:
  - `backend/app/db/models.py`（`UserSettings` 新增 4 个 RAG 配置字段）
  - `backend/app/schemas/requests.py`（`UpdateSettingsRequest` 新增对应字段）
  - `backend/app/api/settings.py`（序列化/反序列化新增字段）
  - `backend/app/services/settings_service.py`（`UserSettingsPatch` 新增字段）
- **修改前端文件**:
  - `src/components/upload-document-dialog.tsx`（加分块策略选择器 + OCR 状态面板）
  - `src/components/settings-dialog.tsx`（加 RAG 配置 Tab）
  - `src/lib/api.ts`（`uploadDocument` / `ingestDocument` 传 `presetId`；新增 `fetchRagPresets` / `fetchOcrEngines`）
  - `src/shared/types.ts`（补齐 `presetId` / `chunkPreset` / OCR 引擎类型）
- **新增 DB migration**: `user_settings` 表新增 4 列（`rag_chunk_preset` / `rag_chunk_size` / `rag_chunk_overlap` / `ocr_engine`）
- **依赖**: 复用已有 `rag-parser-registry`（`get_available_ocr_engines()`）和 `rag-chunking-presets`（`CHUNK_PRESETS`）后端能力，无新外部依赖
