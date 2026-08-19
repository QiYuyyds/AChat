# Design — rewrite-memory-file-native

## Context

AChat 当前记忆系统是 DB-native 架构的产物：

- **ShortTerm**: 纯内存 deque，写入后无人读取（`get_stm_context()` 无调用者）
- **LongTerm**: PG `long_term_memory` 表 + Milvus embedding，扁平 cosine 扫描
- **Preference**: PG `user_preferences` KV 表（**本次保留不动**）
- **GraphMemory**: Neo4j + PG `memory_nodes`/`memory_edges` 镜像表
- **Consolidation**: 单层去重 (cosine ≥ 0.8 → merge) / 衰减 (importance *= decay) / TTL 过期
- **SessionMemory**: 属上下文压缩系统（Tier 2/3 compaction 的三路复用），非记忆系统，本次不动

项目转向 local-first 桌面端，DB-native 架构与"记忆透明、可编辑、可版本化"的需求冲突。ReMe（`待融合项目/ReMe/`）已验证 File-native 范式 + auto_dream 精炼流水线的可行性。

关键约束：
- 单用户桌面端，记忆无需 user_id 隔离
- Agent-scoped 记忆需要保留（per-agent + 全局共享）
- RAG 系统独立不动
- Neo4j 完全移除（KGStore 走 RAG 保留）
- SessionMemory/压缩系统不动
- 不做数据迁移
- 内嵌 FastAPI 进程，不做独立服务

## Goals / Non-Goals

**Goals:**

- 记忆内容以 Markdown 文件 + frontmatter + wikilinks 存储，用户可直接查看/编辑/版本化
- 三级生命周期：session/（原始对话）→ daily/（每日卡片）→ digest/（精炼长期记忆）
- auto_memory pipeline：对话结束 → LLM 提取事实 → daily 卡片
- auto_dream pipeline：阈值触发 + 定时兜底 → daily 精炼到 digest/{procedure,wiki}
- proactive 机制：auto_dream 生成 interests.yaml → Agent 主动拉取
- 混合检索：SQLite FTS5 (BM25) + wikilink 关系扩展 + RRF 融合
- agent-scoped：frontmatter `agent_id` 字段 + digest/procedure/agents/<agent_id>/ 目录
- PromptAssembler 的 RecallSource 从 PG 查询改为文件检索（ProfileSource 保留不动，继续读 PG Preference）
- 记忆管理 UI：文件浏览器 + Markdown 编辑器

**Non-Goals:**

- 不做 embedding 检索（默认关闭，后续可按需启用）
- 不做独立记忆服务（内嵌 FastAPI 进程）
- 不做 MCP Server 接入（后续迭代）
- 不改 RAG 系统（独立不动）
- 不改上下文压缩系统（SessionMemory / compact_pipeline / context_compaction_service 不动）
- 不改 Conversation / Message 业务实体（PG 继续存）
- 不做数据迁移（绿地重写）
- 不做远程 PG 记忆同步（纯本地）
- 不做 resource/ 外部素材记忆（后续迭代，与 auto_resource 相关）
- 不改 Preference 系统（PG KV 表 + `manage_profile` 工具 + `ProfileSource` 注入 + 三层去重机制均保留不动）

## Decisions

### D1. 存储模型：纯文件系统，SQLite 仅做索引

**选择**：记忆内容 = 文件系统 Markdown 文件；索引 = SQLite FTS5 + wikilink 邻接表
**替代**：ReMe 原生的 numpy BM25 + pickle 持久化
**理由**：SQLite FTS5 是事务安全的、标准 SQL 可查的、无需 pickle 反序列化风险的；对 AChat 的 local-first 场景，SQLite 单文件索引是最轻量选择。文件内容保留可读性，索引只是加速层。

### D2. 记忆工作空间目录结构

**选择**：
```
<agenthub-data>/memory/
├── metadata/           # SQLite 索引文件 (bm25.db, wikilinks.db)
├── session/            # 原始对话 (与 PG Message 双写)
│   └── <conv_id>.jsonl
├── daily/              # 每日记忆卡片
│   ├── YYYY-MM-DD.md
│   └── YYYY-MM-DD/
│       ├── <session_event>.md
│       └── interests.yaml
└── digest/             # 长期精炼记忆
    ├── procedure/       #   任务经验
    │   ├── shared/      #     全局可复用经验
    │   └── agents/      #     Agent 专属经验
    │       └── <agent_id>/
    └── wiki/            #   知识节点 (全局)
                       #   ❌ 无 personal/ — 个人事实/偏好由 PG Preference 系统管理
```

