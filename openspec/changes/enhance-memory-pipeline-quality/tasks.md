# Tasks: enhance-memory-pipeline-quality

## 1. P0 — Prompt 质量提升

- [x] 1.1 在 `_EXTRACT_SYSTEM_PROMPT` 中补充质量门控语句：加入 "Prefer fewer, richer units over exhaustive file summaries"、"This extraction step is the gate for not worth memorizing"、"Do not emit passing mentions, known-concept recaps, one-off timestamps, attendance facts"、"Merge evidence from multiple files when it teaches the same abstraction"
- [x] 1.2 在 `_EXTRACT_SYSTEM_PROMPT` 的 Bucket Classification 中加入 `personal` bucket 定义："personal: user/team/project-specific identity, preferences, conventions, constraints, avoidances"
- [x] 1.3 在 `_INTEGRATE_SYSTEM_PROMPT_PROCEDURE` 和 `_INTEGRATE_SYSTEM_PROMPT_WIKI` 的 Wikilink Rules 中补充 only-add 原则："UPDATE must be additive: never remove existing wikilinks or derived_from entries"、"Default to weaving more, not less"
- [x] 1.4 新增 `_INTEGRATE_SYSTEM_PROMPT_PERSONAL` 模板：正文形态为 rule-of-engagement（Rule/fact、Why、How to apply），参考 ReMe personal bucket prompt，包含 only-add 原则和 derived_from provenance 规则

## 2. P0 — Personal Bucket 路由

- [x] 2.1 在 `auto_dream._dream_integrate` 中，bucket 合法值列表加入 `"personal"`，bucket 路由加入 `personal` 分支选择 `_INTEGRATE_SYSTEM_PROMPT_PERSONAL`
- [x] 2.2 在 `auto_dream._dream_integrate` 中，当 bucket 不在 `("procedure", "personal", "wiki")` 时 fallback 到 `wiki`
- [x] 2.3 在 `auto_dream._dream_integrate` 的 CREATE 分支中，确保 `personal` bucket 文件写入 `digest/personal/` 子目录（`workspace.digest_path(bucket, name, agent_id)` 已支持按 bucket 路由）
- [x] 2.4 在 `auto_dream._dream_extract` 中，LLM 输出解析后 bucket 值校验加入 `personal`

## 3. P1 — 代码侧多轮搜索

- [x] 3.1 在 `auto_dream._dream_integrate` 中，将单轮 `node_search.search(query=name, bucket=bucket, limit=5)` 替换为两轮搜索：第一轮 `query=name, limit=10`，第二轮 `query=summary, limit=10`
- [x] 3.2 实现 `_dedupe_by_path(hits_1, hits_2)` 辅助函数：按 path 去重（保留较高 score），返回 top-5
- [x] 3.3 当 `node_search` 不可用时，保持现有 fallback 到 `HybridSearch.search(query=name, top_k=3, bucket=bucket)` 不变

## 4. P2 — Dream Extract 输入保护与输出验证

- [x] 4.1 在 `auto_dream._dream_extract` 的 file_catalog 变更扫描中，跳过 `.yaml` 后缀文件
- [x] 4.2 在 `auto_dream._dream_extract` 的 LLM 输出解析后，对每个 unit 的 `source_paths` 做验证：只保留 `changed_paths` 中存在的路径
- [x] 4.3 过滤后 `source_paths` 为空的 unit，fallback 到 `changed_paths[:3]`

## 5. P2 — Auto Memory 文件重命名 Retarget

- [x] 5.1 在 `auto_memory._update_card` 中，当 LLM 返回新 `name` 且 `safe_new_name != existing_name` 时，执行 retarget 流程：写入新文件 → 删除旧文件
- [x] 5.2 遍历 workspace 中所有 `.md` 文件，对包含旧路径的文件调用 `retarget_wikilinks(content, old_rel, new_rel)` 更新引用
- [x] 5.3 更新 wikilink expander 图：`remove_all_for(old_rel)` → 为新路径重建边 `add_edges_detailed(new_rel, ...)`
- [x] 5.4 更新 file catalog：`remove(old_path)` → `upsert(new_path, bucket="daily")`
- [x] 5.5 当 name 未变化时，不执行 retarget（no-op）

## 6. P2 — Topic 标题归一化去重

- [x] 6.1 在 `auto_dream.py` 中新增 `_normalize_topic_title(title: str) -> str`：转小写 → 移除 `[^\w\s]` → 压缩空格
- [x] 6.2 在 `_dream_topics` 中，`recent_titles` 集合存储归一化形式（`_normalize_topic_title(t)` 替代 `t.strip()`）
- [x] 6.3 在 `_dream_topics` 中，`existing_titles` 集合和候选去重比较均使用归一化形式

## 7. P3 — Session JSONL 时间戳归一化

- [x] 7.1 在 `auto_memory.py` 中新增 `_normalize_timestamp(msg: dict) -> dict`：当 `created_at` 缺失时，依次检查 `time_created` / `timestamp` / `createdAt` / `timeCreated` / `created_time` 别名，找到则写入 `created_at`
- [x] 7.2 在 `write_session_jsonl` 的消息遍历中，在 `_sanitize_msg_for_save` 之前调用 `_normalize_timestamp`

## 8. 测试

- [x] 8.1 新增测试：personal bucket CREATE 路径（LLM 输出 bucket=personal → 文件写入 digest/personal/）
- [x] 8.2 新增测试：personal bucket integrate 使用正确的 system prompt
- [x] 8.3 新增测试：两轮搜索去重（同一 path 在两轮中均出现，结果只保留一条且取较高 score）
- [x] 8.4 新增测试：dream_extract 跳过 .yaml 文件
- [x] 8.5 新增测试：source_paths 验证（LLM 输出幻觉路径被过滤 + 空 source_paths fallback）
- [x] 8.6 新增测试：文件重命名 retarget（旧 wikilink 更新为新路径）
- [x] 8.7 新增测试：Topic 归一化去重（"Memory Pipeline!" 与 "memory pipeline" 判定为重复）
- [x] 8.8 新增测试：时间戳别名归一化（timestamp → created_at 映射 + 已有 created_at 不覆盖）
- [x] 8.9 运行 `ruff check .` 和 `pytest` 确保全部通过
