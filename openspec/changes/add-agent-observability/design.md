## Context

项目当前调用链跨越多层：

- **A 类深链路**：一次 agent run 经历 `POST /api/messages` → `AgentRunner.execute_run` → `run_agent_loop`（solo/coordinated/subagent）→ `build_adapter_input`（含 PromptAssembler 上下文组装）→ `Adapter.stream`（LLM 调用 + 工具循环）→ 工具调用（`rag_search` / `fs_grep` / `task_dispatch` / ...）→ RAG 三路召回（Milvus / ES / KG）+ 查询改写 + RRF 融合 + 精排 → Memory 召回（STM / LTM）→ 子 Agent 派发（递归 `spawn_subagent_loop`）→ DB 持久化
- **B 类浅链路**：常规 API（`/api/conversations` / `/api/agents` / `/api/documents` 等 14 个路由模块）的扁平 HTTP 调用

现状：`logging.basicConfig` 按模块分散，无 trace_id 关联。RAG 召回为空时无法区分"是 ES 没召回还是 Milvus 没召回"。工具调用失败时无法快速定位是哪个工具、哪轮、什么参数。Orchestrator 多 Agent 协作时无法量化拆任务是否合理、子 Agent 质量如何。已有 `add-rag-evaluation`（CLI 脚本，7 指标，Markdown 报告）但无可视化、无持久化、不覆盖 Agent 全过程。

项目正从 local-first 向 SaaS 化转型，采集层需采用标准语义（W3C TraceContext / OpenTelemetry）以支持未来多实例、跨服务传播。

## Goals / Non-Goals

**Goals:**

- 一次 agent run 产生一棵 Level 4 深度 span 树，嵌套展开到 RAG 子步骤 / Memory 子步骤 / 提示词组装 / 每轮 LLM 生成 / 子 Agent 派发内部
- B 类浅链路零侵入覆盖（FastAPI / httpx / openai 自动 instrumentation）
- Custom Agent（OpenAI 兼容 SDK）的 LLM 调用自动 instrument（openinference-instrumentation-openai）
- span name 采用「英文标识 · 中文描述」格式，Phoenix UI 直接显示中文，测试人员可读
- span 业务属性（hits / empty / model / turn / finish_reason / tool_name / success / dispatch_depth 等）可记录并查询
- 采集层采用 OpenTelemetry 标准语义，通过 OTLP exporter 发送至 Phoenix，未来切 Jaeger/Tempo 仅修改 exporter endpoint
- Arize Phoenix 作为独立可观测性后端，Docker 部署，提供独立监控页面（:6006），内置 Trace 瀑布流 + Eval 评分可视化
- Phoenix 存储复用现有 PostgreSQL 实例，使用独立 database `achat_observability` 与业务库物理隔离
- 在线规则评测（默认开启）：每次 agent run 自动从 trace 数据计算规则指标，eval score 挂在 trace 上，Phoenix UI 一体化查看
- 离线 LLM-as-judge 评测（默认关闭，手动触发）：通过 API 对指定 trace 调用 DashScope LLM 深度评判
- Agent 全过程评测指标体系（5 维度）+ 多 Agent 协作评测指标体系（4 维度）
- `trace_enabled` 开关，关闭时全部 no-op

**Non-Goals:**

- 不考虑 CLI 子进程 Agent（Claude Code / Codex）的 trace 采集——CLI Agent 走子进程 stream-json，不由 OTel 自动 instrument，本轮不覆盖
- 不自研 trace 存储 / REST 查询接口 / 瀑布流前端——全部由 Phoenix 内置提供
- 不做 Prometheus / Grafana / Jaeger 服务端——本轮用 Phoenix，SaaS 期再评估切换
- 不做 Metrics 指标聚合（QPS / p99）——本轮只做 Traces + Evals
- 不做前端发起请求的 trace（只覆盖后端侧）
- 不做实时 SSE 推送 trace 数据（Phoenix UI 自带刷新）
- 不改 agent run / RAG / 工具的业务逻辑（仅包裹 span + eval hook）
- 不抓取 LLM 请求/响应 body（隐私 + 体积；只记 model / duration / token 数 / finish_reason）
- 不复用旧 `add-trace-observability` 提案（该提案设计自研全栈，已被本提案替代）