**替代**：ReMe 原生的扁平 digest/ 无 agent 子目录
**理由**：AChat 需要区分 Agent 专属经验和全局经验。`procedure/agents/<agent_id>/` 天然隔离，frontmatter `agent_id` 字段做检索过滤。

### D3. frontmatter schema

**选择**：每条记忆文件的 frontmatter 字段：
```yaml
---
name: "Debug React Hooks"          # 简洁标题 (也是文件名 stem)
description: "How to debug..."      # 详细描述
agent_id: null                      # null=全局共享, 非 null=Agent 专属
tags: ["react", "debug"]           # 分类标签
importance: 0.8                     # 重要性 0-1
bucket: procedure                   # procedure / wiki
created_at: 2026-08-04             # 创建日期
updated_at: 2026-08-04             # 更新日期
source: "daily/2026-08-04/sess.md" # 来源 daily 卡片路径
---
```

**替代**：ReMe 的 frontmatter (name, description, status)
**理由**：AChat 需要 `agent_id`（Agent scope）、`importance`（检索权重）、`bucket`（两分类）。`source` 保留溯源链。`personal` 桶移除——个人事实/偏好由 PG Preference 系统管理（精确 KV 查找优于文件模糊检索）。

### D4. auto_memory：直接 LLM 调用而非 Agent loop

**选择**：对话结束后，用一次 LLM chat completion 调用提取事实，直接写 daily 卡片
**替代**：ReMe 的 Agent loop（起 Agent → 给工具 → Agent 自己调 daily_write）
**理由**：AChat 的 auto_memory 是后台 hook，不宜起完整 Agent loop（工具沙箱/权限/超时复杂度）。一次 LLM 调用 + 结构化输出（JSON）足够。prompt 模板参考 ReMe `auto_memory.yaml`。
**与 Preference 的分工**：auto_memory 的 prompt 模板需明确**跳过偏好类事实**（用户姓名/喜好/所在地等结构化属性交给 Preference 系统的规则提取 + LLM 精化），只提取任务/决策/经验类叙事性事实。两者在 `on_message_end` hook 中并行运行，互不干扰。

### D5. auto_dream：两步子流水线（extract + integrate）

**选择**：
```
auto_dream = dream_extract → dream_integrate → dream_topics
```
- **extract**: 扫描最近 N 天 daily 文件变更 → LLM 识别可复用抽象 → 输出 `{name, bucket, summary, paths}[]`（bucket 仅 procedure/wiki）
- **integrate**: 对每个 unit → 先 search 已有 digest 去重 → CREATE / CORROBORATE / REFINE / CORRECT → 写入 digest/{bucket}/
- **topics**: 从 extract 的 topic candidates 选 top-N，与近 7 天 interests.yaml 去重 → 写入 daily/<date>/interests.yaml

**替代**：ReMe 的三步（extract + integrate + topics + finish），finish 仅做 catalog 持久化
**理由**：AChat 不需要 ReMe 的 dream catalog（用 SQLite 索引替代），finish 步骤可省略。其余直接参考 ReMe 的 `dream/extract.py`、`dream/integrate.py`、`dream/topics.py` 实现。

### D6. auto_dream 触发：阈值 + 定时兜底

**选择**：daily 未精炼卡片数 ≥ N（默认 5）时触发；定时每天 23:00 兜底（与 ReMe 一致）
**替代**：纯定时 / 纯阈值
**理由**：阈值触发保证及时性（Agent 不用等到晚上才获得经验），定时兜底保证不遗漏。

### D7. 检索：SQLite FTS5 + wikilink 扩展 + RRF

**选择**：
- BM25：SQLite FTS5 全文索引，分词用 jieba（中文）+ simple（英文）
- wikilink 扩展：从命中文件的 wikilinks 出发，BFS 扩展 1 跳相关文件
- RRF 融合：k=60，BM25 weight=0.7，wikilink weight=0.3
- embedding：默认关闭，后续可启用（SQLite sqlite-vec 或本地模型）

**替代**：ReMe 的 numpy BM25 + pickle
**理由**：SQLite FTS5 事务安全、标准 SQL、无反序列化风险。wikilink 扩展提供关系召回，比纯 BM25 精度更高。embedding 默认关闭减少依赖（local-first 场景可能没有 GPU 跑本地模型）。

