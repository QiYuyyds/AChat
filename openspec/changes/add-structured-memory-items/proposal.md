## Why

当前 LTM 记忆条目是"纯 content 字符串 + embedding"的扁平结构，存在三个结构性问题：

1. **embedding 信号被稀释**——每条记忆 15-80 词，embedding 基于完整 content 计算，语义匹配精度下降
2. **关键词匹配完全缺失**——纯 cosine 检索无法精确命中专有名词缩写（"TypeScript" vs "TS"），导致漏召回
3. **缺少任务经验类记忆**——当前只存事实和偏好，不存"上次做这类任务什么做法有效"的跨会话经验复用

基于 Mem0 + OpenViking 思想融合，本变更有保留向量库存储基座，在每条记忆条目上内化结构化字段（summary/keywords/content_scope），使 Viking 的"结构导航 + 分层加载"思想自然发生在单条记忆内部，而非外挂目录树。

## What Changes

**A. 结构化记忆条目（核心）**

- LTM `Item` 新增 3 个字段：`summary`（3-10 字摘要标题）、`keywords`（3-5 个检索关键词）、`content_scope`（关联项目/目录路径，可空）
- **BREAKING**：embedding 策略从 `embed(content)` 变为 `embed(summary)`，信号更集中
- `long_term_memory` 表新增 `summary TEXT`、`keywords TEXT[]`、`content_scope TEXT` 三列
- 存量记忆后台异步迁移：为现有条目生成 summary/keywords 并重算 embedding

**B. 双路检索**

- `LongTerm.recall()` 从纯 cosine 匹配改为双路打分：`semantic_sim(summary_emb) * 0.5 + keyword_match * 0.2 + importance * 0.3`
- 关键词匹配使用零依赖 Jaccard 相似度（3-5 个关键词场景足够精确）
- `memory_recall` 工具返回格式增加 `summary` 和 `keywords` 字段

**C. 任务经验沉淀（Case Memory）**

- `category` 白名单新增 `"case"` 类型
- 新增 case 提取流程：任务/会话结束时从 SessionMemory 摘要中提取可复用经验
- Case 记忆有独立生命周期参数：TTL=90 天（vs 30 天）、decay=0.998（vs 0.995）、min_importance=0.4、dedup_threshold=0.90
- `memory_store` 工具支持 `category="case"` 时传入 `summary`/`keywords`

**D. Consolidation 增强**

- `_merge_pair` 合并时同步 summary/keywords/content_scope（去重并集，上限 8 个关键词）
- Consolidation 按 category 分组应用不同生命周期参数（case vs 普通）
- `ConsolidationConfig` 新增 case 专用参数

## Capabilities

### Modified Capabilities

- `memory-persistence`: LTM Item 新增 summary/keywords/content_scope 字段；embedding 基于 summary 而非 content；recall 改为双路匹配（summary embedding 语义 + keyword Jaccard）
- `memory-extraction`: LTM 提取 prompt 输出格式增加 summary/keywords；新增 case 提取 prompt 和触发流程
- `memory-consolidation`: 合并时同步新字段；ConsolidationConfig 新增 case 专用参数；按 category 分组应用生命周期参数

## Impact

- **数据库**：`long_term_memory` 新增 3 列（`summary TEXT`, `keywords TEXT[]`, `content_scope TEXT`），需 migration；可选 `content_scope` 索引
- **后端代码**：`consolidation.py`、`long_term.py`、`memory_writer.py`、`memory_service.py`、`memory_rag.py`、`memory_store.py`、`db/models.py`
- **不改动**：Preference 系统、GraphMemory Cypher 逻辑、ShortTerm/SessionMemory、PromptAssembler Slot 系统
- **存量迁移**：后台异步执行，不阻断服务启动；单条失败不中断；未迁移条目保留 content-based embedding，功能不受影响
- **风险**：embedding 语义变更导致短期召回波动（summary 是 content 浓缩，语义方向一致，精度提升而非下降）
