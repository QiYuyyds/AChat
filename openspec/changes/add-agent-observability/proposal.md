## Why

项目当前缺少 Agent 执行全链路的可观测能力和系统化的评测体系。一次 agent run 跨越 API → AgentRunner → Adapter → LLM → 工具 → RAG(Milvus+ES+KG) → Memory → DB 等 10+ 模块，排障依赖人工逐层排查。同时无法量化回答「工具调用对不对」「Orchestrator 拆任务合理吗」「提示词效果如何」等关键问题。项目正从 local-first 向 SaaS 化转型，需要标准化的可观测性采集层和评测能力支撑持续迭代。

## What Changes

- 新增 Arize Phoenix 作为独立可观测性后端（Docker 部署，复用 PostgreSQL 独立 database），提供独立的监控页面（:6006），内置 Trace 瀑布流可视化 + Eval 评分展示
- 新增 OpenTelemetry SDK 采集层，采用标准 OTel span 语义，通过 OTLP exporter 发送至 Phoenix，未来切换 Jaeger/Tempo 仅需修改 exporter endpoint
- 新增 FastAPI / httpx / openinference-openai 自动 instrumentation，零侵入覆盖 HTTP 请求与 LLM API 外调
- 新增 Level 4 深度手动埋点：覆盖 agent run 全链路（上下文组装 / 记忆召回 / RAG 三路检索子步骤 / 提示词组装 / 每轮 LLM 生成 / 工具调用 / 子 agent 派发），span name 采用「英文标识 · 中文描述」格式确保可视化可读性
- 新增 span name 中英文映射表（`span_names.py`），工程师写英文 key，Phoenix UI 显示中文，测试人员可读
- 新增在线规则评测层（默认开启）：每次 agent run 自动从 trace 数据计算规则指标（任务完成率 / 工具成功率 / 轮次效率 / token 消耗 / 派发深度等），eval score 挂在 trace 上，Phoenix UI 一体化查看
- 新增离线 LLM-as-judge 评测（默认关闭，手动触发）：通过 Phoenix API 对指定 trace 调用 DashScope LLM 深度评判（工具选择准确性 / 子任务粒度 / 聚合忠实度 / 回答忠实度 / 回答相关性 / 回答质量等）
- 新增 `trace_enabled` 配置开关，关闭时所有埋点变为 no-op，不影响主链路
- 新增 Agent 全过程评测指标体系（任务完成 / 工具调用质量 / 步骤效率 / 提示词效果 / 回答质量 5 个维度）
- 新增多 Agent 协作评测指标体系（任务拆解质量 / 调度效率 / 子 Agent 质量 / 聚合质量 4 个维度）

## Capabilities

### New Capabilities

- `agent-trace-observability`: Agent 全链路可观测能力，包括 OTel SDK 标准采集、自动 instrumentation（FastAPI/httpx/openai）、Level 4 深度手动埋点（含 RAG 子步骤 / 记忆子步骤 / 提示词组装 / 每轮 LLM 生成 / 子 agent 派发嵌套）、span 中英文映射、Phoenix OTLP 后端部署、`trace_enabled` 开关
- `agent-evaluation`: Agent 评测能力，包括在线规则评测（默认开启，从 trace 数据自动计算）、离线 LLM-as-judge 评测（默认关闭，手动触发）、Agent 全过程指标体系（5 维度）、多 Agent 协作指标体系（4 维度）、trace+eval 一体化展示

### Modified Capabilities

（无 — 本次变更为纯增量，不修改现有 agent run / RAG / 工具的业务逻辑，仅在关键函数外层包裹 span 和 eval hook）

## Impact

- 新增文件：
  - 后端可观测性：`backend/app/observability/__init__.py`、`backend/app/observability/tracer.py`（OTel 初始化 + tracer 提供 + `trace_enabled` 开关）、`backend/app/observability/span_names.py`（中英文映射表）、`backend/app/observability/instrumentation.py`（`@traced` 装饰器 + 属性 key 常量）
  - 后端评测：`backend/app/observability/eval_rules.py`（在线规则评测计算）、`backend/app/observability/eval_judge.py`（离线 LLM-as-judge 评测）、`backend/app/observability/eval_metrics.py`（指标定义与计算）
  - API：`backend/app/api/eval.py`（`POST /api/eval/judge/{trace_id}` 手动触发 LLM-as-judge）
- 新增依赖：
  - 后端：`opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-instrumentation-fastapi`、`opentelemetry-instrumentation-httpx`、`openinference-instrumentation-openai`、`arize-phoenix`（客户端 SDK）、`arize-phoenix-evals`（评测库）
- 基础设施：PostgreSQL 新增独立 database `achat_observability`（Phoenix 专用，与业务库 `achat` 物理隔离）；Docker Compose 新增 `phoenix` 服务（单容器，:6006 + :4317）
- 修改文件（仅埋点包裹 + eval hook，不改业务逻辑）：
  - `backend/app/main.py`：启动时初始化 OTel tracer + 自动 instrumentation
  - `backend/app/services/agent_runner.py`：`execute_run` / `execute_simple_run` / `build_adapter_input` / `consume_stream` 外层加 span
  - `backend/app/services/agent_loop.py`：`run_agent_loop` / `spawn_subagent_loop` 外层加 span
  - `backend/app/adapters/base.py`：`stream()` 外层加 span
  - `backend/app/services/rag_service.py`：`search()` / `ingest()` 外层加 span，内部子步骤加嵌套 span
  - `backend/app/infra/hybrid.py`：milvus / es / kg search 回调加嵌套 span
  - `backend/app/rag/rag_engine.py`：query_rewrite / rrf_fuse 加嵌套 span
  - `backend/app/memory/memory_service.py`：recall 加 span，ltm/stm 子方法加嵌套 span
  - `backend/app/services/prompt_assembler.py`（如存在）：assemble 加 span
  - `backend/app/tools/`：tool registry 分发函数外层加 span
  - `backend/app/config.py`：新增 `trace_enabled` / `eval_rule_enabled` / `eval_judge_enabled` / `phoenix_endpoint` 等配置项
  - `docker-compose.yml`：新增 phoenix 服务 + `achat_observability` database 初始化
- 不影响现有代码语义：所有 span 包裹为上下文管理器或装饰器，不改变被包裹函数的返回值与异常传播
- 降级：Phoenix 不可用时 OTel SDK BatchSpanProcessor 缓冲后丢弃，不阻断主链路；`trace_enabled=False` 时全部 no-op
