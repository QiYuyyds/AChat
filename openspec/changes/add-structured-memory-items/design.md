# Design — add-structured-memory-items

## Context

AChat 的长期记忆（LTM）系统基于 Mem0 范式：每条记忆是一个 15-80 词的 content 字符串 + embedding 向量，检索时做扁平 cosine 扫描。该范式已通过多轮变更（`fix-memory-subsystem`、`fix-memory-quality`、`fix-ltm-dirty-data`、`improve-memory-extraction`、`add-agent-scoped-memory`）补齐了抽取质量、持久化一致性、scope 隔离等基础设施。

当前的结构性瓶颈在检索精度和记忆类型覆盖上：

```
query → embed → 扁平 cosine 扫描 ALL items → 图扩展(可选) → 返回
```

- **embedding 信号稀释**：80 词的 content 混入大量上下文噪声，语义匹配精度下降
- **关键词匹配缺失**：纯 cosine 无法精确命中专有名词缩写（"TypeScript" vs "TS"）
- **无任务经验记忆**：Agent 做过 100 次类似任务，每次从零开始——缺少跨会话经验复用

OpenViking 的 L0/L1/L2 三级加载思想（"用结构化摘要层先定位，再按需加载详情"）为解决前两个问题提供了方向，但其文件系统范式不适用于对话级原子记忆。本设计的核心思路是：**将 Viking 的结构导航思想内化到每条记忆的结构字段里**——不是外挂目录树，而是让每条记忆自带 summary + keywords。

约束：
- 不引入新依赖（embedding 仍用现有 provider，关键词匹配用 Jaccard）
- 不改变存储引擎（仍用 PG + Milvus/TF-cosine + Neo4j）
- 降级兼容：未迁移的存量记忆保留 content-based embedding，功能不受影响

## Goals / Non-Goals

**Goals:**

- 每条 LTM 记忆携带 `summary`（摘要标题）+ `keywords`（检索关键词）+ `content_scope`（项目路径，可空）
- embedding 基于 summary 计算，信号更集中
- recall 改为双路打分：summary embedding 语义匹配 + keyword Jaccard 匹配
- 新增 `category="case"` 任务经验记忆，在任务结束时从会话摘要中提取
- Case 记忆有独立生命周期参数（更长 TTL、更慢衰减）
- 存量记忆后台异步迁移

**Non-Goals:**

- 不做 Viking 式的外挂目录树（L0/L1/L2 文件系统范式）
- 不引入 BM25 或倒排索引（Jaccard 足够）
- 不存双 embedding（summary_embedding + content_embedding），避免存储和写入成本翻倍
- 不改 Preference 系统（KV 偏好提取和存储不变）
- 不改 GraphMemory 的 Cypher 逻辑（BELONGS_TO 边已支持）
- 不改 ShortTerm / SessionMemory（短期记忆和会话摘要不变）
- 不改 PromptAssembler 的 Slot 系统（RecallSource 透传层无需改动）
- 不做两级递进加载（先返回 summary 再按需加载 content）——ReAct loop 中多一次工具调用 = 多一轮 LLM 推理，成本不划算

## Decisions

### D1: embedding 基于 summary 而非 content

**选择**：`embedding = embed(summary)`

```
现在:  embedding = embed(content)     ← 80 词, 信号稀释
改造后: embedding = embed(summary)    ← 5-10 词, 信号集中
```

**替代**：同时存 `summary_embedding` + `content_embedding`（存储翻倍，写入时两次 embed 调用）

**理由**：summary 是 content 的浓缩，embedding 信号更集中。consolidation 去重时可用 content 级 TF-cosine 做二次确认，不需要第二个 embedding。存储和写入成本不增加。

### D2: 关键词匹配用 Jaccard 而非 BM25

**选择**：Jaccard 相似度

```python
def keyword_score(query_tokens, item_keywords) -> float:
    intersection = query_set & kw_set
    union = query_set | kw_set
    return len(intersection) / len(union) if union else 0.0
```

**替代**：BM25（需要倒排索引，引入复杂度）

**理由**：keywords 每条只有 3-5 个，Jaccard 足够精确。BM25 的优势在大规模文档检索，不适用于 3-5 个关键词的匹配场景。零新依赖。

