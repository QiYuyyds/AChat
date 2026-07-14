## 1. 基础设施部署

- [x] 1.1 在 `docker-compose.yml` 新增 `phoenix` 服务：镜像 `arizephoenix/phoenix`，端口 :6006（UI）+ :4317（OTLP gRPC），环境变量 `PHOENIX_SQL_DATABASE_URL` 指向 `postgresql://agenthub:agenthub@postgres:5432/achat_observability`，`depends_on: postgres`
- [x] 1.2 在 PG 初始化脚本中预建 `achat_observability` database（通过 init script 或 `POSTGRES_MULTIPLE_DATABASES` 环境变量），确保 Phoenix 首次启动时 database 已存在
- [x] 1.3 在 `docker-compose.infra.yml` 同步新增 `phoenix` 服务（远程部署场景）
- [x] 1.4 验证：`docker compose up phoenix` 启动后，访问 `http://localhost:6006` 可看到 Phoenix UI，且 Phoenix 连接 `achat_observability` database 成功

## 2. 后端依赖与配置

- [x] 2.1 在 `backend/requirements.txt`（或 `pyproject.toml`）添加依赖：`opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-exporter-otlp`、`opentelemetry-instrumentation-fastapi`、`opentelemetry-instrumentation-httpx`、`openinference-instrumentation-openai`、`arize-phoenix`（客户端 SDK）、`arize-phoenix-evals`
- [x] 2.2 在 `backend/app/config.py` 的 `Settings` 类新增配置项：`trace_enabled: bool = True`、`phoenix_endpoint: str = "http://localhost:4317"`、`phoenix_ui_url: str = "http://localhost:6006"`、`eval_rule_enabled: bool = True`、`eval_judge_enabled: bool = False`
- [x] 2.3 在 `backend/.env.example` 同步新增上述配置项的示例值
- [x] 2.4 验证：`pip install -r requirements.txt` 成功；后端启动读取配置项无报错

## 3. OTel 采集层初始化

- [x] 3.1 创建 `backend/app/observability/__init__.py`，导出 `init_observability`、`get_tracer`、`traced`、`SPAN_NAMES`
- [x] 3.2 创建 `backend/app/observability/tracer.py`：`init_observability(settings)` 初始化 `TracerProvider`，注册 `BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.phoenix_endpoint))`；`trace_enabled=False` 时跳过初始化直接返回；`get_tracer(name)` 返回 OTel tracer 实例；提供 `shutdown_observability()` 调用 `provider.shutdown()`
- [x] 3.3 创建 `backend/app/observability/instrumentation.py`：定义 `@traced(span_key, **attrs)` 装饰器（同步/异步兼容，自动捕获异常并设 span status=ERROR，自动记录异常信息）；定义属性 key 常量（`AGENTHUB_HITS`、`AGENTHUB_EMPTY`、`AGENTHUB_MODEL`、`AGENTHUB_TURN`、`AGENTHUB_FINISH_REASON`、`AGENTHUB_DISPATCH_DEPTH` 等，统一 `agenthub.` 前缀）
- [x] 3.4 创建 `backend/app/observability/span_names.py`：定义 `SPAN_NAMES: dict[str, str]` 映射表，覆盖 design 中列出的全部 18+ span key（`agent.run` → `agent.run · 代理运行` 等）；`@traced` 装饰器内部查表获取最终 span name；支持动态后缀 `suffix` 参数
- [x] 3.5 在 `backend/app/main.py` 的 `lifespan` 启动阶段调用 `init_observability(settings)` + 自动 instrumentation（`FastAPIInstrumentor.instrument_app(app)`、`HTTPXClientInstrumentor().instrument()`、`OpenAIInstrumentor().instrument()`）；关闭阶段调用 `shutdown_observability()`
- [x] 3.6 验证：启动后端，发一个 `/health` 请求，在 Phoenix UI 确认出现 FastAPI 自动生成的 HTTP 根 span

## 4. Level 4 深度手动埋点

