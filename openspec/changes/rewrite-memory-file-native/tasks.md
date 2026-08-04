## 1. 清理旧记忆模块

- [x] 1.1 删除 `memory/short_term.py` 及其在 MemoryService 中的所有引用 (stm, stm.add(), stm.get())
- [x] 1.2 删除 `memory/long_term.py` 及其在 MemoryService 中的所有引用 (ltm, ltm.add(), ltm.recall())
- [x] 1.3 删除 `memory/graph_memory.py` 及其在 MemoryService 中的所有引用 (graph_memory)
- [x] 1.4 删除 `memory/consolidation.py` 及其在 MemoryService 中的所有引用
- [x] 1.5 删除 `memory/memory_writer.py` (被 auto_memory + auto_dream 取代)
- [x] 1.6 从 `db/models.py` 移除 `LongTermMemory`, `MemoryNode`, `MemoryEdge` ORM 类（**保留 `UserPreference`**）
- [x] 1.7 从 `infra/factory.py` 移除 Neo4j 构建逻辑；从 docker-compose 移除 Neo4j 服务
- [x] 1.8 清理所有 import 和测试中对已删模块的引用
- [x] ~~1.9 删除 `memory/preference.py`~~ — **取消：Preference 保留不动**
- [x] ~~从 `db/models.py` 移除 `UserPreference`~~ — **取消：保留**

## 2. 文件存储基础层

- [x] 2.1 实现 `memory/file_store/workspace.py` — 记忆工作空间目录管理 (创建/验证 session/, daily/, digest/ 目录结构)
- [x] 2.2 实现 `memory/file_store/markdown_io.py` — Markdown 文件读写 (frontmatter 解析/序列化 + body 读写)，依赖 `python-frontmatter`
- [x] 2.3 实现 `memory/file_store/frontmatter.py` — frontmatter schema 验证 (name, description, agent_id, tags, importance, bucket, created_at, updated_at, source)
- [x] 2.4 实现 `memory/file_store/wikilinks.py` — wikilink 解析 (从 Markdown body 中提取 `[[path]]` 链接) 和渲染

## 3. 索引层

- [x] 3.1 实现 `memory/search/bm25_index.py` — SQLite FTS5 全文索引 (建表、插入、查询、删除)；jieba 分词 (中文) + simple 分词 (英文)
- [x] 3.2 实现 `memory/search/wikilink_expander.py` — SQLite 邻接表存储 + 1-hop BFS 扩展
- [x] 3.3 实现 `memory/search/hybrid_search.py` — RRF 融合 (BM25 weight=0.7, wikilink weight=0.3, k=60)；支持 agent_id 过滤 + bucket 过滤

## 4. auto_index 步骤

- [x] 4.1 实现 `memory/pipeline/auto_index.py` — 文件变更监听 + 索引更新触发 (watch daily/ 和 digest/ 目录)
- [x] 4.2 实现启动时全量 reindex (扫描所有 daily/ 和 digest/ 文件 → 重建 BM25 + wikilink 索引)

## 5. auto_memory 步骤

- [x] 5.1 实现 `memory/pipeline/auto_memory.py` — 对话结束后 LLM 提取事实 → 写 daily 卡片
- [x] 5.2 实现 auto_memory prompt 模板 (参考 ReMe `auto_memory.yaml` 的 system_prompt 和 user_message_create)
- [x] 5.3 实现 session/ jsonl 双写 (从 PG Message 导出 → 写入 session/<conv_id>.jsonl)
- [x] 5.4 实现 skip check (LLM 判断对话是否值得记忆)

## 6. auto_dream 步骤

- [x] 6.1 实现 `memory/pipeline/auto_dream.py` — 流水线编排 (extract → integrate → topics)
- [x] 6.2 实现 dream_extract — 扫描 daily/ 变更 → LLM 提取可复用抽象 + 分类 (procedure/wiki)。参考 ReMe `dream/extract.py` + `extract.yaml`
- [x] 6.3 实现 dream_integrate — 逐 unit: search 去重 → CREATE/CORROBORATE/REFINE/CORRECT → 写 digest/{bucket}/。参考 ReMe `dream/integrate.py` + `integrate.yaml` (每个 bucket 独立 prompt)
- [x] 6.4 实现 dream_topics — 选 top-N 话题 + 近 7 天去重 → 写 interests.yaml。参考 ReMe `dream/topics.py`
- [x] 6.5 实现 auto_dream 触发逻辑: 阈值触发 (daily 卡片 ≥ 5) + 定时兜底 (cron 23:00)

