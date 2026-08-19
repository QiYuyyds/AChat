# Design: enhance-memory-pipeline-quality

## Context

AChat 记忆系统在 `optimize-memory-pipeline-execution` 变更中完成了执行层基础设施（FileCatalog、NodeSearch、Wikilink Predicate、HybridSearch 评分分解等）。与 ReMe 对比后，发现剩余差距集中在 **Prompt 质量、Bucket 分类、搜索召回面、运行时断链、Topic 去重** 五个维度。

本次变更的约束：
- **不改变 LLM-as-Function 架构**——AChat 用 `generate_fn` 单次调用 LLM 后由代码代执行副作用，这比 ReMe 的 Agent-as-Tool 更省 token、更可测试。质量差距通过更聪明的代码侧逻辑弥补。
- **不引入新依赖**——纯 Python 标准库 + 已有项目依赖。
- **不涉及 DB schema 变更**——`bucket` 字段已是 TEXT 类型，`personal` 值无需迁移。
- **不涉及事件协议变更**——所有变更在 pipeline 内部。

## Goals / Non-Goals

**Goals:**

- dream_extract Prompt 质量接近 ReMe（反摘要、质量闸口、噪声过滤）
- dream_integrate 召回覆盖面 ×4（从 limit=5 → 两轮 limit=10 合并去重取 top-5）
- 支持 `personal` bucket（用户偏好/约定与通用知识分离）
- 文件重命名时 wikilink 自动 retarget（不断链）
- dream_extract 输入保护（排除 .yaml）+ 输出验证（source_paths 在 changed_paths 内）
- Topic 标题归一化去重（小写 + 去标点）
- Session JSONL 时间戳别名归一化

**Non-Goals:**

- 不改为 Agent-as-Tool 架构（LLM 不直接操作文件系统）
- 不引入 Neo4j 或其他图数据库
- 不引入 Step DAG / DreamState 持久化
- 不引入双语 Prompt 模板系统（当前英文 prompt + "Match the language" 指令已够用）
- 不实现 Virtual Node 概念（SQLite 单表 wikilink 图已满足需求）

## Decisions

### D1: Prompt 强化策略 — 增量补关键语句，不重写

**决策**：在现有 `_EXTRACT_SYSTEM_PROMPT` / `_INTEGRATE_SYSTEM_PROMPT_*` 中增量补充 ReMe 的关键指导语，而非整体重写。

**理由**：现有 prompt 已经包含 bucket 分类、output format、wikilink rules 等框架，缺失的是质量门控和反摘要指导。增量补入风险最低。

**补入内容**：

| Prompt | 补入内容 |
|--------|---------|
| `_EXTRACT_SYSTEM_PROMPT` | "Prefer fewer, richer units over exhaustive file summaries"、"This extraction step is the gate for not worth memorizing"、"Do not emit passing mentions, known-concept recaps, one-off timestamps, attendance facts"、"Merge evidence from multiple files when it teaches the same abstraction" |
| `_INTEGRATE_SYSTEM_PROMPT_PROCEDURE` / `_WIKI` | "UPDATE must be additive: never remove existing wikilinks or derived_from entries"、"Default to weaving more, not less" |

### D2: Personal Bucket — 复用已有字段 + 新增 Prompt 模板

**决策**：

1. `MemoryFrontmatter.bucket` 合法值从 `{"procedure", "wiki"}` 扩展为 `{"procedure", "personal", "wiki"}`
2. 新增 `_INTEGRATE_SYSTEM_PROMPT_PERSONAL`，正文形态为 "rule of engagement"（Rule/Why/How to apply），参考 ReMe personal bucket prompt
3. `auto_dream._dream_integrate` 的 bucket 路由加 `personal` 分支
4. `auto_dream._dream_extract` 的 system prompt 中 bucket 分类说明加 `personal`

**理由**：`bucket` 已是 TEXT 字段无 enum 约束，不需要 DB 迁移。个人偏好（如"用户偏好小 PR"）与通用知识（如"小 PR 更容易 review"）混在 wiki bucket 会降低召回精度。

**备选方案**：不加 personal bucket，全放 wiki → 用户偏好被淹没在通用知识中，召回时无法区分优先级。**否决**。

### D3: 多轮搜索 — 代码侧两轮合并，不让 LLM 自主调工具