### D3: keywords 与 tags 保持独立

**选择**：两个字段独立

| 字段 | 用途 | 例子 |
|------|------|------|
| `tags` | 结构化分类标签（少，固定枚举） | `["name"]` — 用于 `recall_by_filter` 的 `require_tags` |
| `keywords` | 检索关键词（多，自由文本） | `["TypeScript", "React"]` — 用于关键词匹配 |

**替代**：合并为一个字段

**理由**：语义不同，合并会导致过滤逻辑混乱。tags 是程序化过滤的枚举，keywords 是语义检索的补充信号。

### D4: 返回时 summary + content 一起返回（单次调用）

**选择**：`memory_recall` 一次返回 `{summary, keywords, content, score}`

**替代**：先返回 summary，Agent 按需再调 `memory_recall_detail(id)` 拿 content（Viking 的 L0→L2 递进）

**理由**：ReAct loop 中多一次工具调用 = 多一轮 LLM 推理。记忆条目本身 15-80 词，全量返回的 token 成本可控（top_k=3 时 ~100-240 词）。如果未来记忆量暴增可改为两级。

### D5: Case 提取在任务结束时触发，不在每轮触发

**选择**：任务/会话结束时一次性提取

**替代**：每轮对话后提取（类似 LTM 提取）

**理由**：case 经验是从完整任务流程中沉淀的，单轮对话不足以判断"什么做法有效"。每轮提取会产生大量低质量碎片。SessionMemory 已有增量摘要，case 提取复用摘要即可。

### D6: Case 记忆有独立 TTL 和衰减率

| 维度 | Case 记忆 | 普通 LTM 记忆 |
|------|----------|-------------|
| TTL | 90 天 | 30 天 |
| 衰减率 | 0.998 | 0.995 |
| 最低重要度 | 0.4 | 0.3 |
| 去重阈值 | 0.90 | 0.95 |

**理由**：任务经验比事实更有长期价值。"先跑测试再改代码"这种经验 90 天后仍然有效，而"用户上次提到要重构"这种事实 30 天后可能已过时。去重阈值更宽松（0.90 vs 0.95），允许相似但不完全相同的经验共存（不同场景下的不同做法都有参考价值）。

### D7: content_scope 字段可空

**选择**：content_scope 是可选字段，大多数记忆不需要填

**替代**：强制每条记忆都关联项目路径

**理由**：很多记忆是用户级的（"用户叫张三"），不关联具体项目。强制填写会产生无意义的值。只有明确关联到项目/目录的记忆才填写（如"项目用 Next.js 16" → scope=项目路径）。

### D8: 双路打分权重分配

```
score = semantic_sim * 0.5 + keyword_match * 0.2 + importance * 0.3
```

**理由**：
- 语义匹配（0.5）是主信号——summary embedding 是最精准的语义定位
- 关键词匹配（0.2）是补充信号——弥补语义盲区（专有名词缩写），权重低不会主导排序
- 重要度（0.3）是先验信号——高重要度记忆应优先召回

keywords 权重仅 0.2，不会让关键词噪声主导排序。prompt 中明确要求"不要用通用词"（如 user、project、system）。

### D9: 新增字段与现有字段的关系

| 现有字段 | 新增字段 | 关系 |
|---------|---------|------|
| `category` | `summary` | category 是粗分类（7 类 + case），summary 是细粒度标题。互补。 |
| `tags` | `keywords` | tags 是结构化分类标签（少，固定枚举），keywords 是检索关键词（多，自由文本）。各司其职。 |
| `slot_hint` | `content_scope` | slot_hint 用于 PromptAssembler 槽位路由，content_scope 用于上下文隔离。不合并。 |
| `content` | `summary` | content 是完整内容，summary 是浓缩标题。summary 用于 embedding 匹配，content 用于返回。 |
| `embedding` | — | 从 `embed(content)` 改为 `embed(summary)`，列名不变。 |

## Risks / Trade-offs

### R1: summary 质量依赖 LLM

**风险**：LLM 生成的 summary 质量不稳定，可能过于笼统或偏离 content。

**缓解**：prompt 中给出明确的 summary 规则和示例；consolidation 合并时保留信息更丰富的 summary；迁移脚本对失败条目保留原 content-based embedding，功能不受影响。