## Decisions

### D1: Arize Phoenix 作为可观测性后端

**选择**：Arize Phoenix（开源 Apache 2.0），Docker 自托管，作为独立监控页面

**理由**：
- OTel 原生：采集层用 OTel SDK，exporter 发 OTLP 给 Phoenix，未来切 Jaeger 仅改 endpoint，采集代码零改动
- Trace + Eval 一体化：Phoenix 内置 Trace 瀑布流 UI + Eval 评分展示 + 对比分析，无需自研前端
- Python 原生：`arize-phoenix` + `arize-phoenix-evals` Python SDK，与项目 FastAPI 后端同语言
- `openinference-instrumentation-openai` 自动 instrument Custom Agent 的 OpenAI SDK 调用
- 部署最轻：单容器（`phoenix serve`），可复用已有 PostgreSQL
- 支持自定义 evaluator：在线规则评测和离线 LLM-as-judge 结果可通过 Phoenix API 写入，与 trace 关联展示

**备选**：Langfuse（自有 SDK 非 OTel 标准，部署 4 容器较重）；自研全栈（旧 `add-trace-observability` 提案路线，需自研 exporter + REST + 瀑布流前端，工作量大且独立页面需求下自研前端无优势）；LangSmith（云服务，数据不出本地）。

### D2: OTel SDK 采集 + OTLP Exporter

**选择**：OpenTelemetry SDK 标准 `BatchSpanProcessor` + `OTLPSpanExporter`（gRPC → Phoenix :4317）

**理由**：
- 标准协议，SaaS 上云后切 Jaeger/Tempo 仅改 exporter endpoint URL
- `BatchSpanProcessor` 异步批量发送，不阻塞主链路
- Phoenix 不可用时 SDK 缓冲后丢弃，天然降级，不报错
- 自动 instrumentation（FastAPI / httpx / openai）零侵入覆盖 B 类浅链路和 LLM 外调

**备选**：自研 PG SpanExporter（旧提案路线，省一个 Phoenix 容器但需自建存储+查询+UI）；Langfuse SDK（非标准，绑死生态）。

### D3: Level 4 深度手动埋点

**选择**：对 agent run 全链路手动埋点，深度覆盖到 RAG 子步骤 / Memory 子步骤 / 提示词组装 / 每轮 LLM 生成 / 子 Agent 派发嵌套

完整埋点清单：

