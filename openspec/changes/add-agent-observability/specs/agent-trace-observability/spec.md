## ADDED Requirements

### Requirement: OpenTelemetry 采集层初始化

系统 SHALL 在后端启动时初始化 OpenTelemetry TracerProvider，通过 OTLP gRPC exporter 发送 span 至 Phoenix，并提供统一的 tracer 获取与埋点装饰器入口。

- `backend/app/observability/tracer.py` SHALL 提供 `init_observability(settings)`，初始化 `TracerProvider`、注册 `BatchSpanProcessor` + `OTLPSpanExporter`（endpoint 指向 Phoenix :4317）
- `get_tracer(name)` SHALL 返回标准 OTel `Tracer` 实例
- 采集开关 `trace_enabled`（默认 True）为 False 时，`init_observability` SHALL 跳过初始化且不产生任何 span
- 关闭阶段 SHALL 调用 `provider.shutdown()` 刷新缓冲 span
- `PHOENIX_ENDPOINT` 配置项 SHALL 指定 OTLP gRPC endpoint（默认 `http://localhost:4317`）

#### Scenario: 正常初始化

- **WHEN** 后端启动且 `trace_enabled=True`
- **THEN** TracerProvider 被创建并注册 OTLPSpanExporter
- **AND** 后续所有自动/手动埋点产生的 span 通过 OTLP gRPC 批量发送至 Phoenix
- **AND** Phoenix UI（:6006）可查看 trace 瀑布流

#### Scenario: 采集关闭

- **WHEN** `trace_enabled=False`
- **THEN** `init_observability` 不初始化 provider
- **AND** 所有 `@traced` 装饰器变为 no-op，不产生 span，不影响主链路

#### Scenario: Phoenix 不可达时降级

- **WHEN** `trace_enabled=True` 但 Phoenix 服务不可达
- **THEN** OTel SDK `BatchSpanProcessor` 缓冲 span 后静默丢弃
- **AND** 不抛异常，不阻断 agent run 主链路

### Requirement: 自动 Instrumentation

系统 SHALL 通过 OTel 自动 instrumentation 零侵入覆盖 HTTP 请求与 LLM API 外调。

- `opentelemetry-instrumentation-fastapi` SHALL 自动包裹所有 FastAPI 路由请求，产生 HTTP 根 span
- `opentelemetry-instrumentation-httpx` SHALL 自动包裹所有 httpx 客户端调用，记录 url / method / duration
- `openinference-instrumentation-openai` SHALL 自动包裹 Custom Agent 的 OpenAI SDK 调用，记录 model / input_tokens / output_tokens / finish_reason
- 自动 instrumentation SHALL 不需要修改任何路由或服务代码

#### Scenario: 常规 API 自动记录

- **WHEN** 调用 `GET /api/conversations`
- **THEN** Phoenix 中 SHALL 出现一条 HTTP 根 span，记录路由、状态码、耗时

#### Scenario: Custom Agent LLM 调用自动记录

- **WHEN** Custom Agent 通过 OpenAI SDK 调用 LLM API
- **THEN** SHALL 自动产生 LLM 调用 span，记录 model / input_tokens / output_tokens / finish_reason
- **AND** 该 span 嵌套在 `adapter.stream` span 之下

### Requirement: Level 4 深度手动埋点

系统 SHALL 对一次 agent run 的关键调用点手动埋点，形成嵌套 span 树，覆盖 API → AgentRunner → 上下文组装 → 提示词组装 → Adapter → 每轮 LLM → 工具 → RAG 子步骤 → Memory 子步骤 → 子 Agent 派发全链路。