### D8. wikilink 图：纯 Python dict + SQLite 持久化

**选择**：内存 dict 邻接表 + SQLite% 邻接表持久化
**替代**：ReMe 的 jsonl.zst 持久化 / NetworkX / Neo4j
**理由**：SQLite 持久化与 BM25 索引共用一个 db 文件，运维简单。纯 dict 内存查询 O(1)。不需要 Neo4j 的 Cypher 表达力（已决策放弃 Neo4j）。

### D9. Session/ 与 Conversation 双写

**选择**：对话结束时，从 PG Message 表导出 jsonl 到 session/<conv_id>.jsonl
**替代**：session/<conv_id>.jsonl 做 source of truth，PG 只存元数据
**理由**：PG Message 是业务层 source of truth（build_history_for 直接查 PG），改 source of truth scope 太大。双写简单安全：PG 存业务，session/ 存记忆管道输入。

### D10. 接入方式：内嵌 FastAPI 进程

**选择**：MemoryService pipeline 直接在 FastAPI 进程中运行
**替代**：独立子进程 + MCP Server（ReMe 的方式）
**理由**：AChat 当前就是 FastAPI 单进程，内嵌最简单。外部 Agent (Claude Code) 的记忆接入留后续迭代。

### D11. ShortTerm 删除安全

**选择**：直接删除 `ShortTerm` 类及 `MemoryService.stm`
**理由**：`ShortTerm.get_stm_context()` 在整个后端无调用者。`stm.add()` 在 `on_message_end()` 中写入后无人读。session/ jsonl 取代其"记录最近对话"的职责。

### D12. Preference 保留不动

**选择**：`Preference` 类（`memory/preference.py`）、`UserPreference` PG 表/ORM、`ProfileSource` 注入、`manage_profile` 工具、三层去重机制（同义词归一 + 手动覆盖保护 + LLM 合并）均保留不动
**替代**：把偏好迁移到 `digest/personal/*.md` 文件
**理由**：Preference 是结构化键值对（`{姓名: 张三, 喜好: Python, 所在地: 北京}`），访问模式为精确查找（`prefs.get("姓名")`），消费方包括 `manage_profile` 工具的结构化字段访问和 `ProfileSource` 的分类打分注入。把 KV 数据塞进 Markdown 文件是用文件模拟 KV 表——结构不匹配，且会破坏 `manage_profile` 的精确字段访问（`prefs.get("姓名")` 变成"在 personal/ 目录下找 frontmatter.name == '姓名' 的文件"）。Preference 与 File-native 记忆互补而非重叠：前者管结构化属性，后者管叙事性经验。

## Risks / Trade-offs

- **[文件并发写入]** 多 Agent 同时写同一个 daily 文件可能冲突 → 加 asyncio.Lock per file path
- **[auto_dream LLM 成本]** 每次 auto_dream 需 2-3 次 LLM 调用（extract + integrate × N units）→ 限制 max_units=5，integrate 并行化
- **[BM25 索引一致性]** 文件写入和索引更新不同步 → auto_index 在写入后异步触发，容忍短延迟；启动时做一次全量 reindex
- **[文件系统无 ACID]** 删除 + 重写非原子 → 写新文件 + rename（POSIX 原子 rename）；Windows 上需特判（rename 不可覆盖已有文件，先删后 rename）
- **[记忆量增长]** 长期运行 digest/ 可能积累大量文件 → importance 衰减 + 手动归档 + TTL 清理（在 auto_dream 中实现）
- **[wikilink 断链]** 删除 digest 文件后指向它的 wikilink 失效 → auto_index 检测断链并清理

## Open Questions

- auto_memory 提取的 LLM 模型选择：用用户的 ModelProfile 还是固定一个轻量模型（如 deepseek-chat）？
- 前端记忆文件编辑器的选型：Monaco Editor（重但功能全）还是 CodeMirror 6（轻且 Markdown 支持好）？
- proactive 注入位置：system prompt 末尾 vs 单独的 `<proactive_topics>` XML 块？
- auto_dream 的 integrate 步骤中，CREATE/CORROBORATE/REFINE/CORRECT 四动作的去重阈值如何设定？需参考 ReMe `integrate.yaml` 中的 `node_search` 召回策略