| 层级 | 埋点位置 | span name (英文 key) | 中文描述 | 关键属性 |
|------|---------|---------------------|---------|---------|
| L1 | `execute_run` | `agent.run` | 代理运行 | `agent_id`, `run_id`, `conversation_id`, `dispatch_mode` |
| L2 | `build_adapter_input` | `agent.build_context` | 上下文组装 | `history_msg_count`, `rag_enabled`, `memory_enabled` |
| L3 | `PromptAssembler.assemble` | `prompt.assemble` | 提示词组装 | `schema_mode`, `final_token_count`, `system_prompt_hash`, `rag_chunks_injected`, `memory_items_injected` |
| L3 | `memory.recall` | `memory.recall` | 记忆召回 | `source`(stm/ltm/graph) |
| L4 | `memory.ltm.query` | `memory.ltm.query` | 长期记忆查询 | `top_k`, `min_score`, `hits` |
| L4 | `memory.stm.get` | `memory.stm.get` | 短期记忆读取 | `window_size` |
| L3 | `RAGService.search` | `rag.search` | 知识检索 | `query`(截断100字), `mode`, `rewrite_enabled` |
| L4 | `rag.query_rewrite` | `rag.query_rewrite` | 查询改写 | `original`(截断), `rewritten`(截断) |
| L4 | `milvus_search` 回调 | `rag.milvus_search` | 向量检索 | `hits`, `top_k`, `empty`(bool), `scores` |
| L4 | `es_search` 回调 | `rag.es_search` | 全文检索 | `hits`, `top_k`, `empty`(bool) |
| L4 | `kg_search` 回调 | `rag.kg_search` | 图谱检索 | `hits`, `skipped`(bool) |
| L4 | `rag.rrf_fuse` | `rag.rrf_fuse` | 结果融合 | `final_count`, `fusion_method` |
| L2 | `Adapter.stream` | `adapter.stream` | 模型推理 | `adapter_name`, `model_id` |
| L3 | LLM 调用（每轮） | `llm.generate` | LLM生成 | `turn`, `input_tokens`, `output_tokens`, `finish_reason`, `cache_read_tokens` |
| L3 | 工具执行入口 | `tool.call` | 工具调用 | `tool_name`, `success`(bool), `args_summary`(截断) |
| L3 | `spawn_subagent_loop` | `tool.dispatch` | 任务派发 | `task_id`, `child_agent_id`, `dispatch_depth`, `dispatch_visibility` |
| L4 | 子 agent `execute_run` | `agent.run` | 代理运行(子Agent) | `parent_run_id`, `dispatch_depth` |
| L2 | `execute_run` 收尾 | `agent.finalize` | 运行收尾 | `total_turns`, `total_tokens`, `duration_ms` |
| eval | 在线规则评测 | `eval.score` | 评测打分 | `eval_type`(rule_based), `eval_mode`(online) |
| eval | LLM-as-judge | `eval.judge` | LLM评判 | `eval_type`(llm_judge), `eval_mode`(offline) |

**属性 key 约定**：业务自定义属性加 `agenthub.` 前缀（`agenthub.hits` / `agenthub.empty` / `agenthub.model` 等），标准属性对齐 OTel semantic conventions（`http.method` / `http.url` / `db.system`）。属性 key 常量集中在 `instrumentation.py` 定义。

**理由**：
- Level 3（旧提案定义的 agent.run / adapter.stream / rag.search / milvus_search）只到「检索引擎调用」粒度，无法支撑全过程评测
- Level 4 增加：memory 细分到 ltm/stm、RAG 细分到 query_rewrite/rrf_fuse、prompt.assemble（带模板 hash + 注入计数）、llm.generate 每轮一个 span（带 turn + finish_reason）、tool.dispatch 子 agent 嵌套（带 depth + parent_run_id）
- `pymilvus` / `neo4j` / `elasticsearch[async]` 无官方 OTel instrumentation，必须手动埋
- `prompt.assemble` span 对「提示词效果评测」至关重要——记录 system_prompt_hash 可自动对比不同 prompt 版本的表现
- `llm.generate` 每轮一个 span 对「步骤效率评测」必须——知道几轮完成、每轮 token 消耗、finish_reason 分布

**备选**：只做 Level 1-3（无法看到 RAG 子步骤和提示词组装内部，全过程评测无数据支撑）。

### D4: Span name 中英文双语格式

**选择**：span name 格式为 `{英文标识} · {中文描述}`，映射表集中定义在 `span_names.py`

**格式**：`agent.run · 代理运行`、`rag.milvus_search · 向量检索`、`llm.generate · LLM生成`、`eval.score · 评测打分`

**实现**：
- `span_names.py` 定义 `SPAN_NAMES: dict[str, str]` 映射表，key 是英文标识，value 是「英文 · 中文」
- `@traced(span_key)` 装饰器内部查映射表获取最终 span name
- 工程师埋点只写英文 key（`@traced("agent.run")`），不接触中文
- 动态后缀支持：`@traced("llm.generate", suffix="第1轮")` → `llm.generate · LLM生成 (第1轮)`
- Phoenix UI 树视图直接显示「代理运行 · 模型推理 · 向量检索」，测试人员可读
- Phoenix 搜索框搜 `agent.run` 或 `代理运行` 都能命中
- 未来切 Jaeger 照样显示（span name 是纯字符串）