- `AgentRunner.execute_run` SHALL 产生 `agent.run` 根 span，属性含 `agent_id`、`run_id`、`conversation_id`、`dispatch_mode`
- `build_adapter_input` SHALL 产生 `agent.build_context` 子 span，属性含 `history_msg_count`、`rag_enabled`、`memory_enabled`
- `PromptAssembler.assemble` SHALL 产生 `prompt.assemble` 子 span，属性含 `schema_mode`、`final_token_count`、`system_prompt_hash`、`rag_chunks_injected`、`memory_items_injected`
- `MemoryService` 召回 SHALL 产生 `memory.recall` span，属性含 `source`（stm/ltm/graph）
- LTM 查询 SHALL 产生 `memory.ltm.query` 子 span，属性含 `top_k`、`min_score`、`hits`
- STM 读取 SHALL 产生 `memory.stm.get` 子 span，属性含 `window_size`
- `RAGService.search` SHALL 产生 `rag.search` span，属性含 `query`（截断 100 字）、`mode`、`rewrite_enabled`
- 查询改写 SHALL 产生 `rag.query_rewrite` 子 span，属性含 `original`（截断）、`rewritten`（截断）
- `milvus_search` / `es_search` / `kg_search` 回调 SHALL 各产生子 span，属性含 `hits`(int)、`empty`(bool) 或 `skipped`(bool)
- RRF 融合 SHALL 产生 `rag.rrf_fuse` 子 span，属性含 `final_count`、`fusion_method`
- `Adapter.stream` SHALL 产生 `adapter.stream` 子 span，属性含 `adapter_name`、`model_id`
- 每轮 LLM 生成 SHALL 各产生 `llm.generate` 子 span，属性含 `turn`、`input_tokens`、`output_tokens`、`finish_reason`、`cache_read_tokens`
- 工具执行入口 SHALL 产生 `tool.call` span，属性含 `tool_name`、`success`(bool)、`args_summary`（截断）
- `spawn_subagent_loop` SHALL 产生 `tool.dispatch` span，属性含 `task_id`、`child_agent_id`、`dispatch_depth`、`dispatch_visibility`
- 子 agent `execute_run` SHALL 产生嵌套 `agent.run` span，属性含 `parent_run_id`、`dispatch_depth`
- `execute_run` 收尾 SHALL 产生 `agent.finalize` span，属性含 `total_turns`、`total_tokens`、`duration_ms`
- 所有 span SHALL 通过 OTel parent-child 上下文自动嵌套，无需手动传递 trace_id

#### Scenario: agent run span 树完整嵌套

- **WHEN** 触发一次含 RAG 工具调用的 solo agent run
- **THEN** 产生的 span 树 SHALL 包含完整嵌套链路：`agent.run > agent.build_context > prompt.assemble + memory.recall > memory.ltm.query + memory.stm.get` 与 `agent.run > adapter.stream > llm.generate(turn=1) > tool.call > rag.search > rag.query_rewrite + rag.milvus_search + rag.es_search + rag.rrf_fuse > llm.generate(turn=2) > agent.finalize`
- **AND** 所有子 span 的 parent 关系正确

#### Scenario: 多 Agent 协作 span 树

- **WHEN** 触发一次 coordinated 模式 agent run，Orchestrator 派发 2 个子 Agent
- **THEN** span 树 SHALL 包含 `agent.run > tool.dispatch > agent.run(子Agent A)` 与 `agent.run(子Agent B)` 并发子树
- **AND** 子 `agent.run` span 的 `dispatch_depth` 属性 SHALL 为 1

#### Scenario: RAG 空召回标记

- **WHEN** 一次 RAG 查询中 ES 返回 0 条结果
- **THEN** `rag.es_search` span 的属性 SHALL 包含 `agenthub.empty=true` 与 `agenthub.hits=0`

#### Scenario: 提示词版本追踪

- **WHEN** 两次 agent run 使用不同的 system prompt
- **THEN** `prompt.assemble` span 的 `system_prompt_hash` 属性 SHALL 不同
- **AND** Phoenix UI 可按 `system_prompt_hash` 筛选对比不同 prompt 版本的 trace 表现

### Requirement: Span name 中英文双语格式

系统 SHALL 采用「英文标识 · 中文描述」格式的 span name，映射表集中定义，确保 Phoenix UI 可读性。