### R2: 关键词匹配可能引入噪声

**风险**：keywords 匹配可能命中语义不相关的记忆（如 query 中的 "system" 命中了 keywords 中的 "system"）。

**缓解**：keywords 权重仅 0.2（vs 语义 0.5 + 重要度 0.3），不会主导排序。prompt 中明确要求"不要用通用词"。

### R3: Case 提取增加 LLM 调用成本

**风险**：每个任务结束都调一次 LLM 做 case 提取，增加成本。

**缓解**：只在 SessionMemory 摘要存在时触发（已有摘要，不额外加载对话）；LLM 返回空时跳过存储；可配置开关 `case_extraction_enabled`。

### R4: 存量迁移需要时间

**风险**：现有记忆条目需要逐一调 LLM 生成 summary/keywords，大批量迁移耗时。

**缓解**：后台异步迁移，不阻断服务；单条失败不中断；迁移前后的记忆都能正常被 recall（未迁移的用 content-based embedding，迁移后的用 summary-based embedding，两者兼容）。

### R5: embedding 语义变更导致短期召回波动

**风险**：迁移后 embedding 从 content-based 变为 summary-based，短期内召回结果可能与用户预期不同。

**缓解**：summary 是 content 的浓缩，语义方向一致，只是精度提升。迁移后应做 A/B 对比（相同 query 在迁移前后的 recall 结果对比），确认召回质量提升而非下降。

## Migration Plan

### 存量迁移策略

```python
async def migrate_existing_memories(ltm, generate_fn, embed_fn):
    for item in ltm.items:
        if item.summary:
            continue  # 已迁移

        # 1. 调 LLM 生成 summary + keywords
        # 2. 重新计算 embedding (基于 summary)
        # 3. 写回 PG
        # 失败时保留原 embedding (基于 content), 不阻断迁移
```

- 后台异步执行，不阻断服务启动
- 单条失败不中断，跳过继续
- 迁移完成后，未迁移的记忆保留 content-based embedding，功能不受影响（只是匹配精度较低）
- 兼容性：recall 时 summary 为空的条目仍参与 cosine 匹配（embedding 存在即可），只是 keyword_score 为 0

### 数据库 Migration

```sql
ALTER TABLE long_term_memory ADD COLUMN summary TEXT DEFAULT '';
ALTER TABLE long_term_memory ADD COLUMN keywords TEXT[] DEFAULT '{}';
ALTER TABLE long_term_memory ADD COLUMN content_scope TEXT DEFAULT '';

-- 可选索引 (用于按项目过滤)
CREATE INDEX idx_ltm_content_scope ON long_term_memory(content_scope)
    WHERE content_scope IS NOT NULL AND content_scope != '';
```

### 回滚策略

- 新增列均有 DEFAULT 值，回滚时 `ALTER TABLE ... DROP COLUMN` 即可
- embedding 语义变更不影响列结构——回滚代码后新写入的 embedding 重新基于 content，旧 summary-based embedding 仍可被 cosine 匹配（只是匹配基准不同）
- Case 记忆可通过 `WHERE category = 'case'` 批量删除

## Open Questions

- **Case 提取触发时机细化**：设计文档中列出了三个触发点（会话标题生成时、Agent run 结束时、显式调用），实际实现时需要确认哪个是主触发路径，避免重复提取。
- **关键词同义词扩展**：设计文档提到"同义词扩展后命中"，但当前 Jaccard 实现不做同义词扩展。是否需要在 Phase 2 引入轻量同义词表（如 embedding 近邻词）？初版不做，观察召回质量后再定。
- **Case dedup_threshold 语义矛盾**：设计文档设定 `case_dedup_threshold=0.90`（低于普通的 0.95），注释为"更宽松，允许相似经验共存"。但代码中 dedup 逻辑是 `if sim >= dedup_threshold: dedup`——阈值越低，越多数对被判定为重复并合并。因此 0.90 实际上比 0.95 **更激进**地合并，与"允许共存"的意图矛盾。如果确实要让相似经验共存，阈值应**更高**（如 0.98）。当前 spec 暂时忠实记录设计文档的值（0.90），实现前需与设计者确认：是改阈值为 0.98，还是调整注释描述。
