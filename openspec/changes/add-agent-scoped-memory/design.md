# Design — add-agent-scoped-memory

## 背景与定位

AChat 的 LTM 是从 AGI-memory 移植的全局单池模型——一张 `long_term_memory` 表、一个 `LongTerm` 实例、一份 `self.items` 列表。所有 Agent 的记忆写入同一池，recall 时对所有记忆做无差别语义搜索。

这在单 Agent 场景下没问题。但 AChat 是多 Agent 平台：用户可以在一个群聊中同时与代码 Agent、文档 Agent、Orchestrator 对话，这些 Agent 的记忆经验完全不同——代码 Agent 需要记住"用户项目用 TypeScript"，文档 Agent 需要记住"用户偏好 Markdown 格式"。混在一起 recall 时，代码 Agent 会收到文档 Agent 的格式偏好记忆，造成噪声。

本变更不改变记忆的存储引擎（仍用 PG + embedding + Neo4j graph），只在逻辑层增加 scope 维度。

## 决策

### D1. scope 模型：两层（global + agent），不做 user/conversation 维度

```
┌─────────────────────────────────────────────────┐
│            long_term_memory 表                    │
│                                                   │
│  scope='global'  agent_id=NULL                   │
│    ├─ 跨 Agent 共享的事实（天气、通用政策）        │
│    └─ 存量数据迁移到此                            │
│                                                   │
│  scope='agent'   agent_id='agt_xxx'              │
│    ├─ Agent A 的专属记忆（技术栈偏好）            │
│    ├─ Agent B 的专属记忆（文档格式偏好）          │
│    └─ Agent C 的专属记忆（工具失败经验）          │
└─────────────────────────────────────────────────┘
```

**选择**：只做 `global` 和 `agent` 两个 scope。
**替代**：做 `user` / `conversation` / `agent` / `global` 四层。
**理由**：AChat 当前是本地单用户（`default_user`），`user` scope 无意义；`conversation` scope 已由 ShortTerm + ChatHistory 覆盖。先做最小有用的隔离层，不过度设计。

### D2. 写入路由：LLM 抽取结果默认写 agent scope

`extract_memory_from_reply` 的调用方（`MemoryService.on_message_end`）已知当前 `agent_id`（从 conversation→agent 关联获取）。抽取结果默认写入 `scope='agent', agent_id=<current>`。

全局事实（如天气、通用政策）的处理：
- **方案 A（默认）**：所有 LLM 抽取结果都写 agent scope。如果某个事实对多个 Agent 有用，靠 consolidation 的 dedup 不跨 scope 合并，各自保留一份。可接受——embedding 计算成本低，同一事实在不同 Agent 上下文下的措辞可能不同。
- **方案 B（后续可选）**：在 `memory_store` 工具参数里加 `scope` 字段，Agent 可主动声明"这条记忆是全局的"。本变更不做。

### D3. 召回策略：Agent-scoped 优先 + global 补充

```python
async def recall(self, query: str, top_k: int = 3, agent_id: str = "") -> List[Item]:
    # Phase 1: agent-scoped recall
    agent_items = [it for it in self.items
                   if it.scope == "agent" and it.agent_id == agent_id]
    agent_results = self._semantic_search(query, agent_items, top_k)

    # Phase 2: global recall (fill remaining slots)
    global_items = [it for it in self.items if it.scope == "global"]
    remaining = max(0, top_k - len(agent_results))
    global_results = self._semantic_search(query, global_items, remaining)

    # Merge: agent results first, then global
    return agent_results + global_results
```

**选择**：先 agent scoped 拿 top_k，不足时用 global 补。
**替代**：合并后统一排序。
**理由**：Agent 自己的经验比全局共享的事实更相关——同一 Agent 的记忆在措辞和上下文上更一致。优先返回 agent scoped 结果可减少跨 Agent 噪声。

### D4. Consolidation 按 scope+agent_id 分组

`consolidate()` 不再对全量 `self.items` 做 pairwise dedup，而是按 `(scope, agent_id)` 分组，组内做 dedup/merge/expire。跨 scope 不合并。

**理由**：不同 Agent 的记忆即使 embedding 相似也不应合并——它们代表不同 Agent 视角下的经验。

### D5. GraphMemory 的 scope 适配

Neo4j 的 `Memory` 节点增加 `scope` 和 `agent_id` 属性。`find_related()` 查询按 `agent_id` 过滤——只扩展同一 Agent 的图邻居，不跨 Agent 扩展。

`add_to_graph()` 写入时携带 `scope` 和 `agent_id`。

### D6. 向后兼容：存量数据迁移

```sql
ALTER TABLE long_term_memory ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'global';
ALTER TABLE long_term_memory ADD COLUMN agent_id VARCHAR NULL;
CREATE INDEX idx_ltm_scope_agent ON long_term_memory(scope, agent_id);
```

存量行自动获得 `scope='global'`、`agent_id=NULL`。不需数据搬运。

## 不做

- 不做 `user` scope（当前 `default_user` 无意义）
- 不做 `conversation` scope（ShortTerm + ChatHistory 已覆盖）
- 不做 Agent 间记忆共享/同步机制（Agent 各自独立积累）
- 不改 Preference store（Preference 本身就是全局的，不按 Agent 隔离——用户偏好是跨 Agent 的）
- 不做 Agent 记忆上限配额（后续可加，当前 consolidation 的 decay+expire 已能控制膨胀）
