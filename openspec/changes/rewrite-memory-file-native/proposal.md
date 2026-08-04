## Why

AChat 的记忆系统（STM/LTM/Preference/GraphMemory）基于 PostgreSQL + Milvus + Neo4j，是服务端架构的产物。项目转向 local-first 桌面端后，这套重量级基础设施与"用户可直接查看/编辑/版本化自己的记忆"的需求严重冲突——记忆数据存在 PG 表里，用户看不到、改不了、不透明。

阿里开源的 ReMe（Remember Me, Refine Me, ACL 2026 Findings）验证了一套 **File-native 记忆范式**：记忆 = Markdown 文件 + frontmatter + wikilinks，通过 auto_memory → auto_dream → proactive 流水线持续精炼。其核心贡献是把"被动积累"(passive accumulation) 推进到"主动精炼"(active refinement)，同时实现全透明（文件可读/可编辑/可 git）和主动推送（proactive 话题机制）。

本次变更将 AChat 记忆系统从 DB-native 改为 File-native，借鉴 ReMe 的 pipeline 设计，同时适配 AChat 的多 Agent 场景（agent-scoped + 全局记忆）和 local-first 桌面端定位。

## What Changes

- **BREAKING**: 移除 `ShortTerm`（纯内存 deque，无人读取）、`LongTerm`（PG 表 + embedding）、`GraphMemory`（Neo4j + PG 镜像表）、`Consolidation`（单层去重/衰减/TTL）
- **BREAKING**: 移除 `LongTermMemory`、`MemoryNode`、`MemoryEdge` 数据库表及对应 ORM 模型
- **保留**: `Preference`（PG KV 表）及其 `UserPreference` 表/ORM — Preference 是结构化键值对（姓名/喜好/所在地等），访问模式为精确查找（`prefs.get("姓名")`），与 File-native 记忆的叙事性 Markdown + 模糊检索互补而非重叠。`manage_profile` 工具、`ProfileSource` 注入、三层去重机制（同义词归一 + 手动覆盖保护 + LLM 合并）均保留不动
- **BREAKING**: 移除 Neo4j 基础设施（docker-compose 服务、`infra/factory.py` 中的 Neo4j 构建）；KGStore 保留（属 RAG 系统，独立不动）
- 新增 File-native 记忆工作空间：`session/` → `daily/` → `digest/`（procedure/wiki）三级生命周期
- 新增 auto_memory pipeline：对话结束后 LLM 提取事实 → 写入 `daily/<date>/<session>.md`
- 新增 auto_dream pipeline：阈值触发 + 定时兜底，扫描 daily 卡片 → 分类精炼到 `digest/{procedure,wiki}/`，含去重/合并/wikilink 自动建链
- 新增 proactive 机制：auto_dream 产出 `interests.yaml` 话题 → Agent 行动前主动拉取
- 新增混合检索：SQLite FTS5 (BM25) + wikilink 图关系扩展 + RRF 融合排序（embedding 默认关闭，可按需启用）
- 新增 Markdown frontmatter 解析/读写层（name, description, agent_id, tags, importance, created_at, source）
- 新增 agent-scoped 记忆：`agent_id` frontmatter 字段 + `digest/procedure/agents/<agent_id>/`% 目录结构
- 新增记忆文件浏览器 UI（设置面板 → 记忆管理 → 文件树 + 编辑器）
- 修改 `MemoryService` 门面为 pipeline 编排（auto_memory → auto_index → auto_dream → proactive）
- 修改 `PromptAssembler` 的 RecallSource 从 PG 查询改为文件检索；ProfileSource 保留不动（继续读 PG Preference）
- 修改 `build_history_for` 中记忆注入逻辑从 `MemoryService.ltm.recall()` 改为文件 search
- 修改 session/ 与 Conversation 双写：PG 存业务数据，session/ jsonl 存记忆管道输入
- SessionMemory 保留不动（属上下文压缩系统，非记忆系统）

## Capabilities

### New Capabilities

- `file-native-memory`: File-native 记忆系统——Markdown 文件 + frontmatter + wikilinks 三级生命周期（session→daily→digest），auto_memory/auto_dream/proactive pipeline，SQLite FTS5 + wikilink 混合检索，agent-scoped + 全局记忆
- `memory-pipeline`: 记忆精炼流水线——auto_memory（对话→daily 卡片）、auto_index（索引维护）、auto_dream（daily→digest 两 bucket 精炼）、proactive（主动话题推送）
- `memory-search`: 记忆混合检索——BM25 (SQLite FTS5) + wikilink 关系扩展 + RRF 融合，embedding 可选启用

### Modified Capabilities

- `memory-system`: 存储模型从 PG/Milvus/Neo4j 改为文件系统 + SQLite 索引；移除 ShortTerm/LongTerm/GraphMemory/Consolidation；**保留 Preference**；MemoryService 重写为 pipeline 编排 + Preference 保留
- `conversation-context`: build_history_for 中记忆注入从 MemoryService.ltm.recall() 改为文件 search
- `frontend`: 记忆管理 UI 从表格视图改为文件浏览器（文件树 + Markdown 编辑器）

## Impact

- **后端核心模块重写**: `memory/` 目录几乎全量重写（删除 5 个文件，新增 ~15 个文件；`preference.py` 保留不动）
- **后端 API 重写**: `api/memory.py` 从 CRUD 改为文件操作 API
- **后端 DB 移除表**: `LongTermMemory`, `MemoryNode`, `MemoryEdge` 表及 ORM 移除（`UserPreference` 保留）
- **后端基础设施**: Neo4j 从 docker-compose 移除；Milvus 仅供 RAG 使用（记忆不再用）
- **后端服务层**: `prompt_assembler.py` RecallSource 注入逻辑改写（ProfileSource 保留不动）；`agent_runner.py` 记忆 hook 改写
- **后端工具层**: `tools/memory_store.py`, `tools/memory_rag.py` 重写为文件操作
- **前端**: `components/settings/memory-management/` 记忆管理重写为文件浏览器 UI（偏好管理 UI 保留不动）；`lib/api/memory.ts` 记忆文件 API 重写（偏好 API 保留不动）
- **Spec**: `specs/08-db-schema.md` 移除记忆相关表（`UserPreference` 保留）；CLAUDE.md §3.8 重写
- **依赖**: 新增 `python-frontmatter`（frontmatter 解析）；移除 `neo4j` driver；移除 Milvus 记忆相关配置
- **风险**: auto_dream 精炼依赖 LLM 调用（成本/延迟）；文件系统无 ACID 需注意并发写入；BM25 索引一致性需文件监听机制保障

> **实现指引**: 本变更借鉴 ReMe（`待融合项目/ReMe/`）的 pipeline 设计。遇到 auto_memory / auto_dream / auto_index / proactive 的实现细节不明确时，**必须查阅 ReMe 源码**（特别是 `reme/steps/evolve/` 目录下的 `auto_memory.py`、`dream/extract.py`、`dream/integrate.py`、`dream/topics.py`、`dream/proactive.py` 以及对应的 `.yaml` prompt 模板），而非凭空设计。ReMe 的 prompt 工程（extract.yaml 的 bucket 分类规则、integrate.yaml 的 CREATE/CORROBORATE/REFINE/CORRECT 四动作定义）是本变更的灵魂，直接参考。
