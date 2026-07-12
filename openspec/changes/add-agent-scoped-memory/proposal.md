## Why

当前 LTM（`long_term_memory` 表）是全局共享池——所有 Agent 的记忆写入同一张表，recall 时对所有 Agent 的经验做无差别语义搜索。在 AChat 的多 Agent 协作场景下（单聊 / 群聊 / Orchestrator 派发子任务），这导致：

- **记忆污染**：代码 Agent 学到的"用户项目用 React 19"和文档 Agent 学到的"用户偏好中文回复"混在同一池中，recall 时互相干扰
- **无 Agent 隔离**：一个 Agent 的工具失败经验会影响另一个 Agent 的 recall 结果
- **与产品定位矛盾**：AChat 定位"Agent 是联系人"，但联系人不该共享大脑——每个联系人应有自己的记忆

AChat 的 `Agent` 实体已有 `id`、`name`、`system_prompt` 等字段，但记忆层未与之绑定。本变更在 LTM 上增加 `scope` + `agent_id` 维度，使每个 Agent 拥有独立的长期记忆空间，同时保留全局共享层。

## What Changes

**A. 数据模型：LTM 增加 scope 维度**

- `long_term_memory` 表新增 `scope` 列（`global` / `agent`）和 `agent_id` 列（nullable，scope=agent 时必填）
- 现有数据迁移：所有存量行 `scope = 'global'`、`agent_id = NULL`
- `Item` dataclass 同步增加 `scope` 和 `agent_id` 字段

**B. 写入路由：按 Agent 归属分流**

- `memory_writer.extract_memory_from_reply` 接收 `agent_id` 参数，抽取结果写入 `scope='agent'` 的 LTM
- `MemoryService.on_message_end` 传入当前 `agent_id`
- 全局记忆（跨 Agent 共享的事实）仍写入 `scope='global'`

**C. 召回过滤：先 Agent-scoped 再 global**

- `LongTerm.recall()` 和 `recall_by_filter()` 增加 `agent_id` 参数
- 召回策略：先查 `scope='agent' AND agent_id=X`，再查 `scope='global'`，合并排序
- PromptAssembler 的 `RecallSource` 传入当前 `agent_id`

**D. Consolidation 适配**

- `consolidate()` 按 scope+agent_id 分组执行，不跨 Agent 合并
- dedup 只在同一 scope 内做

## Capabilities

### Modified Capabilities

- `memory-persistence`: LTM 增加 scope/agent_id 维度；写入按 Agent 归属路由；召回按 scope 过滤
- `memory-extraction`: 抽取结果携带 agent_id，写入 agent-scoped LTM
- `prompt-assembler`: RecallSource 传入 agent_id 做过滤召回

## Impact

- **数据库**：`long_term_memory` 新增两列（`scope VARCHAR(16)`, `agent_id VARCHAR`），需 migration；存量数据 scope='global'
- **后端代码**：`long_term.py`、`memory_service.py`、`memory_writer.py`、`prompt_assembler.py`（RecallSource）
- **API**：无外部接口变更（agent_id 从 conversation→agent 链路内部获取）
- **前端**：无直接影响（后续 `add-memory-transparency-ui` 变更会按 Agent 分组展示）
- **风险**：召回范围缩小可能导致某些跨 Agent 有用的记忆在 global 层缺位；通过保留 global scope 缓解
