# Change ③ 验收记录 — add-aeval-dataset-metrics

> 数据集构建闭环 + LLM 输出质量指标。验收标准（proposal）：**"数据集 → Suite → Run → 回归样本" 闭环跑通**。

## 1. 验收范围与结论

| 环节 | 验收方式 | 结论 |
| --- | --- | --- |
| 数据集模型 / 溯源 / to_suite | 单测（`tests/test_eval_dataset_sources.py` 等）+ API 测试 | ✅ 通过 |
| 数据集存储（Memory/SQLite，组合挂载 `storage.datasets`） | 单测 `test_eval_dataset_storage.py`（28 例，双实现参数化） | ✅ 通过 |
| 手动导入（YAML/JSON，校验报具体条目与字段） | 单测 + API `POST /datasets/import` 422 断言 | ✅ 通过 |
| Trace Mining 三策略（failed/long_running/diverse） | 单测（FakeTraceProvider）+ API from-trace 端点 | ✅ 通过（无 Phoenix 环境下逻辑级验证） |
| LLM 辅助生成（注入 llm_fn，同一校验） | 单测（stub llm_fn）+ API from-llm 未配置 LLM → 503 断言 | ✅ 通过 |
| 回归提取 + prompt 归一化去重 + max_items | 单测 + API regression-extract 端点 | ✅ 通过 |
| 质量检查 / 覆盖度 / 升版 | 单测 + API quality-check / coverage / version | ✅ 通过 |
| P0 四指标 + LLM Judge 基础设施（JSON 容错/重试/缺参明确失败） | 单测 `test_eval_metrics_module.py`（27 例，stub llm_fn） | ✅ 通过（judge 逻辑 stub 级验证） |
| Metric grader 分发（注册表/未知指标 0 分/required/weight/缓存） | 集成测试 `test_eval_metric_pipeline.py`（10 例） | ✅ 通过 |
| REST API 全链路闭环（创建→导入→质量→to-suite→run→回归提取→升版 minor） | `test_eval_dataset_api.py::test_full_closed_loop`（经 HTTP 全链路断言） | ✅ 通过 |
| 真实 Agent run 闭环 | `scripts/run_dataset_cycle.py`（`--dry-run` 已执行） | ⚠️ 脚本就绪，真实 Agent 段待部署环境执行（见 §3） |
| judge LLM 装配（AEVAL_JUDGE_* 优先 → eval_llm_* → openai 兜底） | `test_eval_integration_config.py` 新增 4 例 | ✅ 通过 |

自动化合计：**305 个 eval 相关测试全部通过**（`pytest tests/test_eval_* -q`）。

## 2. 已验证的闭环（自动化，确定性 Agent）

`tests/test_eval_dataset_api.py::test_full_closed_loop` 经 REST API 断言了完整链条：

1. `POST /datasets/import` 导入带 metric grader 的数据集；
2. `GET /datasets/{id}/quality-check` → ok；
3. `POST /datasets/{id}/to-suite` → suite 元数据携带 `dataset_id` / `dataset_version` 并落库；
4. `POST /runs` 启动 run（metric grader 经注册表分发评分）→ t1 通过、t2 失败；
5. `POST /datasets/{id}/regression-extract`（`bump_version: minor`）→ 提取 t2 首个失败 trial，
   prompt 归一化去重后合入，版本 1.0.0 → **1.1.0**，change_log 记录变更；
6. 回归条目 `source_type=regression`、`source_ref=失败 trace_id`、graders 复用 suite task 配置；
7. 升版后的数据集再次 to-suite → v1.1.0、任务数 2（原条目 + 回归条目）。

## 3. 真实 Agent 闭环（待部署环境执行）

本会话环境无运行中的 AChat 服务与 `EVAL_AGENT_ID`，真实 Agent 段无法执行。
已提供可重复的验收脚本（与 `run_first_suite.py` 同款约定）：

```bash
cd backend
# EVAL_AGENT_ID 需已配置；judge LLM 可选（未配置时 metric grader 返回明确配置错误）
python scripts/run_dataset_cycle.py --dry-run   # 已验证：构建数据集 + to-suite
python scripts/run_dataset_cycle.py             # 完整闭环（真实 Agent run）
```

脚本执行五步并打印各步 ID（dataset_id / run_id / 版本），全部落库可经
`GET /api/eval/datasets/{id}`、`GET /api/eval/runs/{run_id}` 复查。

**部署环境执行后请回填：**

| 项 | 值 |
| --- | --- |
| 执行日期 / 执行人 | （待填） |
| EVAL_AGENT_ID | （待填） |
| dataset_id / 版本 | （待填，预期 1.0.0 → 1.1.0） |
| run_id / 失败任务 | （待填） |
| 回归条目 source_ref | （待填，预期为失败 trial trace_id） |

## 4. Judge LLM 装配（任务 6.1 结论）

- 环境变量优先级：`AEVAL_JUDGE_API_KEY/_API_URL/_MODEL` → `EVAL_LLM_*` → `OPENAI_API_KEY`（模型默认 `gpt-4o-mini`）。
- 无任何凭证：`llm_fn=None` 照常注入四指标注册表 → metric grader 评分时返回明确配置错误结果（不 crash run）；
  `POST /datasets/{id}/from-llm` 返回 503。
- 设计文档 Open Questions（judge 走独立变量）已按"独立 AEVAL_JUDGE_* 优先 + 复用为后备"落地。

## 5. 首版范围说明（回写 §18 的素材）

- 挖掘策略首版 3 个：failed_tasks / long_running（P90×倍数，默认 2.0）/ diverse_sampling（trace_id 哈希确定性采样）；
  `user_dissatisfied` 保留枚举位，调用即抛 `NotImplementedError`（依赖用户反馈数据通道）。
- Trace Mining 的 LLM Enrich 步骤首版未实现（D4：可选、默认关闭）。
- 指标 P1（PromptMetric / pytest 插件 / 批量评测 API / report.py）不在本 change。
- 外部数据集适配器（SWE-bench 等）不在本 change（§18.8 社区贡献）。
