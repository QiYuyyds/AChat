## 1. 后端：UserSettings 新增 RAG 配置字段

- [x] 1.1 `backend/app/db/models.py`: `UserSettings` 模型新增 `rag_chunk_preset` / `rag_chunk_size` / `rag_chunk_overlap` / `ocr_engine` 四列（均 nullable，默认 None）
- [x] 1.2 `backend/app/db/migrations/`: 创建 migration 脚本（ALTER TABLE user_settings ADD COLUMN × 4）
- [x] 1.3 `backend/app/schemas/requests.py`: `UpdateSettingsRequest` 新增 `rag_chunk_preset` / `rag_chunk_size` / `rag_chunk_overlap` / `ocr_engine` 字段（均 Optional）
- [x] 1.4 `backend/app/services/settings_service.py`: `UserSettingsPatch` TypedDict 新增对应字段
- [x] 1.5 `backend/app/api/settings.py`: `_serialize_user_settings()` 和 `_USER_FIELDS` 新增 4 个字段的序列化与路由

## 2. 后端：RAG 配置查询 API

- [x] 2.1 创建 `backend/app/api/rag_config.py`，实现 `GET /api/rag/presets`（返回 `CHUNK_PRESETS` + 默认值）
- [x] 2.2 在 `rag_config.py` 实现 `GET /api/ocr-engines`（调用 `get_available_ocr_engines()` + 细分状态分类 `ok` / `not_installed` / `not_configured` / `unreachable`）
- [x] 2.3 `parser_registry.py`: 新增 `get_ocr_engine_status()` 函数，返回每个引擎的详细状态（区分 not_installed / not_configured / unreachable）
- [x] 2.4 `backend/app/main.py`: 注册 `rag_config` router

## 3. 后端：DocumentService preset_id 优先级解析

- [x] 3.1 `backend/app/services/document_service.py`: `upload_file()` 新增 `_resolve_preset_id(user_id, preset_id)` 方法（空值 → UserSettings.rag_chunk_preset → settings.rag_chunk_preset）
- [x] 3.2 `upload_file()` 和 `ingest_version()` 调用 `_resolve_preset_id` 替代直接透传空 `preset_id`
- [x] 3.3 `upload_file()` 中的 `ocr_engine` 改为从 `UserSettings.ocr_engine` 解析（空值回退 `settings.ocr_engine`）

## 4. 前端：TS 类型补齐

- [x] 4.1 `src/shared/types.ts`: `DocumentRow` 新增 `chunkPreset?: string` 字段
- [x] 4.2 `src/shared/types.ts`: 新增 `RagPreset` 接口（`id` / `label` / `description`）
- [x] 4.3 `src/shared/types.ts`: 新增 `OcrEngineStatus` 接口（`id` / `label` / `available` / `status`）
- [x] 4.4 `src/shared/types.ts`: `UploadResult` 新增 `ragTaskId?: string`（如已有则跳过）

## 5. 前端：API 层补传 preset_id

- [x] 5.1 `src/lib/api.ts`: `uploadDocument()` 新增 `opts.presetId` 参数，追加到 FormData
- [x] 5.2 `src/lib/api.ts`: `ingestDocument()` 新增 `opts?.presetId` 参数，追加到 JSON body
- [x] 5.3 `src/lib/api.ts`: 新增 `fetchRagPresets()` 函数调用 `GET /api/rag/presets`
- [x] 5.4 `src/lib/api.ts`: 新增 `fetchOcrEngines()` 函数调用 `GET /api/ocr-engines`

## 6. 前端：上传对话框加分块策略选择器

- [x] 6.1 `src/components/upload-document-dialog.tsx`: 打开时调用 `fetchRagPresets()` 加载策略列表
- [x] 6.2 新增分块策略 `<select>` 下拉，选项为"跟随默认"+ 动态加载的 preset 列表
- [x] 6.3 选择策略时调用 `uploadDocument(file, { presetId })` 传参
- [x] 6.4 选择"语义 (Semantic)"策略时展示提示"需要嵌入模型，处理较慢"

## 7. 前端：上传对话框 OCR 状态面板

- [x] 7.1 收到 `needsOcr=true` 时，调用 `fetchOcrEngines()` 获取引擎状态
- [x] 7.2 展示 OCR 引擎列表（每个引擎名称 + 状态图标 + 状态文字）
- [x] 7.3 如果有可用引擎且已自动 OCR：展示"已使用 X 引擎自动解析"
- [x] 7.4 如果无可用引擎：展示"无可用 OCR 引擎" + "前往设置配置"链接（跳转到设置面板 RAG Tab）

## 8. 前端：设置面板新增 RAG Tab

- [x] 8.1 `src/components/settings-dialog.tsx`: `SettingsForm` 新增 `ragChunkPreset` / `ragChunkSize` / `ragChunkOverlap` / `ocrEngine` 字段
- [x] 8.2 新增 `TabsTrigger value="rag"` + 对应图标（`FileText` 或 `Layers`）
- [x] 8.3 `TabsContent value="rag"` 内容：默认分块策略下拉（动态加载 presets）+ chunk_size/overlap 输入框 + OCR 引擎选择下拉
- [x] 8.4 OCR 引擎区域：展示每个引擎的状态（调用 `fetchOcrEngines()`）
- [x] 8.5 `handleSave()` 新增 4 个字段的 PATCH 逻辑
- [x] 8.6 `rowToForm()` 新增 4 个字段的映射

## 9. 测试与验证

- [x] 9.1 后端：`ruff check .` 通过
- [x] 9.2 后端：`pytest` 通过（新增 `test_rag_config_api` 测试 presets/ocr-engines 端点）
- [x] 9.3 前端：`pnpm typecheck` 通过
- [x] 9.4 前端：`pnpm lint` 通过
- [ ] 9.5 手动验证：上传对话框选择"QA"策略上传面经文件，确认分块结果正确
- [ ] 9.6 手动验证：上传扫描件 PDF，确认 OCR 引擎状态面板展示正确
- [ ] 9.7 手动验证：设置面板 RAG Tab 修改默认策略，确认后续上传"跟随默认"使用新策略
