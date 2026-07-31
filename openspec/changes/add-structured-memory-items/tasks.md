## 1. 数据模型与基础设施

- [x] 1.1 `consolidation.py` — `Item` dataclass 新增 `summary: str`、`keywords: list[str]`、`content_scope: str` 三个字段（含默认值）
- [x] 1.2 `db/models.py` — `LongTermMemory` 模型新增 `summary`（Text, default ''）、`keywords`（ARRAY(Text), default []）、`content_scope`（Text, default ''）列
- [x] 1.3 创建数据库 migration 脚本（ALTER TABLE 新增 3 列 + 可选 content_scope 部分索引）
- [x] 1.4 `long_term.py` — `load_from_storage` 读取新字段填充到 `Item`；`_to_db_dict` / `_update_pg_item` 写入新字段

## 2. 提取层改造

- [x] 2.1 `memory_writer.py` — LTM 提取 prompt（`_LTM_EXTRACTION_SYSTEM_PROMPT`）输出格式增加 `summary` 和 `keywords` 字段，附 Summary Rules 和 Keywords Rules
- [x] 2.2 `memory_writer.py` — `extract_memory_from_reply` 解析 LLM 输出时提取 `summary` / `keywords`，传入 `store_classified`
- [x] 2.3 `long_term.py` — `store_classified()` 接收并写入 `summary` / `keywords` / `content_scope`；embedding 改为 `embed(summary)` 而非 `embed(content)`

## 3. 检索层改造

- [x] 3.1 `consolidation.py` — 新增 `keyword_score()` 函数（Jaccard 相似度，零依赖）
- [x] 3.2 `long_term.py` — `recall()` / `_recall_impl()` 改为双路打分：`semantic_sim * 0.5 + keyword_match * 0.2 + importance * 0.3`，score < 0.3 过滤
- [x] 3.3 `memory_rag.py` — `memory_recall_handler` 返回格式增加 `summary` 和 `keywords` 字段
- [x] 3.4 `memory_store.py` — 工具参数新增 `summary` / `keywords`（可选）；`category="case"` 时 `summary` 和 `keywords` 为必填

## 4. 任务经验沉淀（Case Memory）

- [x] 4.1 `memory_writer.py` — 新增 case 提取 prompt（从会话摘要中提取可复用经验）
- [x] 4.2 `memory_writer.py` — 新增 `extract_case_memories()` 函数，接收 session summary + task result，返回 case 记忆列表
- [x] 4.3 `memory_service.py` — 新增 `_safe_extract_case_memories()` 方法，在 Agent run 结束时触发（`_post_run_memory_hook`）
- [x] 4.4 `memory_service.py` — 新增 `case_extraction_enabled` 配置开关，默认启用
- [x] 4.5 `memory_store.py` — `_VALID_CATEGORIES` 白名单新增 `"case"`

## 5. Consolidation 增强

- [x] 5.1 `consolidation.py` — `ConsolidationConfig` 新增 case 专用参数：`case_ttl_days=90`、`case_decay_rate=0.998`、`case_min_importance=0.4`、`case_dedup_threshold=0.90`
- [x] 5.2 `long_term.py` — `consolidate()` 按 `category` 分组应用不同生命周期参数（case vs 默认）
- [x] 5.3 `long_term.py` — `_merge_pair()` 合并时同步 `summary`（优先非空）、`keywords`（去重并集，上限 8）、`content_scope`（优先非空）

## 6. 存量迁移

- [x] 6.1 编写 `migrate_existing_memories()` 函数：遍历无 summary 的 LTM 条目，调 LLM 生成 summary + keywords，重算 embedding，写回 PG
- [x] 6.2 迁移函数后台异步执行，不阻断服务启动；单条失败跳过继续
- [x] 6.3 在服务启动流程中注册迁移任务（幂等，已迁移的跳过）

## 7. 测试与验证

- [x] 7.1 单元测试：`keyword_score()` Jaccard 相似度计算（空集、完全匹配、部分匹配、无匹配）
- [x] 7.2 单元测试：双路打分公式（语义 + 关键词 + 重要度权重正确）
- [x] 7.3 单元测试：`store_classified` 写入 summary/keywords/content_scope 并基于 summary 计算 embedding
- [x] 7.4 单元测试：`_merge_pair` 合并新字段（keywords 去重并集、summary/content_scope 优先非空）
- [x] 7.5 单元测试：case 生命周期参数（TTL=90 天、decay=0.998、min_importance=0.4、dedup_threshold 分组应用）
- [x] 7.6 单元测试：case 提取函数（有经验时输出 case 记忆、无经验时返回空数组）
- [x] 7.7 单元测试：memory_store 工具 category="case" 时 summary/keywords 必填校验
- [x] 7.8 集成测试：存量迁移函数（成功迁移、LLM 失败时跳过不阻断）
