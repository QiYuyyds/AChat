## Context

AgentHub 的 RAG 混合检索（`HybridStore`）设计了三路 RRF 融合：Milvus 语义向量、ES BM25 关键词、Neo4j 知识图谱。前两路已通过 `_wire_milvus_to_rag` 和 `_wire_es_to_rag` 接入，但 KG 路径从未 wire——`set_kg_backend` 从未被调用，`_fetch_kg` 中 `self._kg_search_fn` 为 `None`，`_kg_ok()` 永远返回 `False`。

AGI-memory 项目已实现完整的知识图谱模块（`internal/graph/`），包含 LLM 实体关系抽取器（Extractor）和 Neo4j 存储层（KGStore），但使用同步 Neo4j 驱动。AgentHub 的 GraphMemory 已完成同步→async 适配（使用 `AsyncDriver` + `async with session`），KGStore 需沿用同一模式。

当前 `_fetch_kg` 中存在一个遗留 bug：`hits = self._kg_search_fn(query, fetch_k)` 是同步调用，但 ES 路径已修复为 `await`。KG 搜索函数将是 async 的，因此需要同步修复为 `await`。

## Goals / Non-Goals

**Goals:**
- 将 AGI-memory 的 graph 模块（types/extractor/kgstore）移植到 `backend/app/graph/`
- KGStore 的 Neo4j 操作从同步改为 async，与 GraphMemory 共享同一 `AsyncDriver` 实例
- 在 `hybrid.py` 中新增 KG 入库（fire-and-forget）和搜索（await）调用
- 在 `main.py` 中新增 `_wire_kg_to_rag` wiring，复用已有 `generate_fn` 和 `neo4j_driver`
- 修复 `_fetch_kg` 中的同步调用 bug（添加 `await`）

**Non-Goals:**
- 不修改 GraphMemory 的 `:Memory` 节点和边逻辑（两者操作不同标签，互不干扰）
- 不修改 RRF 融合公式和权重计算逻辑（`hybrid.py` L251-274 已有 KG 分支）
- 不新增 API 端点或前端改动（`rag_search`/`rag_ingest` 工具契约不变）
- 不移植 `task_graph.py`（任务图，与 RAG KG 无关）
- 不改变 Neo4j schema 迁移策略（Entity/Relation 在运行时通过 MERGE 幂等写入）

## Decisions

### 决策 1：共享 AsyncDriver 实例

**选择**: KGStore 与 GraphMemory 共用 `_infrastructure.neo4j_driver`（同一 `AsyncDriver`）。

**理由**: 两者操作不同的节点标签（`:Entity` vs `:Memory`）和边类型，无写入冲突。共享驱动避免重复连接池，减少 Neo4j 连接数。

**替代方案**: 为 KGStore 创建独立 driver——增加连接管理复杂度，无实际收益。

### 决策 2：KG 入库使用 fire-and-forget

**选择**: `index_chunks` 末尾以 `asyncio.create_task(self._kg_index_fn(doc_hash, chunk_refs))` 触发 KG 入库，不阻塞主入库流程。

**理由**: 每个 chunk 需一次 LLM 调用抽取实体，41 chunks = 41 次 LLM 调用（约 30-60 秒）。若同步等待会严重拖慢文档摄入响应。KG 入库延迟不影响 Milvus/ES 检索可用性。

**替代方案**: 同步 await——用户上传文档后需等待数十秒才能得到响应，体验差。

### 决策 3：search() 返回 `List[dict]` 而非 dataclass

**选择**: KGStore.search 返回 `[{"pg_id": int, "content": "", "score": float, "entities": [...]}]`，而非 AGI-memory 原版的 `List[GraphSearchResult]`。

**理由**: `hybrid.py` 的 `_fetch_kg` 和 RRF 融合逻辑期望 `hit.get("pg_id")` 字典访问。`_materialize_kg_only` 也用 `isinstance(hit, dict)` 判断。返回 dict 避免适配层转换。

**替代方案**: 返回 dataclass 再在 `_fetch_kg` 中转换——多一层无意义的转换代码。

### 决策 4：LLM 回调保持同步签名

**选择**: Extractor 接受 `llm_fn: Callable[[str, str], str]`（同步），与 AGI-memory 原版一致。`_make_generate_fn` 已生成同步 `generate(system_prompt, user_msg) -> str`，可直接注入。

**理由**: Extractor 在 `index_document`（async 方法内部调用同步 LLM）和 `search`（async 方法内部调用同步 LLM）中使用。LLM 调用本身是 I/O 阻塞的 httpx 同步请求，但在 fire-and-forget task 中执行不影响主事件循环。改造成 async LLM 需重写 `_make_generate_fn` 为 httpx.AsyncClient，超出本次迁移范围。

**风险**: `search` 中同步 LLM 调用会阻塞事件循环约 1-3 秒。可接受——搜索本身是低频操作，且后续可优化为 async。

### 决策 5：APOC 可选，降级为直接匹配

**选择**: KGStore.search 优先使用 `apoc.path.subgraphNodes` 做 1~2 跳遍历；APOC 不可用时（Cypher 执行抛异常）降级为 `_search_direct`（直接实体匹配，无遍历）。

**理由**: APOC 是 Neo4j 插件，社区版默认不安装。降级策略保证 KG 路径在无 APOC 环境下仍能返回结果（仅精度降低——只有直接命中实体的 chunk，无关联扩展）。

### 决策 6：Entity 节点 MERGE 幂等写入

**选择**: 使用 `MERGE (e:Entity {name: $name})` 幂等写入实体节点，`MERGE (a)-[r:{rel_type}]->(b)` 幂等写入关系边。

**理由**: 文档可能被重新摄入（相同 doc_hash）。MERGE 保证不会创建重复节点/边。关系类型动态拼入 Cypher 字符串（无法参数化），安全性由 Extractor 的 `_VALID_REL_TYPES` 白名单保证。

## Risks / Trade-offs

- **[APOC 未安装]** → 降级为 `_search_direct`，仅返回直接实体匹配结果，无多跳关联扩展。日志中无 APOC 错误（被 try/except 静默捕获）。
- **[LLM API Key 未配置]** → Extractor 返回空 `ExtractResult`，`index_document` 跳过所有 chunks，`search` 返回空列表。KG 路径静默跳过，Milvus + ES 两路检索不受影响。
- **[Neo4j 不可用]** → KGStore 所有方法返回空结果/空操作，不抛异常。`_kg_ok()` 仍返回 `True`（search_fn 已注入），但 `_fetch_kg` 中 `await self._kg_search_fn()` 捕获异常后返回 `_PathHits(ok=False)`。
- **[search 中同步 LLM 阻塞事件循环]** → 每次 `rag_search` 调用时阻塞约 1-3 秒（LLM 抽取查询实体）。可接受短期 trade-off；后续可改为 async LLM 回调。
- **[fire-and-forget 入库失败不可观测]** → `asyncio.create_task` 中异常被静默吞掉。通过 logger.warning 记录失败，但调用方无法感知。可接受——KG 入库是 best-effort，失败不影响文档摄入主流程。
- **[Entity 节点 name 全局唯一]** → MERGE 以 `name` 为唯一键，不同文档的相同实体名会合并为同一节点。这是预期行为（知识图谱跨文档关联），但 `doc_hash`/`pg_id` 属性会被后写入的文档覆盖。不影响检索正确性——多文档共享同一实体时，任意 `pg_id` 都指向相关 chunk。
