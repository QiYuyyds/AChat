# Design — add-memory-transparency-ui

## 背景与定位

AChat 的记忆数据全在 PG 表里，用户没有查看/编辑/删除的入口。这在本地单用户场景下有两个问题：

1. **信任问题**：用户不知道 Agent 记住了什么，不知道记忆是否准确，无法纠正错误记忆
2. **治理问题**：错误/过时/重复的记忆只能等 consolidation 自动清理，用户无法主动管理

Claude Code 通过"全文件 + UI 暴露"解决了这个问题——用户直接看 markdown 文件。AChat 用 PG 存储，需要通过 API + UI 提供同等透明度。

## 决策

### D1. API 设计：RESTful CRUD

```
长期记忆 (LTM)
─────────────────────────────────────────
GET    /api/memory/long-term
       ?agent_id=agt_xxx    按 Agent 过滤
       ?category=fact        按 category 过滤
       ?tag=tech_stack       按 tag 过滤
       ?page=1&size=20       分页

PUT    /api/memory/long-term/{id}
       body: { content?, importance?, category?, tags? }
       → 编辑后异步重算 embedding

DELETE /api/memory/long-term/{id}
       → 同步删除 GraphMemory 节点 + 边

用户偏好 (Preference)
─────────────────────────────────────────
GET    /api/memory/preferences

PUT    /api/memory/preferences/{key}
       body: { value }

DELETE /api/memory/preferences/{key}

会话摘要 (Session Memory)
─────────────────────────────────────────
GET    /api/memory/session/{conversation_id}
       → 返回 Session Memory 文本（只读）
```

**选择**：独立的 `/api/memory/*` 路由前缀，不嵌套在 conversation 下。
**理由**：记忆是跨会话的全局资源，不归属于某个会话。LTM 按 agent_id 过滤而非 conversation_id。

### D2. LTM 编辑后 embedding 重算

```python
@router.put("/api/memory/long-term/{memory_id}")
async def update_ltm_memory(memory_id: int, body: LTMUpdateRequest):
    async with get_db() as session:
        row = await session.get(LongTermMemory, memory_id)
        if not row:
            raise HTTPException(404, f"Memory {memory_id} not found")

        old_content = row.content
        if body.content is not None:
            row.content = body.content
        if body.importance is not None:
            row.importance = body.importance
        if body.category is not None:
            row.category = body.category
        if body.tags is not None:
            row.tags = body.tags

        # content 变了 → 异步重算 embedding
        if body.content is not None and body.content != old_content:
            asyncio.create_task(_recompute_embedding(memory_id, body.content))

        # 同步内存中的 Item
        _sync_ltm_item_in_memory(memory_id, row)

    return {"ok": True}
```

**选择**：content 变更时异步重算 embedding，不阻塞 API 响应。
**理由**：embedding 计算需要调外部 API（~100ms），用户编辑时不需要等待。旧 embedding 在新算完之前暂时不一致，但 recall 时用旧 embedding 的窗口很短。

### D3. 删除时同步清理 GraphMemory

```python
async def delete_ltm_memory(memory_id: int):
    # 1. 删 Neo4j 节点 + 边
    if graph_memory and graph_memory._available():
        try:
            await graph_memory.delete_from_graph(memory_id)
        except Exception:
            pass  # Neo4j 不可用时静默

    # 2. 删 PG 镜像表
    async with get_db() as session:
        await session.execute(
            delete(MemoryNode).where(MemoryNode.mem_id == memory_id)
        )
        await session.execute(
            delete(MemoryEdge).where(
                (MemoryEdge.from_id == memory_id) | (MemoryEdge.to_id == memory_id)
            )
        )
        # 3. 删 LTM 行
        await session.execute(
            delete(LongTermMemory).where(LongTermMemory.id == memory_id)
        )

    # 4. 同步内存
    _remove_ltm_item_from_memory(memory_id)
```

### D4. 前端 UI 布局

```
设置面板
├── 已有 Tab（Agent 管理 / API Key / ...）
└── 记忆管理 (新增)
    ├── 长期记忆
    │   ├── 筛选栏: Agent 下拉 + Category 下拉 + 搜索框
    │   ├── 表格: [content | category | importance | tags | agent | created]
    │   ├── 行操作: [编辑] [删除]
    │   └── 分页
    ├── 用户偏好
    │   ├── KV 列表: [key | value | updated_at]
    │   └── 行操作: [编辑] [删除]
    └── 会话摘要
        ├── 会话列表: [conversation_title | updated_at]
        └── 点击 → 展开摘要文本（只读）
```

**选择**：设置面板内新增 Tab，不做独立页面。
**理由**：记忆管理是"配置类"操作，与 Agent 管理、API Key 同级。独立页面会增加导航深度。

### D5. 编辑/删除确认

- 编辑 LTM content：inline edit + 保存按钮，不需二次确认（可撤销）
- 删除 LTM：二次确认弹窗 "确定删除这条记忆？此操作不可撤销"
- 编辑 Preference value：inline edit + 保存
- 删除 Preference：二次确认

### D6. 不暴露 embedding

API 响应中不包含 `embedding` 字段——只返回 `content`、`importance`、`category`、`tags`、`agent_id`、`created_at`、`last_accessed`、`scope`。

**理由**：embedding 是内部实现细节，对用户无意义，且体积大（1536 float）。

## 不做

- 不做记忆的批量导入/导出（后续可加）
- 不做记忆的版本历史（编辑覆盖原值，不保留 diff）
- 不做 GraphMemory 图结构的可视化（节点/边的图形展示超出本变更范围）
- 不做记忆搜索的全文检索（用 category/tags 过滤 + content LIKE 即可，不引入 ES）
- 不做 Session Memory 的编辑（只读——Session Memory 是系统自动维护的摘要，用户不应手动编辑）
