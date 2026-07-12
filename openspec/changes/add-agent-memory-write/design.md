# Design — add-agent-memory-write

## 背景与定位

当前 AChat 的记忆写入是单向的——系统后台从 assistant 回复中被动提取，Agent 没有主动写入能力。这限制了记忆的质量和及时性：

- Agent 在对话过程中发现的关键信息（"用户项目用 Next.js 16"）如果不出现在回复正文中，就不会被提取
- 后台提取器的 k-v 格式（`{"key": "value"}`）丢失了上下文——Agent 自己能写出更好的自包含记忆
- Agent 无法更新或修正已有记忆（如"上次说的 React 18 其实是 React 19"）

Claude Code 通过让 Agent 直接操作文件来解决这个问题，但也带来了"什么都写"的风险。AChat 需要在赋予 Agent 写入能力的同时，用系统级硬约束控制写入质量。

## 决策

### D1. 工具设计：memory_store

```python
memory_store_tool = ToolDef(
    name="memory_store",
    description=(
        "Store a long-term memory that will persist across conversations. "
        "ONLY store facts that are: "
        "(1) long-lived and stable (tech stack, project constraints), "
        "(2) affect future tasks (deployment failures, API quirks), "
        "(3) have long-term learning value. "
        "DO NOT store: temporary conversation details, "
        "information derivable from code, single-use operation results, "
        "or anything already in the agent's system prompt."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Self-contained memory content. "
                               "Must be understandable without conversation context. "
                               "Example: 'User project uses React 19 + Next.js 16 with App Router'",
            },
            "category": {
                "type": "string",
                "enum": ["fact", "policy", "tool_failure"],
                "description": "fact=objective fact about user/project/environment, "
                               "policy=constraint or rule to follow, "
                               "tool_failure=lesson learned from a tool failure",
            },
            "importance": {
                "type": "number",
                "minimum": 0.3,
                "maximum": 1.0,
                "description": "0.3=minor, 0.5=normal, 0.8=critical, 1.0=identity-level",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for filtering during recall.",
            },
        },
        "required": ["content", "category", "importance"],
    },
    handler=memory_store_handler,
)
```

**选择**：category 限制为 `fact`/`policy`/`tool_failure` 三类。
**理由**：`identity`/`preference` 走 Preference store（KV 覆盖语义），不适合 LTM 的追加语义。`general`/`episodic` 噪声太高，Agent 容易滥用。只允许有明确 recall 价值的 category。

### D2. 硬约束：handler 级三道防线

```python
async def memory_store_handler(args, ctx):
    # 防线 1: category 白名单（JSON schema enum 已限制，handler 再校验）
    category = args.get("category", "")
    if category not in ("fact", "policy", "tool_failure"):
        return err(f"category must be one of: fact, policy, tool_failure")

    # 防线 2: importance 下限
    importance = float(args.get("importance", 0))
    if importance < 0.3:
        return err("importance must be >= 0.3")

    # 防线 3: 每轮写入限流
    rate_key = f"mem_writes:{ctx.agent_id}:{ctx.run_id}"
    count = await _rate_limiter.incr(rate_key, ttl=300)  # 5 分钟窗口
    if count > 3:
        return err("memory_store rate limit: max 3 writes per agent run")

    # 走已有的 store_classified 路径（含 cosine dedup）
    content = args["content"].strip()
    if not content or len(content) > 500:
        return err("content must be 1-500 characters")

    emb = None
    if embed_fn:
        emb = await asyncio.to_thread(embed_fn, content)

    inserted = await ltm.store_classified(
        content=content,
        importance=importance,
        emb=emb,
        category=category,
        tags=args.get("tags", []),
        slot_hint=_SLOT_BY_CATEGORY.get(category, ""),
        scope="agent",           # 需 agent-scoped-memory 变更
        agent_id=ctx.agent_id,   # 需 agent-scoped-memory 变更
    )

    # 软约束：返回当前记忆数量让 Agent 感知
    agent_mem_count = sum(
        1 for it in ltm.items
        if it.scope == "agent" and it.agent_id == ctx.agent_id
    )
    return ok({
        "stored": inserted,  # True=新插入, False=dedup命中已更新
        "agent_memory_count": agent_mem_count,
    })
```

### D3. 限流实现：内存级简单计数器

```python
class SimpleRateLimiter:
    """In-memory rate limiter using dict with TTL."""
    def __init__(self):
        self._counts: Dict[str, Tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    async def incr(self, key: str, ttl: int = 300) -> int:
        async with self._lock:
            now = time.time()
            count, expires = self._counts.get(key, (0, now + ttl))
            if now > expires:
                count, expires = 0, now + ttl
            count += 1
            self._counts[key] = (count, expires)
            return count
```

**选择**：内存级 dict + TTL，不持久化。
**理由**：限流是运行时约束，不需要跨重启。进程重启后限流重置可接受——Agent 不会在启动瞬间写入大量记忆。

### D4. Agent 定义：memory_enabled 标志

`agents` 表新增 `memory_enabled BOOLEAN DEFAULT FALSE`。

- `memory_enabled=true` 的 Custom Agent：自动注入 `memory_store` + `memory_recall` 工具
- `memory_enabled=false`（默认）：不注入
- CLI Agent（Claude/Codex）：不注入（CLI 自管理上下文和工具）
- Mock Agent：不注入

注入位置：`agent_runner.py` 的 `build_adapter_input` 中，在 tool_names 列表构建时按 `memory_enabled` 添加。

### D5. 与已有后台抽取的关系

| 写入路径 | 来源 | category | 是否本变更修改 |
|---------|------|----------|--------------|
| `extract_memory_from_reply` | 后台 LLM 抽取 | fact/policy/tool_failure → LTM; identity/preference → Preference | 不修改 |
| `memory_store` 工具 | Agent 主动调用 | fact/policy/tool_failure | 新增 |

两条路径写入同一个 LTM 表（通过 `store_classified`），共享 dedup 逻辑。Agent 主动写的记忆和后台抽取的记忆会在 recall 时合并返回。

**不取消后台抽取**：后台抽取仍有价值——Agent 不一定每次都主动记忆，后台抽取是兜底。两条路径互补。

### D6. 向后兼容

- 若 `add-agent-scoped-memory` 变更未实施，`memory_store` 写入 `scope='global', agent_id=NULL`
- `store_classified` 的 `scope` 和 `agent_id` 参数是可选的（默认 global）
- 限流器是内存级的，进程重启即重置

## 不做

- 不做 `memory_delete` 工具（Agent 不应能删除记忆——删除由用户通过 UI 或 consolidation 负责）
- 不做 `memory_update` 工具（Agent 用 `memory_store` 写新记忆，cosine dedup ≥0.95 自动合并到已有条目）
- 不改后台 `extract_memory_from_reply` 路径（两条路径并行互补）
- 不做 CLI Agent 的记忆工具注入
- 不做记忆上限配额（consolidation 的 decay+expire 已能控制膨胀；后续可加 per-agent 上限）