- `backend/app/observability/span_names.py` SHALL 定义 `SPAN_NAMES: dict[str, str]` 映射表
- 映射表 key 为英文标识（如 `agent.run`），value 为「英文 · 中文」（如 `agent.run · 代理运行`）
- `@traced(span_key)` 装饰器 SHALL 内部查映射表获取最终 span name
- 装饰器 SHALL 支持动态后缀：`@traced("llm.generate", suffix="第1轮")` → span name 为 `llm.generate · LLM生成 (第1轮)`
- 未在映射表中注册的 key SHALL 直接使用 key 本身作为 span name（降级，不报错）
- 新增 span type 时 SHALL 在映射表中添加对应条目

#### Scenario: 测试人员可读

- **WHEN** 测试人员在 Phoenix UI 查看一个 agent run trace
- **THEN** span 树 SHALL 显示中文描述（如「代理运行」「模型推理」「向量检索」）
- **AND** 搜索框搜英文 key 或中文描述均可命中

#### Scenario: 工程师只写英文 key

- **WHEN** 工程师在代码中添加埋点 `@traced("rag.milvus_search")`
- **THEN** 实际 span name SHALL 为 `rag.milvus_search · 向量检索`
- **AND** 工程师不需要在代码中写中文字符串

### Requirement: Phoenix 部署与 PG 独立 database

系统 SHALL 通过 Docker Compose 部署 Phoenix 服务，存储复用现有 PostgreSQL 实例的独立 database。

- `docker-compose.yml` SHALL 新增 `phoenix` 服务，镜像 `arizephoenix/phoenix`
- Phoenix SHALL 暴露 :6006（Web UI）和 :4317（OTLP gRPC）端口
- Phoenix SHALL 通过 `PHOENIX_SQL_DATABASE_URL` 连接 PostgreSQL 的 `achat_observability` database
- `achat_observability` database SHALL 与业务库 `agenthub` 物理隔离
- PG 初始化脚本 SHALL 预建 `achat_observability` database
- Phoenix SHALL 自动在该 database 下创建所需表结构

#### Scenario: Phoenix 启动

- **WHEN** 执行 `docker compose up phoenix`
- **THEN** Phoenix 服务在 :6006 启动
- **AND** Phoenix 连接到 PostgreSQL 的 `achat_observability` database
- **AND** 访问 `http://localhost:6006` 可看到 Phoenix UI

#### Scenario: 数据隔离

- **WHEN** Phoenix 写入 trace 数据
- **THEN** 数据 SHALL 写入 `achat_observability` database
- **AND** 业务库 `agenthub` 不受影响

### Requirement: 属性 key 语义约定

系统 SHALL 统一 span 属性 key 命名，业务自定义属性加 `agenthub.` 前缀，标准属性对齐 OTel semantic conventions。

- 业务属性：`agenthub.hits`、`agenthub.empty`、`agenthub.skipped`、`agenthub.model`、`agenthub.adapter_name`、`agenthub.tool_name`、`agenthub.success`、`agenthub.turn`、`agenthub.finish_reason`、`agenthub.dispatch_depth`、`agenthub.system_prompt_hash`、`agenthub.rag_chunks_injected`、`agenthub.memory_items_injected`
- 标准属性：`http.method`、`http.url`、`http.status_code`、`db.system`
- 属性 key 常量 SHALL 在 `instrumentation.py` 集中定义，埋点统一引用

#### Scenario: 属性 key 一致性

- **WHEN** 新增一个工具的埋点
- **THEN** 该工具 span 的 `tool_name` 属性 SHALL 使用 `agenthub.tool_name` key
- **AND** 不直接硬编码字符串，而是引用 `instrumentation.py` 中的常量

### Requirement: 配置项

系统 SHALL 提供可观测性配置项。

- `backend/app/config.py` SHALL 新增：`trace_enabled`(bool, 默认 True)、`phoenix_endpoint`(str, 默认 `http://localhost:4317`)、`phoenix_ui_url`(str, 默认 `http://localhost:6006`)
- 配置项 SHALL 支持通过环境变量覆盖（`TRACE_ENABLED` / `PHOENIX_ENDPOINT` / `PHOENIX_UI_URL`）

#### Scenario: 配置覆盖

- **WHEN** 环境变量 `TRACE_ENABLED=false`
- **THEN** 后端启动后不产生任何 span
- **AND** agent run 正常执行不受影响
