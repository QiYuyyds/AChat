# Proposal: enhance-memory-pipeline-quality

## Why

AChat 记忆系统 Pipeline 在上一轮优化（`optimize-memory-pipeline-execution`）中完成了执行层基础设施（File Catalog、NodeSearch、Wikilink Predicate 等），但与 ReMe 对比仍存在**Prompt 质量不足、Bucket 分类缺失、搜索召回面窄、运行时断链、Topic 去重粗糙**等问题。这些差距直接影响记忆提取质量——dream_extract 会输出噪声 unit，dream_integrate 召回不充分导致重复创建节点，文件重命名时 wikilink 断链丢失知识图谱关联。需要在不动架构的前提下，通过 Prompt 强化 + 代码侧逻辑增强来弥合效果差距。

## What Changes

### P0 — Prompt 质量提升

- **dream_extract Prompt 补质量门控**：加入"fewer, richer units"反摘要指导、"gate for not worth memorizing"质量闸口语、"do not emit passing mentions"噪声过滤规则
- **dream_integrate Prompt 补 only-add 原则**：明确 UPDATE 必须只增不删 wikilink、default to weaving more
- **新增 Personal Bucket**：digest 支持三种 bucket（`procedure` / `personal` / `wiki`），新增 `_INTEGRATE_SYSTEM_PROMPT_PERSONAL` 模板（rule-of-engagement 格式），`auto_dream` bucket 路由加 `personal` 分支

### P1 — 代码侧多轮搜索

- **dream_integrate 两轮搜索**：第一轮用 unit name 精确搜索（limit=10），第二轮用 unit summary 语义扩展搜索（limit=10），合并去取后取 top-5 塞入 LLM prompt。召回覆盖面从 5 → 20，效果接近 ReMe 的 Agent-as-Tool 多轮 node_search

### P2 — 运行时 Wikilink Retarget + 输入保护

- **auto_memory 文件重命名 retarget**：当 `_update_card` 中 LLM 返回新 name 导致文件重命名时，调用已有的 `retarget_wikilinks()` 更新所有引用该文件的 wikilink
- **dream_extract 输入保护**：扫描时跳过 `.yaml` 文件（interests.yaml 不应作为 extract 输入）
- **dream_extract 输出验证**：`source_paths` 验证是否在 changed_paths 内，不在则 fallback 到 changed_paths[:3]
- **Topic 标题归一化去重**：`_normalize_topic_title()` 小写 + 去标点 + 去多余空格，替代精确字符串匹配

### P3 — Session JSONL 时间戳归一化

- **时间戳别名映射**：`_normalize_timestamp()` 将 `time_created` / `timestamp` / `createdAt` / `timeCreated` / `created_time` 等别名映射到统一 `created_at` 字段，在 `_sanitize_msg_for_save` 前调用

## Capabilities

### New Capabilities

（无新增 capability——本次变更全部在已有 memory pipeline 实现内增强）

### Modified Capabilities

- `memory-pipeline`: dream_extract / dream_integrate / dream_topics / auto_memory 的 Prompt 质量、搜索召回、输入保护、断链 retarget、Topic 去重、Session JSONL 时间戳归一化等行为变化

## Impact

- **代码文件**：
  - `backend/app/memory/pipeline/auto_dream.py` — Prompt 文本、bucket 路由、多轮搜索、clean_paths 验证、topic 归一化
  - `backend/app/memory/pipeline/auto_memory.py` — 文件重命名 retarget、时间戳归一化
  - `backend/app/memory/file_store/wikilinks.py` — 已有 `retarget_wikilinks()` 无需改动，只需补调用链
- **不涉及 DB schema 变更**：personal bucket 复用已有 `bucket` 字段（TEXT 类型，无 enum 约束）
- **不涉及事件协议变更**：所有变更在 pipeline 内部，不产生新的 StreamEvent
- **不涉及新依赖**：纯 Python 标准库 + 已有项目依赖
- **测试**：需要新增/更新集成测试覆盖 personal bucket、多轮搜索、retarget、归一化去重、时间戳映射