**备选**：纯中文 span name（代码查询不方便）；英文 span name + display_name 属性（Phoenix 树视图不显示属性，测试人员仍看不懂）。

### D5: Phoenix 存储复用 PG 独立 database

**选择**：在现有 PostgreSQL 实例中新建独立 database `achat_observability`，Phoenix 专用

**理由**：
- 业务库 `agenthub` 与 Phoenix 数据物理隔离，保留/清理策略互不影响
- 不新增 PG 容器，复用已有 `postgres:16-alpine` 实例
- Phoenix 通过 `PHOENIX_SQL_DATABASE_URL=postgresql://agenthub:agenthub@postgres:5432/achat_observability` 连接
- Phoenix 自动在该 database 下建表（traces / spans / eval_scores / datasets 等），无需手动迁移

**备选**：复用业务库同 schema（清理策略耦合，Phoenix 表与业务表混在一起）；Phoenix 内置 SQLite（不适合大量数据，重启丢数据除非挂卷）；新增独立 PG 容器（多一个容器，资源浪费）。

**初始化**：PG 容器启动时通过 `POSTGRES_MULTIPLE_DATABASES` 或 init script 创建 `achat_observability` database。或 Phoenix 首次启动时自动创建（需 PG 用户有 CREATE DATABASE 权限）。

### D6: `trace_enabled` 开关 + 降级策略

**选择**：`backend/app/config.py` 新增 `trace_enabled: bool = True`，为 False 时 `init_observability` 跳过 OTel 初始化，所有 `@traced` 装饰器变为 no-op

**降级链**：
- `trace_enabled=False` → 全部 no-op，零开销
- `trace_enabled=True` 但 Phoenix 不可达 → OTel SDK `BatchSpanProcessor` 缓冲 span，超时后静默丢弃，不报错，不阻断主链路
- `trace_enabled=True` 且 Phoenix 正常 → span 通过 OTLP gRPC 批量发送

**实现**：`@traced` 装饰器在 `trace_enabled=False` 时直接返回原函数调用，不创建 span。OTel SDK 本身的 `BatchSpanProcessor` 已内置重试和超时丢弃机制。

### D7: 在线规则评测（默认开）+ 离线 LLM-as-judge（默认关，手动触发）

**选择**：双层评测架构

**在线规则评测**（`eval_rules.py`，默认 `eval_rule_enabled=True`）：
- agent run 结束后自动执行，不调 LLM，纯从 trace span 数据计算
- 结果通过 Phoenix API 写入，作为 eval annotation 挂在 trace 上
- 计算的指标：

| 指标 | 计算方式 | 数据来源 |
|------|---------|---------|
| Task Completion | `finish_reason == "end_turn"` ? 1.0 : 0.0 | `llm.generate` 最后一轮 span |
| Max Turns Exceeded | `total_turns >= MAX_TURNS` ? 0.0 : 1.0 | `agent.finalize` span |
| Tool Success Rate | `成功工具调用数 / 总工具调用数` | `tool.call` spans |
| Redundant Tool Calls | `重复(工具名+参数)调用数 / 总调用数` | `tool.call` spans |
| Turns to Complete | `total_turns` | `agent.finalize` span |
| Token Usage | `sum(input_tokens) + sum(output_tokens)` | `llm.generate` spans |
| Latency per Turn | `duration_ms / total_turns` | `agent.run` + `agent.finalize` spans |
| Tool vs LLM Time Ratio | `sum(tool duration) / sum(llm duration)` | `tool.call` + `llm.generate` spans |
| Dispatch Depth | `max(dispatch_depth)` | `tool.dispatch` spans |
| Parallelism Degree | `max(并发子 agent run 数)` | `agent.run` 子 spans |
| Subagent Count | `count(tool.dispatch spans)` | `tool.dispatch` spans |
| Subagent Task Completion | `子 agent end_turn 比例` | 子 `agent.run` spans |
| Error Detection | `有 error span ? 0.0 : 1.0` | 所有 spans |