## 7. proactive 步骤

- [x] 7.1 实现 `memory/pipeline/proactive.py` — 读 daily/<date>/interests.yaml → 返回结构化话题。参考 ReMe `dream/proactive.py`
- [x] 7.2 实现 proactive 注入: Agent 行动前调用 proactive → 相关话题注入 system prompt

## 8. MemoryService 重写

- [x] 8.1 重写 `memory/memory_service.py` — pipeline 编排门面 (初始化工作空间 + 绑定 pipeline 步骤 + on_message_end hook)；**保留 `self.preference` 初始化**
- [x] 8.2 实现 recall 接口: MemoryService.recall() → hybrid_search.search()
- [x] ~~8.3 重写 preference 接口~~ — **取消：`get_preference_context()` 保留原样，继续读 PG Preference**
- [x] 8.3 实现 graph_recall 接口: MemoryService.graph_recall() → wikilink_expander.expand()

## 9. 服务层集成

- [x] 9.1 修改 `services/prompt_assembler.py` — RecallSource 从 MemoryService.ltm.recall() 改为 MemoryService.recall()（**ProfileSource 保留不动，继续读 PG Preference**）
- [x] 9.2 修改 `services/agent_runner.py` — 记忆 hook 从 _post_run_memory_hook 改为 auto_memory 触发; session/ 双写 hook
- [x] 9.3 修改 `services/conversation_context.py` — build_history_for 中记忆注入逻辑适配新接口
- [x] 9.4 修改 `main.py` lifespan — 移除旧记忆系统初始化, 新增文件工作空间初始化 + auto_index 启动 + cron 注册

## 10. API 层

- [x] 10.1 重写 `api/memory.py` — 记忆文件管理 API (list/read/write/edit/delete/search)
- [x] 10.2 新增 proactive API endpoint — GET /api/memory/proactive → 返回当前话题
- [x] 10.3 新增 auto_dream trigger API — POST /api/memory/auto-dream → 手动触发精炼

## 11. Agent 工具层

- [x] 11.1 重写 `tools/memory_store.py` — memory_recall 工具改为调 hybrid_search
- [x] 11.2 重写 `tools/memory_rag.py` — 适配新搜索接口（**preference 部分 `get_preference_context()` 保留不动**）
- [x] ~~重写 `tools/manage_memory.py` preference 部分~~ — **取消：list/delete preference 保留原样**（long_term 部分已适配文件原生）
- [x] ~~重写 `tools/manage_profile.py`~~ — **取消：保留原样**
- [x] 11.3 新增 memory_write 工具 — Agent 主动写记忆 (写 digest/ 文件)
- [x] 11.4 新增 memory_proactive 工具 — Agent 主动拉取 proactive 话题

## 12. 前端

- [x] 12.1 重写 `lib/api/memory.ts` — 适配新文件管理 API（**preference API 部分保留不动**）
- [x] 12.2 重写 `components/settings/memory-management/` — 文件浏览器 UI (文件列表 + Markdown 编辑器)（**偏好管理 UI 保留不动**）
- [x] 12.3 新增 proactive 话题展示组件 (在记忆面板中展示)
- [x] 12.4 更新 `shared/types.ts` — 记忆类型定义改为文件模型（**preference 类型保留不动**）

## 13. 配置与文档

- [x] 13.1 新增记忆相关配置项到 `config.py` (workspace_dir, auto_dream 阈值/cron, BM25 参数, max_units 等)
- [x] 13.2 更新 `backend/.env.example` — 移除旧记忆配置, 新增记忆工作空间路径
- [x] 13.3 更新 `specs/08-db-schema.md` — 移除记忆相关表定义（**保留 `UserPreference` 表定义**）
- [x] 13.4 更新 `CLAUDE.md` §3.8 — RAG/记忆从"可选增强"改为"文件原生"
- [x] 13.5 新增 `python-frontmatter` 到 pyproject.toml 依赖