- [x] 4.1 `backend/app/services/agent_runner.py`：在 `execute_run` 外层加 `@traced("agent.run", agent_id=..., run_id=..., conversation_id=..., dispatch_mode=...)`；在 `build_adapter_input` 加 `@traced("agent.build_context", history_msg_count=..., rag_enabled=..., memory_enabled=...)`；在 run 收尾处加 `agent.finalize` span（total_turns / total_tokens / duration_ms）
- [x] 4.2 `backend/app/services/prompt_assembler.py`：在 `ContextAssembler.assemble` 外层加 `@traced("prompt.assemble", schema_mode=..., final_token_count=..., system_prompt_hash=..., rag_chunks_injected=..., memory_items_injected=...)`
- [x] 4.3 `backend/app/memory/memory_service.py`：召回方法加 `@traced("memory.recall", source=...)`；LTM 查询加 `@traced("memory.ltm.query", top_k=..., min_score=..., hits=...)`；STM 读取加 `@traced("memory.stm.get", window_size=...)`
- [x] 4.4 `backend/app/services/rag_service.py`：在 `search()` 外层加 `@traced("rag.search", query=..., mode=..., rewrite_enabled=...)`；在 `ingest()` 加 `@traced("rag.ingest")`
- [x] 4.5 `backend/app/infra/hybrid.py`（或 `main.py` 内联回调）：`milvus_search` 加 `@traced("rag.milvus_search", hits=..., empty=...)`；`es_search` 加 `@traced("rag.es_search", hits=..., empty=...)`；`kg_search` 加 `@traced("rag.kg_search", hits=..., skipped=...)`
- [x] 4.6 `backend/app/rag/rag_engine.py`：查询改写加 `@traced("rag.query_rewrite", original=..., rewritten=...)`；RRF 融合加 `@traced("rag.rrf_fuse", final_count=..., fusion_method=...)`
- [x] 4.7 `backend/app/adapters/base.py`（或 `custom_adapter.py`）：`stream()` 外层加 `@traced("adapter.stream", adapter_name=..., model_id=...)`；在 LLM 每轮调用处加 `llm.generate` span（turn / input_tokens / output_tokens / finish_reason / cache_read_tokens）
- [x] 4.8 工具执行入口（`backend/app/tools/` 的 dispatch 函数）：加 `@traced("tool.call", tool_name=..., success=..., args_summary=...)`
- [x] 4.9 `backend/app/services/agent_loop.py`：`spawn_subagent_loop` 加 `@traced("tool.dispatch", task_id=..., child_agent_id=..., dispatch_depth=..., dispatch_visibility=...)`
- [x] 4.10 验证：触发一次含 RAG 工具调用的 solo agent run，在 Phoenix UI 确认 span 树完整嵌套（agent.run > agent.build_context > prompt.assemble + memory.recall > memory.ltm.query + adapter.stream > llm.generate > tool.call > rag.search > rag.query_rewrite + rag.milvus_search + rag.es_search + rag.rrf_fuse > llm.generate > agent.finalize）
- [x] 4.11 验证：触发一次 coordinated 模式 agent run，在 Phoenix UI 确认子 agent span 树嵌套（agent.run > tool.dispatch > agent.run(子Agent)），且 `dispatch_depth` 属性正确
- [x] 4.12 验证：构造 RAG 空召回场景（查询知识库中不存在的词），在 Phoenix UI 确认 `rag.es_search` span 属性 `agenthub.empty=true`、`agenthub.hits=0`

## 5. 在线规则评测

- [x] 5.1 创建 `backend/app/observability/eval_metrics.py`：定义全部评测指标的枚举 / 常量（Agent 全过程 5 维度 + 多 Agent 协作 4 维度），每项含名称、维度、评测方式（rule / judge / compare）、说明
- [x] 5.2 创建 `backend/app/observability/eval_rules.py`：实现 `run_rule_evaluations(trace_id, spans) -> list[EvalScore]`，从 span 数据计算全部在线规则指标（Task Completion / Max Turns Exceeded / Tool Success Rate / Redundant Tool Calls / Turns to Complete / Token Usage / Latency per Turn / Tool vs LLM Time Ratio / Dispatch Depth / Parallelism Degree / Subagent Count / Subagent Task Completion / Error Detection）
- [x] 5.3 在 `agent_runner.py` 的 `execute_run` 收尾处（`agent.finalize` span 之后）通过 `asyncio.create_task` 异步调用 `run_rule_evaluations`，`eval_rule_enabled=False` 时跳过
- [x] 5.4 评测结果通过 `arize-phoenix` Python SDK（`phoenix.Client.log_evaluations()`）写入 Phoenix，作为 trace 级 eval annotation
- [x] 5.5 验证：触发一次 agent run，在 Phoenix UI 确认该 trace 下方显示全部规则评测评分；确认 agent run 的 HTTP 响应不被评测阻塞

