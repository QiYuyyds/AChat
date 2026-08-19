## 1. File Catalog 基础设施

- [x] 1.1 创建 `backend/app/memory/file_store/file_catalog.py` — SQLite 表 `memory_catalog(path TEXT PK, st_mtime REAL, bucket TEXT)`，存储在 `<agenthub-data>/memory/metadata/catalog.db`
- [x] 1.2 实现 `FileCatalog.reconcile()` — 全量扫描 `daily/` 和 `digest/` 目录，新增/删除/更新条目
- [x] 1.3 实现 `FileCatalog.get_changed()` — 返回 mtime 变化的文件列表，支持 bucket 过滤
- [x] 1.4 实现 `FileCatalog.upsert(path, st_mtime, bucket)` — auto_memory / auto_dream 写入后调用
- [x] 1.5 实现 `FileCatalog.remove(path)` — 文件删除时调用
- [x] 1.6 在 `MemoryService.__init__` 中初始化 FileCatalog 并调用 `reconcile()`
- [x] 1.7 编写单元测试 — `backend/tests/test_memory_pipeline_optimization.py`

## 2. Session JSONL 净化

- [x] 2.1 在 `memory/file_store/` 或 `memory/pipeline/` 中实现 `_sanitize_msg_for_save(msg)` — 剥离 tool_result block 和 base64 data block
- [x] 2.2 在 session jsonl 写入逻辑中增加 message ID 去重（已有消息不重复写入）
- [x] 2.3 编写单元测试 — 净化后的 message 不含 tool_result / base64，重复 message ID 被跳过

## 3. auto_memory Prompt 重写

- [x] 3.1 在 `memory/pipeline/auto_memory.py` 中定义 `SYSTEM_PROMPT` — 角色定义 + 记录什么/不记录什么 + frontmatter 规则（参考 ReMe `auto_memory.yaml`）
- [x] 3.2 定义 `CREATE_PROMPT_TEMPLATE` — 对话历史 + skip check + 生成 `{action, name, description, body, tags, importance}`
- [x] 3.3 定义 `UPDATE_PROMPT_TEMPLATE` — 对话历史 + 已有笔记 body + 合并规则（时间线追加 / 状态重写 / 语义去重）+ 生成完整合并后 body
- [x] 3.4 修改 `auto_memory()` 主逻辑 — 检查 daily 卡片是否存在 → 选择 create/update prompt → 调用 LLM → 解析结构化输出
- [x] 3.5 实现文件命名 — 从 LLM 输出的 `name` 字段 sanitize（kebab-case, max 50 chars）+ 短 hash 后缀
- [x] 3.6 更新 frontmatter 写入 — 包含 LLM 生成的 name, description, importance
- [x] 3.7 写入后调用 `FileCatalog.upsert()` 更新 catalog
- [x] 3.8 编写集成测试 — 新建路径和更新路径各覆盖一个场景

## 4. auto_dream extract 增强

- [x] 4.1 修改 `auto_dream.py` 的 `dream_extract()` — 从 `FileCatalog.get_changed(bucket='daily')` 获取变更文件列表
- [x] 4.2 重写 extract prompt — 增加跨文件合并指导（"merge evidence from multiple files when they teach the same abstraction"）
- [x] 4.3 增加 topic 候选结构化输出 — extract prompt 输出 `{units: [...], topic_candidates: [{title, reason, evidence, keywords, paths}]}`
- [x] 4.4 处理已删除文件 — catalog 检测到文件不存在时，清理 catalog 条目并跳过
- [x] 4.5 编写测试 — 变更检测只处理 changed 文件

## 5. node_search 专用搜索

- [x] 5.1 创建 `backend/app/memory/search/node_search.py`
- [x] 5.2 实现 `node_search(query, bucket=None, limit=10)` — digest-only 过滤 + 节点级聚合（同 path 多 hit 取最高分）
- [x] 5.3 返回结果包含 frontmatter name + description（内联，免 follow-up read）
- [x] 5.4 编写单元测试

## 6. auto_dream integrate 增强