**离线 LLM-as-judge 评测**（`eval_judge.py`，默认 `eval_judge_enabled=False`）：
- 手动触发：`POST /api/eval/judge/{trace_id}` 从 Phoenix 拉取指定 trace 的 span 数据 + LLM 输入输出摘要
- 调用 DashScope LLM（复用已有 `LLM_API_KEY` + `LLM_MODEL` 配置）进行深度评判
- 结果写回 Phoenix，作为 eval annotation 挂在同一 trace 上
- 评判的指标：

| 指标 | LLM 判定内容 |
|------|-------------|
| Tool Selection Accuracy | 模型选的工具对不对（给定任务 + 工具列表 + 实际选择） |
| Subtask Granularity | Orchestrator 拆的子任务粒度是否合理 |
| Subtask Overlap | 子任务是否有重叠 |
| Subtask Coverage | 子任务是否覆盖了用户需求 |
| Aggregation Fidelity | 最终回答是否整合了子 Agent 产出 |
| Information Loss | 子 Agent 产出在聚合中丢失比例 |
| Faithfulness | 回答是否忠于检索内容（复用已有 RAG eval prompt） |
| Answer Relevance | 回答是否切题（复用已有 RAG eval prompt） |
| Answer Quality | 综合质量（复用已有 RAG eval prompt） |

**trace + eval 一体化**：Phoenix 原生支持 span / trace 级别的 eval annotation。在线规则评测结果在 agent run 结束后立即写入；LLM-as-judge 结果在手动触发后写入。Phoenix UI 中同一 trace 的瀑布流下方直接展示所有 eval scores。

### D8: 评测指标体系——Agent 全过程 + 多 Agent 协作

**选择**：两套完整指标体系，覆盖 5 + 4 = 9 个维度

**Agent 全过程评测（5 维度）**：

| 维度 | 指标 | 评测方式 |
|------|------|---------|
| 任务完成 | Task Completion Rate | 在线规则 |
| | Output Artifact Exists | 在线规则 |
| | Max Turns Exceeded | 在线规则 |
| 工具调用质量 | Tool Success Rate | 在线规则 |
| | Tool Selection Accuracy | LLM-as-judge |
| | Tool Call Efficiency | 在线规则 |
| | Redundant Tool Calls | 在线规则 |
| 步骤效率 | Turns to Complete | 在线规则 |
| | Token Efficiency | 在线规则 |
| | Latency per Turn | 在线规则 |
| | Tool vs LLM Time Ratio | 在线规则 |
| 提示词效果 | RAG Injection Impact | 离线对比（有/无 RAG 的 trace 对比） |
| | Memory Injection Impact | 离线对比（有/无 Memory 的 trace 对比） |
| | Prompt Version A/B | 离线对比（不同 `system_prompt_hash` 的 trace 对比） |
| 回答质量 | Faithfulness | LLM-as-judge |
| | Answer Relevance | LLM-as-judge |
| | Answer Quality | LLM-as-judge |

**多 Agent 协作评测（4 维度）**：

