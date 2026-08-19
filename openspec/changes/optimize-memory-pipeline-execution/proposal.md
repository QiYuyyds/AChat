## Why

当前记忆系统的 pipeline 执行层存在三类质量缺陷：

1. **auto_memory 合并粗糙** — 用单次 LLM 调用 + 行级完全匹配去重，无法做语义级合并（时间线追加、状态重写）。session/ jsonl 不去重、不净化 tool_result，导致检索到的事实可能伪装成用户上下文。
2. **auto_dream integrate 缺少工具链保障** — LLM 只返回 JSON `{action, content}`，程序代写。wikilink 织入、provenance 溯源、per-bucket body shape 全靠 LLM 自觉，无工具链约束。extract 无变更检测，每次全量扫描。
3. **wikilink 图系统薄弱** — 仅 `[[target]]` 无 predicate；无断链检测；无 move+retarget。

ReMe 通过 Agent Loop + 精细 prompt 工程解决了这些问题。本次变更不切换到完整 Agent Loop（保持后台 hook 轻量），而是通过 **prompt 增强 + 程序辅助工具链 + 增量检测** 三条路径缩小质量差距。

## What Changes

### auto_memory 增强

- **BREAKING**: auto_memory prompt 重写 — 融入 ReMe `auto_memory.yaml` 的合并规则（时间线追加 / 状态重写 / 语义去重），LLM 输出从扁平 `facts[]` 改为结构化 `{name, description, body, tags, action}` 支持新建/更新两种路径
- **BREAKING**: auto_memory 文件命名从程序生成 `session_<conv_id>` 改为 LLM 生成语义 name，程序从 frontmatter name 重命名文件
- session/ jsonl 写入增加 message ID 去重 + tool_result / base64 净化（参考 ReMe `_sanitize_msg_for_save`）

### auto_dream extract 增强

- 新增 file catalog（SQLite 表，跟踪 path + st_mtime），dream_extract 只处理 changed 文件
- extract prompt 增加跨文件合并指导（"merge evidence from multiple files when it teaches the same abstraction"）
- extract prompt 增加 topic 候选结构化输出（title / reason / evidence / keywords / paths）

### auto_dream integrate 增强

- **BREAKING**: integrate 从「LLM 返回 JSON → 程序代写」改为「程序先 search → LLM 返回 action + content（含 wikilink 指令）→ 程序代写」，增加 per-bucket 专用 prompt（procedure=runbook body shape, wiki=encyclopedia body shape）
- integrate prompt 增加 provenance wikilink 要求（`derived_from:: [[daily/<path>]]`）
- integrate prompt 增加 wikilink 织入指令（CREATE 和 UPDATE 都要织入 related 节点）
- 新增 `node_search` 专用搜索（digest-only 过滤 + 节点级聚合 + 内联 frontmatter）

### auto_dream topics 增强

- **BREAKING**: topics 从纯规则取前 N 个改为 LLM 选择（有 Topic Quality 指导 + same-day 保留 + recent 去重）

### wikilink 图系统增强

- wikilink 解析器支持 predicate 语法（`derived_from:: [[path]]` / `relates_to:: [[path]]`）
- wikilink 邻接表增加 predicate 列
- auto_index 增加断链检测（target 文件不存在时清理邻接表条目）
- 新增 memory file move API（重命名 + 自动重写全工作空间 `[[old]]` → `[[new]]`）

### search 增强

- HybridSearch 返回 per-hit score breakdown（bm25 / wikilink / rrf）
- 搜索结果增加 link expansion meta（outlinks + inlinks neighbor name + description）

## Capabilities

### New Capabilities

- `memory-file-catalog`: 文件变更跟踪 catalog — SQLite 表跟踪 memory 文件 path + st_mtime，支持增量变更检测（changed / unchanged / deleted），dream_extract 和 auto_index 消费

### Modified Capabilities

- `memory-pipeline`: auto_memory prompt 重写（合并规则 + 语义命名 + session 净化）；auto_dream extract（变更检测 + 跨文件合并）；auto_dream integrate（per-bucket prompt + provenance wikilink + wikilink 织入）；auto_dream topics（LLM 选择）
- `memory-search`: 新增 node_search 专用搜索；HybridSearch 返回 score breakdown + link expansion meta
- `file-native-memory`: wikilink 支持 predicate 语法；frontmatter 增加 `status` 保留字段；新增 move + retarget 能力；auto_index 断链检测

## Impact

- **后端核心模块**: `memory/pipeline/auto_memory.py`（prompt 重写 + session 净化）、`memory/pipeline/auto_dream.py`（extract 变更检测 + integrate per-bucket prompt + topics LLM 化）、`memory/pipeline/auto_index.py`（断链检测）
- **后端搜索**: `memory/search/hybrid_search.py`（score breakdown + link expansion）、新增 `memory/search/node_search.py`（digest 专用搜索）
- **后端文件存储**: `memory/file_store/wikilinks.py`（predicate 解析）、`memory/file_store/markdown_io.py`（move + retarget）
- **后端新增**: `memory/file_store/file_catalog.py`（文件变更跟踪）
- **后端 API**: `api/memory.py`（新增 move endpoint）
- **后端配置**: `config.py` 新增 dream 相关参数（max_units / topic_count / topic_diversity_days）
- **依赖**: 无新增依赖（全部基于已有 SQLite + LLM 调用）
- **风险**: auto_memory prompt 重写可能影响已有 daily 卡片格式的向后兼容；integrate per-bucket prompt 增加 LLM 调用次数（每 bucket 一次 → 每 unit 一次，但 prompt 更精准可能减少迭代）