- [x] 6.1 定义 `INTEGRATE_SYSTEM_PROMPT_PROCEDURE` — body shape = runbook（Trigger / Steps / Pre-conditions / Failure modes / derived_from）
- [x] 6.2 定义 `INTEGRATE_SYSTEM_PROMPT_WIKI` — body shape = encyclopedia（First line definition / Body properties / derived_from）
- [x] 6.3 两个 prompt 都包含：Workflow（recall → classify → action → weave）、Action 定义（CREATE/CORROBORATE/REFINE/CORRECT）、Wikilink graph 规则（predicate 词表 + provenance 要求）
- [x] 6.4 修改 `dream_integrate()` — 对每个 unit 先调 `node_search()` 召回 → 根据 bucket 选 prompt → LLM 返回 `{action, content, reason, wikilinks}` → 程序代写
- [x] 6.5 在 prompt 中要求 LLM 在 body 中加入 `derived_from:: [[daily/<path>]]`
- [x] 6.6 在 prompt 中要求 LLM 在 CREATE 和 UPDATE 时织入 related digest 节点的 wikilink
- [x] 6.7 程序在写入前验证 body 包含至少一条 `derived_from::` wikilink（不包含则 warning 不阻断）
- [x] 6.8 写入后调用 `FileCatalog.upsert()` 更新 catalog
- [x] 6.9 编写集成测试 — CREATE 和 REFINE 各覆盖一个场景

## 7. auto_dream topics LLM 化

- [x] 7.1 定义 `TOPICS_SYSTEM_PROMPT` — Topic Quality 指导（具体、反复出现、可行动 > 泛泛标签）
- [x] 7.2 修改 `dream_topics()` — 将候选 topics + same-day existing + recent 7-day topics 发给 LLM
- [x] 7.3 LLM 返回最终 topics 列表，保留 same-day existing，从 recent 7-day 去重
- [x] 7.4 实现 fallback — LLM 不可用时退回纯规则取前 N 个 fresh
- [x] 7.5 编写测试 — LLM 路径和 fallback 路径各覆盖

## 8. Wikilink Predicate 支持

- [x] 8.1 修改 `memory/file_store/wikilinks.py` 的解析器 — 支持 `predicate:: [[path]]` 语法（regex `^(\w+)::\s*\[\[([^\]]+)\]\]`）
- [x] 8.2 邻接表增加 `predicate TEXT` 列（`ALTER TABLE` 或重建）
- [x] 8.3 `register_wikilink()` 方法增加 predicate 参数
- [x] 8.4 `get_outlinks(path)` 和 `get_inlinks(path)` 支持 predicate 过滤参数
- [x] 8.5 兼容旧格式 — 无 predicate 的 `[[target]]` 存为 `predicate = NULL`
- [x] 8.6 编写单元测试 — predicate 解析、存储、查询

## 9. 断链检测 + Move/Retarget

- [x] 9.1 在 `auto_index.py` 的 `index_file()` 中增加断链检测 — wikilink target 不存在时记入 `broken_links` 标记
- [x] 9.2 在 `full_reindex()` 中清理所有 broken wikilink 条目
- [x] 9.3 在 `memory/file_store/markdown_io.py` 中实现 `move_file(src, dst, retarget=True)` — 移动文件 + 扫描全工作空间 `[[old]]` → `[[new]]` 重写
- [x] 9.4 move_file 中 predicate wikilink 也要 retarget（`derived_from:: [[old]]` → `derived_from:: [[new]]`）
- [x] 9.5 move_file 后更新 FileCatalog + BM25 索引
- [x] 9.6 在 `api/memory.py` 中新增 `POST /api/memory/move` endpoint（scope to user_id）
- [x] 9.7 编写测试 — 断链检测、move+retarget、API endpoint

## 10. HybridSearch 增强

- [x] 10.1 修改 `SearchResult` 数据类 — 增加 `scores: dict` 字段（`{"bm25": float, "wikilink": float, "rrf": float}`）
- [x] 10.2 修改 `SearchResult` — 增加 `expansion: dict` 字段（`{"outlinks": [...], "inlinks": [...]}`）
- [x] 10.3 在 `HybridSearch.search()` 中填充 scores 和 expansion
- [x] 10.4 outlinks/inlinks neighbor 附带 name + description（从 frontmatter 读取）
- [x] 10.5 编写测试 — score breakdown 正确性、expansion meta 完整性

## 11. Digest status 字段

- [x] 11.1 在 `memory/file_store/markdown_io.py` 的 frontmatter schema 中增加 `status` 字段（默认 `active`）
- [x] 11.2 修改 `HybridSearch` — archived 节点 BM25 分数乘以 0.5x
- [x] 11.3 修改 `dream_integrate` — 跳过 `status: archived` 的节点（不 REFINE/CORRECT）
- [x] 11.4 编写测试 — archived 节点降权、dream 跳过

## 12. 集成与回归

- [x] 12.1 在 `memory_service.py` 中集成所有新模块 — FileCatalog 初始化、node_search 注入、move API
- [x] 12.2 端到端测试 — 完整 pipeline 流程：对话结束 → auto_memory → auto_dream → search
- [x] 12.3 回归测试 — 确保已有记忆文件不被破坏（旧格式 `[[target]]` 仍可解析）
- [x] 12.4 `ruff check .` 通过
- [x] 12.5 `pytest` 通过
