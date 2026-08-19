## Why

沉淀板块「长期记忆」筛选里，点「经验」(procedure) 或「知识」(wiki) 会混入「日常」卡片；点「日常」和「全部」表现正常。根因是 `GET /api/memory/files` 在按 bucket 过滤 digest 后，仍无条件并入全部 `daily/` 文件。产品语义已确认：日常只对应 `daily/` 目录，经验/知识只对应 `digest/procedure|wiki/`。

## What Changes

- 修正 `list_memory_files` 的 bucket 过滤语义：
  - `bucket` 省略 / `null` → digest 全量 + daily 全量
  - `bucket=procedure` → 仅 `digest/procedure/**`
  - `bucket=wiki` → 仅 `digest/wiki/**`
  - `bucket=daily` → 仅 `daily/**`
- 为上述列表过滤补 API 级测试，防止回归
- **不改** 前端 UI 文案与下拉选项；**不改** 搜索、读写删、auto_memory / auto_dream、Preference

## Capabilities

### New Capabilities

- `memory-file-listing`: 记忆文件列表 API 的 bucket 过滤契约（daily 阶段 vs digest bucket 的并入规则）

### Modified Capabilities

- （无）主 specs 尚未落地独立 memory listing capability；本次以新 capability 固化列表过滤契约

## Impact

- **API**: `backend/app/api/memory.py` — `GET /api/memory/files` 的 daily 并入条件
- **可能触达**: `MemoryWorkspace.list_digest_files` / `list_daily_files`（仅若需澄清 `bucket=daily` 时 digest 侧行为；优先只改 API 层）
- **测试**: `backend/tests/test_api_memory.py`（或新增等价测试）
- **前端**: `long-term-memory-panel.tsx` 无需改动（已正确传 `bucket`）
- **非影响**: 搜索、单文件读写删、pipeline、Preference、session memory