## 6. 离线 LLM-as-judge 评测

- [x] 6.1 创建 `backend/app/observability/eval_judge.py`：实现 `run_judge_evaluations(trace_id) -> list[EvalScore]`，从 Phoenix 拉取 trace span 数据 + LLM 输入输出摘要，构造 prompt 调用 DashScope LLM，解析返回的 9 项指标评分（Tool Selection Accuracy / Subtask Granularity / Subtask Overlap / Subtask Coverage / Aggregation Fidelity / Information Loss / Faithfulness / Answer Relevance / Answer Quality）
- [x] 6.2 创建 `backend/app/api/eval.py`：`POST /api/eval/judge/{trace_id}` 路由，`eval_judge_enabled=False` 时返回 403；trace 在 Phoenix 不存在时返回 404；正常时调用 `run_judge_evaluations` 并返回结果 JSON
- [x] 6.3 在 `backend/app/main.py` 注册 `app.include_router(eval.router, prefix="/api", tags=["eval"])`
- [x] 6.4 评测结果通过 `arize-phoenix` Python SDK 写入 Phoenix，作为同一 trace 的 eval annotation
- [ ] 6.5 验证：手动调用 `POST /api/eval/judge/{trace_id}`，在 Phoenix UI 确认该 trace 新增 LLM-as-judge 评分；确认 `eval_judge_enabled=False` 时返回 403（需运行环境，手动验证）

## 7. 已有 RAG eval 对接（可选增强）

- [x] 7.1 在 `eval/run_eval.py` 输出阶段增加 Phoenix 写入：评测结果通过 `phoenix.Client.log_evaluations()` 写入 Phoenix，作为 dataset + eval scores
- [ ] 7.2 验证：运行一次 RAG eval，在 Phoenix UI 的 Datasets / Evaluations 页面确认历史评测数据可见（需运行环境，手动验证）

## 8. 端到端验证

- [ ] 8.1 端到端排障场景验证：构造 RAG 召回为空的情况，打开 Phoenix UI，确认能从 span 瀑布流一眼定位是 Milvus 还是 ES 返回 0 hits（`agenthub.empty=true` 标记）（需运行环境，手动验证）
- [ ] 8.2 工具失败排障验证：构造一个工具调用失败（如 fs_grep 搜索不存在的路径），确认 Phoenix UI 中该 `tool.call` span 状态为 ERROR（需运行环境，手动验证）
- [ ] 8.3 多 Agent 协作场景验证：触发一次 coordinated 模式 agent run，确认 Phoenix UI span 树正确反映子 agent 调度顺序、并发结构、dispatch_depth 嵌套（需运行环境，手动验证）
- [ ] 8.4 中文 span name 验证：确认 Phoenix UI 树视图显示中文描述（「代理运行」「模型推理」「向量检索」），搜索框搜英文 key 或中文描述均可命中（需运行环境，手动验证）
- [ ] 8.5 trace + eval 一体化验证：确认 Phoenix UI 中同一 trace 的瀑布流下方同时显示在线规则评测 + LLM-as-judge 评测评分（需运行环境，手动验证）
- [ ] 8.6 trace_enabled 开关验证：设 `TRACE_ENABLED=false`，重启后端，触发 agent run，确认 Phoenix 无新 trace，且 agent run 正常执行（需运行环境，手动验证）
- [ ] 8.7 Phoenix 不可达降级验证：停掉 Phoenix 容器，触发 agent run，确认后端不报错、agent run 正常完成（需运行环境，手动验证）
- [ ] 8.8 性能验证：确认 span 采集与 OTLP 发送（BatchSpanProcessor 异步）对 agent run 主链路延迟影响 < 5%（需运行环境，手动验证）