| 维度 | 指标 | 评测方式 |
|------|------|---------|
| 任务拆解质量 | Subtask Count | 在线规则 |
| | Subtask Granularity | LLM-as-judge |
| | Subtask Overlap | LLM-as-judge |
| | Subtask Coverage | LLM-as-judge |
| 调度效率 | Dispatch Depth | 在线规则 |
| | Parallelism Degree | 在线规则 |
| | Sequential Bottleneck | 在线规则 |
| | Total Agent Runs | 在线规则 |
| 子 Agent 质量 | Subagent Task Completion | 在线规则 |
| | Subagent Output Relevance | LLM-as-judge |
| | Subagent Token Waste | 在线规则 |
| 聚合质量 | Aggregation Fidelity | LLM-as-judge |
| | Information Loss | LLM-as-judge |
| | Redundancy in Final Output | LLM-as-judge |

### D9: Phoenix Docker 部署

**选择**：Docker Compose 新增 `phoenix` 服务

```yaml
phoenix:
  image: arizephoenix/phoenix:latest
  ports:
    - "6006:6006"   # Web UI
    - "4317:4317"   # OTLP gRPC
  environment:
    PHOENIX_SQL_DATABASE_URL: postgresql://agenthub:agenthub@postgres:5432/achat_observability
    PHOENIX_HOST: 0.0.0.0
  depends_on:
    postgres: { condition: service_healthy }
  restart: unless-stopped
```

- Phoenix Web UI 访问：`http://localhost:6006`
- OTLP gRPC endpoint：`http://localhost:4317`（后端 OTel exporter 指向此地址）
- 独立后台页面，不嵌入 Next.js 前端

### D10: 已有 RAG eval 对接

**选择**：已有 `eval/run_eval.py` 的 RAG 评测结果通过 Phoenix Python SDK 写入 Phoenix，作为 dataset + eval scores

**理由**：
- 已有 RAG eval（7 指标 + 3 模式消融）产出 Markdown 报告，无持久化无可视化
- 评测结果可通过 `phoenix.Client.log_evaluations()` 写入 Phoenix
- Phoenix UI 的 Datasets / Evaluations 页面可展示历史评测趋势和模式对比
- 不修改 `run_eval.py` 的核心逻辑，仅在输出阶段增加 Phoenix 写入

## Risks / Trade-offs

- **[OTel 依赖体积]** 5 个 opentelemetry-* + openinference-* 包增加后端依赖体积 → 缓解：相比 Jaeger 全家桶已极小，且 SaaS 转型必经
- **[Level 4 埋点维护成本]** 深度埋点清单覆盖 18 个位置，新增工具/召回路径可能遗漏 → 缓解：`instrumentation.py` 提供统一 `@traced` 装饰器，新埋点一行装饰即可接入；`span_names.py` 映射表集中管理
- **[PG 写入压力]** 每次 agent run 产生 20-50 个 span 全量发 OTLP → 缓解：`BatchSpanProcessor` 异步批量发送，不阻塞主链路；SaaS 期可改采样率
- **[span 属性 schema 漂移]** JSONB 无强 schema，不同版本属性名可能不一致 → 缓解：`instrumentation.py` 集中定义属性 key 常量，埋点统一引用
- **[Phoenix 版本兼容]** Phoenix 快速迭代，API 可能变化 → 缓解：锁定 Phoenix 版本（`arizephoenix/phoenix:latest` → 固定 tag）；eval API 走 Phoenix Python SDK 而非裸 HTTP
- **[在线评测性能]** 每次 agent run 结束后跑规则评测 → 缓解：规则评测纯内存计算（从 span 数据聚合），不调 LLM，耗时 < 10ms；通过 `asyncio.create_task` 异步执行不阻塞响应
- **[LLM-as-judge 成本]** 手动触发但仍需额外 LLM 调用 → 缓解：默认关闭，仅按需触发；复用已有 DashScope 配置不引入新 API key
- **[PG 多 database 权限]** Phoenix 需要 CREATE DATABASE 权限或预建 database → 缓解：PG init script 预建 `achat_observability` database；或给 Phoenix 专用 PG 用户
- **[旧 `add-trace-observability` 提案废弃]** 旧提案从未实现，无迁移成本 → 新提案直接替代，旧提案目录可删除或标记 archived