**决策**：在 `_dream_integrate` 中执行两轮 `node_search.search()`：

```
hits_1 = node_search.search(query=name, bucket=bucket, limit=10)    # 精确匹配
hits_2 = node_search.search(query=summary, bucket=bucket, limit=10) # 语义扩展
existing_nodes = _dedupe_by_path(hits_1 + hits_2)[:5]               # 合并去重取 top-5
```

**理由**：ReMe 让 LLM 自主调用 `node_search`（limit=20-30，可多次调用），AChat 的 LLM-as-Function 架构做不到。但代码侧做两轮搜索（name + summary）可以覆盖大部分召回需求，总召回面 20 ≈ ReMe 的 20-30。不需要改架构。

**备选方案**：单轮搜索 limit=20 → 返回结果过多，LLM prompt 膨胀，且 name 和 summary 的搜索意图不同应分开执行。**否决**。

### D4: 运行时 Retarget — 补调用链，不新增函数

**决策**：`retarget_wikilinks()` 函数已存在于 `wikilinks.py`，只需在 `auto_memory._update_card` 中文件名变化时补调用：

```python
if new_name and new_name != old_name:
    # 写新文件 → 删旧文件 → retarget 所有引用旧路径的 wikilinks
    old_rel = old_filepath.relative_to(workspace.root)
    new_rel = new_filepath.relative_to(workspace.root)
    for f in workspace.all_md_files():
        content = f.read_text()
        if old_rel in content:
            f.write_text(retarget_wikilinks(content, old_rel, new_rel))
    # 更新 expander 图
    expander.remove_all_for(old_rel)
    # 重新索引新文件（expander.add_edges_detailed）
```

**理由**：`retarget_wikilinks()` 已实现且已测试，只是没有被 pipeline 调用。这是补调用链而非新开发。

**范围限制**：只在 `auto_memory._update_card` 中实现（daily card 重命名），`auto_dream` 不涉及文件重命名（它只 CREATE 新文件或 UPDATE 已有文件内容，不改文件名）。

### D5: Topic 归一化去重 — 简单 regex，不引入分词

**决策**：

```python
def _normalize_topic_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"[^\w\s]", "", title)  # 去标点
    title = re.sub(r"\s+", " ", title)      # 压缩空格
    return title
```

在 `_dream_topics` 的 `recent_titles` 和 `existing_titles` 比较中使用归一化形式。

**理由**：ReMe 的 `normalize_topic` 也是简单的小写 + 去标点。不需要 jieba 分词——topic 标题通常很短，精确归一化已足够。

### D6: Session JSONL 时间戳归一化 — 别名映射

**决策**：在 `write_session_jsonl` 的 `_sanitize_msg_for_save` 之前调用 `_normalize_timestamp`：

```python
_TIMESTAMP_ALIASES = ("time_created", "timestamp", "createdAt", "timeCreated", "created_time")

def _normalize_timestamp(msg: dict) -> dict:
    if msg.get("created_at"):
        return msg
    for key in _TIMESTAMP_ALIASES:
        if msg.get(key):
            return {**msg, "created_at": msg[key]}
    return msg
```

**理由**：AChat 的消息来自不同 adapter（Claude CLI / Codex CLI / Custom SDK），时间戳字段名不统一。归一化后 `created_at` 字段一致，便于 session JSONL 的排序和去重。

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Personal bucket 的 LLM 输出可能不稳定（新 prompt 模板） | Prompt 模板参考 ReMe 已验证的 personal bucket prompt 结构；集成测试覆盖 personal bucket 的 CREATE/CORROBORATE/REFINE 路径 |
| 多轮搜索增加 2× BM25 查询开销 | BM25 是 SQLite FTS5 本地查询，单次 <1ms，两轮 <2ms，可忽略 |
| Retarget 需要扫描所有 .md 文件 | 仅在 auto_memory `_update_card` 文件名变化时触发（低频操作）；workspace 文件量通常 <1000，全扫 <100ms |
| Topic 归一化可能误判（如 "C++" 和 "cpp" 归一化后不同） | 归一化只做小写 + 去标点，不做同义词扩展，保守策略；误判只影响去重（多保留一条 topic），不影响数据正确性 |
| Extract Prompt 改动可能影响已有 LLM 输出格式 | 只增加指导语，不改变 output format 定义（JSON schema 不变）；已有集成测试验证 JSON 解析 |
